"""发布中心编排（企划书 3.2 模块三）。

流程严格按企划书实现：
    选平台 → 格式适配 → 发布前检查 → 预览 → **人工确认** → 发布

风控约束（企划书 3.2 / 第七章）：
- 默认强制人工确认：:meth:`PublishService.publish` 必须显式传入 ``confirmed=True``，
  否则直接拒绝执行；
- 不提供任何「全自动发布」开关；
- 默认动作是 ``dry_run=True``（自动填好表单停在最后一步），由用户在浏览器里做最终判断；
- 每次发布都写 publish_log 审计留痕。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from ..core.db import db, now
from ..platforms import (
    PlatformAdapter,
    PlatformLimits,
    PublishPayload,
    available_platforms,
    get_adapter,
    platform_label,
    platform_limits,
    registered_platforms,
)

log = logging.getLogger(__name__)

#: 发布记录状态 → 中文文案。
#: 统一放在服务层，避免 UI 与日志各写一套导致「同一个状态两种说法」。
POST_STATUS_LABELS: dict[str, str] = {
    "pending": "进行中",
    "draft_filled": "待你确认",
    "published": "已发布",
    "failed": "失败",
    "cancelled": "已取消",
}

#: 状态 → 语义色键（UI 据此上色，见 theme.level_color）
POST_STATUS_TONES: dict[str, str] = {
    "pending": "busy",
    "draft_filled": "warn",
    "published": "success",
    "failed": "error",
    "cancelled": "idle",
}

#: 需要用户处理（回浏览器确认或重试）的状态
POST_STATUS_OPEN: tuple[str, ...] = ("pending", "draft_filled")


def post_status_label(status: str) -> str:
    return POST_STATUS_LABELS.get(status or "", status or "未知")


def post_status_tone(status: str) -> str:
    return POST_STATUS_TONES.get(status or "", "idle")


class PublishDenied(RuntimeError):
    """发布被拒绝（缺少人工确认或存在阻断性问题）。"""


@dataclass
class PublishPreparation:
    """「选平台 → 格式适配 → 检查 → 预览」阶段的产物。"""

    platform: str
    label: str
    content_id: int | None
    payload: PublishPayload
    preview: str = ""
    report: Any | None = None  # precheck.CheckReport
    limits: PlatformLimits | None = None
    supported: bool = True
    support_note: str = ""
    adapted_notes: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        if self.report is None:
            return True
        return bool(getattr(self.report, "passed", True))

    @property
    def score(self) -> int:
        return int(getattr(self.report, "score", 0) or 0)

    def report_text(self) -> str:
        if self.report is None:
            return "尚未执行发布前检查"
        try:
            return self.report.to_text()
        except Exception:  # pragma: no cover
            return str(self.report)


@dataclass
class PublishOutcome:
    ok: bool
    platform: str
    post_id: int = 0
    url: str = ""
    message: str = ""
    dry_run: bool = False
    detail: dict[str, Any] = field(default_factory=dict)


class PublishService:
    """发布编排服务（同步接口，供 UI 工作线程调用）。"""

    def __init__(self, database: Any = None) -> None:
        self._db = database or db
        self._precheck: Any = None

    # -- 发布前检查（惰性加载，precheck 依赖较重）-------------------------
    @property
    def precheck(self) -> Any:
        if self._precheck is None:
            from .precheck import PrecheckService  # 惰性导入

            self._precheck = PrecheckService()
        return self._precheck

    # -- 平台信息 --------------------------------------------------------
    @staticmethod
    def platforms(*, only_available: bool = False) -> list[dict[str, Any]]:
        """列出发布平台（含可用性与限制），供 UI 渲染。"""
        keys = available_platforms() if only_available else registered_platforms()
        out: list[dict[str, Any]] = []
        for key in keys:
            adapter = get_adapter(key)
            limits = platform_limits(key)
            out.append(
                {
                    "key": key,
                    "label": platform_label(key),
                    "available": adapter is not None,
                    "needs_media": bool(getattr(adapter, "needs_media", limits.requires_media)),
                    "limits": limits,
                }
            )
        return out

    @staticmethod
    def adapter(platform: str) -> PlatformAdapter | None:
        return get_adapter(platform)

    # -- 格式适配 --------------------------------------------------------
    def build_payload(
        self,
        content: dict[str, Any],
        *,
        media: list[str] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> tuple[PublishPayload, list[str]]:
        """把一条内容适配为目标平台可发布的 payload，返回 (payload, 适配说明)。"""
        notes: list[str] = []
        platform = content.get("platform", "")
        limits = platform_limits(platform)

        titles = content.get("titles") or []
        title = (content.get("title") or (titles[0] if titles else "")).strip()
        body = (content.get("body") or "").strip()
        tags = list(content.get("tags") or [])
        summary = (content.get("summary") or "").strip()

        if title and len(title) > limits.title_max:
            notes.append(f"标题超出 {limits.title_max} 字，已自动截断，建议回创作页精简")
            title = title[: limits.title_max]
        if len(tags) > limits.tags_max:
            notes.append(f"标签数超出上限 {limits.tags_max}，已保留前 {limits.tags_max} 个")
            tags = tags[: limits.tags_max]

        files = [str(p) for p in (media if media is not None else content.get("media") or []) if p]
        if limits.media_max and len(files) > limits.media_max:
            notes.append(f"媒体数量超出上限 {limits.media_max}，已保留前 {limits.media_max} 个")
            files = files[: limits.media_max]

        payload = PublishPayload(
            title=title,
            body=body,
            tags=tags,
            summary=summary,
            topic=content.get("topic", "") or "",
            media=files,
            extra=extra or {},
        )
        if limits.requires_media and not files:
            notes.append(f"{platform_label(platform)} 发布需要媒体文件，请先在发布页选择")
        return payload, notes

    # -- 主流程：准备 ----------------------------------------------------
    def prepare(
        self,
        content_id: int,
        platform: str,
        *,
        media: list[str] | None = None,
        extra: dict[str, Any] | None = None,
        overwrite_platform: bool = False,
    ) -> PublishPreparation:
        """完成「选平台 → 格式适配 → 发布前检查 → 预览」四步。"""
        content = self._db.get_content(content_id)
        if content is None:
            raise PublishError(f"内容不存在（id={content_id}）")

        if overwrite_platform and content.get("platform") != platform:
            content = {**content, "platform": platform}
        payload, notes = self.build_payload(content, media=media, extra=extra)
        limits = platform_limits(platform)
        adapter = get_adapter(platform)
        supported = adapter is not None
        support_note = "" if supported else f"{platform_label(platform)}的自动化适配器暂不可用，可复制文案后人工发布"

        try:
            report = self.precheck.check(payload, limits, platform=platform)
        except Exception as exc:  # noqa: BLE001 - 检查器异常不应阻断预览
            log.exception("发布前检查执行失败")
            report = _FallbackReport(str(exc))

        return PublishPreparation(
            platform=platform,
            label=platform_label(platform),
            content_id=content_id,
            payload=payload,
            preview=self.preview_text(payload, platform),
            report=report,
            limits=limits,
            supported=supported,
            support_note=support_note,
            adapted_notes=notes,
        )

    @staticmethod
    def preview_text(payload: PublishPayload, platform: str) -> str:
        """生成「发布后长这样」的文本预览。"""
        label = platform_label(platform)
        lines = [f"—— {label} 发布预览 ——", ""]
        if payload.title:
            lines.append(f"【标题】{payload.title}")
            lines.append("")
        lines.append(payload.body or "（正文为空）")
        if payload.tags:
            lines.append("")
            lines.append("【标签】" + " ".join(t if t.startswith("#") else f"#{t}" for t in payload.tags))
        if payload.media:
            lines.append("")
            lines.append(f"【媒体】{len(payload.media)} 个文件")
            lines.extend(f"  · {p}" for p in payload.media[:5])
            if len(payload.media) > 5:
                lines.append(f"  · …等共 {len(payload.media)} 个")
        lines.append("")
        lines.append(f"【字数】标题 {len(payload.title)} / 正文 {len(payload.body)}")
        return "\n".join(lines)

    # -- 登录 ------------------------------------------------------------
    def check_login(self, platform: str) -> tuple[bool, str]:
        adapter = get_adapter(platform)
        if adapter is None:
            return False, f"{platform_label(platform)}的适配器不可用"
        try:
            return adapter.check_login(headless=True)
        except Exception as exc:  # noqa: BLE001
            log.exception("登录态检测失败：%s", platform)
            return False, f"检测失败：{exc}"

    def login(self, platform: str, *, on_progress: Callable | None = None, timeout_sec: int = 300) -> tuple[bool, str]:
        adapter = get_adapter(platform)
        if adapter is None:
            return False, f"{platform_label(platform)}的适配器不可用"
        try:
            return adapter.login(timeout_sec=timeout_sec)
        except Exception as exc:  # noqa: BLE001
            log.exception("登录失败：%s", platform)
            return False, f"登录失败：{exc}"

    def logout(self, platform: str) -> tuple[bool, str]:
        adapter = get_adapter(platform)
        if adapter is None:
            return False, "适配器不可用"
        try:
            return adapter.logout()
        except Exception as exc:  # noqa: BLE001
            return False, f"退出失败：{exc}"

    # -- 发布 ------------------------------------------------------------
    def publish(
        self,
        preparation: PublishPreparation,
        *,
        confirmed: bool,
        dry_run: bool = True,
        on_progress: Callable | None = None,
        timeout_sec: int = 300,
    ) -> PublishOutcome:
        """执行发布。

        :param confirmed: 人工确认标记，必须为 True（企划书风险控制要求）
        :param dry_run: True = 只自动填写表单停在最后一步，不点击发布按钮
        """
        platform = preparation.platform
        label = preparation.label
        db_ = self._db

        if not confirmed:
            db_.log_action(platform, "denied", "缺少人工确认，已拒绝发布")
            raise PublishDenied("发布已取消：该操作必须经过人工确认（Stent 不提供全自动发布）")

        if not preparation.supported:
            db_.log_action(platform, "unsupported", preparation.support_note)
            return PublishOutcome(
                ok=False,
                platform=platform,
                message=preparation.support_note or "该平台暂不支持自动发布，请复制文案后人工发布",
            )

        if not preparation.passed:
            errors = getattr(preparation.report, "errors", lambda: [])()
            detail = "；".join(getattr(i, "title", str(i)) for i in errors[:3])
            db_.log_action(platform, "blocked", f"发布前检查未通过：{detail}")
            return PublishOutcome(
                ok=False,
                platform=platform,
                message=f"发布前检查存在阻断问题，请先修改：{detail}",
            )

        adapter = get_adapter(platform)
        if adapter is None:
            return PublishOutcome(ok=False, platform=platform, message="适配器不可用")

        # 先落一条 pending 记录，保证失败也能追溯
        post_id = db_.create_post(
            content_id=preparation.content_id,
            platform=platform,
            title=preparation.payload.title,
            body=preparation.payload.body,
            status="pending",
        )
        db_.log_action(platform, "publish_start", f"dry_run={dry_run}", post_id=post_id)

        try:
            result = adapter.publish(
                preparation.payload,
                headless=False,
                dry_run=dry_run,
                timeout_sec=timeout_sec,
                on_progress=on_progress,
            )
        except Exception as exc:  # noqa: BLE001 - 保留异常信息到记录
            log.exception("发布异常：%s", platform)
            db_.update_post(post_id, status="failed", error=str(exc))
            db_.log_action(platform, "publish_error", str(exc), post_id=post_id)
            return PublishOutcome(ok=False, platform=platform, post_id=post_id, message=f"发布异常：{exc}", dry_run=dry_run)

        if result.ok:
            status = "draft_filled" if dry_run else "published"
            db_.update_post(
                post_id,
                status=status,
                url=result.url or "",
                published_at=None if dry_run else now(),
                error="",
                last_synced="",
            )
            if preparation.content_id:
                db_.update_content(
                    preparation.content_id,
                    status="ready" if dry_run else "published",
                    platform=platform,
                )
            db_.log_action(platform, "publish_ok" if not dry_run else "publish_dry_run", result.message, post_id=post_id)
            return PublishOutcome(
                ok=True,
                platform=platform,
                post_id=post_id,
                url=result.url or "",
                message=result.message or ("已填写完成，请到浏览器中确认并发布" if dry_run else "发布成功"),
                dry_run=dry_run,
                detail=result.detail or {},
            )

        db_.update_post(post_id, status="failed", error=result.message)
        db_.log_action(platform, "publish_fail", result.message, post_id=post_id)
        return PublishOutcome(
            ok=False, platform=platform, post_id=post_id, message=result.message or "发布失败", dry_run=dry_run
        )

    # -- 记录 ------------------------------------------------------------
    def history(self, *, platform: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        return self._db.list_posts(platform=platform, limit=limit)

    def logs(self, limit: int = 100) -> list[dict[str, Any]]:
        return self._db.recent_logs(limit)

    def mark_published_manually(self, post_id: int, url: str = "") -> None:
        """用户在浏览器里手动完成最终发布后，回来标记为已发布。"""
        self._db.update_post(post_id, status="published", url=url, published_at=now(), error="")
        self._db.log_action("", "manual_confirm", f"用户在浏览器中完成发布，url={url}", post_id=post_id)

    def mark_failed_manually(self, post_id: int, reason: str = "") -> None:
        """用户回浏览器发现没发成功，手动标记失败（避免记录一直挂在「待确认」）。"""
        self._db.update_post(post_id, status="failed", error=reason or "用户标记为发布失败")
        self._db.log_action("", "manual_failed", reason or "用户标记为发布失败", post_id=post_id)

    def cancel_post(self, post_id: int, reason: str = "") -> None:
        """取消这次发布：记录保留但归档为「已取消」，不再占用待办。"""
        self._db.update_post(post_id, status="cancelled", error=reason or "用户取消了这次发布")
        self._db.log_action("", "cancel_post", reason or "用户取消发布", post_id=post_id)

    def open_posts(self) -> list[dict[str, Any]]:
        """仍需要用户处理的记录（进行中 / 待确认）。"""
        return [p for p in self._db.list_posts(limit=200) if p.get("status") in POST_STATUS_OPEN]

    # -- 运行环境 --------------------------------------------------------
    def install_environment(
        self,
        *,
        on_progress: Callable[[str, int], None] | None = None,
        cancel: Any = None,
    ) -> tuple[bool, str]:
        """一键安装发布运行环境（Playwright 组件 + Chromium 内核）。

        面向非技术用户：这里把「该敲哪条命令」变成一次点击。命令仍然会执行，
        只是不再需要用户自己复制粘贴——失败时把原始输出回传给界面供排查。
        """
        import subprocess  # noqa: PLC0415
        import sys  # noqa: PLC0415

        def report(message: str, percent: int = -1) -> None:
            if on_progress is None:
                return
            try:
                on_progress(message, percent)
            except Exception:  # pragma: no cover - 进度回调不应影响安装
                pass

        status = environment_status()
        if status.ready:
            return True, "发布环境已就绪"

        steps: list[tuple[str, list[str], int]] = []
        if status.code == "no_playwright":
            steps.append(
                (
                    "正在安装 Playwright 组件…",
                    [sys.executable, "-m", "pip", "install", "playwright"],
                    40,
                )
            )
        steps.append(
            (
                "正在下载浏览器内核（约 150 MB，请保持网络畅通）…",
                [sys.executable, "-m", "playwright", "install", "chromium"],
                100,
            )
        )

        for message, command, percent in steps:
            report(message, percent)
            ok, output = self._run_command(command, cancel=cancel)
            if not ok:
                tail = output.strip().splitlines()[-1:] or [""]
                return False, f"{message.rstrip('…')}失败：{tail[0][:200]}"

        # 装完之后必须清掉适配器缓存：首次探测失败时缓存里存的是 None，
        # 不清掉的话平台依旧显示「适配器不可用」。
        try:
            from ..platforms import reset_cache  # noqa: PLC0415

            reset_cache()
        except Exception:  # pragma: no cover
            log.debug("重置适配器缓存失败", exc_info=True)

        if environment_status().ready:
            return True, "发布环境安装完成，现在可以登录并发布了"
        return False, "安装命令已执行，但仍未检测到浏览器内核，请查看日志了解详情"

    @staticmethod
    def _run_command(
        command: list[str], *, cancel: Any = None, timeout_sec: int = 1800
    ) -> tuple[bool, str]:
        """执行外部命令并回收输出（供一键安装使用）。

        用后台线程读 stdout：主循环负责轮询取消与超时，这样即使用户中途点「中断」
        也能立刻杀掉子进程，不会被阻塞在 readline 上。
        """
        import subprocess  # noqa: PLC0415
        import threading  # noqa: PLC0415
        import time  # noqa: PLC0415

        flags = 0
        if hasattr(subprocess, "CREATE_NO_WINDOW"):  # Windows：不要弹黑框
            flags = subprocess.CREATE_NO_WINDOW
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=flags,
            )
        except Exception as exc:  # noqa: BLE001
            return False, f"无法启动安装程序：{exc}"

        collected: list[str] = []

        def pump() -> None:
            try:
                if process.stdout is not None:
                    for line in process.stdout:
                        collected.append(line.rstrip())
            except Exception:  # pragma: no cover - 管道关闭
                pass

        reader = threading.Thread(target=pump, daemon=True, name="stent-install-reader")
        reader.start()

        deadline = time.monotonic() + timeout_sec
        cancelled = False
        while process.poll() is None:
            if cancel is not None and getattr(cancel, "is_set", lambda: False)():
                cancelled = True
                process.kill()
                break
            if time.monotonic() > deadline:
                process.kill()
                return False, "安装超时，请检查网络后重试"
            time.sleep(0.2)

        reader.join(timeout=3)
        output = "\n".join(collected[-40:])
        if cancelled:
            return False, "安装已取消"
        return process.returncode == 0, output

    def export_text(self, preparation: PublishPreparation) -> str:
        """导出可粘贴的纯文本（适配器不可用或用户选择人工发布时使用）。"""
        payload = preparation.payload
        chunks = []
        if payload.title:
            chunks.append(payload.title)
        chunks.append(payload.body)
        if payload.tags:
            chunks.append(" ".join(t if t.startswith("#") else f"#{t}" for t in payload.tags))
        return "\n\n".join(c for c in chunks if c).strip()


class PublishError(RuntimeError):
    """发布准备阶段错误。"""


# --------------------------------------------------------------------------
# 浏览器内核检测
# --------------------------------------------------------------------------
@dataclass
class EnvironmentStatus:
    """发布运行环境的就绪状态。

    刻意与 :func:`browser_status` 分开：后者返回的是给开发者看的路径/命令，
    这里返回的是**给用户看的措辞**——普通用户不需要知道 Playwright 是什么。
    """

    ready: bool
    code: str = "ok"  # ok / no_playwright / no_chromium
    title: str = "发布环境已就绪"
    detail: str = ""
    #: 一键安装失败时供排查用的原始命令
    command: str = ""
    size_hint: str = ""


def _chromium_root():
    import os
    from pathlib import Path

    base = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if base:
        return Path(base)
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"
    return Path.home() / ".cache" / "ms-playwright"


def environment_status() -> EnvironmentStatus:
    """检测发布环境，返回面向用户的结论（只做文件系统探测，可在 UI 线程调用）。"""
    try:
        import playwright  # noqa: F401
    except ImportError:
        return EnvironmentStatus(
            ready=False,
            code="no_playwright",
            title="缺少发布组件",
            detail="发布功能需要一个配套组件，当前还没有装上。点下面的按钮自动装好，不需要你敲任何命令。",
            command="pip install playwright",
        )

    root = _chromium_root()
    if root.exists():
        candidates = list(root.glob("chromium*/**/chrome.exe")) + list(
            root.glob("chromium*/**/chrome")
        )
        if candidates:
            return EnvironmentStatus(
                ready=True,
                code="ok",
                title="发布环境已就绪",
                detail="浏览器内核已安装，登录与发布都可以正常使用。",
            )
    return EnvironmentStatus(
        ready=False,
        code="no_chromium",
        title="未检测到浏览器内核",
        detail="发布时需要浏览器内核来打开平台页面并填写表单，当前还没有下载。",
        command="python -m playwright install chromium",
        size_hint="约 150 MB",
    )


def browser_status() -> tuple[bool, str]:
    """兼容旧接口：返回 (是否就绪, 说明)。"""
    status = environment_status()
    if status.ready:
        return True, ""
    return False, status.detail


#: 各平台的登录态「痕迹」域名（Chromium 的 Cookies 库里按 host 存）。
#: 只用来做**本地快速判断**，不联网、不启动浏览器。
LOGIN_HINTS: dict[str, tuple[str, ...]] = {
    "xiaohongshu": ("xiaohongshu.com",),
    "douyin": ("douyin.com",),
    "zhihu": ("zhihu.com",),
    "bilibili": ("bilibili.com",),
}


def local_login_state(platform: str) -> tuple[bool, str]:
    """本地快速判断某平台是否「看起来已登录」。

    真正的登录校验必须启动浏览器（慢且吃资源），不适合进设置页就一次性跑四个平台。
    这里只检查持久化目录里有没有对应站点的 Cookie，毫秒级返回，
    用来给出「大概已登录 / 从未登录」的第一印象；需要确认时再点单平台的「检测」。
    """
    import os
    from pathlib import Path

    key = (platform or "").strip().lower()
    if not key:
        return False, "未指定平台"
    try:
        from .. import paths

        profile = Path(paths.browser_dir()) / key
    except Exception:  # noqa: BLE001
        return False, "无法定位登录态目录"
    if not profile.exists():
        return False, "从未登录过"

    cookies = list(profile.glob("**/Cookies")) + list(profile.glob("**/cookies.sqlite"))
    cookies = [c for c in cookies if c.is_file() and c.stat().st_size > 0]
    if not cookies:
        return False, "本地没有登录记录"

    hints = LOGIN_HINTS.get(key, ())
    if not hints:
        return True, "本地有登录记录"
    try:
        import sqlite3

        found = False
        for path in cookies:
            try:
                # 只读方式打开，避免和浏览器进程抢锁
                uri = f"file:{path.as_posix()}?mode=ro&immutable=1"
                with sqlite3.connect(uri, uri=True, timeout=1.0) as conn:
                    for hint in hints:
                        row = conn.execute(
                            "SELECT 1 FROM cookies WHERE host_key LIKE ? LIMIT 1", (f"%{hint}%",)
                        ).fetchone()
                        if row:
                            found = True
                            break
            except Exception:  # noqa: BLE001 - 库被占用或结构不同都不算错
                log.debug("读取 Cookie 库失败：%s", path, exc_info=True)
                continue
            if found:
                break
        if found:
            return True, "本地有登录记录"
        return False, "本地没有该站点的登录记录"
    except Exception:  # noqa: BLE001
        return True, "本地有登录记录（未能读取明细）"


def account_overview() -> list[dict[str, Any]]:
    """列出各平台的账号状态概览（供设置页「平台账号」栏目渲染）。"""
    from ..platforms import keys as platform_keys, platform_label

    out: list[dict[str, Any]] = []
    for key in platform_keys():
        logged, note = local_login_state(key)
        out.append(
            {
                "key": key,
                "label": platform_label(key),
                "logged_in": logged,
                "note": note,
                "supported": True,
            }
        )
    return out


@dataclass
class _FallbackReport:
    """检查器不可用时的兜底报告，避免阻断主流程。"""

    message: str = ""

    passed: bool = True
    score: int = 0

    def errors(self) -> list[Any]:
        return []

    def warnings(self) -> list[Any]:
        return []

    def to_text(self) -> str:
        return f"发布前检查未能执行：{self.message}\n已跳过检查，请自行确认内容合规。"


publish_service = PublishService()
