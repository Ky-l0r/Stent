"""业务服务层：热点发现 / 内容创作 / 发布编排 / 数据分析 / 账号画像。

注意：本包 ``__init__`` 刻意保持轻量，具体服务按需懒加载（PEP 562），
以避免 UI 启动时连锁加载 Playwright、openai 等重依赖（企划书 4.3 冷启动 < 3s）。

用法::

    from stent.services import AnalyticsService   # 首次访问时才真正 import
    from stent.services.analytics import AnalyticsService   # 等价的显式写法
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "AnalyticsService",
    "available_platforms",
    "BENCHMARKS",
    "CreationRequest",
    "CreationResult",
    "CreatorService",
    "creator_service",
    "DEFAULT_BENCHMARK",
    "METRIC_KEYS",
    "PLATFORM_STYLES",
    "profile_service",
    "ProfileService",
    "publish_service",
    "PublishOutcome",
    "PublishPreparation",
    "PublishService",
    "HotSearchService",
    "HotItem",
    "hotsearch_service",
    "PlatformResult",
    "CheckItem",
    "CheckReport",
    "PrecheckService",
]

#: 符号 → 所属子模块。新增服务时在此登记即可被懒加载导出。
_LAZY_EXPORTS: dict[str, str] = {
    "AnalyticsService": "analytics",
    "available_platforms": "analytics",
    "BENCHMARKS": "analytics",
    "DEFAULT_BENCHMARK": "analytics",
    "METRIC_KEYS": "analytics",
    "HotSearchService": "hotsearch",
    "HotItem": "hotsearch",
    "PlatformResult": "hotsearch",
    "hotsearch_service": "hotsearch",
    "CheckItem": "precheck",
    "CheckReport": "precheck",
    "PrecheckService": "precheck",
    "CreationRequest": "creator",
    "CreationResult": "creator",
    "CreatorService": "creator",
    "creator_service": "creator",
    "PLATFORM_STYLES": "creator",
    "ProfileService": "profile",
    "profile_service": "profile",
    "PublishService": "publisher",
    "PublishPreparation": "publisher",
    "PublishOutcome": "publisher",
    "publish_service": "publisher",
}


def __getattr__(name: str) -> Any:
    """按需导入子模块符号（PEP 562），避免包初始化时的重依赖连锁加载。"""
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    module = import_module(f".{module_name}", __name__)
    value = getattr(module, name)
    globals()[name] = value  # 缓存，后续访问不再走 __getattr__
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
