"""数据分析服务（企划书 3.2 模块四）。

四类能力，全部走本地 SQLite，不依赖任何 UI：

1. **展示** —— :meth:`AnalyticsService.overview` / :meth:`AnalyticsService.rank_contents`
   汇总与排行单篇内容的播放、点赞、评论、收藏、分享。
2. **趋势** —— :meth:`AnalyticsService.trend` 按日期聚合的时间序列（折线图数据源），
   可再按平台、主题维度切片。
3. **回流** —— :meth:`AnalyticsService.attribute` 用 LLM 分析表现最好 / 最差的内容，
   产出「可复用的内容结构经验」并写入画像记忆（``db.append_memory``），指导下一次创作。
4. **增量拉取** —— :meth:`AnalyticsService.sync_metrics` 只拉 ``db.posts_needing_sync()``
   挑出的待更新内容，单条失败不影响整体。

移植来源（Easel → Stent，详见 ``_reference/specs/analytics.md``）：

- ``skills/shared/scripts/social_stats.py`` 的确定性计算口径（互动率、互动综合分、
  环比、样本量警告、分组聚合）在此模块内以纯函数重新实现，避免跨项目 import。
- ``skills/openclaw/skill-social-performance-review`` 的基准区间与 7 维分析框架。
- ``skills/openclaw/skill-publish-analytics/scripts/analyze.py`` 的「最佳发布时段」模式。
- ``skills/openclaw/skill-data-tracker/scripts/track.py`` 的「一天一快照 + 去重」增量思路。

设计约束：

- 本模块**不得** import PySide6 / anthropic / openclaw / Easel 的任何模块；
- 平台适配器走**惰性导入 + 防御性写法**：``stent.platforms`` 的 registry 尚未实现时，
  本模块仍可正常 import，``sync_metrics`` 返回友好错误而不是抛 ImportError；
- 所有数据访问都通过注入的 ``Database`` 对象，不直接裸写 sqlite3；
- 空数据一律返回空结构或带 ``warnings`` 的结果，不抛异常。
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta
from typing import Any, Callable, Iterable, Sequence

log = logging.getLogger(__name__)

__all__ = [
    "AnalyticsService",
    "BENCHMARKS",
    "DEFAULT_BENCHMARK",
    "METRIC_KEYS",
    "PROGRESS_METRIC_KEYS",
    "TIME_BUCKETS",
    "WEEKDAYS",
    "available_platforms",
    "evaluate_benchmark",
    "get_adapter",
    "resolve_adapter",
    "safe_div",
    "engagement_rate",
    "engagement_score",
    "pct_change",
    "mean",
    "median",
    "sample_warning",
]

# --------------------------------------------------------------------------- #
# 常量：指标口径与平台基准
# --------------------------------------------------------------------------- #

#: 采样范围内可供图表/聚合使用的指标列（metrics 表的数值列）
METRIC_KEYS: tuple[str, ...] = ("views", "likes", "comments", "collects", "shares")

#: 参与互动率的指标（与 Easel review.py 的 _interactions 一致：赞 + 评 + 藏 + 转）
INTERACTION_KEYS: tuple[str, ...] = ("likes", "comments", "collects", "shares")

#: 互动综合分权重（沿用 social_stats.engagement_score 的默认权重）
ENGAGEMENT_WEIGHTS: dict[str, float] = {
    "views": 0.1,
    "likes": 1.0,
    "comments": 2.0,
    "collects": 2.0,  # Easel 原口径为 shares×3；收藏在中文平台等价于 saves，给 2.0
    "shares": 3.0,
}

#: 内部加权的评分维度（沿用 analysis-framework.md「内部评分框架」）
SCORE_WEIGHTS: dict[str, float] = {
    "engagement_vs_benchmark": 0.25,
    "relative_views": 0.20,
    "top_post": 0.20,
    "consistency": 0.15,
    "momentum": 0.20,
}

#: 一条画像记忆最多保留多少字，避免记忆区膨胀
MEMORY_TEXT_LIMIT = 1200

#: 归因时参与分析的内容条数（Top N / Bottom N）
ATTRIBUTE_SAMPLE = 3

#: 单次同步最多处理的帖子数（默认值，可被方法参数覆盖）
DEFAULT_SYNC_LIMIT = 50

#: 增量同步的最小间隔（分钟）
DEFAULT_SYNC_INTERVAL_MINUTES = 30

#: 一次拉取最多读入的 metrics 行数（防止长历史导致内存膨胀）
DEFAULT_METRIC_ROW_LIMIT = 20000

#: 发布时段分桶（沿用 analyze.py TIME_BUCKETS）
TIME_BUCKETS: tuple[tuple[str, range], ...] = (
    ("早晨", range(6, 9)),
    ("上午", range(9, 12)),
    ("午间", range(12, 14)),
    ("下午", range(14, 18)),
    ("晚间", range(18, 22)),
)

WEEKDAYS: tuple[str, ...] = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

#: 平台基准表（口径见 _reference/specs/analytics.md）
#: engagement = 互动率区间（百分数）；collect_rate 仅小红书等「收藏为核心质量信号」的平台有意义。
#: ranges 为 <偏低上限, <平均上限, <良好上限> 三段，超过第三段即「优秀」。
BENCHMARKS: dict[str, dict[str, Any]] = {
    "xiaohongshu": {
        "label": "小红书",
        "engagement": (3.0, 6.0, 10.0),
        "collect_rate": (1.0, 3.0, 6.0),
        "note": "互动率 =(点赞+收藏+评论+分享)÷曝光；收藏率是核心质量信号",
    },
    "douyin": {
        "label": "抖音",
        "engagement": (3.0, 6.0, 10.0),
        "note": "完播率为首要指标；播放量按推荐分发，与粉丝量不成正比",
    },
    "bilibili": {
        "label": "B 站",
        "engagement": (3.0, 8.0, 12.0),
        "note": "看「三连」（点赞+投币+收藏）与完播；互动率良好线约 8%",
    },
    "weibo": {
        "label": "微博",
        "engagement": (0.5, 2.0, 4.0),
        "note": "转发链决定放大，蹭热点显著拉高",
    },
    "zhihu": {
        "label": "知乎",
        "engagement": (0.5, 2.0, 5.0),
        "note": "长文打开与赞同为主，无公开权威区间，按互动率粗估",
    },
    "weixin_mp": {
        "label": "公众号",
        "engagement": (2.0, 5.0, 8.0),
        "note": "无推荐分发；打开率普遍 2-5%，无在看数据时用互动率近似",
    },
}

#: 未收录平台的兜底基准（取 Easel 通用「互动率 1-3-6」口径）
DEFAULT_BENCHMARK: dict[str, Any] = {
    "label": "通用",
    "engagement": (1.0, 3.0, 6.0),
    "note": "平台未收录，使用通用区间；建立自有历史后以自身上月为准",
}

# --------------------------------------------------------------------------- #
# 常量：中英术语对照（喂给 LLM 的统计一律用中文标签，避免模型照抄英文键名）
# --------------------------------------------------------------------------- #

#: 平台 key → 中文名（registry 不可用时的兜底）
PLATFORM_LABELS: dict[str, str] = {
    "bilibili": "B 站",
    "douyin": "抖音",
    "xiaohongshu": "小红书",
    "zhihu": "知乎",
    "weibo": "微博",
    "weixin_mp": "公众号",
    "kuaishou": "快手",
    "toutiao": "今日头条",
    "unknown": "未知平台",
}

#: 指标键 → 中文名
METRIC_LABELS: dict[str, str] = {
    "views": "播放量",
    "likes": "点赞",
    "comments": "评论",
    "collects": "收藏",
    "shares": "转发",
    "engagement_rate": "互动率",
    "engagement_score": "综合分",
    "collect_rate": "收藏率",
}

#: 归因统计字段 → 中文名
STATS_LABELS: dict[str, str] = {
    "post_count": "内容条数",
    "avg_engagement_rate": "平均互动率",
    "median_engagement_rate": "互动率中位数",
    "aggregate_engagement_rate": "总体互动率(总互动÷总播放)",
    "totals": "各项合计",
    "avg_views": "平均播放量",
    "avg_collects": "平均收藏",
    "top_avg_rate": "头部平均互动率",
    "bottom_avg_rate": "尾部平均互动率",
    "top_lift": "头部相对平均的倍数",
    "by_platform": "分平台表现",
    "by_topic": "分主题表现",
    "internal_score": "内部评分",
    "warnings": "数据提醒",
    "platform": "平台",
    "topic": "主题",
    "score_1to10": "评分(1-10)",
    "components": "评分维度",
    "dimensions_used": "实际参与维度",
    "note": "说明",
}

#: 内部评分维度键 → 中文名
COMPONENT_LABELS: dict[str, str] = {
    "engagement_vs_benchmark": "互动率对平台基准",
    "relative_views": "播放量相对水平",
    "top_post": "头部内容相对均值",
    "consistency": "发布一致性",
    "momentum": "近期势头",
}

#: 兜底替换表：LLM 回复里若仍残留英文标识，展示/入库前统一换成中文
_TERM_ZH: dict[str, str] = {
    **STATS_LABELS,
    **COMPONENT_LABELS,
    **METRIC_LABELS,
    **PLATFORM_LABELS,
}
_TERM_ZH.pop("note", None)  # 太短，容易误伤正常英文单词

_TERM_RE = re.compile(
    "|".join(re.escape(key) for key in sorted(_TERM_ZH, key=len, reverse=True)),
    re.IGNORECASE,
)


# --------------------------------------------------------------------------- #
# 纯函数：确定性计算（移植自 social_stats.py，只依赖标准库）
# --------------------------------------------------------------------------- #
def safe_div(
    numerator: float | None, denominator: float | None, default: float | None = None
) -> float | None:
    """安全除法：分子/分母为 None 或分母为 0 时返回 ``default``（默认 None）。"""
    if numerator is None or denominator is None or denominator == 0:
        return default
    return numerator / denominator


def _clean(values: Iterable[float | None]) -> list[float]:
    """去掉 None，返回可参与计算的数值列表。"""
    return [v for v in values if v is not None]


def mean(values: Iterable[float | None]) -> float | None:
    """算术平均，忽略 None；空列表返回 None。"""
    xs = _clean(values)
    return safe_div(sum(xs), float(len(xs)))


def median(values: Iterable[float | None]) -> float | None:
    """中位数，忽略 None；空列表返回 None。"""
    xs = sorted(_clean(values))
    n = len(xs)
    if n == 0:
        return None
    mid = n // 2
    if n % 2 == 1:
        return float(xs[mid])
    return (xs[mid - 1] + xs[mid]) / 2.0


def coverage(values: Sequence[float | None]) -> float | None:
    """非 None 比例（0~1），衡量某指标的数据覆盖率；空序列返回 None。"""
    if not values:
        return None
    return safe_div(float(len(_clean(values))), float(len(values)))


def engagement_rate(
    interactions: float | None, denominator: float | None, *, as_percent: bool = True
) -> float | None:
    """互动率 = 互动总量 ÷ 基数。

    基数（reach / impressions / views）为 0 或 None 时返回 None —— 即
    「数据缺失」而不是「表现差」。``as_percent=True`` 时返回百分数。
    """
    rate = safe_div(interactions, denominator)
    if rate is None:
        return None
    return rate * 100.0 if as_percent else rate


def engagement_score(
    views: float | None = 0,
    likes: float | None = 0,
    comments: float | None = 0,
    collects: float | None = 0,
    shares: float | None = 0,
    weights: dict[str, float] | None = None,
) -> float:
    """互动综合分：默认 ``views×0.1 + likes×1 + comments×2 + collects×2 + shares×3``。

    缺失值视为 0（不贡献分数），可通过 ``weights`` 覆盖任意权重。
    """
    w = dict(ENGAGEMENT_WEIGHTS)
    if weights:
        w.update(weights)
    values = {
        "views": views,
        "likes": likes,
        "comments": comments,
        "collects": collects,
        "shares": shares,
    }
    return sum(w[k] * (values.get(k) or 0) for k in w)


def pct_change(current: float | None, previous: float | None) -> float | None:
    """变化率 = (current - previous) / previous × 100；previous 为 0/None 时返回 None。"""
    if current is None or previous is None or previous == 0:
        return None
    return (current - previous) / previous * 100.0


def sample_warning(n: int, min_n: int = 5, label: str = "样本") -> str | None:
    """样本量不足时返回中文警告字符串，充足返回 None。"""
    if n < min_n:
        return f"⚠ {label}不足（{n} < {min_n}），结果可能不具统计意义"
    return None


def scale_to_range(
    value: float | None,
    lo: float,
    hi: float,
    out_lo: float = 0.0,
    out_hi: float = 10.0,
) -> float | None:
    """把 ``value`` 从 ``[lo, hi]`` 线性映射到 ``[out_lo, out_hi]`` 并夹逼。"""
    if value is None or hi == lo:
        return None
    ratio = max(0.0, min(1.0, (value - lo) / (hi - lo)))
    return out_lo + ratio * (out_hi - out_lo)


def weighted_score(
    components: dict[str, float | None], weights: dict[str, float]
) -> tuple[float | None, list[str]]:
    """加权评分，值为 None 的维度被剔除并对剩余权重重新归一化（缺数据不废评分）。

    返回 ``(总分, 实际参与维度列表)``；全维度缺失时返回 ``(None, [])``。
    """
    used = [k for k in weights if components.get(k) is not None]
    total_w = sum(weights[k] for k in used)
    if not used or total_w == 0:
        return None, []
    score = sum(float(components[k]) * weights[k] for k in used) / total_w
    return score, used


def benchmark_for(platform: str | None) -> dict[str, Any]:
    """取平台基准（未收录时返回通用基准）。"""
    key = (platform or "").strip().lower()
    return BENCHMARKS.get(key, DEFAULT_BENCHMARK)


def evaluate_benchmark(
    value_pct: float | None, ranges: Sequence[float]
) -> str | None:
    """按基准区间给出中文评级：偏低 / 平均 / 良好 / 优秀；无数据返回 None。"""
    if value_pct is None:
        return None
    padded = list(ranges) + [0.0] * (3 - len(list(ranges)))
    low, avg, good = padded[0], padded[1], padded[2]
    if value_pct < low:
        return "偏低"
    if value_pct < avg:
        return "平均"
    if value_pct < good:
        return "良好"
    return "优秀"


# --------------------------------------------------------------------------- #
# 平台适配器：惰性导入 + 防御性解析
# --------------------------------------------------------------------------- #
def _import_platforms() -> Any | None:
    """惰性导入 ``stent.platforms``；不存在或导入失败时返回 None（绝不抛 ImportError）。"""
    try:
        from .. import platforms as platforms_module  # noqa: PLC0415
    except Exception:  # noqa: BLE001 - registry 未实现 / 循环依赖 / 依赖缺失
        log.debug("stent.platforms 尚不可用，数据分析将无法拉取指标", exc_info=True)
        return None
    return platforms_module


def get_adapter(platform: str) -> Any | None:
    """按平台 key 取适配器实例。

    期望的 registry 接口（见 ``_reference/specs/analytics.md``）::

        from stent.platforms import get_adapter
        adapter = get_adapter("xiaohongshu")   # 未注册时返回 None

    为了在 registry 尚未落地时也能工作，这里依次尝试多种可能形态：
    ``get_adapter`` / ``get`` / ``adapter_for`` 工厂函数，或
    ``ADAPTERS`` / ``PLATFORMS`` / ``REGISTRY`` / ``_REGISTRY`` 映射。
    全部不可用时返回 None。
    """
    module = _import_platforms()
    if module is None or not platform:
        return None

    for factory_name in ("get_adapter", "get", "adapter_for", "resolve_adapter"):
        factory = getattr(module, factory_name, None)
        if not callable(factory):
            continue
        try:
            adapter = factory(platform)
        except Exception:  # noqa: BLE001 - 工厂内部异常不应炸掉同步流程
            log.warning("platforms.%s(%r) 调用失败", factory_name, platform, exc_info=True)
            continue
        if adapter is not None:
            return adapter

    for mapping_name in ("ADAPTERS", "PLATFORMS", "REGISTRY", "_REGISTRY", "ADAPTER_REGISTRY"):
        mapping = getattr(module, mapping_name, None)
        if not isinstance(mapping, dict):
            continue
        adapter = mapping.get(platform)
        if adapter is None:
            continue
        # 映射里可能存的是类而不是实例
        if isinstance(adapter, type):
            try:
                adapter = adapter()
            except Exception:  # noqa: BLE001
                log.warning("适配器类 %s 实例化失败", adapter, exc_info=True)
                continue
        return adapter

    # 最后尝试 ``stent.platforms.<platform>`` 子模块里的 Adapter 类
    for attr in (platform, f"{platform}_adapter"):
        sub = getattr(module, attr, None)
        if isinstance(sub, type):
            try:
                return sub()
            except Exception:  # noqa: BLE001
                continue
    return None


def resolve_adapter(platform: str) -> tuple[Any | None, str]:
    """取适配器并返回 ``(adapter, 错误说明)``；成功时错误说明为空串。"""
    if _import_platforms() is None:
        return None, "平台适配器模块 stent.platforms 尚未实现，暂时无法拉取指标"
    adapter = get_adapter(platform)
    if adapter is None:
        return None, f"未注册平台适配器：{platform}"
    if not callable(getattr(adapter, "fetch_metrics", None)):
        return None, f"适配器 {platform} 未实现 fetch_metrics(post_url)"
    return adapter, ""


def _overrides_account_listing(adapter: Any) -> bool:
    """判断适配器是否**真的**实现了 ``list_account_posts``。

    基类给了一个返回空列表的默认实现，所以「方法存在」不等于「平台支持」；
    这里比较函数对象是否被重写，避免把「不支持」误报成「读到 0 篇」。
    """
    method = getattr(type(adapter), "list_account_posts", None)
    if method is None:
        return False
    try:
        from ..platforms.base import PlatformAdapter
    except Exception:  # noqa: BLE001
        return False
    return method is not PlatformAdapter.list_account_posts


#: 支持「登记已有作品」的平台白名单。
#: 只放经过验证、或至少路径可靠的平台；其余（如小红书）不给入口，
#: 免得用户反复尝试却总是失败。
IMPORTABLE_PLATFORMS: tuple[str, ...] = ("bilibili", "douyin", "zhihu")


def _platform_label(platform: str) -> str:
    """平台中文名（registry 不可用时退回 key）。"""
    module = _import_platforms()
    labeller = getattr(module, "platform_label", None) if module is not None else None
    if callable(labeller):
        try:
            return str(labeller(platform) or platform)
        except Exception:  # noqa: BLE001
            pass
    return platform


def _platform_zh(platform: Any) -> str:
    """平台 key → 中文名（用于 prompt 与展示，保证不出现 bilibili/douyin 这类英文）。"""
    key = str(platform or "").strip()
    if not key:
        return "未知平台"
    lowered = key.lower()
    if lowered in PLATFORM_LABELS:
        return PLATFORM_LABELS[lowered]
    bench = BENCHMARKS.get(lowered) or {}
    if bench.get("label"):
        return str(bench["label"])
    label = _platform_label(lowered)
    return label if label and label != lowered else key


def _key_zh(key: Any) -> str:
    """统计字段键 → 中文名（未收录的键原样保留）。"""
    raw = str(key)
    return (
        STATS_LABELS.get(raw)
        or COMPONENT_LABELS.get(raw)
        or METRIC_LABELS.get(raw)
        or PLATFORM_LABELS.get(raw.lower())
        or raw
    )


def _zh_stats(value: Any, key: Any = None) -> Any:
    """把确定性统计递归翻译成中文标签结构（只改键名与平台名，不动数值）。"""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            label = _key_zh(raw_key)
            lowered = str(raw_key).lower()
            if lowered == "platform" and isinstance(raw_value, str):
                out[label] = _platform_zh(raw_value)
            elif lowered == "dimensions_used" and isinstance(raw_value, (list, tuple)):
                # 值是维度键名（如 consistency / relative_views），同样要翻成中文
                out[label] = [_key_zh(item) for item in raw_value]
            else:
                out[label] = _zh_stats(raw_value, raw_key)
        return out
    if isinstance(value, (list, tuple)):
        return [_zh_stats(item, key) for item in value]
    return value


def _localize_terms(text: Any) -> str:
    """兜底中文化：把文本里残留的英文指标名/平台标识换成中文。"""
    if not text:
        return ""
    return _TERM_RE.sub(lambda m: _TERM_ZH[m.group(0).lower()], str(text))


def _zh_list(raw: Any) -> list[str]:
    """把 LLM 返回的条目列表统一成中文化后的纯文本列表。"""
    if isinstance(raw, (str, bytes)):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return []
    out: list[str] = []
    for item in raw:
        if isinstance(item, dict):
            item = "；".join(f"{_key_zh(k)}：{v}" for k, v in item.items())
        text = _localize_terms(item).strip()
        if text:
            out.append(text)
    return out


def _canonical_url(platform: str, url: str) -> str:
    """把作品链接归一化（去参数、短链还原），用于去重。"""
    module = _import_platforms()
    resolver = getattr(module, "canonical_post_url", None) if module is not None else None
    if not callable(resolver):
        return str(url or "").strip()
    try:
        return str(resolver(platform, url) or "").strip()
    except Exception:  # noqa: BLE001
        log.debug("归一化作品链接失败", exc_info=True)
        return str(url or "").strip()


def _post_identity(platform: str, url: str) -> str:
    """取作品的唯一标识（B 站 BV 号、抖音 video id 等）。"""
    module = _import_platforms()
    resolver = getattr(module, "post_identity", None) if module is not None else None
    if not callable(resolver):
        return ""
    try:
        return str(resolver(platform, url) or "")
    except Exception:  # noqa: BLE001
        return ""


def _looks_like_url(text: str) -> bool:
    """判断标题是不是「其实是链接」——早期登记的作品标题被 URL 顶替过。"""
    value = str(text or "").strip()
    if not value:
        return True
    lowered = value.lower()
    return lowered.startswith(("http://", "https://", "www.")) or "://" in lowered


#: 这些「标题」其实是抓错的占位值（浏览器打开抖音作品页时经常拿到的就是它），
#: 需要在下一次同步时用真实标题覆盖掉。
_INVALID_TITLES: frozenset[str] = frozenset(
    {
        "抖音创作者中心",
        "创作者中心",
        "创作者服务平台",
        "抖音",
        "douyin",
        "抖音短视频",
        "记录美好生活",
        "抖音-记录美好生活",
    }
)


def _title_needs_refill(title: Any) -> bool:
    """判断一条记录的标题是否需要用平台标题回填。

    三种情况要回填：标题为空、标题其实是链接（早期被 URL 顶替过）、
    标题是「抖音创作者中心」这类抓错的占位值——否则内容排行里会出现
    一整页一模一样的「抖音创作者中心」。
    """
    value = str(title or "").strip()
    if not value:
        return True
    if _looks_like_url(value):
        return True
    if value.lower() in _INVALID_TITLES:
        return True
    return any(bad in value for bad in ("抖音创作者中心", "创作者服务平台"))


def _platform_from_url(url: str) -> str:
    """按作品链接判断所属平台；registry 不可用时退回空串。

    走 ``stent.platforms.platform_from_url``，保持与发布中心同一套域名口径。
    """
    module = _import_platforms()
    resolver = getattr(module, "platform_from_url", None) if module is not None else None
    if not callable(resolver):
        return ""
    try:
        return str(resolver(url) or "")
    except Exception:  # noqa: BLE001
        log.debug("按链接识别平台失败", exc_info=True)
        return ""


def available_platforms() -> list[str]:
    """列出 registry 中已注册的平台 key（registry 不存在时返回空列表）。"""
    module = _import_platforms()
    if module is None:
        return []
    for mapping_name in ("ADAPTERS", "PLATFORMS", "REGISTRY", "_REGISTRY"):
        mapping = getattr(module, mapping_name, None)
        if isinstance(mapping, dict) and mapping:
            return sorted(str(k) for k in mapping)
    for factory_name in ("available_platforms", "list_platforms", "platforms"):
        factory = getattr(module, factory_name, None)
        if callable(factory):
            try:
                found = factory()
            except Exception:  # noqa: BLE001
                continue
            if isinstance(found, (list, tuple, set)):
                return sorted(str(k) for k in found)
    return []


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #
def _emit(on_progress: Any, message: str, percent: int | None = None) -> None:
    """向 UI 汇报进度；回调异常绝不影响主流程（与 platforms.base.emit 同口径）。"""
    if not callable(on_progress):
        return
    try:
        on_progress(message, percent)
    except TypeError:
        try:
            on_progress(message)
        except Exception:  # pragma: no cover - 进度回调不应影响主流程
            pass
    except Exception:  # pragma: no cover
        pass


def _ts(value: Any) -> datetime | None:
    """宽松解析时间戳：支持 ``YYYY-MM-DD HH:MM:SS`` / ISO / ``YYYY-MM-DD`` / ``YYYY/MM/DD``。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("/", "-").replace("Z", ""))
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(text[: len(fmt) + 2], fmt)
        except ValueError:
            continue
    return None


