"""平台适配器包。

对外暴露统一注册表，发布中心 / 数据分析只依赖 ``base`` 接口：

.. code-block:: python

    from stent.platforms import get_adapter, available_platforms

    adapter = get_adapter("xiaohongshu")   # 未登记 / 依赖缺失时返回 None
    ok, note = adapter.check_login()

设计要点：适配器一律**惰性导入**。只有在真正需要某个平台时才 import 对应的
Playwright 代码，保证冷启动不把浏览器内核拖进来（企划书 4.3 冷启动 < 3 秒）。
"""

from __future__ import annotations

import importlib
import logging
import threading
from typing import TYPE_CHECKING

from .base import (
    LoginRequired,
    LoginState,
    MetricSnapshot,
    PlatformAdapter,
    PlatformError,
    PlatformLimits,
    PublishPayload,
    PublishResult,
    emit,
)

if TYPE_CHECKING:  # pragma: no cover
    pass

log = logging.getLogger(__name__)

__all__ = [
    "ADAPTERS",
    "LoginRequired",
    "LoginState",
    "MetricSnapshot",
    "PLATFORM_ORDER",
    "PlatformAdapter",
    "PlatformError",
    "PlatformLimits",
    "PublishPayload",
    "PublishResult",
    "adapters",
    "all_adapters",
    "available_platforms",
    "emit",
    "get_adapter",
    "keys",
    "platform_label",
    "platform_labels",
    "platform_limits",
    "platform_from_url",
    "canonical_post_url",
    "post_identity",
    "PLATFORM_DOMAINS",
    "POST_ID_PATTERNS",
    "registered_platforms",
    "reset_cache",
]

#: 平台 key → (子模块名, 类名, 中文名)
_PLATFORM_SPECS: dict[str, tuple[str, str, str]] = {
    "xiaohongshu": (".xiaohongshu", "XiaoHongShuAdapter", "小红书"),
    "douyin": (".douyin", "DouYinAdapter", "抖音"),
    "zhihu": (".zhihu", "ZhiHuAdapter", "知乎"),
    "bilibili": (".bilibili", "BiliBiliAdapter", "B 站"),
}

#: 展示顺序（发布中心按此顺序列出平台）
PLATFORM_ORDER: tuple[str, ...] = ("xiaohongshu", "douyin", "zhihu", "bilibili")

#: 兼容别名：key → 适配器类（首次访问注册表后惰性填充）
ADAPTERS: dict[str, type[PlatformAdapter]] = {}

_cache: dict[str, PlatformAdapter | None] = {}
_lock = threading.RLock()


# --------------------------------------------------------------------------
# 注册表
# --------------------------------------------------------------------------
def registered_platforms() -> list[str]:
    """已登记的平台 key（不保证依赖可导入）。"""
    return [p for p in PLATFORM_ORDER if p in _PLATFORM_SPECS]


def keys() -> list[str]:
    """返回全部平台 key（按展示顺序）。"""
    ordered = [k for k in PLATFORM_ORDER if k in _PLATFORM_SPECS]
    ordered += [k for k in _PLATFORM_SPECS if k not in ordered]
    return ordered


def platform_label(platform: str) -> str:
    entry = _PLATFORM_SPECS.get((platform or "").strip().lower())
    return entry[2] if entry else platform


def platform_labels() -> dict[str, str]:
    return {key: entry[2] for key, entry in _PLATFORM_SPECS.items()}


def get_adapter(platform: str) -> PlatformAdapter | None:
    """取得平台适配器实例。

    未登记、模块缺失或实例化失败时返回 ``None``（**不抛异常**），
    调用方据此降级为「暂不支持该平台」提示。
    """
    key = (platform or "").strip().lower()
    if not key:
        return None
    with _lock:
        if key in _cache:
            return _cache[key]
        adapter = _load(key)
        _cache[key] = adapter
        if adapter is not None:
            ADAPTERS.setdefault(key, type(adapter))
        return adapter


def _load(key: str) -> PlatformAdapter | None:
    entry = _PLATFORM_SPECS.get(key)
    if not entry:
        return None
    module_name, class_name, label = entry
    try:
        module = importlib.import_module(module_name, __package__)
    except ImportError as exc:
        log.warning("平台 %s 的适配器暂不可用（%s）", label, exc)
        return None
    except Exception:
        log.exception("平台 %s 的适配器模块导入失败", label)
        return None
    cls = getattr(module, class_name, None)
    if cls is None:
        log.warning("模块 %s 中未找到类 %s", module_name, class_name)
        return None
    try:
        return cls()
    except Exception:
        log.exception("平台 %s 适配器实例化失败", label)
        return None


def available_platforms() -> list[str]:
    """适配器可成功加载的平台。"""
    return [p for p in keys() if get_adapter(p) is not None]


