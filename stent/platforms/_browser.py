"""平台适配器共用的浏览器与 DOM 工具（内部模块，不属于平台实现）。

三个平台适配器（抖音 / 知乎 / B 站）都需要同一套东西：
持久化上下文的启动与关闭、多候选选择器定位、富文本输入、中文计数解析、
失败现场落盘。放在这里避免三份复制粘贴——平台特有的选择器与流程仍留在各自模块。

本模块只依赖标准库与 base；playwright 在函数内局部导入，方便离线（未装 playwright）
时也能被导入以复用纯函数（如 ``parse_cn_number``）。
"""

from __future__ import annotations

import re
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

#: 通用 Chromium 启动参数（不含 --disable-dev-shm-usage：Windows 下重页面易崩）
COMMON_LAUNCH_ARGS: tuple[str, ...] = (
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

#: 判定「未登录」的通用信号（各平台自行追加）
GENERIC_LOGGED_OUT_KEYWORDS: tuple[str, ...] = ("扫码登录", "密码登录", "验证码登录", "立即登录", "登录后")

_NUM_TOKEN_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?\s*[万亿wWkK]?)")


# --------------------------------------------------------------------------- #
# 纯函数
# --------------------------------------------------------------------------- #

def parse_cn_number(text: Any) -> int:
    """把中文计数的展示值解析成整数。

    支持 ``"1.2万"`` → 12000、``"3.4亿"`` → 340000000、``"1,234"`` → 1234、
    ``"1.2w"`` → 12000、``"2500"`` → 2500；无法解析（``"-"``、``"暂无"``、空）
    时返回 0，绝不抛异常。
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


# --------------------------------------------------------------------------- #
# 浏览器会话
# --------------------------------------------------------------------------- #

class BrowserSession:
    """包装 ``launch_persistent_context``，提供 ``page`` 与幂等的 ``close()``。"""

    def __init__(self, playwright: Any, user_data_dir: str, *, headless: bool,
                 timeout_ms: int = 30_000, args: tuple[str, ...] = COMMON_LAUNCH_ARGS):
        self._ctx = self._launch(playwright, user_data_dir, headless=headless,
                                 timeout_ms=timeout_ms, args=args)
        pages = list(getattr(self._ctx, "pages", []) or [])
        self.page = pages[0] if pages else self._ctx.new_page()
        try:
            self.page.set_default_timeout(30_000)
            self.page.set_default_navigation_timeout(45_000)
        except Exception:
            pass

    @staticmethod
    def _launch(playwright: Any, user_data_dir: str, *, headless: bool,
                timeout_ms: int, args: tuple[str, ...]):
        """启动持久化上下文；先试禁用系统代理（国内平台直连更稳），失败退回默认。"""
        common: dict[str, Any] = dict(
            headless=headless,
            locale="zh-CN",
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
def open_browser(adapter: Any, *, headless: bool,
                 args: tuple[str, ...] = COMMON_LAUNCH_ARGS) -> Iterator[BrowserSession | None]:
    """打开浏览器会话；playwright 缺失 / 内核缺失时产出 ``None``（调用方给中文提示）。"""
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        yield None
        return
    with sync_playwright() as pw:
        session: BrowserSession | None = None
        try:
            session = BrowserSession(pw, adapter.profile_dir(), headless=headless, args=args)
        except Exception:
            yield None
            return
        try:
            yield session
        finally:
            session.close()


# --------------------------------------------------------------------------- #
# DOM 小工具
# --------------------------------------------------------------------------- #

def first_visible(page: Any, selectors: tuple[str, ...], *, require_enabled: bool = False) -> Any:
    """按候选顺序返回第一个可见元素（可选要求未禁用）；都没有返回 None。"""
    for sel in selectors:
        try:
            candidates = page.query_selector_all(sel)
        except Exception:
            continue
        for el in candidates:
            try:
                if not el.is_visible():
                    continue
                if require_enabled:
                    if el.get_attribute("disabled") is not None:
                        continue
                    if el.get_attribute("aria-disabled") in ("true", "1"):
                        continue
                return el
            except Exception:
                continue
    return None


def first_visible_text(page: Any, texts: tuple[str, ...]) -> Any:
    """按候选顺序返回第一个可见的「文本节点」元素（按钮/标签类目标）。"""
    for text in texts:
        try:
            for el in page.query_selector_all(f"text={text}"):
                try:
                    if el.is_visible():
                        return el
                except Exception:
                    continue
        except Exception:
            continue
    return None


def click_soft(el: Any) -> bool:
    """尽最大努力点击：原生 click → 滚动后 force click → JS 指针事件链。"""
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


def click_text_in(page: Any, scope: Any, text: str) -> bool:
    """在 scope 内按文本点击（先 button/a，再 text= 节点，最后 JS 遍历）。"""
    if scope is None:
        scope = page
    for sel in (f"button:has-text('{text}')", f"a:has-text('{text}')", f"[role=button]:has-text('{text}')"):
        try:
            for el in scope.query_selector_all(sel):
                try:
                    if el.is_visible() and click_soft(el):
                        return True
                except Exception:
                    continue
        except Exception:
            continue
    try:
        for el in scope.query_selector_all(f"text={text}"):
            try:
                if el.is_visible() and click_soft(el):
                    return True
            except Exception:
                continue
    except Exception:
        pass
    try:
        scope.evaluate(
            """(kw) => {
                const nodes = Array.from(document.querySelectorAll('button,a,[role=button],span,div'));
                const hit = nodes.find(n => (n.innerText || '').trim().includes(kw) && n.offsetParent !== null);
                if (hit) hit.click();
            }""",
            text,
        )
        return True
    except Exception:
        return False


def clear_and_type(page: Any, el: Any, text: str, *, delay_ms: int = 15) -> bool:
    """聚焦 → Ctrl+A 清空 → 逐字输入（富文本编辑器也能接收）。"""
    if el is None or not text:
        return False
    try:
        el.click()
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


def paste_into_focused(page: Any, text: str) -> bool:
    """用 ClipboardEvent 把长文粘进当前聚焦的富文本编辑器（Draft.js/slate 通用）。"""
    try:
        return bool(page.evaluate(
            """(text) => {
                const el = document.activeElement;
                if (!el) return false;
                try {
                    const dt = new DataTransfer();
                    dt.setData('text/plain', text);
                    el.dispatchEvent(new ClipboardEvent('paste', {clipboardData: dt, bubbles: true}));
                    return true;
                } catch (e) { return false; }
            }""",
            text,
        ))
    except Exception:
        return False


def type_multiline(page: Any, text: str, *, delay_ms: int = 8) -> None:
    """按段落逐字输入（保留换行），适合富文本编辑器。"""
    paragraphs = str(text).split("\n")
    for i, para in enumerate(paragraphs):
        if para.strip():
            for ch in para:
                try:
                    page.keyboard.type(ch)
                except Exception:
                    return
                if delay_ms:
                    page.wait_for_timeout(delay_ms)
        if i < len(paragraphs) - 1:
            try:
                page.keyboard.press("Enter")
            except Exception:
                return


def visible(page: Any, selector: str) -> Any:
    """返回选择器命中的第一个可见元素（单选择器快捷版）。"""
    try:
        for el in page.query_selector_all(selector):
            try:
                if el.is_visible():
                    return el
            except Exception:
                continue
    except Exception:
        pass
    return None


def page_title(page: Any, *, strip_suffixes: tuple[str, ...] = ()) -> str:
    """取当前页面代表的作品标题。

    粘贴链接登记的作品一开始只有 URL，标题得从页面上补回来，
    否则内容排行里显示的会是一串地址。优先级：``og:title`` → ``h1`` → ``<title>``。
    """
    for selector, attr in (('meta[property="og:title"]', "content"), ("h1", "")):
        try:
            el = page.query_selector(selector)
            if el is None:
                continue
            text = (el.get_attribute(attr) if attr else el.inner_text()) or ""
            text = " ".join(text.split())
            if text:
                return text[:200]
        except Exception:
            continue
    try:
        raw = " ".join((page.title() or "").split())
    except Exception:
        return ""
    for suffix in strip_suffixes:
        if raw.endswith(suffix):
            raw = raw[: -len(suffix)].strip()
    return raw[:200]


def collect_content_links(
    page: Any,
    pattern: Any,
    *,
    base_url: str = "",
    scrolls: int = 4,
    wait_ms: int = 1_200,
    limit: int = 100,
) -> list[str]:
    """从当前页面里收集匹配 ``pattern`` 的作品链接。

    创作中心 / 个人主页的 DOM 结构各平台不同且经常改版，按元素选择器抓很容易失效；
    但「页面里存在指向自己作品的链接」这件事是稳定的，因此这里退一步：
    扫描页面上所有 ``href``，用各平台自己的作品 URL 规则筛出来。

    为触发懒加载，会先滚动若干次再收集。
    """
    import re as _re

    if isinstance(pattern, str):
        pattern = _re.compile(pattern)
    found: list[str] = []
    seen: set[str] = set()

    def harvest() -> None:
        try:
            hrefs = page.eval_on_selector_all(
                "a[href]", "els => els.map(e => e.href || e.getAttribute('href') || '')"
            )
        except Exception:
            hrefs = []
        for href in hrefs or []:
            text = str(href or "").strip()
            if not text:
                continue
            if text.startswith("/") and base_url:
                text = base_url.rstrip("/") + text
            if not text.lower().startswith("http"):
                continue
            if not pattern.search(text):
                continue
            # 去掉 query / fragment，避免同一篇作品因参数不同被当成两条
            clean = text.split("#", 1)[0].split("?", 1)[0]
            if clean in seen:
                continue
            seen.add(clean)
            found.append(clean)

    harvest()
    for _ in range(max(0, scrolls)):
        if len(found) >= limit:
            break
        try:
            page.mouse.wheel(0, 2400)
            page.wait_for_timeout(wait_ms)
        except Exception:
            break
        harvest()
    return found[:limit]


def dump_debug(page: Any, directory: str, prefix: str, tag: str) -> str:
    """把当前页面 DOM + 截图落盘，供选择器校准；返回目录路径（失败返回空串）。"""
    try:
        target = Path(directory)
        target.mkdir(parents=True, exist_ok=True)
        try:
            (target / f"{prefix}-{tag}.html").write_text(page.content(), encoding="utf-8")
        except Exception:
            pass
        try:
            page.screenshot(path=str(target / f"{prefix}-{tag}.png"))
        except Exception:
            pass
        return str(target)
    except Exception:
        return ""


def wait_until(predicate: Any, *, timeout_s: float, interval_s: float = 0.8) -> bool:
    """轮询等待条件成立；超时返回 False。异常按「不成立」处理。"""
    deadline = time.time() + max(0.1, timeout_s)
    while time.time() < deadline:
        try:
            if predicate():
                return True
        except Exception:
            pass
        time.sleep(interval_s)
    return False