def _hour_bucket(hour: int) -> str:
    """发布小时 → 中文时段名（沿用 analyze.py 的分桶）。"""
    for name, hours in TIME_BUCKETS:
        if hour in hours:
            return name
    return "深夜"


def _to_int(value: Any) -> int | None:
    """把平台返回值转 int；无法转换返回 None。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError):
        return None


def _extract_json(text: str) -> Any | None:
    """从 LLM 回复里尽力提取 JSON 对象（支持 ```json 围栏与前后废话）。"""
    if not text:
        return None
    fenced = re.search(r"```(?:json)?\s*(.+?)\s*```", text, re.S)
    candidates = [fenced.group(1)] if fenced else []
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])
    candidates.append(text)
    for chunk in candidates:
        try:
            data = json.loads(chunk)
        except (TypeError, ValueError):
            continue
        if isinstance(data, dict):
            return data
    return None


# --------------------------------------------------------------------------- #
# 服务主体
# --------------------------------------------------------------------------- #
class AnalyticsService:
    """社媒数据分析服务：指标拉取、汇总、趋势、排行、归因回流。

    典型用法（UI 层在工作线程里调用）::

        from stent.services.analytics import AnalyticsService

        svc = AnalyticsService()
        svc.sync_metrics(on_progress=lambda msg, pct: print(msg, pct))
        data = svc.overview(days=30)
        series = svc.trend(days=30, platform="xiaohongshu", metric="views")
        result = svc.attribute(days=30)      # LLM 未配置时返回友好提示
    """

    def __init__(
        self,
        db: Any | None = None,
        llm: Any | None = None,
        config_manager: Any | None = None,
    ) -> None:
        """``db`` / ``llm`` / ``config_manager`` 均可注入，默认使用全局单例。"""
        self._db = db
        self._llm = llm
        self._config_manager = config_manager

    # -- 依赖（全部惰性，方便测试与解耦） --------------------------------
    @property
    def db(self) -> Any:
        """数据库访问对象（``stent.core.db.Database``）。"""
        if self._db is None:
            from ..core.db import db as global_db  # noqa: PLC0415

            self._db = global_db
        return self._db

    @property
    def config_manager(self) -> Any:
        """配置中心单例。"""
        if self._config_manager is None:
            from ..config import config_manager as global_manager  # noqa: PLC0415

            self._config_manager = global_manager
        return self._config_manager

    @property
    def llm(self) -> Any | None:
        """LLM 客户端；未安装 openai 或未配置时返回 None。"""
        if self._llm is None:
            try:
                from ..config import LLMConfig  # noqa: PLC0415
                from ..core.llm import LLMClient  # noqa: PLC0415

                cfg = getattr(self.config_manager.config, "llm", None) or LLMConfig()
                api_key = ""
                try:
                    api_key = self.config_manager.api_key() or ""
                except Exception:  # noqa: BLE001 - 密钥后端异常不应阻断分析
                    log.debug("读取 API Key 失败", exc_info=True)
                self._llm = LLMClient(cfg, api_key)
            except Exception:  # noqa: BLE001
                log.warning("LLM 客户端初始化失败", exc_info=True)
                return None
        return self._llm

    @property
    def llm_ready(self) -> bool:
        """LLM 是否已具备最小可用配置（Base URL + 模型 + Key）。"""
        checker = getattr(self.config_manager, "ready", None)
        if callable(checker):
            try:
                return bool(checker())
            except Exception:  # noqa: BLE001
                return False
        cfg = getattr(self.config_manager.config, "llm", None)
        return bool(getattr(cfg, "base_url", "") and getattr(cfg, "model", ""))

    # ------------------------------------------------------------------ #
    # 1. 指标拉取（增量）
    # ------------------------------------------------------------------ #
    def sync_metrics(
        self,
        platforms: Sequence[str] | None = None,
        on_progress: Any | None = None,
        force: bool = False,
        min_interval_minutes: int = DEFAULT_SYNC_INTERVAL_MINUTES,
        limit: int = DEFAULT_SYNC_LIMIT,
    ) -> dict[str, Any]:
        """增量拉取已发布内容的指标快照并落库。

        - ``force=False``：只处理 ``db.posts_needing_sync()`` 挑出的帖子（距上次同步
          超过 ``min_interval_minutes`` 或从未同步），避免每次全量刷新。
        - ``force=True``：忽略时间间隔，取最近 ``limit`` 条 ``status='published'`` 的帖子。
        - 单条失败只记入 ``errors``，不中断整体。
        - 成功后写 ``db.add_metric`` 并更新 ``posts.last_synced``。

        :param platforms: 只同步这些平台 key（如 ``["xiaohongshu"]``）；None 表示全部。
        :param on_progress: ``callable(message, percent)`` 或 ``callable(message)``。
        :param force: 是否忽略增量间隔强制刷新。
        :param min_interval_minutes: 增量同步的最小间隔（分钟）。
        :param limit: 单次最多处理的帖子数。
        :return: ``{"synced": n, "failed": n, "skipped": n, "errors": [...]}``
        """
        wanted = {str(p).strip().lower() for p in (platforms or []) if str(p).strip()}
        result: dict[str, Any] = {"synced": 0, "failed": 0, "skipped": 0, "errors": []}

        try:
            if force:
                posts = self.db.list_posts(status="published", limit=limit)
            else:
                posts = self.db.posts_needing_sync(
                    min_interval_minutes=min_interval_minutes, limit=limit
                )
        except Exception as exc:  # noqa: BLE001 - 数据库异常不向外抛
            log.exception("读取待同步帖子失败")
            result["errors"].append({"platform": "", "post_id": None, "error": f"读取待同步列表失败：{exc}"})
            return result

        if wanted:
            posts = [p for p in posts if str(p.get("platform") or "").lower() in wanted]

        if _import_platforms() is None:
            result["errors"].append(
                {
                    "platform": "",
                    "post_id": None,
                    "error": "平台适配器模块 stent.platforms 尚未实现，暂时无法拉取指标",
                }
            )
            _emit(on_progress, "平台适配器模块尚未实现，已中止同步", 100)
            return result
        if not posts:
            _emit(on_progress, "没有需要同步的内容", 100)
            return result

        total = len(posts)
        _emit(on_progress, f"待同步 {total} 条内容", 0)
        for index, post in enumerate(posts, start=1):
            platform = str(post.get("platform") or "")
            post_id = post.get("id")
            url = str(post.get("url") or "").strip()
            percent = int(index / total * 100)

            if not url:
                result["skipped"] += 1
                result["errors"].append(
                    {"platform": platform, "post_id": post_id, "error": "帖子缺少链接，无法拉取指标"}
                )
                _emit(on_progress, f"跳过（无链接）：{post.get('title') or post_id}", percent)
                continue

            adapter, reason = resolve_adapter(platform)
            if adapter is None:
                result["failed"] += 1
                result["errors"].append({"platform": platform, "post_id": post_id, "error": reason})
                _emit(on_progress, f"跳过：{reason}", percent)
                continue

            _emit(on_progress, f"拉取 {platform} · {post.get('title') or url}", percent)
            try:
                snapshot = adapter.fetch_metrics(url)
            except TypeError as exc:
                # 兼容只接受关键字参数或额外参数的适配器实现
                try:
                    snapshot = adapter.fetch_metrics(post_url=url)
                except Exception as inner:  # noqa: BLE001
                    result["failed"] += 1
                    result["errors"].append(
                        {"platform": platform, "post_id": post_id, "error": f"{inner}"}
                    )
                    log.warning("拉取指标失败 %s %s: %s", platform, url, exc)
                    continue
            except Exception as exc:  # noqa: BLE001 - 单条失败不影响整体
                result["failed"] += 1
                result["errors"].append(
                    {"platform": platform, "post_id": post_id, "error": f"{type(exc).__name__}: {exc}"}
                )
                log.warning("拉取指标失败 %s %s: %s", platform, url, exc)
                continue

            if snapshot is None:
                result["failed"] += 1
                result["errors"].append(
                    {"platform": platform, "post_id": post_id, "error": "适配器未返回指标（可能未登录或需要人工处理）"}
                )
                continue

            try:
                payload = snapshot.as_dict() if hasattr(snapshot, "as_dict") else dict(snapshot)
            except Exception:  # noqa: BLE001
                payload = {
                    key: getattr(snapshot, key, 0) for key in (*METRIC_KEYS, "raw")
                }

            try:
                self.db.add_metric(post_id, payload)
                fields: dict[str, Any] = {"last_synced": _now()}
                # 顺带用平台标题回填：粘贴链接登记的作品一开始没有标题，
                # 不补的话内容排行里显示的会是一串 URL；标题若是「抖音创作者中心」
                # 这类抓错的值，也一并覆盖掉。
                snapshot_title = str(getattr(snapshot, "title", "") or "").strip()
                if snapshot_title and _title_needs_refill(post.get("title")):
                    fields["title"] = snapshot_title
                self.db.update_post(post_id, **fields)
                result["synced"] += 1
            except Exception as exc:  # noqa: BLE001
                result["failed"] += 1
                result["errors"].append(
                    {"platform": platform, "post_id": post_id, "error": f"写入指标失败：{exc}"}
                )
                log.exception("写入指标失败 post_id=%s", post_id)

        _emit(on_progress, f"同步完成：成功 {result['synced']} / 失败 {result['failed']}", 100)
        if result["synced"]:
            try:
                self.db.log_action(
                    "",
                    "analytics_sync",
                    f"同步 {result['synced']} 条，失败 {result['failed']} 条",
                )
            except Exception:  # noqa: BLE001
                log.debug("写审计日志失败", exc_info=True)
        return result

    # ------------------------------------------------------------------ #
    # 0. 登记平台已有作品
    # ------------------------------------------------------------------ #
    def register_post(
        self,
        url: str,
        *,
        platform: str = "",
        title: str = "",
        content_id: int | None = None,
    ) -> dict[str, Any]:
        """登记一篇「已经在平台上发过」的作品，使其纳入指标同步。

        背景：数据分析此前只能看到从 Stent 发布中心发出去的内容；
        用户如果早就在平台上发过视频，那些数据是拿不到的。这里允许直接粘贴作品链接
        登记成一条 ``status='published'`` 的记录，后续 ``sync_metrics`` 就会把它
        一起拉取——**不需要经过 Stent 发布**。

        去重按**归一化后的链接**做：``b23.tv`` 短链、带 ``spm_id_from`` 参数的链接、
        账号导入返回的规范链接，都会归一到同一个值，因此「先粘链接、再账号导入」
        不会重复登记同一篇作品。

        :param url: 作品链接（必填，适配器靠它定位作品）
        :param platform: 平台 key；留空则按链接域名自动识别
        :param title: 作品标题；留空则先占位，抓指标时会用平台标题回填
        :param content_id: 可选，关联到某条草稿
        :return: ``{"ok": bool, "post_id": int, "platform": str, "message": str}``
        """
        link = str(url or "").strip()
        if not link:
            return {"ok": False, "post_id": 0, "platform": "", "message": "请填写作品链接"}

        key = str(platform or "").strip().lower()
        if not key:
            key = _platform_from_url(link)
        if not key:
            return {
                "ok": False,
                "post_id": 0,
                "platform": "",
                "message": "无法从链接识别平台，请手动选择所属平台",
            }
        if key not in IMPORTABLE_PLATFORMS:
            return {
                "ok": False,
                "post_id": 0,
                "platform": key,
                "message": (
                    f"{_platform_label(key)}暂不支持登记作品"
                    "（网页端拿不到可靠数据），请用其它平台或手动记录"
                ),
            }

        adapter, reason = resolve_adapter(key)
        if adapter is None:
            return {"ok": False, "post_id": 0, "platform": key, "message": reason}

        canonical = _canonical_url(key, link) or link

        # 同一个作品不要重复登记，否则指标会算两遍
        try:
            existing = self._find_post_by_identity(key, canonical)
        except Exception:  # noqa: BLE001 - 查重失败不阻断登记
            log.debug("登记作品查重失败", exc_info=True)
            existing = None
        if existing is not None:
            return {
                "ok": True,
                "post_id": int(existing.get("id") or 0),
                "platform": key,
                "message": "这篇作品之前已经登记过了，已跳过",
                "duplicated": True,
            }

        try:
            post_id = self.db.create_post(
                content_id=content_id,
                platform=key,
                title=str(title or "").strip(),
                body="",
                url=canonical,
                status="published",
                published_at=_now(),
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("登记作品失败")
            return {"ok": False, "post_id": 0, "platform": key, "message": f"写入记录失败：{exc}"}

        try:
            self.db.log_action(key, "register_post", f"登记平台已有作品：{canonical}", post_id=post_id)
        except Exception:  # noqa: BLE001
            log.debug("写审计日志失败", exc_info=True)

        return {
            "ok": True,
            "post_id": post_id,
            "platform": key,
            "message": "已登记，正在拉取该作品的指标",
        }

    def _find_post_by_identity(self, platform: str, canonical: str) -> dict[str, Any] | None:
        """按「平台 + 作品唯一标识」查重，兼容历史数据里存的各种链接写法。"""
        identity = _post_identity(platform, canonical)
        try:
            posts = self.db.list_posts(limit=1000)
        except Exception:  # noqa: BLE001
            return None
        for post in posts:
            if str(post.get("platform") or "").lower() != platform:
                continue
            stored = str(post.get("url") or "").strip()
            if stored == canonical:
                return post
            if identity and _post_identity(platform, stored) == identity:
                return post
        return None

    def delete_post(self, post_id: int) -> dict[str, Any]:
        """删除一条记录（含其指标）。用于清掉导入错的作品。"""
        try:
            post = self.db.get_post(int(post_id))
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "message": f"读取记录失败：{exc}"}
        if not post:
            return {"ok": False, "message": "记录不存在"}
        try:
            self.db.delete_post(int(post_id))
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "message": f"删除失败：{exc}"}
        try:
            self.db.log_action(
                str(post.get("platform") or ""),
                "delete_post",
                f"删除记录：{post.get('title') or post.get('url') or post_id}",
            )
        except Exception:  # noqa: BLE001
            log.debug("写审计日志失败", exc_info=True)
        return {"ok": True, "message": "已删除该记录及其指标"}

    def register_posts(self, lines: Iterable[str]) -> dict[str, Any]:
        """批量登记：一行一个链接，返回汇总结果。"""
        out: dict[str, Any] = {"added": 0, "skipped": 0, "failed": 0, "details": []}
        for raw in lines:
            link = str(raw or "").strip()
            if not link:
                continue
            result = self.register_post(link)
            if result.get("ok") and result.get("duplicated"):
                out["skipped"] += 1
            elif result.get("ok"):
                out["added"] += 1
            else:
                out["failed"] += 1
            out["details"].append(
                {"url": link, "platform": result.get("platform", ""), "message": result.get("message", "")}
            )
        return out

    def discover_account_posts(
        self, platform: str, *, limit: int = 50
    ) -> dict[str, Any]:
        """从已登录的账号拉取作品列表并自动登记。

        相比逐条粘贴链接，这条路一次就能把历史作品补齐；列表里若已带指标
        （B 站创作中心就是如此），直接入库，省掉逐条打开播放页的开销。

        平台适配器未实现 ``list_account_posts`` 时返回明确原因，由 UI 提示改用粘贴登记。
        """
        key = str(platform or "").strip().lower()
        out: dict[str, Any] = {
            "ok": False,
            "platform": key,
            "added": 0,
            "skipped": 0,
            "failed": 0,
            "message": "",
            "posts": [],
        }
        if not key:
            out["message"] = "请先选择要导入的平台"
            return out

        adapter, reason = resolve_adapter(key)
        if adapter is None:
            out["message"] = reason
            return out

        lister = getattr(adapter, "list_account_posts", None)
        if not callable(lister) or not _overrides_account_listing(adapter):
            out["message"] = (
                f"{_platform_label(key)}暂不支持自动导入作品列表，"
                "请改用「粘贴作品链接」逐个登记"
            )
            return out

        try:
            posts = lister(limit=limit)
        except TypeError:
            try:
                posts = lister(limit=limit, headless=True)
            except Exception as exc:  # noqa: BLE001
                out["message"] = f"读取作品列表失败：{type(exc).__name__}: {exc}"
                return out
        except Exception as exc:  # noqa: BLE001
            out["message"] = f"读取作品列表失败：{type(exc).__name__}: {exc}"
            return out

        posts = list(posts or [])
        if not posts:
            out["message"] = (
                f"没能从{_platform_label(key)}账号读到作品。"
                "常见原因：尚未在该平台登录（可在「设置 → 平台账号」登录后重试）、"
                "账号下还没有已发布内容，或平台页面结构有变化。"
                "可以改用「粘贴作品链接」逐个登记。"
            )
            return out

        out["posts"] = posts
        for item in posts:
            url = getattr(item, "url", "") or ""
            if not url:
                continue
            result = self.register_post(
                url, platform=key, title=getattr(item, "title", "") or ""
            )
            if not result.get("ok"):
                out["failed"] += 1
                continue
            post_id = int(result.get("post_id") or 0)
            if result.get("duplicated"):
                out["skipped"] += 1
            else:
                out["added"] += 1

            # 列表里自带的指标直接落库；发布时间也以平台为准
            published_at = getattr(item, "published_at", "") or ""
            try:
                if published_at and post_id:
                    self.db.update_post(post_id, published_at=published_at)
            except Exception:  # noqa: BLE001
                log.debug("回写发布时间失败 post_id=%s", post_id, exc_info=True)
            metrics = getattr(item, "metrics", None) or {}
            if metrics and post_id:
                try:
                    self.db.add_metric(post_id, metrics)
                    self.db.update_post(post_id, last_synced=_now())
                except Exception:  # noqa: BLE001
                    log.debug("写入列表指标失败 post_id=%s", post_id, exc_info=True)

        out["ok"] = True
        out["message"] = (
            f"从账号导入 {out['added']} 篇作品"
            + (f"，已有 {out['skipped']} 篇" if out["skipped"] else "")
            + (f"，失败 {out['failed']} 篇" if out["failed"] else "")
        )
        try:
            self.db.log_action(key, "discover_posts", out["message"])
        except Exception:  # noqa: BLE001
            log.debug("写审计日志失败", exc_info=True)
        return out

    def supports_account_import(self, platform: str) -> bool:
        """该平台是否支持「从账号导入作品」。

        读适配器上的显式声明（``supports_account_import``），而不是猜「有没有重写方法」：
        不支持的平台会明确返回 False，UI 据此禁用入口并说明原因。
        """
        adapter, _reason = resolve_adapter(platform)
        if adapter is None:
            return False
        return bool(getattr(adapter, "supports_account_import", False))

    @staticmethod
    def importable_platforms() -> list[str]:
        """可以出现在「登记已有作品」里的平台。

        小红书被排除：网页版不公开播放量、创作中心接口不稳定，粘贴链接也常常
        因为登录墙取不到数据。与其给一个「点了没反应」的入口，不如明确不提供。
        """
        module = _import_platforms()
        if module is None:
            return []
        keys = getattr(module, "keys", None)
        if not callable(keys):
            return []
        try:
            return [str(k) for k in keys() if str(k) in IMPORTABLE_PLATFORMS]
        except Exception:  # noqa: BLE001
            return []

    def sync_one(self, post_id: int) -> dict[str, Any]:
        """单独同步一条记录的指标（登记完立刻拉一次，用户不用再点同步）。"""
        try:
            post = self.db.get_post(int(post_id))
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "message": f"读取记录失败：{exc}"}
        if not post:
            return {"ok": False, "message": "记录不存在"}

        platform = str(post.get("platform") or "")
        url = str(post.get("url") or "").strip()
        if not url:
            return {"ok": False, "message": "该记录没有链接，无法拉取指标"}

        adapter, reason = resolve_adapter(platform)
        if adapter is None:
            return {"ok": False, "message": reason}
        try:
            snapshot = adapter.fetch_metrics(url)
        except TypeError:
            try:
                snapshot = adapter.fetch_metrics(post_url=url)
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "message": f"{type(exc).__name__}: {exc}"}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "message": f"{type(exc).__name__}: {exc}"}

        if snapshot is None:
            return {"ok": False, "message": "未能读取指标（可能需要先登录该平台账号）"}

        try:
            payload = snapshot.as_dict() if hasattr(snapshot, "as_dict") else dict(snapshot)
        except Exception:  # noqa: BLE001
            payload = {key: getattr(snapshot, key, 0) for key in (*METRIC_KEYS, "raw")}
        try:
            self.db.add_metric(int(post_id), payload)
            fields: dict[str, Any] = {"last_synced": _now()}
            snapshot_title = str(getattr(snapshot, "title", "") or "").strip()
            if snapshot_title and _looks_like_url(post.get("title")):
                fields["title"] = snapshot_title
            self.db.update_post(int(post_id), **fields)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "message": f"写入指标失败：{exc}"}
        return {"ok": True, "message": "指标已更新"}

    def sync_account_metrics(self, platforms: Sequence[str] | None = None) -> dict[str, Any]:
        """拉取账号级指标（粉丝数等）并缓存到 KV，供后续环比使用。

        适配器未实现 ``fetch_account_metrics`` 时静默跳过，不影响主流程。
        """
        wanted = [str(p).strip().lower() for p in (platforms or []) if str(p).strip()] or [
            str(p) for p in (getattr(self.config_manager.config, "publish_platforms", []) or [])
        ]
        out: dict[str, Any] = {"ok": [], "errors": []}
        for platform in wanted:
            adapter, reason = resolve_adapter(platform)
            if adapter is None or not callable(getattr(adapter, "fetch_account_metrics", None)):
                out["errors"].append({"platform": platform, "error": reason or "不支持账号级指标"})
                continue
            try:
                data = adapter.fetch_account_metrics()
            except Exception as exc:  # noqa: BLE001
                out["errors"].append({"platform": platform, "error": f"{type(exc).__name__}: {exc}"})
                continue
            if not data:
                out["errors"].append({"platform": platform, "error": "账号级指标为空"})
                continue
            try:
                self.db.set_kv(f"account_metrics:{platform}", {"at": _now(), "data": data})
                out["ok"].append(platform)
            except Exception as exc:  # noqa: BLE001
                out["errors"].append({"platform": platform, "error": f"缓存失败：{exc}"})
        return out

    # ------------------------------------------------------------------ #
    # 2. 汇总与展示
    # ------------------------------------------------------------------ #
    def overview(self, days: int = 30, platform: str | None = None) -> dict[str, Any]:
        """窗口内总览：总播放/点赞/评论/收藏/分享、发布篇数、按平台分组、平均互动率。

        单篇取窗口内**最近一条**指标快照（与 ``db.metric_overview`` 同口径），
        避免同一条内容被重复累加。
        """
        rows = self._snapshot_rows(days=days, platform=platform)
        totals = {key: sum(int(r.get(key) or 0) for r in rows) for key in METRIC_KEYS}
        interactions = sum(int(r.get(key) or 0) for r in rows for key in INTERACTION_KEYS)

        per_post_rates = [
            r["engagement_rate"] for r in rows if r.get("engagement_rate") is not None
        ]
        # 聚合互动率 = 总互动 ÷ 总播放（加权口径），与逐帖平均互动率一起给出
        aggregate_rate = engagement_rate(interactions, totals["views"])
        benchmark = benchmark_for(platform)

        latest = max((r.get("captured_at") or "" for r in rows), default="")
        warnings = [w for w in (sample_warning(len(rows), 5, "窗口内内容"),) if w]
        if rows and not any(r.get("views") for r in rows):
            warnings.append("窗口内所有内容的播放量为 0 或缺失，互动率不可用")

        return {
            "days": days,
            "platform": platform,
            "post_count": len(rows),
            "posts_with_metrics": sum(1 for r in rows if r.get("captured_at")),
            "totals": totals,
            "interactions": interactions,
            "avg_engagement_rate": mean(per_post_rates),
            "median_engagement_rate": median(per_post_rates),
            "aggregate_engagement_rate": aggregate_rate,
            "engagement_rate_coverage": coverage(
                [r.get("engagement_rate") for r in rows]
            ),
            "benchmark": {
                "label": benchmark.get("label"),
                "engagement_ranges": list(benchmark.get("engagement") or []),
                "rating": evaluate_benchmark(
                    aggregate_rate, benchmark.get("engagement") or ()
                ),
                "note": benchmark.get("note"),
            },
            "latest_captured_at": latest,
            "warnings": warnings,
        }

    def platform_breakdown(self, days: int = 30) -> list[dict[str, Any]]:
        """按平台分组统计（总览的切片），按互动综合分降序。"""
        rows = self._snapshot_rows(days=days)
        buckets: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            buckets.setdefault(str(row.get("platform") or "未知"), []).append(row)

        out: list[dict[str, Any]] = []
        for platform, items in buckets.items():
            totals = {key: sum(int(r.get(key) or 0) for r in items) for key in METRIC_KEYS}
            interactions = sum(int(r.get(key) or 0) for r in items for key in INTERACTION_KEYS)
            rate = engagement_rate(interactions, totals["views"])
            benchmark = benchmark_for(platform)
            out.append(
                {
                    "platform": platform,
                    "label": benchmark.get("label"),
                    "post_count": len(items),
                    "totals": totals,
                    "interactions": interactions,
                    "avg_views": mean([r.get("views") for r in items]),
                    "avg_likes": mean([r.get("likes") for r in items]),
                    "avg_collects": mean([r.get("collects") for r in items]),
                    "avg_engagement_rate": mean(
                        [r.get("engagement_rate") for r in items]
                    ),
                    "aggregate_engagement_rate": rate,
                    "rating": evaluate_benchmark(rate, benchmark.get("engagement") or ()),
                    "avg_score": mean([r.get("engagement_score") for r in items]),
                    "warning": sample_warning(len(items), 5, f"{platform} 内容"),
                }
            )
        out.sort(key=lambda r: (r["avg_score"] is None, -(r["avg_score"] or 0)))
        return out

    def rank_contents(
        self, days: int = 30, by: str = "engagement", limit: int = 20
    ) -> list[dict[str, Any]]:
        """内容排行。

        :param by: 排行依据，可选
            ``engagement``（互动综合分，默认）/ ``engagement_rate``（互动率）/
            ``views`` / ``likes`` / ``comments`` / ``collects`` / ``shares``。
        :param limit: 返回条数上限。
        :raises ValueError: ``by`` 不在支持范围内。
        """
        keys: dict[str, Callable[[dict[str, Any]], float]] = {
            "engagement": lambda r: r.get("engagement_score") or 0.0,
            "engagement_rate": lambda r: r.get("engagement_rate") or -1.0,
            "views": lambda r: r.get("views") or 0,
            "likes": lambda r: r.get("likes") or 0,
            "comments": lambda r: r.get("comments") or 0,
            "collects": lambda r: r.get("collects") or 0,
            "shares": lambda r: r.get("shares") or 0,
        }
        if by not in keys:
            raise ValueError(f"不支持排行依据：{by}（可选 {', '.join(keys)}）")

        rows = self._snapshot_rows(days=days)
        rows.sort(key=keys[by], reverse=True)
        ranked: list[dict[str, Any]] = []
        for index, row in enumerate(rows[: max(0, int(limit))], start=1):
            item = dict(row)
            item["rank"] = index
            item["topics"] = self._topics_for(row.get("post_id"))
            ranked.append(item)
        return ranked

    def trend(
        self,
        days: int = 30,
        platform: str | None = None,
        metric: str = "views",
        *,
        cumulative: bool = True,
    ) -> list[dict[str, Any]]:
        """按日期聚合的时间序列，供折线图使用。

        - ``cumulative=True``（默认）：每个日期取「截至当天，各内容的最新一条快照」求和，
          即账号累计值曲线，适合看整体增长。
        - ``cumulative=False``：只累加当天发布内容的最新快照，看单日产出表现。

        :param metric: ``views`` / ``likes`` / ``comments`` / ``collects`` / ``shares``
            / ``engagement``（互动总量）/ ``posts``（当日发布篇数）。
        :return: ``[{"date": "2026-10-01", "value": 123, "count": 4}, ...]``，无数据返回空列表。
        """
        if metric not in (*METRIC_KEYS, "engagement", "posts"):
            raise ValueError(
                f"不支持的指标：{metric}（可选 {', '.join((*METRIC_KEYS, 'engagement', 'posts'))}）"
            )

        today = date.today()
        start = today - timedelta(days=max(1, int(days)) - 1)
        series: list[dict[str, Any]] = []

        posts, metrics, _ = self._raw_data(platform=platform)

        if metric == "posts":
            wanted_platform = (platform or "").strip().lower()
            published_dates: list[date] = []
            for post in posts:
                if wanted_platform and str(post.get("platform") or "").lower() != wanted_platform:
                    continue
                published = _ts(post.get("published_at")) or _ts(post.get("created_at"))
                if published is not None:
                    published_dates.append(published.date())
            for offset in range((today - start).days + 1):
                current = start + timedelta(days=offset)
                count = sum(1 for d in published_dates if d == current)
                series.append({"date": current.isoformat(), "value": count, "count": count})
            return series

        if not posts:
            return []

        wanted = (platform or "").strip().lower()
        for offset in range((today - start).days + 1):
            current = start + timedelta(days=offset)
            day_end = datetime(current.year, current.month, current.day, 23, 59, 59)
            total = 0
            covered = 0
            for post in posts:
                if wanted and str(post.get("platform") or "").lower() != wanted:
                    continue
                published = _ts(post.get("published_at")) or _ts(post.get("created_at"))
                if published is None or published > day_end:
                    continue
                if not cumulative and published.date() != current:
                    continue
                latest = None
                for entry in metrics.get(int(post.get("id") or 0), []):
                    captured = _ts(entry.get("captured_at"))
                    if captured is not None and captured <= day_end:
                        latest = entry
                if latest is None:
                    continue
                covered += 1
                if metric == "engagement":
                    total += sum(int(latest.get(key) or 0) for key in INTERACTION_KEYS)
                else:
                    total += int(latest.get(metric) or 0)
            series.append({"date": current.isoformat(), "value": total, "count": covered})
        return series

    def best_publish_hours(
        self,
        days: int = 90,
        metric: str = "engagement",
        min_samples: int = 2,
    ) -> list[dict[str, Any]]:
        """按发布小时统计平均表现（移植 analyze.py 的「最佳发布时段」模式）。

        :param metric: 参与比较的指标，默认 ``engagement``（互动综合分）。
        :param min_samples: 低于该样本数的小时仍返回，但带 ``warning``。
        :return: ``[{"hour": 20, "bucket": "晚间", "count": 6, "avg_score": ...,
            "avg_views": ..., "avg_engagement_rate": ..., "weekday": "周三",
            "warning": None}, ...]``，按平均表现降序。
        """
        rows = self._snapshot_rows(days=days)
        buckets: dict[tuple[int, str], list[dict[str, Any]]] = {}
        for row in rows:
            published = _ts(row.get("published_at")) or _ts(row.get("created_at"))
            if published is None:
                continue
            buckets.setdefault((published.hour, WEEKDAYS[published.weekday()]), []).append(row)

        out: list[dict[str, Any]] = []
        for (hour, weekday), items in buckets.items():
            key = {
                "engagement": lambda r: r.get("engagement_score"),
                "engagement_rate": lambda r: r.get("engagement_rate"),
                "views": lambda r: r.get("views"),
                "likes": lambda r: r.get("likes"),
            }.get(metric, lambda r: r.get("engagement_score"))
            out.append(
                {
                    "hour": hour,
                    "bucket": _hour_bucket(hour),
                    "weekday": weekday,
                    "count": len(items),
                    "avg_score": mean([key(r) for r in items]),
                    "avg_views": mean([r.get("views") for r in items]),
                    "avg_likes": mean([r.get("likes") for r in items]),
                    "avg_engagement_rate": mean([r.get("engagement_rate") for r in items]),
                    "warning": sample_warning(len(items), min_samples, "该时段样本"),
                }
            )
        out.sort(key=lambda r: (r["avg_score"] is None, -(r["avg_score"] or 0)))
        return out

    # ------------------------------------------------------------------ #
    # 3. 归因回流
    # ------------------------------------------------------------------ #
    def attribute(
        self,
        days: int = 30,
        on_progress: Any | None = None,
        *,
        persist: bool = True,
        use_llm: bool = True,
    ) -> dict[str, Any]:
        """归因回流：分析表现最好 / 最差的内容，提炼可复用的内容结构经验。

        流程：取窗口内 Top / Bottom 内容 → 用 :class:`~stent.core.llm.LLMClient` 做定性归因
        （Hook、结构、选题、平台适配等维度，移植 postmortem-dimensions.md）→
        把结论写入画像记忆（``db.append_memory``）→ 同时返回给 UI。

        LLM 未配置或调用失败时**不抛异常**，返回 ``ok=False`` 与中文 ``message``，
        并附带本地的确定性统计结果（``stats``），UI 仍可展示数据侧结论。

        :param persist: 是否把结论写入画像记忆（默认写入）。
        :param use_llm: 传 False 时跳过 LLM，只做确定性统计与回流。
        """
        rows = self._snapshot_rows(days=days)
        if not rows:
            return {
                "ok": False,
                "message": f"最近 {days} 天还没有可用于归因的数据，请先同步指标。",
                "stats": {},
                "top": [],
                "bottom": [],
                "insight": "",
                "summary": "",
                "structure_experience": [],
                "top_reasons": [],
                "bottom_reasons": [],
                "avoid": [],
                "next_actions": [],
                "memory_saved": False,
            }

        ranked = sorted(rows, key=lambda r: r.get("engagement_score") or 0.0, reverse=True)
        top = ranked[:ATTRIBUTE_SAMPLE]
        # Bottom 只在样本足够时给出，避免 3 条数据时 top 与 bottom 重叠
        bottom = ranked[-ATTRIBUTE_SAMPLE:][::-1] if len(ranked) >= ATTRIBUTE_SAMPLE * 2 else []

        stats = self._attribution_stats(rows, top, bottom)
        result: dict[str, Any] = {
            "ok": False,
            "message": "",
            "days": days,
            "stats": stats,
            "top": [self._brief(r) for r in top],
            "bottom": [self._brief(r) for r in bottom],
            "insight": "",
            "summary": "",
            "structure_experience": [],
            "top_reasons": [],
            "bottom_reasons": [],
            "avoid": [],
            "next_actions": [],
            "memory_saved": False,
            "warnings": [w for w in (sample_warning(len(rows), 5, "窗口内内容"),) if w],
        }

        if not use_llm:
            result["message"] = "已跳过 AI 归因（use_llm=False），仅返回确定性统计。"
            return result

        if not self.llm_ready:
            result["message"] = (
                "尚未配置 AI 模型（配置中心 → API Base URL / API Key / 模型名称），"
                "暂不能做内容结构归因；下方数据统计仍可使用。"
            )
            return result

        client = self.llm
        if client is None:
            result["message"] = "AI 客户端不可用（可能未安装 openai 库），暂不能做内容结构归因。"
            return result

        _emit(on_progress, "正在分析表现最好与最差的内容…", 30)
        prompt = self._build_attribution_prompt(top, bottom, stats, days)
        try:
            reply = client.chat(
                [
                    {
                        "role": "system",
                        "content": (
                            "你是社媒内容数据分析师。只基于给定数据归因，不编造指标；"
                            "区分「内容因素」与「运气因素」（平台推荐、热点窗口）；"
                            "结论要能指导下一次创作，禁止空话。"
                            "所有输出一律使用简体中文：平台写「B 站/抖音/小红书/知乎/微博」，"
                            "指标写「播放量/点赞/评论/收藏/转发/互动率」，"
                            "不要写平台的英文 key 或英文指标字段名。"
                        ),
                    },
                    {"role": "user", "content": prompt},
                ]
            )
        except Exception as exc:  # noqa: BLE001 - LLM 失败不抛给 UI
            log.warning("归因 LLM 调用失败", exc_info=True)
            result["message"] = f"AI 归因调用失败：{exc}"
            return result

        _emit(on_progress, "归因完成，正在沉淀到账号画像…", 80)
        insight = (reply or "").strip()
        parsed = _extract_json(insight) or {}
        if insight and not parsed:
            # 模型没按 JSON 输出（或输出被尾部截断）时不当作失败：原文仍会写入
            # 画像记忆，只是结构化经验为空。记一条日志方便定位。
            log.warning("归因回复无法解析为 JSON，已按原文保留：%s", insight[:200])
        experience = self._normalize_experience(parsed.get("structure_experience"))
        summary = _localize_terms(parsed.get("summary"))
        if not summary and not experience:
            # 模型没按 JSON 输出时，退回中文化后的原文，至少让用户看到结论
            summary = _localize_terms(insight)
        result["insight"] = insight
        result["structure_experience"] = experience
        result["summary"] = summary
        result["top_reasons"] = _zh_list(parsed.get("top_reasons"))
        result["bottom_reasons"] = _zh_list(parsed.get("bottom_reasons"))
        result["avoid"] = _zh_list(parsed.get("avoid"))
        result["next_actions"] = _zh_list(parsed.get("next_actions"))

        if persist and insight:
            saved = self._persist_memory(summary, experience, days, stats)
            result["memory_saved"] = saved
        result["ok"] = True
        result["message"] = "归因完成" + ("，已写入账号画像记忆。" if result["memory_saved"] else "。")
        _emit(on_progress, result["message"], 100)
        return result

    # ------------------------------------------------------------------ #
    # 4. 内部数据访问
    # ------------------------------------------------------------------ #
    def _raw_data(
        self, days: int | None = None, platform: str | None = None
    ) -> tuple[list[dict[str, Any]], dict[int, list[dict[str, Any]]], dict[int, dict[str, Any]]]:
        """一次性读入帖子、指标与关联内容，供各分析函数在内存中聚合。

        复用 ``db.query``（数据库层），避免在业务层重复拼 SQL 连接逻辑。
        """
        since = None
        if days and int(days) > 0:
            since = (datetime.now() - timedelta(days=int(days))).strftime("%Y-%m-%d %H:%M:%S")

        sql = """SELECT p.id, p.content_id, p.platform, p.title, p.body, p.url, p.status,
                        p.published_at, p.created_at, p.last_synced,
                        c.topic AS topic
                 FROM posts p
                 LEFT JOIN contents c ON c.id = p.content_id
                 WHERE p.status = 'published'"""
        params: list[Any] = []
        if platform:
            sql += " AND lower(p.platform) = ?"
            params.append(platform.strip().lower())
        if since:
            sql += " AND COALESCE(p.published_at, p.created_at) >= ?"
            params.append(since)
        sql += " ORDER BY COALESCE(p.published_at, p.created_at) DESC"
        try:
            posts = self.db.query(sql, tuple(params))
        except Exception:  # noqa: BLE001
            log.exception("读取帖子失败")
            posts = []

        metrics: dict[int, list[dict[str, Any]]] = {}
        try:
            rows = self.db.query(
                """SELECT post_id, captured_at, views, likes, comments, collects, shares
                   FROM metrics ORDER BY post_id ASC, captured_at ASC LIMIT ?""",
                (DEFAULT_METRIC_ROW_LIMIT,),
            )
        except Exception:  # noqa: BLE001
            log.exception("读取指标失败")
            rows = []
        for row in rows:
            metrics.setdefault(int(row.get("post_id") or 0), []).append(row)

        content_ids = [int(p["content_id"]) for p in posts if p.get("content_id")]
        contents: dict[int, dict[str, Any]] = {}
        if content_ids:
            try:
                placeholders = ", ".join("?" for _ in content_ids)
                for row in self.db.query(
                    f"SELECT id, topic, summary, tags_json FROM contents WHERE id IN ({placeholders})",
                    tuple(content_ids),
                ):
                    contents[int(row["id"])] = row
            except Exception:  # noqa: BLE001
                log.debug("读取关联内容失败", exc_info=True)
        return posts, metrics, contents

    def _published_posts(
        self, days: int | None = None, platform: str | None = None
    ) -> list[dict[str, Any]]:
        """只取已发布帖子的轻量列表。"""
        posts, _, _ = self._raw_data(days=days, platform=platform)
        return posts

    def _snapshot_rows(
        self, days: int = 30, platform: str | None = None
    ) -> list[dict[str, Any]]:
        """窗口内每个已发布内容的「最近一条快照」，并附带派生指标。

        返回字段：``post_id / platform / title / url / published_at / created_at /
        topic / captured_at / views / likes / comments / collects / shares /
        interactions / engagement_rate / engagement_score``。
        """
        posts, metrics, contents = self._raw_data(days=days, platform=platform)
        rows: list[dict[str, Any]] = []
        for post in posts:
            post_id = int(post.get("id") or 0)
            latest = metrics.get(post_id, [])
            latest = latest[-1] if latest else None
            content = contents.get(int(post.get("content_id") or 0), {})
            row: dict[str, Any] = {
                "post_id": post_id,
                "content_id": post.get("content_id"),
                "platform": post.get("platform") or "",
                "title": post.get("title") or "",
                "url": post.get("url") or "",
                "body_preview": _preview(post.get("body")),
                "published_at": post.get("published_at"),
                "created_at": post.get("created_at"),
                "topic": (content.get("topic") or post.get("topic") or ""),
                "tags": _load_tags(content.get("tags_json")),
                "last_synced": post.get("last_synced") or "",
                "captured_at": None,
            }
            for key in METRIC_KEYS:
                row[key] = None
            row["interactions"] = None
            row["engagement_rate"] = None
            row["engagement_score"] = None
            if latest:
                row["captured_at"] = latest.get("captured_at")
                values = {key: _to_int(latest.get(key)) or 0 for key in METRIC_KEYS}
                row.update(values)
                row["interactions"] = sum(values[key] for key in INTERACTION_KEYS)
                row["engagement_rate"] = engagement_rate(row["interactions"], values["views"])
                row["engagement_score"] = engagement_score(**values)
            rows.append(row)
        return rows

    def _topics_for(self, post_id: Any) -> list[str]:
        """取该帖子所属内容层的主题标签（供「按主题维度」切片）。"""
        if not post_id:
            return []
        try:
            row = self.db.query_one(
                """SELECT c.topic FROM posts p LEFT JOIN contents c ON c.id = p.content_id
                   WHERE p.id = ?""",
                (post_id,),
            )
        except Exception:  # noqa: BLE001
            return []
        topic = (row or {}).get("topic") or ""
        return [t for t in re.split(r"[，,;；/\s]+", topic) if t]

    def _brief(self, row: dict[str, Any]) -> dict[str, Any]:
        """归因用的内容摘要（控制 prompt 体积）。"""
        return {
            "post_id": row.get("post_id"),
            "platform": row.get("platform"),
            "title": row.get("title"),
            "topic": row.get("topic"),
            "published_at": row.get("published_at") or row.get("created_at"),
            "views": row.get("views"),
            "likes": row.get("likes"),
            "comments": row.get("comments"),
            "collects": row.get("collects"),
            "shares": row.get("shares"),
            "engagement_rate": _round(row.get("engagement_rate")),
            "engagement_score": _round(row.get("engagement_score")),
            "body_preview": row.get("body_preview"),
        }

    # ------------------------------------------------------------------ #
    # 5. 归因辅助
    # ------------------------------------------------------------------ #
    def _attribution_stats(
        self,
        rows: list[dict[str, Any]],
        top: list[dict[str, Any]],
        bottom: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """归因用的确定性统计（LLM 只解读，不心算）。"""
        rates = [r.get("engagement_rate") for r in rows]
        avg_rate = mean(rates)
        totals = {key: sum(int(r.get(key) or 0) for r in rows) for key in METRIC_KEYS}
        return {
            "post_count": len(rows),
            "avg_engagement_rate": _round(avg_rate),
            "median_engagement_rate": _round(median(rates)),
            "aggregate_engagement_rate": _round(
                engagement_rate(
                    sum(int(r.get(key) or 0) for r in rows for key in INTERACTION_KEYS),
                    totals["views"],
                )
            ),
            "totals": totals,
            "avg_views": _round(mean([r.get("views") for r in rows])),
            "avg_collects": _round(mean([r.get("collects") for r in rows])),
            "top_avg_rate": _round(mean([r.get("engagement_rate") for r in top])),
            "bottom_avg_rate": _round(mean([r.get("engagement_rate") for r in bottom])),
            "top_lift": _round(
                safe_div(
                    mean([r.get("engagement_rate") for r in top]),
                    avg_rate,
                )
            ),
            "by_platform": [
                {
                    "platform": platform_name,
                    "post_count": len(items),
                    "avg_engagement_rate": _round(
                        mean([r.get("engagement_rate") for r in items])
                    ),
                }
                for platform_name, items in _group_platforms(rows)
            ],
            "by_topic": [
                {
                    "topic": topic,
                    "post_count": len(items),
                    "avg_engagement_rate": _round(
                        mean([r.get("engagement_rate") for r in items])
                    ),
                    "avg_views": _round(mean([r.get("views") for r in items])),
                }
                for topic, items in _group_topics(rows)
            ],
            "internal_score": self._internal_score(rows, top),
            "warnings": [
                w
                for w in (
                    sample_warning(len(rows), 5, "窗口内内容"),
                    sample_warning(len(top), ATTRIBUTE_SAMPLE, "Top 样本"),
                )
                if w
            ],
        }

    def _internal_score(
        self, rows: list[dict[str, Any]], top: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """内部加权评分 1-10（移植 analysis-framework.md 的评分框架，缺维度自动剔除）。"""
        avg_rate = mean([r.get("engagement_rate") for r in rows])
        avg_views = mean([r.get("views") for r in rows])
        platform = str(rows[0].get("platform") or "") if rows else ""
        bench = benchmark_for(platform).get("engagement") or ()

        components: dict[str, float | None] = {}
        # 互动率 vs 基准：达「平均线」记 6 分
        if avg_rate is not None and len(bench) >= 2:
            components["engagement_vs_benchmark"] = scale_to_range(
                avg_rate, bench[0] * 0.5, bench[2] if len(bench) > 2 else bench[1] * 2, 0, 10
            )
        # 播放量相对均值（离散度）
        if avg_views:
            components["relative_views"] = scale_to_range(
                safe_div(mean([r.get("views") for r in rows]), avg_views), 0.6, 1.6, 0, 10
            )
        # Top 内容相对均值
        top_rate = mean([r.get("engagement_rate") for r in top])
        if top_rate is not None and avg_rate:
            components["top_post"] = scale_to_range(safe_div(top_rate, avg_rate), 1.0, 3.0, 5, 10)
        # 发布一致性：窗口内有指标的内容占比
        if rows:
            components["consistency"] = scale_to_range(
                safe_div(float(sum(1 for r in rows if r.get("captured_at"))), float(len(rows))),
                0.0,
                1.0,
                0,
                10,
            )
        # 近半窗 vs 前半窗的互动率变化方向
        half = max(1, len(rows) // 2)
        recent = mean([r.get("engagement_rate") for r in rows[:half]])
        prior = mean([r.get("engagement_rate") for r in rows[half:]])
        change = pct_change(recent, prior)
        if change is not None:
            components["momentum"] = scale_to_range(change, -30, 30, 0, 10)

        score, used = weighted_score(components, SCORE_WEIGHTS)
        return {
            "score_1to10": round(score, 1) if score is not None else None,
            "components": {k: _round(v) for k, v in components.items()},
            "dimensions_used": used,
            "note": "缺数据的维度已剔除并重新归一化权重",
        }

    def _build_attribution_prompt(
        self,
        top: list[dict[str, Any]],
        bottom: list[dict[str, Any]],
        stats: dict[str, Any],
        days: int,
    ) -> str:
        """构造归因 prompt（含确定性统计 + Top/Bottom 明细）。"""
        def render(items: list[dict[str, Any]]) -> str:
            if not items:
                return "（样本不足，本期无对照）"
            lines = []
            for item in items:
                lines.append(
                    "- [{platform}] {title}｜主题：{topic}｜{published_at}｜"
                    "播放 {views} / 赞 {likes} / 评 {comments} / 藏 {collects} / 转 {shares}｜"
                    "互动率 {rate}%｜综合分 {score}\n  开头：{body}".format(
                        platform=_platform_zh(item.get("platform")),
                        title=item.get("title") or "(无标题)",
                        topic=item.get("topic") or "未分类",
                        published_at=item.get("published_at") or "时间未知",
                        views=item.get("views"),
                        likes=item.get("likes"),
                        comments=item.get("comments"),
                        collects=item.get("collects"),
                        shares=item.get("shares"),
                        rate=item.get("engagement_rate"),
                        score=item.get("engagement_score"),
                        body=(item.get("body_preview") or "（无正文）")[:120],
                    )
                )
            return "\n".join(lines)

        profile: dict[str, Any] = {}
        try:
            profile = self.db.get_profile() or {}
        except Exception:  # noqa: BLE001
            log.debug("读取画像失败", exc_info=True)

        return f"""请对以下最近 {days} 天的内容表现做归因，产出**可复用的内容结构经验**。

## 账号画像
定位：{profile.get("positioning") or "未填写"}
风格：{profile.get("style") or "未填写"}
受众：{profile.get("audience") or "未填写"}

## 确定性统计（已由代码算好，不要重算，可直接引用）
{json.dumps(_zh_stats(stats), ensure_ascii=False, indent=2)}

## 表现最好
{render(top)}

## 表现最差
{render(bottom)}

## 分析维度（逐维给出判断与证据，无证据就写"数据不足"）
1. Hook 力（前 3 秒 / 首屏）2. 内容结构 3. 信息密度 4. 互动引导
5. 选题与主题 6. 平台适配 7. 发布时间与节奏
注意：短视频触达天然膨胀，不要直接与图文比绝对值；区分内容因素与运气因素（平台推荐/热点窗口）。

## 输出语言（硬性要求）
所有字段值一律使用简体中文：平台写「B 站 / 抖音 / 小红书 / 知乎 / 微博」，指标写「播放量 / 点赞 / 评论 / 收藏 / 转发 / 互动率」。
上面统计里的中文标签可以直接引用；不要使用平台的英文 key 或英文指标字段名。

## 输出（严格 JSON，不要输出任何其他文字或 Markdown 围栏）
{{
  "summary": "一句话结论",
  "structure_experience": [
    {{
      "name": "公式名（≤8字）",
      "structure": "结构，用 → 连接各环节",
      "evidence": "支撑数据（引用上面的中文指标名）",
      "transferable_when": "什么类型的内容可以套用",
      "example": "用本账号领域举一个可执行示例"
    }}
  ],
  "top_reasons": ["表现好的原因，2-4 条"],
  "bottom_reasons": ["表现差的原因，0-3 条，无对照留空"],
  "avoid": ["下一条内容应避免的做法，1-3 条"],
  "next_actions": ["下一条内容立即可用的动作，2-3 条"]
}}"""

    def _normalize_experience(self, raw: Any) -> list[dict[str, str]]:
        """把 LLM 返回的结构经验归一化成固定字段的列表。"""
        items: list[Any] = raw if isinstance(raw, list) else ([raw] if isinstance(raw, dict) else [])
        out: list[dict[str, str]] = []
        for item in items:
            if isinstance(item, str):
                out.append({"name": _localize_terms(item)[:40], "structure": "", "evidence": "",
                            "transferable_when": "", "example": ""})
                continue
            if not isinstance(item, dict):
                continue
            out.append(
                {
                    "name": _localize_terms(item.get("name"))[:40],
                    "structure": _localize_terms(item.get("structure")),
                    "evidence": _localize_terms(item.get("evidence")),
                    "transferable_when": _localize_terms(item.get("transferable_when")),
                    "example": _localize_terms(item.get("example")),
                }
            )
        return [item for item in out if item["name"] or item["structure"]]

    def _persist_memory(
        self,
        summary: str,
        experience: list[dict[str, str]],
        days: int,
        stats: dict[str, Any],
    ) -> bool:
        """把归因结论写入画像记忆（供下一次创作参考）。失败返回 False，不抛异常。"""
        lines = [f"【数据分析归因 · {date.today().isoformat()} · 近 {days} 天】"]
        if experience:
            lines.append("可复用的内容结构经验：")
            for index, item in enumerate(experience, start=1):
                lines.append(
                    f"{index}. {item['name']}｜结构：{item['structure']}｜"
                    f"依据：{item['evidence']}｜适用：{item['transferable_when']}"
                )
        lines.append(
            f"数据：内容 {stats.get('post_count')} 条，平均互动率 "
            f"{stats.get('avg_engagement_rate')}%，最高/最低对照 "
            f"{stats.get('top_avg_rate')}% vs {stats.get('bottom_avg_rate')}%"
        )
        if summary:
            lines.append("分析结论：" + _localize_terms(summary))
        text = "\n".join(lines).strip()[:MEMORY_TEXT_LIMIT]
        try:
            self.db.append_memory(text)
            self.db.log_action("", "analytics_attribute", f"写入画像记忆（近 {days} 天归因）")
            return True
        except Exception:  # noqa: BLE001
            log.exception("写入画像记忆失败")
            return False


# --------------------------------------------------------------------------- #
# 模块级辅助
# --------------------------------------------------------------------------- #
def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _round(value: Any, digits: int = 2) -> float | None:
    """四舍五入保留小数；非数值原样返回 None。"""
    if value is None:
        return None
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def _preview(text: Any, limit: int = 200) -> str:
    """正文摘要（用于 prompt 与列表展示）。"""
    flat = re.sub(r"\s+", " ", str(text or "")).strip()
    return flat[:limit]


def _load_tags(raw: Any) -> list[str]:
    """解析 contents.tags_json。"""
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(t) for t in raw]
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return [str(t) for t in data] if isinstance(data, list) else []


def _group_platforms(rows: Sequence[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    """按平台分组，返回 ``[(platform, rows), ...]``。"""
    buckets: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        buckets.setdefault(str(row.get("platform") or "未知"), []).append(row)
    return sorted(buckets.items(), key=lambda kv: -len(kv[1]))


def _group_topics(rows: Sequence[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    """按主题分组（同一条内容可归入多个主题）。"""
    buckets: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        topics = [t for t in re.split(r"[，,;；/\s]+", str(row.get("topic") or "")) if t] or ["未分类"]
        for topic in topics:
            buckets.setdefault(topic, []).append(row)
    return sorted(buckets.items(), key=lambda kv: -len(kv[1]))
