"""接口实现：每个端点只做「取参数 → 调 service → 序列化」。

这里不许出现业务判断（字数上限、敏感词、平台规范都属于 ``services``）。
唯一允许的逻辑是**参数校验**与**面向 Qt 的返回值转 dict**。
"""

from __future__ import annotations

import dataclasses
import logging
import queue
import threading
import time
from typing import Any

from .. import __version__
from ..config import PROVIDER_PRESETS, config_manager
from ..config import secrets as secret_store
from ..core.db import db
from ..core.llm import LLMClient, LLMError, Message
from ..platforms import platform_limits
from ..services.creator import PLATFORM_STYLES, CreationRequest, CreationResult, creator_service
from ..services.hotsearch import ALL_PLATFORMS, PLATFORM_LABELS, hotsearch_service
from ..services.profile import DIMENSIONS, profile_service
from ..services.publisher import PublishService
from .server import ApiError, ApiServer, Request, Response, StreamResponse

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# 公共工具
# --------------------------------------------------------------------------
def _llm() -> LLMClient:
    """按当前配置构造 LLM 客户端；未配置时给出可操作的中文提示。"""
    cfg = config_manager.config.llm
    key = config_manager.api_key()
    if not (cfg.base_url and cfg.model and key):
        raise ApiError(
            "尚未配置 LLM：请到「设置」填写 API Base URL、API Key 与模型名称",
            status=409,
            code="llm_not_configured",
        )
    return LLMClient(cfg, key)


def _dict(value: Any) -> Any:
    """把 dataclass / tuple 转成可 JSON 化的结构。"""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.asdict(value)
    if isinstance(value, (list, tuple)):
        return [_dict(v) for v in value]
    if isinstance(value, dict):
        return {k: _dict(v) for k, v in value.items()}
    return value


def _report_dict(report: Any) -> dict[str, Any]:
    """发布前检查报告 → dict（``CheckReport`` 是 dataclass，直接 asdict 即可）。"""
    items = []
    for item in getattr(report, "items", None) or []:
        if dataclasses.is_dataclass(item) and not isinstance(item, type):
            items.append(dataclasses.asdict(item))
        else:  # pragma: no cover - 兜底，避免检查器换实现后整页 500
            items.append({"level": "info", "code": "", "title": str(item), "detail": ""})
    try:
        text = report.to_text()
    except Exception:  # pragma: no cover - 报告不可读时不影响其它字段
        text = ""
    return {
        "passed": bool(getattr(report, "passed", False)),
        "score": int(getattr(report, "score", 0) or 0),
        "items": items,
        "text": text,
    }


def _settings_payload() -> dict[str, Any]:
    cfg = config_manager.config
    llm = cfg.llm
    return {
        "llm": {
            "provider": llm.provider,
            "base_url": llm.base_url,
            "model": llm.model,
            "temperature": llm.temperature,
            "max_tokens": llm.max_tokens,
            "timeout": llm.timeout,
            "retries": llm.retries,
        },
        "theme": cfg.theme,
        "hotlist_platforms": list(cfg.hotlist_platforms or []),
        "publish_platforms": list(cfg.publish_platforms or []),
        # 只暴露「有没有设置」与「存在哪」，密钥本身永不出后端
        "key_set": bool(config_manager.api_key()),
        "key_mode": secret_store.describe_mode(),
        "presets": [p.as_dict() for p in PROVIDER_PRESETS],
    }


def _publish_platforms() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for entry in PublishService.platforms():
        limits = entry.get("limits")
        out.append(
            {
                "key": entry.get("key", ""),
                "label": entry.get("label", ""),
                "available": bool(entry.get("available")),
                "needs_media": bool(entry.get("needs_media")),
                "limits": _dict(limits) if limits is not None else {},
            }
        )
    return out


# --------------------------------------------------------------------------
# 基础
# --------------------------------------------------------------------------
def health(request: Request) -> Response:
    return Response(
        {
            "ok": True,
            "version": __version__,
            "llm_ready": config_manager.ready(),
        }
    )


