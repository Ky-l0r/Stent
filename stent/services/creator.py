"""内容创作服务（企划书 3.2 模块二）。

- 输入：主题 / 热点 / 素材 + 目标平台
- 输出：正文文案、多个标题候选、简介、标签
- 特性：平台适配（小红书笔记体 / 抖音口播稿 / 知乎长文 / B 站简介）、
  账号画像影响输出风格、流式输出且可随时中断
- 复用：Easel 的 social-content 平台规范、skill-quality-gate 质量门、
  skill-hook-generator 钩子公式（裁剪后内联为 prompt 规则）
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Iterator

from ..core.llm import LLMClient, LLMError, Message
from ..services.profile import profile_service

log = logging.getLogger(__name__)

#: 流式解析用的分段标记
SECTION_BODY = "正文"
SECTION_TITLES = "标题"
SECTION_SUMMARY = "简介"
SECTION_TAGS = "标签"
SECTION_ORDER = (SECTION_BODY, SECTION_TITLES, SECTION_SUMMARY, SECTION_TAGS)


# --------------------------------------------------------------------------
# 平台体例（数据来源：Easel social-content/references/platform-specs.md，已裁剪）
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class PlatformStyle:
    key: str
    label: str
    content_type: str
    title_max: int
    title_guide: str
    body_min: int
    body_max: int
    body_guide: str
    tags_max: int
    tag_guide: str
    taboos: str
    best_hours: str


PLATFORM_STYLES: dict[str, PlatformStyle] = {
    "xiaohongshu": PlatformStyle(
        key="xiaohongshu",
        label="小红书",
        content_type="图文笔记",
        title_max=20,
        title_guide="≤20 字，可带 1 个 emoji，制造好奇或直给利益点，不要标题党到与内容不符",
        body_min=300,
        body_max=800,
        body_guide=(
            "300-800 字。口语化、闺蜜分享感，第一人称。短段落，每段 1-3 行，"
            "用 emoji 做分点符号。开头第一句就是钩子（痛点/反差/结果前置）。"
            "结尾引导收藏或评论（如「码住」「评论区蹲一个」）。忌硬广、忌说明书口吻"
        ),
        tags_max=10,
        tag_guide="5-10 个，2-4 字短标签为主，混合大词与精准长尾词，放正文末尾",
        taboos="绝对化用语、医疗功效承诺、引流外链、联系方式、夸张营销词",
        best_hours="11-13 点、19-22 点",
    ),
    "douyin": PlatformStyle(
        key="douyin",
        label="抖音",
        content_type="口播稿",
        title_max=55,
        title_guide="≤55 字，钩子前置，用疑问或结果吸引点击，不要平铺直叙",
        body_min=150,
        body_max=600,
        body_guide=(
            "短视频口播稿，150-600 字（对应 30-60 秒）。"
            "结构：0-3 秒钩子（一句扎心提问或反常识结论）→ 3-15 秒抛出冲突/问题 → "
            "中段每 3-5 秒一个信息点或转折 → 结尾 CTA（关注/评论/看最后）。"
            "用短句、口语，标注必要的停顿与重音，不要书面语长句"
        ),
        tags_max=5,
        tag_guide="3-5 个，含 1-2 个热点或挑战类话题",
        taboos="绝对化用语、医疗承诺、诱导性话术、站外引流",
        best_hours="12-13 点、18-22 点",
    ),
    "zhihu": PlatformStyle(
        key="zhihu",
        label="知乎",
        content_type="长文",
        title_max=100,
        title_guide="≤100 字，信息量足，可用「如何评价」「为什么」「是什么体验」等知乎句式",
        body_min=800,
        body_max=3000,
        body_guide=(
            "800-3000 字。开头前 3 行直接给结论或反常识观点，再展开论证。"
            "分点论述，配合案例、数据、亲身经历建立可信度。"
            "专业理性，允许有立场但要有依据，忌口水话与情绪宣泄。结尾引导赞同与收藏"
        ),
        tags_max=5,
        tag_guide="3-5 个，绑定相关话题词，优先大话题带流量",
        taboos="编造数据、夸大疗效、搬运洗稿、外链导流",
        best_hours="12-14 点、20-23 点",
    ),
    "bilibili": PlatformStyle(
        key="bilibili",
        label="B 站",
        content_type="视频简介",
        title_max=80,
        title_guide="≤80 字，信息量足、可带梗或悬念，标题与内容必须相符",
        body_min=80,
        body_max=2000,
        body_guide=(
            "视频简介：前 2 行最关键（会被折叠前展示），交代「这期讲什么、为什么值得看」，"
            "然后分点列出内容要点或时间轴。语气真诚、懂梗，反感硬广。"
            "可引导一键三连与弹幕互动"
        ),
        tags_max=10,
        tag_guide="4-10 个，覆盖分区词与垂类词",
        taboos="标题党与内容不符、恰饭不标注、搬运未授权内容",
        best_hours="18-23 点",
    ),
    "weibo": PlatformStyle(
        key="weibo",
        label="微博",
        content_type="短博",
        title_max=30,
        title_guide="微博无独立标题，此处给出可作为话题导语的一句话（≤30 字）",
        body_min=30,
        body_max=140,
        body_guide=(
            "140 字以内最易传播。短平快，观点鲜明或情绪浓度高。"
            "可用 1-2 个 #话题#（双井号包裹）蹭广场流量，结尾引导转发或评论"
        ),
        tags_max=3,
        tag_guide="1-3 个 #话题#，用双井号包裹",
        taboos="造谣、未证实信息、极端言论",
        best_hours="8-9 点、12 点、20-22 点",
    ),
}

CREATION_PLATFORMS = tuple(PLATFORM_STYLES.keys())


def style_for(platform: str) -> PlatformStyle:
    return PLATFORM_STYLES.get(platform, PLATFORM_STYLES["xiaohongshu"])


# --------------------------------------------------------------------------
# 请求与结果
# --------------------------------------------------------------------------
@dataclass
class CreationRequest:
    topic: str
    platform: str = "xiaohongshu"
    content_type: str = ""
    goal: str = "互动涨粉"
    audience: str = ""
    tone: str = ""
    source_text: str = ""  # 热点详情或素材原文
    extra: str = ""  # 用户补充要求
    variants: int = 3

    @property
    def style(self) -> PlatformStyle:
        return style_for(self.platform)


@dataclass
class CreationResult:
    platform: str
    body: str = ""
    titles: list[str] = field(default_factory=list)
    summary: str = ""
    tags: list[str] = field(default_factory=list)
    raw: str = ""
    usage_hint: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "body": self.body,
            "titles": self.titles,
            "summary": self.summary,
            "tags": self.tags,
            "raw": self.raw,
        }

    @property
    def title(self) -> str:
        return self.titles[0] if self.titles else ""

    def full_body(self) -> str:
        """正文 + 标签，用于发布前检查与预览。"""
        tags = " ".join(t if t.startswith("#") else f"#{t}" for t in self.tags if t)
        return f"{self.body}\n\n{tags}".strip() if tags else self.body


# --------------------------------------------------------------------------
# 流式分段解析
# --------------------------------------------------------------------------
_MARK_RE = re.compile(r"^\s*[【\[]\s*(正文|标题|简介|标签)\s*[】\]]\s*$")


class SectionStreamParser:
    """把流式输出按「【正文】/【标题】/…」标记切分，边收边分类。

    用法::

        parser = SectionStreamParser()
        for delta in llm.stream_chat(...):
            for section, text in parser.feed(delta):
                ...

    最后一个分段通过 :meth:`finish` 冲刷。
    """

    def __init__(self) -> None:
        self.section: str | None = None
        self.sections: dict[str, list[str]] = {s: [] for s in SECTION_ORDER}
        self._buffer = ""
        self._raw: list[str] = []

    def feed(self, delta: str) -> list[tuple[str, str]]:
        """喂入增量文本，返回若干 (段落名, 文本增量) 事件。"""
        if not delta:
            return []
        self._raw.append(delta)
        self._buffer += delta
        events: list[tuple[str, str]] = []
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            events.extend(self._handle_line(line, newline=True))
        return events

    def finish(self) -> list[tuple[str, str]]:
        """冲刷缓冲区中最后一行（无换行结尾的情况）。"""
        events: list[tuple[str, str]] = []
        if self._buffer:
            events.extend(self._handle_line(self._buffer, newline=False))
            self._buffer = ""
        return events

    def _handle_line(self, line: str, *, newline: bool) -> list[tuple[str, str]]:
        match = _MARK_RE.match(line)
        if match:
            self.section = match.group(1)
            return []  # 标记行本身不输出
        if self.section is None:
            # 标记之前的内容（模型偶尔先寒暄）：忽略，避免污染正文
            return []
        text = line + ("\n" if newline else "")
        if text:
            self.sections[self.section].append(text)
        return [(self.section, text)]

    @property
    def raw(self) -> str:
        return "".join(self._raw)

    def section_text(self, name: str) -> str:
        return "".join(self.sections.get(name, []))

    def to_result(self, platform: str) -> CreationResult:
        return CreationResult(
            platform=platform,
            body=self.section_text(SECTION_BODY).strip(),
            titles=parse_titles(self.section_text(SECTION_TITLES)),
            summary=self.section_text(SECTION_SUMMARY).strip(),
            tags=parse_tags(self.section_text(SECTION_TAGS)),
            raw=self.raw,
        )


def parse_titles(text: str) -> list[str]:
    """从「1. xxx / - xxx / xxx」混合格式中提取标题候选。"""
    out: list[str] = []
    for line in (text or "").splitlines():
        cleaned = re.sub(r"^\s*(?:\d+[.、)．]|[-*·•])\s*", "", line).strip()
        cleaned = cleaned.strip("\"'“”「」")
        if cleaned:
            out.append(cleaned)
    return out


def parse_tags(text: str) -> list[str]:
    """提取标签，兼容 #话题#、#话题、纯词列表。"""
    raw = (text or "").strip()
    tags: list[str] = []
    for token in re.split(r"[\s,，、;；|]+", raw):
        token = token.strip().strip("#").strip()
        if not token:
            continue
        if token not in tags:
            tags.append(token)
    # 兜底：整行是 #a #b 形式时上面的切分已覆盖
    return tags


