"""Stent 服务层端到端自检（不依赖网络、不打开浏览器）。

运行::

    python tests/selftest_all.py

覆盖：配置与密钥、SQLite 全表、热榜解析、创作流式解析、发布前检查、
发布编排（含「未经确认必须拒绝」的风控断言）、数据分析、账号画像、平台注册表。
"""

from __future__ import annotations

import os
import sys
import tempfile
import traceback
from pathlib import Path

# 必须在 import stent 之前设置，让所有路径落到临时目录
_TMP = tempfile.mkdtemp(prefix="stent-selftest-")
os.environ["STENT_DATA_DIR"] = _TMP
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PASS = 0
FAIL = 0
FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
    else:
        FAIL += 1
        FAILURES.append(f"{name} {detail}")
        print(f"  [FAIL] {name} {detail}")


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def main() -> int:
    from stent import paths
    from stent.config import AppConfig, LLMConfig, PROVIDER_PRESETS, preset_for, secrets
    from stent.core.db import Database, now as db_now
    from stent.core.llm import LLMClient

    section("路径与配置")
    from stent.core.db import db as global_db

    global_db.init()  # 全局单例（创作/画像服务默认使用它）
    check("数据目录被环境变量接管", str(paths.data_dir()) == _TMP, str(paths.data_dir()))
    cfg = AppConfig.from_dict({"theme": "dark", "llm": {"base_url": "x", "model": "m"}, "hotlist_platforms": ["weibo"]})
    check("配置反序列化 theme", cfg.theme == "dark")
    check("配置反序列化嵌套 llm", cfg.llm.base_url == "x" and cfg.llm.model == "m")
    check("配置反序列化列表", cfg.hotlist_platforms == ["weibo"])
    cfg2 = AppConfig()
    cfg2.llm.apply_preset("deepseek")
    check("预设应用 base_url", "deepseek" in cfg2.llm.base_url)
    check("预设数量 >= 8", len(PROVIDER_PRESETS) >= 8)
    check("按 URL 反查预设", (preset_for("https://api.deepseek.com/v1/") or None) is not None)

    section("密钥存储")
    mode = secrets.mode
    check("密钥后端可判定", mode in {"keyring", "dpapi", "none"}, mode)
    secrets.set_api_key("sk-selftest-123456")
    got = secrets.get_api_key()
    if mode == "none":
        check("无加密后端时不明文落盘", got == "", f"got={got!r}")
    else:
        check("密钥可回读", got == "sk-selftest-123456", f"mode={mode}")
    secrets.clear_api_key()
    check("密钥可清除", secrets.get_api_key() == "")

    section("SQLite 存储")
    db = Database(str(Path(_TMP) / "test.db"))
    db.init()
    cid = db.create_content(
        topic="通勤穿搭", platform="xiaohongshu", title="3 个显瘦技巧",
        titles=["a", "b"], body="正文内容" * 30, summary="简介", tags=["通勤", "穿搭"],
        status="ready", source="hot", source_ref="weibo:1:x",
    )
    check("内容可创建", cid > 0)
    content = db.get_content(cid)
    check("内容可读取", content is not None and content["topic"] == "通勤穿搭")
    check("标题列表往返", content["titles"] == ["a", "b"])
    check("标签往返", content["tags"] == ["通勤", "穿搭"])
    db.update_content(cid, status="published", title="新标题")
    check("内容可更新", db.get_content(cid)["title"] == "新标题")
    check("内容可列表", len(db.list_contents()) == 1)
    check("按状态过滤", len(db.list_contents(status="published")) == 1)

    pid = db.create_post(content_id=cid, platform="xiaohongshu", title="t", body="b", status="published", published_at=db_now())
    check("发布记录可创建", pid > 0)
    db.add_metric(pid, {"views": 1200, "likes": 88, "comments": 6, "collects": 30, "shares": 2}, captured_at="2026-10-01 09:00:00")
    db.add_metric(pid, {"views": 1500, "likes": 99, "comments": 7, "collects": 33, "shares": 3}, captured_at="2026-10-02 09:00:00")
    latest = db.latest_metric(pid)
    check("指标取最新", latest["views"] == 1500)
    check("指标序列长度", len(db.metric_series(pid)) == 2)
    check("指标总览", len(db.metric_overview(days=30)) == 1)
    db.update_post(pid, last_synced="2000-01-01 00:00:00")
    check("待同步查询", any(p["id"] == pid for p in db.posts_needing_sync()))

    db.save_profile(positioning="平价通勤穿搭", style="口语", audience="25-35 女性",
                    platforms=["小红书"], preferences="数字标题", memory=[])
    profile = db.get_profile()
    check("画像定位往返", profile["positioning"] == "平价通勤穿搭")
    check("画像平台往返", profile["platforms"] == ["小红书"])
    db.append_memory("标题带数字表现更好")
    check("画像记忆追加", len(db.get_profile()["memory"]) == 1)

    db.save_hot_items("weibo", [{"rank": 1, "title": "测试热搜", "url": "u", "heat": "1万"}])
    items, _fetched = db.load_hot_items("weibo", 600)
    check("热榜缓存往返", len(items) == 1 and items[0]["title"] == "测试热搜")
    db.set_kv("k", {"a": 1})
    check("KV 往返", db.get_kv("k") == {"a": 1})
    db.log_action("weibo", "test", "detail", post_id=pid)
    check("审计日志", len(db.recent_logs()) == 1)
    db.close()

    section("热榜解析（离线，用真实响应样本）")
    from stent.services.hotsearch import (
        HotSearchService, _parse_douyin_xx, _parse_sixty, _parse_strlist, _parse_xxapi, _unwrap, classify,
    )
    sixty = _parse_sixty([{"title": "标题A", "hot_value": 1304727, "link": "L"}], "weibo")
    check("60s 解析", sixty[0]["title"] == "标题A" and sixty[0]["heat"] == "130.5万", str(sixty[0]))
    xx = _parse_xxapi([{"index": 2, "title": "标题B", "url": "U", "hot": "114万"}], "weibo")
    check("xxapi 解析", xx[0]["rank"] == 2 and xx[0]["hot" if False else "heat"] == "114万", str(xx[0]))
    dy = _parse_douyin_xx([{"position": 1, "word": "抖音标题", "hot_value": 11246129}], "douyin")
    check("抖音解析", dy[0]["title"] == "抖音标题" and dy[0]["heat"] == "1124.6万", str(dy[0]))
    sl = _parse_strlist(["B站标题"], "bilibili")
    check("字符串列表解析", sl[0]["title"] == "B站标题" and sl[0]["url"].startswith("https://"), str(sl[0]))
    check("嵌套解包", _unwrap({"code": 200, "data": {"data": [1, 2]}}) == [1, 2])
    check("垂类分类-体育", classify("亚运会闭幕夺冠") == "体育")
    check("垂类分类-科技", classify("国产 AI 芯片发布") == "科技")
    check("垂类兜底", classify("zzz qqq") == "综合")
    svc = HotSearchService()
    check("缓存 TTL 在 60-3600", 60 <= svc.cache_ttl <= 3600, str(svc.cache_ttl))
    check("平台清单含 6 个", len(svc.enabled_platforms()) >= 6)

    section("内容创作（假 LLM）")
    from stent.services.creator import (
        PLATFORM_STYLES, CreationRequest, CreatorService, SectionStreamParser, parse_tags, parse_titles,
    )
    check("平台体例 >= 5 个", len(PLATFORM_STYLES) >= 5)
    check("小红书标题上限 20", PLATFORM_STYLES["xiaohongshu"].title_max == 20)
    parser = SectionStreamParser()
    events = []
    sample = "【正文】\n第一段\n第二段\n【标题】\n1. 标题一\n2. 标题二\n【简介】\n这是简介\n【标签】\n#通勤 #穿搭\n"
    for chunk in [sample[i:i + 7] for i in range(0, len(sample), 7)]:
        events.extend(parser.feed(chunk))
    events.extend(parser.finish())
    result = parser.to_result("xiaohongshu")
    check("流式解析正文", "第一段" in result.body and "第二段" in result.body, repr(result.body))
    check("流式解析标题", result.titles == ["标题一", "标题二"], str(result.titles))
    check("流式解析简介", result.summary == "这是简介", repr(result.summary))
    check("流式解析标签", result.tags == ["通勤", "穿搭"], str(result.tags))
    check("分段事件非空", len(events) > 5, str(len(events)))
    check("标题解析去序号", parse_titles("1. A\n- B\nC") == ["A", "B", "C"])
    check("标签解析多分隔", parse_tags("#a #b, c、d") == ["a", "b", "c", "d"])

    class FakeLLM:
        def __init__(self, text: str) -> None:
            self.text = text

        def chat(self, messages, **kwargs):  # noqa: ANN001
            return self.text

        def stream_chat(self, messages, **kwargs):  # noqa: ANN001
            for i in range(0, len(self.text), 5):
                yield self.text[i:i + 5]

    creator = CreatorService()
    req = CreationRequest(topic="通勤穿搭", platform="xiaohongshu")
    messages = creator.build_messages(req)
    check("prompt 含平台规范", "小红书" in messages[0].content)
    check("prompt 含输出格式", "【正文】" in messages[0].content)
    check("prompt 含合规红线", "极限词" in messages[0].content and "AI 味" in messages[0].content)
    check("prompt 含主题", "通勤穿搭" in messages[1].content)
    streamed = creator.create_stream(req, FakeLLM(sample))
    check("流式创作正文", "第一段" in streamed.body)
    check("流式创作标题数", len(streamed.titles) == 2)
    check("字数提示生成", "平台建议" in streamed.usage_hint or "建议" in streamed.usage_hint, streamed.usage_hint)
    parsed = creator.parse("随便一段没有标记的文字", "zhihu")
    check("无标记时退化为正文", parsed.body.startswith("随便一段"))
    multi = creator.create_multi(req, ["xiaohongshu", "douyin"], FakeLLM(sample))
    check("多平台成稿", len(multi) == 2, str(list(multi)))

    section("账号画像")
    from stent.services.profile import DIMENSIONS, ProfileService
    profile_db = Database(str(Path(_TMP) / "test.db"))
    profile_db.init()
    ps = ProfileService(database=profile_db)
    check("六维定义完整", len(DIMENSIONS) == 6, str(len(DIMENSIONS)))
    ps.save(positioning="平价穿搭博主", style="闺蜜口语", audience="上班族", platforms=["小红书"], preferences="")
    check("画像已配置判定", ps.is_configured())
    check("完整度计算", 0 < ps.completeness() <= 100, str(ps.completeness()))
    prompt = ps.to_prompt(platform="小红书")
    check("画像 prompt 含标题行", "STENT 账号画像" in prompt)
    check("画像 prompt 含维度", "【定位】" in prompt and "【风格】" in prompt)
    check("画像 prompt 含平台", "小红书" in prompt)
    ps.append_memory("数字标题更好")
    check("记忆进入 prompt", "数字标题更好" in ps.to_prompt())
    check("Markdown 导出", "identity.md" in ps.export_markdown())
    draft = ProfileService._parse_draft('```json\n{"positioning":"p","style":"s"}\n```')
    check("画像草稿解析容错", draft["positioning"] == "p", str(draft))

    section("发布前检查")
    from stent.platforms import platform_limits
    from stent.platforms.base import PublishPayload
    from stent.services.precheck import PrecheckService
    pre = PrecheckService()
    limits = platform_limits("xiaohongshu")
    check("小红书 limits 合理", limits.title_max == 20 and limits.requires_media, str(limits))
    clean = PublishPayload(
        title="通勤显瘦的3个技巧",
        body="今天分享一下我通勤穿搭的心得。" * 12,
        tags=["通勤穿搭", "平价好物", "显瘦"],
        media=[],
    )
    report = pre.check(clean, limits, platform="xiaohongshu")
    check("检查返回报告", hasattr(report, "items") and hasattr(report, "passed"))
    check("报告有分数", 0 <= report.score <= 100, str(report.score))
    dirty = PublishPayload(
        title="全网最好的祛斑神器，100%有效，加微信 abc123 领取",
        body="国家级专家推荐，根治色斑，永不复发。微信：abc123，链接 http://spam.example.com",
        tags=[],
        media=[],
    )
    bad_report = pre.check(dirty, limits, platform="xiaohongshu")
    check("风险内容被拦截", not bad_report.passed, f"score={bad_report.score}")
    risk_codes = {i.code for i in bad_report.errors()}
    check("命中风险规则", len(risk_codes) >= 3, str(sorted(risk_codes)[:10]))
    check(
        "命中极限词/医疗/导流类规则",
        any(
            token in code
            for code in risk_codes
            for token in ("extreme", "sensitive", "medical", "contact", "link", "diversion")
        ),
        str(sorted(risk_codes)),
    )
    check("报告可读文本", "检查" in bad_report.to_text() or len(bad_report.to_text()) > 10)
    check("空内容不崩溃", pre.check(PublishPayload(), limits, platform="xiaohongshu") is not None)

    section("平台注册表")
    from stent.platforms import available_platforms, get_adapter, platform_label, registered_platforms
    check("登记 4 个平台", len(registered_platforms()) == 4, str(registered_platforms()))
    check("标签正确", platform_label("xiaohongshu") == "小红书")
    check("未知平台返回 None", get_adapter("not-exist") is None)
    check("空 key 返回 None", get_adapter("") is None)
    adapters = [get_adapter(k) for k in registered_platforms()]
    loaded = [a for a in adapters if a is not None]
    if loaded:
        check("适配器类名正确", any(type(a).__name__ == "XiaoHongShuAdapter" for a in loaded))
        check("适配器有 limits", all(a.limits.title_max > 0 for a in loaded))
        check("适配器 key 唯一", len({a.key for a in loaded}) == len(loaded))
    else:
        print("  [SKIP] 平台适配器未加载（playwright 未安装或模块缺失）")
    check("available_platforms 是列表", isinstance(available_platforms(), list))

    section("发布编排（风控断言）")
    from stent.services.publisher import PublishDenied, PublishPreparation, PublishService
    pub = PublishService(database=db)
    prep = PublishPreparation(
        platform="xiaohongshu", label="小红书", content_id=cid,
        payload=clean, preview="预览", report=report, limits=limits, supported=True,
    )
    raised = False
    try:
        pub.publish(prep, confirmed=False)
    except PublishDenied:
        raised = True
    except Exception as exc:  # noqa: BLE001
        check("未确认时抛出 PublishDenied", False, f"抛出了 {type(exc).__name__}")
    check("未经人工确认必须拒绝发布", raised)

    failed_report = type(report)() if False else bad_report
    prep_bad = PublishPreparation(
        platform="xiaohongshu", label="小红书", content_id=cid,
        payload=dirty, preview="预览", report=failed_report, limits=limits, supported=True,
    )
    outcome = pub.publish(prep_bad, confirmed=True, dry_run=True)
    check("检查未通过时拒绝执行", not outcome.ok, outcome.message)

    prep_unsupported = PublishPreparation(
        platform="zhihu", label="知乎", content_id=cid,
        payload=clean, preview="预览", report=report, limits=limits, supported=False,
        support_note="适配器不可用",
    )
    outcome2 = pub.publish(prep_unsupported, confirmed=True, dry_run=True)
    check("适配器不可用时给出引导而非异常", (not outcome2.ok) and "适配器" in outcome2.message, outcome2.message)

    check("预览文本包含正文", "通勤" in PublishService.preview_text(clean, "xiaohongshu"))
    check("平台列表非空", len(PublishService.platforms()) == 4)
    check("适配器缺失时不抛异常", PublishService.adapter("nope") is None)

    section("发布准备链路（真实内容）")
    real_prep = pub.prepare(cid, "xiaohongshu", media=None)
    check("prepare 返回对象", real_prep.content_id == cid)
    check("prepare 生成预览", "标题" in real_prep.preview)
    check("prepare 带检查报告", real_prep.report is not None)
    check("prepare 记录适配说明", isinstance(real_prep.adapted_notes, list))
    check("导出可粘贴文案", "通勤" in pub.export_text(real_prep) or len(pub.export_text(real_prep)) > 0)

    section("数据分析")
    from stent.services.analytics import AnalyticsService
    adb = Database(str(Path(_TMP) / "test.db"))
    analytics = AnalyticsService(db=adb)
    overview = analytics.overview(days=30)
    check("overview 有总量", "totals" in overview and overview["totals"]["views"] >= 1500, str(overview.get("totals")))
    check("overview 有篇数", overview["post_count"] >= 1, str(overview.get("post_count")))
    check("overview 有基准", "benchmark" in overview)
    trend = analytics.trend(days=30, metric="views")
    check("trend 返回序列", isinstance(trend, list) and len(trend) >= 1, str(len(trend)))
    ranked = analytics.rank_contents(days=30, by="views")
    check("排行按播放降序", len(ranked) >= 1 and ranked[0]["rank"] == 1)
    breakdown = analytics.platform_breakdown(days=30)
    check("平台分布非空", len(breakdown) >= 1 and breakdown[0]["post_count"] >= 1)
    hours = analytics.best_publish_hours(days=90)
    check("最佳时段返回列表", isinstance(hours, list))
    attr = analytics.attribute(days=30, use_llm=False, persist=False)
    check("归因未配置 LLM 时友好返回", isinstance(attr, dict) and "ok" in attr, str(attr.get("message", ""))[:60])
    check("归因不抛异常", True)
    empty_svc = AnalyticsService(db=Database(str(Path(_TMP) / "empty.db")))
    empty_svc.db.init()
    check("空库 overview 不崩", empty_svc.overview(days=7)["post_count"] == 0)
    check("空库 trend 不崩", empty_svc.trend(days=7) == [])
    check("空库 rank 不崩", empty_svc.rank_contents(days=7) == [])

    section("LLM 层（无网络断言）")
    client = LLMClient(LLMConfig(base_url="http://127.0.0.1:9/v1", model="none", timeout=2, retries=0), "k")
    ok, message = client.test_connection(timeout=2)
    check("连接失败返回中文原因", (not ok) and len(message) > 4, message[:60])
    check("未配置 base_url 时报错", LLMClient(LLMConfig(base_url="", model="m"), "k").test_connection()[0] is False)

    print(f"\n===== 自检结果：{PASS} 通过 / {FAIL} 失败 =====")
    if FAILURES:
        print("失败项：")
        for item in FAILURES:
            print("  -", item)
    print(f"临时数据目录：{_TMP}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        raise SystemExit(2)
