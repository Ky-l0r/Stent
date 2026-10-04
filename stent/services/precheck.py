"""stent/services/precheck.py — 发布中心「发布前检查」服务（纯本地规则）。

职责
----
发布流程「选平台 → 格式适配 → **发布前检查** → 预览 → 人工确认 → 发布」中的第三环。
对一份 :class:`PublishPayload` 做确定性扫描，产出 :class:`CheckReport`：
敏感词 / 极限词、平台字数、格式、媒体附件、平台特有规则、风控（导流 / 洗稿 / 版权）提示，
并给出 0-100 质量分与「无 error 即通过」的判定。

规则来源（Easel → Stent 的复用与改写，详见 ``_reference/specs/precheck.md``）
--------------------------------------------------------------------------
- ``skills/shared/scripts/content_guard.py``     出站文本安全闸门（密钥 / 内网 IP / 内部路径 /
  环境变量名 / AI 自曝措辞的分级扫描思路与正则，迁移为 §密钥与内部信息检查）
- ``skills/shared/scripts/wordcount.py``         社媒字数口径 ``social_count``（中文字 + 英文词 +
  数字串 + 标点各计 1），迁移为：func:`social_count`
- ``skills/openclaw/skill-quality-gate/``        合规维度（references/general-rules.md）与
  三平台特有规则（references/platform-xiaohongshu.md / platform-douyin.md / platform-bilibili.md）
- ``skills/openclaw/skill-publish-checklist/SKILL.md``  完整性清单（标题 / 正文 / 封面 / 标签 /
  CTA / 图片规格 / 字数 / Emoji 的逐项检查项）
- ``skills/openclaw/skill-risk-scanner/``        洗稿模式、引用规范、素材版权提示
  （references/washing-patterns.md、references/copyright-guide.md）
- ``skills/shared/scoring-dimensions.md``        敏感赛道合规预警（文末「敏感赛道合规预警」）

设计约束
--------
- 纯本地规则：不联网、不调用 LLM、不 import anthropic / openclaw / Easel 任何模块；
- 不 import UI 框架（不 import PySide6），只依赖 ``stent.platforms.base`` 的数据结构；
- **永不抛异常**：任何内部错误都降级为一条 warn 级 CheckItem，不阻塞用户；
- 词表从 ``stent/resources/sensitive_words.txt`` 加载，文件缺失时用内置兜底词表；
- 全部提示为中文。

关于 ``CheckItem.span``
-----------------------
span 是「该检查项所属字段」内的字符区间：字段名写在 ``detail`` 开头的【】里
（如「【正文】命中『最好』」），UI 可据此在原字段文本上高亮。同一份 payload 的
不同字段（标题 / 正文 / 标签 / 简介 / 主题）会拆成多条 CheckItem，便于分别定位。

用法::

    from stent.platforms.base import PlatformLimits, PublishPayload
    from stent.services.precheck import PrecheckService

    svc = PrecheckService()
    report = svc.check(PublishPayload(title=..., body=..., tags=[...]),
                       PlatformLimits(title_max=20, body_max=1000, tags_max=10),
                       platform="xiaohongshu")
    print(report.to_text())
    if not report.passed:
        ...  # 阻塞发布，等用户改稿（不提供跳过开关，见企划书第七章风控约束）
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..platforms.base import PlatformLimits, PublishPayload

__all__ = [
    "CheckItem",
    "CheckReport",
    "PrecheckService",
    "WordRule",
    "WordBank",
    "load_wordbank",
    "wordbank_paths",
    "social_count",
    "SENSITIVE_WORDS_FILE",
]


# ═══════════════════════════════════════════════════════════════════════════
# 一、字数口径（移植自 Easel skills/shared/scripts/wordcount.py 的 count()）
# ═══════════════════════════════════════════════════════════════════════════
# 口径说明：社媒平台（小红书 / 微博 / 抖音）主要看「计数字符数」，即
#   中文字符 + 英文单词（每词计 1）+ 数字串（每串计 1）+ 标点/符号/emoji（每个计 1）。
# 这是 wordcount.py 里的 social_count 口径，也是本模块判定标题/正文超限的唯一口径。
_CJK_CLASS = "\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00020000-\U0002ffff"
_CJK_RE = re.compile("[" + _CJK_CLASS + "]")
_WORD_RE = re.compile(r"[A-Za-z]+(?:['\-][A-Za-z]+)*")
_NUM_RE = re.compile(r"\d+(?:[.,]\d+)*")
_PUNCT_RE = re.compile("[^\\s0-9A-Za-z" + _CJK_CLASS + "]")

# Emoji / 表情符号（含常见符号区与补充平面），用于「过多 Emoji」判定
_EMOJI_RE = re.compile(
    "[\u00a9\u00ae\u2122\u2190-\u21ff\u2300-\u23ff\u2460-\u24ff"
    "\u25a0-\u27bf\u2b00-\u2bff\u3030\u303d\u3297\u3299\ufe0f\u200d"
    "\U0001f000-\U0001faff]"
)

# 零宽 / 不可见字符
_INVISIBLE_RE = re.compile("[\u200b-\u200f\u202a-\u202e\ufeff]")


def _s(value: object) -> str:
    """把任意值安全地转成字符串（None → ""，非字符串用 str()）。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def social_count(text: object) -> dict[str, int]:
    """按社媒口径统计字数（移植自 Easel wordcount.py）。

    返回 ``cjk_chars`` / ``en_words`` / ``num_groups`` / ``punct`` / ``total_chars``
    / ``social_count`` 六个口径；判定平台字数上限时用 ``social_count``。
    """
    t = _s(text)
    cjk = len(_CJK_RE.findall(t))
    en_words = len(_WORD_RE.findall(t))
    num_groups = len(_NUM_RE.findall(t))
    punct = len(_PUNCT_RE.findall(t))
    return {
        "cjk_chars": cjk,
        "en_words": en_words,
        "num_groups": num_groups,
        "punct": punct,
        "total_chars": len(t),
        "social_count": cjk + en_words + num_groups + punct,
    }


# ═══════════════════════════════════════════════════════════════════════════
# 二、词表分节 → 命中级别 / 代码 / 说明
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class SectionRule:
    """一个词表分节的命中行为。"""

    level: str  # error / warn / info
    code: str  # 机器可读代码
    reason: str  # 中文原因说明
    suggestion: str  # 中文修改建议


#: 分节名 → 命中行为。分节名与 sensitive_words.txt 里的「== 分节名 ==」一一对应。
SECTION_RULES: dict[str, SectionRule] = {
    "广告法极限词": SectionRule(
        "error", "extreme_word",
        "《广告法》禁止使用的绝对化用语，无充分依据即属违规宣传。",
        "改为可验证的表述，例如「我用下来觉得…」「今年销量靠前」；确有官方榜单可引用并注明出处。",
    ),
    "极限词（需人工复核）": SectionRule(
        "warn", "extreme_word_soft",
        "程度副词类极限词，日常口语中很常见，是否构成绝对化宣传需结合语境判断。",
        "若用于宣传产品功效，建议改为具体数据或使用体验；若是普通叙述可忽略。",
    ),
    "虚假宣传与绝对承诺": SectionRule(
        "error", "false_claim",
        "缺乏依据的功效或利益承诺，属虚假宣传高风险表述。",
        "删除承诺性表述，改为可溯源的事实描述；确实有依据的请注明来源。",
    ),
    "医疗夸大": SectionRule(
        "error", "medical_claim",
        "非医疗产品宣称治疗/治愈功效，属医疗违规高风险表述。",
        "删除治疗、功效类承诺；如为医疗资质主体，请补充资质与合规表述。",
    ),
    "医疗术语（需人工复核）": SectionRule(
        "warn", "medical_term",
        "医疗相关术语，科普/测评语境中可能合法，但易被平台判定为功效宣传。",
        "如非医疗资质账号，建议弱化术语或改为生活化表述（如「肌肤状态」代替「皮炎」）。",
    ),
    "金融风险": SectionRule(
        "error", "finance_risk",
        "金融/理财类高风险表述，涉嫌承诺收益或违规引流。",
        "删除收益承诺与保本表述，改为风险提示；金融类内容建议补充「投资有风险」。",
    ),
    "平台违禁": SectionRule(
        "error", "banned_content",
        "平台普遍禁止的内容类型（赌博、违禁品、色情、暴力、造假等）。",
        "直接删除该表述；如为必要讨论（如新闻报道），请改为中性转述并说明背景。",
    ),
    "国家机构与特供": SectionRule(
        "error", "state_agency",
        "使用国家机关名义或「特供/专供」类表述，属《广告法》明令禁止的情形。",
        "删除国家机构、特供、军供类表述。",
    ),
    "歧视与引战": SectionRule(
        "error", "hostile_content",
        "歧视、侮辱或引战内容，平台零容忍，可能导致账号处罚。",
        "删除人身攻击与群体对立表述，改为就事论事的客观讨论。",
    ),
    "导流与联系方式": SectionRule(
        "warn", "contact_diversion",
        "疑似站外导流或留下联系方式，多数平台会限流甚至处罚。",
        "删除联系方式与外站入口；如确需引导，改为「主页」「评论区」等平台内路径。",
    ),
    "平台限流与过度营销": SectionRule(
        "warn", "marketing_heavy",
        "过度营销或诱导互动话术，容易触发平台降权（小红书/抖音对刷屏式话术敏感）。",
        "减少叫卖式表达，改为真实使用感受与具体场景描述。",
    ),
    "内部信息与密钥": SectionRule(
        "error", "secret_leak",
        "疑似密钥、内部地址或个人信息泄露，一旦发布无法撤回。",
        "立即从内容中删除该信息；如已泄露，请尽快更换对应密钥。",
    ),
    "技术词（需人工复核）": SectionRule(
        "warn", "tech_term",
        "技术类词汇，技术分享/教程中可能完全正常。",
        "确认是否涉及内部系统或敏感实现细节；纯技术科普可忽略。",
    ),
    "AI 自曝措辞": SectionRule(
        "info", "ai_disclosure",
        "提及 AI 生成或具体模型名；在 AI 科普/论文解读里属正常内容，在种草内容里可能显得不真诚。",
        "视内容类型决定：科普内容保留即可；营销内容建议删除，或按平台要求标注「AI 辅助生成」。",
    ),
    "洗稿特征词": SectionRule(
        "warn", "washing_pattern",
        "疑似搬运/洗稿痕迹或来源标注缺失，存在版权与原创度风险。",
        "补充来源标注（作者名 + 出处），或用自己的语言重写并加入个人经验与观点。",
    ),
    "低质内容特征": SectionRule(
        "info", "low_quality",
        "低质内容或标题党特征词，影响完读率与账号权重。",
        "替换为具体、有信息量的表述，避免空泛口号。",
    ),
}

