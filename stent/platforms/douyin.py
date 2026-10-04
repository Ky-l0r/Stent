"""抖音平台适配器（creator.douyin.com，Playwright 同步 API）。

移植来源：Easel ``skills/shared/scripts/douyin_publish.py``（选择器与流程见
``_reference/specs/douyin_zhihu_bilibili.md``）。已剥离 OpenClaw / anthropic /
content_guard / platform_readback / human_pace 等依赖，改为自包含实现。

设计要点：
- 只做**单次、人工触发**的发布；没有批量/全自动发布开关（企划书第七章风控约束）。
- ``dry_run=True`` 时把表单填完即返回，**绝不点击最终「发布」按钮**。
- 所有选择器都是模块级多候选元组，逐个尝试以抗平台改版。
- 所有浏览器动作都带超时与 try/except，失败返回中文说明，绝不向上抛异常。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from .base import (
    LoginState,
    MetricSnapshot,
    PlatformAdapter,
    PlatformLimits,
    PublishPayload,
    PublishResult,
    emit,
)

# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #

HOME_URL = "https://creator.douyin.com/"
UPLOAD_URL = "https://creator.douyin.com/creator-micro/content/upload"
MANAGE_URL = "https://creator.douyin.com/creator-micro/content/manage"

#: Chromium 启动参数（不含 --disable-dev-shm-usage：Windows 下该参数曾导致重页面渲染崩溃）
LAUNCH_ARGS: tuple[str, ...] = (
    "--disable-blink-features=AutomationControlled",
    "--disable-gpu",
    "--disable-software-rasterizer",
    "--disable-extensions",
    "--disable-background-networking",
    "--disable-background-timer-throttling",
    "--disable-renderer-backgrounding",
    "--disable-features=TranslateUI,BackForwardCache",
    "--mute-audio",
    "--no-first-run",
    "--no-default-browser-check",
)

#: 「高清发布」入口（首页 → 上传页）
HD_PUBLISH_SELECTORS: tuple[str, ...] = (
    'button[class*="douyin-creator-master-button"]',
    "#douyin-creator-master-side-upload-wrap button",
    'button:has-text("高清发布")',
    'div:has-text("高清发布")',
    'text=发布视频',
)

#: 登录判定：出现二维码即未登录
QRCODE_SELECTORS: tuple[str, ...] = (
    'img[aria-label="二维码"]',
    '[class*="qrcode"] img',
    '[class*="qrcode"] canvas',
    'img[class*="qr"]',
)

#: 「扫码登录」tab（抖音默认停在「验证码登录」）
QR_TAB_TEXTS: tuple[str, ...] = ("扫码登录", "二维码登录")

#: 二维码失效后的刷新入口
QR_REFRESH_TEXTS: tuple[str, ...] = ("点击刷新", "刷新二维码", "二维码已失效", "已失效")

#: 发布类型 tab
TAB_VIDEO_SELECTORS: tuple[str, ...] = (
    'div[class*="tab-item"]:has-text("发布视频")',
    '[class*="tab"]:has-text("发布视频")',
    'text=发布视频',
)

#: 隐藏的 file input（视频必须通过它上传）
FILE_INPUT_SELECTORS: tuple[str, ...] = (
    'div[class*="drag-upload"] input[type="file"]',
    'input[type="file"][accept*="video"]',
    'input[type="file"]',
)

#: 上传/转码中的进度条容器（真机上可能是常驻隐藏元素，需判可见性）
UPLOADING_SELECTORS: tuple[str, ...] = (
    '[class*="uploading-container"]',
    '[class*="upload-progress"]',
    '[role="progressbar"]',
)

#: 封面推荐（best-effort，缺失就跳过，不空等）
COVER_TITLE_SELECTORS: tuple[str, ...] = (
    'span[class*="recommendTitle"]',
    '[class*="recommend-title"]',
)
COVER_FIRST_SELECTORS: tuple[str, ...] = (
    'div[class*="recommendCoverContainer"] > div:first-child',
    '[class*="recommendCover"] img',
)
COVER_CONFIRM_SELECTORS: tuple[str, ...] = (
    "div.semi-modal-footer button.semi-button-primary",
    'button:has-text("确定")',
    'button:has-text("完成")',
)

#: 作品标题输入框
TITLE_SELECTORS: tuple[str, ...] = (
    'input[placeholder*="作品标题"]',
    'input[placeholder*="标题"]',
    'textarea[placeholder*="标题"]',
)

#: 作品简介（slate contenteditable）
DESC_SELECTORS: tuple[str, ...] = (
    'div[data-placeholder*="作品简介"][contenteditable="true"]',
    'div.editor-kit-container[contenteditable="true"]',
    'div[contenteditable="true"][data-slate-editor="true"]',
    '[contenteditable="true"]',
)

#: 发布按钮所在容器（在容器内按文本找「发布」，避免误点其它按钮）
PUBLISH_CONTAINER_SELECTORS: tuple[str, ...] = (
    'div[class*="card-container-creator-layout"]',
    'div[class*="content-confirm"]',
)
PUBLISH_BUTTON_TEXTS: tuple[str, ...] = ("发布", "立即发布")

#: 结果 toast
TOAST_SELECTORS: tuple[str, ...] = (
    'span[class*="semi-toast-content-text"]',
    '[class*="toast"] [class*="content"]',
    '[class*="Toast"]',
)

#: 短信/身份验证墙（登录与发布都可能触发）
SMS_WALL_SELECTORS: tuple[str, ...] = (
    'div[class*="uc_verification_component"]',
    "[class*='second_verify_panel']",
    "[class*='second-verify-panel']",
    "[class*='second_verify_mask']",
    "[data-e2e='verification-dialog']",
)
SMS_WALL_KEYWORDS: tuple[str, ...] = (
    "身份验证", "接收短信", "短信验证", "验证方式", "短信已发送",
    "验证码已发送", "获取验证码", "验证并登录",
)
SMS_MODAL_SELECTORS: tuple[str, ...] = SMS_WALL_SELECTORS + (
    "[class*='semi-modal-content']",
    "div[role='dialog']",
    "[class*='modal-content']",
)
SMS_SEND_TEXTS: tuple[str, ...] = ("获取验证码", "发送验证码", "重新发送")
SMS_SUBMIT_TEXTS: tuple[str, ...] = ("验证并登录", "确定", "登录", "验证", "提交")
SMS_ERROR_TEXTS: tuple[str, ...] = (
    "验证码错误", "验证码填写错误", "验证码输入错误", "验证码不正确",
    "验证码已过期", "验证码过期", "请重新获取", "请重新发送",
    "验证失败", "输入错误", "已失效",
)

#: 发布失败/超时时的现场落盘目录（相对登录态目录）
DEBUG_SUBDIR = "debug"

#: 指标抓取：先在整个页面文本里按「标签 + 数字」兜底解析，再用选择器精确定位
METRIC_LABELS: dict[str, tuple[str, ...]] = {
    "views": ("播放量", "播放", "观看量", "观看"),
    "likes": ("点赞量", "点赞", "获赞"),
    "comments": ("评论量", "评论"),
    "collects": ("收藏量", "收藏"),
    "shares": ("分享量", "分享", "转发"),
}
METRIC_VALUE_SELECTORS: dict[str, tuple[str, ...]] = {
    "likes": ('[data-e2e="video-like-count"]', '[data-e2e="like-count"]', '[class*="like-count"]'),
    "comments": ('[data-e2e="comment-count"]', '[class*="comment-count"]'),
    "collects": ('[data-e2e="collect-count"]', '[class*="collect-count"]'),
    "shares": ('[data-e2e="share-count"]', '[class*="share-count"]'),
    "views": ('[data-e2e="video-play-count"]', '[class*="play-count"]'),
}

from ._browser import (  # noqa: F401 — 平台共用工具
    BrowserSession,
    clear_and_type,
    click_soft,
    dump_debug,
    extract_metric_from_text,
    first_visible,
    first_visible_text,
    open_browser,
    parse_cn_number,
    paste_into_focused,
)


# --------------------------------------------------------------------------- #
# 纯工具函数（离线可测，不依赖 playwright）
# --------------------------------------------------------------------------- #

def looks_like_douyin_share_url(url: str) -> bool:
    """判断是否为抖音作品分享链接（www.douyin.com/video/xxx、v.douyin.com 短链）。"""
    if not url:
        return False
    low = url.strip().lower()
    return any(k in low for k in ("douyin.com/video", "douyin.com/note", "v.douyin.com", "iesdouyin.com/share"))


# --------------------------------------------------------------------------- #
# 适配器
# --------------------------------------------------------------------------- #

class DouYinAdapter(PlatformAdapter):
    """抖音适配器（视频作品，creator.douyin.com Web 端）。"""

    key = "douyin"
    label = "抖音"
    home_url = HOME_URL
    publish_url = UPLOAD_URL
    needs_media = True
    supports_auto_publish = True
    metrics_hint = "抖音单篇数据需打开作品分享页；部分账号需登录态才能看到完整数据"

    @property
    def limits(self) -> PlatformLimits:
        return PlatformLimits(
            title_max=30,          # 作品标题
            body_max=2200,         # 作品简介
            body_min=0,
            tags_max=5,
            requires_media=True,
            media_max=1,
            media_kinds=("video",),
            supports_video=True,
            notes="视频必填；话题写进简介的 # 话题（抖音自动联想）",
        )

    # -- 内部小工具 ----------------------------------------------------
    def _log(self, on_progress: Any, message: str, percent: int | None = None) -> None:
        emit(on_progress, message, percent)

    def _debug_dir(self) -> str:
        return str(Path(self.profile_dir()) / DEBUG_SUBDIR)

    def _qr_png(self) -> str:
        return str(Path(self.profile_dir()) / "login_qr.png")

    def _sms_code_file(self) -> Path:
        return Path(self.profile_dir()) / "sms.code"

    def _read_sms_code(self) -> str:
        """一次性读取用户写入的短信验证码（读走即删）。"""
        path = self._sms_code_file()
        try:
            if not path.is_file():
                return ""
            code = path.read_text(encoding="utf-8").strip()
            path.unlink()
            return "".join(ch for ch in code if ch.isdigit())[:8]
        except Exception:
            return ""

    # -- 登录 ----------------------------------------------------------
    def check_login(self, *, headless: bool = True) -> tuple[bool, str]:
        try:
            with open_browser(self, headless=headless) as sess:
                if sess is None:
                    return False, "浏览器不可用：请先安装 playwright 与 chromium（pip install playwright && playwright install chromium）"
                page = sess.page
                try:
                    page.goto(HOME_URL, wait_until="domcontentloaded", timeout=45_000)
                except Exception as exc:
                    return False, f"打开抖音创作者中心失败：{type(exc).__name__}（请检查网络）"
                state = self._probe_login(page, wait_s=12)
                if state is LoginState.LOGGED_IN:
                    return True, "抖音已登录"
                if state is LoginState.LOGGED_OUT:
                    return False, "抖音未登录，请先扫码登录"
                return False, "无法确认抖音登录态（页面可能改版或加载超时）"
        except Exception as exc:  # noqa: BLE001 — 对外只返回中文说明
            return False, f"检测抖音登录态异常：{type(exc).__name__}: {exc}"

    def _probe_login(self, page: Any, *, wait_s: int = 12) -> LoginState:
        """轮询判定登录态：出现二维码=未登录；出现「高清发布」=已登录。"""
        deadline = time.time() + max(1, wait_s)
        while time.time() < deadline:
            try:
                if first_visible(page, QRCODE_SELECTORS) is not None:
                    return LoginState.LOGGED_OUT
                if first_visible(page, HD_PUBLISH_SELECTORS) is not None:
                    return LoginState.LOGGED_IN
            except Exception:
                pass
            try:
                page.wait_for_timeout(600)
            except Exception:
                return LoginState.UNKNOWN
        return LoginState.UNKNOWN

    def login(self, *, timeout_sec: int = 300) -> tuple[bool, str]:
        try:
            with open_browser(self, headless=False) as sess:
                if sess is None:
                    return False, "浏览器不可用：请先安装 playwright 与 chromium"
                page = sess.page
                try:
                    page.goto(HOME_URL, wait_until="domcontentloaded", timeout=45_000)
                except Exception as exc:
                    return False, f"打不开抖音登录页：{type(exc).__name__}（请检查网络/代理）"
                page.wait_for_timeout(1_500)
                if self._probe_login(page, wait_s=6) is LoginState.LOGGED_IN:
                    return True, "抖音已是登录状态，无需重新扫码"
                return self._qr_login_loop(page, timeout_sec=timeout_sec)
        except Exception as exc:  # noqa: BLE001
            return False, f"抖音登录异常：{type(exc).__name__}: {exc}"

    def _qr_login_loop(self, page: Any, *, timeout_sec: int) -> tuple[bool, str]:
        """引导扫码：切「扫码登录」tab → 截二维码 → 轮询登录态 / 短信墙。"""
        # 切到扫码登录 tab（抖音默认可能停在验证码登录）
        try:
            tab = first_visible_text(page, QR_TAB_TEXTS)
            if tab is not None:
                click_soft(tab)
                page.wait_for_timeout(800)
        except Exception:
            pass

        qr_path = self._qr_png()
        last_shot = 0.0
        deadline = time.time() + max(30, timeout_sec)
        sms_deadline: float | None = None
        sms_started = False
        while time.time() < deadline:
            state = self._probe_login(page, wait_s=1)
            if state is LoginState.LOGGED_IN:
                try:
                    Path(qr_path).unlink()
                except OSError:
                    pass
                return True, "抖音登录成功，登录态已保存到本地"
            if self._sms_wall_up(page):
                if sms_deadline is None:
                    sms_deadline = time.time() + 240
                if not sms_started:
                    sms_started = True
                    self._sms_click_send(page)
                code = self._read_sms_code()
                if code:
                    self._sms_submit_code(page, code)
                    page.wait_for_timeout(2_000)
                    continue
                if time.time() > sms_deadline:
                    return False, (
                        "抖音要求短信验证，但未收到验证码。请把收到的验证码写入文件："
                        f"{self._sms_code_file()}（脚本会自动读取并提交）"
                    )
            # 每 ~10s 重截二维码：抖音码约 1-2 分钟过期，只截一次会扫到失效码
            if time.time() - last_shot > 10:
                qr = self._find_qr(page)
                if qr is not None:
                    self._shot_qr(page, qr, qr_path)
                else:
                    self._refresh_qr(page)
                    qr = self._find_qr(page)
                    if qr is not None:
                        self._shot_qr(page, qr, qr_path)
                last_shot = time.time()
        return False, (
            f"{timeout_sec}s 内未完成抖音扫码登录。请在浏览器窗口用抖音 App 扫码"
            f"（二维码截图：{qr_path}）"
        )

    def _find_qr(self, page: Any) -> Any:
        """定位二维码元素：①方形 data:image ②「扫码登录」标签下方的方形 svg/canvas/div。"""
        try:
            for img in page.query_selector_all("img"):
                try:
                    src = img.get_attribute("src") or ""
                    if not src.startswith("data:image"):
                        continue
                    box = img.bounding_box()
                    if box and 100 <= box["width"] <= 320 and abs(box["width"] - box["height"]) < 40:
                        return img
                except Exception:
                    continue
        except Exception:
            pass
        try:
            label = None
            for text in QR_TAB_TEXTS:
                label = page.query_selector(f"text={text}")
                if label:
                    break
            lb = None
            try:
                lb = label.bounding_box() if label else None
            except Exception:
                lb = None
            best = None
            for el in page.query_selector_all("div, svg, canvas"):
                try:
                    box = el.bounding_box()
                    if not box:
                        continue
                    w, h = box["width"], box["height"]
                    if not (110 <= w <= 340 and abs(w - h) < 40):
                        continue
                    if lb is not None and box["y"] < lb["y"]:
                        continue
                    if not el.is_visible():
                        continue
                    if best is None or box["y"] < best[1]:
                        best = (el, box["y"])
                except Exception:
                    continue
            return best[0] if best else None
        except Exception:
            return None

    def _shot_qr(self, page: Any, qr: Any, out_path: str) -> None:
        """截二维码：canvas 直接元素截图常为空白，优先页面级截图 + clip。"""
        try:
            box = qr.bounding_box()
        except Exception:
            box = None
        if box and box.get("width", 0) > 20:
            pad = 6
            try:
                page.screenshot(path=out_path, clip={
                    "x": max(0, box["x"] - pad),
                    "y": max(0, box["y"] - pad),
                    "width": box["width"] + pad * 2,
                    "height": box["height"] + pad * 2,
                })
                return
            except Exception:
                pass
        try:
            qr.screenshot(path=out_path)
        except Exception:
            pass

    def _refresh_qr(self, page: Any) -> bool:
        for text in QR_REFRESH_TEXTS:
            try:
                el = page.query_selector(f"text={text}")
                if el is not None and el.is_visible():
                    click_soft(el)
                    page.wait_for_timeout(1_800)
                    return True
            except Exception:
                continue
        return False

    # -- 短信验证墙（登录与发布共用） ------------------------------------
    def _sms_scope(self, page: Any) -> Any:
        """定位验证弹窗本体（排除背景登录表单），找不到时返回 page。"""
        best = None
        for sel in SMS_MODAL_SELECTORS:
            try:
                for el in page.query_selector_all(sel):
                    try:
                        if not el.is_visible():
                            continue
                        txt = el.inner_text() or ""
                        if "验证" not in txt and "短信" not in txt:
                            continue
                        if best is None or len(txt) < best[0]:
                            best = (len(txt), el)
                    except Exception:
                        continue
            except Exception:
                continue
        return best[1] if best else None

    def _sms_wall_up(self, page: Any) -> bool:
        """判定是否真的出现风控短信墙（只认弹窗内关键词，不认背景『验证码登录』表单）。"""
        scope = self._sms_scope(page)
        if scope is None:
            return False
        try:
            txt = (scope.inner_text() or "").replace("\n", " ")
        except Exception:
            return False
        return any(kw in txt for kw in SMS_WALL_KEYWORDS)

    def _sms_click_text(self, page: Any, texts: tuple[str, ...], word: str) -> bool:
        scope = self._sms_scope(page) or page
        for text in texts:
            try:
                targets = scope.query_selector_all(f"button:has-text('{text}')")
                for el in targets:
                    try:
                        if el.is_visible():
                            click_soft(el)
                            return True
                    except Exception:
                        continue
            except Exception:
                pass
            try:
                el = scope.query_selector(f"text={text}")
                if el is not None and el.is_visible() and click_soft(el):
                    return True
            except Exception:
                continue
        try:
            scope.evaluate(
                """(kw) => {
                    const nodes = Array.from(document.querySelectorAll('button,a,span,div'));
                    const hit = nodes.find(n => (n.innerText || '').trim().includes(kw)
                        && n.offsetParent !== null);
                    if (hit) hit.click();
                }""",
                word,
            )
            return True
        except Exception:
            return False

    def _sms_click_send(self, page: Any) -> bool:
        return self._sms_click_text(page, SMS_SEND_TEXTS, "获取验证码")

    def _sms_submit_code(self, page: Any, code: str) -> bool:
        scope = self._sms_scope(page) or page
        filled = False
        for sel in ('input[placeholder*="验证码"]', 'input[maxlength="6"]', 'input[type="tel"]', "input"):
            try:
                for el in scope.query_selector_all(sel):
                    try:
                        if not el.is_visible():
                            continue
                        el.click()
                        el.fill("")
                        el.type(code, delay=30)
                        filled = True
                        break
                    except Exception:
                        continue
                if filled:
                    break
            except Exception:
                continue
        if not filled:
            return False
        return self._sms_click_text(page, SMS_SUBMIT_TEXTS, "确定")

    def _sms_error_text(self, page: Any) -> str:
        scope = self._sms_scope(page)
        if scope is None:
            return ""
        try:
            txt = scope.inner_text() or ""
        except Exception:
            return ""
        for kw in SMS_ERROR_TEXTS:
            if kw in txt:
                return kw
        return ""

    # -- 发布 ----------------------------------------------------------
    def publish(
        self,
        payload: PublishPayload,
        *,
        headless: bool = False,
        dry_run: bool = False,
        timeout_sec: int = 300,
        on_progress: Any = None,
    ) -> PublishResult:
        # 1) 入参校验（发布前检查，超限直接给中文提示）
        title = (payload.title or "").strip()
        body = (payload.body or "").strip()
        tags = [str(t).strip() for t in (payload.tags or []) if str(t).strip()]
        lim = self.limits
        if not title:
            return PublishResult(ok=False, message="抖音作品标题不能为空")
        if len(title) > lim.title_max:
            return PublishResult(ok=False, message=f"标题过长（{len(title)}/{lim.title_max} 字），请精简后再发布")
        if len(body) > lim.body_max:
            return PublishResult(ok=False, message=f"作品简介过长（{len(body)}/{lim.body_max} 字），请精简后再发布")
        if len(tags) > lim.tags_max:
            return PublishResult(ok=False, message=f"话题过多（{len(tags)}/{lim.tags_max}），请删减后再发布")
        media = [p for p in (payload.media or []) if p]
        if not media:
            return PublishResult(ok=False, message="抖音发布视频必填，请在预览里选择视频文件")
        video = Path(media[0]).expanduser()
        if not video.is_file():
            return PublishResult(ok=False, message=f"视频文件不存在：{video}")

        try:
            with open_browser(self, headless=headless) as sess:
                if sess is None:
                    return PublishResult(ok=False, message="浏览器不可用：请先安装 playwright 与 chromium")
                try:
                    return self._publish_flow(
                        sess.page, title=title, body=body, tags=tags, video=str(video),
                        dry_run=dry_run, timeout_sec=timeout_sec, on_progress=on_progress,
                    )
                except Exception as exc:  # noqa: BLE001
                    where = dump_debug(sess.page, self._debug_dir(), "douyin", "publish-error")
                    note = f"，现场已落盘：{where}" if where else ""
                    return PublishResult(
                        ok=False,
                        message=f"抖音发布失败：{type(exc).__name__}: {exc}{note}",
                    )
        except Exception as exc:  # noqa: BLE001
            return PublishResult(ok=False, message=f"抖音发布异常：{type(exc).__name__}: {exc}")

    def _publish_flow(
        self,
        page: Any,
        *,
        title: str,
        body: str,
        tags: list[str],
        video: str,
        dry_run: bool,
        timeout_sec: int,
        on_progress: Any,
    ) -> PublishResult:
        budget = max(60, timeout_sec)
        deadline = time.time() + budget

        self._log(on_progress, "打开抖音创作者中心…", 5)
        try:
            page.goto(HOME_URL, wait_until="domcontentloaded", timeout=45_000)
        except Exception as exc:
            return PublishResult(ok=False, message=f"打开抖音创作者中心失败：{type(exc).__name__}（请检查网络）")
        page.wait_for_timeout(1_500)

        state = self._probe_login(page, wait_s=10)
        if state is not LoginState.LOGGED_IN:
            return PublishResult(ok=False, message="抖音未登录，请先在「账号」页扫码登录后再发布")

        self._log(on_progress, "进入视频上传页…", 12)
        if "content/upload" not in (page.url or ""):
            if not self._go_upload(page):
                where = dump_debug(page, self._debug_dir(), "douyin", "no-upload-page")
                return PublishResult(
                    ok=False,
                    message="未找到「高清发布」入口，无法进入上传页（抖音可能改版）" + (f"，现场：{where}" if where else ""),
                )

        self._log(on_progress, "选择「发布视频」…", 18)
        tab = first_visible(page, TAB_VIDEO_SELECTORS)
        if tab is not None:
            click_soft(tab)
            page.wait_for_timeout(600)

        self._log(on_progress, "上传视频文件…", 25)
        if not self._upload_video(page, video):
            where = dump_debug(page, self._debug_dir(), "douyin", "no-file-input")
            return PublishResult(
                ok=False,
                message="未找到视频上传入口（file input），无法上传" + (f"，现场：{where}" if where else ""),
            )

        self._log(on_progress, "等待视频上传与转码…", 40)
        if not self._wait_video_ready(page, deadline=deadline):
            where = dump_debug(page, self._debug_dir(), "douyin", "upload-timeout")
            return PublishResult(
                ok=False,
                message="视频上传/转码超时或编辑器未就绪" + (f"，现场：{where}" if where else ""),
            )

        self._log(on_progress, "选择推荐封面（可选）…", 55)
        self._pick_cover(page)

        self._log(on_progress, "填写标题与简介…", 65)
        if not self._fill_title(page, title):
            where = dump_debug(page, self._debug_dir(), "douyin", "no-title-input")
            return PublishResult(
                ok=False,
                message="未找到作品标题输入框（抖音可能改版）" + (f"，现场：{where}" if where else ""),
            )
        desc = body
        if tags:
            desc = (desc + " " + " ".join("#" + str(t).lstrip("#") for t in tags)).strip()
        if desc:
            self._fill_desc(page, desc)
        page.wait_for_timeout(400)

        if dry_run:
            self._log(on_progress, "已填写完成，等待人工确认（未点击发布）", 90)
            return PublishResult(
                ok=True,
                url=page.url or "",
                message="已填写完成，等待人工确认",
                detail={"dry_run": True, "video": video, "tags": tags,
                        "hint": "确认标题/简介/封面无误后，在浏览器窗口内手动点击「发布」"},
            )

        self._log(on_progress, "等待「发布」按钮就绪…", 75)
        button = self._wait_publish_button(page, deadline=deadline)
        if button is None:
            where = dump_debug(page, self._debug_dir(), "douyin", "publish-button-not-ready")
            return PublishResult(
                ok=False,
                message="「发布」按钮迟迟未就绪（可能仍在转码或表单校验未通过），已按未发布处理"
                        + (f"，现场：{where}" if where else ""),
            )

        self._log(on_progress, "提交发布…", 85)
        if not click_soft(button):
            return PublishResult(ok=False, message="点击「发布」按钮失败，请改为人工发布")

        self._log(on_progress, "等待平台回执…", 92)
        ok, message, url = self._wait_publish_result(page, timeout_s=60)
        if ok:
            self._log(on_progress, "发布完成", 100)
            return PublishResult(ok=True, url=url, message=message)
        where = dump_debug(page, self._debug_dir(), "douyin", "publish-unconfirmed")
        return PublishResult(
            ok=False,
            url=url,
            message=message + (f"，现场已落盘：{where}" if where else ""),
            detail={"unconfirmed": True},
        )

    def _go_upload(self, page: Any) -> bool:
        """首页点「高清发布」→ 等 URL 进入 content/upload。"""
        if "content/upload" in (page.url or ""):
            return True
        entry = first_visible(page, HD_PUBLISH_SELECTORS)
        if entry is None:
            # 兜底：直接打开上传页
            try:
                page.goto(UPLOAD_URL, wait_until="domcontentloaded", timeout=45_000)
            except Exception:
                return False
            page.wait_for_timeout(1_500)
            return "content/upload" in (page.url or "")
        if not click_soft(entry):
            return False
        deadline = time.time() + 20
        while time.time() < deadline:
            if "content/upload" in (page.url or ""):
                page.wait_for_timeout(800)
                return True
            page.wait_for_timeout(500)
        return False

    def _upload_video(self, page: Any, video: str) -> bool:
        """通过隐藏 file input 塞文件（比拦截 filechooser 稳）。"""
        for sel in FILE_INPUT_SELECTORS:
            try:
                inputs = page.query_selector_all(sel)
            except Exception:
                inputs = []
            for el in inputs:
                try:
                    el.set_input_files(video)
                    page.wait_for_timeout(1_000)
                    return True
                except Exception:
                    continue
        # 兜底：有些版本必须点击上传按钮触发 filechooser
        try:
            with page.expect_file_chooser(timeout=8_000) as fc:
                trigger = first_visible(page, ('div[class*="drag-upload"]', 'text=点击上传', 'text=上传视频'))
                if trigger is None:
                    return False
                click_soft(trigger)
            fc.value.set_files(video)
            page.wait_for_timeout(1_000)
            return True
        except Exception:
            return False

    def _wait_video_ready(self, page: Any, *, deadline: float) -> bool:
        """等上传/转码完成：标题框可见 + 上传进度条不可见，连续两次通过才算稳定。"""
        stable = 0
        while time.time() < deadline:
            ok = False
            try:
                title_el = first_visible(page, TITLE_SELECTORS)
                if title_el is not None:
                    uploading = None
                    for sel in UPLOADING_SELECTORS:
                        try:
                            for el in page.query_selector_all(sel):
                                if el.is_visible():
                                    uploading = el
                                    break
                        except Exception:
                            continue
                        if uploading is not None:
                            break
                    if uploading is None:
                        ok = True
                    else:
                        try:
                            val = uploading.get_attribute("aria-valuenow")
                            if val is not None and float(val) < 100:
                                ok = False
                        except Exception:
                            pass
            except Exception:
                ok = False
            stable = stable + 1 if ok else 0
            if stable >= 2:
                page.wait_for_timeout(1_200)
                return True
            page.wait_for_timeout(2_000)
        return False

    def _pick_cover(self, page: Any) -> None:
        """best-effort 选推荐封面：抖音默认取首帧，选不了就跳过（不空等）。"""
        deadline = time.time() + 15
        while time.time() < deadline:
            el = first_visible(page, COVER_TITLE_SELECTORS)
            if el is None:
                return
            try:
                if "生成中" not in (el.inner_text() or ""):
                    break
            except Exception:
                break
            page.wait_for_timeout(1_000)
        cover = first_visible(page, COVER_FIRST_SELECTORS)
        if cover is None:
            return
        click_soft(cover)
        page.wait_for_timeout(400)
        confirm = first_visible(page, COVER_CONFIRM_SELECTORS)
        if confirm is not None:
            click_soft(confirm)
            page.wait_for_timeout(400)

    def _fill_title(self, page: Any, title: str) -> bool:
        el = first_visible(page, TITLE_SELECTORS)
        if el is None:
            return False
        if clear_and_type(page, el, title, delay_ms=12):
            return True
        try:
            el.fill(title)
            return True
        except Exception:
            return False

    def _fill_desc(self, page: Any, desc: str) -> bool:
        el = first_visible(page, DESC_SELECTORS)
        if el is None:
            return False
        # 剪贴板注入优先（长文更快），失败退键盘逐字
        try:
            el.click()
            page.wait_for_timeout(200)
            page.keyboard.press("Control+a")
            page.keyboard.press("Delete")
            ok = paste_into_focused(page, desc)
            page.wait_for_timeout(600)
            if ok:
                try:
                    if len((el.inner_text() or "").strip()) >= min(10, len(desc)):
                        return True
                except Exception:
                    return True
        except Exception:
            pass
        return clear_and_type(page, el, desc, delay_ms=8)

    def _find_publish_button(self, page: Any) -> Any:
        containers = []
        for sel in PUBLISH_CONTAINER_SELECTORS:
            try:
                containers.extend(page.query_selector_all(sel))
            except Exception:
                continue
        scopes = containers + [page]
        for scope in scopes:
            try:
                for btn in scope.query_selector_all("button"):
                    try:
                        text = (btn.inner_text() or "").strip()
                        if text in PUBLISH_BUTTON_TEXTS and btn.is_visible():
                            return btn
                    except Exception:
                        continue
            except Exception:
                continue
        return None

    def _wait_publish_button(self, page: Any, *, deadline: float) -> Any:
        stable = 0
        while time.time() < deadline:
            btn = self._find_publish_button(page)
            ok = False
            if btn is not None:
                try:
                    ok = (btn.get_attribute("disabled") is None
                          and btn.get_attribute("aria-disabled") not in ("true", "1"))
                except Exception:
                    ok = False
            stable = stable + 1 if ok else 0
            if stable >= 2:
                return btn
            page.wait_for_timeout(1_500)
        return None

    def _wait_publish_result(self, page: Any, *, timeout_s: int = 60) -> tuple[bool, str, str]:
        """判发布结果：①跳内容管理页 ②toast 含「成功」= 成功；
        ③toast 含失败类词 ④出现短信墙 → 返回未确认（让用户人工核对，绝不误报成功）。"""
        start_url = page.url or ""
        deadline = time.time() + timeout_s
        sms_since: float | None = None
        while time.time() < deadline:
            try:
                url = page.url or ""
                if "content/manage" in url and url != start_url:
                    return True, "抖音发布成功（已跳转内容管理页）", url
                for sel in TOAST_SELECTORS:
                    try:
                        el = page.query_selector(sel)
                    except Exception:
                        el = None
                    if el is None:
                        continue
                    text = (el.inner_text() or "").strip()
                    if not text:
                        continue
                    if "成功" in text:
                        return True, f"抖音发布成功（{text}）", url
                    if any(k in text for k in ("失败", "错误", "不能", "不支持", "请先", "请选择", "请上传")):
                        return False, f"抖音发布失败：{text}", url
            except Exception:
                pass
            if self._sms_wall_up(page):
                if sms_since is None:
                    sms_since = time.time()
                    self._sms_click_send(page)
                code = self._read_sms_code()
                if code:
                    self._sms_submit_code(page, code)
                    page.wait_for_timeout(1_500)
                    continue
                if time.time() - sms_since > 120:
                    return False, (
                        "抖音发布触发短信验证，已下发验证码但未收到。请把验证码写入文件 "
                        f"{self._sms_code_file()} 后重试发布"
                    ), page.url or ""
            page.wait_for_timeout(1_000)
        return False, "发布结果未确认（未跳转、无成功提示），请到抖音创作者中心内容管理页人工核对是否已发出", page.url or ""

    # -- 数据 ----------------------------------------------------------
    def fetch_metrics(self, post_url: str, *, headless: bool = True) -> MetricSnapshot | None:
        url = (post_url or "").strip()
        if not url:
            return None
        try:
            with open_browser(self, headless=headless) as sess:
                if sess is None:
                    return None
                page = sess.page
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=45_000)
                except Exception:
                    return None
                page.wait_for_timeout(3_000)
                snapshot = self._scrape_metrics(page, url)
                if snapshot is None:
                    dump_debug(page, self._debug_dir(), "douyin", "metrics-fail")
                return snapshot
        except Exception:
            return None

    def _scrape_metrics(self, page: Any, url: str) -> MetricSnapshot | None:
        """抓取作品分享页上的可见指标；一条都没抓到则返回 None。"""
        data: dict[str, int] = {}
        for key, selectors in METRIC_VALUE_SELECTORS.items():
            for sel in selectors:
                try:
                    el = page.query_selector(sel)
                except Exception:
                    el = None
                if el is None:
                    continue
                try:
                    text = el.inner_text() or el.get_attribute("title") or ""
                except Exception:
                    text = ""
                val = parse_cn_number(text)
                if val:
                    data[key] = val
                    break
        # 文本兜底（分享页多数指标以「点赞 1.2万」形式排版）
        try:
            page_text = page.inner_text("body") or ""
        except Exception:
            page_text = ""
        for key, labels in METRIC_LABELS.items():
            if data.get(key):
                continue
            val = extract_metric_from_text(page_text, labels)
            if val:
                data[key] = val
        if not any(data.values()):
            return None
        raw: dict[str, Any] = {"url": url, "source": "douyin-share-page", "text_hint": page_text[:400]}
        for key, selectors in METRIC_VALUE_SELECTORS.items():
            if data.get(key):
                raw[f"{key}_selector"] = selectors[0]
        return MetricSnapshot(
            views=data.get("views", 0),
            likes=data.get("likes", 0),
            comments=data.get("comments", 0),
            collects=data.get("collects", 0),
            shares=data.get("shares", 0),
            raw=raw,
        )
