"""知乎平台适配器（zhihu.com，Playwright 同步 API）。

移植来源：
- 回答：Easel ``skills/shared/scripts/zhihu_answer.py``（三策略唤起编辑器 + JS 遍历点发布）
- 文章：Easel ``skills/shared/scripts/web_publisher.py`` 的 ``zhihu`` 平台配置
  （``zhuanlan.zhihu.com/write``，标题 textarea + Draft.js 正文 + 「发布」二次确认抽屉）

已剥离 content_guard / OpenClaw 等依赖，改为自包含实现。

两种模式由 ``PublishPayload.extra`` 指定：
- ``{"mode": "answer", "question_url": "https://www.zhihu.com/question/xxx"}``：回答问题
- ``{"mode": "article"}``（默认）：发布专栏文章
"""

from __future__ import annotations

import re as _re
import time
from pathlib import Path
from typing import Any

from ._browser import (
    clear_and_type,
    click_soft,
    click_text_in,
    collect_content_links,
    dump_debug,
    extract_metric_from_text,
    first_visible,
    first_visible_text,
    open_browser,
    page_title,
    parse_cn_number,
    paste_into_focused,
    type_multiline,
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

HOME_URL = "https://www.zhihu.com/"
SIGNIN_URL = "https://www.zhihu.com/signin"
WRITE_URL = "https://zhuanlan.zhihu.com/write"
CREATOR_URL = "https://www.zhihu.com/creator"

#: 登录判定：已登录时页头出现「个人/消息/创作中心」入口
LOGGED_IN_SELECTORS: tuple[str, ...] = (
    ".AppHeader-profile",
    ".AppHeader-userInfo",
    ".AppHeader-profileAvatar",
    '[class*="AppHeader"] [class*="Avatar"]',
)
LOGGED_OUT_SELECTORS: tuple[str, ...] = (
    ".SignContainer",
    ".Login-content",
    ".SignFlow",
    'button:has-text("登录")',
    'a:has-text("登录")',
    'button:has-text("注册")',
)
#: URL 命中即视为未登录（被重定向到登录页）
LOGGED_OUT_URL_KEYWORDS: tuple[str, ...] = ("/signin", "/signup", "account.unconventional")

#: 「写回答 / 编辑回答」入口
WRITE_ANSWER_BUTTON_SELECTORS: tuple[str, ...] = (
    "button.WriteAnswerButton",
    ".WriteAnswerButton",
    ".QuestionAnswers-answerButton",
    'button:has-text("写回答")',
    'button:has-text("编辑回答")',
    'button:has-text("回答问题")',
)
#: 编辑器出现判据
EDITOR_READY_SELECTORS: tuple[str, ...] = (
    ".public-DraftEditor-content",
    ".DraftEditor-editorContainer [contenteditable=true]",
    "[contenteditable=true][data-contents]",
    ".AnswerForm [contenteditable=true]",
    "[contenteditable=true]",
)
#: 正文编辑器（写回答 / 写文章共用）
EDITOR_SELECTORS: tuple[str, ...] = (
    ".DraftEditor-editorContainer [contenteditable=true]",
    ".public-DraftEditor-content",
    "[contenteditable=true][data-contents]",
    ".AnswerForm [contenteditable=true]",
    "#root [contenteditable=true]",
)

#: 文章标题
TITLE_SELECTORS: tuple[str, ...] = (
    ".WriteIndex-titleInput textarea",
    ".WriteIndex-titleInput input",
    "textarea[placeholder*='标题']",
    "input[placeholder*='标题']",
    ".Editable-title textarea",
)

#: 发布入口与二次确认（知乎点「发布」会弹「发布设置」抽屉，抽屉里再点一次「发布」）
PUBLISH_ENTRY_SELECTORS: tuple[str, ...] = (
    'button:has-text("发布")',
    ".PublishPanel-triggerButton",
    ".WriteIndex-publish",
)
PUBLISH_MODAL_SELECTORS: tuple[str, ...] = (
    "[role='dialog']",
    ".Modal-content",
    "[class*='PublishPanel']",
    "[class*='Modal']",
)
PUBLISH_TEXTS: tuple[str, ...] = ("发布", "发布文章", "确认发布")
#: 文章话题标签输入（可选，失败不影响发布）
TOPIC_INPUT_SELECTORS: tuple[str, ...] = (
    ".Popover-content input",
    ".PublishPanel input[placeholder*='话题']",
    "input[placeholder*='话题']",
    "input[placeholder*='添加话题']",
)

#: 结果信号
SUCCESS_URL_KEYWORDS: tuple[str, ...] = ("/p/", "/answer/")
FAIL_TEXTS: tuple[str, ...] = ("失败", "错误", "不能", "请先", "请选择", "违规", "无法")

#: 指标抓取
METRIC_LABELS: dict[str, tuple[str, ...]] = {
    "views": ("阅读", "浏览", "播放"),
    "likes": ("赞同", "点赞"),
    "comments": ("评论",),
    "collects": ("收藏",),
    "shares": ("分享", "转发"),
}
METRIC_VALUE_SELECTORS: dict[str, tuple[str, ...]] = {
    "likes": (
        "button.VoteButton--up",
        ".VoteButton--up",
        'button[aria-label*="赞同"]',
        '[class*="VoteButton"]',
    ),
    "comments": ('button[aria-label*="评论"]', '[class*="CommentsCount"]', 'a[href*="comment"]'),
    "collects": ('button[aria-label*="收藏"]', '[class*="CollectButton"]'),
    "shares": ('button[aria-label*="分享"]', '[class*="ShareButton"]'),
    "views": ('[class*="ViewCount"]', '[class*="ReadCount"]', '[class*="ContentItem-status"]'),
}
METRIC_EXCLUDE_SELECTORS: tuple[str, ...] = ("header", "nav", ".AppHeader", ".QuestionHeader", ".CornerButtons")

DEBUG_DIR_NAME = "debug"


# --------------------------------------------------------------------------- #
# 纯工具函数
# --------------------------------------------------------------------------- #

def normalize_question_url(url: str) -> str:
    """把回答链接 / 问题链接归一化成问题页 URL；无法识别时原样返回。

    ``https://www.zhihu.com/question/123/answer/456`` → ``https://www.zhihu.com/question/123``
    """
    s = (url or "").strip()
    if not s or "zhihu.com" not in s:
        return s
    marker = "/question/"
    idx = s.find(marker)
    if idx < 0:
        return s
    rest = s[idx + len(marker):]
    digits = ""
    for ch in rest:
        if ch.isdigit():
            digits += ch
        else:
            break
    if not digits:
        return s
    return f"https://www.zhihu.com/question/{digits}"


def looks_like_article_url(url: str) -> bool:
    """是否为知乎文章（含草稿）链接。"""
    low = (url or "").lower()
    return "zhuanlan.zhihu.com/p/" in low or "/p/" in low


#: 作品链接规则（账号导入时用它从创作者中心筛出自己的文章与回答）
_WORK_URL_RE = _re.compile(
    r"(?:zhuanlan\.zhihu\.com/p/\d+|zhihu\.com/question/\d+/answer/\d+)"
)


# --------------------------------------------------------------------------- #
# 适配器
# --------------------------------------------------------------------------- #

class ZhiHuAdapter(PlatformAdapter):
    """知乎适配器（回答 / 文章两种模式）。"""

    key = "zhihu"
    label = "知乎"
    home_url = HOME_URL
    publish_url = WRITE_URL
    needs_media = False
    supports_auto_publish = True
    #: 支持从创作者中心导入（扫描自己的文章与回答链接）
    supports_account_import = True
    metrics_hint = "知乎单篇数据取自内容页可见计数（赞同/评论/收藏/分享）"

    @property
    def limits(self) -> PlatformLimits:
        return PlatformLimits(
            title_max=100,        # 文章标题（回答模式无标题）
            body_max=20000,
            body_min=1,
            tags_max=5,
            requires_media=False,
            media_max=0,
            media_kinds=(),
            supports_video=False,
            notes="回答模式用 extra.question_url；文章模式为默认，正文最多 20000 字",
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
                    return False, f"打开知乎失败：{type(exc).__name__}（请检查网络）"
                state = self._probe_login(page, wait_s=12)
                if state is LoginState.LOGGED_IN:
                    return True, "知乎已登录"
                if state is LoginState.LOGGED_OUT:
                    return False, "知乎未登录，请先扫码登录"
                return False, "无法确认知乎登录态（页面可能改版或加载超时）"
        except Exception as exc:  # noqa: BLE001
            return False, f"检测知乎登录态异常：{type(exc).__name__}: {exc}"

    def _probe_login(self, page: Any, *, wait_s: int = 12) -> LoginState:
        deadline = time.time() + max(1, wait_s)
        while time.time() < deadline:
            try:
                url = (page.url or "").lower()
                if any(k in url for k in LOGGED_OUT_URL_KEYWORDS):
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
        """打开有头浏览器引导扫码：知乎登录页二维码直接显示在窗口里。"""
        try:
            with open_browser(self, headless=False) as sess:
                if sess is None:
                    return False, "浏览器不可用：请先安装 playwright 与 chromium"
                page = sess.page
                try:
                    page.goto(SIGNIN_URL, wait_until="domcontentloaded", timeout=45_000)
                except Exception as exc:
                    return False, f"打不开知乎登录页：{type(exc).__name__}（请检查网络/代理）"
                page.wait_for_timeout(1_500)
                # 顺手把二维码区域截图落盘，便于 UI 展示（截不到不影响登录）
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
                        return True, "知乎登录成功，登录态已保存到本地"
                    if time.time() - shot_at > 15:
                        self._try_shot_qr(page)
                        shot_at = time.time()
                return False, (
                    f"{timeout_sec}s 内未完成知乎登录。请在浏览器窗口用知乎 App 扫码"
                    f"（二维码截图：{self._qr_png()}）"
                )
        except Exception as exc:  # noqa: BLE001
            return False, f"知乎登录异常：{type(exc).__name__}: {exc}"

    def _try_shot_qr(self, page: Any) -> None:
        """把登录二维码区域截成 PNG（best-effort，失败静默）。"""
        selectors = (
            ".Qrcode-img",
            ".SignFlow-qrcode",
            'img[alt*="二维码"]',
            "[class*='Qrcode'] img",
            "[class*='qrcode'] img",
            "[class*='Qrcode'] canvas",
        )
        el = first_visible(page, selectors)
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
        mode = str(extra.get("mode") or "article").strip().lower()
        if mode not in ("answer", "article"):
            return PublishResult(ok=False, message=f"不支持的知乎发布模式：{mode}（可选 answer / article）")

        # 1) 入参校验
        title = (payload.title or "").strip()
        body = (payload.body or "").strip()
        tags = [str(t).strip() for t in (payload.tags or []) if str(t).strip()]
        lim = self.limits
        if not body:
            return PublishResult(ok=False, message="知乎正文不能为空")
        if len(body) > lim.body_max:
            return PublishResult(ok=False, message=f"正文过长（{len(body)}/{lim.body_max} 字），请精简后再发布")
        if len(tags) > lim.tags_max:
            return PublishResult(ok=False, message=f"话题过多（{len(tags)}/{lim.tags_max}），请删减后再发布")
        question_url = ""
        if mode == "answer":
            raw_url = str(extra.get("question_url") or "").strip()
            question_url = normalize_question_url(raw_url)
            if not question_url or "zhihu.com" not in question_url:
                # 只接受知乎域名，避免把任意 URL 交给浏览器打开
                return PublishResult(ok=False, message="问题链接必须指向 zhihu.com，例如 https://www.zhihu.com/question/123")
            if "/question/" not in question_url:
                return PublishResult(ok=False, message="回答模式需要在 extra 里给出问题链接，例如 {\"mode\":\"answer\",\"question_url\":\"https://www.zhihu.com/question/123\"}")
        else:
            if not title:
                return PublishResult(ok=False, message="知乎文章标题不能为空")
            if len(title) > lim.title_max:
                return PublishResult(ok=False, message=f"文章标题过长（{len(title)}/{lim.title_max} 字），请精简后再发布")

        try:
            with open_browser(self, headless=headless) as sess:
                if sess is None:
                    return PublishResult(ok=False, message="浏览器不可用：请先安装 playwright 与 chromium")
                try:
                    if mode == "answer":
                        return self._publish_answer(
                            sess.page, question_url=question_url, body=body, tags=tags,
                            dry_run=dry_run, timeout_sec=timeout_sec, on_progress=on_progress,
                        )
                    return self._publish_article(
                        sess.page, title=title, body=body, tags=tags,
                        dry_run=dry_run, timeout_sec=timeout_sec, on_progress=on_progress,
                    )
                except Exception as exc:  # noqa: BLE001
                    where = dump_debug(sess.page, self._debug_dir(), "zhihu", "publish-error")
                    note = f"，现场已落盘：{where}" if where else ""
                    return PublishResult(
                        ok=False,
                        message=f"知乎发布失败：{type(exc).__name__}: {exc}{note}",
                    )
        except Exception as exc:  # noqa: BLE001
            return PublishResult(ok=False, message=f"知乎发布异常：{type(exc).__name__}: {exc}")

    # ---- 回答模式 ----------------------------------------------------
    def _publish_answer(
        self, page: Any, *, question_url: str, body: str, tags: list[str],
        dry_run: bool, timeout_sec: int, on_progress: Any,
    ) -> PublishResult:
        budget = max(60, timeout_sec)
        deadline = time.time() + budget

        self._log(on_progress, "打开知乎问题页…", 5)
        try:
            page.goto(question_url, wait_until="domcontentloaded", timeout=45_000)
        except Exception as exc:
            return PublishResult(ok=False, message=f"打开问题页失败：{type(exc).__name__}（请检查网络/链接）")
        page.wait_for_timeout(2_000)

        state = self._probe_login(page, wait_s=8)
        if state is LoginState.LOGGED_OUT:
            return PublishResult(ok=False, message="知乎未登录，请先在「账号」页扫码登录后再发布")

        question_title = self._question_title(page)
        content = body
        if tags:
            content = (content + "\n\n" + " ".join("#" + t.lstrip("#") for t in tags)).strip()

        self._log(on_progress, "唤起回答编辑器…", 20)
        if not self._open_answer_editor(page, deadline=deadline):
            where = dump_debug(page, self._debug_dir(), "zhihu", "answer-editor-fail")
            return PublishResult(
                ok=False,
                message="未能唤起知乎回答编辑器（可能已改版或需要先登录）" + (f"，现场：{where}" if where else ""),
            )

        self._log(on_progress, "写入回答内容…", 45)
        if not self._fill_editor(page, content):
            where = dump_debug(page, self._debug_dir(), "zhihu", "answer-type-fail")
            return PublishResult(
                ok=False,
                message="未找到回答正文编辑器，无法写入内容" + (f"，现场：{where}" if where else ""),
            )

        if dry_run:
            self._log(on_progress, "已填写完成，等待人工确认（未点击发布）", 90)
            return PublishResult(
                ok=True,
                url=question_url,
                message="已填写完成，等待人工确认",
                detail={"dry_run": True, "mode": "answer", "question": question_title,
                        "hint": "确认正文无误后，在浏览器窗口内手动点击「发布回答」"},
            )

        self._log(on_progress, "提交回答…", 80)
        if not self._click_publish(page, mode="answer"):
            where = dump_debug(page, self._debug_dir(), "zhihu", "answer-publish-btn-fail")
            return PublishResult(
                ok=False,
                message="未找到「发布回答」按钮，未提交" + (f"，现场：{where}" if where else ""),
            )

        ok, message, url = self._wait_result(page, mode="answer", timeout_s=45)
        if ok:
            self._log(on_progress, "发布完成", 100)
            return PublishResult(ok=True, url=url, message=message)
        where = dump_debug(page, self._debug_dir(), "zhihu", "answer-unconfirmed")
        return PublishResult(ok=False, url=url, message=message + (f"，现场已落盘：{where}" if where else ""),
                             detail={"unconfirmed": True})

    def _question_title(self, page: Any) -> str:
        el = first_visible(page, ("h1.QuestionHeader-title", ".QuestionHeader-title", "h1"))
        if el is None:
            return ""
        try:
            return (el.inner_text() or "").strip()[:60]
        except Exception:
            return ""

    def _open_answer_editor(self, page: Any, *, deadline: float) -> bool:
        """三策略唤起「写回答」编辑器：诊断已存在编辑器 → dispatch 事件 → 事件序列 → focus+Enter。"""
        if first_visible(page, EDITOR_READY_SELECTORS) is not None:
            return True

        button = None
        for sel in WRITE_ANSWER_BUTTON_SELECTORS:
            try:
                for el in page.query_selector_all(sel):
                    try:
                        if el.is_visible():
                            button = el
                            break
                    except Exception:
                        continue
            except Exception:
                continue
            if button is not None:
                break
        if button is None:
            button = first_visible_text(page, ("写回答", "编辑回答", "回答问题"))
        if button is None:
            return False

        # 策略 1：原生/force 点击
        click_soft(button)
        if self._editor_appeared(page, timeout_s=6):
            return True
        # 策略 2：完整指针事件序列
        try:
            button.evaluate(
                "el => ['mousedown','mouseup','click']"
                ".forEach(t => el.dispatchEvent(new MouseEvent(t,{bubbles:true,cancelable:true,view:true})))"
            )
        except Exception:
            pass
        if self._editor_appeared(page, timeout_s=6):
            return True
        # 策略 3：focus + Enter（最可靠，绕过头部覆盖层）
        try:
            button.focus()
            page.wait_for_timeout(300)
            page.keyboard.press("Enter")
        except Exception:
            pass
        return self._editor_appeared(page, timeout_s=max(6, int(deadline - time.time())))

    def _editor_appeared(self, page: Any, *, timeout_s: int = 6) -> bool:
        deadline = time.time() + max(1, timeout_s)
        while time.time() < deadline:
            if first_visible(page, EDITOR_READY_SELECTORS) is not None:
                return True
            try:
                page.wait_for_timeout(500)
            except Exception:
                return False
        return False

    def _fill_editor(self, page: Any, content: str) -> bool:
        """激活正文编辑器 → 清空草稿 → 写入内容（剪贴板优先，退化到逐段键入）。"""
        el = first_visible(page, EDITOR_SELECTORS)
        if el is None:
            return False
        try:
            el.click()
            page.wait_for_timeout(400)
        except Exception:
            pass
        try:
            page.keyboard.press("Control+a")
            page.wait_for_timeout(200)
            page.keyboard.press("Backspace")
            page.wait_for_timeout(400)
        except Exception:
            pass
        if paste_into_focused(page, content):
            page.wait_for_timeout(800)
            try:
                got = (el.inner_text() or "").strip()
                if len(got) >= min(10, len(content)):
                    return True
            except Exception:
                return True
        try:
            page.keyboard.press("Control+a")
            page.keyboard.press("Backspace")
        except Exception:
            pass
        type_multiline(page, content, delay_ms=6)
        page.wait_for_timeout(800)
        return True

    # ---- 文章模式 ----------------------------------------------------
    def _publish_article(
        self, page: Any, *, title: str, body: str, tags: list[str],
        dry_run: bool, timeout_sec: int, on_progress: Any,
    ) -> PublishResult:
        budget = max(60, timeout_sec)
        deadline = time.time() + budget

        self._log(on_progress, "打开知乎写文章页…", 5)
        try:
            page.goto(WRITE_URL, wait_until="domcontentloaded", timeout=45_000)
        except Exception as exc:
            return PublishResult(ok=False, message=f"打开知乎写文章页失败：{type(exc).__name__}（请检查网络）")
        page.wait_for_timeout(2_500)

        state = self._probe_login(page, wait_s=8)
        if state is LoginState.LOGGED_OUT:
            return PublishResult(ok=False, message="知乎未登录，请先在「账号」页扫码登录后再发布")

        self._log(on_progress, "填写文章标题…", 20)
        title_el = first_visible(page, TITLE_SELECTORS)
        if title_el is None:
            where = dump_debug(page, self._debug_dir(), "zhihu", "article-title-fail")
            return PublishResult(ok=False, message="未找到文章标题输入框（知乎可能改版）"
                                                + (f"，现场：{where}" if where else ""))
        if not clear_and_type(page, title_el, title, delay_ms=8):
            try:
                title_el.fill(title)
            except Exception:
                where = dump_debug(page, self._debug_dir(), "zhihu", "article-title-type-fail")
                return PublishResult(ok=False, message="文章标题写入失败" + (f"，现场：{where}" if where else ""))

        self._log(on_progress, "写入文章正文…", 45)
        if not self._fill_editor(page, body):
            where = dump_debug(page, self._debug_dir(), "zhihu", "article-body-fail")
            return PublishResult(ok=False, message="未找到文章正文编辑器，无法写入内容"
                                                + (f"，现场：{where}" if where else ""))
        page.wait_for_timeout(600)

        self._log(on_progress, "填写话题标签…", 60)
        self._fill_topics(page, tags)

        if dry_run:
            self._log(on_progress, "已填写完成，等待人工确认（未点击发布）", 90)
            return PublishResult(
                ok=True,
                url=page.url or WRITE_URL,
                message="已填写完成，等待人工确认",
                detail={"dry_run": True, "mode": "article", "tags": tags,
                        "hint": "确认标题/正文/话题无误后，在浏览器窗口内手动点击「发布」并在弹窗中确认"},
            )

        self._log(on_progress, "打开发布设置…", 75)
        if not self._click_publish(page, mode="article"):
            where = dump_debug(page, self._debug_dir(), "zhihu", "article-publish-btn-fail")
            return PublishResult(ok=False, message="未找到「发布」按钮，未提交"
                                                + (f"，现场：{where}" if where else ""))

        self._log(on_progress, "等待平台回执…", 90)
        ok, message, url = self._wait_result(page, mode="article", timeout_s=60)
        if ok:
            self._log(on_progress, "发布完成", 100)
            return PublishResult(ok=True, url=url, message=message)
        where = dump_debug(page, self._debug_dir(), "zhihu", "article-unconfirmed")
        return PublishResult(ok=False, url=url, message=message + (f"，现场已落盘：{where}" if where else ""),
                             detail={"unconfirmed": True})

    def _fill_topics(self, page: Any, tags: list[str]) -> None:
        """best-effort 填话题标签（失败不影响发布）。"""
        if not tags:
            return
        for tag in tags[:5]:
            el = first_visible(page, TOPIC_INPUT_SELECTORS)
            if el is None:
                return
            try:
                el.click()
                el.fill(tag)
                page.wait_for_timeout(900)
                page.keyboard.press("Enter")
                page.wait_for_timeout(500)
            except Exception:
                return

    def _click_publish(self, page: Any, *, mode: str) -> bool:
        """点发布：文章模式要处理「发布设置」抽屉里的二次确认。"""
        if mode == "answer":
            return (click_text_in(page, page, "发布回答")
                    or click_text_in(page, page, "提交修改")
                    or click_text_in(page, page, "发布"))

        clicked = False
        for sel in PUBLISH_ENTRY_SELECTORS:
            try:
                for el in page.query_selector_all(sel):
                    try:
                        text = (el.inner_text() or "").strip()
                        if text and text != "发布设置" and el.is_visible():
                            if click_soft(el):
                                clicked = True
                                break
                    except Exception:
                        continue
            except Exception:
                continue
            if clicked:
                break
        if not clicked:
            clicked = click_text_in(page, page, "发布")
        if not clicked:
            return False
        # 抽屉/弹窗里的最终确认按钮
        deadline = time.time() + 12
        while time.time() < deadline:
            modal = first_visible(page, PUBLISH_MODAL_SELECTORS)
            if modal is not None:
                for text in PUBLISH_TEXTS:
                    if click_text_in(page, modal, text):
                        page.wait_for_timeout(1_000)
                        return True
            page.wait_for_timeout(800)
        # 无弹窗形态：直接提交（老版写文章页）
        return True

    def _wait_result(self, page: Any, *, mode: str, timeout_s: int = 60) -> tuple[bool, str, str]:
        """判发布结果：URL 跳到文章页/回答页且离开编辑页 = 成功；出现失败文案 = 失败；
        超时 = 未确认（绝不误报成功）。"""
        start_url = page.url or ""
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            try:
                url = page.url or ""
                if url != start_url and any(k in url for k in SUCCESS_URL_KEYWORDS):
                    if "/edit" not in url:
                        return True, f"知乎发布成功（{url[:80]}）", url
                if mode == "article" and "/p/" in url and "/edit" not in url:
                    return True, f"知乎文章已发布（{url[:80]}）", url
                body_text = ""
                try:
                    body_text = page.inner_text("body")[:3000]
                except Exception:
                    body_text = ""
                for kw in FAIL_TEXTS:
                    if kw in body_text and "发布失败" in body_text:
                        return False, f"知乎发布失败：页面提示包含「{kw}」", url
            except Exception:
                pass
            page.wait_for_timeout(1_000)
        return False, "知乎发布结果未确认（未跳转到文章/回答页），请到「创作中心 → 内容管理」人工核对是否已发布", page.url or ""

    # -- 数据 ----------------------------------------------------------
    def list_account_posts(
        self, *, headless: bool = True, limit: int = 50
    ) -> list[AccountPost]:
        """从「创作者中心」扫描账号下的文章 / 回答链接（需要已登录）。"""
        out: list[AccountPost] = []
        try:
            with open_browser(self, headless=headless) as sess:
                if sess is None:
                    return []
                page = sess.page
                try:
                    page.goto(CREATOR_URL, wait_until="domcontentloaded", timeout=45_000)
                except Exception:
                    return []
                page.wait_for_timeout(3_000)
                links = collect_content_links(
                    page,
                    _WORK_URL_RE,
                    base_url="https://www.zhihu.com",
                    scrolls=6,
                    limit=limit,
                )
                for link in links:
                    out.append(AccountPost(url=link, platform_id=link.rsplit("/", 1)[-1]))
        except Exception:
            return out
        return out

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
                    dump_debug(page, self._debug_dir(), "zhihu", "metrics-fail")
                return snapshot
        except Exception:
            return None

    def _scrape_metrics(self, page: Any, url: str) -> MetricSnapshot | None:
        """抓内容页可见指标：赞同 / 评论 / 收藏 / 分享（阅读量多数形态不公开，取不到记 0）。"""
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
                    text = (el.inner_text() or "") + " " + (el.get_attribute("aria-label") or "")
                except Exception:
                    text = ""
                val = parse_cn_number(text)
                if val:
                    data[key] = val
                    break
        # 文本兜底：只在正文之外的主区块内查找，避免把正文里的「赞同 3 个理由」当成指标
        content_text = ""
        for sel in (".QuestionAnswer", ".AnswerCard", ".Post-Main", "main", "article"):
            try:
                el = page.query_selector(sel)
            except Exception:
                el = None
            if el is not None:
                try:
                    content_text = el.inner_text() or ""
                    break
                except Exception:
                    continue
        if not content_text:
            try:
                content_text = page.inner_text("body") or ""
            except Exception:
                content_text = ""
        for key, labels in METRIC_LABELS.items():
            if data.get(key):
                continue
            val = extract_metric_from_text(content_text, labels)
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
            title=page_title(page, strip_suffixes=(" - 知乎", " | 知乎")),
            # 知乎多数形态不公开阅读量，UI 上显示「—」更诚实
            views_public=bool(data.get("views")),
            raw={"url": url, "source": "zhihu-content-page", "text_hint": content_text[:400]},
        )