# --------------------------------------------------------------------------
# Prompt 组装
# --------------------------------------------------------------------------
SYSTEM_TEMPLATE = """你是资深中文社媒内容操盘手，为账号主撰写可直接发布的平台原生内容。

【输出格式（必须严格遵守）】
只输出以下四段，段标题独占一行、使用中文方括号，不要寒暄、不要解释、不要 markdown 代码块：
【正文】
（这里写正文）
【标题】
1. （标题候选一）
2. （标题候选二）
3. （标题候选三）
【简介】
（这里写简介/摘要）
【标签】
（标签之间用空格分隔）

【内容质量硬性要求】
1. 平台适配优先：体例、长度、语气、节奏必须符合下面给出的平台规范。
2. 钩子决定成败：第一句必须能让人停下来，用痛点、反差、结果前置或反常识结论。
3. 去 AI 味：禁止使用「首先/其次/最后/总之/综上所述/在这个快节奏的时代/让我们一起/值得注意的是/不仅仅是…更是…」等套话；禁止排比堆砌与万能空话；句式长短交错，像真人在分享。
4. 合规红线：不使用广告法极限词（最、第一、100%、国家级、绝对、永久等），不做医疗功效承诺，不留联系方式、微信号、手机号与站外链接。
5. 信息密度：每一句都要提供信息、情绪或判断，删掉所有可有可无的过渡句。
6. 标题候选要风格互异：分别偏向「利益点」「好奇心」「情绪共鸣」，不要三个同质标题。

【目标平台规范 · {label}】
- 内容类型：{content_type}
- 标题：{title_guide}
- 正文：{body_guide}（目标长度 {body_min}-{body_max} 字）
- 标签：{tag_guide}（最多 {tags_max} 个）
- 平台禁忌：{taboos}
- 建议发布时段：{best_hours}
"""


