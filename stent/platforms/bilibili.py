"""B 站（哔哩哔哩）平台适配器（member.bilibili.com Web 投稿页，Playwright 同步 API）。

复用与改写取舍：
- Easel 的 ``skill-bilibili-upload/scripts/bili_upload.py`` 包装的是**外部 biliup CLI**
  （Rust 后端 + ``cookies.json`` + ``biliup login``，见该文件 docstring 的「⚠️ 环境依赖」），
  移植进 Stent 需要用户额外安装 biliup 与配套 cookie 文件，违背「Stent 自带、零外部工具」的定位。
  因此这里**不引入 biliup**，改为用 Playwright 走 ``member.bilibili.com/platform/upload/video/frame``
  Web 投稿页（同一套登录态 ``profile_dir()`` 持久化），并保留其参数语义：
  标题 ≤ 80 字、简介 ≤ 2000 字、标签 ≤ 10、分区名 → tid 映射、封面、转载来源。
- 分区名 → tid 映射沿用 ``bili_upload.py`` 的 ``PARTITIONS`` 表（见下方 PARTITIONS）。
- 登录改用 Web 扫码（不再走 TV 端 QR API + segno），以便与其它平台统一为 Playwright 持久化登录态。

风控约束同其它平台：单次人工触发发布，``dry_run=True`` 时只填表单、绝不点击「立即投稿」。
"""

from __future__ import annotations

import json
import re
import time
import urllib.request
from pathlib import Path
from typing import Any