def meta(request: Request) -> Response:
    return Response(
        {
            "version": __version__,
            "theme": config_manager.config.theme,
            "hot_platforms": [
                {"key": key, "label": PLATFORM_LABELS.get(key, key)} for key in ALL_PLATFORMS
            ],
            "hot_enabled": hotsearch_service.enabled_platforms(),
            "creation_platforms": creator_service.platforms(),
            "publish_platforms": _publish_platforms(),
            "llm_ready": config_manager.ready(),
        }
    )


# --------------------------------------------------------------------------
# 设置
# --------------------------------------------------------------------------
def get_settings(request: Request) -> Response:
    return Response(_settings_payload())


def put_settings(request: Request) -> Response:
    data = request.json()
    llm = data.get("llm") or {}
    if not isinstance(llm, dict):
        raise ApiError("llm 字段必须是对象")

    allowed_llm = {
        "provider",
        "base_url",
        "model",
        "temperature",
        "max_tokens",
        "timeout",
        "retries",
    }
    patch: dict[str, Any] = {}
    for key in allowed_llm & set(llm):
        value = llm[key]
        if key in {"temperature"}:
            try:
                patch[key] = float(value)
            except (TypeError, ValueError):
                raise ApiError(f"{key} 必须是数字") from None
        elif key in {"max_tokens", "timeout", "retries"}:
            try:
                patch[key] = int(value)
            except (TypeError, ValueError):
                raise ApiError(f"{key} 必须是整数") from None
        else:
            patch[key] = str(value or "")
    if patch:
        config_manager.update_llm(**patch)

    if "theme" in data:
        theme = "dark" if str(data.get("theme")).lower() == "dark" else "light"
        config_manager.update(theme=theme)

    # 密钥只进不出：传了非空值才写，传 clear_key 才清
    key = data.get("api_key")
    if isinstance(key, str) and key.strip():
        secret_store.set_api_key(key.strip())
    elif data.get("clear_key"):
        secret_store.clear_api_key()

    return Response(_settings_payload())


def test_settings(request: Request) -> Response:
    client = _llm()
    started = time.time()
    try:
        reply = client.chat([Message("user", "只回复两个字：可用")], temperature=0)
    except LLMError as exc:
        return Response({"ok": False, "message": str(exc), "latency_ms": 0})
    elapsed = int((time.time() - started) * 1000)
    sample = (reply or "").strip().replace("\n", " ")[:60]
    return Response({"ok": True, "message": f"连通正常（{elapsed} ms）", "sample": sample})


# --------------------------------------------------------------------------
# 账号画像
# --------------------------------------------------------------------------
def _profile_payload() -> dict[str, Any]:
    return {
        "profile": profile_service.get(),
        "dimensions": [dataclasses.asdict(dim) for dim in DIMENSIONS],
        "completeness": profile_service.completeness(),
        "missing": profile_service.missing_dimensions(),
    }


def get_profile(request: Request) -> Response:
    return Response(_profile_payload())


def put_profile(request: Request) -> Response:
    data = request.json()
    allowed = {"positioning", "style", "audience", "platforms", "preferences"}
    patch = {k: v for k, v in data.items() if k in allowed}
    if not patch:
        raise ApiError("没有可保存的画像字段")
    profile_service.save(**patch)
    return Response(_profile_payload())


def profile_draft(request: Request) -> Response:
    text = str(request.json().get("text") or "").strip()
    if not text:
        raise ApiError("请先粘贴一段自我介绍")
    client = _llm()
    try:
        draft = profile_service.draft_from_text(text, client)
    except LLMError as exc:
        raise ApiError(str(exc), status=502, code="llm_error") from exc
    return Response({"draft": draft})


def profile_memory(request: Request) -> Response:
    if request.method == "DELETE":
        profile_service.clear_memory()
        return Response(_profile_payload())
    text = str(request.json().get("text") or "").strip()
    if not text:
        raise ApiError("记忆内容不能为空")
    profile_service.append_memory(text)
    return Response(_profile_payload())


