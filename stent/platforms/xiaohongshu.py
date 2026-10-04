"""小红书平台适配器（creator.xiaohongshu.com，Playwright 同步 API）。

移植来源：Easel ``skills/shared/scripts/xhs_publish.py``（选择器与流程见
``_reference/specs/xiaohongshu.md``）。已剥离 OpenClaw / anthropic / content_guard /
calendar_ops / login_state / human_pace 等依赖，改为自包含实现；登录态不再走
``~/.easel-browser-profiles``，改用 ``PlatformAdapter.profile_dir()``
（即 ``_data/browser/xiaohongshu``）。

Easel 上游选择器本身又移植自开源实现 `xpzouying/xiaohongshu-mcp
<https://github.com/xpzouying/xiaohongshu-mcp>`_（Go/go-rod）；本次为核对来源行号，
已把对应 Go 源文件抓到 ``_reference/upstream_xhs_mcp/``，选择器常量逐条标注了
「Easel 行号 + upstream 文件:行号」。

设计要点：
- 只做**单次、人工触发**的发布；没有批量或全自动发布开关（企划书第七章风控约束）。
- ``dry_run=True`` 时把表单填完即返回，**绝不点击最终「发布」按钮**，并保持浏览器窗口
  打开一段时间供人工复核。
- 所有选择器都是模块级多候选元组，逐个尝试以抗平台改版；改版时只改本文件顶部常量。
- 所有浏览器动作都带超时与 try/except，失败返回中文说明的 ``PublishResult`` / ``None``，
  绝不向上抛异常。
"""

from __future__ import annotations

import re as _re
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

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
# 站点常量
# --------------------------------------------------------------------------- #

#: 网页版首页（登录二维码弹窗在此触发；也是登录态探测页）——Easel xhs_publish.py:46
EXPLORE_URL = "https://www.xiaohongshu.com/explore"
#: 创作者中心发布页——Easel xhs_publish.py:45
PUBLISH_URL = "https://creator.xiaohongshu.com/publish/publish?source=official"
#: 创作者中心登录页（首页被风控拦截时的备选入口）——Easel xhs_publish.py:47
CREATOR_LOGIN_URL = "https://creator.xiaohongshu.com/login"
#: 创作者中心笔记管理页（发布成功后用于提取作品链接）
CREATOR_NOTES_URL = "https://creator.xiaohongshu.com/new/note-manager"
#: 笔记链接匹配（/explore/<id> 与 /discovery/item/<id> 两种形态）
NOTE_URL_RE = _re.compile(r"xiaohongshu\.com/(?:explore|discovery/item)/([0-9a-zA-Z]+)")

#: 标题上限（小红书口径：全角字计 1，见 calc_title_length）
TITLE_MAX = 20

