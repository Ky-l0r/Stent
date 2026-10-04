"""热点发现服务（企划书 3.2 模块一）。

- 聚合多平台热榜：微博、抖音、知乎、B 站、百度、头条（另附小红书热搜）
- 数据源复用 Easel 的 ``skills/shared/hotlist-apis.md``：60s API 为主，xxapi 为备源
- 多平台并发拉取，本地 SQLite 缓存 5~10 分钟（可配置）
- 支持关键词与垂类筛选

数据源降级策略（与 Easel 一致）：主源失败自动切备源，
全部失败时返回缓存中的过期数据并标注「数据可能过期」，不向用户抛异常。
"""

from __future__ import annotations

import json
import logging
import queue as queue_mod
import re
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Iterable

from ..config import config_manager
from ..core.db import db

log = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Stent/1.0"
BASE_60S = "https://60s.viki.moe"
BASE_XXAPI = "https://v2.xxapi.cn"


def _http_get_json(url: str, timeout: float) -> Any:
    """极简 JSON GET。

    这里刻意使用标准库而非 httpx：实测 httpx 0.28 在 Windows 上每次构造
    ``Client`` 需要约 3 秒（与 DNS/网络无关），会直接吃掉「热点拉取 < 2 秒」的预算。
    urllib 复用进程级 SSL 上下文，首个请求即可用。
    """
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - 固定可信数据源
        raw = response.read()
    return json.loads(raw.decode("utf-8", "replace"))


# --------------------------------------------------------------------------
# 数据结构
# --------------------------------------------------------------------------
@dataclass
class HotItem:
    platform: str
    title: str
    rank: int = 0
    url: str = ""
    heat: str = ""
    keyword: str = ""
    category: str = ""
    fetched_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "title": self.title,
            "rank": self.rank,
            "url": self.url,
            "heat": self.heat,
            "keyword": self.keyword,
            "category": self.category,
            "fetched_at": self.fetched_at,
        }


@dataclass
class PlatformResult:
    platform: str
    label: str
    items: list[HotItem] = field(default_factory=list)
    source: str = ""
    fetched_at: str = ""
    from_cache: bool = False
    stale: bool = False
    error: str = ""
    duration_ms: int = 0

    @property
    def ok(self) -> bool:
        return bool(self.items)


# --------------------------------------------------------------------------
# 平台与数据源定义
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Source:
    name: str
    url: str
    parser: str  # 解析器标识


def _s(name: str, url: str, parser: str) -> Source:
    return Source(name=name, url=url, parser=parser)


#: 平台 → 有序数据源列表（前者失败自动降级到后者）
SOURCES: dict[str, tuple[Source, ...]] = {
    "weibo": (
        _s("60s", f"{BASE_60S}/v2/weibo", "sixty"),
        _s("xxapi", f"{BASE_XXAPI}/api/weibohot", "xxapi"),
    ),
    # 抖音的 60s 端点响应不稳定（常握手超时），把稳定的 xxapi 放前面
    "douyin": (
        _s("xxapi", f"{BASE_XXAPI}/api/douyinhot", "douyin_xx"),
        _s("60s", f"{BASE_60S}/v2/douyin", "sixty"),
    ),
    "zhihu": (
        _s("60s", f"{BASE_60S}/v2/zhihu", "sixty"),
    ),
    "bilibili": (
        _s("xxapi", f"{BASE_XXAPI}/api/bilibilihot", "strlist"),
        _s("60s", f"{BASE_60S}/v2/bili", "sixty"),
    ),
    "baidu": (
        _s("xxapi", f"{BASE_XXAPI}/api/baiduhot", "xxapi"),
        _s("60s", f"{BASE_60S}/v2/baidu/hot", "sixty"),
    ),
    "toutiao": (
        _s("60s", f"{BASE_60S}/v2/toutiao", "sixty"),
    ),
    "rednote": (
        _s("60s", f"{BASE_60S}/v2/rednote", "sixty"),
    ),
}

PLATFORM_LABELS: dict[str, str] = {
    "weibo": "微博",
    "douyin": "抖音",
    "zhihu": "知乎",
    "bilibili": "B 站",
    "baidu": "百度",
    "toutiao": "今日头条",
    "rednote": "小红书",
}

#: 企划书默认聚合的平台
DEFAULT_PLATFORMS: tuple[str, ...] = ("weibo", "douyin", "zhihu", "bilibili", "baidu", "toutiao")