# --------------------------------------------------------------------------
# 热点
# --------------------------------------------------------------------------
def get_hot(request: Request) -> Response:
    force = request.flag("force")
    raw = request.q("platforms")
    platforms = [p.strip() for p in raw.split(",") if p.strip()] or None

    results = hotsearch_service.fetch_all(platforms, force=force)

    items: list[dict[str, Any]] = []
    platform_rows: list[dict[str, Any]] = []
    for key, result in results.items():
        platform_rows.append(
            {
                "key": key,
                "label": result.label,
                "count": len(result.items),
                "source": result.source,
                "from_cache": result.from_cache,
                "stale": result.stale,
                "error": result.error,
                "fetched_at": result.fetched_at,
            }
        )
        items.extend(item.as_dict() for item in result.items)

    counts = hotsearch_service.category_counts(results)
    return Response(
        {
            "items": items,
            "platforms": platform_rows,
            "categories": [{"name": name, "count": count} for name, count in counts],
            "llm_ready": config_manager.ready(),
        }
    )


# --------------------------------------------------------------------------
# 创作
# --------------------------------------------------------------------------
def _creation_request(data: dict[str, Any]) -> CreationRequest:
    topic = str(data.get("topic") or "").strip()
    if not topic:
        raise ApiError("请先填写主题")
    platform = str(data.get("platform") or "xiaohongshu").strip()
    if platform not in PLATFORM_STYLES:
        raise ApiError(f"不支持的创作平台：{platform}")
    try:
        variants = int(data.get("variants") or 3)
    except (TypeError, ValueError):
        variants = 3
    return CreationRequest(
        topic=topic,
        platform=platform,
        content_type=str(data.get("content_type") or ""),
        goal=str(data.get("goal") or "互动涨粉"),
        audience=str(data.get("audience") or ""),
        tone=str(data.get("tone") or ""),
        source_text=str(data.get("source_text") or ""),
        extra=str(data.get("extra") or ""),
        variants=max(1, min(variants, 5)),
    )


def create_stream(request: Request) -> StreamResponse:
    """流式创作（SSE）。

    实现在工作线程里跑 ``creator_service.create_stream``，用队列把分段事件搬给
    生成器；生成器被关闭时（客户端断开 / 点了中断）``finally`` 置位取消信号，
    上游 LLM 迭代器随之停下——否则「中断」只是前端不再显示，token 照烧。
    """
    req = _creation_request(request.json())
    client = _llm()
    cancel = threading.Event()

    def events():
        events_queue: queue.Queue = queue.Queue()
        finished = object()
        holder: dict[str, Any] = {}

        def worker() -> None:
            try:
                result = creator_service.create_stream(
                    req,
                    client,
                    cancel=cancel,
                    on_event=lambda section, text: events_queue.put(
                        ("delta", {"section": section, "text": text})
                    ),
                )
                holder["result"] = result
            except Exception as exc:  # noqa: BLE001 - 任何失败都要让前端看到原因
                log.warning("流式创作失败", exc_info=True)
                holder["error"] = str(exc) or type(exc).__name__
            finally:
                events_queue.put(finished)

        threading.Thread(target=worker, name="web-create", daemon=True).start()

        try:
            yield ("ready", {"platform": req.platform, "topic": req.topic})
            while True:
                item = events_queue.get()
                if item is finished:
                    break
                yield item
        finally:
            # 正常结束、客户端断开、异常，都要让上游停手
            cancel.set()

        if "error" in holder:
            yield ("error", {"message": holder["error"]})
            return
        result = holder.get("result")
        if result is None:
            yield ("error", {"message": "生成未完成"})
            return
        yield (
            "done",
            {
                "result": result.as_dict(),
                "hint": result.usage_hint,
                "platform": result.platform,
            },
        )

    return StreamResponse(events())


def create_titles(request: Request) -> Response:
    data = request.json()
    req = _creation_request(data)
    body = str(data.get("body") or "").strip()
    if not body:
        raise ApiError("请先生成或填写正文")
    try:
        count = max(1, min(int(data.get("count") or 3), 5))
    except (TypeError, ValueError):
        count = 3
    client = _llm()
    try:
        titles = creator_service.regenerate_titles(req, body, client, count=count)
    except LLMError as exc:
        raise ApiError(str(exc), status=502, code="llm_error") from exc
    return Response({"titles": titles})