def build_messages(req: CreationRequest, *, profile_prompt: str = "") -> list[Message]:
    """组装创作请求（复用 Easel 「画像优先、平台规范其次」的 prompt 分层）。"""
    style = req.style
    system = SYSTEM_TEMPLATE.format(
        label=style.label,
        content_type=req.content_type or style.content_type,
        title_guide=style.title_guide,
        body_guide=style.body_guide,
        body_min=style.body_min,
        body_max=style.body_max,
        tag_guide=style.tag_guide,
        tags_max=style.tags_max,
        taboos=style.taboos,
        best_hours=style.best_hours,
    )
    if profile_prompt:
        system += f"\n{profile_prompt}\n（创作时必须匹配上述账号定位与风格；历史经验优先于通用套路。）\n"

    parts = [f"主题：{req.topic.strip()}"]
    if req.source_text.strip():
        parts.append(f"热点/素材原文：{req.source_text.strip()[:1500]}")
    parts.append(f"内容目标：{req.goal or '互动涨粉'}")
    if req.audience.strip():
        parts.append(f"目标受众：{req.audience.strip()}")
    if req.tone.strip():
        parts.append(f"期望调性：{req.tone.strip()}")
    if req.extra.strip():
        parts.append(f"补充要求：{req.extra.strip()}")
    parts.append(f"标题候选数量：{max(1, min(req.variants, 5))} 个")
    parts.append("请直接按要求输出四段内容。")

    return [Message("system", system), Message("user", "\n".join(parts))]