#: 送创作时使用的平台搜索链接模板
SEARCH_TEMPLATES: dict[str, str] = {
    "weibo": "https://s.weibo.com/weibo?q={q}",
    "douyin": "https://www.douyin.com/search/{q}",
    "zhihu": "https://www.zhihu.com/search?type=content&q={q}",
    "bilibili": "https://search.bilibili.com/all?keyword={q}",
    "baidu": "https://www.baidu.com/s?wd={q}",
    "toutiao": "https://so.toutiao.com/search?keyword={q}",
    "rednote": "https://www.xiaohongshu.com/search_result?keyword={q}",
}


# --------------------------------------------------------------------------
# 垂类分类（关键词规则，供「垂类筛选」使用；后续可升级为 LLM 打标）
# --------------------------------------------------------------------------
CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "娱乐": ("明星", "综艺", "电影", "电视剧", "演唱会", "演员", "歌手", "票房", "剧", "粉丝", "爱豆", "导演", "颁奖"),
    "科技": ("AI", "人工智能", "芯片", "手机", "华为", "苹果", "小米", "机器人", "大模型", "算力", "发布", "科技", "数码", "自动驾驶", "卫星"),
    "财经": ("股市", "基金", "房价", "经济", "GDP", "央行", "利率", "比特币", "黄金", "关税", "出口", "消费", "破产", "融资", "上市"),
    "社会": ("警方", "通报", "事故", "争议", "回应", "官方", "罚款", "判决", "网友", "救援", "教育", "学校", "医院", "老人", "地铁"),
    "体育": ("比赛", "夺冠", "亚运", "奥运", "足球", "篮球", "球员", "联赛", "冠军", "世界杯", "乒乓球", "游泳"),
    "生活": ("美食", "旅行", "穿搭", "减肥", "健康", "养生", "宠物", "家居", "育儿", "天气", "假期", "攻略", "食谱"),
    "职场": ("就业", "招聘", "工资", "加班", "裁员", "考公", "面试", "副业", "创业", "老板"),
    "国际": ("美国", "俄罗斯", "日本", "乌克兰", "以色列", "联合国", "总统", "外交", "欧洲", "韩国"),
}

CATEGORY_ORDER = tuple(CATEGORY_KEYWORDS.keys())


def classify(title: str) -> str:
    """按关键词命中数为热点打垂类标签，未命中返回「综合」。"""
    text = title or ""
    best, best_score = "综合", 0
    for category, words in CATEGORY_KEYWORDS.items():
        score = sum(1 for w in words if w in text)
        if score > best_score:
            best, best_score = category, score
    return best


# --------------------------------------------------------------------------
# 解析器
# --------------------------------------------------------------------------
def _unwrap(payload: Any) -> list[Any]:
    """兼容 60s / xxapi 的多种包裹结构，取出列表本体。"""
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    data: Any = payload.get("data", payload)
    if isinstance(data, dict):
        for key in ("data", "list", "items", "result"):
            if isinstance(data.get(key), list):
                return data[key]
        return []
    return data if isinstance(data, list) else []


def _fmt_heat(value: Any) -> str:
    """把热度值格式化为易读文本。"""
    if value is None or value == "":
        return ""
    if isinstance(value, str):
        text = value.strip()
        if text.endswith(("w", "W")):
            return f"{text[:-1]}万"
        return text
    try:
        num = float(value)
    except (TypeError, ValueError):
        return str(value)
    if num >= 100_000_000:
        return f"{num / 100_000_000:.1f}亿"
    if num >= 10_000:
        return f"{num / 10_000:.1f}万"
    return str(int(num))


def _clean(text: Any, limit: int = 300) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text[:limit]


def _parse_sixty(raw: list[Any], platform: str) -> list[dict[str, Any]]:
    """60s API：title/link/hot_value，百度为 title/url/score_desc，小红书为 title/link/score。"""
    out: list[dict[str, Any]] = []
    for idx, item in enumerate(raw):
        if not isinstance(item, dict):
            if isinstance(item, str):
                out.append({"rank": idx + 1, "title": _clean(item), "url": "", "heat": ""})
            continue
        title = _clean(item.get("title") or item.get("name") or item.get("word"))
        if not title:
            continue
        heat = item.get("hot_value")
        if heat is None:
            heat = item.get("score_desc") or item.get("score") or item.get("hot_value_desc") or ""
        out.append(
            {
                "rank": int(item.get("rank") or item.get("index") or idx + 1),
                "title": title,
                "url": str(item.get("link") or item.get("url") or ""),
                "heat": _fmt_heat(heat),
                "keyword": _clean(item.get("word_type") or "", 20),
            }
        )
    return out