#: 未在 SECTION_RULES 中登记的分节（用户在词表里自建分节）的兜底行为。
_DEFAULT_SECTION_RULE = SectionRule(
    "warn", "risk_word",
    "自定义风险词，是否违规需人工确认。",
    "请人工判断该表述是否适合公开发布。",
)

#: 「自动降级」名单：这些词在正常语境里非常常见（如「首选」「返利」），
#: 即使所在分节是 error，也只报 warn，避免误拦正常内容。
#: 仅用于降低误报，命中时 detail 会注明「已自动降级」。
SOFT_LEVEL_DOWNGRADE: frozenset[str] = frozenset({
    "首选", "极品", "返利", "无风险", "国字号", "无效退款", "延时", "首个",
})

_LEVEL_ORDER = {"error": 0, "warn": 1, "info": 2}


# ═══════════════════════════════════════════════════════════════════════════
# 三、词表加载（文件缺失时用内置兜底）
# ═══════════════════════════════════════════════════════════════════════════

SENSITIVE_WORDS_FILE = "sensitive_words.txt"

#: 内置兜底词表：仅在词表文件缺失/不可读时启用，只覆盖各分节的高频风险词。
BUILTIN_WORDS: dict[str, tuple[str, ...]] = {
    "广告法极限词": (
        "国家级", "最高级", "顶级", "首个", "首家", "独家", "独一无二",
        "全网最低价", "全网第一", "全国第一", "行业第一", "销量第一",
        "100%有效", "绝对有效", "绝对安全", "保证有效", "永久有效", "立竿见影",
    ),
    "极限词（需人工复核）": (
        "最好", "最佳", "最强", "最大", "最便宜", "最专业", "最权威", "极致", "万能", "唯一",
    ),
    "虚假宣传与绝对承诺": (
        "永久免费", "免费领取", "白送", "躺赚", "日入过万", "稳赚不赔", "一本万利",
        "官方认证", "明星同款", "无效退款", "错过后悔", "零添加",
    ),
    "医疗夸大": (
        "治愈", "根治", "抗癌", "降血压", "降血糖", "排毒", "暴瘦", "丰胸", "壮阳",
        "增强免疫力", "抗衰老", "药到病除", "药妆",
    ),
    "医疗术语（需人工复核）": (
        "医疗", "药品", "疗程", "处方", "疗效", "祛痘", "美白", "养生", "调理",
    ),
    "金融风险": (
        "保本保息", "保收益", "高收益", "无风险", "翻倍", "投资返利", "拉人头",
        "发展下线", "炒币", "原始股", "荐股", "配资", "套现", "无视征信", "割韭菜",
    ),
    "平台违禁": (
        "赌博", "博彩", "六合彩", "刷单", "外挂", "破解版", "盗版", "枪支", "迷药",
        "毒品", "色情", "裸聊", "假证", "银行卡买卖", "人肉搜索", "开盒",
    ),
    "国家机构与特供": (
        "特供", "专供", "军供", "国宴", "中南海", "人民大会堂", "国家领导人",
        "国家免检", "军用级", "政府指定",
    ),
    "歧视与引战": (
        "地域黑", "地图炮", "低端人口", "脑残", "智障", "傻逼", "舔狗", "键盘侠",
        "杠精", "引战", "滚出中国",
    ),
    "导流与联系方式": (
        "微信号", "加微信", "二维码", "扫码关注", "长按识别", "QQ群", "群号",
        "私信我", "主页链接", "店铺链接", "淘宝链接", "百度网盘", "提取码", "加V",
    ),
    "平台限流与过度营销": (
        "买它", "闭眼入", "冲冲冲", "必买", "全网疯抢", "抢疯了", "秒空", "手慢无",
        "一键三连", "双击点赞", "关注不迷路", "点赞抽奖", "限时秒杀", "白嫖", "求关注",
    ),
    "内部信息与密钥": (
        "API_KEY=", "SECRET_KEY=", "ACCESS_TOKEN=", "sk-", ".env 文件", "私钥",
        "助记词", "身份证号", "银行卡号", "服务器密码", "数据库密码", "内网地址",
        "内网IP", "代理端口", "VPN账号",
    ),
    "技术词（需人工复核）": (
        "API_KEY", "token", "Authorization", "Bearer", "localhost", "127.0.0.1",
        "内网", "测试环境", "调试接口", "后台管理",
    ),
    "AI 自曝措辞": (
        "由AI生成", "AI生成", "AI撰写", "AI创作", "大语言模型", "大模型生成",
        "作为AI", "system prompt", "系统提示词", "ChatGPT",
    ),
    "洗稿特征词": (
        "本文转载", "转载自", "转自网络", "图源网络", "侵删", "如有侵权",
        "综合整理", "原文链接", "洗稿", "搬运", "未经授权", "网传", "未经证实",
    ),
    "低质内容特征": (
        "点击关注", "每日更新", "更多精彩", "未完待续", "废话不多说", "干货满满",
        "建议收藏", "不看后悔", "震惊", "99%的人不知道", "揭秘", "标题党", "水一篇",
    ),
}


@dataclass(frozen=True)
class WordRule:
    """一条词表规则（词条 + 所属分节 + 命中行为）。"""

    word: str
    section: str
    level: str
    code: str
    reason: str
    suggestion: str


@dataclass
class WordBank:
    """加载完成的词表。"""

    rules: list[WordRule]
    source: str = "builtin"  # 实际生效的词表来源（文件路径或 "builtin"）
    notes: list[str] = field(default_factory=list)  # 加载过程中的中文说明

    def __len__(self) -> int:
        return len(self.rules)

    def sections(self) -> dict[str, int]:
        """分节 → 词条数。"""
        out: dict[str, int] = {}
        for rule in self.rules:
            out[rule.section] = out.get(rule.section, 0) + 1
        return out

    def words(self) -> list[str]:
        """全部词条（按加载顺序）。"""
        return [rule.word for rule in self.rules]


def wordbank_paths() -> list[Path]:
    """词表候选路径（按优先级），覆盖开发态、打包态与自定义覆盖。"""
    candidates: list[Path] = []

    env = os.environ.get("STENT_SENSITIVE_WORDS")
    if env:
        candidates.append(Path(env))

    here = Path(__file__).resolve()  # <root>/stent/services/precheck.py
    pkg = here.parent.parent  # <root>/stent
    root = pkg.parent  # 开发态=仓库根；打包态=程序目录
    candidates.append(pkg / "resources" / SENSITIVE_WORDS_FILE)
    candidates.append(root / "resources" / SENSITIVE_WORDS_FILE)
    candidates.append(root / SENSITIVE_WORDS_FILE)

    try:  # 复用项目的只读资源目录约定（paths.resource_dir / %APPDATA%）
        from .. import paths as stent_paths

        res = stent_paths.resource_dir()
        candidates.append(res / "resources" / SENSITIVE_WORDS_FILE)
        candidates.append(res / "stent" / "resources" / SENSITIVE_WORDS_FILE)
    except Exception:  # pragma: no cover - 路径探测失败不应影响检查
        pass

    unique: list[Path] = []
    seen: set[str] = set()
    for cand in candidates:
        key = str(cand).lower()
        if key not in seen:
            seen.add(key)
            unique.append(cand)
    return unique