# --------------------------------------------------------------------------
# 服务
# --------------------------------------------------------------------------
class CreatorService:
    """内容创作编排：组装 prompt、流式生成、解析结果。"""

    def __init__(self, profile: Any = None) -> None:
        self._profile = profile or profile_service

    def build_messages(self, req: CreationRequest, *, use_profile: bool = True) -> list[Message]:
        profile_prompt = ""
        if use_profile:
            try:
                profile_prompt = self._profile.to_prompt(platform=req.style.label)
            except Exception:  # pragma: no cover - 画像异常不应阻断创作
                log.warning("画像读取失败，跳过画像注入", exc_info=True)
        return build_messages(req, profile_prompt=profile_prompt)

    # -- 非流式 ----------------------------------------------------------
    def create(
        self,
        req: CreationRequest,
        llm: LLMClient,
        *,
        cancel: Any = None,
        use_profile: bool = True,
        temperature: float | None = None,
    ) -> CreationResult:
        raw = llm.chat(
            self.build_messages(req, use_profile=use_profile),
            temperature=temperature,
            cancel=cancel,
        )
        return self.parse(raw, req.platform)

    # -- 流式 ------------------------------------------------------------
    def create_stream(
        self,
        req: CreationRequest,
        llm: LLMClient,
        *,
        cancel: Any = None,
        use_profile: bool = True,
        temperature: float | None = None,
        on_event: Callable[[str, str], None] | None = None,
    ) -> CreationResult:
        """流式创作；``on_event(section, delta)`` 会被逐片段回调（供 UI 实时上屏）。"""
        parser = SectionStreamParser()
        for delta in llm.stream_chat(
            self.build_messages(req, use_profile=use_profile),
            temperature=temperature,
            cancel=cancel,
        ):
            for section, text in parser.feed(delta):
                if on_event:
                    try:
                        on_event(section, text)
                    except Exception:  # pragma: no cover
                        log.debug("流式回调异常", exc_info=True)
        for section, text in parser.finish():
            if on_event:
                try:
                    on_event(section, text)
                except Exception:  # pragma: no cover
                    pass
        result = parser.to_result(req.platform)
        if not result.body:
            # 模型没按格式输出时，退化为全文当正文
            result.body = parser.raw.strip()
        result.usage_hint = self.hint_for(req.platform, result)
        return result

    # -- 解析 ------------------------------------------------------------
    @staticmethod
    def parse(raw: str, platform: str) -> CreationResult:
        parser = SectionStreamParser()
        parser.feed(raw or "")
        parser.finish()
        result = parser.to_result(platform)
        if not result.body:
            result.body = (raw or "").strip()
        result.usage_hint = CreatorService.hint_for(platform, result)
        return result

    # -- 辅助 ------------------------------------------------------------
    @staticmethod
    def hint_for(platform: str, result: CreationResult) -> str:
        """生成字数与规范提示，帮助用户判断是否需要再改。"""
        style = style_for(platform)
        length = len(result.body or "")
        notes = [f"正文字数 {length}（平台建议 {style.body_min}-{style.body_max}）"]
        if result.title:
            title_len = len(result.title)
            notes.append(f"标题 {title_len} 字（上限 {style.title_max}）")
            if title_len > style.title_max:
                notes.append("标题超出上限，发布前需精简")
        if length < style.body_min:
            notes.append("正文偏短，建议补充细节")
        elif length > style.body_max:
            notes.append("正文偏长，建议精简")
        notes.append(f"建议发布时段：{style.best_hours}")
        return "；".join(notes)

    @staticmethod
    def platforms() -> list[dict[str, str]]:
        return [{"key": s.key, "label": s.label, "content_type": s.content_type} for s in PLATFORM_STYLES.values()]

    # -- 多平台一次成稿 --------------------------------------------------
    def create_multi(
        self,
        req: CreationRequest,
        platforms: Iterable[str],
        llm: LLMClient,
        *,
        cancel: Any = None,
        on_progress: Callable[[str, int], None] | None = None,
    ) -> dict[str, CreationResult | str]:
        """以一份母版主题产出多平台适配版本（企划书 2.2：小团队运营）。"""
        targets = [p for p in platforms if p in PLATFORM_STYLES]
        results: dict[str, CreationResult | str] = {}
        total = max(1, len(targets))
        for idx, platform in enumerate(targets):
            if cancel is not None and getattr(cancel, "is_set", lambda: False)():
                break
            try:
                local_req = CreationRequest(**{**req.__dict__, "platform": platform})
                results[platform] = self.create(local_req, llm, cancel=cancel)
            except Exception as exc:  # noqa: BLE001 - 单个平台失败不影响其他
                log.warning("平台 %s 创作失败：%s", platform, exc)
                results[platform] = f"失败：{exc}"
            if on_progress:
                try:
                    on_progress(style_for(platform).label, int((idx + 1) / total * 100))
                except Exception:
                    pass
        return results

    # -- 标题再生 --------------------------------------------------------
    def regenerate_titles(
        self,
        req: CreationRequest,
        body: str,
        llm: LLMClient,
        *,
        count: int = 3,
        cancel: Any = None,
    ) -> list[str]:
        """基于已定正文重新生成标题候选（复用 Easel skill-hook-generator 的钩子公式）。"""
        style = req.style
        system = (
            f"你是标题优化专家。为下面这篇{style.label}内容生成 {count} 个标题候选。\n"
            f"要求：{style.title_guide}；不超过 {style.title_max} 字；"
            "风格必须互异，分别偏向利益点、好奇心、情绪共鸣；"
            "不使用极限词与夸张营销词。\n"
            "只输出标题，每行一个，不要编号、不要解释、不要引号。"
        )
        raw = llm.chat(
            [Message("system", system), Message("user", f"主题：{req.topic}\n\n正文：\n{body[:2000]}")],
            temperature=0.9,
            cancel=cancel,
        )
        titles = [t for t in parse_titles(raw) if t]
        return titles[:count]

    # -- 质量门 ----------------------------------------------------------
    def quality_check(
        self, result: CreationResult, llm: LLMClient, *, cancel: Any = None
    ) -> dict[str, Any]:
        """复用 Easel skill-quality-gate 的评审维度，对成稿做一次自检打分。"""
        style = style_for(result.platform)
        system = (
            "你是严格的内容质量评审。按四个维度打分（每项 0-10 分）并给出改进建议：\n"
            "1. 钩子强度（首句能否让人停下来）\n"
            "2. 平台匹配度（体例/语气/长度是否符合平台）\n"
            "3. 信息价值（是否有干货、是否有具体细节）\n"
            "4. 真人感（是否像真人分享，有无 AI 套话）\n"
            "严格只输出 JSON："
            '{"hook":0,"platform_fit":0,"value":0,"human":0,"total":0,"issues":["..."],"suggestions":["..."]}'
        )
        user = (
            f"平台：{style.label}\n平台规范：{style.body_guide}\n"
            f"标题：{result.title}\n正文：\n{result.body[:2000]}"
        )
        raw = llm.chat([Message("system", system), Message("user", user)], temperature=0.2, cancel=cancel)
        import json
        import re

        text = re.sub(r"^```(?:json)?|```$", "", (raw or "").strip(), flags=re.MULTILINE).strip()
        try:
            data = json.loads(text)
        except ValueError:
            match = re.search(r"\{.*\}", text, re.S)
            if not match:
                raise LLMError("质量评审解析失败：模型未返回合法 JSON") from None
            data = json.loads(match.group(0))
        if not isinstance(data, dict):
            raise LLMError("质量评审解析失败：返回结构不是对象")
        return data


creator_service = CreatorService()


def iter_sections(text: str) -> Iterator[tuple[str, str]]:
    """把一段完整输出按分段标记拆开（供预览/导入复用）。"""
    parser = SectionStreamParser()
    events = parser.feed(text or "")
    events += parser.finish()
    return iter(events)
