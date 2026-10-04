"""账号画像页（企划书 3.1「跨模块共享的账号画像」）。

六维配置：定位 / 风格 / 受众 / 平台 / 偏好 / 记忆。
画像影响内容创作的风格与选题，并接收数据分析回流的经验。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ...services.profile import DIMENSIONS, profile_service
from ..components import Card, CardTitle, EmptyState, PageHeader, StatusBanner, make_button, hline
from .base import BasePage


class ProfilePage(BasePage):
    key = "profile"
    title = "账号画像"
    icon = "user"

    def build(self) -> None:
        self._editors: dict[str, QTextEdit] = {}
        self._worker = None

        header = PageHeader(
            "账号画像",
            "六维画像决定创作的语气、选题与平台适配；数据分析的结论会回流到「记忆」",
        )
        self.save_button = make_button("保存画像", icon="save", theme=self.ctx.theme, primary=True)
        self.save_button.clicked.connect(self.save)
        header.add_action(self.save_button)
        self.export_button = make_button("导出 Markdown", icon="external", theme=self.ctx.theme, ghost=True)
        self.export_button.clicked.connect(self.export_markdown)
        header.add_action(self.export_button)
        self.add(header)

        self.banner = StatusBanner()
        self.add(self.banner)

        # ---- 完整度 ----
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

        # ---- 一键生成 ----
        assist_card = Card(padding=14, spacing=8)
        self.add(assist_card)
        assist_title = CardTitle(
            "用 AI 生成画像草稿",
            icon="sparkles",
            hint="粘贴一段自我介绍或账号说明（例如「我做通勤穿搭，粉丝是 25-35 岁女生…」），AI 会整理成六维画像",
            theme=self.ctx.theme,
        )
        assist_card.add(assist_title)
        self.assist_input = QTextEdit()
        self.assist_input.setPlaceholderText("例：我做小红书平价通勤穿搭，粉丝主要是一二线城市 25-35 岁女生，预算 300 以内，语气像闺蜜聊天…")
        self.assist_input.setFixedHeight(74)
        assist_card.add(self.assist_input)
        assist_row = QHBoxLayout()
        assist_row.addStretch(1)
        self.assist_button = make_button("生成画像草稿", icon="sparkles", theme=self.ctx.theme)
        self.assist_button.clicked.connect(self.generate_draft)
        assist_row.addWidget(self.assist_button)
        assist_card.add_layout(assist_row)

        # ---- 六维表单 ----
        form_card = Card(padding=16, spacing=12)
        self.add(form_card, 1)
        form_card.add(CardTitle("六维画像", icon="user", theme=self.ctx.theme))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(0, 0, 8, 0)
        inner_layout.setSpacing(14)

        for dim in DIMENSIONS:
            if dim.key == "memory":
                continue
            block = QWidget()
            block_layout = QVBoxLayout(block)
            block_layout.setContentsMargins(0, 0, 0, 0)
            block_layout.setSpacing(4)
            label = QLabel(f"{dim.label}　·　{dim.hint}")
            label.setObjectName("SectionLabel")
            label.setWordWrap(True)
            block_layout.addWidget(label)
            editor = QTextEdit()
            editor.setPlaceholderText(dim.placeholder)
            editor.setFixedHeight(66 if dim.key != "style" else 78)
            self._editors[dim.key] = editor
            block_layout.addWidget(editor)
            inner_layout.addWidget(block)

        # 平台维度用一行输入更合适
        self.platforms_edit = QLineEdit()
        self.platforms_edit.setPlaceholderText("例：小红书（主）、抖音、知乎")
        inner_layout.addWidget(QLabel("补充：常用平台（逗号分隔）"))
        inner_layout.addWidget(self.platforms_edit)

        inner_layout.addStretch(1)
        scroll.setWidget(inner)
        form_card.add(scroll, 1)

        # ---- 记忆 ----
        memory_card = Card(padding=16, spacing=8)
        self.add(memory_card, 1)
        memory_title = CardTitle(
            "记忆 · 数据分析回流",
            icon="chart",
            hint="由「数据分析 → 归因回流」自动写入，也可手动添加；创作时优先级最高",
            theme=self.ctx.theme,
        )
        memory_card.add(memory_title)
        memory_input_row = QHBoxLayout()
        memory_input_row.setSpacing(8)
        self.memory_input = QLineEdit()
        self.memory_input.setPlaceholderText("手动补充一条经验，例如「标题带具体数字的笔记收藏率更高」")
        self.memory_input.returnPressed.connect(self.add_memory)
        memory_input_row.addWidget(self.memory_input, 1)
        add_button = make_button("添加", icon="check", theme=self.ctx.theme)
        add_button.clicked.connect(self.add_memory)
        memory_input_row.addWidget(add_button)
        clear_button = make_button("清空记忆", icon="trash", theme=self.ctx.theme, danger=True)
        clear_button.clicked.connect(self.clear_memory)
        memory_input_row.addWidget(clear_button)
        memory_card.add_layout(memory_input_row)

        self.memory_list = QListWidget()
        self.memory_list.setMinimumHeight(120)
        memory_card.add(self.memory_list, 1)
        self.memory_empty = EmptyState("暂无归因经验", hint="去「数据分析」页点击「归因回流」，让表现数据沉淀为创作经验", theme=self.ctx.theme)
        memory_card.add(self.memory_empty)
        self.memory_empty.setVisible(False)

    # ------------------------------------------------------------------
    def on_first_show(self) -> None:
        self.reload()

    def reload(self) -> None:
        profile = profile_service.get()
        for key, editor in self._editors.items():
            value = profile.get(key)
            if isinstance(value, (list, tuple)):
                value = "、".join(str(v) for v in value)
            editor.setPlainText(str(value or ""))
        platforms = profile.get("platforms") or []
        self.platforms_edit.setText("、".join(str(p) for p in platforms))
        self._refresh_completeness()
        self._refresh_memory()

    def _refresh_completeness(self) -> None:
        percent = profile_service.completeness()
        self.progress.setValue(percent)
        self.completeness_label.setText(f"画像完整度 {percent}%")
        missing = profile_service.missing_dimensions()
        self.missing_label.setText("待补充：" + "、".join(missing) if missing else "六维信息已完整")
        self.ctx.notify_data_changed("profile")

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

    # ------------------------------------------------------------------
    def collect(self) -> dict[str, object]:
        data: dict[str, object] = {key: editor.toPlainText().strip() for key, editor in self._editors.items()}
        raw = self.platforms_edit.text().strip()
        if raw:
            data["platforms"] = [p.strip() for p in raw.replace(",", "、").replace("，", "、").split("、") if p.strip()]
        return data

    def save(self) -> None:
        try:
            profile_service.save(**self.collect())
        except Exception as exc:  # noqa: BLE001
            self.banner.show_message(f"保存失败：{exc}", "error")
            return
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
        profile_service.clear_memory()
        self._refresh_memory()
        self.toast("记忆已清空", "warn")

    # ------------------------------------------------------------------
    def generate_draft(self) -> None:
        text = self.assist_input.toPlainText().strip()
        if not text:
            self.toast("请先粘贴一段自我介绍", "warn")
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