def _rules_from_sections(
    sections: dict[str, tuple[str, ...]],
    *,
    notes: list[str] | None = None,
) -> list[WordRule]:
    """把「分节 → 词条」映射展开成规则列表（内置兜底词表用）。"""
    rules: list[WordRule] = []
    seen: set[str] = set()
    for section, words in sections.items():
        rule = SECTION_RULES.get(section, _DEFAULT_SECTION_RULE)
        for word in words:
            if word in seen:
                continue
            seen.add(word)
            rules.append(WordRule(word, section, rule.level, rule.code, rule.reason, rule.suggestion))
    if notes is not None:
        notes.append(f"内置兜底词表共 {len(rules)} 个词条，仅覆盖各分节高频词，建议补齐词表文件。")
    return rules


def _parse_wordfile(path: Path) -> tuple[list[WordRule], list[str]]:
    """解析词表文件。

    格式：``#`` 开头为注释，``== 分节名 ==`` 标记分节，其余非空行是词条。
    """
    rules: list[WordRule] = []
    notes: list[str] = []
    seen: set[str] = set()
    current = ""
    unknown_sections: set[str] = set()
    raw_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    for lineno, raw in enumerate(raw_lines, 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        header = re.match(r"^==\s*(.+?)\s*==$", line)
        if header:
            current = header.group(1)
            continue
        word = line
        if current == "":
            notes.append(f"第 {lineno} 行「{word}」不在任何分节内，已按默认分节处理。")
        if word in seen:
            notes.append(f"第 {lineno} 行「{word}」重复出现，已忽略后一条。")
            continue
        seen.add(word)
        section = current or "未分节词条"
        rule = SECTION_RULES.get(section)
        if rule is None:
            unknown_sections.add(section)
            rule = _DEFAULT_SECTION_RULE
        rules.append(WordRule(word, section, rule.level, rule.code, rule.reason, rule.suggestion))
    if unknown_sections:
        notes.append("词表中存在未登记的分节（已按 warn/risk_word 处理）：" + "、".join(sorted(unknown_sections)))
    return rules, notes


def load_wordbank(path: str | Path | None = None) -> WordBank:
    """加载词表；显式传入 ``path`` 时只尝试该文件。任何失败都回落到内置兜底词表。"""
    candidates = [Path(path)] if path else wordbank_paths()
    for cand in candidates:
        try:
            if not cand.is_file():
                continue
            rules, notes = _parse_wordfile(cand)
            if rules:
                return WordBank(rules=rules, source=str(cand), notes=notes)
        except Exception:
            continue  # 词表不可读时继续尝试下一个候选路径
    notes: list[str] = []
    rules = _rules_from_sections(BUILTIN_WORDS, notes=notes)
    notes.append("未找到可用的 sensitive_words.txt，已启用内置兜底词表。")
    return WordBank(rules=rules, source="builtin", notes=notes)


# ═══════════════════════════════════════════════════════════════════════════
# 四、正则规则（密钥 / 链接 / 联系方式 / 格式）
# ═══════════════════════════════════════════════════════════════════════════
# 密钥与内部信息：思路与正则迁移自 content_guard.py 的 SECRET_PATTERNS，
# 但去掉了 Easel 专有的内部域名/路径，改为通用形态；分级也做了调整：
#   - 真实密钥形态 / 内网 IP / 身份证 / 银行卡 → error
#   - 环境变量名 / 内部域名 / 绝对路径 → warn（技术文章里可能正常）
# 代码 → (级别, 中文说明, 中文建议)
_SECRET_PATTERNS: list[tuple[re.Pattern[str], str, str, str, str]] = [
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}\b"), "error", "secret_leak",
     "疑似 API Key（sk- 开头）",
     "立即删除该密钥；如已发布，请到服务商后台吊销并重新生成。"),
    (re.compile(
        r"(?i)\b(?:api[_-]?key|apikey|auth[_-]?token|access[_-]?key|secret[_-]?key"
        r"|app[_-]?secret|password|passwd|密码)\b\s*[:=＝：]\s*['\"]?[A-Za-z0-9/_\-\.!@#$%^&*]{6,}"),
     "error", "secret_leak", "键值形式的密钥 / 口令", "删除该键值对，不要在公开内容里出现任何凭据。"),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9/_\-\.]{12,}"), "error", "secret_leak",
     "Bearer 令牌", "删除 Authorization 头与令牌内容。"),
    (re.compile(r"\b(?:10|192\.168|172\.(?:1[6-9]|2\d|3[01]))(?:\.\d{1,3}){2,3}(?::\d+)?\b"),
     "error", "secret_leak", "私网 / 内网 IP 地址", "删除内网地址，改为对外可访问的地址或不写地址。"),
    (re.compile(r"\b\d{17}[\dXx]\b"), "error", "privacy_leak", "疑似身份证号",
     "删除或打码（保留前 6 位与后 4 位以外的部分）。"),
    (re.compile(r"(?<!\d)\d{16,19}(?!\d)"), "warn", "privacy_leak", "疑似银行卡号",
     "如确需展示请打码；与业务无关请直接删除。"),
    (re.compile(r"(?i)\b(?:ANTHROPIC|OPENAI|DASHSCOPE|DEEPSEEK|MOONSHOT|ARK|MINIMAX"
                r"|SILICONFLOW|STENT|EASEL)_[A-Z0-9_]{2,}\b"),
     "warn", "secret_leak", "环境变量名", "环境变量名会暴露内部配置，建议改为「API Key」等泛称。"),
    (re.compile(r"(?i)\b[a-z0-9][a-z0-9\-]*\.(?:internal|intra|corp|devops|lan|local)\b"),
     "warn", "secret_leak", "疑似内部域名", "删除内部域名；对外内容只写公开域名。"),
    (re.compile(r"/(?:home|root|mnt|var|opt|usr|srv|data|tmp)/[A-Za-z0-9_\-./]{3,}"),
     "warn", "secret_leak", "疑似服务器绝对路径", "删除服务器路径，避免暴露内部环境结构。"),
    (re.compile(r"(?i)\b[A-Za-z]:\\{1,2}(?:Users|Documents|Desktop|AppData|Program)\\"),
     "warn", "secret_leak", "本机绝对路径", "删除本机路径；如为教程示例，请改用「你的输出目录」等占位说法。"),
    (re.compile(r":3128\b"), "warn", "secret_leak", "常见代理端口 3128", "删除代理端口信息。"),
]

# 链接与联系方式
_LINK_RE = re.compile(r"(?i)\b(?:https?://|www\.)[^\s，。！？；、）)】」》\"']+")
_BARE_DOMAIN_RE = re.compile(
    r"(?i)\b[a-z0-9][a-z0-9\-]{1,62}\.(?:com|cn|net|org|top|xyz|vip|cc|io|me|shop|site"
    r"|app|club|live|info|biz|store|tech|online|fun|pro)(?:/[^\s，。！？；、）)】]*)?")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"(?<!\d)(?:1[3-9]\d{9}|(?:400|800)-?\d{3}-?\d{4})(?!\d)")
_QQ_RE = re.compile(r"(?i)(?:QQ|扣扣|企鹅)\s*(?:号|号码|群|群号)?\s*[:：]?\s*[1-9]\d{4,11}")
_WECHAT_RE = re.compile(
    r"(?i)(?:微信|weixin|wechat|vx|wx|v信)\s*(?:号|号码|ID|id|账号)?\s*[:：]?\s*[A-Za-z0-9_\-]{4,}")
_CONTACT_WORDS: tuple[str, ...] = (
    "扫码", "二维码", "长按识别", "识别二维码", "私信", "加群", "进群", "群号",
    "联系方式", "电话咨询", "手机号", "网盘", "提取码", "主页链接", "简介链接",
    "店铺链接", "站外", "详情页下单", "加我",
)
# 谐音 / 变体绕过（来源：platform-xiaohongshu.md「使用谐音或变体绕过检测」）
_EVASION_RE = re.compile(r"(?:[+➕]\s*[Vv](?![A-Za-z])|[Vv]\s*[:：]\s*[Xx]|\b[vV][xX]\b|\b[wW][xX]\b|v信|薇信|扣扣|企鹅号)")

# 格式类
_MARKDOWN_RESIDUE: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("加粗/斜体标记", re.compile(r"\*\*[^*\n]{1,40}\*\*|__[^_\n]{1,40}__")),
    ("Markdown 标题", re.compile(r"(?m)^\s{0,3}#{1,6}\s+\S")),
    ("图片语法", re.compile(r"!\[[^\]\n]{0,60}\]\([^)\n]{1,200}\)")),
    ("链接语法", re.compile(r"(?<!!)\[[^\]\n]{1,60}\]\([^)\n]{1,200}\)")),
)
_SYMBOL_STACK_RE = re.compile(r"([!！?？。.~～*✧♡·_\-=+>》])\1{5,}")
_PLACEHOLDER_TITLE = {
    "无题", "无标题", "未命名", "标题", "标题党", "test", "todo", "tbd", "xxx",
    "待定", "占位", "占位符", "测试", "新建文档", "untitled",
}
_PLACEHOLDER_BODY = {
    "内容", "正文", "待补充", "待完善", "暂无", "无", "todo", "tbd", "占位符",
    "此处省略", "内容待补充", "test",
}

# 媒体扩展名 → 类别
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".heic", ".heif",
               ".tif", ".tiff", ".avif", ".jfif"}
_VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".flv", ".wmv", ".webm",
               ".mpg", ".mpeg", ".ts", ".3gp", ".rmvb"}
_AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".wma", ".amr"}