def _parse_xxapi(raw: list[Any], platform: str) -> list[dict[str, Any]]:
    """xxapi：index/title/url/hot。"""
    out: list[dict[str, Any]] = []
    for idx, item in enumerate(raw):
        if isinstance(item, str):
            out.append(
                {
                    "rank": idx + 1,
                    "title": _clean(item),
                    "url": _search_url(platform, item),
                    "heat": "",
                }
            )
            continue
        if not isinstance(item, dict):
            continue
        title = _clean(item.get("title") or item.get("word") or item.get("name"))
        if not title:
            continue
        out.append(
            {
                "rank": int(item.get("index") or item.get("rank") or item.get("position") or idx + 1),
                "title": title,
                "url": str(item.get("url") or item.get("link") or _search_url(platform, title)),
                "heat": _fmt_heat(item.get("hot") or item.get("hot_value") or ""),
                "keyword": "",
            }
        )
    return out


def _parse_douyin_xx(raw: list[Any], platform: str) -> list[dict[str, Any]]:
    """抖音 xxapi：position/word/hot_value，字段名与通用结构不同。"""
    out: list[dict[str, Any]] = []
    for idx, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        title = _clean(item.get("word") or item.get("sentence") or item.get("title"))
        if not title:
            continue
        out.append(
            {
                "rank": int(item.get("position") or idx + 1),
                "title": title,
                "url": _search_url("douyin", title),
                "heat": _fmt_heat(item.get("hot_value") or ""),
                "keyword": _clean(item.get("word_type") or "", 20),
            }
        )
    return out


def _parse_strlist(raw: list[Any], platform: str) -> list[dict[str, Any]]:
    """纯字符串列表（xxapi 的 B 站热榜）。"""
    out: list[dict[str, Any]] = []
    for idx, item in enumerate(raw):
        title = _clean(item if isinstance(item, str) else (item or {}).get("title"))
        if not title:
            continue
        out.append(
            {
                "rank": idx + 1,
                "title": title,
                "url": _search_url(platform, title),
                "heat": "",
            }
        )
    return out


PARSERS: dict[str, Callable[[list[Any], str], list[dict[str, Any]]]] = {
    "sixty": _parse_sixty,
    "xxapi": _parse_xxapi,
    "douyin_xx": _parse_douyin_xx,
    "strlist": _parse_strlist,
}


def _search_url(platform: str, keyword: str) -> str:
    from urllib.parse import quote

    tpl = SEARCH_TEMPLATES.get(platform)
    return tpl.format(q=quote(keyword)) if tpl else ""


