"""归因中文化回归自检：prompt / 展示字段 / 画像记忆都不再出现英文标识。

用假 LLM 返回「中英混排」的归因 JSON，验证整条链路都会被中文化。

运行::

    python tests/selftest_attribution_zh.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="stent-zh-")
os.environ["STENT_DATA_DIR"] = _TMP
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stent.core.db import Database  # noqa: E402
from stent.services import analytics as A  # noqa: E402

# 会被判定为「不该出现在面向用户文本里」的英文标识
FORBIDDEN = (
    "bilibili", "douyin", "xiaohongshu", "zhihu", "weibo", "weixin_mp",
    "views", "likes", "comments", "collects", "shares",
    "engagement_rate", "engagement_score", "consistency", "momentum",
    "aggregate_engagement_rate", "post_count", "top_lift", "by_platform",
)

SAMPLES = [
    ("bilibili", "崩坏3 MMD 卡点", 12000, 900, 60, 1200, 40),
    ("douyin", "星铁 MMD 三连切", 86000, 1500, 90, 900, 30),
    ("bilibili", "原神 MMD 循环", 3000, 200, 8, 120, 5),
    ("douyin", "角色混剪", 20000, 300, 12, 80, 6),
    ("xiaohongshu", "MMD 花絮", 900, 40, 3, 20, 1),
    ("zhihu", "二创思路", 500, 20, 2, 10, 1),
]

REPLY = json.dumps(
    {
        "summary": "收藏驱动、触达受限：douyin 高播放来自推荐，bilibili 互动率 51.56%",
        "structure_experience": [
            {
                "name": "收藏钩子结构",
                "structure": "大特写开场 → 卡点主舞 → 定格循环",
                "evidence": "收藏 473，占全部互动 31.2%；aggregate_engagement_rate 33.85%、consistency 10.0",
                "transferable_when": "MMD 等重看型内容",
                "example": "bilibili 首帧正脸特写，douyin 副歌三连切",
            }
        ],
        "top_reasons": ["views 高但 comments 低"],
        "bottom_reasons": ["xiaohongshu 分发弱"],
        "avoid": ["不要在 zhihu 发 MMD"],
        "next_actions": ["提高 shares"],
    },
    ensure_ascii=False,
)

FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global FAIL
    if ok:
        print(f"  [ok] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {detail}")


class FakeLLM:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def chat(self, messages, **kwargs):  # noqa: ANN001, ANN003
        self.prompts.append("\n".join(str(m.get("content", "")) for m in messages))
        return REPLY


class FakeConfig:
    def ready(self) -> bool:
        return True


def hits(text: str) -> list[str]:
    lowered = str(text).lower()
    return [word for word in FORBIDDEN if word in lowered]


def main() -> None:
    db = Database(str(Path(_TMP) / "t.db"))
    db.init()
    for platform, title, views, likes, comments, collects, shares in SAMPLES:
        cid = db.create_content(
            topic="MMD", platform=platform, title=title, body="前 3 秒角色大特写，副歌卡点三连切"
        )
        pid = db.create_post(
            content_id=cid,
            platform=platform,
            title=title,
            body="前 3 秒角色大特写，副歌卡点三连切",
            status="published",
            published_at="2026-09-20 20:00:00",
            url=f"https://example.com/{cid}",
        )
        db.add_metric(
            pid,
            {
                "views": views,
                "likes": likes,
                "comments": comments,
                "collects": collects,
                "shares": shares,
            },
        )

    llm = FakeLLM()
    svc = A.AnalyticsService(db=db, llm=llm, config_manager=FakeConfig())
    result = svc.attribute(days=36500, persist=True)

    print("\n=== prompt ===")
    prompt = llm.prompts[0] if llm.prompts else ""
    check("prompt 不含英文指标键", not hits(prompt), str(hits(prompt)))
    check("prompt 含中文指标名", "总体互动率" in prompt and "播放量" in prompt)
    check("prompt 含中文平台名", "抖音" in prompt and "B 站" in prompt)
    check("prompt 有中文硬约束", "硬性要求" in prompt and "不要使用平台的英文 key" in prompt)

    print("\n=== 面向用户的字段 ===")
    check("归因成功", bool(result.get("ok")), str(result.get("message")))
    for key in ("summary", "top_reasons", "bottom_reasons", "avoid", "next_actions"):
        value = result.get(key)
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        check(f"{key} 无英文标识", not hits(text), f"{hits(text)} -> {text[:120]}")
    exp = result.get("structure_experience") or []
    check("结构化经验非空", bool(exp))
    exp_text = json.dumps(exp, ensure_ascii=False)
    check("structure_experience 无英文标识", not hits(exp_text), f"{hits(exp_text)} -> {exp_text[:200]}")
    check("evidence 已中文化", "总体互动率" in exp_text and "发布一致性" in exp_text, exp_text[:160])

    print("\n=== 画像记忆 ===")
    memory = db.get_profile().get("memory") or []
    check("记忆已写入", bool(memory))
    memory_text = "\n".join(str(item.get("text") or "") for item in memory)
    check("记忆无英文标识", not hits(memory_text), f"{hits(memory_text)} -> {memory_text[:200]}")
    check("记忆含中文结论", "收藏钩子结构" in memory_text and "分析结论" in memory_text)

    print("\n=== 文本兜底 ===")
    localized = A._localize_terms(
        "douyin 播放 8.6 万，aggregate_engagement_rate 33.85%，consistency 10.0，views 高但 comments 低"
    )
    check("兜底替换无英文", not hits(localized), localized)
    print("  兜底结果：" + localized)

    print("\n" + ("全部通过" if FAIL == 0 else f"{FAIL} 项失败"))
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