# 平台键归一化
_XHS_KEYS = {"xiaohongshu", "xhs", "redbook", "rednote", "小红书"}
_DOUYIN_KEYS = {"douyin", "dy", "抖音"}
_BILI_KEYS = {"bilibili", "bili", "b站"}
_ZHIHU_KEYS = {"zhihu", "知乎"}
_WEIBO_KEYS = {"weibo", "微博"}
#: 对站外导流 / 联系方式最严格的平台（命中直接判 error）
_STRICT_DIVERSION_KEYS = _XHS_KEYS | _DOUYIN_KEYS

#: 敏感赛道关键词（来源：scoring-dimensions.md 文末「敏感赛道合规预警」）
_SENSITIVE_TRACKS: dict[str, str] = {
    "医美": "医美", "轻医美": "医美", "整形": "医美",
    "理财": "财商理财", "财商": "财商理财", "基金定投": "财商理财", "股票": "财商理财",
    "母婴": "母婴育儿", "育儿": "母婴育儿", "辅食": "母婴育儿", "早教": "母婴育儿",
    "K12": "K12 教育", "升学": "K12 教育", "择校": "K12 教育", "教辅": "K12 教育",
    "养生": "健康养生", "祛湿": "健康养生", "三高": "健康养生",
    "护肤": "护肤功效", "功效": "护肤功效", "医美级": "护肤功效",
    "职场": "职场收入", "副业": "职场收入", "年薪": "职场收入", "薪资": "职场收入",
    "婚恋": "婚恋情感", "相亲": "婚恋情感", "情感": "婚恋情感",
}

#: 抖音「诱导互动」（来源：platform-douyin.md「诱导互动」）
_DOUYIN_INDUCE: tuple[str, ...] = (
    "双击", "点个赞", "求点赞", "关注不迷路", "一键三连", "点赞抽奖", "关注领福利",
    "互关", "求关注", "求转发", "爱心走一波", "双击666", "不转不是中国人",
)
#: B站「封面党 / 标题党」（来源：platform-bilibili.md「封面党与标题党」）
_BILI_CLICKBAIT: tuple[str, ...] = (
    "震惊", "不看后悔", "太可怕了", "99%的人不知道", "删前速看", "不看亏大了",
    "标题党", "必看", "史上最",
)
#: B站「恰饭未标注」识别词（来源：platform-bilibili.md「恰饭未标注」）
_BILI_BRAND_WORDS: tuple[str, ...] = ("赞助", "植入", "品牌方", "恰饭", "商单", "推广合作", "广告合作")
#: 转载 / 搬运标识（来源：copyright-guide.md「B站 - 转载标注」）
_REPRINT_WORDS: tuple[str, ...] = ("转载", "搬运", "转自", "授权转载", "原视频")


# ═══════════════════════════════════════════════════════════════════════════
# 五、检查结果数据结构
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class CheckItem:
    """一条检查结果。

    level: "error"（必须改，阻塞发布）/ "warn"（建议改）/ "info"（提示）。
    span:  命中片段在「所属字段」内的字符区间 (start, end)；
           字段名写在 detail 开头的【】内，便于 UI 在原字段文本上高亮。
    """

    level: str
    code: str
    title: str
    detail: str
    suggestion: str = ""
    span: tuple[int, int] | None = None


@dataclass
class CheckReport:
    """一次发布前检查的完整结果。"""

    items: list[CheckItem]
    score: int
    passed: bool

    def errors(self) -> list[CheckItem]:
        """阻塞发布的问题。"""
        return [it for it in self.items if it.level == "error"]

    def warnings(self) -> list[CheckItem]:
        """建议修改的问题。"""
        return [it for it in self.items if it.level == "warn"]

    def infos(self) -> list[CheckItem]:
        """提示性信息（含平台风控提醒、AI 标注建议等）。"""
        return [it for it in self.items if it.level == "info"]

    def to_text(self) -> str:
        """中文可读报告，直接贴到发布中心的预览区即可。"""
        lines: list[str] = ["发布前检查报告", "=" * 34]
        verdict = "✅ 通过（无阻塞项）" if self.passed else "❌ 未通过（存在必须修改的问题）"
        lines.append(f"结论：{verdict}")
        lines.append(f"质量分：{self.score}/100")
        lines.append(
            f"问题统计：必须修改 {len(self.errors())} 项 · 建议修改 {len(self.warnings())} 项 "
            f"· 提示 {len(self.infos())} 项"
        )
        labels = (("error", "必须修改"), ("warn", "建议修改"), ("info", "提示"))
        for level, label in labels:
            group = [it for it in self.items if it.level == level]
            if not group:
                continue
            lines.append("")
            lines.append(f"【{label}】{len(group)} 项")
            for idx, item in enumerate(group, 1):
                head = f"{idx}. [{item.code}] {item.title}"
                if item.span:
                    head += f"（位置 {item.span[0]}-{item.span[1]}）"
                lines.append(head)
                lines.append(f"   说明：{item.detail}")
                if item.suggestion:
                    lines.append(f"   建议：{item.suggestion}")
        if not self.items:
            lines.append("")
            lines.append("未发现风险项，可进入预览与人工确认。")
        elif self.passed and not self.warnings():
            lines.append("")
            lines.append("无阻塞项，可进入预览与人工确认。")
        return "\n".join(lines)


#: 每级每代码的基础扣分与最多计分条数（避免同类问题刷分刷穿）
_LEVEL_PENALTY = {"error": 15, "warn": 5, "info": 1}
_LEVEL_MAX_COUNT = {"error": 3, "warn": 3, "info": 5}

#: 「命中片段」类代码：同一个片段可能被词表与正则同时命中，
#: 这类重复只保留最长的一条（其它代码（如标题超长）覆盖整段文本，不参与去重）。
_SPAN_FAMILY_CODES = frozenset({
    "secret_leak", "privacy_leak", "contact_info", "contact_diversion",
    "contact_hint", "external_link", "bare_domain", "evasion_spelling",
})


def _field_label(detail: str) -> str:
    """从 detail 开头的【字段名】里取出字段名（用于判断两条结果是否来自同一字段）。"""
    match = re.match(r"^【([^】]{1,8})】", detail or "")
    return match.group(1) if match else ""


def _dedupe_span_items(items: list[CheckItem]) -> list[CheckItem]:
    """同一字段内、命中区间重叠的「片段类」结果只保留最长的一条。"""
    kept: list[CheckItem] = []
    for item in items:
        if item.code not in _SPAN_FAMILY_CODES or item.span is None:
            kept.append(item)
            continue
        label = _field_label(item.detail)
        replaced = False
        for idx, other in enumerate(kept):
            if other.code not in _SPAN_FAMILY_CODES or other.span is None:
                continue
            if _field_label(other.detail) != label:
                continue
            start = max(item.span[0], other.span[0])
            end = min(item.span[1], other.span[1])
            if start >= end:  # 区间不相交
                continue
            if (item.span[1] - item.span[0]) > (other.span[1] - other.span[0]):
                kept[idx] = item
            replaced = True
            break
        if not replaced:
            kept.append(item)
    return kept


def _compute_score(items: list[CheckItem]) -> int:
    """按 error 扣重分、warn 扣轻分、info 微扣，产出 0-100 质量分。"""
    used: dict[str, int] = {}
    deduction = 0
    for item in items:
        seen = used.get(item.code, 0)
        used[item.code] = seen + 1
        if seen >= _LEVEL_MAX_COUNT.get(item.level, 1):
            continue
        deduction += _LEVEL_PENALTY.get(item.level, 0)
    score = max(0, 100 - deduction)
    if any(it.level == "error" for it in items):
        # 存在阻塞项时不允许出现「高分」观感
        score = min(score, 60)
    return score


def _build_report(items: list[CheckItem]) -> CheckReport:
    """去重 + 排序 + 计分 + 判定，产出 CheckReport。"""
    ordered = sorted(
        _dedupe_span_items(items),
        key=lambda it: (
            _LEVEL_ORDER.get(it.level, 3),
            it.code,
            it.span if it.span is not None else (1 << 30, 1 << 30),
        ),
    )
    passed = not any(it.level == "error" for it in ordered)
    return CheckReport(items=ordered, score=_compute_score(ordered), passed=passed)


# ═══════════════════════════════════════════════════════════════════════════
# 六、辅助函数
# ═══════════════════════════════════════════════════════════════════════════


def _kind_of_ext(ext: str) -> str | None:
    """扩展名 → 媒体类别；未知返回 None。"""
    ext = (ext or "").lower()
    if ext in _IMAGE_EXTS:
        return "image"
    if ext in _VIDEO_EXTS:
        return "video"
    if ext in _AUDIO_EXTS:
        return "audio"
    return None


def _contains_any(text: str, words: tuple[str, ...]) -> list[str]:
    """返回 text 中命中的词（保留给定顺序）。"""
    return [w for w in words if w and w in text]