def create_quality(request: Request) -> Response:
    data = request.json()
    platform = str(data.get("platform") or "xiaohongshu")
    body = str(data.get("body") or "").strip()
    if not body:
        raise ApiError("请先生成或填写正文")
    result = CreationResult(
        platform=platform,
        body=body,
        titles=[str(t) for t in (data.get("titles") or []) if str(t).strip()],
        summary=str(data.get("summary") or ""),
        tags=[str(t) for t in (data.get("tags") or []) if str(t).strip()],
    )
    client = _llm()
    try:
        report = creator_service.quality_check(result, client)
    except LLMError as exc:
        raise ApiError(str(exc), status=502, code="llm_error") from exc
    return Response({"report": _dict(report)})


# --------------------------------------------------------------------------
# 发布（P0：只做适配 / 检查 / 预览，真正提交在 P2）
# --------------------------------------------------------------------------
def publish_platforms(request: Request) -> Response:
    return Response({"platforms": _publish_platforms()})


def publish_prepare(request: Request) -> Response:
    data = request.json()
    platform = str(data.get("platform") or "").strip()
    if not platform:
        raise ApiError("请选择目标平台")
    body = str(data.get("body") or "").strip()
    if not body:
        raise ApiError("正文为空，请先创作或粘贴内容")

    content = {
        "platform": platform,
        "title": str(data.get("title") or ""),
        "titles": [str(t) for t in (data.get("titles") or []) if str(t).strip()],
        "body": body,
        "tags": [str(t) for t in (data.get("tags") or []) if str(t).strip()],
        "summary": str(data.get("summary") or ""),
        "topic": str(data.get("topic") or ""),
    }
    media = [str(p) for p in (data.get("media") or []) if str(p).strip()]

    service = PublishService()
    payload, notes = service.build_payload(content, media=media)
    limits = platform_limits(platform)
    adapter = service.adapter(platform)
    try:
        report = service.precheck.check(payload, limits, platform=platform)
    except Exception as exc:  # noqa: BLE001 - 检查器失败不能挡住预览
        log.exception("发布前检查失败")
        raise ApiError(f"发布前检查执行失败：{exc}", status=500, code="precheck_failed") from exc

    return Response(
        {
            "platform": platform,
            "label": PLATFORM_LABELS.get(platform, platform),
            "preview": service.preview_text(payload, platform),
            "notes": notes,
            "checks": _report_dict(report),
            "supported": adapter is not None,
            "support_note": (
                ""
                if adapter is not None
                else f"{PLATFORM_LABELS.get(platform, platform)}的自动化适配器暂不可用，"
                "可复制文案后人工发布"
            ),
            "limits": _dict(limits),
        }
    )


# --------------------------------------------------------------------------
# 路由注册
# --------------------------------------------------------------------------
def build_app(*, host: str = "127.0.0.1", port: int = 0, token: str | None = None) -> ApiServer:
    """构造并注册全部路由。

    ``db.init()`` 在这里调一次：Qt 版是在 ``app.main()`` 里初始化的，
    Web 版没有那一步，忘了就会在第一次读写画像时炸 ``no such table``。
    该方法是幂等的（建表用 IF NOT EXISTS + 显式迁移）。
    """
    db.init()
    app = ApiServer(host=host, port=port, token=token)

    app.route("GET", "/api/health")(health)
    app.route("GET", "/api/meta")(meta)

    app.route("GET", "/api/settings")(get_settings)
    app.route("PUT", "/api/settings")(put_settings)
    app.route("POST", "/api/settings/test")(test_settings)

    app.route("GET", "/api/profile")(get_profile)
    app.route("PUT", "/api/profile")(put_profile)
    app.route("POST", "/api/profile/draft")(profile_draft)
    app.route("POST", "/api/profile/memory")(profile_memory)
    app.route("DELETE", "/api/profile/memory")(profile_memory)

    app.route("GET", "/api/hot")(get_hot)

    app.route("POST", "/api/create/stream")(create_stream)
    app.route("POST", "/api/create/titles")(create_titles)
    app.route("POST", "/api/create/quality")(create_quality)

    app.route("GET", "/api/publish/platforms")(publish_platforms)
    app.route("POST", "/api/publish/prepare")(publish_prepare)

    return app