from ._browser import (
    clear_and_type,
    click_soft,
    click_text_in,
    dump_debug,
    extract_metric_from_text,
    first_visible,
    first_visible_text,
    open_browser,
    parse_cn_number,
    paste_into_focused,
)
from .base import (
    AccountPost,
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

HOME_URL = "https://member.bilibili.com/platform/home"
UPLOAD_URL = "https://member.bilibili.com/platform/upload/video/frame"
LOGIN_URL = "https://passport.bilibili.com/login"

#: 公开的稿件信息 API：无需登录，``stat.reply`` 就是评论数。
#: 比爬播放页 DOM 更稳——工具栏上本来就没有「评论」这个数字，所以之前一直抓不到。
VIEW_API = "https://api.bilibili.com/x/web-interface/view"
#: 创作中心「稿件管理」列表接口（需要登录态，在已登录的页面里 fetch 即可）
ACCOUNT_API = "https://member.bilibili.com/x/web/archives"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

_BV_RE = re.compile(r"(BV[0-9A-Za-z]{10})")
_AV_RE = re.compile(r"av(\d+)", re.IGNORECASE)

#: 分区名 → tid（沿用 Easel bili_upload.py 的 PARTITIONS；B 站改版后有的仍可用）
PARTITIONS: dict[str, int] = {
    "动画": 1, "音乐": 3, "游戏": 4, "娱乐": 5, "生活": 160, "日常": 21,
    "科技": 188, "数码": 95, "知识": 36, "科普": 201, "资讯": 202,
    "影视": 181, "美食": 211, "时尚": 155, "美妆": 157, "穿搭": 158,
    "运动": 234, "汽车": 223, "动物": 217, "舞蹈": 129, "鬼畜": 119,
    "绘画": 162, "手工": 161, "摄影": 282, "职场": 253, "校园": 55,
    "母婴": 216, "家居": 239, "旅游": 250, "三农": 251,
}
DEFAULT_PARTITION = "知识"

#: 视频扩展名（发布前粗校验；不做转码）
VIDEO_SUFFIXES: tuple[str, ...] = (
    ".mp4", ".mov", ".mkv", ".flv", ".avi", ".wmv", ".webm", ".m4v", ".ts", ".rmvb",
)

#: 登录判定（创作中心页头头像/昵称）
LOGGED_IN_SELECTORS: tuple[str, ...] = (
    ".user-info .name",
    ".bili-avatar",
    '[class*="user-info"] [class*="name"]',
    '#app [class*="avatar"]',
)
LOGGED_OUT_SELECTORS: tuple[str, ...] = (
    'text=登录',
    'button:has-text("登录")',
    ".login-btn",
    '[class*="login-btn"]',
    ".bili-login-card",
)
LOGGED_OUT_URL_KEYWORDS: tuple[str, ...] = ("passport.bilibili.com/login", "passport.bilibili.com/register")

#: 登录二维码
QR_SELECTORS: tuple[str, ...] = (
    ".login-scan-box img",
    ".qrcode-img",
    'img[alt*="二维码"]',
    "[class*='qrcode'] img",
    "[class*='qr-code'] img",
    "[class*='login-scan'] canvas",
    "[class*='login-scan'] img",
)
QR_TAB_TEXTS: tuple[str, ...] = ("扫码登录", "二维码登录")
QR_REFRESH_TEXTS: tuple[str, ...] = ("点击刷新", "刷新二维码", "二维码已失效", "已失效")

#: 投稿页 file input
FILE_INPUT_SELECTORS: tuple[str, ...] = (
    'input[type="file"][accept*="video"]',
    'input[type="file"][accept*="mp4"]',
    '.bcc-upload-wrapper input[type="file"]',
    '[class*="upload"] input[type="file"]',
    'input[type="file"]',
)

#: 上传/转码中的信号
UPLOADING_TEXT_KEYWORDS: tuple[str, ...] = ("上传中", "上传完成", "转码中", "视频处理中", "解析中", "处理中")
UPLOAD_READY_KEYWORDS: tuple[str, ...] = ("上传完成", "转码完成", "稿件封面", "封面")
UPLOAD_AREA_SELECTORS: tuple[str, ...] = (
    ".bcc-upload-wrapper",
    ".upload-area",
    '[class*="upload-wrp"]',
    '[class*="upload-content"]',
)

#: 表单字段
TITLE_SELECTORS: tuple[str, ...] = (
    'input[placeholder*="标题"]',
    ".video-title-container input",
    'input[maxlength="80"]',
    '[class*="title"] input[type="text"]',
)
DESC_SELECTORS: tuple[str, ...] = (
    '[class*="desc"] textarea',
    'textarea[placeholder*="简介"]',
    'textarea[placeholder*="简介，"]',
    ".b-input.textarea textarea",
    'textarea[maxlength="2000"]',
)
TAG_SELECTORS: tuple[str, ...] = (
    '[class*="tag"] input',
    'input[placeholder*="标签"]',
    'input[placeholder*="按回车键Enter创建标签"]',
    '[class*="tag-container"] input',
)
TAG_DELETE_SELECTORS: tuple[str, ...] = (
    '[class*="tag"] [class*="close"]',
    '[class*="tag-item"] svg',
    '[class*="tag"] .bcc-icon-close',
)
PARTITION_TRIGGER_SELECTORS: tuple[str, ...] = (
    '[class*="select-container"]',
    ".bcc-select .bcc-select-view",
    '[class*="partition"] [class*="select"]',
    'div:has-text("请选择分区")',
)
PARTITION_OPTION_SELECTORS: tuple[str, ...] = (
    ".bcc-select-dropdown .bcc-option",
    ".bcc-option",
    "[class*='option']",
)
PARTITION_SEARCH_SELECTORS: tuple[str, ...] = (
    '[class*="select"] input',
    ".bcc-select-dropdown input",
)
COVER_INPUT_SELECTORS: tuple[str, ...] = (
    'input[type="file"][accept*="image"]',
    '[class*="cover"] input[type="file"]',
)
COVER_APPLY_TEXTS: tuple[str, ...] = ("完成", "确定", "确认")
COVER_APPLY_SELECTORS: tuple[str, ...] = (
    ".bcc-dialog .bcc-button--primary",
    "[class*='dialog'] [class*='primary']",
    'button:has-text("完成")',
)

#: 提交按钮
SUBMIT_BUTTON_SELECTORS: tuple[str, ...] = (
    ".submit-add",
    "[class*='submit-add']",
    'button:has-text("立即投稿")',
    'span:has-text("立即投稿")',
    'div:has-text("立即投稿")',
)
SUBMIT_TEXTS: tuple[str, ...] = ("立即投稿", "投稿", "发布")

#: 结果信号
TOAST_SELECTORS: tuple[str, ...] = (
    ".bcc-message",
    "[class*='message'] [class*='content']",
    "[class*='toast']",
)
FAIL_TEXTS: tuple[str, ...] = (
    "投稿失败", "提交失败", "上传失败", "失败", "错误", "请先", "请选择", "请上传", "不能为空", "不符合",
)

#: 指标抓取（旧版播放页 / 新版播放页两套 DOM 都覆盖）
METRIC_LABELS: dict[str, tuple[str, ...]] = {
    "views": ("播放", "观看"),
    "likes": ("点赞",),
    "comments": ("评论",),
    "collects": ("收藏",),
    "shares": ("分享", "转发"),
}
METRIC_VALUE_SELECTORS: dict[str, tuple[str, ...]] = {
    "views": (
        ".video-info-detail .view",
        ".video-data .view",
        '[class*="view-text"]',
        '[class*="view"] [class*="num"]',
        ".number[title]",
    ),
    "likes": (".video-like-info", ".like .info-text", '[class*="like"] [class*="info-text"]', '[class*="like"] .num'),
    "comments": (".video-comment-info", ".comment .info-text", '[class*="comment"] [class*="info-text"]'),
    "collects": (".video-fav-info", ".collect .info-text", '[class*="fav"] [class*="info-text"]'),
    "shares": (".video-share-info", ".share .info-text", '[class*="share"] [class*="info-text"]'),
}
#: 播放页里互动数字初始常为「--」（未登录/未渲染），需要点击或等待后才出来
METRIC_HOVER_TARGETS: tuple[str, ...] = (
    ".video-toolbar-left-item",
    '[class*="toolbar"] [class*="item"]',
)

DEBUG_DIR_NAME = "debug"


# --------------------------------------------------------------------------- #
# 纯工具函数
# --------------------------------------------------------------------------- #

def resolve_tid(partition: str | None) -> int | None:
    """分区名/数字 → tid；无法识别返回 None（调用方退化为人工选择）。"""
    if not partition:
        return PARTITIONS.get(DEFAULT_PARTITION)
    text = str(partition).strip()
    if not text:
        return PARTITIONS.get(DEFAULT_PARTITION)
    if text.isdigit():
        return int(text)
    return PARTITIONS.get(text)


def is_video_file(path: str) -> bool:
    """粗校验是否为视频文件（按扩展名）。"""
    try:
        return Path(path).suffix.lower() in VIDEO_SUFFIXES
    except Exception:
        return False


def normalize_video_url(url: str) -> str:
    """``BV1xx411c7mD`` → 完整播放页 URL；已是 URL 则原样返回。"""
    s = (url or "").strip()
    if not s:
        return ""
    if s.lower().startswith("http"):
        return s
    return f"https://www.bilibili.com/video/{s}"


def video_ids(url: str) -> tuple[str, str]:
    """从播放页链接 / BV 号 / av 号里取出 ``(bvid, aid)``，取不到的部分为空串。"""
    text = (url or "").strip()
    if not text:
        return "", ""
    bv = _BV_RE.search(text)
    if bv:
        return bv.group(1), ""
    av = _AV_RE.search(text)
    if av:
        return "", av.group(1)
    # 纯数字也当作 aid（用户可能只粘了 av 号里的数字）
    if text.isdigit():
        return "", text
    return "", ""


def _http_json(url: str, *, timeout: float = 12.0) -> Any:
    """极简 JSON GET（B 站公开接口需要 UA 与 Referer，否则可能被拒）。"""
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Referer": "https://www.bilibili.com/",
            "Accept": "application/json, text/plain, */*",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - 固定可信数据源
        return json.loads(response.read().decode("utf-8", "replace"))


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


# --------------------------------------------------------------------------- #
# 适配器
# --------------------------------------------------------------------------- #

class BiliBiliAdapter(PlatformAdapter):
    """B 站适配器（视频投稿，member.bilibili.com Web 端）。"""

    key = "bilibili"
    label = "B 站"
    home_url = HOME_URL
    publish_url = UPLOAD_URL
    needs_media = True
    supports_auto_publish = True
    #: 走创作中心接口，列表自带全部指标，是最可靠的一条导入路径
    supports_account_import = True
    metrics_hint = "B 站单篇数据取自公开接口（播放/点赞/评论/收藏/分享）"

    @property
    def limits(self) -> PlatformLimits:
        return PlatformLimits(
            title_max=80,
            body_max=2000,
            body_min=0,
            tags_max=10,
            requires_media=True,
            media_max=1,
            media_kinds=("video",),
            supports_video=True,
            notes="视频必填；分区用 extra.partition（如「知识」）或 extra.tid 指定，默认知识区",
        )

    # -- 内部小工具 ----------------------------------------------------
    def _log(self, on_progress: Any, message: str, percent: int | None = None) -> None:
        emit(on_progress, message, percent)

    def _debug_dir(self) -> str:
        return str(Path(self.profile_dir()) / DEBUG_DIR_NAME)

    def _qr_png(self) -> str:
        return str(Path(self.profile_dir()) / "login_qr.png")

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
                    return False, f"打开 B 站创作中心失败：{type(exc).__name__}（请检查网络）"
                state = self._probe_login(page, wait_s=12)
                if state is LoginState.LOGGED_IN:
                    return True, "B 站已登录"
                if state is LoginState.LOGGED_OUT:
                    return False, "B 站未登录，请先扫码登录"
                return False, "无法确认 B 站登录态（页面可能改版或加载超时）"
        except Exception as exc:  # noqa: BLE001
            return False, f"检测 B 站登录态异常：{type(exc).__name__}: {exc}"

    def _probe_login(self, page: Any, *, wait_s: int = 12) -> LoginState:
        deadline = time.time() + max(1, wait_s)
        while time.time() < deadline:
            try:
                url = (page.url or "").lower()
                if any(k in url for k in LOGGED_OUT_URL_KEYWORDS):
                    return LoginState.LOGGED_OUT
                if first_visible(page, QR_SELECTORS) is not None and "passport" in url:
                    return LoginState.LOGGED_OUT
                if first_visible(page, LOGGED_IN_SELECTORS) is not None:
                    return LoginState.LOGGED_IN
                if first_visible(page, LOGGED_OUT_SELECTORS) is not None:
                    return LoginState.LOGGED_OUT
            except Exception:
                pass
            try:
                page.wait_for_timeout(600)
            except Exception:
                return LoginState.UNKNOWN
        return LoginState.UNKNOWN

    def login(self, *, timeout_sec: int = 300) -> tuple[bool, str]:
        """打开有头浏览器引导扫码；二维码同时截图落盘便于 UI 展示。"""
        try:
            with open_browser(self, headless=False) as sess:
                if sess is None:
                    return False, "浏览器不可用：请先安装 playwright 与 chromium"
                page = sess.page
                try:
                    page.goto(HOME_URL, wait_until="domcontentloaded", timeout=45_000)
                except Exception as exc:
                    return False, f"打不开 B 站登录页：{type(exc).__name__}（请检查网络/代理）"
                page.wait_for_timeout(1_500)
                if self._probe_login(page, wait_s=5) is LoginState.LOGGED_IN:
                    return True, "B 站已是登录状态，无需重新扫码"
                # 点「登录」进入扫码页（创作中心首页是嵌入登录卡片）
                entry = first_visible_text(page, ("登录", "立即登录"))
                if entry is not None:
                    click_soft(entry)
                    page.wait_for_timeout(2_000)
                tab = first_visible_text(page, QR_TAB_TEXTS)
                if tab is not None:
                    click_soft(tab)
                    page.wait_for_timeout(800)
                self._try_shot_qr(page)

                deadline = time.time() + max(30, timeout_sec)
                shot_at = time.time()
                while time.time() < deadline:
                    state = self._probe_login(page, wait_s=1)
                    if state is LoginState.LOGGED_IN:
                        try:
                            Path(self._qr_png()).unlink()
                        except OSError:
                            pass
                        return True, "B 站登录成功，登录态已保存到本地"
                    if time.time() - shot_at > 15:
                        qr = first_visible(page, QR_SELECTORS)
                        if qr is None:
                            for text in QR_REFRESH_TEXTS:
                                el = first_visible_text(page, (text,))
                                if el is not None:
                                    click_soft(el)
                                    page.wait_for_timeout(1_500)
                                    break
                        self._try_shot_qr(page)
                        shot_at = time.time()
                return False, (
                    f"{timeout_sec}s 内未完成 B 站扫码登录。请用 B 站 App 扫码"
                    f"（二维码截图：{self._qr_png()}）"
                )
        except Exception as exc:  # noqa: BLE001
            return False, f"B 站登录异常：{type(exc).__name__}: {exc}"

    def _try_shot_qr(self, page: Any) -> None:
        el = first_visible(page, QR_SELECTORS)
        if el is None:
            return
        out = self._qr_png()
        try:
            box = el.bounding_box()
        except Exception:
            box = None
        if box and box.get("width", 0) > 20:
            pad = 6
            try:
                page.screenshot(path=out, clip={
                    "x": max(0, box["x"] - pad),
                    "y": max(0, box["y"] - pad),
                    "width": box["width"] + pad * 2,
                    "height": box["height"] + pad * 2,
                })
                return
            except Exception:
                pass
        try:
            el.screenshot(path=out)
        except Exception:
            pass

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
        extra = payload.extra or {}
        title = (payload.title or "").strip()
        body = (payload.body or "").strip()
        tags = [str(t).strip().lstrip("#") for t in (payload.tags or []) if str(t).strip()]
        lim = self.limits

        if not title:
            return PublishResult(ok=False, message="B 站稿件标题不能为空")
        if len(title) > lim.title_max:
            return PublishResult(ok=False, message=f"标题过长（{len(title)}/{lim.title_max} 字），请精简后再投稿")
        if len(body) > lim.body_max:
            return PublishResult(ok=False, message=f"简介过长（{len(body)}/{lim.body_max} 字），请精简后再投稿")
        if len(tags) > lim.tags_max:
            return PublishResult(ok=False, message=f"标签过多（{len(tags)}/{lim.tags_max}），请删减后再投稿")
        media = [p for p in (payload.media or []) if p]
        if not media:
            return PublishResult(ok=False, message="B 站投稿必填视频，请在预览里选择视频文件")
        video = Path(media[0]).expanduser()
        if not video.is_file():
            return PublishResult(ok=False, message=f"视频文件不存在：{video}")
        if not is_video_file(str(video)):
            return PublishResult(ok=False, message=f"不是支持的视频格式（{video.suffix}）：请选择 mp4/mov/mkv 等视频文件")

        partition = str(extra.get("partition") or DEFAULT_PARTITION).strip()
        tid = _safe_int(extra.get("tid")) or resolve_tid(partition)
        cover_raw = str(extra.get("cover") or "").strip()
        cover = str(Path(cover_raw).expanduser()) if cover_raw else ""
        if cover and not Path(cover).is_file():
            cover = ""

        try:
            with open_browser(self, headless=headless) as sess:
                if sess is None:
                    return PublishResult(ok=False, message="浏览器不可用：请先安装 playwright 与 chromium")
                try:
                    return self._publish_flow(
                        sess.page, title=title, body=body, tags=tags, video=str(video),
                        partition=partition, tid=tid, cover=cover,
                        dry_run=dry_run, timeout_sec=timeout_sec, on_progress=on_progress,
                    )
                except Exception as exc:  # noqa: BLE001
                    where = dump_debug(sess.page, self._debug_dir(), "bilibili", "publish-error")
                    note = f"，现场已落盘：{where}" if where else ""
                    return PublishResult(ok=False, message=f"B 站投稿失败：{type(exc).__name__}: {exc}{note}")
        except Exception as exc:  # noqa: BLE001
            return PublishResult(ok=False, message=f"B 站投稿异常：{type(exc).__name__}: {exc}")

    def _publish_flow(
        self, page: Any, *, title: str, body: str, tags: list[str], video: str,
        partition: str, tid: int, cover: str,
        dry_run: bool, timeout_sec: int, on_progress: Any,
    ) -> PublishResult:
        budget = max(120, timeout_sec)
        deadline = time.time() + budget

        self._log(on_progress, "打开 B 站创作中心…", 5)
        try:
            page.goto(HOME_URL, wait_until="domcontentloaded", timeout=45_000)
        except Exception as exc:
            return PublishResult(ok=False, message=f"打开 B 站创作中心失败：{type(exc).__name__}（请检查网络）")
        page.wait_for_timeout(1_500)
        if self._probe_login(page, wait_s=8) is LoginState.LOGGED_OUT:
            return PublishResult(ok=False, message="B 站未登录，请先在「账号」页扫码登录后再投稿")

        self._log(on_progress, "进入视频投稿页…", 12)
        try:
            page.goto(UPLOAD_URL, wait_until="domcontentloaded", timeout=45_000)
        except Exception as exc:
            return PublishResult(ok=False, message=f"打开发布页失败：{type(exc).__name__}")
        page.wait_for_timeout(2_500)
        if "passport.bilibili.com" in (page.url or ""):
            return PublishResult(ok=False, message="B 站登录态已失效（被重定向到登录页），请重新扫码登录")

        self._log(on_progress, "上传视频文件…", 20)
        if not self._upload_video(page, video):
            where = dump_debug(page, self._debug_dir(), "bilibili", "no-file-input")
            return PublishResult(ok=False, message="未找到视频上传入口（file input）"
                                                + (f"，现场：{where}" if where else ""))

        self._log(on_progress, "等待视频上传与转码…", 35)
        if not self._wait_upload_done(page, deadline=deadline):
            where = dump_debug(page, self._debug_dir(), "bilibili", "upload-timeout")
            return PublishResult(ok=False, message="视频上传/转码超时，未进入表单填写阶段"
                                                + (f"，现场：{where}" if where else ""))

        self._log(on_progress, "填写标题…", 50)
        if not self._set_field(page, TITLE_SELECTORS, title):
            where = dump_debug(page, self._debug_dir(), "bilibili", "no-title")
            return PublishResult(ok=False, message="未找到稿件标题输入框（B 站可能改版）"
                                                + (f"，现场：{where}" if where else ""))

        self._log(on_progress, "填写简介…", 58)
        self._set_field(page, DESC_SELECTORS, body, multiline=True, required=False)

        self._log(on_progress, "填写标签…", 66)
        self._fill_tags(page, tags)

        self._log(on_progress, f"选择分区（{partition}）…", 74)
        if not self._select_partition(page, partition, tid):
            self._log(on_progress, "分区自动选择失败，请在提交前人工确认分区", 74)

        if cover:
            self._log(on_progress, "上传封面…", 80)
            self._upload_cover(page, cover)

        if dry_run:
            self._log(on_progress, "已填写完成，等待人工确认（未点击投稿）", 90)
            return PublishResult(
                ok=True,
                url=page.url or UPLOAD_URL,
                message="已填写完成，等待人工确认",
                detail={"dry_run": True, "video": video, "tags": tags,
                        "partition": partition, "tid": tid,
                        "hint": "确认标题/封面/分区无误后，在浏览器窗口内手动点击「立即投稿」"},
            )

        self._log(on_progress, "等待「立即投稿」按钮就绪…", 85)
        button = self._wait_submit_button(page, deadline=deadline)
        if button is None:
            where = dump_debug(page, self._debug_dir(), "bilibili", "submit-not-ready")
            return PublishResult(ok=False, message="「立即投稿」按钮迟迟未就绪（可能仍在转码或校验未通过），已按未投稿处理"
                                                + (f"，现场：{where}" if where else ""))

        self._log(on_progress, "提交投稿…", 92)
        if not click_soft(button):
            return PublishResult(ok=False, message="点击「立即投稿」失败，请改为人工投稿")

        self._log(on_progress, "等待平台回执…", 95)
        ok, message, url = self._wait_submit_result(page, timeout_s=60)
        if ok:
            self._log(on_progress, "投稿完成", 100)
            return PublishResult(ok=True, url=url, message=message)
        where = dump_debug(page, self._debug_dir(), "bilibili", "submit-unconfirmed")
        return PublishResult(ok=False, url=url,
                             message=message + (f"，现场已落盘：{where}" if where else ""),
                             detail={"unconfirmed": True})

    def _upload_video(self, page: Any, video: str) -> bool:
        """通过 file input 塞视频；找不到 input 时退化为点击上传区触发 filechooser。"""
        for sel in FILE_INPUT_SELECTORS:
            try:
                inputs = page.query_selector_all(sel)
            except Exception:
                inputs = []
            for el in inputs:
                try:
                    el.set_input_files(video)
                    page.wait_for_timeout(1_500)
                    return True
                except Exception:
                    continue
        try:
            with page.expect_file_chooser(timeout=8_000) as fc:
                trigger = first_visible(page, UPLOAD_AREA_SELECTORS + ('text=上传视频', 'text=点击上传'))
                if trigger is None:
                    return False
                click_soft(trigger)
            fc.value.set_files(video)
            page.wait_for_timeout(1_500)
            return True
        except Exception:
            return False

    def _wait_upload_done(self, page: Any, *, deadline: float) -> bool:
        """等转码完成：标题框出现 + 上传区不再显示「上传中/转码中」，连续两次通过才放行。"""
        stable = 0
        while time.time() < deadline:
            ok = False
            try:
                title_el = first_visible(page, TITLE_SELECTORS)
                if title_el is not None:
                    text = self._page_text(page, 4000)
                    busy = any(kw in text for kw in UPLOADING_TEXT_KEYWORDS if kw.endswith("中"))
                    ok = not busy
            except Exception:
                ok = False
            stable = stable + 1 if ok else 0
            if stable >= 2:
                page.wait_for_timeout(1_500)
                return True
            page.wait_for_timeout(2_500)
        return False

    def _page_text(self, page: Any, limit: int = 4000) -> str:
        try:
            return (page.inner_text("body") or "")[:limit]
        except Exception:
            return ""

    def _set_field(self, page: Any, selectors: tuple[str, ...], value: str,
                   *, multiline: bool = False, required: bool = True) -> bool:
        if not value:
            return not required
        el = first_visible(page, selectors)
        if el is None:
            return not required
        if multiline:
            try:
                el.click()
                page.wait_for_timeout(200)
                page.keyboard.press("Control+a")
                page.keyboard.press("Delete")
                if paste_into_focused(page, value):
                    page.wait_for_timeout(500)
                    return True
            except Exception:
                pass
            return clear_and_type(page, el, value, delay_ms=6)
        if clear_and_type(page, el, value, delay_ms=10):
            return True
        try:
            el.fill(value)
            return True
        except Exception:
            return not required

    def _fill_tags(self, page: Any, tags: list[str]) -> bool:
        """先清掉默认标签，再逐个输入 + 回车创建（B 站标签必须回车确认）。"""
        if not tags:
            return True
        for sel in TAG_DELETE_SELECTORS:
            try:
                for el in page.query_selector_all(sel):
                    try:
                        if el.is_visible():
                            click_soft(el)
                            page.wait_for_timeout(200)
                    except Exception:
                        continue
                break
            except Exception:
                continue
        filled = False
        for tag in tags:
            el = first_visible(page, TAG_SELECTORS)
            if el is None:
                return filled
            try:
                el.click()
                el.fill(tag)
                page.wait_for_timeout(900)
                page.keyboard.press("Enter")
                page.wait_for_timeout(500)
                filled = True
            except Exception:
                continue
        return filled

    def _select_partition(self, page: Any, partition: str, tid: int) -> bool:
        """选择投稿分区：搜索框输入分区名 → 点命中项；失败返回 False（交由人工确认）。"""
        trigger = first_visible(page, PARTITION_TRIGGER_SELECTORS)
        if trigger is None:
            return False
        click_soft(trigger)
        page.wait_for_timeout(800)
        search = first_visible(page, PARTITION_SEARCH_SELECTORS)
        if search is not None:
            try:
                search.click()
                search.fill(partition)
                page.wait_for_timeout(1_200)
            except Exception:
                pass
        # 优先点文本完全匹配的选项
        for sel in PARTITION_OPTION_SELECTORS:
            try:
                for el in page.query_selector_all(sel):
                    try:
                        text = (el.inner_text() or "").strip()
                        if text == partition or (text and partition in text):
                            if click_soft(el):
                                page.wait_for_timeout(800)
                                return True
                    except Exception:
                        continue
            except Exception:
                continue
        try:
            if click_text_in(page, page, partition):
                page.wait_for_timeout(800)
                return True
        except Exception:
            pass
        return False

    def _upload_cover(self, page: Any, cover: str) -> bool:
        for sel in COVER_INPUT_SELECTORS:
            try:
                inputs = page.query_selector_all(sel)
            except Exception:
                inputs = []
            for el in inputs:
                try:
                    el.set_input_files(cover)
                    page.wait_for_timeout(2_000)
                except Exception:
                    continue
                for confirm_sel in COVER_APPLY_SELECTORS:
                    confirm = first_visible(page, (confirm_sel,))
                    if confirm is not None:
                        click_soft(confirm)
                        page.wait_for_timeout(1_000)
                        return True
                for text in COVER_APPLY_TEXTS:
                    if click_text_in(page, page, text):
                        page.wait_for_timeout(1_000)
                        return True
                return True
        return False

    def _wait_submit_button(self, page: Any, *, deadline: float) -> Any:
        stable = 0
        while time.time() < deadline:
            btn = first_visible(page, SUBMIT_BUTTON_SELECTORS) or first_visible_text(page, SUBMIT_TEXTS)
            ok = False
            if btn is not None:
                try:
                    ok = (btn.get_attribute("disabled") is None
                          and btn.get_attribute("aria-disabled") not in ("true", "1")
                          and "disabled" not in ((btn.get_attribute("class") or "").lower()))
                except Exception:
                    ok = False
            stable = stable + 1 if ok else 0
            if stable >= 2:
                return btn
            page.wait_for_timeout(1_500)
        return None

    def _wait_submit_result(self, page: Any, *, timeout_s: int = 60) -> tuple[bool, str, str]:
        """判投稿结果：离开投稿页（去稿件管理/成功页）或出现成功提示 = 成功；
        出现失败文案 = 失败；超时 = 未确认（绝不误报成功）。"""
        start_url = page.url or ""
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            try:
                url = page.url or ""
                if url != start_url and ("upload-manager" in url or "archive-list" in url
                                         or "upload/text" in url or "success" in url):
                    return True, f"B 站投稿已提交（已跳转：{url[:70]}）", url
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
                    if "成功" in text or "已提交" in text or "审核" in text:
                        return True, f"B 站投稿已提交（{text}）", url
                    if any(k in text for k in FAIL_TEXTS):
                        return False, f"B 站投稿失败：{text}", url
            except Exception:
                pass
            page.wait_for_timeout(1_000)
        return False, "投稿结果未确认（未跳转、无成功提示），请到 B 站创作中心「稿件管理」人工核对是否已提交", page.url or ""

    # -- 数据 ----------------------------------------------------------
    def fetch_metrics(self, post_url: str, *, headless: bool = True) -> MetricSnapshot | None:
        """优先走公开接口，失败再退回爬播放页。

        接口这条路不需要浏览器，秒回，而且**评论数（stat.reply）拿得到**——
        播放页工具栏上压根没有「评论」这个数字，所以旧实现里 comments 一直是 0。
        """
        url = normalize_video_url(post_url)
        if not url:
            return None
        snapshot = self.fetch_metrics_api(url)
        if snapshot is not None:
            return snapshot
        return self._fetch_metrics_page(url, headless=headless)

    def fetch_metrics_api(self, post_url: str) -> MetricSnapshot | None:
        """通过 ``x/web-interface/view`` 取指标；失败返回 None（不抛异常）。"""
        bvid, aid = video_ids(post_url)
        if not bvid and not aid:
            return None
        query = f"bvid={bvid}" if bvid else f"aid={aid}"
        try:
            payload = _http_json(f"{VIEW_API}?{query}")
        except Exception:
            return None
        if not isinstance(payload, dict) or payload.get("code") != 0:
            return None
        data = payload.get("data") or {}
        stat = data.get("stat") or {}
        if not stat:
            return None
        return MetricSnapshot(
            views=_safe_int(stat.get("view")),
            likes=_safe_int(stat.get("like")),
            # reply 就是评论数——这是旧实现一直缺失的那一项
            comments=_safe_int(stat.get("reply")),
            collects=_safe_int(stat.get("favorite")),
            shares=_safe_int(stat.get("share")),
            # 顺带带回标题：粘贴链接登记时标题只能靠这一步补上
            title=str(data.get("title") or ""),
            raw={
                "url": post_url,
                "source": "bilibili-view-api",
                "bvid": data.get("bvid") or bvid,
                "aid": data.get("aid") or aid,
                "title": data.get("title") or "",
                "danmaku": _safe_int(stat.get("danmaku")),
                "coin": _safe_int(stat.get("coin")),
                "pubdate": data.get("pubdate"),
            },
        )

    def list_account_posts(
        self, *, headless: bool = True, limit: int = 50
    ) -> list[AccountPost]:
        """从创作中心「稿件管理」拉取账号下的全部稿件（需要已登录）。

        在已登录的页面里直接 ``fetch`` 创作中心接口，登录态由浏览器自动带上，
        因此不用自己处理 Cookie 与签名。列表里就带播放/评论等数据，
        拉下来即可直接入库，不必再逐条打开播放页。
        """
        out: list[AccountPost] = []
        seen: set[str] = set()
        page_size = 30
        try:
            with open_browser(self, headless=headless) as sess:
                if sess is None:
                    return []
                page = sess.page
                try:
                    page.goto(HOME_URL, wait_until="domcontentloaded", timeout=45_000)
                except Exception:
                    return []
                page.wait_for_timeout(2_000)

                for page_no in range(1, 11):  # 最多翻 10 页，兜住异常分页
                    if len(out) >= limit:
                        break
                    url = (
                        f"{ACCOUNT_API}?status=pubed&pn={page_no}&ps={page_size}"
                        "&coop=1&interactive=1"
                    )
                    try:
                        payload = page.evaluate(
                            """async (target) => {
                                try {
                                    const resp = await fetch(target, {credentials: 'include'});
                                    return await resp.json();
                                } catch (err) {
                                    return {code: -1, message: String(err)};
                                }
                            }""",
                            url,
                        )
                    except Exception:
                        break
                    if not isinstance(payload, dict) or payload.get("code") != 0:
                        break
                    data = payload.get("data") or {}
                    rows = data.get("arc_audits") or data.get("list") or []
                    if not rows:
                        break
                    for row in rows:
                        if len(out) >= limit:
                            break
                        item = self._account_row_to_post(row)
                        if item is None or item.url in seen:
                            continue
                        seen.add(item.url)
                        out.append(item)
                    total = _safe_int((data.get("page") or {}).get("count"))
                    if total and page_no * page_size >= total:
                        break
        except Exception:
            return out
        return out

    @staticmethod
    def _account_row_to_post(row: Any) -> AccountPost | None:
        """把创作中心返回的一行转成 :class:`AccountPost`（字段名兼容两种写法）。"""
        if not isinstance(row, dict):
            return None
        archive = row.get("Archive") if isinstance(row.get("Archive"), dict) else row
        bvid = str(archive.get("bvid") or "")
        aid = archive.get("aid")
        if bvid:
            link = f"https://www.bilibili.com/video/{bvid}"
        elif aid:
            link = f"https://www.bilibili.com/video/av{aid}"
        else:
            return None
        stat = archive.get("stat") or {}
        ptime = archive.get("ptime") or archive.get("pubtime") or archive.get("ctime")
        published_at = ""
        if isinstance(ptime, (int, float)) and ptime > 0:
            try:
                published_at = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ptime))
            except Exception:
                published_at = ""
        return AccountPost(
            url=link,
            title=str(archive.get("title") or ""),
            published_at=published_at,
            platform_id=bvid or (f"av{aid}" if aid else ""),
            metrics={
                "views": _safe_int(stat.get("view")),
                "likes": _safe_int(stat.get("like")),
                "comments": _safe_int(stat.get("reply")),
                # 创作中心接口里收藏叫 fav，公开接口里叫 favorite
                "collects": _safe_int(stat.get("fav") or stat.get("favorite")),
                "shares": _safe_int(stat.get("share")),
                "raw": {
                    "source": "bilibili-creator-api",
                    "bvid": bvid,
                    "aid": aid,
                    "danmaku": _safe_int(stat.get("danmaku")),
                    "coin": _safe_int(stat.get("coin")),
                },
            },
        )

    def _fetch_metrics_page(self, url: str, *, headless: bool) -> MetricSnapshot | None:
        """兜底：爬播放页 DOM（接口不可用时才走这里）。"""
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
                # 互动数字初始常是「--」，hover 一下工具条促使前端渲染
                for sel in METRIC_HOVER_TARGETS:
                    try:
                        for el in page.query_selector_all(sel):
                            try:
                                el.hover(timeout=2_000)
                            except Exception:
                                continue
                            break
                    except Exception:
                        continue
                page.wait_for_timeout(1_500)
                snapshot = self._scrape_metrics(page, url)
                if snapshot is None:
                    dump_debug(page, self._debug_dir(), "bilibili", "metrics-fail")
                return snapshot
        except Exception:
            return None

    def _scrape_metrics(self, page: Any, url: str) -> MetricSnapshot | None:
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
                    text = (el.inner_text() or "") + " " + (el.get_attribute("title") or "")
                except Exception:
                    text = ""
                val = parse_cn_number(text)
                if val:
                    data[key] = val
                    break
        page_text = self._page_text(page, 6000)
        for key, labels in METRIC_LABELS.items():
            if data.get(key):
                continue
            val = extract_metric_from_text(page_text, labels)
            if val:
                data[key] = val
        if not any(data.values()):
            return None
        return MetricSnapshot(
            views=data.get("views", 0),
            likes=data.get("likes", 0),
            comments=data.get("comments", 0),
            collects=data.get("collects", 0),
            shares=data.get("shares", 0),
            raw={"url": url, "source": "bilibili-video-page", "text_hint": page_text[:400]},
        )
