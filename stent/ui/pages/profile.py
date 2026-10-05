"""账号画像页（企划书 3.1「跨模块共享的账号画像」）。

六维配置：定位 / 风格 / 受众 / 平台 / 偏好 / 记忆。
画像影响内容创作的风格与选题，并接收数据分析回流的经验。

界面要点（v1.0.5 打磨）：

- 六维改成一张张可折叠卡片：一屏能看全，点开哪一维就填哪一维，
  标题上直接标出「已填写 / 待补充」，不用展开也知道缺什么
- AI 草稿输入框加高到能写几行，并给一个「填入示例」降低启动成本
- 「记忆」的说明改成人话，并给一个直接跳到「数据分析 · 归因回流」的按钮
- 清空记忆是破坏性操作，改为二次确认
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ...services.profile import DIMENSIONS, profile_service
from ..components import (
    Card,
    CollapsibleCard,
    EmptyState,
    PageHeader,
    make_button,
)
from .base import BasePage

#: 「填入示例」用的自我介绍样例
SAMPLE_INTRO = (
    "我做小红书平价通勤穿搭，粉丝主要是一二线城市 25-35 岁女生，"
    "预算大多在 300 元以内。内容以「实拍 + 上身对比」为主，"
    "语气像闺蜜聊天，喜欢用数字和具体价格说话，不做硬广。"
    "希望多接平价品牌的合作，也在抖音同步发短视频。"
)


class ProfilePage(BasePage):
    key = "profile"
    title = "账号画像"
    icon = "user"

    def build(self) -> None:
        self._editors: dict[str, QTextEdit] = {}
        self._dim_cards: dict[str, CollapsibleCard] = {}
        self._worker = None
        self._auto_expanded = False

        header = PageHeader(
            "账号画像",
            "六维画像决定创作的语气、选题与平台适配；数据分析的结论会回流到「记忆」",
        )
        header.add_action(self.status_light)
        self.save_button = make_button("保存画像", icon="save", theme=self.ctx.theme, primary=True)
        self.save_button.clicked.connect(self.save)
        header.add_action(self.save_button)
        self.export_button = make_button("导出 Markdown", icon="external", theme=self.ctx.theme, ghost=True)
        self.export_button.clicked.connect(self.export_markdown)
        header.add_action(self.export_button)
        self.add(header)

        # ---- 完整度（常驻，很轻）----
        progress_card = Card(padding=14, spacing=6)
        self.add(progress_card)
        row = QHBoxLayout()
        row.setSpacing(10)
        self.completeness_label = QLabel("画像完整度 0%")
        self.completeness_label.setObjectName("CardTitle")
        row.addWidget(self.completeness_label)
        row.addStretch(1)
        self.missing_label = QLabel()
        self.missing_label.setObjectName("CardHint")
        row.addWidget(self.missing_label)
        progress_card.add_layout(row)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(7)
        progress_card.add(self.progress)

        # ---- 滚动区：折叠卡片一张张排下去 ----
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(12)

        layout.addWidget(self._build_assist_card())
        for dim in DIMENSIONS:
            if dim.key == "memory":
                continue
            layout.addWidget(self._build_dimension_card(dim))
        layout.addWidget(self._build_memory_card())
        layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(inner)
        self.add(scroll, 1)

    # ------------------------------------------------------------------
    def _build_assist_card(self) -> QWidget:
        card = CollapsibleCard(
            "用 AI 生成画像草稿",
            icon="sparkles",
            hint="粘贴一段自我介绍，AI 会整理成六维画像",
            theme=self.ctx.theme,
            expanded=False,
        )
        intro = QLabel(
            "把自我介绍、账号说明或过往内容风格粘进来，AI 会拆成六维填好草稿，"
            "你再逐条确认修改即可。"
        )
        intro.setObjectName("FieldHint")
        intro.setWordWrap(True)
        card.add(intro)

        self.assist_input = QTextEdit()
        self.assist_input.setPlaceholderText(
            "例：我做小红书平价通勤穿搭，粉丝主要是一二线城市 25-35 岁女生，"
            "预算 300 以内，语气像闺蜜聊天…"
        )
        # 这里可能一次粘贴几百字，给足高度免得边写边滚
        self.assist_input.setMinimumHeight(132)
        card.add(self.assist_input)

        row = QHBoxLayout()
        row.setSpacing(8)
        sample = make_button("填入示例", theme=self.ctx.theme, ghost=True)
        sample.setToolTip("填一段示例自我介绍，可在此基础上改写")
        sample.clicked.connect(lambda: self.assist_input.setPlainText(SAMPLE_INTRO))
        row.addWidget(sample)
        row.addStretch(1)
        self.assist_button = make_button("生成画像草稿", icon="sparkles", theme=self.ctx.theme, primary=True)
        self.assist_button.clicked.connect(self.generate_draft)
        row.addWidget(self.assist_button)
        card.add_layout(row)
        return card

    def _build_dimension_card(self, dim) -> QWidget:
        card = CollapsibleCard(
            dim.label, icon="user", hint=dim.hint, theme=self.ctx.theme, expanded=False
        )
        hint = QLabel(dim.hint)
        hint.setObjectName("FieldHint")
        hint.setWordWrap(True)
        card.add(hint)
        editor = QTextEdit()
        editor.setPlaceholderText(dim.placeholder)
        editor.setFixedHeight(84 if dim.key != "style" else 96)
        self._editors[dim.key] = editor
        card.add(editor)
        self._dim_cards[dim.key] = card
        return card

    def _build_memory_card(self) -> QWidget:
        card = CollapsibleCard("记忆 · 创作经验", icon="chart", theme=self.ctx.theme, expanded=True)
        # 说明改成人话：不解释「归因回流」是什么，只讲它对你有什么用
        hint = QLabel(
            "这里记录你的创作经验，AI 生成内容时会优先参考。"
            "数据表现好的内容会自动总结成经验写进来，你也可以手动补一条。"
        )
        hint.setObjectName("FieldHint")
        hint.setWordWrap(True)
        card.add(hint)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.memory_input = QLineEdit()
        self.memory_input.setPlaceholderText("手动补一条经验，例如「标题带具体数字的笔记收藏率更高」")
        self.memory_input.returnPressed.connect(self.add_memory)
        row.addWidget(self.memory_input, 1)
        add_button = make_button("添加", icon="plus", theme=self.ctx.theme, primary=True)
        add_button.clicked.connect(self.add_memory)
        row.addWidget(add_button)
        card.add_layout(row)

        self.memory_list = QListWidget()
        self.memory_list.setMinimumHeight(120)
        card.add(self.memory_list, 1)
        self.memory_empty = EmptyState(
            "还没有积累经验",
            hint="发布并同步数据后，点一次「归因回流」，表现好的内容会自动总结成经验",
            icon="sparkles",
            theme=self.ctx.theme,
        )
        card.add(self.memory_empty)
        self.memory_empty.setVisible(False)

        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        goto = make_button("去数据分析页做归因", icon="chart", theme=self.ctx.theme, ghost=True)
        goto.clicked.connect(lambda: self._goto("analytics"))
        bottom.addWidget(goto)
        bottom.addStretch(1)
        # 破坏性操作：靠右、红色文字、点了还要二次确认
        self.clear_memory_button = make_button("清空全部记忆", icon="trash", theme=self.ctx.theme, danger=True)
        self.clear_memory_button.clicked.connect(self.clear_memory)
        bottom.addWidget(self.clear_memory_button)
        card.add_layout(bottom)
        self._memory_card = card
        return card

    def _goto(self, key: str) -> None:
        window = self.window()
        if hasattr(window, "navigate"):
            window.navigate(key)  # type: ignore[attr-defined]

    # ------------------------------------------------------------------
    def on_first_show(self) -> None:
        self.reload()

    def apply_theme(self, theme: str) -> None:
        for card in self._dim_cards.values():
            card.apply_theme(theme)
        card = getattr(self, "_memory_card", None)
        if card is not None:
            card.apply_theme(theme)
        self.memory_empty.apply_theme(theme)

    def reload(self) -> None:
        profile = profile_service.get()
        for key, editor in self._editors.items():
            value = profile.get(key)
            if isinstance(value, (list, tuple)):
                value = "、".join(str(v) for v in value)
            editor.setPlainText(str(value or ""))
        self._refresh_completeness()
        self._refresh_memory()

    def _refresh_completeness(self) -> None:
        percent = profile_service.completeness()
        self.progress.setValue(percent)
        self.completeness_label.setText(f"画像完整度 {percent}%")
        missing = profile_service.missing_dimensions()
        self.missing_label.setText("待补充：" + "、".join(missing) if missing else "六维信息已完整")
        self._refresh_dim_status(missing)
        self.ctx.notify_data_changed("profile")

    def _refresh_dim_status(self, missing: list[str]) -> None:
        """折叠状态下也要能看出哪一维是空的：把状态写进卡片标题。"""
        missing_set = set(missing)
        for key, card in self._dim_cards.items():
            label = next((d.label for d in DIMENSIONS if d.key == key), key)
            is_missing = label in missing_set
            card.set_title(f"{label}　·　{'待补充' if is_missing else '已填写'}")
            # 只把「第一个还没填的」自动展开，避免一屏全展开又变拥挤
            if is_missing and not getattr(self, "_auto_expanded", False):
                card.set_expanded(True)
                self._auto_expanded = True

    def _refresh_memory(self) -> None:
        entries = profile_service.recent_memory(limit=50)
        self.memory_list.clear()
        for entry in entries:
            text = entry.get("text", "") if isinstance(entry, dict) else str(entry)
            at = entry.get("at", "") if isinstance(entry, dict) else ""
            item = QListWidgetItem(f"{at}　{text}" if at else text)
            item.setToolTip(text)
            self.memory_list.addItem(item)
        has = bool(entries)
        self.memory_list.setVisible(has)
        self.memory_empty.setVisible(not has)
        self.clear_memory_button.setEnabled(has)
        if hasattr(self, "_memory_card"):
            self._memory_card.set_title(f"记忆 · 创作经验（{len(entries)} 条）")

    # ------------------------------------------------------------------
    def collect(self) -> dict[str, object]:
        data: dict[str, object] = {
            key: editor.toPlainText().strip() for key, editor in self._editors.items()
        }
        # 「平台」维度在库里是列表：这里统一拆成列表再存，
        # 否则存进去的是裸文本，读回来会因为解析失败而整段丢失。
        raw = str(data.get("platforms") or "")
        data["platforms"] = [
            part.strip()
            for part in raw.replace(",", "、").replace("，", "、").split("、")
            if part.strip()
        ]
        return data

    def save(self) -> None:
        try:
            profile_service.save(**self.collect())
        except Exception as exc:  # noqa: BLE001
            self.banner.show_message(f"保存失败：{exc}", "error")
            return
        self._auto_expanded = True  # 保存后不要再自动展开
        self._refresh_completeness()
        self.banner.show_message("画像已保存，后续创作会自动带上这些设定", "success")
        self.toast("画像已保存", "success")
        self.ctx.notify_data_changed("profile")

    def add_memory(self) -> None:
        text = self.memory_input.text().strip()
        if not text:
            return
        profile_service.append_memory(text)
        self.memory_input.clear()
        self._refresh_memory()
        self.toast("已加入记忆", "success")

    def clear_memory(self) -> None:
        """破坏性操作：先问一次，避免和「添加」挨着被误点。"""
        answer = QMessageBox.question(
            self,
            "清空全部记忆",
            "将删除所有已积累的创作经验，且无法恢复。\n\n"
            "这些经验来自数据归因回流，清空后 AI 生成内容时不再参考它们。\n\n确定清空吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        profile_service.clear_memory()
        self._refresh_memory()
        self.toast("记忆已清空", "warn")

    # ------------------------------------------------------------------
    def generate_draft(self) -> None:
        text = self.assist_input.toPlainText().strip()
        if not text:
            self.toast("请先粘贴一段自我介绍，或点「填入示例」", "warn")
            return
        llm = self.ctx.require_llm()
        if llm is None:
            return
        self.assist_button.setEnabled(False)
        self.assist_button.setText("生成中…")
        self.banner.show_message("正在根据你的描述整理画像草稿…", "info", closable=False)

        def job(worker):
            return profile_service.draft_from_text(text, llm, cancel=worker.cancel_event)

        self._worker = self.run_task(
            job,
            on_result=self._on_draft,
            on_error=self._on_draft_error,
            on_done=self._on_draft_done,
            name="profile-draft",
        )

    def _on_draft(self, draft: dict[str, str]) -> None:
        for key, value in (draft or {}).items():
            editor = self._editors.get(key)
            if editor is not None and value:
                editor.setPlainText(value)
                card = self._dim_cards.get(key)
                if card is not None:
                    card.set_expanded(True)
        self.banner.show_message("画像草稿已生成，请确认后点击「保存画像」", "success")
        self.toast("草稿已生成，请检查后保存", "success")

    def _on_draft_error(self, message: str) -> None:
        self.banner.show_message(f"生成失败：{message}", "error")

    def _on_draft_done(self) -> None:
        self.assist_button.setEnabled(True)
        self.assist_button.setText("生成画像草稿")
        self._worker = None

    def export_markdown(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        from ... import paths

        default = paths.exports_dir() / "profile.md"
        path, _ = QFileDialog.getSaveFileName(self, "导出画像", str(default), "Markdown (*.md)")
        if not path:
            return
        try:
            from pathlib import Path

            Path(path).write_text(profile_service.export_markdown(), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            self.toast(f"导出失败：{exc}", "error")
            return
        self.toast("画像已导出", "success")

    def receive(self, **kwargs: object) -> None:
        if kwargs.get("reload"):
            self.reload()
