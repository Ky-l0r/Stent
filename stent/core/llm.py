"""LLM 接入层：OpenAI 兼容协议，流式输出、自动重试、可中断。

对应企划书 4.4：绕过 OpenClaw provider 抽象，直接使用 openai 库；
统一转换为 OpenAI messages 格式；embedding 使用 /embeddings 端点，
未配置 embedding 模型时由上层降级为关键词检索。
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any

from ..config import LLMConfig

log = logging.getLogger(__name__)


class LLMError(RuntimeError):
    """LLM 调用失败（网络、鉴权、限流等）。"""


class LLMCancelled(RuntimeError):
    """调用被用户主动中断。"""


class LLMTransientError(LLMError):
    """服务端临时故障（上游抖动、限流），值得按退避策略重试。"""


@dataclass
class Message:
    role: str  # system | user | assistant
    content: str

    def as_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


def _is_retryable(exc: Exception) -> bool:
    """判断异常是否值得重试（网络错误 / 限流 / 5xx）。"""
    if isinstance(exc, LLMTransientError):
        return True
    if isinstance(exc, LLMError):
        # 结构类错误（Base URL 路径不对、未配置等）重试多少次结果都一样
        return False
    name = type(exc).__name__
    if name in {"APIConnectionError", "APITimeoutError", "InternalServerError", "RateLimitError"}:
        return True
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and (status == 429 or status >= 500):
        return True
    text = str(exc).lower()
    return any(k in text for k in ("timeout", "timed out", "connection", "temporarily", "overloaded"))


# --------------------------------------------------------------------------
# 响应结构校验
#
# openai SDK 只有在 HTTP 状态码非 2xx 时才报错；若服务端返回 2xx 但响应体
# 不是对话补全 JSON（最典型的是 Base URL 路径写错，反向代理回了一个网站
# HTML 首页），SDK 会把**字符串原样返回**。此时 ``resp.choices`` 会抛
# AttributeError: 'str' object has no attribute 'choices'，用户完全看不懂。
# 下面这几个函数把这种情况识别出来并翻译成可操作的中文提示。
# --------------------------------------------------------------------------
def _import_client_class() -> Any:
    """导入 openai 的客户端类；未安装时抛出中文错误。"""
    try:
        from openai import OpenAI  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover
        raise LLMError("未安装 openai 库，请执行：pip install openai") from exc
    return OpenAI


def _is_llm_response(resp: Any) -> bool:
    """返回值是否是标准的对话补全响应对象（而不是字符串等原始响应体）。"""
    if isinstance(resp, (str, bytes)):
        return False
    choices = getattr(resp, "choices", None)
    return isinstance(choices, (list, tuple)) and len(choices) > 0


def _looks_like_html(text: str) -> bool:
    head = text.lstrip()[:200].lower()
    return head.startswith("<!doctype html") or head.startswith("<html") or "<head>" in head


def _describe_bad_response(resp: Any) -> str:
    """把「不是对话补全响应」的返回值翻译成用户能照着修的中文诊断。"""
    if isinstance(resp, (str, bytes)):
        text = resp.decode("utf-8", "replace") if isinstance(resp, bytes) else resp
        preview = " ".join(text.split())[:120]
        if _looks_like_html(text):
            return (
                "服务端返回的是网页 HTML，而不是 AI 接口响应：通常是 API Base URL 路径不对，"
                "多数服务需要以 /v1 结尾（例如 https://api.xxx.com/v1）。"
                f"实际返回开头：{preview}"
            )
        return f"服务端返回的不是 AI 接口响应（{type(resp).__name__}）：{preview}"
    return f"服务端返回的不是 AI 接口响应：{type(resp).__name__}"


#: 部分中转 / 网关在临时故障或额度不足时，会返回 200 + 合法响应结构，却把错误
#: 提示塞在正文里（而不是标准错误码）。不识别的话，这段英文会被当成 AI 的结论
#: 直接写进账号画像记忆，污染后续所有创作。
_SERVER_ERROR_MARKERS = (
    "the request could not be completed",
    "please retry later",
    "reduce the request parameters",
    "service temporarily unavailable",
    "no available channel",
    "upstream error",
    "无可用渠道",
    "上游负载已饱和",
    "请求过于频繁",
)


def _looks_like_server_error(text: str) -> bool:
    """判断正文是不是网关塞进来的错误提示（而不是模型的真实回答）。"""
    if not text or len(text) > 500:
        return False
    lowered = text.lower()
    if lowered.startswith("the request could not be completed"):
        return True
    return sum(1 for marker in _SERVER_ERROR_MARKERS if marker in lowered) >= 2


def _extract_content(resp: Any) -> str:
    """取回复正文；响应有问题时抛出可诊断的 :class:`LLMError`。"""
    if not _is_llm_response(resp):
        raise LLMError(_describe_bad_response(resp))
    choice = resp.choices[0]
    message = getattr(choice, "message", None)
    content = getattr(message, "content", None)
    text = (content or "").strip()
    if not text and getattr(choice, "finish_reason", None) == "length":
        # 推理模型（deepseek-reasoner / *-thinking 等）的思考过程同样计入
        # max_tokens。配额被思考吃满时正文会是空的、finish_reason 停在
        # "length"；不识别的话上层会把「空回复」当成调用成功，用户看到的是
        # 「归因完成」却没有任何结论。
        reasoning = getattr(message, "reasoning_content", "") or ""
        detail = f"，本次思考约 {len(reasoning)} 字" if reasoning else ""
        raise LLMError(
            f"模型输出被 max_tokens 截断，正文为空{detail}。"
            "请在「设置」里把 max_tokens 调大（推理模型建议 8192 以上）后重试。"
        )
    if _looks_like_server_error(text):
        raise LLMTransientError(f"服务端返回的是错误提示而不是回答：{text[:160]}")
    return text


def _wait_before_retry(cancel: threading.Event | None, attempt: int, exc: Exception) -> None:
    """重试前的退避等待（可被 cancel 事件打断）。"""
    delay = 1.5 * (2**attempt)
    log.warning("LLM 调用失败（第 %s 次），%.1fs 后重试：%s", attempt + 1, delay, exc)
    if cancel is not None:
        if cancel.wait(delay):
            raise LLMCancelled("已取消") from exc
    else:
        time.sleep(delay)


class LLMClient:
    """封装 openai SDK，供所有 SKILL 调用。"""

    def __init__(self, cfg: LLMConfig, api_key: str) -> None:
        self.cfg = cfg
        self.api_key = api_key or ""
        self._client: Any = None

    # -- 基础设施 --------------------------------------------------------
    @property
    def client(self) -> Any:
        if self._client is None:
            client_cls = _import_client_class()
            if not self.cfg.base_url:
                raise LLMError("尚未配置 API Base URL")
            # 本地 Ollama 等场景允许空 Key
            self._client = client_cls(
                api_key=self.api_key or "sk-no-key-required",
                base_url=self.cfg.base_url,
                timeout=self.cfg.timeout,
                max_retries=0,  # 重试由本层控制，便于中断与日志
            )
        return self._client

    def reset(self) -> None:
        """配置变更后丢弃已缓存的客户端。"""
        self._client = None

    # -- 对话 ------------------------------------------------------------
    def chat(
        self,
        messages: Sequence[Message | dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        cancel: threading.Event | None = None,
    ) -> str:
        payload = [m.as_dict() if isinstance(m, Message) else dict(m) for m in messages]
        kwargs: dict[str, Any] = {
            "model": self.cfg.model,
            "messages": payload,
            "temperature": self.cfg.temperature if temperature is None else temperature,
        }
        if max_tokens or self.cfg.max_tokens:
            kwargs["max_tokens"] = max_tokens or self.cfg.max_tokens

        last_error: Exception | None = None
        for attempt in range(self.cfg.retries + 1):
            if cancel is not None and cancel.is_set():
                raise LLMCancelled("已取消")
            try:
                resp = self.client.chat.completions.create(**kwargs)
                # 响应结构 / 内容校验放在这里：既能把 SDK 内部属性错误
                # （'str' object has no attribute 'choices'）换成可诊断的中文提示，
                # 也能让「网关用 200 回错误文本」这类抖动走正常重试。
                return _extract_content(resp)
            except LLMError as exc:
                if not _is_retryable(exc):
                    raise  # 结构性问题，原样抛出干净的中文原因
                last_error = exc
            except Exception as exc:  # noqa: BLE001 - 统一转 LLMError
                last_error = exc
                if not _is_retryable(exc):
                    break
            if attempt >= self.cfg.retries:
                break
            _wait_before_retry(cancel, attempt, last_error)
        raise LLMError(f"LLM 调用失败：{last_error}") from last_error

    def stream_chat(
        self,
        messages: Sequence[Message | dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        cancel: threading.Event | None = None,
    ) -> Iterator[str]:
        """流式输出增量文本片段；支持通过 cancel 事件随时中断。"""
        payload = [m.as_dict() if isinstance(m, Message) else dict(m) for m in messages]
        kwargs: dict[str, Any] = {
            "model": self.cfg.model,
            "messages": payload,
            "temperature": self.cfg.temperature if temperature is None else temperature,
            "stream": True,
        }
        if max_tokens or self.cfg.max_tokens:
            kwargs["max_tokens"] = max_tokens or self.cfg.max_tokens

        last_error: Exception | None = None
        for attempt in range(self.cfg.retries + 1):
            if cancel is not None and cancel.is_set():
                raise LLMCancelled("已取消")
            emitted = False
            chunks = 0
            first_text = ""
            try:
                stream = self.client.chat.completions.create(**kwargs)
                for chunk in stream:
                    chunks += 1
                    if cancel is not None and cancel.is_set():
                        try:
                            stream.close()
                        except Exception:
                            pass
                        raise LLMCancelled("已取消")
                    if isinstance(chunk, (str, bytes)):
                        # 服务端没有按流式协议返回（典型：Base URL 路径不对，回了一页 HTML）
                        raise LLMError(_describe_bad_response(chunk))
                    if not getattr(chunk, "choices", None):
                        continue
                    delta = chunk.choices[0].delta
                    piece = getattr(delta, "content", None)
                    if not piece:
                        continue
                    if not emitted:
                        # 网关的错误提示通常是整段一次性返回的；在吐给用户之前先识别，
                        # 免得把「The request could not be completed…」当成正文输出。
                        first_text += piece
                        if _looks_like_server_error(first_text):
                            raise LLMTransientError(
                                f"服务端返回的是错误提示而不是回答：{first_text[:160]}"
                            )
                    emitted = True
                    yield piece
                if chunks == 0:
                    # 服务端返回 200 却一个数据块都没有（例如路径写错时拿到一页 HTML，
                    # SDK 对非 SSE 响应只是静默跳过）。不校验的话上层会拿到「空白回复」
                    # 却以为调用成功。
                    raise LLMError(
                        "服务端没有返回任何流式数据：通常是 API Base URL 路径不对，"
                        "多数服务需要以 /v1 结尾（例如 https://api.xxx.com/v1）。"
                    )
                if not emitted:
                    # 收到了数据块但没有一个正文片段：推理模型把 max_tokens 全用在
                    # 思考上时就是这样，上层会拿到空白内容却以为生成成功。
                    raise LLMError(
                        "模型只输出了思考过程、没有返回正文（通常是把 max_tokens 用在了思考上）。"
                        "请在「设置」里把 max_tokens 调大（推理模型建议 8192 以上）后重试。"
                    )
                return
            except LLMCancelled:
                raise
            except LLMError as exc:
                # 结构性问题重试无意义；网关抖动可重试，但已吐字就不能重来（内容会重复）
                if not _is_retryable(exc):
                    raise
                last_error = exc
                if emitted or attempt >= self.cfg.retries:
                    break
                _wait_before_retry(cancel, attempt, last_error)
                continue
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                # 已经吐字就不重试，避免内容重复
                if emitted or attempt >= self.cfg.retries or not _is_retryable(exc):
                    break
                _wait_before_retry(cancel, attempt, last_error)
                continue
        raise LLMError(f"LLM 流式调用失败：{last_error}") from last_error

    # -- 诊断 ------------------------------------------------------------
    def test_connection(self, timeout: int = 20) -> tuple[bool, str]:
        """「测试连通性」按钮：发一条最小请求验证 Key、Base URL 与模型名。

        只判断「请求没报错」是不够的：Base URL 路径写错时反向代理会返回
        ``200 OK`` 加一页网站首页，SDK 不抛异常却拿不到任何回复，于是出现
        「提示连接成功、实际一调用就失败」。这里必须校验响应结构。
        """
        try:
            client = self.client
        except LLMError as exc:
            return False, str(exc)
        try:
            resp = client.with_options(timeout=timeout).chat.completions.create(
                model=self.cfg.model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=5,
                temperature=0,
            )
        except Exception as exc:  # noqa: BLE001
            return False, _humanize(exc)

        if not _is_llm_response(resp):
            message = _describe_bad_response(resp)
            suggestion = self._probe_base_url_variant(timeout)
            if suggestion:
                message += f"　已探测到可用地址：{suggestion}，请把 API Base URL 改成它后重试。"
            return False, message

        model = getattr(resp, "model", self.cfg.model)
        return True, f"连接成功，模型 {model} 可用"

    def _probe_base_url_variant(self, timeout: int) -> str | None:
        """Base URL 疑似漏写 ``/v1`` 时试一次 ``base_url + /v1``；可用则返回该地址。"""
        base = (self.cfg.base_url or "").rstrip("/")
        if not base or base.lower().endswith("/v1"):
            return None
        candidate = f"{base}/v1"
        try:
            probe = _import_client_class()(
                api_key=self.api_key or "sk-no-key-required",
                base_url=candidate,
                timeout=timeout,
                max_retries=0,
            )
            resp = probe.chat.completions.create(
                model=self.cfg.model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=5,
                temperature=0,
            )
        except Exception:  # noqa: BLE001 - 探测失败即放弃，不影响主流程
            log.debug("探测备用 Base URL 失败：%s", candidate, exc_info=True)
            return None
        return candidate if _is_llm_response(resp) else None

    def list_models(self) -> tuple[bool, list[str] | str]:
        """拉取服务商模型列表（部分中转不支持，失败不影响使用）。"""
        try:
            resp = self.client.models.list()
            return True, sorted(m.id for m in resp.data)
        except Exception as exc:  # noqa: BLE001
            return False, _humanize(exc)

    def embed(self, texts: Sequence[str]) -> list[list[float]] | None:
        """向量化；未配置 embedding 模型时返回 None，由上层降级（企划书 4.4）。"""
        if not self.cfg.embedding_model:
            return None
        try:
            resp = self.client.embeddings.create(model=self.cfg.embedding_model, input=list(texts))
            return [item.embedding for item in resp.data]
        except Exception:
            log.warning("embedding 调用失败，降级为关键词检索", exc_info=True)
            return None


def _humanize(exc: Exception) -> str:
    """把 SDK 异常翻译成用户能看懂的中文提示。"""
    name = type(exc).__name__
    status = getattr(exc, "status_code", None)
    mapping = {
        "AuthenticationError": "鉴权失败：API Key 无效或已过期",
        "PermissionDeniedError": "权限不足：该 Key 无权访问此模型",
        "NotFoundError": "模型或接口不存在：请检查 Base URL 与模型名称",
        "RateLimitError": "请求过于频繁或额度不足：请稍后重试或检查余额",
        "APIConnectionError": "网络连接失败：请检查 Base URL、代理与网络",
        "APITimeoutError": "请求超时：请检查网络或改用更快的模型",
        "BadRequestError": "请求被拒绝：请检查模型名称与参数",
    }
    if name in mapping:
        return f"{mapping[name]}（{exc}）"
    if isinstance(status, int):
        return f"HTTP {status}：{exc}"
    return f"{name}：{exc}"


def build_client(cfg: LLMConfig, api_key: str) -> LLMClient:
    return LLMClient(cfg, api_key)