#: Chromium 启动参数。移除了 Easel 的 ``--no-sandbox`` / ``--disable-dev-shm-usage``
#: （Linux 容器专用，Windows 桌面下反而加剧不稳定），保留反检测与资源相关参数。
LAUNCH_ARGS: tuple[str, ...] = (
    "--disable-blink-features=AutomationControlled",   # 反检测——Easel xhs_publish.py:83
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

#: 失败现场落盘目录（相对登录态目录）
DEBUG_SUBDIR = "debug"

# --------------------------------------------------------------------------- #
# 选择器集中维护（小红书改版时单点更新）
#
# 来源标注格式：[E:行号] = Easel skills/shared/scripts/xhs_publish.py；
#              [U:文件:行号] = upstream xiaohongshu-mcp（Go）。
# 新增的候选都标了 [新增]，未经真机实测，仅作抗改版兜底。
# --------------------------------------------------------------------------- #

#: 登录成功的页面标志：网页版右上角用户区（[E:52][U:login.go:27/81/123]）
LOGIN_OK_SELECTORS: tuple[str, ...] = (
    ".main-container .user .link-wrapper .channel",
    ".main-container .user a[href^='/user/profile/']",   # [新增]
    ".main-container .user img.reds-img",                 # [新增]
)

#: 登录二维码（[E:53][U:login.go:102]）
QRCODE_SELECTORS: tuple[str, ...] = (
    ".login-container .qrcode-img",
    ".qrcode-img",                                        # [新增]
    "img[src^='data:image'][class*='qrcode']",            # [新增]
    "[class*='qrcode'] canvas",                           # [新增]
)

#: 二维码失效后的刷新入口（[新增]，Easel 未处理过期码）
QR_REFRESH_TEXTS: tuple[str, ...] = (
    "点击刷新", "刷新二维码", "二维码已失效", "已失效", "重新获取",
)

#: 创作平台登录页的登录方式 tab（[新增]，兜底引导）
LOGIN_TABS_BY_TEXT: tuple[str, ...] = ("扫码登录", "二维码登录")

#: 发布页容器就绪标志（[E:55][U:publish.go:151]）
UPLOAD_CONTENT_SELECTORS: tuple[str, ...] = ("div.upload-content",)

#: 「上传图文 / 上传视频」tab（[E:56][U:publish.go:194]）
CREATOR_TAB_SELECTORS: tuple[str, ...] = ("div.creator-tab",)

#: tab 被浮层遮挡时要移除的浮层（[E:57][U:publish.go:106/129]）
POP_COVER_SELECTORS: tuple[str, ...] = ("div.d-popover",)

#: 首张媒体文件输入框（[E:58][U:publish.go:279]）
UPLOAD_INPUT_FIRST_SELECTORS: tuple[str, ...] = (
    ".upload-input",
    "input[type=file]",
)

#: 追加媒体文件输入框（[E:59][U:publish.go:282]）
UPLOAD_INPUT_MORE_SELECTORS: tuple[str, ...] = (
    "input[type=file]",
    ".upload-input",
)

#: 图文已上传预览（用于判定每张图上传完成）（[E:60][U:publish.go:326]）
IMG_PREVIEW_SELECTORS: tuple[str, ...] = (
    ".img-preview-area .pr",
    "[class*='img-preview'] [class*='pr']",               # [新增]
)

#: 视频上传中的进度容器（[新增]；Easel 以「发布按钮可点击」间接判定处理完成）
VIDEO_UPLOADING_SELECTORS: tuple[str, ...] = (
    "[class*='upload-progress']",
    "[role='progressbar']",
    "[class*='progress-bar']",
)

#: 标题输入框（[E:63][U:publish.go:349 / publish_video.go:110]）
TITLE_INPUT_SELECTORS: tuple[str, ...] = (
    "div.d-input input",
    "input[placeholder*='标题']",
    "input[placeholder*='填写标题']",                      # [新增]
    "textarea[placeholder*='标题']",                       # [新增]
)

#: 正文编辑器（Quill 旧版 → tiptap/contenteditable 新版）（[E:64][U:publish.go:672-674]）
CONTENT_EDITOR_SELECTORS: tuple[str, ...] = (
    "div.ql-editor",
    "div[role='textbox'][contenteditable='true']",
    "div.tiptap[contenteditable='true']",
    "div.editor-container [contenteditable='true']",
    "[contenteditable='true']",
)

#: 正文占位符（旧版用它反查可编辑祖先）（[E:66][U:publish.go getContentElement]）
CONTENT_PLACEHOLDER_SELECTORS: tuple[str, ...] = (
    "p[data-placeholder*='输入正文描述']",
    "[data-placeholder*='输入正文描述']",                  # [新增]
)

#: 话题联想容器与联想项（[E:71-72][U:publish.go:762-768]）
TOPIC_CONTAINER_SELECTORS: tuple[str, ...] = ("#creator-editor-topic-container",)
TOPIC_ITEM_SELECTORS: tuple[str, ...] = (
    "#creator-editor-topic-container .item",
    "#creator-editor-topic-container li",                  # [新增]
    "[class*='topic-container'] [class*='item']",          # [新增]
)

#: 发布按钮：新版 ``<xhs-publish-btn>`` web component（[E:73][U:publish.go:487]）
PUBLISH_BTN_NEW_SELECTORS: tuple[str, ...] = ("xhs-publish-btn",)
#: 发布按钮：旧版 ``.publish-page-publish-btn button.bg-red``（[E:74][U:publish.go:516]）
PUBLISH_BTN_OLD_SELECTORS: tuple[str, ...] = (
    ".publish-page-publish-btn button.bg-red",
    ".publish-page-publish-btn button",                    # [新增]
)
#: 按文案兜底的发布按钮（[新增]，仅在两版结构都失效时尝试）
PUBLISH_BTN_TEXTS: tuple[str, ...] = ("发布笔记", "发布")

#: 二次确认弹窗的确认按钮文案（[E:463-468]）
CONFIRM_PUBLISH_TEXTS: tuple[str, ...] = (
    "确认发布", "确定发布", "继续发布", "立即发布", "确认", "确定",
)

#: 标题 / 正文超限提示（[E:67-68][U:publish.go:624/642]）
TITLE_OVERFLOW_SELECTORS: tuple[str, ...] = ("div.title-container div.max_suffix",)
CONTENT_OVERFLOW_SELECTORS: tuple[str, ...] = (
    "div.edit-container div.length-error",
    "[class*='length-error']",                             # [新增]
)

#: 失败现场诊断：可见弹框 / toast / 报错（[E:432-433]）
DIAG_TEXT_SELECTORS: tuple[str, ...] = (
    ".d-modal", "[role=dialog]", "[class*=modal]", "[class*=dialog]",
    ".d-message", ".d-toast", "[class*=toast]", "[class*=error]", "[class*=tip]",
)

#: 发布成功提示（小红书成功后通常原地清空表单，故还需配合 URL / 表单复位判定）（[E:480]）
SUCCESS_TOAST_SELECTORS: tuple[str, ...] = (
    ".d-message", ".d-toast", "[class*=toast]", "[class*=message]",
)

#: 笔记管理页里的笔记链接（用于发布后提取作品链接）
NOTE_LINK_SELECTORS: tuple[str, ...] = (
    "a[href*='/explore/']",
    "a[href*='discovery/item']",
)

#: 指标抓取：DOM 精确选择器（[新增]，Easel 未实现单篇指标抓取；
#: 依据 upstream 的 interactInfo 语义：likedCount / collectedCount / commentCount / sharedCount）
METRIC_VALUE_SELECTORS: dict[str, tuple[str, ...]] = {
    "likes": (
        ".interact-container .like-wrapper .count",
        ".engage-bar .like-wrapper .count",
        "[class*='like-wrapper'] [class*='count']",
        "[data-v-a9660e72][class*='count']",               # [新增] 小红书 Vue 组件 scoped 属性
    ),
    "collects": (
        ".interact-container .collect-wrapper .count",
        ".engage-bar .collect-wrapper .count",
        "[class*='collect-wrapper'] [class*='count']",
    ),
    "comments": (
        ".interact-container .chat-wrapper .count",
        ".engage-bar .chat-wrapper .count",
        "[class*='chat-wrapper'] [class*='count']",
        "[class*='comment-wrapper'] [class*='count']",
    ),
    "shares": (
        ".interact-container .share-wrapper .count",
        ".engage-bar .share-wrapper .count",
        "[class*='share-wrapper'] [class*='count']",
    ),
    # 浏览量：网页版笔记页通常**不展示**，仅在部分账号/版本可见，取不到即为 0
    "views": (
        ".interact-container .view-wrapper .count",
        "[class*='view-wrapper'] [class*='count']",
        "[class*='view-count']",
        "[class*='read-count']",
    ),
}

#: 指标抓取：整页文本兜底（标签 + 数字）
METRIC_LABELS: dict[str, tuple[str, ...]] = {
    "likes": ("点赞", "获赞", "赞"),
    "collects": ("收藏",),
    "comments": ("评论",),
    "shares": ("分享", "转发"),
    "views": ("浏览", "观看", "阅读"),
}

#: 出站内容安全护栏用的关键词（仅用于提示，不作为拦截依据——Stent 侧由发布中心校验）
RISK_URL_MARKERS: tuple[str, ...] = ("website-login/error", "error_code=300012")
RISK_TITLE_MARKERS: tuple[str, ...] = ("安全限制", "IP存在风险")
RISK_BODY_MARKERS: tuple[str, ...] = ("IP存在风险", "请切换可靠网络环境", "安全限制")


# --------------------------------------------------------------------------- #
# 纯函数（离线可测，不依赖 playwright）
# --------------------------------------------------------------------------- #

def calc_title_length(text: str) -> int:
    """小红书标题长度口径（移植 Easel ``xhs_publish.py:95`` ``calc_title_length``）。

    算法来源 upstream ``pkg/xhsutil/title.go`` ``CalcTitleLength``：UTF-16 码元
    非 ASCII 计 2、ASCII 计 1，再 ``(n+1)//2`` 上取整。等价于「全角字计 1，
    ASCII 两字计 1」，20 个全角字 = 20（上限）。
    """
    if not text:
        return 0
    utf16 = text.encode("utf-16-le")
    byte_len = 0
    for i in range(0, len(utf16), 2):
        code = utf16[i] | (utf16[i + 1] << 8)
        byte_len += 2 if code > 127 else 1
    return (byte_len + 1) // 2


def parse_cn_number(text: Any) -> int:
    """把中文计数的展示值解析成整数。

    支持 ``"1.2万"`` → 12000、``"3.4亿"`` → 340000000、``"1,234"`` → 1234、
    ``"1.5w"`` → 15000、``"2.3k"`` → 2300；无法解析（``"-"``、``"暂无"``、空）
    时返回 0，绝不抛异常。口径与 Easel ``account_stats.parse_num``（:101）一致。
    """
    if text is None or isinstance(text, bool):
        return 0
    if isinstance(text, (int, float)):
        try:
            return int(text)
        except (TypeError, ValueError, OverflowError):
            return 0
    s = str(text).strip().replace(",", "").replace("，", "").replace(" ", "")
    if not s:
        return 0
    multiplier = 1.0
    for suffix, factor in (("亿", 1e8), ("万", 1e4), ("w", 1e4), ("W", 1e4), ("k", 1e3), ("K", 1e3)):
        if s.endswith(suffix):
            multiplier = factor
            s = s[: -len(suffix)]
            break
    num = ""
    for ch in s:
        if ch.isdigit() or (ch == "." and num and "." not in num):
            num += ch
        elif num:
            break
    if not num:
        return 0
    try:
        return int(float(num) * multiplier)
    except (TypeError, ValueError, OverflowError):
        return 0


_NUM_TOKEN_RE = _re.compile(r"(\d[\d,]*(?:\.\d+)?\s*[万亿wWkK]?)")


def extract_metric_from_text(text: str, labels: tuple[str, ...]) -> int:
    """在页面文本里按「标签 + 数字」抽取指标；找不到返回 0。

    兼容「点赞 1.2万」「1.2万 点赞」「点赞：1234」等排版。属启发式：若标签后紧跟的
    数字其实是别的含义（如「点赞 3 小时前」），会取到 3——调用方应优先用选择器精确取值。
    """
    if not text:
        return 0
    flat = " ".join(str(text).split())
    for label in labels:
        idx = flat.find(label)
        while idx >= 0:
            tail = flat[idx + len(label): idx + len(label) + 24].lstrip(" ：:·|/")
            match = _NUM_TOKEN_RE.match(tail)
            if match:
                val = parse_cn_number(match.group(1))
                if val:
                    return val
            head = flat[max(0, idx - 24): idx].strip()
            if head:
                matches = _NUM_TOKEN_RE.findall(head)
                if matches:
                    val = parse_cn_number(matches[-1])
                    if val:
                        return val
            idx = flat.find(label, idx + len(label))
    return 0


def looks_like_note_url(url: str) -> bool:
    """判断是否为小红书笔记链接（``/explore/<id>`` 或 ``/discovery/item/<id>``）。"""
    if not url:
        return False
    return bool(NOTE_URL_RE.search(str(url).strip()))


def normalize_content(text: str) -> str:
    """小红书编辑器不支持连续空行输入：把 2+ 连续空行压成单空行，清行尾空白。

    移植 Easel ``_normalize_content``（xhs_publish.py:500）——这是修「发布失败」的关键一步。
    """
    if not text:
        return text
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _re.sub(r"[ \t]+\n", "\n", text)
    text = _re.sub(r"\n{3,}", "\n\n", text)
    return text.strip("\n")


# --------------------------------------------------------------------------- #
# 浏览器会话
# --------------------------------------------------------------------------- #

class BrowserSession:
    """包一层 ``launch_persistent_context``，提供 ``page`` 与幂等的 ``close()``。

    与本项目抖音适配器同构（每个平台各自持有这份轻量封装，换来模块间零耦合）。
    """

    def __init__(self, playwright: Any, user_data_dir: str, *, headless: bool,
                 timeout_ms: int = 30_000, args: tuple[str, ...] = LAUNCH_ARGS):
        self._ctx = self._launch(playwright, user_data_dir, headless=headless,
                                 timeout_ms=timeout_ms, args=args)
        pages = list(getattr(self._ctx, "pages", []) or [])
        self.page = pages[0] if pages else self._ctx.new_page()
        try:
            self.page.set_default_timeout(30_000)
            self.page.set_default_navigation_timeout(60_000)
        except Exception:
            pass

    @staticmethod
    def _launch(playwright: Any, user_data_dir: str, *, headless: bool,
                timeout_ms: int, args: tuple[str, ...]):
        """启动持久化上下文；先试禁用系统代理（小红书直连更稳），失败退回默认。"""
        common: dict[str, Any] = dict(
            headless=headless,
            locale="zh-CN",                                   # 反检测——Easel xhs_publish.py:555
            viewport={"width": 1440, "height": 900},
            timeout=timeout_ms,
        )
        try:
            return playwright.chromium.launch_persistent_context(
                user_data_dir, args=list(args) + ["--no-proxy-server"], **common
            )
        except Exception:
            return playwright.chromium.launch_persistent_context(
                user_data_dir, args=list(args), **common
            )

    def close(self) -> None:
        try:
            self._ctx.close()
        except Exception:
            pass


@contextmanager
def open_browser(adapter: "XiaoHongShuAdapter", *, headless: bool,
                 timeout_ms: int = 30_000) -> Iterator[BrowserSession | None]:
    """打开浏览器会话；playwright 缺失/内核缺失时产出 ``None``（由调用方给中文提示）。"""
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        yield None
        return
    with sync_playwright() as pw:
        session: BrowserSession | None = None
        try:
            session = BrowserSession(pw, adapter.profile_dir(), headless=headless, timeout_ms=timeout_ms)
        except Exception:
            yield None
            return
        try:
            yield session
        finally:
            session.close()


# --------------------------------------------------------------------------- #
# DOM 小工具（全部容错，绝不抛异常）
# --------------------------------------------------------------------------- #

def first_visible(page: Any, selectors: tuple[str, ...], *, scope: Any = None) -> Any:
    """按候选顺序返回第一个可见元素；都没有返回 None。"""
    target = scope if scope is not None else page
    for sel in selectors:
        try:
            candidates = target.query_selector_all(sel)
        except Exception:
            continue
        for el in candidates:
            try:
                if el.is_visible():
                    return el
            except Exception:
                continue
    return None


def query_safe(page: Any, selectors: tuple[str, ...]) -> Any:
    """容错查询：导航瞬间查询旧 DOM 会抛 "Execution context was destroyed"，
    这里吞掉异常返回 None（Easel ``_query_safe``，xhs_publish.py:627）。"""
    for sel in selectors:
        try:
            el = page.query_selector(sel)
        except Exception:
            continue
        if el is not None:
            return el
    return None


def is_visible_safe(el: Any) -> bool:
    try:
        return bool(el is not None and el.is_visible())
    except Exception:
        return False


def click_soft(el: Any) -> bool:
    """尽最大努力点击：原生 click → 滚动后 force click → JS 事件链。"""
    if el is None:
        return False
    try:
        el.scroll_into_view_if_needed()
    except Exception:
        pass
    try:
        el.click(timeout=8_000)
        return True
    except Exception:
        pass
    try:
        el.click(force=True, timeout=5_000)
        return True
    except Exception:
        pass
    try:
        el.evaluate(
            "el => ['pointerdown','mousedown','pointerup','mouseup','click']"
            ".forEach(t => el.dispatchEvent(new MouseEvent(t,{bubbles:true,cancelable:true,view:true})))"
        )
        return True
    except Exception:
        return False


def clear_and_type(page: Any, el: Any, text: str, *, delay_ms: int = 40) -> bool:
    """聚焦 → 全选清空 → 逐字符输入（反检测，Easel ``_human_type`` xhs_publish.py:273）。

    默认 40ms/字，落在 Easel 的 ``random(30,110)`` 区间内（取固定值以免引入随机依赖）。
    """
    if el is None or not text:
        return False
    try:
        el.click()
    except Exception:
        if not click_soft(el):
            return False
    try:
        page.wait_for_timeout(150)
        try:
            page.keyboard.press("Control+a")
            page.keyboard.press("Delete")
        except Exception:
            pass
        for ch in text:
            page.keyboard.type(ch)
            if delay_ms:
                page.wait_for_timeout(delay_ms)
        return True
    except Exception:
        return False


def click_text_in(page: Any, scope: Any, texts: tuple[str, ...]) -> bool:
    """在 scope 内按文案点击（先 button/a，再 text= 节点，最后 JS 遍历）。"""
    base = scope if scope is not None else page
    for text in texts:
        for sel in (f"button:has-text('{text}')", f"a:has-text('{text}')",
                    f"[role=button]:has-text('{text}')"):
            try:
                for el in base.query_selector_all(sel):
                    try:
                        if el.is_visible() and click_soft(el):
                            return True
                    except Exception:
                        continue
            except Exception:
                continue
        try:
            for el in base.query_selector_all(f"text={text}"):
                try:
                    if el.is_visible() and click_soft(el):
                        return True
                except Exception:
                    continue
        except Exception:
            continue
    try:
        base.evaluate(
            """(kws) => {
                const nodes = Array.from(document.querySelectorAll('button,a,[role=button],span,div'));
                for (const kw of kws) {
                    const hit = nodes.find(n => (n.innerText || '').trim() === kw && n.offsetParent !== null);
                    if (hit) { hit.click(); return true; }
                }
                return false;
            }""",
            list(texts),
        )
        return True
    except Exception:
        return False


def dump_debug(page: Any, directory: str, tag: str) -> str:
    """把当前页面 DOM + 截图落盘，供选择器校准；返回落盘目录（失败返回空串）。"""
    try:
        target = Path(directory)
        target.mkdir(parents=True, exist_ok=True)
        try:
            (target / f"xiaohongshu-{tag}.html").write_text(page.content(), encoding="utf-8")
        except Exception:
            pass
        try:
            page.screenshot(path=str(target / f"xiaohongshu-{tag}.png"))
        except Exception:
            pass
        return str(target)
    except Exception:
        return ""


def wait_until(predicate: Any, *, timeout_s: float, interval_s: float = 0.8) -> bool:
    """轮询等待条件成立；超时返回 False，异常按「不成立」处理。"""
    deadline = time.time() + max(0.1, timeout_s)
    while time.time() < deadline:
        try:
            if predicate():
                return True
        except Exception:
            pass
        time.sleep(interval_s)
    return False


@contextmanager
def with_longer_timeout(page: Any, timeout_ms: int = 120_000) -> Iterator[None]:
    """临时放宽页面默认超时（大文件 set_input_files 会阻塞到超时）。

    退出时恢复原值；页面已关闭等异常一律吞掉。
    """
    original: Any = None
    try:
        original = page.get_default_timeout()
    except Exception:
        original = None
    try:
        page.set_default_timeout(timeout_ms)
    except Exception:
        pass
    try:
        yield
    finally:
        if original is not None:
            try:
                page.set_default_timeout(original)
            except Exception:
                pass


# --------------------------------------------------------------------------- #
# 适配器
# --------------------------------------------------------------------------- #

class XiaoHongShuAdapter(PlatformAdapter):
    """小红书适配器（图文 / 视频笔记，creator.xiaohongshu.com 创作者中心）。"""

    key = "xiaohongshu"
    label = "小红书"
    home_url = EXPLORE_URL
    publish_url = PUBLISH_URL
    needs_media = True
    supports_auto_publish = True
    metrics_hint = (
        "小红书单篇数据需已登录态打开笔记页；官方未在网页版公开「浏览量」，"
        "取不到时记为 0，可在创作者中心「数据看板」人工查看"
    )

    @property
    def limits(self) -> PlatformLimits:
        return PlatformLimits(
            title_max=TITLE_MAX,        # 标题 ≤ 20 全角字（upstream pkg/xhsutil/title.go）
            body_max=1000,              # 正文 ≤ 1000 字（Easel SKILL.md 约束）
            body_min=0,
            tags_max=10,
            requires_media=True,        # 小红书笔记必须有图或视频
            media_max=18,               # 图文最多 18 张
            media_kinds=("image", "video"),
            supports_video=True,
            notes="图文与视频二选一，不可混用；标题按全角字数计（20 字上限）",
        )

    # -- 内部小工具 ----------------------------------------------------
    def _log(self, on_progress: Any, message: str, percent: int | None = None) -> None:
        emit(on_progress, message, percent)

    def _debug_dir(self) -> str:
        return str(Path(self.profile_dir()) / DEBUG_SUBDIR)

    def _qr_png(self) -> str:
        return str(Path(self.profile_dir()) / "login_qr.png")

    def _shot_qr(self, page: Any, qr: Any, out_path: str) -> None:
        """截二维码：canvas 直接元素截图常为空白，优先页面级截图 + clip。"""
        box = None
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

    # -- 登录态判定 ----------------------------------------------------
    def _risk_blocked(self, page: Any) -> bool:
        """是否被小红书风控拦截（Easel ``_risk_blocked``，xhs_publish.py:235）。

        风险 IP 会得到「安全限制 300012 · IP存在风险」，此时二维码根本不弹。
        """
        try:
            url = page.url or ""
            if any(m in url for m in RISK_URL_MARKERS):
                return True
        except Exception:
            pass
        try:
            title = page.title() or ""
            if any(m in title for m in RISK_TITLE_MARKERS):
                return True
        except Exception:
            pass
        try:
            html = page.content() or ""
            if any(m in html for m in RISK_BODY_MARKERS):
                return True
        except Exception:
            pass
        return False

    def _is_logged_in(self, page: Any) -> bool:
        """登录态判定：①出现用户区元素 ②URL 在创作者域名下且不在 /login。

        （Easel ``_is_logged_in``，xhs_publish.py:245）
        """
        if query_safe(page, LOGIN_OK_SELECTORS) is not None:
            return True
        try:
            url = (page.url or "").split("?", 1)[0]
        except Exception:
            return False
        if "creator.xiaohongshu.com" in url and "/login" not in url:
            return True
        return False

    def _probe_login(self, page: Any, *, wait_s: float = 10) -> LoginState:
        """轮询判定登录态：先看登录成功标志，再看是否落到登录页/弹窗。"""
        deadline = time.time() + max(0.5, wait_s)
        saw_login_ui = False
        while time.time() < deadline:
            if self._is_logged_in(page):
                return LoginState.LOGGED_IN
            if query_safe(page, QRCODE_SELECTORS) is not None:
                saw_login_ui = True
            try:
                url = (page.url or "").lower()
            except Exception:
                url = ""
            if "login" in url or "website-login" in url:
                saw_login_ui = True
            if saw_login_ui:
                return LoginState.LOGGED_OUT
            try:
                page.wait_for_timeout(500)
            except Exception:
                return LoginState.UNKNOWN
        return LoginState.UNKNOWN

    # -- 登录 ----------------------------------------------------------
    def check_login(self, *, headless: bool = True) -> tuple[bool, str]:
        """检测登录态。失败一律返回 ``(False, 中文原因)``，不抛异常。"""
        try:
            with open_browser(self, headless=headless) as sess:
                if sess is None:
                    return False, ("浏览器不可用：请先安装 playwright 与 chromium"
                                   "（pip install playwright && playwright install chromium）")
                page = sess.page
                try:
                    page.goto(EXPLORE_URL, wait_until="domcontentloaded", timeout=45_000)
                except Exception as exc:
                    return False, f"打开小红书首页失败：{type(exc).__name__}（请检查网络或代理）"
                state = self._probe_login(page, wait_s=12)
                if state is LoginState.LOGGED_IN:
                    return True, "小红书已登录"
                # 首页被风控时改开创作平台再判一次（Easel whoami 同策略，xhs_publish.py:911）
                if self._risk_blocked(page):
                    try:
                        page.goto(CREATOR_LOGIN_URL, wait_until="domcontentloaded", timeout=45_000)
                        page.wait_for_timeout(1_500)
                    except Exception:
                        pass
                    if self._is_logged_in(page):
                        return True, "小红书已登录"
                    return False, ("小红书判定当前网络为风险 IP（安全限制 300012）——"
                                   "请换干净网络/家宽代理后重试，或在正常网络下登录后拷贝登录态目录")
                if state is LoginState.LOGGED_OUT:
                    return False, "小红书未登录，请先点击「登录」扫码"
                return False, "无法确认小红书登录态（页面可能改版或加载超时）"
        except Exception as exc:  # noqa: BLE001 — 对外只返回中文说明
            return False, f"检测小红书登录态异常：{type(exc).__name__}: {exc}"

    def login(self, *, timeout_sec: int = 300) -> tuple[bool, str]:
        """打开有头浏览器引导扫码登录，并把登录态持久化到 ``profile_dir()``。"""
        try:
            with open_browser(self, headless=False) as sess:
                if sess is None:
                    return False, ("浏览器不可用：请先安装 playwright 与 chromium"
                                   "（pip install playwright && playwright install chromium）")
                page = sess.page
                # 先探一次：已登录则无需扫码
                try:
                    page.goto(EXPLORE_URL, wait_until="domcontentloaded", timeout=45_000)
                except Exception as exc:
                    return False, f"打不开小红书登录页：{type(exc).__name__}（请检查网络或代理）"
                page.wait_for_timeout(1_500)
                if self._probe_login(page, wait_s=5) is LoginState.LOGGED_IN:
                    return True, "小红书已是登录状态，无需重新扫码"
                return self._qr_login_loop(page, timeout_sec=timeout_sec)
        except Exception as exc:  # noqa: BLE001
            return False, f"小红书登录异常：{type(exc).__name__}: {exc}"

    def _qr_login_loop(self, page: Any, *, timeout_sec: int) -> tuple[bool, str]:
        """引导扫码：等二维码 → 落盘二维码图片供 UI 展示 → 轮询登录成功。

        二维码失效不会主动刷新（小红书弹窗自带刷新按钮，用户可在窗口内点击）。
        """
        qr_path = self._qr_png()
        deadline = time.time() + max(30, timeout_sec)
        last_shot = 0.0
        waited_for_qr = False
        while time.time() < deadline:
            # ① 登录成功判定：URL 变化或用户区元素出现
            if self._is_logged_in(page):
                try:
                    Path(qr_path).unlink()
                except OSError:
                    pass
                return True, "登录成功"
            # ② 兜底：首页被风控时改开创作平台登录页
            if not waited_for_qr and self._risk_blocked(page):
                waited_for_qr = True
                try:
                    page.goto(CREATOR_LOGIN_URL, wait_until="domcontentloaded", timeout=45_000)
                except Exception:
                    pass
                try:
                    click_text_in(page, page, LOGIN_TABS_BY_TEXT)   # 切到「扫码登录」
                except Exception:
                    pass
                page.wait_for_timeout(1_200)
            # ③ 每 ~10s 重截二维码（小红书码约几分钟过期，只截一次可能扫到失效码）
            if time.time() - last_shot > 10:
                qr = first_visible(page, QRCODE_SELECTORS)
                if qr is not None:
                    self._shot_qr(page, qr, qr_path)
                    last_shot = time.time()
            try:
                page.wait_for_timeout(1_500)
            except Exception as exc:
                low = str(exc).lower()
                if "closed" in low or "target" in low:
                    return False, "登录窗口已被关闭，请重新点击「登录」"
                return False, f"登录过程中浏览器异常：{type(exc).__name__}"
        return False, (f"{timeout_sec}s 内未检测到登录成功（二维码可能已过期）。"
                       f"请重新点击「登录」再扫一次（二维码截图：{qr_path}）")

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
        """执行发布。``dry_run=True`` 时只填写表单不提交（人工确认前的预览）。"""
        # 1) 入参校验（发布前检查，超限直接给中文提示）
        title = (payload.title or "").strip()
        body = (payload.body or "").strip()
        tags = [str(t).strip().lstrip("#") for t in (payload.tags or []) if str(t).strip()]
        lim = self.limits
        if not title:
            return PublishResult(ok=False, message="小红书标题不能为空")
        title_len = calc_title_length(title)
        if title_len > lim.title_max:
            return PublishResult(
                ok=False,
                message=f"标题超长（按小红书口径 {title_len}/{lim.title_max} 字），请精简后再发布",
            )
        if len(body) > lim.body_max:
            return PublishResult(
                ok=False,
                message=f"正文超长（{len(body)}/{lim.body_max} 字），请精简后再发布",
            )
        if len(tags) > lim.tags_max:
            return PublishResult(ok=False, message=f"话题过多（{len(tags)}/{lim.tags_max} 个），请删减后再发布")

        media = [str(p) for p in (payload.media or []) if str(p).strip()]
        if not media:
            return PublishResult(ok=False, message="小红书笔记必须有图片或视频，请先在预览里选择媒体文件")
        missing = [p for p in media if not Path(p).expanduser().is_file()]
        if missing:
            return PublishResult(ok=False, message=f"媒体文件不存在：{missing[0]}")
        if len(media) > lim.media_max:
            return PublishResult(ok=False, message=f"媒体文件过多（{len(media)}/{lim.media_max}），请删减后再发布")

        # 判断图文还是视频：视频只能单独一个（Easel SKILL.md：两者不可混用）
        videos = [p for p in media if Path(p).suffix.lower() in (".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm")]
        images = [p for p in media if p not in videos]
        if videos and images:
            return PublishResult(ok=False, message="小红书图文与视频不可混用，请只保留一种媒体")
        if len(videos) > 1:
            return PublishResult(ok=False, message="小红书视频笔记只支持 1 个视频文件")
        kind = "video" if videos else "image"

        try:
            with open_browser(self, headless=headless) as sess:
                if sess is None:
                    return PublishResult(ok=False, message="浏览器不可用：请先安装 playwright 与 chromium")
                page = sess.page
                try:
                    result = self._publish_flow(
                        page, title=title, body=body, tags=tags, media=media, kind=kind,
                        dry_run=dry_run, timeout_sec=timeout_sec, on_progress=on_progress,
                    )
                except Exception as exc:  # noqa: BLE001
                    where = dump_debug(page, self._debug_dir(), "publish-error")
                    note = f"，失败现场已落盘：{where}" if where else ""
                    return PublishResult(
                        ok=False,
                        message=f"小红书发布失败：{type(exc).__name__}: {exc}{note}",
                    )
                # dry-run 的「等待人工确认」阶段必须保住浏览器窗口，故在 close 之前执行
                if dry_run and result.ok:
                    self._hold_for_review(page, payload, on_progress)
                return result
        except Exception as exc:  # noqa: BLE001
            return PublishResult(ok=False, message=f"小红书发布异常：{type(exc).__name__}: {exc}")

    def _hold_for_review(self, page: Any, payload: PublishPayload, on_progress: Any) -> None:
        """dry-run 收尾：保持窗口打开供人工复核，可选调用人工确认回调。

        ``payload.extra["confirm_callback"](page)`` 返回 True 表示人工已确认可以关闭；
        否则最多保持 ``payload.extra["hold_sec"]``（默认 180 秒，上限 600 秒）后关闭。
        """
        extra = payload.extra or {}
        hold_sec = 180
        try:
            hold_sec = int(extra.get("hold_sec", 180))
        except Exception:
            hold_sec = 180
        hold_sec = max(30, min(600, hold_sec))
        callback = extra.get("confirm_callback")
        if callable(callback):
            self._log(on_progress, "已填写完成，等待人工确认…", 95)
            wait_until(lambda: bool(callback(page)), timeout_s=hold_sec, interval_s=1.0)
            return
        self._log(on_progress, f"已填写完成，浏览器窗口保持 {hold_sec} 秒供人工复核…", 95)
        try:
            page.wait_for_timeout(hold_sec * 1000)
        except Exception:
            pass

    def _publish_flow(
        self,
        page: Any,
        *,
        title: str,
        body: str,
        tags: list[str],
        media: list[str],
        kind: str,
        dry_run: bool,
        timeout_sec: int,
        on_progress: Any,
    ) -> PublishResult:
        budget = max(60, timeout_sec)
        deadline = time.time() + budget

        # 1) 打开发布页
        self._log(on_progress, "打开发布页…", 5)
        try:
            page.goto(PUBLISH_URL, wait_until="domcontentloaded", timeout=45_000)
        except Exception as exc:
            return PublishResult(ok=False, message=f"打开发布页失败：{type(exc).__name__}（请检查网络或代理）")
        page.wait_for_timeout(1_500)
        if not self._is_logged_in(page):
            if "login" in (page.url or "").lower() or query_safe(page, QRCODE_SELECTORS) is not None:
                return PublishResult(ok=False, message="小红书未登录，请先在「账号」页扫码登录后再发布")

        # 2) 切「上传图文 / 上传视频」tab
        tab_name = "上传视频" if kind == "video" else "上传图文"
        self._log(on_progress, f"选择「{tab_name}」…", 12)
        if not self._click_publish_tab(page, tab_name):
            where = dump_debug(page, self._debug_dir(), "no-publish-tab")
            return PublishResult(
                ok=False,
                message=f"未找到发布 TAB「{tab_name}」（小红书可能改版）"
                        + (f"，失败现场已落盘：{where}" if where else ""),
            )
        page.wait_for_timeout(1_000)

        # 3) 上传媒体
        if kind == "video":
            self._log(on_progress, "上传视频（等待平台处理，最长 10 分钟）…", 20)
            if not self._upload_video(page, media[0]):
                where = dump_debug(page, self._debug_dir(), "no-file-input")
                return PublishResult(
                    ok=False,
                    message="未找到视频上传入口（file input），无法上传"
                            + (f"，失败现场已落盘：{where}" if where else ""),
                )
            if not self._wait_publish_clickable(page, timeout_s=600):
                where = dump_debug(page, self._debug_dir(), "video-not-ready")
                return PublishResult(
                    ok=False,
                    message="视频上传/转码超时或编辑器未就绪（已按未发布处理）"
                            + (f"，失败现场已落盘：{where}" if where else ""),
                )
            page.wait_for_timeout(1_000)
        else:
            self._log(on_progress, f"上传图片（共 {len(media)} 张）…", 20)
            failed = self._upload_images(page, media)
            if failed:
                where = dump_debug(page, self._debug_dir(), "image-upload-timeout")
                return PublishResult(
                    ok=False,
                    message=failed + (f"，失败现场已落盘：{where}" if where else ""),
                )

        # 4) 填写标题
        self._log(on_progress, "填写标题…", 45)
        title_el = first_visible(page, TITLE_INPUT_SELECTORS)
        if title_el is None:
            where = dump_debug(page, self._debug_dir(), "no-title-input")
            return PublishResult(
                ok=False,
                message="未找到标题输入框（小红书可能改版）"
                        + (f"，失败现场已落盘：{where}" if where else ""),
            )
        if not clear_and_type(page, title_el, title):
            return PublishResult(ok=False, message="标题输入失败，请重试或在浏览器窗口内手动填写")
        page.wait_for_timeout(400)

        # 5) 填写正文（小红书编辑器不支持连续空行，先规范化）
        self._log(on_progress, "填写正文…", 60)
        body = normalize_content(body)
        content_el = self._content_element(page)
        if content_el is None:
            where = dump_debug(page, self._debug_dir(), "no-content-editor")
            return PublishResult(
                ok=False,
                message="未找到正文编辑器（小红书可能改版）"
                        + (f"，失败现场已落盘：{where}" if where else ""),
            )
        if body and not clear_and_type(page, content_el, body):
            return PublishResult(ok=False, message="正文输入失败，请重试或在浏览器窗口内手动填写")
        page.wait_for_timeout(500)
        # 回点标题增强稳定性（Easel waitAndClickTitleInput，xhs_publish.py:524）
        try:
            title_el.click(timeout=3_000)
        except Exception:
            pass

        # 6) 添加话题标签（# + 联想下拉点选，真绑话题）
        if tags:
            self._log(on_progress, f"添加话题标签（{len(tags)} 个）…", 72)
            self._input_tags(page, content_el, tags)

        # 7) 平台侧长度校验
        overflow = self._check_overflow(page)
        if overflow:
            where = dump_debug(page, self._debug_dir(), "length-overflow")
            return PublishResult(
                ok=False,
                message=f"平台提示内容超出长度限制：{overflow}"
                        + (f"，失败现场已落盘：{where}" if where else ""),
            )

        # 8) dry_run：停在最后一步，绝不点击发布
        if dry_run:
            self._log(on_progress, "已填写完成，等待人工确认（未点击发布）", 90)
            return PublishResult(
                ok=True,
                url=page.url or "",
                message="已填写完成，等待人工确认",
                detail={
                    "dry_run": True,
                    "kind": kind,
                    "media": media,
                    "tags": tags,
                    "title": title,
                    "hint": "请在弹出的浏览器窗口内核对标题/正文/图片与话题，无误后手动点击「发布」",
                },
            )

        # 9) 正式发布：等按钮可点击 → 点击 → 二次确认 → 成功校验
        self._log(on_progress, "等待「发布」按钮就绪…", 80)
        found = self._wait_publish_clickable(page, timeout_s=min(60, max(15, int(deadline - time.time()))))
        if not found:
            where = dump_debug(page, self._debug_dir(), "publish-button-not-ready")
            return PublishResult(
                ok=False,
                message="「发布」按钮迟迟不可点击（可能校验未过或仍在处理媒体），已按未发布处理"
                        + (f"，失败现场已落盘：{where}" if where else ""),
            )
        self._log(on_progress, "提交发布…", 88)
        if not self._click_publish_button(page, found):
            return PublishResult(ok=False, message="点击「发布」按钮失败，请改为在浏览器窗口内人工发布")
        page.wait_for_timeout(1_000)
        self._confirm_publish_dialog(page)

        self._log(on_progress, "等待平台回执…", 94)
        ok, message = self._wait_publish_success(page, timeout_s=60)
        if not ok:
            where = dump_debug(page, self._debug_dir(), "publish-unconfirmed")
            return PublishResult(
                ok=False,
                url=page.url or "",
                message=message + (f"，失败现场已落盘：{where}" if where else ""),
                detail={"unconfirmed": True},
            )
        # 10) 成功：尝试提取作品链接（进不了笔记管理页就退回当前 URL，不算失败）
        self._log(on_progress, "发布成功，正在提取作品链接…", 98)
        note_url = self._find_latest_note_url(page)
        self._log(on_progress, "发布完成", 100)
        return PublishResult(
            ok=True,
            url=note_url,
            message=message,
            detail={"kind": kind, "title": title, "tags": tags,
                    "note_url": note_url, "publish_page_url": page.url or ""},
        )

    # -- 发布子步骤 ----------------------------------------------------
    def _click_publish_tab(self, page: Any, tabname: str) -> bool:
        """点「上传图文/视频」tab：重试 15s + 遮挡检测 + 移弹层。

        移植 Easel ``_click_publish_tab``（xhs_publish.py:281）/ upstream ``mustClickPublishTab``。
        """
        try:
            if first_visible(page, UPLOAD_CONTENT_SELECTORS) is None:
                page.wait_for_timeout(1_500)
        except Exception:
            pass
        deadline = time.time() + 15
        while time.time() < deadline:
            for tab in _query_all_safe(page, CREATOR_TAB_SELECTORS):
                try:
                    if not tab.is_visible():
                        continue
                    if (tab.inner_text() or "").strip() != tabname:
                        continue
                except Exception:
                    continue
                # 遮挡检测（elementFromPoint 命中的是不是自己）
                try:
                    blocked = tab.evaluate(
                        """(el) => { const r = el.getBoundingClientRect();
                            if (!r.width || !r.height) return true;
                            const t = document.elementFromPoint(r.left + r.width/2, r.top + r.height/2);
                            return !(t === el || el.contains(t)); }"""
                    )
                except Exception:
                    blocked = False
                if blocked:
                    cover = first_visible(page, POP_COVER_SELECTORS)
                    if cover is not None:
                        try:
                            cover.evaluate("el => el.remove()")
                        except Exception:
                            pass
                    page.wait_for_timeout(200)
                    continue
                if click_soft(tab):
                    return True
            page.wait_for_timeout(200)
        return False

    def _upload_images(self, page: Any, paths: list[str]) -> str:
        """逐张上传并等预览出现（≤60s/张）。返回空串表示成功，否则返回中文失败说明。"""
        for i, path in enumerate(paths):
            selectors = UPLOAD_INPUT_FIRST_SELECTORS if i == 0 else UPLOAD_INPUT_MORE_SELECTORS
            ok = False
            for sel in selectors:
                try:
                    # 大图 set_input_files 可能阻塞较久，临时放宽默认超时
                    with with_longer_timeout(page, 120_000):
                        page.set_input_files(sel, path)
                    ok = True
                    break
                except Exception:
                    continue
            if not ok:
                return f"未找到第 {i + 1} 张图片的上传入口（file input），无法上传"
            deadline = time.time() + 60
            while time.time() < deadline:
                try:
                    if len(page.query_selector_all(IMG_PREVIEW_SELECTORS[0])) >= i + 1:
                        break
                    if first_visible(page, IMG_PREVIEW_SELECTORS) is not None and i == 0:
                        break
                except Exception:
                    pass
                page.wait_for_timeout(500)
            else:
                return f"第 {i + 1} 张图片上传超时（60 秒）：{path}"
            page.wait_for_timeout(1_000)
        return ""

    def _upload_video(self, page: Any, path: str) -> bool:
        """上传视频（等「发布按钮可点击」由调用方负责，≤10min）。"""
        for sel in UPLOAD_INPUT_FIRST_SELECTORS:
            try:
                with with_longer_timeout(page, 600_000):   # 大视频写入可能很慢
                    page.set_input_files(sel, path)
                page.wait_for_timeout(1_000)
                return True
            except Exception:
                continue
        # 兜底：点上传区触发 filechooser
        try:
            with page.expect_file_chooser(timeout=8_000) as fc:
                trigger = first_visible(page, UPLOAD_CONTENT_SELECTORS)
                if trigger is None or not click_soft(trigger):
                    return False
            fc.value.set_files(path)
            page.wait_for_timeout(1_000)
            return True
        except Exception:
            return False

    def _content_element(self, page: Any) -> Any:
        """定位正文编辑器：新版 contenteditable/tiptap → 旧版 ql-editor → 占位符反查祖先。

        移植 Easel ``_content_element``（xhs_publish.py:339）/ upstream ``getContentElement``。
        """
        for sel in ("div.ql-editor",
                    "div[role='textbox'][contenteditable='true']",
                    "div.tiptap[contenteditable='true']",
                    "div.editor-container [contenteditable='true']"):
            try:
                el = page.query_selector(sel)
            except Exception:
                el = None
            if el is not None:
                return el
        el = first_visible(page, CONTENT_EDITOR_SELECTORS)
        if el is not None:
            return el
        ph = query_safe(page, CONTENT_PLACEHOLDER_SELECTORS)
        if ph is None:
            return None
        cur = ph
        for _ in range(5):
            try:
                parent = cur.evaluate_handle("el => el.parentElement").as_element()
            except Exception:
                break
            if not parent:
                break
            try:
                role = parent.get_attribute("role") or ""
                if role == "textbox" or parent.get_attribute("contenteditable") == "true":
                    return parent
            except Exception:
                pass
            cur = parent
        return None

    def _check_overflow(self, page: Any) -> str:
        """读平台自身的溢出提示；有则返回提示文案，无则空串（Easel ``_check_overflow``）。

        只认「超出/超出限制/最多/不能超过」这类措辞，避免把同名的装饰性元素误判为超限。
        """
        for selectors in (TITLE_OVERFLOW_SELECTORS, CONTENT_OVERFLOW_SELECTORS):
            el = first_visible(page, selectors)
            if el is None:
                continue
            try:
                text = (el.inner_text() or "").strip()
            except Exception:
                text = ""
            if not text:
                continue
            if any(kw in text for kw in ("超出", "超过", "最多", "超限", "上限")):
                return text
        return ""

    def _input_tags(self, page: Any, content_el: Any, tags: list[str]) -> None:
        """话题：正文末尾输 ``#`` + 联想下拉点第一项，真绑定话题。

        移植 Easel ``_input_tags``（xhs_publish.py:370）/ upstream ``inputTag``。
        关键：``#`` 与话题名必须**连续输入**，中途不能再 click 正文，否则光标被挪走、
        ``#`` 与话题名被拆开，小红书识别不到连续 token，联想不出、绑不上。
        """
        try:
            content_el.click()
        except Exception:
            click_soft(content_el)
        try:
            page.keyboard.press("Control+End")   # 光标移到正文末尾
            page.wait_for_timeout(400)
        except Exception:
            pass
        for raw in tags:
            tag = str(raw).lstrip("#").strip()
            if not tag:
                continue
            try:
                page.keyboard.type(" ")          # 与正文/上一话题分隔，确保 # 起新 token
                page.keyboard.type("#")
                page.wait_for_timeout(300)
                for ch in tag:                   # 逐字输入，不再 click
                    page.keyboard.type(ch)
                    page.wait_for_timeout(40)
                page.wait_for_timeout(1_000)
            except Exception:
                return
            item = first_visible(page, TOPIC_ITEM_SELECTORS)
            if item is not None:
                click_soft(item)                 # 点联想第一项 = 真正绑定话题
            else:
                try:
                    page.keyboard.type(" ")      # 无联想则退化为空格分隔（至少保留 #文字）
                except Exception:
                    pass
            try:
                page.wait_for_timeout(500)
            except Exception:
                return

    def _wait_publish_clickable(self, page: Any, timeout_s: int = 30) -> tuple[str, Any] | None:
        """等发布按钮可点击（新版 ``<xhs-publish-btn>`` / 旧版 ``button.bg-red`` 双兼容）。

        移植 Easel ``_wait_publish_clickable``（xhs_publish.py:400）/ upstream ``findPublishButton``。
        """
        deadline = time.time() + max(3, timeout_s)
        while time.time() < deadline:
            for widget in _query_all_safe(page, PUBLISH_BTN_NEW_SELECTORS):
                try:
                    if not widget.is_visible():
                        continue
                    if (widget.get_attribute("is-publish") or "") == "false":
                        continue
                    if (widget.get_attribute("submit-disabled") or "") == "true":
                        continue
                    return ("new", widget)
                except Exception:
                    continue
            for btn in _query_all_safe(page, PUBLISH_BTN_OLD_SELECTORS):
                try:
                    if not btn.is_visible():
                        continue
                    if btn.get_attribute("disabled") is not None:
                        continue
                    if (btn.get_attribute("aria-disabled") or "") in ("true", "1"):
                        continue
                    return ("old", btn)
                except Exception:
                    continue
            page.wait_for_timeout(1_000)
        return None

    def _click_publish_button(self, page: Any, found: tuple[str, Any]) -> bool:
        """点击发布按钮。新版 web component 内含[暂存离开][发布]两颗按钮，故按坐标点右侧。

        Easel 实测：``<xhs-publish-btn>`` 是闭合 Shadow DOM 宽横条，点 host 中心会落在
        两按钮间隙而无效，发布按钮在右侧约 62% 处（xhs_publish.py:529-541）。
        """
        kind, btn = found
        try:
            btn.scroll_into_view_if_needed()
        except Exception:
            pass
        try:
            page.wait_for_timeout(300)
        except Exception:
            pass
        if kind == "new":
            box = None
            try:
                box = btn.bounding_box()
            except Exception:
                box = None
            if box and box.get("width", 0) > 40:
                try:
                    page.mouse.click(box["x"] + box["width"] * 0.62, box["y"] + box["height"] / 2)
                    return True
                except Exception:
                    pass
        if click_soft(btn):
            return True
        # 兜底：按文案找按钮（仅在结构失效时）
        return click_text_in(page, None, PUBLISH_BTN_TEXTS)

    def _confirm_publish_dialog(self, page: Any) -> None:
        """点发布后若弹二次确认框，点其确认按钮（保守：仅当可见且文案匹配）。"""
        deadline = time.time() + 5
        while time.time() < deadline:
            for sel in ("button, .d-button, [role=button]",):
                for btn in _query_all_safe(page, (sel,)):
                    try:
                        if not btn.is_visible():
                            continue
                        text = (btn.inner_text() or "").strip()
                        if text in CONFIRM_PUBLISH_TEXTS:
                            click_soft(btn)
                            page.wait_for_timeout(1_000)
                            return
                    except Exception:
                        continue
            page.wait_for_timeout(500)

    def _diag_after_publish(self, page: Any) -> str:
        """抓屏幕上可见的弹框/报错/toast 文案，供排错（Easel ``_diag_after_publish``）。"""
        bits: list[str] = []
        for sel in DIAG_TEXT_SELECTORS:
            for el in _query_all_safe(page, (sel,)):
                try:
                    if not el.is_visible():
                        continue
                    text = (el.inner_text() or "").strip().replace("\n", " / ")
                except Exception:
                    continue
                if text and text not in " ".join(bits):
                    bits.append(f"[{sel}] {text[:120]}")
        return "屏幕可见元素：" + " | ".join(bits[:6]) if bits else "屏幕无可识别弹框/报错"

    def _wait_publish_success(self, page: Any, timeout_s: int = 60) -> tuple[bool, str]:
        """发布成功校验。

        小红书发布成功后**常原地清空表单回到上传页**（不换 URL），成功信号任一：
        ①跳离 ``/publish/publish`` ②出现含「成功」的 toast ③编辑表单已复位
        （标题框与图片预览都消失）。移植 Easel ``_wait_publish_success``（xhs_publish.py:472）。
        """
        deadline = time.time() + max(5, timeout_s)
        while time.time() < deadline:
            try:
                url = page.url or ""
            except Exception:
                url = ""
            if "/publish/publish" not in url:
                return True, f"发布成功（已跳转：{url}）"
            for sel in SUCCESS_TOAST_SELECTORS:
                el = first_visible(page, (sel,))
                if el is None:
                    continue
                try:
                    text = (el.inner_text() or "").strip()
                except Exception:
                    continue
                if "成功" in text:
                    return True, f"发布成功（{text[:40]}）"
            # 表单已复位：标题框 + 图片预览都消失 = 已提交回到空上传页
            try:
                if (query_safe(page, TITLE_INPUT_SELECTORS) is None
                        and first_visible(page, IMG_PREVIEW_SELECTORS) is None):
                    return True, "发布成功（编辑表单已清空复位）"
            except Exception:
                pass
            page.wait_for_timeout(500)
        return False, "发布未确认成功：点击发布后未跳离发布页、未见成功提示。" + self._diag_after_publish(page)

    def _find_latest_note_url(self, page: Any) -> str:
        """发布成功后到笔记管理页提取最新作品链接；取不到返回空串（不算发布失败）。"""
        try:
            page.goto(CREATOR_NOTES_URL, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(2_500)
        except Exception:
            return ""
        for sel in NOTE_LINK_SELECTORS:
            for el in _query_all_safe(page, (sel,)):
                try:
                    href = el.get_attribute("href") or ""
                except Exception:
                    continue
                if not href:
                    continue
                if href.startswith("/"):
                    href = "https://www.xiaohongshu.com" + href
                if looks_like_note_url(href):
                    return href.split("?")[0]
        # 兜底：从页面 HTML 里正则捞一条
        try:
            match = NOTE_URL_RE.search(page.content() or "")
        except Exception:
            match = None
        if match:
            return f"https://www.xiaohongshu.com/explore/{match.group(1)}"
        return ""

    # -- 数据 ----------------------------------------------------------
    def fetch_metrics(self, post_url: str, *, headless: bool = True) -> MetricSnapshot | None:
        """打开笔记页抓取 点赞/收藏/评论/分享/浏览；失败返回 ``None``。"""
        url = (post_url or "").strip()
        if not url:
            return None
        if not url.lower().startswith("http"):
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
                    dump_debug(page, self._debug_dir(), "metrics-fail")
                return snapshot
        except Exception:
            return None

    def _scrape_metrics(self, page: Any, url: str) -> MetricSnapshot | None:
        """抓指标：①DOM 精确选择器 ②``__INITIAL_STATE__`` 结构化数据 ③整页文本兜底。"""
        data: dict[str, int] = {}
        used: dict[str, str] = {}

        # ① DOM 精确选择器
        for key, selectors in METRIC_VALUE_SELECTORS.items():
            for sel in selectors:
                el = first_visible(page, (sel,))
                if el is None:
                    continue
                try:
                    text = el.inner_text() or el.get_attribute("title") or ""
                except Exception:
                    text = ""
                val = parse_cn_number(text)
                if val:
                    data[key] = val
                    used[key] = f"selector:{sel}"
                    break

        # ② 页面初始状态（来源 upstream types.go InteractInfo；也适配 noteDetailMap）
        state = self._read_initial_interact(page)
        for key, val in state.items():
            if val and not data.get(key):
                data[key] = val
                used[key] = "initial_state"

        # ③ 整页文本兜底
        page_text = ""
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
                used[key] = "page_text"

        if not any(data.values()):
            return None
        return MetricSnapshot(
            views=data.get("views", 0),
            likes=data.get("likes", 0),
            comments=data.get("comments", 0),
            collects=data.get("collects", 0),
            shares=data.get("shares", 0),
            raw={
                "url": url,
                "source": "xiaohongshu-note-page",
                "fields": used,
                "text_hint": page_text[:300],
            },
        )

    def _read_initial_interact(self, page: Any) -> dict[str, int]:
        """从 ``window.__INITIAL_STATE__`` 读互动计数（结构化，比 DOM 稳）。

        来源 upstream ``types.go:69`` ``InteractInfo``：``likedCount`` / ``collectedCount`` /
        ``commentCount`` / ``sharedCount`` 都是「1.2万」形式的字符串；浏览数官方未公开，
        若页面状态里存在 ``viewCount`` / ``viewNum`` 则一并取用。
        """
        try:
            payload = page.evaluate(
                """() => {
                    const st = window.__INITIAL_STATE__;
                    if (!st) return null;
                    const box = st.note || st.feed || st;
                    const map = box.noteDetailMap || box.noteDetail || null;
                    let info = null;
                    if (map) {
                        const first = Object.values(map)[0];
                        info = (first && (first.note || first)) || null;
                    }
                    if (!info) info = box.noteDetail || box.feedDetail || null;
                    if (!info) return null;
                    const ii = info.interactInfo || info.interact_info || info;
                    return JSON.stringify({
                        liked: ii.likedCount, collected: ii.collectedCount,
                        comment: ii.commentCount, shared: ii.sharedCount,
                        view: ii.viewCount || ii.viewNum || ii.readCount,
                    });
                }"""
            )
        except Exception:
            return {}
        if not payload:
            return {}
        try:
            import json
            raw = json.loads(payload)
        except Exception:
            return {}
        mapping = {"likes": "liked", "collects": "collected", "comments": "comment",
                   "shares": "shared", "views": "view"}
        out: dict[str, int] = {}
        for key, field in mapping.items():
            val = parse_cn_number(raw.get(field))
            if val:
                out[key] = val
        return out


# --------------------------------------------------------------------------- #
# 模块内小工具
# --------------------------------------------------------------------------- #

def _query_all_safe(page: Any, selectors: tuple[str, ...]) -> list[Any]:
    """容错 ``query_selector_all``：逐候选聚合，异常跳过。"""
    out: list[Any] = []
    for sel in selectors:
        try:
            out.extend(page.query_selector_all(sel) or [])
        except Exception:
            continue
    return out