def _iter_hits(text: str, rules: list[WordRule]) -> list[tuple[WordRule, int, int]]:
    """在 text 中查找所有词条命中（英文大小写不敏感），返回 (规则, 起, 止)。"""
    hits: list[tuple[WordRule, int, int]] = []
    if not text:
        return hits
    # lower() 对中文是恒等变换，对 ASCII 只是大小写折叠，字符数不变，
    # 因此用 lower 后的文本做定位，返回的下标对原文依然有效。
    low = text.lower()
    for rule in rules:
        needle = rule.word.lower()
        if not needle:
            continue
        start = low.find(needle)
        while start != -1:
            hits.append((rule, start, start + len(needle)))
            start = low.find(needle, start + len(needle))
    return hits


def _dedupe_hits(hits: list[tuple[WordRule, int, int]]) -> list[tuple[WordRule, int, int]]:
    """同一区间被多个词条命中时只留最长的一条；同一个词重复出现只报第一次。"""
    hits.sort(key=lambda h: (h[1], -(h[2] - h[1])))
    kept: list[tuple[WordRule, int, int]] = []
    seen_words: set[str] = set()
    for rule, start, end in hits:
        if rule.word in seen_words:
            continue
        if any(start >= k[1] and end <= k[2] for k in kept):
            continue
        seen_words.add(rule.word)
        kept.append((rule, start, end))
    return kept


def _find_overlap_span(body: str, reference: str, *, n: int = 12, min_span: int = 24) -> tuple[int, int] | None:
    """在 body 中找与 reference 连续重合的最长片段（用于洗稿嫌疑提示）。

    做法是确定性的 n-gram 连续命中，不计算相似度百分比（Easel risk-scanner 明确要求
    不输出相似度分数），只返回重合区间，让用户自己判断。
    """
    if len(body) < n or len(reference) < n:
        return None
    grams = {reference[i:i + n] for i in range(len(reference) - n + 1)}
    best_start, best_len = 0, 0
    run_start, run_len = 0, 0
    for i in range(len(body) - n + 1):
        if body[i:i + n] in grams:
            if run_len == 0:
                run_start = i
            run_len += 1
            if run_len > best_len:
                best_start, best_len = run_start, run_len
        else:
            run_len = 0
    if best_len == 0:
        return None
    end = best_start + best_len + n - 1
    if end - best_start < min_span:
        return None
    return (best_start, min(end, len(body)))


# ═══════════════════════════════════════════════════════════════════════════
# 七、发布前检查服务
# ═══════════════════════════════════════════════════════════════════════════