#: 平台 key → 作品链接的域名特征。
#: 用于「登记已有作品」：用户直接粘贴平台上已发布作品的链接，
#: 由域名判断该归到哪个平台，省掉一次手选。
PLATFORM_DOMAINS: dict[str, tuple[str, ...]] = {
    "xiaohongshu": ("xiaohongshu.com", "xhslink.com"),
    "douyin": ("douyin.com", "iesdouyin.com"),
    "zhihu": ("zhihu.com",),
    "bilibili": ("bilibili.com", "b23.tv"),
}


def platform_from_url(url: str) -> str:
    """按作品链接判断所属平台；识别不出返回空串。

    只做域名匹配，不请求网络——调用方据此决定是否让用户手动指定平台。
    """
    from urllib.parse import urlparse

    text = str(url or "").strip()
    if not text:
        return ""
    # 用户可能只粘了 "www.bilibili.com/video/BV1xx" 这种不带协议的
    if "://" not in text:
        text = "https://" + text
    try:
        host = (urlparse(text).hostname or "").lower()
    except Exception:  # noqa: BLE001
        return ""
    if not host:
        return ""
    for platform, domains in PLATFORM_DOMAINS.items():
        for domain in domains:
            if host == domain or host.endswith("." + domain):
                return platform
    return ""


#: 各平台作品链接的「唯一标识」规则。
#: 同一篇作品会有很多种链接写法（带 spm_id_from 参数、b23.tv 短链、modal_id 弹窗链接…），
#: 直接按字符串比较会把同一篇当成两条——这正是「先粘链接再账号导入就重复」的原因。
POST_ID_PATTERNS: dict[str, tuple[str, ...]] = {
    "bilibili": (r"(BV[0-9A-Za-z]{10})", r"av(\d+)"),
    "xiaohongshu": (r"/(?:explore|discovery/item|note)/([0-9a-zA-Z]+)",),
    "douyin": (r"/video/(\d+)", r"modal_id=(\d+)", r"/share/video/(\d+)"),
    "zhihu": (r"/p/(\d+)", r"/answer/(\d+)"),
}


def post_identity(platform: str, url: str) -> str:
    """取作品的唯一标识（如 B 站的 ``BV1xx411c7mD``）；识别不出返回空串。"""
    import re

    key = (platform or "").strip().lower()
    text = str(url or "").strip()
    if not text:
        return ""
    for pattern in POST_ID_PATTERNS.get(key, ()):
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1)
    return ""


def canonical_post_url(platform: str, url: str) -> str:
    """把作品链接归一化成稳定形式，用于去重与展示。

    归一化规则：优先用作品唯一标识重建标准链接；拿不到标识时退化为
    「去掉 query / fragment 的链接」。这样同一篇作品的多种写法会落到同一个值上。
    """
    from urllib.parse import urlparse

    key = (platform or "").strip().lower()
    text = str(url or "").strip()
    if not text:
        return ""
    identity = post_identity(key, text)
    if identity:
        if key == "bilibili":
            if identity.startswith("BV"):
                return f"https://www.bilibili.com/video/{identity}"
            return f"https://www.bilibili.com/video/av{identity}"
        if key == "xiaohongshu":
            return f"https://www.xiaohongshu.com/explore/{identity}"
        if key == "douyin":
            return f"https://www.douyin.com/video/{identity}"
        if key == "zhihu":
            # 文章与回答的 id 前缀不同，用原链接路径判断
            if "/answer/" in text:
                return f"https://www.zhihu.com/answer/{identity}"
            return f"https://zhuanlan.zhihu.com/p/{identity}"
    # 兜底：去掉参数与锚点
    probe = text if "://" in text else "https://" + text
    try:
        parsed = urlparse(probe)
        clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")
        return clean
    except Exception:  # noqa: BLE001
        return text


def all_adapters() -> list[PlatformAdapter]:
    """返回全部可用适配器实例（按展示顺序）。"""
    return [a for p in keys() if (a := get_adapter(p)) is not None]


def adapters() -> dict[str, PlatformAdapter]:
    return {p: a for p in keys() if (a := get_adapter(p)) is not None}


def platform_limits(platform: str) -> PlatformLimits:
    """取得平台发布限制；适配器不可用时给出保底默认值。"""
    adapter = get_adapter(platform)
    if adapter is not None:
        try:
            return adapter.limits
        except Exception:  # pragma: no cover
            log.warning("平台 %s 的 limits 获取失败", platform, exc_info=True)
    return PlatformLimits()


def reset_cache() -> None:
    """清空适配器缓存（配置或代码变更后调用）。"""
    with _lock:
        _cache.clear()
        ADAPTERS.clear()
