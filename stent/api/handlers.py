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
from ..platforms import (
    platform_label,
    platform_limits,
    registered_platforms,
    system_browser_channel,
    system_browser_label,
)
from ..services.creator import PLATFORM_STYLES, CreationRequest, CreationResult, creator_service
from ..services.hotsearch import ALL_PLATFORMS, PLATFORM_LABELS, hotsearch_service
from ..services.profile import DIMENSIONS, profile_service
from ..services.publisher import POST_STATUS_LABELS, PublishService, environment_status
from .jobs import jobs
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


def _str_list(value: Any) -> list[str]:
    """把标签 / 媒体 / 标题候选这类字段收成字符串列表。

    前端应该传数组，但外部调用（curl、脚本）传成字符串时不能按字符拆开——
    那会把 "小红书" 变成三个标签，排查起来非常费劲。
    """
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return []


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

    if "hotlist_platforms" in data:
        raw = data.get("hotlist_platforms")
        if not isinstance(raw, list):
            raise ApiError("hotlist_platforms 必须是数组")
        known = set(ALL_PLATFORMS)
        chosen = []
        for key in raw:
            text = str(key or "").strip().lower()
            if text in known and text not in chosen:
                chosen.append(text)
        if not chosen:
            raise ApiError("至少要保留一个热点平台")
        config_manager.update(hotlist_platforms=chosen)

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
        "titles": _str_list(data.get("titles")),
        "body": body,
        "tags": _str_list(data.get("tags")),
        "summary": str(data.get("summary") or ""),
        "topic": str(data.get("topic") or ""),
    }
    media = _str_list(data.get("media"))

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
            "label": platform_label(platform),
            "preview": service.preview_text(payload, platform),
            "notes": notes,
            "checks": _report_dict(report),
            "supported": adapter is not None,
            "support_note": (
                ""
                if adapter is not None
                else f"{platform_label(platform)}的自动化适配器暂不可用，可复制文案后人工发布"
            ),
            "limits": _dict(limits),
        }
    )


