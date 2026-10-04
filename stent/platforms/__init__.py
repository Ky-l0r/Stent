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