# --------------------------------------------------------------------------
# 服务
# --------------------------------------------------------------------------
class HotSearchService:
    """聚合热榜服务。所有方法线程安全，可被 UI 工作线程直接调用。"""

    def __init__(self, timeout: float = 6.0, max_workers: int = 6) -> None:
        self.timeout = timeout
        self.max_workers = max_workers
        self._lock = threading.Lock()

    # -- 数据源竞速 ------------------------------------------------------
    def _fetch_source(self, platform: str, source: Source) -> list[HotItem]:
        """请求单个数据源并解析；失败抛异常。"""
        parsed = PARSERS[source.parser](_unwrap(_http_get_json(source.url, self.timeout)), platform)
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return [
            HotItem(
                platform=platform,
                title=row["title"],
                rank=row.get("rank") or idx + 1,
                url=row.get("url", ""),
                heat=row.get("heat", ""),
                keyword=row.get("keyword", ""),
                category=classify(row["title"]),
                fetched_at=stamp,
            )
            for idx, row in enumerate(parsed)
        ]

    def _race_sources(
        self, platform: str, sources: tuple[Source, ...]
    ) -> tuple[tuple[list[HotItem], str] | None, list[str]]:
        """并发请求同一平台的多个数据源，取最先成功者（降低尾延迟）。"""
        errors: list[str] = []
        if len(sources) == 1:
            # 单源平台（知乎、头条）没有降级目标，多试一次显著提升成功率
            source = sources[0]
            last_error = ""
            for attempt in range(2):
                try:
                    items = self._fetch_source(platform, source)
                except Exception as exc:  # noqa: BLE001
                    last_error = type(exc).__name__
                    if attempt == 0:
                        time.sleep(0.4)
                    continue
                if items:
                    return (items, source.name), errors
                last_error = "返回空数据"
                break
            return None, [f"{source.name}：{last_error}"]

        queue: "queue_mod.Queue[tuple[Source, list[HotItem] | None, str]]" = queue_mod.Queue()
        for source in sources:
            threading.Thread(
                target=self._source_worker,
                args=(queue, platform, source),
                daemon=True,
                name=f"hot-{platform}-{source.name}",
            ).start()

        deadline = time.monotonic() + self.timeout + 3
        for _ in range(len(sources)):
            remain = deadline - time.monotonic()
            if remain <= 0:
                errors.append("数据源响应超时")
                break
            try:
                source, items, err = queue.get(timeout=remain)
            except queue_mod.Empty:
                errors.append("数据源响应超时")
                break
            if err:
                errors.append(f"{source.name}：{err}")
            elif items:
                return (items, source.name), errors
            else:
                errors.append(f"{source.name} 返回空数据")
        return None, errors

    def _source_worker(
        self,
        queue: "queue_mod.Queue[tuple[Source, list[HotItem] | None, str]]",
        platform: str,
        source: Source,
    ) -> None:
        try:
            items = self._fetch_source(platform, source)
            queue.put((source, items, ""))
        except Exception as exc:  # noqa: BLE001
            queue.put((source, None, type(exc).__name__))

    # -- 缓存参数 --------------------------------------------------------
    @property
    def cache_ttl(self) -> int:
        ttl = int(getattr(config_manager.config, "hotlist_cache_ttl", 300) or 300)
        return max(60, min(ttl, 3600))

    def enabled_platforms(self) -> list[str]:
        platforms = list(getattr(config_manager.config, "hotlist_platforms", None) or DEFAULT_PLATFORMS)
        return [p for p in platforms if p in SOURCES] or list(DEFAULT_PLATFORMS)

    # -- 单平台 ----------------------------------------------------------
    def fetch_platform(self, platform: str, *, force: bool = False) -> PlatformResult:
        """拉取单个平台热榜：缓存 → 主源 → 备源 → 过期缓存。"""
        label = PLATFORM_LABELS.get(platform, platform)
        started = datetime.now()

        if not force:
            items, fetched_at = db.load_hot_items(platform, self.cache_ttl)
            if items:
                return PlatformResult(
                    platform=platform,
                    label=label,
                    items=[self._row_to_item(r) for r in items],
                    source="cache",
                    fetched_at=fetched_at,
                    from_cache=True,
                    duration_ms=int((datetime.now() - started).total_seconds() * 1000),
                )

        sources = SOURCES.get(platform)
        if not sources:
            return PlatformResult(platform=platform, label=label, error=f"暂不支持该平台：{platform}")

        winner, errors = self._race_sources(platform, sources)
        if winner is not None:
            items, source_name = winner
            stamp = items[0].fetched_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            db.save_hot_items(platform, [i.as_dict() for i in items])
            return PlatformResult(
                platform=platform,
                label=label,
                items=items,
                source=source_name,
                fetched_at=stamp,
                duration_ms=int((datetime.now() - started).total_seconds() * 1000),
            )

        # 全部失败：退回过期缓存，诚实标注
        stale_items, stale_at = db.load_hot_items(platform, max_age_seconds=10**9)
        if stale_items:
            return PlatformResult(
                platform=platform,
                label=label,
                items=[self._row_to_item(r) for r in stale_items],
                source="stale-cache",
                fetched_at=stale_at,
                from_cache=True,
                stale=True,
                error="实时源不可用，展示的是历史缓存",
                duration_ms=int((datetime.now() - started).total_seconds() * 1000),
            )
        return PlatformResult(
            platform=platform,
            label=label,
            error="；".join(errors) or "数据源不可用",
            duration_ms=int((datetime.now() - started).total_seconds() * 1000),
        )

    # -- 多平台并发 ------------------------------------------------------
    def fetch_all(
        self,
        platforms: Iterable[str] | None = None,
        *,
        force: bool = False,
        on_progress: Callable[[str, int], None] | None = None,
    ) -> dict[str, PlatformResult]:
        """并发拉取多个平台（企划书 3.2：多平台并发拉取，目标 < 2 秒）。

        命中本地缓存时**不创建 HTTP 客户端**，整条路径只读 SQLite，毫秒级返回。
        """
        targets = [p for p in (platforms or self.enabled_platforms()) if p in SOURCES]
        results: dict[str, PlatformResult] = {}
        if not targets:
            return results

        # 1) 先吃缓存
        pending: list[str] = []
        if force:
            pending = list(targets)
        else:
            for platform in targets:
                items, fetched_at = db.load_hot_items(platform, self.cache_ttl)
                if items:
                    results[platform] = PlatformResult(
                        platform=platform,
                        label=PLATFORM_LABELS.get(platform, platform),
                        items=[self._row_to_item(r) for r in items],
                        source="cache",
                        fetched_at=fetched_at,
                        from_cache=True,
                    )
                else:
                    pending.append(platform)

        # 2) 只对未命中的平台发起网络请求
        if pending:
            total = len(pending)
            done = 0
            with ThreadPoolExecutor(max_workers=min(self.max_workers, total)) as pool:
                futures = {
                    pool.submit(self.fetch_platform, platform, force=True): platform
                    for platform in pending
                }
                for future in as_completed(futures):
                    platform = futures[future]
                    try:
                        results[platform] = future.result()
                    except Exception as exc:  # noqa: BLE001
                        results[platform] = PlatformResult(
                            platform=platform,
                            label=PLATFORM_LABELS.get(platform, platform),
                            error=f"{type(exc).__name__}: {exc}",
                        )
                    done += 1
                    if on_progress:
                        try:
                            on_progress(PLATFORM_LABELS.get(platform, platform), int(done / total * 100))
                        except Exception:
                            pass

        # 按用户配置顺序返回
        return {p: results[p] for p in targets if p in results}

    # -- 筛选 ------------------------------------------------------------
    @staticmethod
    def filter_items(
        results: dict[str, PlatformResult],
        *,
        keyword: str = "",
        category: str = "",
        platforms: Iterable[str] | None = None,
        limit: int = 500,
    ) -> list[HotItem]:
        """关键词 + 垂类 + 平台筛选，统一按「平台 → 排名」排序。"""
        allow = set(platforms) if platforms else None
        key = (keyword or "").strip().lower()
        cat = (category or "").strip()
        out: list[HotItem] = []
        for platform, result in results.items():
            if allow is not None and platform not in allow:
                continue
            for item in result.items:
                if key and key not in item.title.lower() and key not in (item.keyword or "").lower():
                    continue
                if cat and cat not in ("全部", "综合筛选") and item.category != cat:
                    continue
                out.append(item)
                if len(out) >= limit:
                    return out
        return out

    @staticmethod
    def categories(results: dict[str, PlatformResult]) -> list[tuple[str, int]]:
        """统计各垂类数量，供筛选下拉框使用。"""
        counts: dict[str, int] = {}
        for result in results.values():
            for item in result.items:
                counts[item.category or "综合"] = counts.get(item.category or "综合", 0) + 1
        ordered = [(c, counts.pop(c)) for c in CATEGORY_ORDER if c in counts]
        ordered += sorted(counts.items(), key=lambda kv: -kv[1])
        return ordered

    @staticmethod
    def to_draft_seed(item: HotItem, target_platform: str = "") -> dict[str, Any]:
        """「一键送入创作」：把热点转换为创作输入。"""
        return {
            "topic": item.title,
            "source": "hot",
            "source_ref": f"{item.platform}:{item.rank}:{item.title}",
            "source_platform": item.platform,
            "target_platform": target_platform,
            "heat": item.heat,
            "url": item.url,
            "category": item.category,
        }

    @staticmethod
    def _row_to_item(row: dict[str, Any]) -> HotItem:
        title = row.get("title", "")
        return HotItem(
            platform=row.get("platform", ""),
            title=title,
            rank=int(row.get("rank") or 0),
            url=row.get("url", ""),
            heat=row.get("heat", ""),
            keyword=row.get("keyword", ""),
            category=classify(title),
            fetched_at=row.get("fetched_at", ""),
        )


hotsearch_service = HotSearchService()
