"""账号画像服务（企划书 3.1「跨模块共享的账号画像」）。

六维画像（复用 Easel ``profiles/<name>/`` 的 identity / style / audience /
platforms / preferences / memory 结构）：
    定位 → 风格 → 受众 → 平台 → 偏好 → 记忆

画像影响内容创作的风格与选题（企划书 3.2 模块二「增强」），
并接收数据分析模块回流的经验（模块四「回流」）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from ..core.db import db
from ..core.llm import LLMClient, LLMError, Message

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Dimension:
    key: str
    label: str
    hint: str
    placeholder: str


#: 六维定义（顺序即 UI 展示顺序）
DIMENSIONS: tuple[Dimension, ...] = (
    Dimension(
        "positioning",
        "定位",
        "账号是做什么的、给谁看、与同类账号的差异",
        "例：一二线城市 25-35 岁职场女性，分享低成本通勤穿搭与平价好物，主打真实试穿与性价比",
    ),
    Dimension(
        "style",
        "风格",
        "语气、句式、称呼、口头禅与表达禁区",
        "例：口语化、第一人称、短句为主，爱用「姐妹们」开头，不用夸张营销词，不喊口号",
    ),
    Dimension(
        "audience",
        "受众",
        "目标人群的画像、需求与痛点",
        "例：月薪 8k-15k，通勤 1 小时，预算有限但想穿得体面，最大痛点是「显胖」和「不会搭」",
    ),
    Dimension(
        "platforms",
        "平台",
        "主阵地、分发平台与各平台的人设差异",
        "例：小红书为主（日更图文），抖音发口播切片（每周 3 条），知乎偶尔回答穿搭问题",
    ),
    Dimension(
        "preferences",
        "偏好",
        "选题偏好、内容形式、发布节奏与标签习惯",
        "例：偏好平价好物与避雷，标题带数字，正文 300-500 字，常用标签 #通勤穿搭 #平价好物",
    ),
    Dimension(
        "memory",
        "记忆",
        "历史经验与数据分析回流的结论",
        "（由数据分析模块自动写入，也可手动补充）",
    ),
)

DIMENSION_MAP = {d.key: d for d in DIMENSIONS}


class ProfileService:
    """画像读写与 prompt 组装。"""

    def __init__(self, database: Any = None) -> None:
        self._db = database or db

    # -- 读写 ------------------------------------------------------------
    def get(self) -> dict[str, Any]:
        return self._db.get_profile()

    def save(self, **fields: Any) -> dict[str, Any]:
        allowed = {d.key for d in DIMENSIONS}
        payload = {k: v for k, v in fields.items() if k in allowed}
        if payload:
            self._db.save_profile(**payload)
        return self.get()

    def clear(self) -> dict[str, Any]:
        return self.save(
            positioning="", style="", audience="", platforms=[], preferences="", memory=[]
        )

    def append_memory(self, text: str) -> None:
        if text and text.strip():
            self._db.append_memory(text.strip())

    def clear_memory(self) -> None:
        self._db.save_profile(memory=[])

    def recent_memory(self, limit: int = 10) -> list[dict[str, Any]]:
        memory = list(self.get().get("memory") or [])
        return memory[-limit:][::-1]

    # -- 状态 ------------------------------------------------------------
    def is_configured(self) -> bool:
        profile = self.get()
        return any(
            str(profile.get(d.key) or "").strip()
            for d in DIMENSIONS
            if d.key != "memory"
        )

    def completeness(self) -> int:
        """画像完整度百分比，用于 UI 进度条。"""
        profile = self.get()
        scored = 0
        total = 0
        for dim in DIMENSIONS:
            if dim.key == "memory":
                continue
            total += 1
            value = profile.get(dim.key)
            if isinstance(value, (list, tuple)):
                if value:
                    scored += 1
            elif str(value or "").strip():
                scored += 1
        return int(scored / total * 100) if total else 0

    def missing_dimensions(self) -> list[str]:
        profile = self.get()
        out = []
        for dim in DIMENSIONS:
            if dim.key == "memory":
                continue
            value = profile.get(dim.key)
            if not (list(value) if isinstance(value, (list, tuple)) else str(value or "").strip()):
                out.append(dim.label)
        return out

    # -- prompt 组装 ------------------------------------------------------
    def to_prompt(self, *, memory_limit: int = 8, platform: str = "") -> str:
        """把六维画像渲染成注入 LLM 的 prompt 块（无画像时返回空串）。"""
        profile = self.get()
        lines: list[str] = []
        for dim in DIMENSIONS:
            if dim.key == "memory":
                continue
            value = profile.get(dim.key)
            if isinstance(value, (list, tuple)):
                value = "、".join(str(v) for v in value if v)
            text = str(value or "").strip()
            if text:
                lines.append(f"【{dim.label}】{text}")

        memory_items = list(profile.get("memory") or [])[-memory_limit:]
        if memory_items:
            lines.append("【历史经验（来自数据分析回流，权重最高，务必遵循）】")
            for entry in memory_items:
                text = entry.get("text") if isinstance(entry, dict) else str(entry)
                at = entry.get("at", "") if isinstance(entry, dict) else ""
                if text:
                    lines.append(f"- {text}" + (f"（{at}）" if at else ""))

        if not lines:
            return ""
        header = "=== STENT 账号画像 ==="
        if platform:
            header += f"（本次目标平台：{platform}）"
        return "\n".join([header, *lines, "=== 画像结束 ==="])

    def export_markdown(self) -> str:
        """导出为 Markdown（复用 Easel 的六维文件命名，便于迁移）。"""
        profile = self.get()
        names = {
            "positioning": "identity.md",
            "style": "style.md",
            "audience": "audience.md",
            "platforms": "platforms.md",
            "preferences": "preferences.md",
            "memory": "memory.md",
        }
        chunks = ["# Stent 账号画像", ""]
        for dim in DIMENSIONS:
            chunks.append(f"## {names[dim.key]} · {dim.label}")
            value = profile.get(dim.key)
            if dim.key == "memory":
                items = value or []
                if items:
                    for entry in items:
                        text = entry.get("text") if isinstance(entry, dict) else str(entry)
                        at = entry.get("at", "") if isinstance(entry, dict) else ""
                        chunks.append(f"- {text}" + (f"  <!-- {at} -->" if at else ""))
                else:
                    chunks.append("_暂无_")
            else:
                if isinstance(value, (list, tuple)):
                    value = "、".join(str(v) for v in value if v)
                chunks.append(str(value or "").strip() or "_未填写_")
            chunks.append("")
        return "\n".join(chunks)

    # -- LLM 辅助 --------------------------------------------------------
    def draft_from_text(self, text: str, llm: LLMClient, *, cancel: Any = None) -> dict[str, str]:
        """把一段自我介绍凝练成六维画像草稿，供引导页「一键生成画像」使用。"""
        system = (
            "你是社媒账号运营顾问。用户会给一段自我介绍，请把它整理成结构化的账号画像。"
            "严格只输出 JSON，不要任何解释、不要 markdown 代码块。"
            'JSON 结构：{"positioning":"","style":"","audience":"","platforms":"","preferences":""}'
            "每个字段控制在 60 字以内的中文，信息不足时给出合理推断。"
        )
        raw = llm.chat(
            [Message("system", system), Message("user", text.strip())],
            temperature=0.4,
            cancel=cancel,
        )
        return self._parse_draft(raw)

    @staticmethod
    def _parse_draft(raw: str) -> dict[str, str]:
        import json
        import re

        text = (raw or "").strip()
        text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
        try:
            data = json.loads(text)
        except ValueError:
            match = re.search(r"\{.*\}", text, re.S)
            if not match:
                raise LLMError("画像草稿解析失败：模型未返回合法 JSON") from None
            try:
                data = json.loads(match.group(0))
            except ValueError as exc:
                raise LLMError(f"画像草稿解析失败：{exc}") from exc
        if not isinstance(data, dict):
            raise LLMError("画像草稿解析失败：返回结构不是对象")
        keys = {d.key for d in DIMENSIONS if d.key != "memory"}
        return {k: str(data.get(k, "") or "").strip() for k in keys}


profile_service = ProfileService()
