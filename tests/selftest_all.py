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

# 输出全是中文：英文版 Windows 的控制台编码是 cp1252，直接 print 会
# UnicodeEncodeError 把自检打断。CI 的 windows-latest 正是这种环境。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # 被重定向成非文本流时跳过
        pass

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
    from stent.config import (
        PROVIDER_PRESETS,
        AppConfig,
        LLMConfig,
        SecretStore,
        preset_for,
        secrets,
    )
    from stent.core.db import Database
    from stent.core.db import now as db_now
    from stent.core.llm import LLMClient

    section("路径与配置")
    from stent.core.db import db as global_db

    global_db.init()  # 全局单例（创作/画像服务默认使用它）
    check("数据目录被环境变量接管", str(paths.data_dir()) == _TMP, str(paths.data_dir()))
    cfg = AppConfig.from_dict(
        {"theme": "dark", "llm": {"base_url": "x", "model": "m"}, "hotlist_platforms": ["weibo"]}
    )
    check("配置反序列化 theme", cfg.theme == "dark")
    check("配置反序列化嵌套 llm", cfg.llm.base_url == "x" and cfg.llm.model == "m")
    check("配置反序列化列表", cfg.hotlist_platforms == ["weibo"])
    cfg2 = AppConfig()
    cfg2.llm.apply_preset("deepseek")
    check("预设应用 base_url", "deepseek" in cfg2.llm.base_url)
    check("预设数量 >= 8", len(PROVIDER_PRESETS) >= 8)
    check("按 URL 反查预设", (preset_for("https://api.deepseek.com/v1/") or None) is not None)

    section("Playwright 内核目录（打包态回归）")
    # 冻结态下 Playwright 会把内核目录默认指向包内的 .local-browsers
    # （见 playwright/_impl/_transport.py 的 env.setdefault）。Stent 刻意不内置
    # Chromium，若不纠正就必然报「浏览器不可用」——而检测却认为环境就绪，
    # 连一键安装按钮都不给，用户彻底卡死。下面几条锁住这个坑。
    browsers_dir = paths.playwright_browsers_dir()
    check("内核目录落在系统位置", ".local-browsers" not in str(browsers_dir), str(browsers_dir))

    _saved_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    _saved_frozen = getattr(sys, "frozen", None)
    try:
        sys.frozen = True
        os.environ.pop("PLAYWRIGHT_BROWSERS_PATH", None)
        resolved = paths.ensure_playwright_browsers_path()
        check(
            "冻结态自动钉住系统内核目录",
            os.environ.get("PLAYWRIGHT_BROWSERS_PATH") == resolved,
            resolved,
        )
        check("冻结态不指向包内 .local-browsers", ".local-browsers" not in resolved, resolved)

        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = r"D:\custom-browsers"
        check(
            "尊重用户显式配置的内核目录",
            paths.ensure_playwright_browsers_path() == r"D:\custom-browsers",
        )
    finally:
        if _saved_path is None:
            os.environ.pop("PLAYWRIGHT_BROWSERS_PATH", None)
        else:
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = _saved_path
        if _saved_frozen is None:
            del sys.frozen
        else:
            sys.frozen = _saved_frozen

    from stent.services import publisher as _publisher

    try:
        import playwright  # noqa: F401

        _has_playwright = True
    except ImportError:
        _has_playwright = False
    _revision = _publisher.expected_chromium_revision()
    check("随包驱动声明了 Chromium 版本号", (not _has_playwright) or bool(_revision), _revision)
    check(
        "版本号对不上时判定为未就绪",
        not _publisher._has_chromium(_publisher._chromium_root(), "0000"),
    )
    _install_cmd = _publisher.chromium_install_command()
    check(
        "安装命令形如 … install chromium",
        len(_install_cmd) >= 3 and _install_cmd[-2:] == ["install", "chromium"],
        str(_install_cmd),
    )

    section("密钥存储")
    # 必须用独立命名空间：keyring 是全局的，不随 STENT_DATA_DIR 隔离，
    # 直接操作全局 secrets 会把用户真实的 API Key 覆盖成测试值再删掉。
    probe = SecretStore(service="Stent-selftest", user="llm_api_key")
    mode = probe.mode
    check("密钥后端可判定", mode in {"keyring", "dpapi", "none"}, mode)
    probe.set_api_key("sk-selftest-123456")
    got = probe.get_api_key()
    if mode == "none":
        check("无加密后端时不明文落盘", got == "", f"got={got!r}")
    else:
        check("密钥可回读", got == "sk-selftest-123456", f"mode={mode}")
    probe.clear_api_key()
    check("密钥可清除", probe.get_api_key() == "")
    check("自检未触碰真实密钥", secrets.get_api_key() != "sk-selftest-123456")

    section("SQLite 存储")
    db = Database(str(Path(_TMP) / "test.db"))
    db.init()
    cid = db.create_content(
        topic="通勤穿搭",
        platform="xiaohongshu",
        title="3 个显瘦技巧",
        titles=["a", "b"],
        body="正文内容" * 30,
        summary="简介",
        tags=["通勤", "穿搭"],
        status="ready",
        source="hot",
        source_ref="weibo:1:x",
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

    # 作品记录分页 / 搜索：作品多起来以后，前端不能再靠一次全量渲染
    for index in range(5):
        db.create_post(
            platform="bilibili",
            title=f"作品 {index}",
            url=f"https://www.bilibili.com/video/BV1xx411c7m{index}",
            status="published",
            # 放到窗口外：分页测试不该改变后面「指标总览」的期数
            published_at="2020-01-01 00:00:00",
        )
    page_one = db.list_posts(platform="bilibili", limit=2)
    page_two = db.list_posts(platform="bilibili", limit=2, offset=2)
    check("作品分页 limit 生效", len(page_one) == 2, str(len(page_one)))
    check(
        "作品分页 offset 不重叠",
        {p["id"] for p in page_one}.isdisjoint({p["id"] for p in page_two}),
    )
    check("作品按标题搜索", len(db.list_posts(query="作品 3")) == 1)
    check("作品按链接搜索", len(db.list_posts(query="BV1xx411c7m4")) == 1)
    check(
        "作品总数与筛选一致",
        db.count_posts(platform="bilibili") == 5,
        str(db.count_posts(platform="bilibili")),
    )
    check("作品搜索总数一致", db.count_posts(query="作品") == 5, str(db.count_posts(query="作品")))

    # 草稿要连「创作输入」一起存：补充要求与快捷参数是从草稿箱载回来后
    # 继续用同一组设定重新生成的上下文，丢了就等于让用户从零重来。
    draft_id = db.create_content(
        topic="副业避坑",
        platform="bilibili",
        title="t",
        body="b",
        extra="必须提到三个具体品牌；不要出现价格",
        params={"goal": "直接带货", "tone": "犀利点评", "audience": ""},
    )
    draft = db.get_content(draft_id)
    check(
        "草稿保存补充要求", draft["extra"].startswith("必须提到三个具体品牌"), repr(draft["extra"])
    )
    check("草稿保存快捷参数", draft["params"]["goal"] == "直接带货", str(draft["params"]))
    check("快捷参数保留空值项", "audience" in draft["params"], str(draft["params"]))
    db.update_content(draft_id, extra="改过了", params={"goal": "建立专业形象"})
    updated = db.get_content(draft_id)
    check("补充要求可更新", updated["extra"] == "改过了")
    check("快捷参数可更新", updated["params"] == {"goal": "建立专业形象"}, str(updated["params"]))
    # 老库没有这两列：init() 必须能补上，否则升级上来的用户一保存就报错
    legacy = Path(_TMP) / "legacy.db"
    import sqlite3 as _sqlite3

    _conn = _sqlite3.connect(legacy)
    _conn.executescript(
        "CREATE TABLE contents (id INTEGER PRIMARY KEY AUTOINCREMENT, topic TEXT NOT NULL DEFAULT '',"
        " platform TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '',"
        " titles_json TEXT NOT NULL DEFAULT '[]', body TEXT NOT NULL DEFAULT '',"
        " summary TEXT NOT NULL DEFAULT '', tags_json TEXT NOT NULL DEFAULT '[]',"
        " status TEXT NOT NULL DEFAULT 'draft', source TEXT NOT NULL DEFAULT 'manual',"
        " source_ref TEXT NOT NULL DEFAULT '', media_json TEXT NOT NULL DEFAULT '[]',"
        " created_at TEXT NOT NULL, updated_at TEXT NOT NULL);"
        "INSERT INTO contents (topic, created_at, updated_at) VALUES ('老草稿', '2026-01-01', '2026-01-01');"
    )
    _conn.commit()
    _conn.close()
    legacy_db = Database(str(legacy))
    legacy_db.init()
    legacy_columns = {r["name"] for r in legacy_db.query("PRAGMA table_info(contents)")}
    check("老库自动补 extra 列", "extra" in legacy_columns, str(sorted(legacy_columns)))
    check("老库自动补 params_json 列", "params_json" in legacy_columns)
    check("老数据读得出来", legacy_db.list_contents()[0]["topic"] == "老草稿")
    check(
        "老数据字段有默认值",
        legacy_db.list_contents()[0]["params"] == {}
        and legacy_db.list_contents()[0]["extra"] == "",
    )
    legacy_db.close()

    pid = db.create_post(
        content_id=cid,
        platform="xiaohongshu",
        title="t",
        body="b",
        status="published",
        published_at=db_now(),
    )
    check("发布记录可创建", pid > 0)
    db.add_metric(
        pid,
        {"views": 1200, "likes": 88, "comments": 6, "collects": 30, "shares": 2},
        captured_at="2026-10-01 09:00:00",
    )
    db.add_metric(
        pid,
        {"views": 1500, "likes": 99, "comments": 7, "collects": 33, "shares": 3},
        captured_at="2026-10-02 09:00:00",
    )
    latest = db.latest_metric(pid)
    check("指标取最新", latest["views"] == 1500)
    check("指标序列长度", len(db.metric_series(pid)) == 2)
    check("指标总览", len(db.metric_overview(days=30)) == 1)
    db.update_post(pid, last_synced="2000-01-01 00:00:00")
    check("待同步查询", any(p["id"] == pid for p in db.posts_needing_sync()))

    db.save_profile(
        positioning="平价通勤穿搭",
        style="口语",
        audience="25-35 女性",
        platforms=["小红书"],
        preferences="数字标题",
        memory=[],
    )
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
        SOURCES,
        HotSearchService,
        _parse_douyin_xx,
        _parse_sixty,
        _parse_strlist,
        _parse_uapis,
        _parse_xxapi,
        _unwrap,
        classify,
    )

    sixty = _parse_sixty([{"title": "标题A", "hot_value": 1304727, "link": "L"}], "weibo")
    check("60s 解析", sixty[0]["title"] == "标题A" and sixty[0]["heat"] == "130.5万", str(sixty[0]))
    xx = _parse_xxapi([{"index": 2, "title": "标题B", "url": "U", "hot": "114万"}], "weibo")
    check(
        "xxapi 解析",
        xx[0]["rank"] == 2 and xx[0]["hot" if False else "heat"] == "114万",
        str(xx[0]),
    )
    dy = _parse_douyin_xx([{"position": 1, "word": "抖音标题", "hot_value": 11246129}], "douyin")
    check("抖音解析", dy[0]["title"] == "抖音标题" and dy[0]["heat"] == "1124.6万", str(dy[0]))
    sl = _parse_strlist(["B站标题"], "bilibili")
    check(
        "字符串列表解析",
        sl[0]["title"] == "B站标题" and sl[0]["url"].startswith("https://"),
        str(sl[0]),
    )
    up = _parse_uapis(
        [
            {
                "index": 3,
                "title": "知乎标题",
                "url": "https://zhihu.com/q/1",
                "hot_value": "493 万热度",
            }
        ],
        "zhihu",
    )
    check(
        "uapis 解析",
        up[0]["rank"] == 3 and up[0]["heat"] == "493 万热度" and up[0]["title"] == "知乎标题",
        str(up[0]),
    )
    check("嵌套解包", _unwrap({"code": 200, "data": {"data": [1, 2]}}) == [1, 2])
    check(
        "uapis 能解出 list 包裹",
        _unwrap({"type": "zhihu", "list": [{"title": "x"}]}) == [{"title": "x"}],
    )
    # 60s 单源被限流时整片失败，因此除小红书外都要有第二个来源
    for key in ("weibo", "douyin", "zhihu", "bilibili", "baidu", "toutiao", "it-news"):
        check(f"{key} 至少两个数据源", len(SOURCES[key]) >= 2, str(len(SOURCES[key])))
    check("知乎有 uapis 兜底", any(s.name == "uapis" for s in SOURCES["zhihu"]))
    check("头条有 uapis 兜底", any(s.name == "uapis" for s in SOURCES["toutiao"]))
    check("垂类分类-体育", classify("亚运会闭幕夺冠") == "体育")
    check("垂类分类-科技", classify("国产 AI 芯片发布") == "科技")
    check("垂类兜底", classify("zzz qqq") == "综合")
    svc = HotSearchService()
    check("缓存 TTL 在 60-3600", 60 <= svc.cache_ttl <= 3600, str(svc.cache_ttl))
    check("平台清单含 6 个", len(svc.enabled_platforms()) >= 6)

    section("内容创作（假 LLM）")
    from stent.services.creator import (
        PLATFORM_STYLES,
        CreationRequest,
        CreatorService,
        SectionStreamParser,
        parse_tags,
        parse_titles,
    )

    check("平台体例 >= 5 个", len(PLATFORM_STYLES) >= 5)
    check("小红书标题上限 20", PLATFORM_STYLES["xiaohongshu"].title_max == 20)
    parser = SectionStreamParser()
    events = []
    sample = "【正文】\n第一段\n第二段\n【标题】\n1. 标题一\n2. 标题二\n【简介】\n这是简介\n【标签】\n#通勤 #穿搭\n"
    for chunk in [sample[i : i + 7] for i in range(0, len(sample), 7)]:
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
                yield self.text[i : i + 5]

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
    check(
        "字数提示生成",
        "平台建议" in streamed.usage_hint or "建议" in streamed.usage_hint,
        streamed.usage_hint,
    )
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
    ps.save(
        positioning="平价穿搭博主",
        style="闺蜜口语",
        audience="上班族",
        platforms=["小红书"],
        preferences="",
    )
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
    from stent.platforms.base import PublishPayload, PublishResult
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
    from stent.platforms import (
        available_platforms,
        get_adapter,
        platform_label,
        registered_platforms,
    )

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

    section("系统浏览器复用（不下载内核也能发布）")
    from stent.platforms import (
        browser_executable,
        system_browser_channel,
        system_browser_label,
    )
    from stent.services.publisher import environment_status

    try:
        import playwright  # noqa: F401

        has_playwright = True
    except ImportError:
        has_playwright = False

    channel = system_browser_channel()
    check("channel 取值合法", channel in {None, "msedge", "chrome"}, str(channel))
    if channel:
        exe = browser_executable(channel)
        label = system_browser_label(channel)
        check(f"{label} 可执行文件存在", exe is not None and exe.is_file(), str(exe))
        check("channel 有中文名", bool(label), label)
        if has_playwright:
            status = environment_status()
            check(
                "有系统浏览器时环境可用",
                status.ready,
                f"{status.code} {status.title} {status.detail}",
            )
            check("标明复用系统浏览器", status.code in {"ok", "system_browser"}, status.code)
    else:
        print("  [SKIP] 本机未检测到 Edge / Chrome")

    # 强制只用内置内核时，不应再返回系统浏览器（用户可在环境变量里指定）
    previous = os.environ.get("STENT_BROWSER_CHANNEL")
    os.environ["STENT_BROWSER_CHANNEL"] = "chromium"
    try:
        check("强制内置时忽略系统浏览器", system_browser_channel() is None)
    finally:
        if previous is None:
            os.environ.pop("STENT_BROWSER_CHANNEL", None)
        else:
            os.environ["STENT_BROWSER_CHANNEL"] = previous

    # 账号导入前的登录页判断：未登录时创作中心会跳登录页，页面上还有推荐内容
    from stent.platforms._browser import page_requires_login

    class _LoginByUrl:
        url = "https://www.zhihu.com/signin?next=%2Fcreator"

        def inner_text(self, *_args):
            return "创作中心"

    class _LoginByText:
        url = "https://www.douyin.com/creator-micro/home"

        def inner_text(self, *_args):
            return "扫码登录 / 密码登录"

    class _ContentPage:
        url = "https://www.zhihu.com/creator"

        def inner_text(self, *_args):
            return "我的文章 草稿箱 数据看板"

    check("登录页按 URL 识别", page_requires_login(_LoginByUrl()))
    check("登录页按文案识别", page_requires_login(_LoginByText()))
    check("正常内容页不误判", not page_requires_login(_ContentPage()))

    section("B 站改版适配（简介 / 分区 / dry-run 收尾）")
    from stent.platforms import bilibili as bili

    # 简介字段：B 站只有这一个长文本字段，创作页的「简介」+「正文」都要带上，
    # 否则用户在创作页单独改过的简介会被静默丢掉。
    check(
        "简介=简介+正文", bili.compose_description("我的简介", "我的正文") == "我的简介\n\n我的正文"
    )
    check("简介为空时只用正文", bili.compose_description("", "正文") == "正文")
    check("正文为空时只用简介", bili.compose_description("简介", "") == "简介")
    check("正文已包含简介时不重复", bili.compose_description("正文", "正文") == "正文")
    check(
        "简介已包含正文时不重复",
        bili.compose_description("简介里带了正文", "正文") == "简介里带了正文",
    )

    # 简介编辑器：新版是 Quill 富文本（.ql-editor），老版的 textarea 选择器一个都命中不了
    check(
        "简介选择器覆盖 Quill 编辑器",
        any("ql-editor" in sel for sel in bili.DESC_EDITOR_SELECTORS),
        str(bili.DESC_EDITOR_SELECTORS[:2]),
    )
    check("简介选择器不再只依赖 textarea", bili.DESC_EDITOR_SELECTORS[0] != bili.DESC_SELECTORS[0])
    check(
        "写入富文本时声明 user 源（否则 Vue 模型收不到）",
        "'user'" in bili.QUILL_SET_TEXT_JS,
        bili.QUILL_SET_TEXT_JS[:60],
    )
    check("支持读回校验", "getText" in bili.QUILL_READ_JS)

    # 分区：老表混了父分区 id，而投稿页只收叶子分区
    check("默认分区解析到叶子分区", bili.resolve_tid("知识") == 201, str(bili.resolve_tid("知识")))
    check("数字 tid 原样返回", bili.resolve_tid("201") == 201)
    check(
        "未知分区名退回默认",
        bili.resolve_tid("不存在的分区") == bili.resolve_tid(bili.DEFAULT_PARTITION),
    )

    _typelist = [
        {
            "name": "生活",
            "id": 160,
            "children": [{"name": "日常", "id": 21}, {"name": "搞笑", "id": 138}],
        },
        {
            "name": "知识",
            "id": 36,
            "children": [{"name": "科学科普", "id": 201}, {"name": "财经商业", "id": 207}],
        },
    ]
    _flat = bili.flatten_typelist(_typelist)
    check("分区树可摊平", len(_flat) == 4, str([c.label() for c in _flat]))
    _choice = bili.resolve_partition("知识", typelist=_typelist)
    check(
        "实时分区树优先：二级名命中",
        _choice is not None
        and (_choice.parent, _choice.child, _choice.tid) == ("知识", "科学科普", 201),
        str(_choice),
    )
    _by_child = bili.resolve_partition("财经商业", typelist=_typelist)
    check("按二级分区名命中", _by_child is not None and _by_child.tid == 207, str(_by_child))
    _by_parent = bili.resolve_partition("生活", typelist=_typelist)
    check(
        "按一级分区名命中该级首个二级",
        _by_parent is not None and _by_parent.parent == "生活",
        str(_by_parent),
    )
    _by_tid = bili.resolve_partition("", tid=138, typelist=_typelist)
    check(
        "按 tid 命中并带回父分区",
        _by_tid is not None and (_by_tid.parent, _by_tid.child) == ("生活", "搞笑"),
        str(_by_tid),
    )
    check(
        "分区下拉是两级结构",
        any("drop-f-item" in sel for sel in bili.PARTITION_PARENT_SELECTORS)
        and any("drop-t-item" in sel for sel in bili.PARTITION_CHILD_SELECTORS),
    )
    check("分区接口指向实时分区树", bili.PARTITION_API.endswith("/archive/pre"), bili.PARTITION_API)

    # 「分区已选」必须由下拉文案读回确认，不能只看「点了没报错」：
    # 下拉空着时文案是「请选择分区」，一级相同但二级点错时文案里两个名字都在，
    # 但都不是目标分区。判错会让用户在浏览器里才发现分区是空的。
    _lp = bili.BiliBiliAdapter._label_matches_partition
    check("下拉空着不算选中", not _lp("请选择分区", bili.PartitionChoice("知识", "科学科普", 201)))
    check(
        "文案含两级名算选中", _lp("知识 → 科学科普", bili.PartitionChoice("知识", "科学科普", 201))
    )
    check("文案只写二级也算选中", _lp("科学科普", bili.PartitionChoice("知识", "科学科普", 201)))
    check(
        "一级对、二级错不算选中",
        not _lp("知识 → 财经商业", bili.PartitionChoice("知识", "科学科普", 201)),
    )
    check(
        "二级同名但一级不同不算选中",
        not _lp("生活 → 日常", bili.PartitionChoice("知识", "日常", 21)),
    )
    check(
        "二级同名且一级相同才算选中", _lp("知识 → 日常", bili.PartitionChoice("知识", "日常", 21))
    )
    # 二级名里本身含别的一级名（「汽车生活」含「生活」），不能因此误判失败
    check(
        "二级名含其它一级名时不误判",
        _lp("汽车 → 汽车生活", bili.PartitionChoice("汽车", "汽车生活", 176)),
    )
    check(
        "一级名进了文案但二级不符，仍不算选中",
        not _lp("生活 → 生活", bili.PartitionChoice("生活", "日常", 21)),
    )

    # 多行字段的粘贴路径只被富文本编辑器认账；原生 textarea 上
    # ``dispatchEvent`` 一定「成功」但 value 没变，必须读回确认才能算填写成功。
    class _StubInput:
        def __init__(self, value):
            self._value = value

        def input_value(self):
            return self._value

    check(
        "读回确认：写进去了算成功",
        bili.BiliBiliAdapter._element_has_text(_StubInput("一段简介内容"), "一段简介内容") is True,
    )
    check(
        "读回确认：没写进去必须报失败",
        bili.BiliBiliAdapter._element_has_text(_StubInput(""), "一段简介内容") is False,
    )
    check(
        "读回确认：内容不符也报失败",
        bili.BiliBiliAdapter._element_has_text(_StubInput("完全不同的内容"), "一段简介内容")
        is False,
    )
    check(
        "读回确认：读不出来时不妄下结论",
        bili.BiliBiliAdapter._element_has_text(object(), "一段简介内容") is None,
    )

    # dry-run 收尾：以前 publish() 一返回就 close()，浏览器自己关掉，
    # 用户既没时间核对也来不及点发布，填的东西自然「什么都不会保存」。
    from stent.platforms._browser import DEFAULT_HOLD_SEC, hold_for_review, resolve_hold_sec

    class _FakePage:
        def __init__(self, closed_after: int = 0, signal_after: int = 0) -> None:
            self.ticks = 0
            self.closed_after = closed_after
            self.signal_after = signal_after

        def is_closed(self) -> bool:
            return bool(self.closed_after) and self.ticks >= self.closed_after

        def wait_for_timeout(self, _ms: int) -> None:
            self.ticks += 1

    class _Cancel:
        def __init__(self, after: int = 0) -> None:
            self.after = after
            self.calls = 0

        def is_set(self) -> bool:
            self.calls += 1
            return bool(self.after) and self.calls >= self.after

    check("默认保持时间足够人工确认", DEFAULT_HOLD_SEC >= 300, str(DEFAULT_HOLD_SEC))
    check(
        "hold_sec 有上下限",
        resolve_hold_sec({"hold_sec": 1}) >= 30 and resolve_hold_sec({"hold_sec": 999999}) <= 3600,
    )

    _page = _FakePage(closed_after=3)
    _msg = hold_for_review(_page, extra={}, on_progress=None)
    check(
        "用户关掉浏览器即结束", "关闭" in _msg and _page.ticks >= 3, f"{_msg} ticks={_page.ticks}"
    )

    _page2 = _FakePage()
    _msg2 = hold_for_review(
        _page2, extra={}, on_progress=None, probe=lambda: "已检测到你完成了投稿"
    )
    check("检测到用户已发布即结束", "已检测到" in _msg2, _msg2)

    _page3 = _FakePage()
    _msg3 = hold_for_review(_page3, extra={}, on_progress=None, cancel=_Cancel(after=2))
    check("主程序关闭时可中断", "中断" in _msg3, _msg3)

    _page4 = _FakePage()
    _msg4 = hold_for_review(_page4, extra={"hold_sec": 1}, on_progress=None)
    check("到上限才结束（并如实说明未提交）", "超时" in _msg4, _msg4)

    _progress: list[str] = []
    hold_for_review(
        _FakePage(closed_after=1), extra={}, on_progress=lambda m, p: _progress.append(m)
    )
    check(
        "收尾时给用户一句可读的说明",
        bool(_progress) and "浏览器" in _progress[0],
        str(_progress[:1]),
    )

    # 四个平台都要有这一步：以前只有小红书保持窗口，其余三个填完就关
    from stent.services.publisher import PublishService as _PS

    _holders = [k for k in registered_platforms() if hasattr(get_adapter(k), "_submission_signal")]
    check("四个平台都实现了发布检测", len(_holders) == 4, str(_holders))

    # 取消信号要能从发布编排一路传到适配器：签名对不上会直接 TypeError。
    # 用空 payload 触发「入参校验失败」提前返回，不碰浏览器。
    _signature_ok = True
    _signature_note = ""
    for _key in registered_platforms():
        _adapter = get_adapter(_key)
        if _adapter is None:
            continue
        try:
            _res = _adapter.publish(PublishPayload(), dry_run=True, cancel=None, on_progress=None)
            if not isinstance(_res, PublishResult):
                _signature_ok = False
                _signature_note = f"{_key} 返回了 {type(_res).__name__}"
        except TypeError as exc:
            _signature_ok = False
            _signature_note = f"{_key}: {exc}"
        except Exception as exc:  # noqa: BLE001 - 其它异常说明签名没问题，只是提前失败了
            _signature_note = f"{_key} 正常提前返回（{type(exc).__name__}）"
    check("取消信号可传入四个适配器（签名一致）", _signature_ok, _signature_note)

    check(
        "B 站适配器声明简介是独立字段",
        getattr(get_adapter("bilibili"), "uses_summary", False) is True,
    )
    check(
        "预览会单独列出 B 站简介",
        "【简介】我的简介"
        in _PS.preview_text(PublishPayload(title="t", body="b", summary="我的简介"), "bilibili"),
    )
    check(
        "其它平台预览不额外加简介行",
        "【简介】"
        not in _PS.preview_text(
            PublishPayload(title="t", body="b", summary="我的简介"), "xiaohongshu"
        ),
    )

    section("发布编排（风控断言）")
    from stent.services.publisher import PublishDenied, PublishPreparation, PublishService

    pub = PublishService(database=db)
    prep = PublishPreparation(
        platform="xiaohongshu",
        label="小红书",
        content_id=cid,
        payload=clean,
        preview="预览",
        report=report,
        limits=limits,
        supported=True,
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
        platform="xiaohongshu",
        label="小红书",
        content_id=cid,
        payload=dirty,
        preview="预览",
        report=failed_report,
        limits=limits,
        supported=True,
    )
    outcome = pub.publish(prep_bad, confirmed=True, dry_run=True)
    check("检查未通过时拒绝执行", not outcome.ok, outcome.message)

    prep_unsupported = PublishPreparation(
        platform="zhihu",
        label="知乎",
        content_id=cid,
        payload=clean,
        preview="预览",
        report=report,
        limits=limits,
        supported=False,
        support_note="适配器不可用",
    )
    outcome2 = pub.publish(prep_unsupported, confirmed=True, dry_run=True)
    check(
        "适配器不可用时给出引导而非异常",
        (not outcome2.ok) and "适配器" in outcome2.message,
        outcome2.message,
    )

    check("预览文本包含正文", "通勤" in PublishService.preview_text(clean, "xiaohongshu"))
    check("平台列表非空", len(PublishService.platforms()) == 4)
    check("适配器缺失时不抛异常", PublishService.adapter("nope") is None)

    section("发布准备链路（真实内容）")
    real_prep = pub.prepare(cid, "xiaohongshu", media=None)
    check("prepare 返回对象", real_prep.content_id == cid)
    check("prepare 生成预览", "标题" in real_prep.preview)
    check("prepare 带检查报告", real_prep.report is not None)
    check("prepare 记录适配说明", isinstance(real_prep.adapted_notes, list))
    check(
        "导出可粘贴文案",
        "通勤" in pub.export_text(real_prep) or len(pub.export_text(real_prep)) > 0,
    )

    section("数据分析")
    from stent.services.analytics import AnalyticsService

    adb = Database(str(Path(_TMP) / "test.db"))
    analytics = AnalyticsService(db=adb)
    overview = analytics.overview(days=30)
    check(
        "overview 有总量",
        "totals" in overview and overview["totals"]["views"] >= 1500,
        str(overview.get("totals")),
    )
    check("overview 有篇数", overview["post_count"] >= 1, str(overview.get("post_count")))
    check("overview 有基准", "benchmark" in overview)
    trend = analytics.trend(days=30, metric="views")
    check("trend 返回序列", isinstance(trend, list) and len(trend) >= 1, str(len(trend)))
    ranked = analytics.rank_contents(days=30, by="views")
    check("排行按播放降序", len(ranked) >= 1 and ranked[0]["rank"] == 1)
    breakdown = analytics.platform_breakdown(days=30)
    check("平台分布非空", len(breakdown) >= 1 and breakdown[0]["post_count"] >= 1)

    # 去重：同一作品的长链 / 带追踪参数链接必须只落一条
    from stent.platforms.base import AccountPost
    from stent.services import analytics as analytics_module

    if analytics.supports_account_import("bilibili"):
        first = analytics.register_post(
            "https://www.bilibili.com/video/BV1xx411c7mD?spm_id_from=333.999"
        )
        check("登记作品成功", bool(first.get("ok")), str(first))
        second = analytics.register_post("https://www.bilibili.com/video/BV1xx411c7mD/")
        check("同一作品的变体链接被判重", bool(second.get("duplicated")), str(second))
        check(
            "去重后只保留一条",
            adb.count_posts(platform="bilibili", query="BV1xx411c7mD") == 1,
            str(adb.count_posts(platform="bilibili", query="BV1xx411c7mD")),
        )

        # 账号导入：用假适配器验证「已有作品跳过、批量内也不重复写」
        real_resolve = analytics_module.resolve_adapter

        class FakeAdapter:
            supports_account_import = True

            def list_account_posts(self, *, headless: bool = True, limit: int = 50):
                return [
                    AccountPost(url="https://www.bilibili.com/video/BV1xx411c7mD"),
                    AccountPost(url="https://www.bilibili.com/video/BV1yy411c7mE"),
                ]

        analytics_module.resolve_adapter = lambda key: (
            (FakeAdapter(), "") if key == "bilibili" else real_resolve(key)
        )
        try:
            first_import = analytics.discover_account_posts("bilibili")
            second_import = analytics.discover_account_posts("bilibili")
        finally:
            analytics_module.resolve_adapter = real_resolve

        check(
            "账号导入跳过已登记作品",
            first_import.get("added") == 1 and first_import.get("skipped") == 1,
            str(first_import),
        )
        check(
            "重复导入全部跳过",
            second_import.get("added") == 0 and second_import.get("skipped") == 2,
            str(second_import),
        )
    else:
        print("  [SKIP] B 站适配器不可用，跳过去重/账号导入断言")

    check("小红书不支持账号导入", not analytics.supports_account_import("xiaohongshu"))

    hours = analytics.best_publish_hours(days=90)
    check("最佳时段返回列表", isinstance(hours, list))
    attr = analytics.attribute(days=30, use_llm=False, persist=False)
    check(
        "归因未配置 LLM 时友好返回",
        isinstance(attr, dict) and "ok" in attr,
        str(attr.get("message", ""))[:60],
    )
    check("归因不抛异常", True)
    check("归因结果含中文结论字段", "summary" in attr and "next_actions" in attr)
    from stent.services.analytics import _localize_terms, _platform_zh

    check(
        "平台 key 映射为中文",
        _platform_zh("douyin") == "抖音" and _platform_zh("bilibili") == "B 站",
    )
    localized = _localize_terms("douyin 高播放，aggregate_engagement_rate 33.85%，consistency 10.0")
    check(
        "归因术语兜底中文化",
        "抖音" in localized and "总体互动率" in localized and "douyin" not in localized,
        localized,
    )
    zh_prompt = analytics._build_attribution_prompt(
        attr.get("top") or [], attr.get("bottom") or [], attr.get("stats") or {}, 30
    )
    check(
        "归因 prompt 无英文指标键",
        not any(
            word in zh_prompt
            for word in (
                "bilibili",
                "douyin",
                "engagement_rate",
                "aggregate_engagement_rate",
                "consistency",
            )
        ),
    )
    check("归因 prompt 有中文硬性约束", "硬性要求" in zh_prompt)
    empty_svc = AnalyticsService(db=Database(str(Path(_TMP) / "empty.db")))
    empty_svc.db.init()
    check("空库 overview 不崩", empty_svc.overview(days=7)["post_count"] == 0)
    check("空库 trend 不崩", empty_svc.trend(days=7) == [])
    check("空库 rank 不崩", empty_svc.rank_contents(days=7) == [])

    section("LLM 层（无网络断言）")
    client = LLMClient(
        LLMConfig(base_url="http://127.0.0.1:9/v1", model="none", timeout=2, retries=0), "k"
    )
    ok, message = client.test_connection(timeout=2)
    check("连接失败返回中文原因", (not ok) and len(message) > 4, message[:60])
    check(
        "未配置 base_url 时报错",
        LLMClient(LLMConfig(base_url="", model="m"), "k").test_connection()[0] is False,
    )

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
        # traceback 已经打出来了，不再让 SystemExit 挂上原始异常链
        raise SystemExit(2) from None
