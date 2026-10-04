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


@dataclass
class Message:
    role: str  # system | user | assistant
    content: str

    def as_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


def _is_retryable(exc: Exception) -> bool:
    """判断异常是否值得重试（网络错误 / 限流 / 5xx）。"""
    name = type(exc).__name__
    if name in {"APIConnectionError", "APITimeoutError", "InternalServerError", "RateLimitError"}:
        return True
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and (status == 429 or status >= 500):
        return True
    text = str(exc).lower()
    return any(k in text for k in ("timeout", "timed out", "connection", "temporarily", "overloaded"))


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
            try:
                from openai import OpenAI  # noqa: PLC0415
            except ImportError as exc:  # pragma: no cover
                raise LLMError("未安装 openai 库，请执行：pip install openai") from exc
            if not self.cfg.base_url:
                raise LLMError("尚未配置 API Base URL")
            # 本地 Ollama 等场景允许空 Key
            self._client = OpenAI(
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
                return (resp.choices[0].message.content or "").strip()
            except Exception as exc:  # noqa: BLE001 - 统一转 LLMError
                last_error = exc
                if attempt >= self.cfg.retries or not _is_retryable(exc):
                    break
                delay = 1.5 * (2**attempt)
                log.warning("LLM 调用失败（第 %s 次），%.1fs 后重试：%s", attempt + 1, delay, exc)
                if cancel is not None:
                    if cancel.wait(delay):
                        raise LLMCancelled("已取消") from exc
                else:
                    time.sleep(delay)
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
            try:
                stream = self.client.chat.completions.create(**kwargs)
                for chunk in stream:
                    if cancel is not None and cancel.is_set():
                        try:
                            stream.close()
                        except Exception:
                            pass
                        raise LLMCancelled("已取消")
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta
                    piece = getattr(delta, "content", None)
                    if piece:
                        emitted = True
                        yield piece
                return
            except LLMCancelled:
                raise
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                # 已经吐字就不重试，避免内容重复
                if emitted or attempt >= self.cfg.retries or not _is_retryable(exc):
                    break
                delay = 1.5 * (2**attempt)
                log.warning("LLM 流式调用失败（第 %s 次），%.1fs 后重试：%s", attempt + 1, delay, exc)
                if cancel is not None:
                    if cancel.wait(delay):
                        raise LLMCancelled("已取消") from exc
                else:
                    time.sleep(delay)
        raise LLMError(f"LLM 流式调用失败：{last_error}") from last_error

    # -- 诊断 ------------------------------------------------------------
    def test_connection(self, timeout: int = 20) -> tuple[bool, str]:
        """「测试连通性」按钮：发一条最小请求验证 Key、Base URL 与模型名。"""
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
            model = getattr(resp, "model", self.cfg.model)
            return True, f"连接成功，模型 {model} 可用"
        except Exception as exc:  # noqa: BLE001
            return False, _humanize(exc)

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
