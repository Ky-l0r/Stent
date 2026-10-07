"""抖音作品信息轻量抓取（纯标准库，不依赖 Playwright）。

**为什么需要它**：粘贴抖音链接登记作品时，标题只能靠抓取回填。原先一律走
Playwright 打开作品页，但桌面端页面经常把标题渲染成「抖音创作者中心」，
结果内容排行里几十条作品的标题全都一样，等于没有标题。

**方案**（2026-10 实测）：

1. 移动端分享页 ``https://www.iesdouyin.com/share/video/{id}/`` 配合 ``ttwid``
   cookie，可从 ``window._ROUTER_DATA`` 的 ``videoInfoRes.item_list[0]``
   拿到 ``desc`` 与 ``statistics``；
2. 网页详情接口 ``https://www.douyin.com/aweme/v1/web/aweme/detail/`` 直接返回
   ``aweme_detail`` JSON，作为第一通道（更稳、更快）；
3. 必须用**移动端 UA**，桌面 UA 会拿到反爬挑战页；
4. 不带 ``ttwid`` 时分享页只返回一张空壳挑战页，因此要先去
   ``ttwid.bytedance.com`` 注册一个并缓存 6 小时；
5. 需要限速：间隔 < 0.5 秒连续请求约 4 次即被风控，这里统一节流到 1.2 秒；
6. ``play_count`` 官方恒为 0（抖音不对外公开播放量），因此 ``views`` 通常是 0。

任何异常都被吞掉并返回 ``None``，调用方据此回退到浏览器方案。
"""

from __future__ import annotations

import json
import logging
import random
import re
import ssl
import string
import threading
import time
import urllib.error
import urllib.request
from typing import Any

log = logging.getLogger(__name__)

#: 移动端 UA：桌面 UA 会拿到反爬挑战页，拿不到任何数据
MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1"
)

_TTWID_URL = "https://ttwid.bytedance.com/ttwid/union/register/"
_SHARE_URL = "https://www.iesdouyin.com/share/video/{vid}/"
_DETAIL_URL = (
    "https://www.douyin.com/aweme/v1/web/aweme/detail/"
    "?device_platform=webapp&aid=6383&channel=channel_pc_web"
    "&pc_client_type=1&version_code=170400&version_name=17.4.0"
    "&cookie_enabled=true&screen_width=1920&screen_height=1080"
    "&browser_language=zh-CN&browser_platform=Win32&browser_name=Chrome"
    "&browser_version=124.0.0.0&browser_online=true&engine_name=Blink"
    "&engine_version=124.0.0.0&os_name=Windows&os_version=10"
    "&cpu_core_num=16&device_memory=8&platform=PC&downlink=10"
    "&effective_type=4g&round_trip_time=50&aweme_id={vid}&msToken={ms}"
)

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

#: 从各种链接写法里抠作品 id
_ID_PATTERNS = (
    r"/share/video/(\d{15,25})",
    r"/video/(\d{15,25})",
    r"/note/(\d{15,25})",
    r"modal_id=(\d{15,25})",
    r"item_ids=(\d{15,25})",
    r"aweme_id=(\d{15,25})",
)
_SHORT_LINK_RE = re.compile(r"https?://v\.douyin\.com/[A-Za-z0-9_\-]+", re.I)
_ID_RE = re.compile(r"\d{15,25}")

#: 这些「标题」其实不是作品标题，必须当成无效值丢掉
INVALID_TITLES: frozenset[str] = frozenset(
    {
        "",
        "抖音",
        "douyin",
        "抖音短视频",
        "抖音创作者中心",
        "创作者中心",
        "记录美好生活",
        "抖音-记录美好生活",
    }
)

_TTWID_TTL = 6 * 3600.0  # ttwid 本地缓存 6 小时
#: 两次网络请求的最小间隔。实测 < 0.5 秒连续请求约 4 次即触发风控，这里留足余量
_MIN_INTERVAL = 1.2

_ttwid_lock = threading.Lock()
_ttwid_cache: dict[str, Any] = {"value": None, "expire": 0.0}
_rate_lock = threading.Lock()
_last_request = [0.0]