def _int_arg(request: Request, name: str, default: int, low: int, high: int) -> int:
    """读取整数查询参数；非法值直接给中文 400，而不是静默用默认值。"""
    raw = request.q(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ApiError(f"{name} 必须是整数") from None
    return max(low, min(high, value))


# --------------------------------------------------------------------------
# 草稿箱（contents 表里 status='draft' 的记录）
# --------------------------------------------------------------------------
#: 草稿可写字段。``status`` 不在这里：由接口根据动作决定，前端不能把草稿改成已发布。
_DRAFT_TEXT_FIELDS = ("topic", "platform", "title", "body", "summary", "extra")


def _content_row(row: dict[str, Any]) -> dict[str, Any]:
    """contents 行 → 前端用的草稿结构。"""
    body = str(row.get("body") or "")
    return {
        "id": int(row.get("id") or 0),
        "topic": row.get("topic") or "",
        "platform": row.get("platform") or "",
        "platform_label": platform_label(row.get("platform") or ""),
        "title": row.get("title") or "",
        "titles": list(row.get("titles") or []),
        "body": body,
        "excerpt": body.strip().replace("\n", " ")[:80],
        "summary": row.get("summary") or "",
        "tags": list(row.get("tags") or []),
        "media": list(row.get("media") or []),
        "extra": row.get("extra") or "",
        "params": row.get("params") or {},
        "status": row.get("status") or "draft",
        "source": row.get("source") or "manual",
        "created_at": row.get("created_at") or "",
        "updated_at": row.get("updated_at") or "",
        "chars": len(body),
    }


def _draft_fields(data: dict[str, Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for key in _DRAFT_TEXT_FIELDS:
        if key in data:
            fields[key] = str(data.get(key) or "")
    for key in ("titles", "tags", "media"):
        if key in data:
            fields[key] = _str_list(data.get(key))
    if "params" in data and isinstance(data.get("params"), dict):
        fields["params"] = data["params"]
    return fields


def drafts_list(request: Request) -> Response:
    """草稿列表。``?status=draft`` 只看草稿；默认也是草稿，避免把已发布的混进来。"""
    status = request.q("status") or "draft"
    limit = _int_arg(request, "limit", 200, 1, 500)
    rows = [_content_row(r) for r in db.list_contents(status=status, limit=500)]
    keyword = request.q("query").strip().lower()
    platform = request.q("platform").strip()
    if platform:
        rows = [r for r in rows if r["platform"] == platform]
    if keyword:
        rows = [
            r
            for r in rows
            if keyword in f"{r['topic']} {r['title']} {r['excerpt']} {' '.join(r['tags'])}".lower()
        ]
    return Response({"items": rows[:limit], "total": len(rows), "status": status})


def drafts_get(request: Request) -> Response:
    content_id = _int_arg(request, "id", 0, 0, 1 << 30)
    if not content_id:
        raise ApiError("缺少草稿 id")
    row = db.get_content(content_id)
    if row is None:
        raise ApiError("草稿不存在，可能已被删除", status=404, code="not_found")
    return Response({"draft": _content_row(row)})


def drafts_save(request: Request) -> Response:
    """新建或更新草稿。带 ``id`` 即更新，否则新建。"""
    data = request.json()
    fields = _draft_fields(data)
    if not fields.get("body", "").strip() and not fields.get("topic", "").strip():
        raise ApiError("草稿至少要有主题或正文，否则存下来也没法用")

    content_id = 0
    try:
        content_id = int(data.get("id") or 0)
    except (TypeError, ValueError):
        raise ApiError("id 必须是整数") from None

    if content_id:
        existing = db.get_content(content_id)
        if existing is None:
            raise ApiError("草稿不存在，可能已被删除；请另存为新草稿", status=404, code="not_found")
        db.update_content(content_id, **fields)
    else:
        payload = {**fields}
        payload.setdefault("status", "draft")
        payload["status"] = "draft"
        payload.setdefault("source", "manual")
        content_id = db.create_content(**payload)

    row = db.get_content(content_id)
    if row is None:  # pragma: no cover - 写后读不回来属于存储异常
        raise ApiError("草稿保存后读不回来，请检查数据目录权限", status=500)
    return Response({"draft": _content_row(row), "created": not bool(data.get("id"))})


def drafts_delete(request: Request) -> Response:
    content_id = _int_arg(request, "id", 0, 0, 1 << 30)
    if not content_id:
        raise ApiError("缺少草稿 id")
    if db.get_content(content_id) is None:
        raise ApiError("草稿不存在，可能已被删除", status=404, code="not_found")
    db.delete_content(content_id)
    return Response({"ok": True, "id": content_id})


# --------------------------------------------------------------------------
# 数据分析
# --------------------------------------------------------------------------
def _analytics():
    """惰性构造：analytics 模块体积不小，冷启动路径上不必提前加载。"""
    from ..services.analytics import AnalyticsService  # noqa: PLC0415

    return AnalyticsService()


def _metric_summary(row: dict[str, Any]) -> dict[str, Any]:
    latest = db.latest_metric(int(row.get("id") or 0)) or {}
    return {
        "views": int(latest.get("views") or 0),
        "likes": int(latest.get("likes") or 0),
        "comments": int(latest.get("comments") or 0),
        "collects": int(latest.get("collects") or 0),
        "shares": int(latest.get("shares") or 0),
        "captured_at": latest.get("captured_at") or "",
    }


def analytics_overview(request: Request) -> Response:
    days = _int_arg(request, "days", 30, 1, 3650)
    platform = request.q("platform").strip() or None
    service = _analytics()
    return Response(
        {
            "days": days,
            "platform": platform,
            "overview": _dict(service.overview(days=days, platform=platform)),
            "breakdown": _dict(service.platform_breakdown(days=days)),
            "importable_platforms": [
                {"key": key, "label": platform_label(key)} for key in service.importable_platforms()
            ],
            "llm_ready": config_manager.ready(),
        }
    )


def analytics_trend(request: Request) -> Response:
    days = _int_arg(request, "days", 30, 1, 3650)
    platform = request.q("platform").strip() or None
    metric = request.q("metric") or "views"
    cumulative = request.q("cumulative", "1").lower() not in {"0", "false", "no"}
    try:
        series = _analytics().trend(
            days=days, platform=platform, metric=metric, cumulative=cumulative
        )
    except ValueError as exc:
        raise ApiError(str(exc)) from None
    return Response({"series": series, "days": days, "metric": metric, "cumulative": cumulative})


def analytics_rank(request: Request) -> Response:
    days = _int_arg(request, "days", 30, 1, 3650)
    limit = _int_arg(request, "limit", 20, 1, 200)
    by = request.q("by") or "engagement"
    try:
        items = _analytics().rank_contents(days=days, by=by, limit=limit)
    except ValueError as exc:
        raise ApiError(str(exc)) from None
    rows = _dict(items)
    for row in rows:
        row["platform_label"] = platform_label(row.get("platform") or "")
    return Response({"items": rows, "days": days, "by": by})


def analytics_posts(request: Request) -> Response:
    """作品记录（含未同步到指标的），支持平台 / 状态 / 关键词与分页。"""
    limit = _int_arg(request, "limit", 100, 1, 500)
    offset = _int_arg(request, "offset", 0, 0, 1 << 30)
    platform = request.q("platform").strip() or None
    status = request.q("status").strip() or None
    query = request.q("query").strip()
    rows = db.list_posts(status=status, platform=platform, limit=limit, offset=offset, query=query)
    total = db.count_posts(status=status, platform=platform, query=query)
    items = []
    for row in rows:
        status_key = str(row.get("status") or "")
        items.append(
            {
                "id": int(row.get("id") or 0),
                "platform": row.get("platform") or "",
                "platform_label": platform_label(row.get("platform") or ""),
                "title": row.get("title") or "",
                "url": row.get("url") or "",
                "status": status_key,
                "status_label": POST_STATUS_LABELS.get(status_key, status_key or "未知"),
                "error": row.get("error") or "",
                "published_at": row.get("published_at") or "",
                "created_at": row.get("created_at") or "",
                "last_synced": row.get("last_synced") or "",
                "metrics": _metric_summary(row),
            }
        )
    return Response({"items": items, "total": total, "offset": offset, "limit": limit})


def analytics_importable(request: Request) -> Response:
    """哪些平台支持「从已登录账号导入作品」，供前端禁用入口并给出原因。"""
    service = _analytics()
    supported = set(service.importable_platforms())
    rows = []
    for key in registered_platforms():
        ok = key in supported
        rows.append(
            {
                "key": key,
                "label": platform_label(key),
                "supported": ok,
                "hint": (
                    "" if ok else f"{platform_label(key)}暂不支持账号导入，请用「粘贴作品链接」登记"
                ),
            }
        )
    return Response({"platforms": rows})


def analytics_import(request: Request) -> Response:
    """从已登录账号批量导入作品（后台任务）。

    一次最多 200 条；重复作品由 ``register_post`` 按平台 + 作品标识去重，
    返回新增 / 已存在 / 失败三项计数。
    """
    data = request.json()
    platform = str(data.get("platform") or "").strip().lower()
    if not platform:
        raise ApiError("请选择要导入的平台")
    try:
        limit = max(1, min(int(data.get("limit") or 50), 200))
    except (TypeError, ValueError):
        raise ApiError("limit 必须是整数") from None

    service = _analytics()
    if not service.supports_account_import(platform):
        raise ApiError(
            f"{platform_label(platform)}暂不支持从账号导入作品，请用「粘贴作品链接」逐个登记"
        )
    busy = [job for job in jobs.running("analytics") if job.label.startswith("导入作品")]
    if busy:
        raise ApiError("已有一个账号导入任务在进行，请等它结束", status=409, code="busy")

    def work(job) -> dict[str, Any]:
        job.progress("正在打开平台账号页读取作品列表…", 10)
        result = service.discover_account_posts(platform, limit=limit)
        posts = result.pop("posts", []) or []
        result["samples"] = [
            str(getattr(item, "title", "") or getattr(item, "url", ""))[:60] for item in posts[:5]
        ]
        job.message = str(result.get("message") or ("导入完成" if result.get("ok") else "导入失败"))
        return result

    job = jobs.start(
        "analytics",
        f"导入作品·{platform_label(platform)}",
        work,
        message="正在准备导入…",
    )
    return Response({"job": job.snapshot(with_result=False)})


def analytics_post_sync(request: Request) -> Response:
    """单独刷新一条作品的指标（作品多时不必全量同步）。"""
    data = request.json()
    try:
        post_id = int(data.get("id") or 0)
    except (TypeError, ValueError):
        raise ApiError("id 必须是整数") from None
    if not post_id:
        raise ApiError("缺少记录 id")
    row = db.get_post(post_id)
    if row is None:
        raise ApiError("记录不存在", status=404, code="not_found")
    if not str(row.get("url") or "").strip():
        raise ApiError("该记录没有作品链接，无法拉取指标；请先补上链接")

    service = _analytics()

    def work(job) -> dict[str, Any]:
        job.progress("正在打开作品页读取指标…", 30)
        result = service.sync_one(post_id)
        job.message = str(
            result.get("message") or ("指标已更新" if result.get("ok") else "未取到指标")
        )
        return result

    job = jobs.start("analytics", "刷新单条指标", work, message="准备中…")
    return Response({"job": job.snapshot(with_result=False)})


def analytics_register(request: Request) -> Response:
    """登记一条平台上已有的作品，并顺手拉一次指标（后台任务）。"""
    data = request.json()
    url = str(data.get("url") or "").strip()
    if not url:
        raise ApiError("请填写作品链接")
    service = _analytics()
    result = service.register_post(
        url,
        platform=str(data.get("platform") or "").strip(),
        title=str(data.get("title") or "").strip(),
    )
    if not result.get("ok"):
        raise ApiError(str(result.get("message") or "登记失败"))

    post_id = int(result.get("post_id") or 0)
    job_payload = None
    if post_id:
        job = jobs.start(
            "analytics",
            "拉取作品指标",
            lambda current: service.sync_one(post_id),
            message="正在打开平台页面读取指标…",
        )
        job_payload = job.snapshot(with_result=False)
    return Response(
        {
            "ok": True,
            "post_id": post_id,
            "message": result.get("message") or "已登记",
            "duplicated": bool(result.get("duplicated")),
            "job": job_payload,
        }
    )


def analytics_delete_post(request: Request) -> Response:
    post_id = _int_arg(request, "id", 0, 0, 1 << 30)
    if not post_id:
        raise ApiError("缺少记录 id")
    result = _analytics().delete_post(post_id)
    if not result.get("ok"):
        raise ApiError(str(result.get("message") or "删除失败"))
    return Response({"ok": True, "id": post_id})


def analytics_sync(request: Request) -> Response:
    """增量同步指标。开浏览器逐条访问，必须放后台任务。"""
    data = request.json()
    force = bool(data.get("force"))
    platforms = _str_list(data.get("platforms")) or None
    service = _analytics()

    def work(job) -> dict[str, Any]:
        def on_progress(message: str, percent: int = -1) -> None:
            job.progress(message, percent)

        result = service.sync_metrics(platforms=platforms, force=force, on_progress=on_progress)
        synced = int(result.get("synced") or 0)
        failed = int(result.get("failed") or 0)
        skipped = int(result.get("skipped") or 0)
        job.message = f"同步完成：成功 {synced} 条，失败 {failed} 条，跳过 {skipped} 条"
        return result

    job = jobs.start("analytics", "同步指标", work, message="正在准备同步…")
    return Response({"job": job.snapshot(with_result=False)})


def analytics_attribute(request: Request) -> Response:
    """归因回流：分析 Top / Bottom 内容并写入画像记忆（LLM 可能较慢，走任务）。"""
    data = request.json()
    try:
        days = max(1, min(int(data.get("days") or 30), 3650))
    except (TypeError, ValueError):
        raise ApiError("days 必须是整数") from None
    persist = data.get("persist", True) is not False
    service = _analytics()

    def work(job) -> dict[str, Any]:
        result = service.attribute(
            days=days,
            persist=persist,
            on_progress=lambda message, percent=-1: job.progress(message, percent),
        )
        job.message = str(
            result.get("message") or ("归因完成" if result.get("ok") else "归因未完成")
        )
        return result

    job = jobs.start("analytics", "归因回流", work, message="正在分析表现最好与最差的内容…")
    return Response({"job": job.snapshot(with_result=False)})


# --------------------------------------------------------------------------
# 发布中心（P2：登录、填写、确认发布、发布记录）
# --------------------------------------------------------------------------
def _publish_service() -> PublishService:
    return PublishService()


def _env_payload() -> dict[str, Any]:
    status = environment_status()
    rows = []
    for entry in PublishService.platforms():
        rows.append(
            {
                "key": entry.get("key", ""),
                "label": entry.get("label", ""),
                "available": bool(entry.get("available")),
                "needs_media": bool(entry.get("needs_media")),
            }
        )
    return {
        "ready": bool(status.ready),
        "code": status.code,
        "title": status.title,
        "detail": status.detail,
        "size_hint": status.size_hint,
        "system_browser": system_browser_label(system_browser_channel()) or "",
        "platforms": rows,
    }


def publish_env(request: Request) -> Response:
    """发布运行环境 + 各平台可用性。只做文件系统探测，可以直接同步返回。"""
    return Response(_env_payload())


def publish_login(request: Request) -> Response:
    """登录态检测 / 打开登录窗口。都要开浏览器，统一走后台任务。"""
    data = request.json()
    platform = str(data.get("platform") or "").strip()
    if not platform:
        raise ApiError("请选择目标平台")
    action = str(data.get("action") or "check")
    if action not in {"check", "login", "logout"}:
        raise ApiError(f"不支持的操作：{action}")
    service = _publish_service()

    def work(job) -> dict[str, Any]:
        if action == "check":
            job.message = "正在检测登录状态…"
            ok, message = service.check_login(platform)
            job.message = message or ("已登录" if ok else "未登录")
            return {"ok": ok, "message": message, "action": action}
        if action == "logout":
            job.message = "正在退出登录…"
            ok, message = service.logout(platform)
            return {"ok": ok, "message": message, "action": action}
        job.message = f"请在弹出的浏览器窗口中完成{platform_label(platform)}登录…"
        ok, message = service.login(platform)
        job.message = message or ("登录成功" if ok else "登录未完成")
        return {"ok": ok, "message": message, "action": action}

    label = {"check": "检测登录", "login": "登录", "logout": "退出登录"}[action]
    job = jobs.start("publish", f"{label}·{platform_label(platform)}", work, message="准备中…")
    return Response({"job": job.snapshot(with_result=False)})


def publish_install(request: Request) -> Response:
    """一键安装发布环境（Playwright 组件 + Chromium 内核）。

    下载约 150MB，必须后台跑；进度与失败原因都通过任务状态回传，
    前端据此显示「正在下载内核 40%」而不是干等。

    ``force=true``：系统已有 Edge/Chrome 时也强制下载内置内核（备用路径）。
    """
    data = request.json()
    force = bool(data.get("force"))
    service = _publish_service()
    if jobs.running("publish"):
        raise ApiError("已有发布相关任务在进行，请等它结束", status=409, code="busy")

    def work(job) -> dict[str, Any]:
        ok, message = service.install_environment(
            on_progress=lambda msg, percent=-1: job.progress(msg, percent),
            cancel=job.cancel,
            force=force,
        )
        job.message = message
        return {"ok": ok, "message": message, "env": _env_payload()}

    job = jobs.start("publish", "配置发布环境", work, message="正在准备安装…")
    return Response({"job": job.snapshot(with_result=False)})


def publish_start(request: Request) -> Response:
    """检查通过后执行发布。

    两条硬约束（与 Qt 版一致，也是产品红线）：

    1. 必须带 ``confirmed=true``，否则直接拒绝——Stent 不提供静默自动发布；
    2. ``dry_run`` 默认 **true**：只填表停在最后一步，由用户在浏览器里确认提交。
       真要直接提交必须在前端显式选择，并且仍然带 ``confirmed``。
    """
    data = request.json()
    if not data.get("confirmed"):
        db.log_action("", "denied", "Web 发布缺少人工确认，已拒绝")
        raise ApiError(
            "发布需要人工确认：请先勾选「我已核对内容」再执行",
            status=400,
            code="not_confirmed",
        )

    platform = str(data.get("platform") or "").strip()
    if not platform:
        raise ApiError("请选择目标平台")
    body = str(data.get("body") or "").strip()
    if not body:
        raise ApiError("正文为空，先创作或粘贴内容")
    dry_run = data.get("dry_run", True) is not False

    existing = jobs.running("publish")
    if any(job.label.startswith(("填写", "发布")) for job in existing):
        raise ApiError("已有一个发布任务在进行，请等它结束或先取消", status=409, code="busy")

    fields: dict[str, Any] = {
        "topic": str(data.get("topic") or ""),
        "platform": platform,
        "title": str(data.get("title") or ""),
        "titles": _str_list(data.get("titles")),
        "body": body,
        "summary": str(data.get("summary") or ""),
        "tags": _str_list(data.get("tags")),
        "status": "ready",
    }
    content_id = 0
    try:
        content_id = int(data.get("content_id") or 0)
    except (TypeError, ValueError):
        raise ApiError("content_id 必须是整数") from None

    if content_id and db.get_content(content_id) is None:
        content_id = 0  # 草稿被别处删了：不报错，重新落一条，避免用户白填
    if content_id:
        db.update_content(content_id, **fields)
    else:
        content_id = db.create_content(source="web", **fields)

    media = _str_list(data.get("media"))
    service = _publish_service()
    try:
        preparation = service.prepare(content_id, platform, media=media, overwrite_platform=True)
    except Exception as exc:  # noqa: BLE001 - 准备失败要变成可读 400/500
        log.exception("发布准备失败")
        raise ApiError(f"发布准备失败：{exc}", status=500, code="prepare_failed") from exc

    def work(job) -> dict[str, Any]:
        outcome = service.publish(
            preparation,
            confirmed=True,
            dry_run=dry_run,
            on_progress=lambda msg, percent=-1: job.progress(msg, percent),
            cancel=job.cancel,
        )
        job.message = outcome.message or ("已完成" if outcome.ok else "发布未完成")
        detail = dict(outcome.detail or {})
        warnings = [str(w) for w in (detail.get("warnings") or []) if str(w).strip()]
        return {
            "ok": bool(outcome.ok),
            "platform": outcome.platform,
            "platform_label": platform_label(outcome.platform),
            "post_id": int(outcome.post_id or 0),
            "url": outcome.url or "",
            "message": outcome.message or "",
            "dry_run": bool(outcome.dry_run),
            "warnings": warnings,
            "content_id": content_id,
        }

    label = f"填写·{platform_label(platform)}" if dry_run else f"发布·{platform_label(platform)}"
    job = jobs.start("publish", label, work, message="正在打开浏览器…")
    return Response(
        {
            "job": job.snapshot(with_result=False),
            "content_id": content_id,
            "platform": platform,
            "label": platform_label(platform),
            "dry_run": dry_run,
            "supported": preparation.supported,
            "passed": preparation.passed,
            "preview": preparation.preview,
            "checks": _report_dict(preparation.report) if preparation.report else None,
            "notes": list(preparation.adapted_notes),
            "support_note": preparation.support_note,
        }
    )


def publish_history(request: Request) -> Response:
    limit = _int_arg(request, "limit", 100, 1, 500)
    platform = request.q("platform").strip() or None
    rows = PublishService().history(platform=platform, limit=limit)
    items = []
    for row in rows:
        status_key = str(row.get("status") or "")
        items.append(
            {
                "id": int(row.get("id") or 0),
                "platform": row.get("platform") or "",
                "platform_label": platform_label(row.get("platform") or ""),
                "title": row.get("title") or "",
                "url": row.get("url") or "",
                "status": status_key,
                "status_label": POST_STATUS_LABELS.get(status_key, status_key or "未知"),
                "error": row.get("error") or "",
                "published_at": row.get("published_at") or "",
                "created_at": row.get("created_at") or "",
                "last_synced": row.get("last_synced") or "",
            }
        )
    return Response({"items": items, "total": len(items)})


def publish_mark(request: Request) -> Response:
    """用户在浏览器里完成（或放弃）后，回来更新记录状态。"""
    data = request.json()
    try:
        post_id = int(data.get("post_id") or 0)
    except (TypeError, ValueError):
        raise ApiError("post_id 必须是整数") from None
    if not post_id:
        raise ApiError("缺少记录 id")
    if db.get_post(post_id) is None:
        raise ApiError("记录不存在", status=404, code="not_found")
    action = str(data.get("action") or "published")
    service = PublishService()
    if action == "published":
        service.mark_published_manually(post_id, url=str(data.get("url") or "").strip())
    elif action == "failed":
        service.mark_failed_manually(post_id, reason=str(data.get("reason") or "").strip())
    elif action == "cancelled":
        service.cancel_post(post_id, reason=str(data.get("reason") or "").strip())
    else:
        raise ApiError(f"不支持的操作：{action}")
    row = db.get_post(post_id) or {}
    return Response(
        {
            "ok": True,
            "post": {
                "id": post_id,
                "status": row.get("status") or "",
                "status_label": POST_STATUS_LABELS.get(
                    str(row.get("status") or ""), str(row.get("status") or "")
                ),
                "url": row.get("url") or "",
            },
        }
    )


def publish_logs(request: Request) -> Response:
    limit = _int_arg(request, "limit", 100, 1, 500)
    rows = PublishService().logs(limit=limit)
    return Response(
        {
            "items": [
                {
                    "id": int(row.get("id") or 0),
                    "platform": row.get("platform") or "",
                    "platform_label": platform_label(row.get("platform") or ""),
                    "action": row.get("action") or "",
                    "detail": row.get("detail") or "",
                    "post_id": row.get("post_id"),
                    "created_at": row.get("created_at") or "",
                }
                for row in rows
            ]
        }
    )


# --------------------------------------------------------------------------
# 后台任务
# --------------------------------------------------------------------------
def job_get(request: Request) -> Response:
    job = jobs.get(request.q("id"))
    if job is None:
        raise ApiError("任务不存在或已过期", status=404, code="not_found")
    return Response({"job": job.snapshot()})


def job_cancel(request: Request) -> Response:
    data = request.json()
    job = jobs.cancel(str(data.get("id") or ""))
    if job is None:
        raise ApiError("任务不存在或已过期", status=404, code="not_found")
    return Response({"job": job.snapshot(with_result=False)})


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

    # 草稿箱
    app.route("GET", "/api/drafts")(drafts_list)
    app.route("GET", "/api/drafts/get")(drafts_get)
    app.route("POST", "/api/drafts")(drafts_save)
    app.route("DELETE", "/api/drafts")(drafts_delete)

    # 数据分析
    app.route("GET", "/api/analytics/overview")(analytics_overview)
    app.route("GET", "/api/analytics/trend")(analytics_trend)
    app.route("GET", "/api/analytics/rank")(analytics_rank)
    app.route("GET", "/api/analytics/posts")(analytics_posts)
    app.route("POST", "/api/analytics/posts")(analytics_register)
    app.route("DELETE", "/api/analytics/posts")(analytics_delete_post)
    app.route("POST", "/api/analytics/posts/sync")(analytics_post_sync)
    app.route("GET", "/api/analytics/importable")(analytics_importable)
    app.route("POST", "/api/analytics/import")(analytics_import)
    app.route("POST", "/api/analytics/sync")(analytics_sync)
    app.route("POST", "/api/analytics/attribute")(analytics_attribute)

    # 发布中心
    app.route("GET", "/api/publish/env")(publish_env)
    app.route("POST", "/api/publish/install")(publish_install)
    app.route("POST", "/api/publish/login")(publish_login)
    app.route("POST", "/api/publish/start")(publish_start)
    app.route("GET", "/api/publish/history")(publish_history)
    app.route("POST", "/api/publish/mark")(publish_mark)
    app.route("GET", "/api/publish/logs")(publish_logs)

    # 长任务
    app.route("GET", "/api/jobs")(job_get)
    app.route("POST", "/api/jobs/cancel")(job_cancel)

    return app