class PrecheckService:
    """发布前检查服务（纯本地规则，无状态、可复用）。

    参数
    ----
    bank: 可注入自定义 :class:`WordBank`（测试用）；缺省从词表文件加载。
    """

    def __init__(self, bank: WordBank | None = None) -> None:
        self._bank: WordBank = bank if bank is not None else load_wordbank()

    # -- 公开属性 ----------------------------------------------------------
    @property
    def bank(self) -> WordBank:
        """当前生效的词表。"""
        return self._bank

    # -- 主入口 ------------------------------------------------------------
    def check(
        self,
        payload: PublishPayload,
        limits: PlatformLimits,
        *,
        platform: str = "",
    ) -> CheckReport:
        """执行全部检查。永不抛异常；内部错误降级为 warn 级结果。"""
        items: list[CheckItem] = []
        try:
            pl = payload if payload is not None else PublishPayload()
            lm = limits if limits is not None else PlatformLimits()
            key = _s(platform).strip().lower()
            steps = (
                self._check_length,
                self._check_format,
                self._check_media,
                self._check_words,
                self._check_secrets,
                self._check_platform_rules,
                self._check_risk,
            )
            for step in steps:
                self._safe(step, pl, lm, key, items)
        except Exception as exc:  # 兜底：绝不让检查流程把发布中心带崩
            items.append(CheckItem(
                level="warn", code="precheck_failed", title="检查未完成",
                detail=f"发布前检查内部异常：{exc!r}",
                suggestion="已跳过其余检查项，请人工复核内容后再确认发布。",
            ))
        return _build_report(items)

    @staticmethod
    def _safe(step, pl, lm, key, items: list[CheckItem]) -> None:
        """执行单个检查项，异常时降级为一条 info，不影响其它检查项。"""
        try:
            step(pl, lm, key, items)
        except Exception as exc:
            items.append(CheckItem(
                level="info", code="check_skipped", title="检查项被跳过",
                detail=f"「{getattr(step, '__name__', 'step')}」执行失败：{exc!r}",
                suggestion="可继续发布；若反复出现请反馈该问题。",
            ))

    # -- 取值辅助 ----------------------------------------------------------
    @staticmethod
    def _extra(pl: PublishPayload) -> dict:
        extra = getattr(pl, "extra", None)
        return extra if isinstance(extra, dict) else {}

    @staticmethod
    def _tags(pl: PublishPayload) -> list[str]:
        tags = getattr(pl, "tags", None)
        if not isinstance(tags, (list, tuple)):
            return []
        return [_s(t).strip() for t in tags if _s(t).strip()]

    @staticmethod
    def _media(pl: PublishPayload) -> list[str]:
        media = getattr(pl, "media", None)
        if not isinstance(media, (list, tuple)):
            return []
        return [_s(m).strip() for m in media if _s(m).strip()]

    # ── 1. 字数（平台 limits） ────────────────────────────────────────────
    def _check_length(self, pl: PublishPayload, lm: PlatformLimits, key: str,
                      items: list[CheckItem]) -> None:
        title = _s(getattr(pl, "title", ""))
        body = _s(getattr(pl, "body", ""))
        tags = self._tags(pl)

        title_len = social_count(title)["social_count"]
        title_max = int(getattr(lm, "title_max", 0) or 0)
        if title_max > 0 and title.strip():
            if title_len > title_max:
                items.append(CheckItem(
                    "error", "title_too_long", "标题超出平台字数上限",
                    f"【标题】当前 {title_len} 字，平台上限 {title_max} 字。",
                    f"删减约 {title_len - title_max} 字，把核心卖点与关键词前置。",
                    (0, len(title)),
                ))
            elif title_len > int(title_max * 0.9):
                items.append(CheckItem(
                    "warn", "title_near_limit", "标题接近字数上限",
                    f"【标题】当前 {title_len} 字，上限 {title_max} 字，几乎没有余量。",
                    "预留 2-3 字余量，避免平台端计数差异导致被截断。",
                    (0, len(title)),
                ))
            elif title_len < 4:
                items.append(CheckItem(
                    "info", "title_too_short", "标题过短",
                    f"【标题】仅 {title_len} 字，信息量不足，影响点击率。",
                    "补充具体对象或利益点，例如「3 个收纳技巧，小户型必看」。",
                    (0, len(title)),
                ))

        body_len = social_count(body)["social_count"]
        body_max = int(getattr(lm, "body_max", 0) or 0)
        body_min = int(getattr(lm, "body_min", 0) or 0)
        if body_max > 0 and body_len > body_max:
            items.append(CheckItem(
                "error", "body_too_long", "正文超出平台字数上限",
                f"【正文】当前 {body_len} 字，平台上限 {body_max} 字。",
                f"删减约 {body_len - body_max} 字，或拆成两篇／改为长图。",
                (0, len(body)),
            ))
        elif body_max > 0 and body_len > int(body_max * 0.95):
            items.append(CheckItem(
                "warn", "body_near_limit", "正文接近字数上限",
                f"【正文】当前 {body_len} 字，上限 {body_max} 字。",
                "预留少量余量，避免平台端计数差异导致正文被截断。",
            ))
        if body_min > 1 and body.strip() and body_len < body_min:
            items.append(CheckItem(
                "error", "body_too_short", "正文未达平台字数下限",
                f"【正文】当前 {body_len} 字，平台要求至少 {body_min} 字。",
                f"补充约 {body_min - body_len} 字的具体信息。",
            ))

        tags_max = int(getattr(lm, "tags_max", 0) or 0)
        if tags_max > 0 and len(tags) > tags_max:
            items.append(CheckItem(
                "error", "tags_too_many", "标签数量超出平台上限",
                f"【标签】当前 {len(tags)} 个，平台上限 {tags_max} 个。",
                f"删除 {len(tags) - tags_max} 个相关性最弱的标签。",
            ))
        if not tags:
            items.append(CheckItem(
                "warn", "tags_missing", "没有话题标签",
                "【标签】未填写任何标签，会损失平台话题流量。",
                "补充 3-5 个与内容强相关的标签（小红书建议 5-10 个）。",
            ))
        elif len(tags) < 3:
            items.append(CheckItem(
                "info", "tags_few", "标签偏少",
                f"【标签】仅 {len(tags)} 个，一般建议 3-10 个。",
                "再补充 2-3 个垂类标签，覆盖搜索词。",
            ))
        for tag in tags:
            if social_count(tag)["social_count"] > 20:
                items.append(CheckItem(
                    "warn", "tag_too_long", "标签过长",
                    f"【标签】「{tag[:20]}…」超过 20 字，平台通常按普通正文处理。",
                    "标签控制在 2-10 字，用搜索词而非句子。",
                ))
        lowered = [t.lower() for t in tags]
        if len(set(lowered)) != len(lowered):
            items.append(CheckItem(
                "info", "tag_duplicate", "标签重复",
                "【标签】存在重复标签。",
                "删除重复项，换成其它相关话题。",
            ))

    # ── 2. 格式 ──────────────────────────────────────────────────────────
    def _check_format(self, pl: PublishPayload, lm: PlatformLimits, key: str,
                      items: list[CheckItem]) -> None:
        title = _s(getattr(pl, "title", ""))
        body = _s(getattr(pl, "body", ""))
        summary = _s(getattr(pl, "summary", ""))
        title_max = int(getattr(lm, "title_max", 0) or 0)

        # 标题缺失 / 占位符 / 纯符号
        if title_max > 0 and not title.strip():
            items.append(CheckItem(
                "error", "title_missing", "标题为空",
                "【标题】该平台需要标题，当前为空。",
                "填写一个具体、含关键词的标题。",
            ))
        elif title.strip():
            if title.strip().lower() in _PLACEHOLDER_TITLE:
                items.append(CheckItem(
                    "error", "title_placeholder", "标题是占位符",
                    f"【标题】「{title.strip()}」看起来是占位符，不是真实标题。",
                    "替换为真实标题后再发布。",
                    (0, len(title)),
                ))
            if not _CJK_RE.search(title) and not re.search(r"[0-9A-Za-z]", title):
                items.append(CheckItem(
                    "error", "title_symbol_only", "标题只有符号",
                    f"【标题】「{title.strip()}」不含任何汉字或字母数字。",
                    "写出真实标题内容。",
                    (0, len(title)),
                ))
        if title_max == 0 and title.strip():
            items.append(CheckItem(
                "info", "title_unused", "该平台不使用标题字段",
                "【标题】当前平台 limits.title_max 为 0，填写的标题不会随内容一起发布。",
                "把标题里的关键信息并入正文首句。",
            ))

        # 正文缺失 / 占位 / 过短
        stripped_body = body.strip()
        if not stripped_body:
            items.append(CheckItem(
                "error", "body_empty", "正文为空",
                "【正文】没有可发布的正文内容。",
                "写入正文后再发布。",
            ))
        elif stripped_body.lower() in _PLACEHOLDER_BODY:
            items.append(CheckItem(
                "error", "body_placeholder", "正文是占位符",
                f"【正文】「{stripped_body[:20]}」看起来是占位内容。",
                "替换为真实文案。",
                (0, len(body)),
            ))
        elif not _CJK_RE.search(body) and not re.search(r"[0-9A-Za-z]", body):
            items.append(CheckItem(
                "error", "body_symbol_only", "正文只有符号",
                "【正文】不含任何汉字或字母数字，无法作为内容发布。",
                "写入真实正文。",
                (0, len(body)),
            ))
        elif social_count(body)["social_count"] < 20:
            items.append(CheckItem(
                "warn", "body_thin", "正文过短",
                f"【正文】仅 {social_count(body)['social_count']} 字，信息量与完读率都偏低。",
                "补充背景、步骤或结论，让内容对读者有实际价值。",
            ))
        if summary.strip() and social_count(summary)["social_count"] < 10:
            items.append(CheckItem(
                "info", "summary_thin", "简介过短",
                f"【简介】仅 {social_count(summary)['social_count']} 字。",
                "简介建议 20-80 字，概括内容亮点。",
            ))

        # Emoji
        title_emoji = len(_EMOJI_RE.findall(title))
        body_emoji = len(_EMOJI_RE.findall(body))
        if title_emoji > 4:
            items.append(CheckItem(
                "warn", "too_many_emoji_title", "标题 Emoji 过多",
                f"【标题】含 {title_emoji} 个 Emoji，通常 0-2 个即可。",
                "把 Emoji 留给正文分点，标题保留 1 个强调就够。",
            ))
        body_total = max(1, social_count(body)["social_count"])
        if body_emoji > 25 or (body_emoji >= 12 and body_emoji / body_total > 0.25):
            items.append(CheckItem(
                "warn", "too_many_emoji", "正文 Emoji 过多",
                f"【正文】含 {body_emoji} 个 Emoji（正文约 {body_total} 字），显得堆砌。",
                "每段保留 1-2 个用于分点即可，其余删除。",
            ))
        if body.strip() and body_emoji == 0 and (key in _XHS_KEYS):
            items.append(CheckItem(
                "info", "emoji_suggest", "小红书正文建议适度使用 Emoji",
                "【正文】未使用 Emoji 分点，纯文字在信息流里可读性偏弱。",
                "用小标题 Emoji 分点，每段 1-3 行。",
            ))

        # 换行 / 段落
        if re.search(r"\n[ \t]*\n[ \t]*\n", body):
            items.append(CheckItem(
                "warn", "too_many_blank_lines", "存在连续空行",
                "【正文】出现 2 个以上连续空行，多数平台会折叠或导致排版错乱。",
                "最多保留 1 个空行作为段落分隔。",
            ))
        long_lines = [ln for ln in body.splitlines() if social_count(ln)["social_count"] > 400]
        if long_lines:
            items.append(CheckItem(
                "info", "long_paragraph", "存在超长段落",
                f"【正文】最长段落约 {max(social_count(ln)['social_count'] for ln in long_lines)} 字，"
                "手机端阅读体验差。",
                "按语义拆成 3-5 行一段，小红书建议每段 1-3 行。",
            ))
        if body.strip() and "\n" not in body and social_count(body)["social_count"] > 200:
            items.append(CheckItem(
                "warn", "no_line_break", "正文没有任何换行",
                "【正文】超过 200 字且无换行，是一整块文字墙。",
                "按逻辑分段，每段之间留一个空行。",
            ))

        # 符号堆砌 / 不可见字符 / Markdown 残留
        stack = _SYMBOL_STACK_RE.search(body)
        if stack is None:
            stack = _SYMBOL_STACK_RE.search(title)
        if stack:
            items.append(CheckItem(
                "warn", "symbol_stack", "存在连续重复符号",
                f"【正文】出现「{stack.group(0)[:12]}」这类连续重复符号，观感差且可能被判低质。",
                "保留 1 个标点即可。",
                stack.span(),
            ))
        inv = _INVISIBLE_RE.search(body)
        if inv:
            items.append(CheckItem(
                "info", "invisible_char", "存在不可见字符",
                "【正文】检测到零宽字符，可能来自复制粘贴，平台可能判为异常排版。",
                "重新粘贴为纯文本。",
                inv.span(),
            ))
        residues: list[str] = []
        for name, pattern in _MARKDOWN_RESIDUE:
            if pattern.search(body) or pattern.search(title):
                residues.append(name)
        if residues:
            items.append(CheckItem(
                "warn", "markdown_residue", "残留 Markdown 语法",
                "【正文】检测到未转换的 Markdown 标记：" + "、".join(residues) + "。",
                "发布前转换为纯文本（加粗改为【】或空行强调，链接改为文字说明）。",
            ))

        # 外链
        link = _LINK_RE.search(body) or _LINK_RE.search(title) or _LINK_RE.search(summary)
        if link:
            strict = key in _STRICT_DIVERSION_KEYS
            items.append(CheckItem(
                "error" if strict else "warn", "external_link", "含站外链接",
                f"【正文】出现外链「{link.group(0)[:40]}」"
                + ("；该平台严格限制站外导流，属高风险。" if strict else "，多数平台会因此限流。"),
                "删除链接；如确需分享，改为「同名公众号」「主页」等平台内指引。",
                link.span(),
            ))
        bare = _BARE_DOMAIN_RE.search(body)
        if bare and not (link and link.span()[0] <= bare.span()[0] <= link.span()[1]):
            items.append(CheckItem(
                "info", "bare_domain", "疑似出现裸域名",
                f"【正文】「{bare.group(0)[:40]}」像是域名，也可能只是产品名，请人工确认。",
                "若确为外站域名请删除；若是产品名可忽略（该判定易误报）。",
                bare.span(),
            ))

        # 联系方式
        contact = (
            _PHONE_RE.search(body) or _QQ_RE.search(body) or _WECHAT_RE.search(body)
            or _EMAIL_RE.search(body) or _PHONE_RE.search(summary) or _EMAIL_RE.search(summary)
        )
        contact_words = _contains_any(body + title + summary, _CONTACT_WORDS)
        if contact:
            strict = key in _STRICT_DIVERSION_KEYS
            items.append(CheckItem(
                "error" if strict else "warn", "contact_info", "含联系方式",
                f"【正文】命中联系方式「{contact.group(0)[:24]}」"
                + ("；该平台对导流联系方式零容忍。" if strict else "，多数平台会限流。"),
                "删除手机号 / 微信号 / QQ / 邮箱等联系方式。",
                contact.span(),
            ))
        elif contact_words:
            span = None
            for word in contact_words:
                idx = body.find(word)
                if idx != -1:
                    span = (idx, idx + len(word))
                    break
            items.append(CheckItem(
                "warn", "contact_hint", "含导流引导话术",
                "【正文】出现导流话术：" + "、".join(contact_words) + "。",
                "删除「扫码」「私信」「加群」等引导，改为平台内互动（评论区讨论）。",
                span,
            ))
        evasion = _EVASION_RE.search(body)
        if evasion:
            items.append(CheckItem(
                "warn", "evasion_spelling", "疑似谐音/变体绕过检测",
                f"【正文】「{evasion.group(0)}」像是用缩写或变体写联系方式，平台风控会识别并降权。",
                "不要试图绕过检测，直接删除导流意图。",
                evasion.span(),
            ))

    # ── 3. 媒体附件 ──────────────────────────────────────────────────────
    def _check_media(self, pl: PublishPayload, lm: PlatformLimits, key: str,
                     items: list[CheckItem]) -> None:
        media = self._media(pl)
        requires = bool(getattr(lm, "requires_media", False))
        media_max = int(getattr(lm, "media_max", 0) or 0)
        kinds = tuple(_s(k).strip().lower() for k in (getattr(lm, "media_kinds", ()) or ()) if _s(k).strip())
        supports_video = bool(getattr(lm, "supports_video", False))

        if requires and not media:
            items.append(CheckItem(
                "error", "media_missing", "缺少必需的媒体附件",
                "【媒体】该平台要求必须带图片/视频，当前未选择任何文件。",
                "添加媒体文件后再发布。",
            ))
        if media_max > 0 and len(media) > media_max:
            items.append(CheckItem(
                "error", "media_too_many", "媒体数量超出平台上限",
                f"【媒体】当前 {len(media)} 个，平台上限 {media_max} 个。",
                f"删除 {len(media) - media_max} 个，把最关键的内容放在前几张。",
            ))
        if not media:
            return

        kind_counts: dict[str, int] = {}
        for path in media:
            p = Path(path)
            ext = p.suffix.lower()
            kind = _kind_of_ext(ext)
            kind_counts[kind or "unknown"] = kind_counts.get(kind or "unknown", 0) + 1

            if not p.exists():
                items.append(CheckItem(
                    "error", "media_not_found", "媒体文件不存在",
                    f"【媒体】找不到文件：{path}",
                    "重新选择文件，或检查该文件是否被移动/删除。",
                ))
                continue
            if p.is_dir():
                items.append(CheckItem(
                    "error", "media_is_dir", "媒体路径是文件夹",
                    f"【媒体】{path} 是目录而不是文件。",
                    "选择具体的图片或视频文件。",
                ))
                continue
            try:
                if p.stat().st_size == 0:
                    items.append(CheckItem(
                        "error", "media_empty_file", "媒体文件为空",
                        f"【媒体】{p.name} 大小为 0 字节，无法上传。",
                        "重新导出该文件。",
                    ))
                    continue
            except OSError as exc:
                items.append(CheckItem(
                    "warn", "media_unreadable", "媒体文件无法读取",
                    f"【媒体】{p.name} 读取失败：{exc}",
                    "检查文件是否被占用或权限不足。",
                ))
                continue

            if kind is None:
                items.append(CheckItem(
                    "warn", "media_unknown_kind", "无法识别的媒体类型",
                    f"【媒体】{p.name} 的扩展名 {ext or '（无）'} 不在常见图片/视频列表内。",
                    "确认文件可正常上传，或转换为常见格式（jpg/png/mp4）。",
                ))
                continue
            if kinds and kind not in kinds:
                items.append(CheckItem(
                    "error", "media_kind_mismatch", "媒体类型不符合平台要求",
                    f"【媒体】{p.name} 是{'图片' if kind == 'image' else '视频' if kind == 'video' else '音频'}，"
                    f"该平台只接受：{'、'.join(kinds)}。",
                    "替换为符合平台要求的媒体文件。",
                ))
            if kind == "video" and not supports_video:
                items.append(CheckItem(
                    "error", "media_video_unsupported", "该平台不支持视频",
                    f"【媒体】{p.name} 是视频，但该平台的 supports_video 为 False。",
                    "改用图片，或改发到支持视频的平台。",
                ))

        if kinds and "image" in kinds and kind_counts.get("image", 0) and kind_counts["image"] < 3:
            if key in _XHS_KEYS:
                items.append(CheckItem(
                    "info", "xhs_card_count", "小红书图文建议 3 张以上",
                    f"【媒体】当前图片 {kind_counts['image']} 张（平台允许 1-{media_max or 9} 张）。",
                    "小红书图文建议 3-9 张，首图作为封面钩子。",
                ))

    # ── 4. 敏感词 / 风险词（词表驱动） ────────────────────────────────────
    def _check_words(self, pl: PublishPayload, lm: PlatformLimits, key: str,
                     items: list[CheckItem]) -> None:
        title = _s(getattr(pl, "title", ""))
        body = _s(getattr(pl, "body", ""))
        summary = _s(getattr(pl, "summary", ""))
        topic = _s(getattr(pl, "topic", ""))
        tag_text = " ".join(self._tags(pl))
        fields: tuple[tuple[str, str], ...] = (
            ("标题", title), ("正文", body), ("标签", tag_text),
            ("简介", summary), ("主题", topic),
        )
        rules = self._bank.rules
        per_field_cap = 12
        for label, text in fields:
            if not text.strip():
                continue
            hits = _dedupe_hits(_iter_hits(text, rules))
            if not hits:
                continue
            for idx, (rule, start, end) in enumerate(hits):
                if idx >= per_field_cap:
                    remain = len(hits) - per_field_cap
                    items.append(CheckItem(
                        "info", "risk_word_overflow", "还有更多风险词未逐条列出",
                        f"【{label}】另有 {remain} 处风险词命中（同类项过多，已合并展示）。",
                        "建议整体重写该段落，而不是逐词替换。",
                    ))
                    break
                level = rule.level
                downgraded = ""
                if level == "error" and rule.word in SOFT_LEVEL_DOWNGRADE:
                    level = "warn"
                    downgraded = "（该词在正常语境中常见，已自动降级为建议项）"
                if level == "warn" and rule.code == "contact_diversion" and key in _STRICT_DIVERSION_KEYS:
                    level = "error"
                    downgraded = "（该平台对站外导流零容忍，已升为必须修改）"
                items.append(CheckItem(
                    level=level,
                    code=rule.code,
                    title=f"命中风险词：{rule.section}",
                    detail=(
                        f"【{label}】命中「{rule.word}」（分节：{rule.section}）：{rule.reason}{downgraded}"
                    ),
                    suggestion=rule.suggestion,
                    span=(start, end),
                ))

    # ── 5. 密钥 / 内部信息（正则驱动，迁移自 content_guard.py） ────────────
    def _check_secrets(self, pl: PublishPayload, lm: PlatformLimits, key: str,
                       items: list[CheckItem]) -> None:
        title = _s(getattr(pl, "title", ""))
        body = _s(getattr(pl, "body", ""))
        summary = _s(getattr(pl, "summary", ""))
        topic = _s(getattr(pl, "topic", ""))
        tag_text = " ".join(self._tags(pl))
        fields: tuple[tuple[str, str], ...] = (
            ("标题", title), ("正文", body), ("标签", tag_text),
            ("简介", summary), ("主题", topic),
        )
        for label, text in fields:
            if not text.strip():
                continue
            for pattern, level, code, hint, suggestion in _SECRET_PATTERNS:
                match = pattern.search(text)
                if not match:
                    continue
                snippet = match.group(0)
                if len(snippet) > 24:
                    snippet = snippet[:12] + "…" + snippet[-6:]
                items.append(CheckItem(
                    level=level,
                    code=code,
                    title="疑似泄露敏感信息",
                    detail=f"【{label}】{hint}：「{snippet}」。发出后无法撤回。",
                    suggestion=suggestion,
                    span=match.span(),
                ))

    # ── 6. 平台特有规则（提炼自 Easel platform-*.md） ─────────────────────
    def _check_platform_rules(self, pl: PublishPayload, lm: PlatformLimits, key: str,
                              items: list[CheckItem]) -> None:
        title = _s(getattr(pl, "title", ""))
        body = _s(getattr(pl, "body", ""))
        text = f"{title}\n{body}"
        extra = self._extra(pl)

        # 小红书：风控强制人工确认（企划书 3.2 模块三）
        if key in _XHS_KEYS:
            items.append(CheckItem(
                "info", "xhs_manual_confirm", "小红书需人工确认后发布",
                "小红书自动化发布存在风控风险，Stent 默认强制人工确认，不提供全自动发布开关。",
                "请在预览页确认文案、话题与图片后再点击发布。",
            ))
            body_len = social_count(body)["social_count"]
            if body.strip() and (body_len < 200 or body_len > 1000):
                items.append(CheckItem(
                    "info", "xhs_body_range", "小红书正文长度偏离推荐区间",
                    f"【正文】当前 {body_len} 字，小红书推荐 300-800 字。",
                    "过短显得单薄，过长读者会划走；按 300-800 字调整。",
                ))
            tags = self._tags(pl)
            if tags and len(tags) < 5:
                items.append(CheckItem(
                    "info", "xhs_tags_range", "小红书标签偏少",
                    f"【标签】当前 {len(tags)} 个，小红书推荐 5-10 个。",
                    "补足到 5-10 个，覆盖搜索词与话题。",
                ))
            if "买它" in text or "闭眼入" in text or "冲冲冲" in text:
                items.append(CheckItem(
                    "warn", "xhs_hard_sell", "小红书硬广式话术",
                    "【正文】出现「买它」类刷屏式话术，容易触发平台降权。",
                    "改为真实使用感受，弱化叫卖感。",
                ))
            if _contains_any(text, ("赞助", "品牌方", "合作款", "品牌寄送", "商家提供")) \
                    and not extra.get("brand_deal"):
                items.append(CheckItem(
                    "warn", "xhs_soft_ad", "疑似软广未标注",
                    "【正文】出现品牌合作相关表述，但未声明利益关系。",
                    "如为商业合作，请通过平台「合作」报备并在文案中声明；自购分享请注明「自购无广」。",
                ))

        # 抖音：诱导互动 / AI 标注 / 文案长度
        if key in _DOUYIN_KEYS:
            induce = _contains_any(text, _DOUYIN_INDUCE)
            if induce:
                items.append(CheckItem(
                    "warn", "douyin_induce_engagement", "抖音限制诱导互动",
                    "【正文】出现显式索要互动的表述：" + "、".join(induce) + "。",
                    "删除机械式互动索取，改为内容内自然引导（如结尾提问）。",
                ))
            body_len = social_count(body)["social_count"]
            if body_len > 55:
                items.append(CheckItem(
                    "info", "douyin_caption_length", "抖音文案超出可见区间",
                    f"【正文】当前 {body_len} 字，抖音文案约 55 字内可见，超出部分被折叠。",
                    "把钩子前置到前 55 字，其余内容放到字幕或评论区。",
                ))
            if not self._media(pl):
                items.append(CheckItem(
                    "warn", "douyin_need_video", "抖音需要视频素材",
                    "【媒体】未附加任何媒体文件，抖音为视频平台，没有视频无法发布。",
                    "选择竖版 9:16 视频（15-60 秒为主）。",
                ))

        # B站：标题党 / 恰饭标注 / 转载来源
        if key in _BILI_KEYS:
            clickbait = _contains_any(text, _BILI_CLICKBAIT)
            if clickbait:
                items.append(CheckItem(
                    "warn", "bili_clickbait", "B站反感封面党/标题党",
                    "【标题】出现夸张引流词：" + "、".join(clickbait) + "。",
                    "标题可以适度优化，但必须与内容相符，否则会被差评反噬。",
                ))
            if _contains_any(text, _BILI_BRAND_WORDS) and not extra.get("brand_deal"):
                items.append(CheckItem(
                    "info", "bili_brand_deal", "B站商业合作需标注",
                    "【正文】出现品牌合作相关表述，但未标记商业合作。",
                    "如含品牌植入或口播推荐，请使用平台「商业合作」标签并声明利益关系。",
                ))
            if _contains_any(text, _REPRINT_WORDS) and not _s(extra.get("source_url")).strip():
                items.append(CheckItem(
                    "warn", "bili_reprint_source", "B站转载需填写来源",
                    "【正文】出现转载/搬运表述，但未提供原视频来源链接。",
                    "上传时选择「转载」并填写原视频来源链接。",
                ))
            if social_count(title)["social_count"] > 40:
                items.append(CheckItem(
                    "info", "bili_title_length", "B站标题偏长",
                    f"【标题】当前 {social_count(title)['social_count']} 字，B站推荐 40 字以内。",
                    "精简标题，把信息量放在简介前两行。",
                ))

        # 知乎：长文导向
        if key in _ZHIHU_KEYS:
            body_len = social_count(body)["social_count"]
            if body.strip() and body_len < 300:
                items.append(CheckItem(
                    "info", "zhihu_body_range", "知乎回答偏短",
                    f"【正文】当前 {body_len} 字，知乎推荐 800-3000 字、有信息增量的长文。",
                    "补充案例、数据与论证过程。",
                ))

        # 微博：字数与话题格式
        if key in _WEIBO_KEYS:
            body_len = social_count(body)["social_count"]
            if body_len > 2000:
                items.append(CheckItem(
                    "error", "weibo_body_too_long", "微博正文超出上限",
                    f"【正文】当前 {body_len} 字，微博正文上限 2000 字。",
                    "删减或改为长文/头条文章。",
                ))
            elif body_len > 140:
                items.append(CheckItem(
                    "info", "weibo_body_fold", "微博正文会被折叠",
                    f"【正文】当前 {body_len} 字，超过 140 字会折叠。",
                    "把最抓人的信息放在前 140 字。",
                ))

    # ── 7. 风控提示（洗稿 / 版权 / AI 标注 / 敏感赛道） ───────────────────
    def _check_risk(self, pl: PublishPayload, lm: PlatformLimits, key: str,
                    items: list[CheckItem]) -> None:
        title = _s(getattr(pl, "title", ""))
        body = _s(getattr(pl, "body", ""))
        summary = _s(getattr(pl, "summary", ""))
        text = f"{title}\n{body}\n{summary}"
        extra = self._extra(pl)
        media = self._media(pl)

        # 版权：素材来源提示（copyright-guide.md 六、自查清单）
        has_image = any(_kind_of_ext(Path(m).suffix) == "image" for m in media)
        has_video = any(_kind_of_ext(Path(m).suffix) == "video" for m in media)
        source_marks = ("图源", "来源", "摄影", "自摄", "原创图", "侵权", "授权",
                        "unsplash", "pexels", "pixabay")
        if has_image and not _contains_any(text.lower(), source_marks) and not extra.get("media_source"):
            items.append(CheckItem(
                "info", "copyright_image_source", "图片素材建议注明来源",
                "【媒体】内容带图片但正文未提到图片来源，搬运检测与维权风险较高。",
                "使用自有/授权素材；引用他人图片注明「图源：@原作者」，或改用免费商用图库。",
            ))
        if has_video and not _s(extra.get("bgm_source")).strip():
            items.append(CheckItem(
                "info", "copyright_bgm", "BGM 版权需自行确认",
                "【媒体】带视频但未填写 BGM 来源，跨平台分发时版权风险较高。",
                "优先使用平台内置曲库；跨平台发布请改用 CC0 或已授权的音乐。",
            ))

        # AI 生成标注（copyright-guide.md 五、AI 生成内容标注要求）
        ai_hit = next(
            (w for w in ("由AI生成", "AI生成", "AI撰写", "AI创作", "AI代笔", "人工智能生成",
                         "大语言模型", "AI绘图", "AI绘画")
             if w in text),
            "",
        )
        if ai_hit and not extra.get("ai_declared"):
            level = "warn" if key in (_DOUYIN_KEYS | _XHS_KEYS) else "info"
            items.append(CheckItem(
                level, "ai_label_hint", "AI 生成内容建议标注",
                f"【正文】出现「{ai_hit}」类表述，部分平台要求或建议标注 AI 生成内容。",
                "拍板前确认：营销内容建议删除该措辞；确为 AI 生成素材请按平台要求标注。",
            ))

        # 引用规范：数据/结论缺来源（washing-patterns.md 三、引用规范检查）
        has_data = bool(re.search(
            r"\d+(?:\.\d+)?\s*%|百分之\s*\d+|数据显示|调查显示|研究表明|统计显示|报告显示", text))
        has_source = bool(re.search(r"来源|出处|据.{0,6}(?:报道|统计|报告|研究)|引自|引用自|数据来自", text))
        if has_data and not has_source:
            items.append(CheckItem(
                "info", "missing_source", "数据未标注来源",
                "【正文】出现数据/百分比或「研究表明」类表述，但未看到来源标注。",
                "补充数据来源与时间（如「据 XX 报告 2026 年数据」），提升可信度并降低风险。",
            ))

        # 洗稿嫌疑：与参考原文的连续重合片段（仅定性，不给相似度分数）
        reference = _s(extra.get("reference_text"))
        if reference.strip() and body.strip():
            span = _find_overlap_span(body, reference)
            if span:
                snippet = body[span[0]:span[1]]
                if len(snippet) > 40:
                    snippet = snippet[:40] + "…"
                items.append(CheckItem(
                    "warn", "washing_overlap", "疑似与参考原文大段重合",
                    f"【正文】与参考原文存在连续重合片段（位置 {span[0]}-{span[1]}）：「{snippet}」"
                    "。本检查只做本地连续比对，不计算相似度，请人工判断是否属洗稿。",
                    "用自己的语言重写该段：换论证角度、补充个人经验与具体细节、加入自己的判断。",
                    span,
                ))

        # 洗稿特征词汇总提示（词表的 washing_pattern 命中已在词表检查里逐条给出）
        washing_words = _contains_any(text, ("侵删", "来源网络", "综合整理", "洗稿", "搬运"))
        if washing_words:
            items.append(CheckItem(
                "info", "washing_hint", "原创度自查提示",
                "【正文】出现来源标注类措辞：" + "、".join(washing_words)
                + "。Easel 的原创度评估为定性判断，此处只提示风险。",
                "补充作者名与出处，并确保有个人经验、独家数据或独立观点等原创成分。",
            ))

        # 敏感赛道预警（scoring-dimensions.md「敏感赛道合规预警」）
        tracks: list[str] = []
        for word, track in _SENSITIVE_TRACKS.items():
            if word in text and track not in tracks:
                tracks.append(track)
        if tracks:
            items.append(CheckItem(
                "info", "sensitive_track", "敏感赛道合规预警",
                "【正文】涉及敏感赛道：" + "、".join(tracks)
                + "。这类内容平台审核更严，措辞需谨慎。",
                "避免功效承诺与绝对化表述；专业资质类内容建议补充资质说明。",
            ))