#: 内部哨兵：作品确实不存在 / 已失效（与「抓取失败」区分开）
_NOT_FOUND = object()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """阻止 urllib 自动跟随 302，便于手动解析短链。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: N802
        return None


# --------------------------------------------------------------------------- #
# 内部工具
# --------------------------------------------------------------------------- #
def _throttle() -> None:
    """全局请求节流，避免触发 argus 风控。"""
    with _rate_lock:
        gap = time.time() - _last_request[0]
        if gap < _MIN_INTERVAL:
            time.sleep(_MIN_INTERVAL - gap)
        _last_request[0] = time.time()


def _http_get(url: str, timeout: float, extra_headers: dict[str, str] | None = None):
    """返回 ``(status, text, headers)``；HTTP 错误也返回响应体而不抛异常。"""
    headers = {
        "User-Agent": MOBILE_UA,
        "Accept": "text/html,application/json,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
            return resp.status, resp.read().decode("utf-8", "replace"), resp.headers
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            body = ""
        return exc.code, body, exc.headers


def _random_ms_token(size: int = 105) -> str:
    alphabet = string.ascii_letters + string.digits + "-_"
    return "".join(random.choice(alphabet) for _ in range(size)) + "=="


def _get_ttwid(timeout: float) -> str | None:
    """获取（并缓存）ttwid cookie；失败返回 None。"""
    now = time.time()
    if _ttwid_cache["value"] and _ttwid_cache["expire"] > now:
        return _ttwid_cache["value"]
    with _ttwid_lock:
        if _ttwid_cache["value"] and _ttwid_cache["expire"] > now:
            return _ttwid_cache["value"]
        payload = json.dumps(
            {
                "region": "cn",
                "aid": 1768,
                "needFid": False,
                "service": "www.ixigua.com",
                "migrate_info": {"ticket": "", "source": "node"},
                "cbUrlProtocol": "https",
                "union": True,
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            _TTWID_URL,
            data=payload,
            headers={"User-Agent": MOBILE_UA, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
                cookies = resp.headers.get_all("Set-Cookie") or []
        except Exception:  # noqa: BLE001
            return None
        for raw in cookies:
            match = re.search(r"(?:^|[;\s])ttwid=([^;]+)", raw)
            if match:
                value = match.group(1).strip()
                _ttwid_cache["value"] = value
                _ttwid_cache["expire"] = now + _TTWID_TTL
                return value
    return None


def _reset_ttwid() -> None:
    with _ttwid_lock:
        _ttwid_cache["value"] = None
        _ttwid_cache["expire"] = 0.0


def _extract_router_data(html: str) -> Any | None:
    """从 ``window._ROUTER_DATA = {...}`` 抠 JSON。

    用括号配平而不是正则：JSON 里嵌套的对象与字符串里的花括号会让正则提前截断。
    """
    pos = html.find("window._ROUTER_DATA")
    if pos < 0:
        return None
    start = html.find("{", pos)
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(html)):
        char = html[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        else:
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(html[start : index + 1])
                    except Exception:  # noqa: BLE001
                        return None
    return None


def is_valid_title(value: Any) -> bool:
    """判断一个标题是不是「真的标题」（过滤掉抖音创作者中心这类占位值）。"""
    if not isinstance(value, str):
        return False
    title = value.strip()
    if not title or title.lower() in INVALID_TITLES:
        return False
    # 「抖音创作者中心」偶尔会带上后缀
    return not any(bad and bad in title for bad in ("抖音创作者中心", "创作者服务平台"))


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _pack(desc: Any, statistics: Any) -> dict[str, Any]:
    statistics = statistics or {}
    title = str(desc).strip() if isinstance(desc, str) else ""
    return {
        "title": title if is_valid_title(title) else "",
        "views": _safe_int(statistics.get("play_count")),
        "likes": _safe_int(statistics.get("digg_count")),
        "comments": _safe_int(statistics.get("comment_count")),
        "collects": _safe_int(statistics.get("collect_count")),
        "shares": _safe_int(statistics.get("share_count")),
    }


# --------------------------------------------------------------------------- #
# 两条抓取通道
# --------------------------------------------------------------------------- #
def _fetch_via_detail(video_id: str, ttwid: str, timeout: float) -> Any:
    """通道 1：网页详情接口，直接返回结构化 JSON。"""
    url = _DETAIL_URL.format(vid=video_id, ms=_random_ms_token())
    status, text, _ = _http_get(
        url,
        timeout,
        extra_headers={
            "Cookie": f"ttwid={ttwid}",
            "Referer": f"https://www.douyin.com/video/{video_id}",
        },
    )
    if status != 200 or not text:
        return None
    try:
        payload = json.loads(text)
    except Exception:  # noqa: BLE001
        return None
    if payload.get("aweme_detail") is None and payload.get("filter_detail"):
        return _NOT_FOUND
    detail = payload.get("aweme_detail")
    if not isinstance(detail, dict):
        return None
    return _pack(detail.get("desc"), detail.get("statistics"))


def _fetch_via_share(video_id: str, ttwid: str, timeout: float) -> Any:
    """通道 2：移动端分享页 SSR 数据（降级 / 兜底）。"""
    url = _SHARE_URL.format(vid=video_id)
    status, text, _ = _http_get(
        url,
        timeout,
        extra_headers={"Cookie": f"ttwid={ttwid}", "Referer": "https://www.iesdouyin.com/"},
    )
    if status != 200 or not text:
        return None
    data = _extract_router_data(text)
    if not isinstance(data, dict):
        return None
    for value in (data.get("loaderData") or {}).values():
        if not isinstance(value, dict):
            continue
        info = value.get("videoInfoRes")
        if not isinstance(info, dict):
            continue
        if info.get("filter_list"):
            return _NOT_FOUND
        items = info.get("item_list") or []
        if items and isinstance(items[0], dict):
            return _pack(items[0].get("desc"), items[0].get("statistics"))
    return None


# --------------------------------------------------------------------------- #
# 对外接口
# --------------------------------------------------------------------------- #
def extract_video_id(text: str, timeout: float = 8.0) -> str | None:
    """从抖音链接 / 分享文案解析 video_id；支持 ``v.douyin.com`` 短链跳转。"""
    if not isinstance(text, str) or not text.strip():
        return None
    raw = text.strip()
    for pattern in _ID_PATTERNS:
        match = re.search(pattern, raw)
        if match:
            return match.group(1)
    if _ID_RE.fullmatch(raw):
        return raw
    short = _SHORT_LINK_RE.search(raw)
    if short:
        try:
            _throttle()
            req = urllib.request.Request(short.group(0), headers={"User-Agent": MOBILE_UA})
            opener = urllib.request.build_opener(_NoRedirect)
            try:
                with opener.open(req, timeout=timeout) as resp:
                    location = resp.headers.get("Location") or ""
            except urllib.error.HTTPError as exc:
                location = (exc.headers or {}).get("Location") or ""
            if location:
                return extract_video_id(location, timeout)
        except Exception:  # noqa: BLE001
            pass
    return None


def fetch_share_info(url_or_id: str, timeout: float = 8.0) -> dict[str, Any] | None:
    """抓取抖音作品的标题与互动指标。

    返回 ``{"title": str, "views": int, "likes": int, "comments": int,
    "collects": int, "shares": int}``；失败（含作品不存在 / 已失效）返回 ``None``，
    拿不到的字段一律为 0。

    :param url_or_id: 作品链接（``www.douyin.com/video/xxx``、``v.douyin.com`` 短链）
        或纯数字作品 id。
    """
    video_id = extract_video_id(str(url_or_id or ""))
    if not video_id or not _ID_RE.fullmatch(video_id):
        return None

    try:
        for attempt in (0, 1):
            ttwid = _get_ttwid(timeout)
            if not ttwid:
                return None
            for channel in (_fetch_via_detail, _fetch_via_share):
                try:
                    _throttle()
                    result = channel(video_id, ttwid, timeout)
                except Exception:  # noqa: BLE001 - 单通道失败不影响另一条
                    log.debug("抖音抓取通道失败", exc_info=True)
                    result = None
                if result is _NOT_FOUND:
                    return None
                if isinstance(result, dict):
                    return result
            # 两条通道都没拿到数据：可能是 ttwid 失效，清缓存重试一次
            if attempt == 0:
                _reset_ttwid()
    except Exception:  # noqa: BLE001 - 任何异常都退化为「抓不到」，由调用方兜底
        log.debug("抖音轻量抓取失败", exc_info=True)
        return None
    return None


__all__ = [
    "INVALID_TITLES",
    "extract_video_id",
    "fetch_share_info",
    "is_valid_title",
]
