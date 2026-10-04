"""内容创作页（企划书 3.2 模块二）。

输入主题/热点/素材 + 目标平台 → 输出正文、多个标题候选、简介与标签。
账号画像影响输出风格；流式输出，可随时中断；可复制、可存草稿、可送发布。
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ...core.db import db
from ...services.creator import (
    CREATION_PLATFORMS,
    CreationRequest,
    creator_service,
    style_for,
)
from ...services.profile import profile_service
from ..components import Card, CardTitle, EmptyState, PageHeader, make_button, labeled_row
from .base import BasePage

log = logging.getLogger(__name__)


class CreatePage(BasePage):
    key = "create"
    title = "内容创作"
    icon = "pen-line"

    def build(self) -> None:
        self._worker = None
        self._last_result = None
        self._seed: dict | None = None
        self._titles_buffer = ""
        self._tags_buffer = ""
        self._summary_buffer = ""
        self._body_buffer = ""

        header = PageHeader(
            "内容创作",
            "输入主题即可产出正文、标题候选、简介与标签；生成过程可随时中断（Ctrl+Enter 快捷生成）",
        )
        header.add_action(self.status_light)
        self.generate_button = make_button("开始生成", icon="sparkles", theme=self.ctx.theme, primary=True)
        self.generate_button.clicked.connect(self.generate)
        header.add_action(self.generate_button)
        self.stop_button = make_button("中断", icon="stop", theme=self.ctx.theme, danger=True)
        self.stop_button.clicked.connect(self.stop)
        self.stop_button.setEnabled(False)
        header.add_action(self.stop_button)
        self.drafts_button = make_button("草稿箱", icon="folder", theme=self.ctx.theme, ghost=True)
        self.drafts_button.clicked.connect(self.open_drafts)
        header.add_action(self.drafts_button)
        self.add(header)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_input_panel())
        splitter.addWidget(self._build_output_panel())
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 5)
        self.add(splitter, 1)

        QShortcut(QKeySequence("Ctrl+Return"), self, activated=self.generate)
        QShortcut(QKeySequence("Ctrl+Enter"), self, activated=self.generate)

    # ------------------------------------------------------------------
    def _build_input_panel(self) -> QWidget:
        card = Card(padding=16, spacing=12)
        card.setMinimumWidth(330)
        card.add(CardTitle("创作输入", icon="pen-line", theme=self.ctx.theme))

        self.topic_edit = QTextEdit()
        self.topic_edit.setPlaceholderText("想写什么？例如「通勤穿搭的 3 个显瘦技巧」")
        self.topic_edit.setFixedHeight(64)
        card.add(QLabel("主题 / 选题"))
        card.add(self.topic_edit)

        self.source_edit = QTextEdit()
        self.source_edit.setPlaceholderText("可选：热点原文、素材片段或参考链接说明")
        self.source_edit.setFixedHeight(72)
        card.add(QLabel("热点 / 素材（可选）"))
        card.add(self.source_edit)

        self.platform_combo = QComboBox()
        for item in creator_service.platforms():
            self.platform_combo.addItem(f"{item['label']}（{item['content_type']}）", item["key"])
        self.platform_combo.currentIndexChanged.connect(self._on_platform_changed)
        card.add(labeled_row("目标平台", self.platform_combo))

        self.content_type_edit = QLineEdit()
        card.add(labeled_row("内容类型", self.content_type_edit))

        self.goal_edit = QLineEdit("互动涨粉")
        card.add(labeled_row("内容目标", self.goal_edit))

        self.tone_edit = QLineEdit()
        self.tone_edit.setPlaceholderText("可选，例如：轻松、犀利、专业")
        card.add(labeled_row("调性", self.tone_edit))

        self.extra_edit = QTextEdit()
        self.extra_edit.setPlaceholderText("补充要求，例如：必须提到三个具体品牌；不要出现价格")
        self.extra_edit.setFixedHeight(62)
        card.add(QLabel("补充要求（可选）"))
        card.add(self.extra_edit)

        self.style_hint = QLabel()
        self.style_hint.setObjectName("Faint")
        self.style_hint.setWordWrap(True)
        card.add(self.style_hint)

        self.profile_hint = QLabel()
        self.profile_hint.setObjectName("Faint")
        self.profile_hint.setWordWrap(True)
        card.add(self.profile_hint)

        card.add(CardTitle("质量自检", icon="check", hint="生成后可让 AI 按钩子/平台匹配/信息价值/真人感四项打分", theme=self.ctx.theme))
        self.quality_button = make_button("AI 质量自检", icon="check", theme=self.ctx.theme)
        self.quality_button.setEnabled(False)
        self.quality_button.clicked.connect(self.quality_check)
        row = QHBoxLayout()
        row.addWidget(self.quality_button)
        row.addStretch(1)
        card.add_layout(row)
        self.quality_result = QLabel()
        self.quality_result.setObjectName("Faint")
        self.quality_result.setWordWrap(True)
        card.add(self.quality_result)

        card.body().addStretch(1)
        self._on_platform_changed()
        return card

    def _build_output_panel(self) -> QWidget:
        card = Card(padding=16, spacing=10)
        card.add(CardTitle("创作结果", icon="sparkles", hint="正文流式生成，可随时中断后编辑", theme=self.ctx.theme))

        self.result_splitter = QSplitter(Qt.Orientation.Vertical)
        self.result_splitter.setChildrenCollapsible(False)

        # 正文
        body_box = QWidget()
        body_layout = QVBoxLayout(body_box)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(4)
        body_header = QHBoxLayout()
        body_header.addWidget(QLabel("正文"))
        body_header.addStretch(1)
        self.body_count = QLabel("0 字")
        self.body_count.setObjectName("Faint")
        body_header.addWidget(self.body_count)
        body_layout.addLayout(body_header)
        self.body_edit = QTextEdit()
        self.body_edit.setPlaceholderText("生成结果会在这里实时出现，你可以直接在此编辑")
        self.body_edit.textChanged.connect(self._update_counts)
        body_layout.addWidget(self.body_edit, 1)
        self.result_splitter.addWidget(body_box)

        # 标题 / 简介 / 标签
        meta_box = QWidget()
        meta_layout = QVBoxLayout(meta_box)
        meta_layout.setContentsMargins(0, 0, 0, 0)
        meta_layout.setSpacing(8)

        title_row = QHBoxLayout()
        title_row.addWidget(QLabel("标题候选（双击选用）"))
        title_row.addStretch(1)
        self.regen_button = make_button("重新生成标题", icon="refresh", theme=self.ctx.theme, ghost=True)
        self.regen_button.clicked.connect(self.regenerate_titles)
        self.regen_button.setEnabled(False)
        title_row.addWidget(self.regen_button)
        meta_layout.addLayout(title_row)

        self.titles_list = QListWidget()
        self.titles_list.setMaximumHeight(92)
        self.titles_list.itemDoubleClicked.connect(self._use_title)
        meta_layout.addWidget(self.titles_list)

        self.selected_title_label = QLabel("当前标题：未选择")
        self.selected_title_label.setObjectName("Faint")
        meta_layout.addWidget(self.selected_title_label)

        summary_row = QHBoxLayout()
        summary_row.addWidget(QLabel("简介"))
        summary_row.addStretch(1)
        meta_layout.addLayout(summary_row)
        self.summary_edit = QTextEdit()
        self.summary_edit.setPlaceholderText("用于发布时的摘要/简介字段")
        self.summary_edit.setFixedHeight(58)
        meta_layout.addWidget(self.summary_edit)

        tags_row = QHBoxLayout()
        tags_row.addWidget(QLabel("标签（空格分隔）"))
        tags_row.addStretch(1)
        self.tags_count = QLabel("0 个")
        self.tags_count.setObjectName("Faint")
        tags_row.addWidget(self.tags_count)
        meta_layout.addLayout(tags_row)
        self.tags_edit = QLineEdit()
        self.tags_edit.setPlaceholderText("#通勤穿搭 #平价好物")
        self.tags_edit.textChanged.connect(self._update_counts)
        meta_layout.addWidget(self.tags_edit)

        self.result_splitter.addWidget(meta_box)
        self.result_splitter.setStretchFactor(0, 4)
        self.result_splitter.setStretchFactor(1, 3)
        card.add(self.result_splitter, 1)

        # 操作条
        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        self.copy_body_button = make_button("复制正文", icon="copy", theme=self.ctx.theme)
        self.copy_body_button.clicked.connect(lambda: self._copy(self.body_edit.toPlainText(), "正文已复制"))
        action_row.addWidget(self.copy_body_button)
        self.copy_all_button = make_button("复制全文", icon="copy", theme=self.ctx.theme)
        self.copy_all_button.clicked.connect(lambda: self._copy(self._full_text(), "全文已复制"))
        action_row.addWidget(self.copy_all_button)
        action_row.addStretch(1)
        self.save_button = make_button("存为草稿", icon="save", theme=self.ctx.theme)
        self.save_button.clicked.connect(lambda: self.save_draft(to_publish=False))
        action_row.addWidget(self.save_button)
        self.publish_button = make_button("送入发布中心", icon="send", theme=self.ctx.theme, primary=True)
        self.publish_button.clicked.connect(lambda: self.save_draft(to_publish=True))
        action_row.addWidget(self.publish_button)
        card.add_layout(action_row)

        self.result_hint = QLabel("尚未生成内容")
        self.result_hint.setObjectName("Faint")
        card.add(self.result_hint)
        return card

    # ------------------------------------------------------------------
    def _on_platform_changed(self) -> None:
        key = self.platform_combo.currentData()
        style = style_for(key)
        self.content_type_edit.setText(style.content_type)
        self.style_hint.setText(
            f"平台规范：标题 ≤{style.title_max} 字；正文 {style.body_min}-{style.body_max} 字；"
            f"标签 ≤{style.tags_max} 个；建议发布时段 {style.best_hours}"
        )

    def apply_theme(self, theme: str) -> None:
        pass

    def on_first_show(self) -> None:
        self._refresh_profile_hint()

    def _refresh_profile_hint(self) -> None:
        if profile_service.is_configured():
            percent = profile_service.completeness()
            self.profile_hint.setText(f"将使用账号画像（完整度 {percent}%）影响风格与选题")
        else:
            self.profile_hint.setText("尚未填写账号画像，将使用通用风格；建议到「账号画像」补充")

    def receive(self, **kwargs) -> None:
        seed = kwargs.get("seed")
        if isinstance(seed, dict):
            self._seed = seed
            self.topic_edit.setPlainText(str(seed.get("topic", "")))
            self.source_edit.setPlainText(
                "\n".join(
                    part
                    for part in (
                        f"来源：{seed.get('source_platform', '')} 热榜第 {seed.get('rank', '')} 名" if seed.get("source_platform") else "",
                        f"热度：{seed.get('heat', '')}" if seed.get("heat") else "",
                        str(seed.get("topic", "")),
                    )
                    if part
                )
            )
            target = seed.get("target_platform")
            if target:
                index = self.platform_combo.findData(target)
                if index >= 0:
                    self.platform_combo.setCurrentIndex(index)
            self.banner.show_message(f"已载入热点：{seed.get('topic', '')[:40]}", "info")
            self._refresh_profile_hint()

    # ------------------------------------------------------------------
    # 生成
    # ------------------------------------------------------------------
    def _request(self) -> CreationRequest | None:
        topic = self.topic_edit.toPlainText().strip()
        if not topic:
            self.toast("请先填写主题", "warn")
            return None
        return CreationRequest(
            topic=topic,
            platform=self.platform_combo.currentData() or "xiaohongshu",
            content_type=self.content_type_edit.text().strip(),
            goal=self.goal_edit.text().strip() or "互动涨粉",
            tone=self.tone_edit.text().strip(),
            source_text=self.source_edit.toPlainText().strip(),
            extra=self.extra_edit.toPlainText().strip(),
        )

    def generate(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self.toast("正在生成中，可点击「中断」停止", "info")
            return
        req = self._request()
        if req is None:
            return
        llm = self.ctx.require_llm()
        if llm is None:
            return

        self._reset_output()
        self._set_generating(True)
        self.banner.show_message(f"正在为「{style_for(req.platform).label}」生成内容…", "info", closable=False)

        def job(worker):
            return creator_service.create_stream(
                req,
                llm,
                cancel=worker.cancel_event,
                on_event=lambda section, text: worker.emit_stream(section, text),
            )

        self._worker = self.run_task(
            job,
            on_stream=self._on_stream,
            on_result=self._on_result,
            on_error=self._on_error,
            on_done=self._on_done,
            name="create",
        )

    def stop(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self.banner.show_message("已中断生成，已生成的部分会保留", "warn")
            self.toast("已中断", "warn")

    def _reset_output(self) -> None:
        self._titles_buffer = ""
        self._tags_buffer = ""
        self._summary_buffer = ""
        self._body_buffer = ""
        self._last_result = None
        self.body_edit.clear()
        self.summary_edit.clear()
        self.tags_edit.clear()
        self.titles_list.clear()
        self.selected_title_label.setText("当前标题：未选择")
        self.quality_result.clear()
        self.quality_button.setEnabled(False)
        self.result_hint.setText("生成中…")
        self._update_counts()

    def _on_stream(self, section: str, text: str) -> None:
        if section == "正文":
            self._body_buffer += text
            self._append_text(self.body_edit, text)
        elif section == "标题":
            self._titles_buffer += text
            self._render_titles_live()
        elif section == "简介":
            self._summary_buffer += text
            self._append_text(self.summary_edit, text)
        elif section == "标签":
            self._tags_buffer += text
            self._render_tags_live()
        self._update_counts()

    @staticmethod
    def _append_text(edit: QTextEdit, text: str) -> None:
        cursor = edit.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(text)
        edit.setTextCursor(cursor)
        edit.ensureCursorVisible()

    def _render_titles_live(self) -> None:
        from ...services.creator import parse_titles

        titles = parse_titles(self._titles_buffer)
        if len(titles) == len([t for t in (self.titles_list.item(i).text() for i in range(self.titles_list.count()))]):
            return
        self.titles_list.clear()
        for title in titles:
            item = QListWidgetItem(title)
            item.setToolTip(title)
            self.titles_list.addItem(item)
        if self.titles_list.count() and not self.titles_list.currentItem():
            self.titles_list.setCurrentRow(0)

    def _render_tags_live(self) -> None:
        from ...services.creator import parse_tags

        tags = parse_tags(self._tags_buffer)
        text = " ".join(f"#{t}" for t in tags)
        if self.tags_edit.text() != text:
            self.tags_edit.setText(text)

    def _on_result(self, result) -> None:
        self._last_result = result
        if result.body and self.body_edit.toPlainText().strip() != result.body.strip():
            self._body_buffer = result.body
            self.body_edit.setPlainText(result.body)
        from ...services.creator import parse_tags, parse_titles

        titles = result.titles or parse_titles(self._titles_buffer)
        self.titles_list.clear()
        for title in titles:
            item = QListWidgetItem(title)
            item.setToolTip(title)
            self.titles_list.addItem(item)
        if self.titles_list.count():
            self.titles_list.setCurrentRow(0)
            self._use_title(self.titles_list.item(0))

        summary = result.summary or self._summary_buffer.strip()
        if summary and self.summary_edit.toPlainText().strip() != summary:
            self.summary_edit.setPlainText(summary)
        tags = result.tags or parse_tags(self._tags_buffer)
        self.tags_edit.setText(" ".join(f"#{t}" for t in tags))

        self.result_hint.setText(result.usage_hint or "生成完成")
        self.quality_button.setEnabled(True)
        self.regen_button.setEnabled(True)
        self.banner.show_message(
            "生成完成，可直接编辑后「存为草稿」或「送入发布中心」", "success", summary="生成完成"
        )
        self.ctx.notify_data_changed("create")
        self._update_counts()

    def _on_error(self, message: str) -> None:
        self.banner.show_message(f"生成失败：{message}", "error")
        self.result_hint.setText("生成失败")
        self.toast("生成失败", "error")

    def _on_done(self) -> None:
        self._set_generating(False)
        self._worker = None
        self._update_counts()

    def _set_generating(self, busy: bool) -> None:
        self.generate_button.setEnabled(not busy)
        self.generate_button.setText("生成中…" if busy else "开始生成")
        self.stop_button.setEnabled(busy)
        for widget in (self.topic_edit, self.source_edit, self.platform_combo, self.extra_edit):
            widget.setEnabled(not busy)

    # ------------------------------------------------------------------
    # 输出操作
    # ------------------------------------------------------------------
    def _use_title(self, item: QListWidgetItem | None) -> None:
        if item is None:
            return
        self.titles_list.setCurrentItem(item)
        self.selected_title_label.setText(f"当前标题：{item.text()}")

    def _current_title(self) -> str:
        item = self.titles_list.currentItem()
        if item is not None:
            return item.text()
        return ""

    def _current_tags(self) -> list[str]:
        from ...services.creator import parse_tags

        return parse_tags(self.tags_edit.text())

    def _full_text(self) -> str:
        title = self._current_title()
        body = self.body_edit.toPlainText().strip()
        tags = " ".join(f"#{t}" for t in self._current_tags())
        parts = [p for p in (title, body, tags) if p]
        return "\n\n".join(parts)

    def _copy(self, text: str, message: str) -> None:
        if not text.strip():
            self.toast("没有可复制的内容", "warn")
            return
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText(text)
        self.toast(message, "success")

    def _update_counts(self) -> None:
        body_len = len(self.body_edit.toPlainText())
        style = style_for(self.platform_combo.currentData() or "xiaohongshu")
        self.body_count.setText(f"{body_len} 字 / 建议 {style.body_min}-{style.body_max}")
        note = "" if style.body_min <= body_len <= style.body_max else "（超出建议区间）"
        self.body_count.setToolTip(note)
        self.tags_count.setText(f"{len(self._current_tags())} 个 / 上限 {style.tags_max}")

    # ------------------------------------------------------------------
    # 其他动作
    # ------------------------------------------------------------------
    def regenerate_titles(self) -> None:
        body = self.body_edit.toPlainText().strip()
        if not body:
            self.toast("请先生成正文", "warn")
            return
        req = self._request()
        if req is None:
            return
        llm = self.ctx.require_llm()
        if llm is None:
            return
        self.regen_button.setEnabled(False)
        self.regen_button.setText("生成中…")
        self.banner.show_message("正在重新生成标题…", "info", closable=False)

        self._worker = self.run_task(
            lambda worker: creator_service.regenerate_titles(req, body, llm, cancel=worker.cancel_event),
            on_result=self._on_titles,
            on_error=lambda msg: self.banner.show_message(f"标题生成失败：{msg}", "error"),
            on_done=self._on_regen_done,
            name="titles",
        )

    def _on_titles(self, titles: list[str]) -> None:
        self.titles_list.clear()
        for title in titles or []:
            item = QListWidgetItem(title)
            item.setToolTip(title)
            self.titles_list.addItem(item)
        if self.titles_list.count():
            self.titles_list.setCurrentRow(0)
            self._use_title(self.titles_list.item(0))
        self.banner.show_message("标题候选已更新", "success")

    def _on_regen_done(self) -> None:
        self.regen_button.setEnabled(True)
        self.regen_button.setText("重新生成标题")

    def quality_check(self) -> None:
        if self._last_result is None:
            self.toast("请先生成内容", "warn")
            return
        llm = self.ctx.require_llm()
        if llm is None:
            return
        from ...services.creator import CreationResult

        result = CreationResult(
            platform=self._last_result.platform,
            body=self.body_edit.toPlainText().strip(),
            titles=[self._current_title()] if self._current_title() else [],
            summary=self.summary_edit.toPlainText().strip(),
            tags=self._current_tags(),
        )
        self.quality_button.setEnabled(False)
        self.quality_button.setText("自检中…")
        self.banner.show_message("正在做质量自检…", "info", closable=False)
        self._worker = self.run_task(
            lambda worker: creator_service.quality_check(result, llm, cancel=worker.cancel_event),
            on_result=self._on_quality,
            on_error=lambda msg: self.banner.show_message(f"质量自检失败：{msg}", "error"),
            on_done=self._on_quality_done,
            name="quality",
        )

    def _on_quality(self, data: dict) -> None:
        total = data.get("total") or sum(
            int(data.get(k) or 0) for k in ("hook", "platform_fit", "value", "human")
        )
        issues = data.get("issues") or []
        suggestions = data.get("suggestions") or []
        lines = [
            f"总分 {total}/40　钩子 {data.get('hook', '-')}　平台匹配 {data.get('platform_fit', '-')}"
            f"　信息价值 {data.get('value', '-')}　真人感 {data.get('human', '-')}"
        ]
        if issues:
            lines.append("问题：" + "；".join(str(i) for i in issues[:4]))
        if suggestions:
            lines.append("建议：" + "；".join(str(s) for s in suggestions[:4]))
        self.quality_result.setText("\n".join(lines))
        self.banner.show_message("质量自检完成", "success")

    def _on_quality_done(self) -> None:
        self.quality_button.setEnabled(True)
        self.quality_button.setText("AI 质量自检")

    # ------------------------------------------------------------------
    def save_draft(self, *, to_publish: bool) -> None:
        body = self.body_edit.toPlainText().strip()
        if not body:
            self.toast("正文为空，无法保存", "warn")
            return
        titles = [self.titles_list.item(i).text() for i in range(self.titles_list.count())]
        title = self._current_title() or (titles[0] if titles else "")
        seed = self._seed or {}
        try:
            content_id = db.create_content(
                topic=self.topic_edit.toPlainText().strip(),
                platform=self.platform_combo.currentData() or "",
                title=title,
                titles=titles,
                body=body,
                summary=self.summary_edit.toPlainText().strip(),
                tags=self._current_tags(),
                status="ready",
                source="hot" if seed else "manual",
                source_ref=str(seed.get("source_ref", "")),
            )
        except Exception as exc:  # noqa: BLE001
            self.banner.show_message(f"保存失败：{exc}", "error")
            return
        self.ctx.notify_data_changed("drafts")
        if to_publish:
            self.toast("已保存草稿，正在打开发布中心", "success")
            window = self.window()
            if hasattr(window, "open_page_with"):
                window.open_page_with("publish", content_id=content_id)  # type: ignore[attr-defined]
        else:
            self.toast(f"已存为草稿（#{content_id}）", "success")
            self.banner.show_message(f"草稿已保存（编号 {content_id}），可在「草稿箱」中再次载入", "success")

    # ------------------------------------------------------------------
    def open_drafts(self) -> None:
        dialog = DraftDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.selected:
            self._load_content(dialog.selected)

    def _load_content(self, content: dict) -> None:
        self.topic_edit.setPlainText(content.get("topic", ""))
        index = self.platform_combo.findData(content.get("platform", ""))
        if index >= 0:
            self.platform_combo.setCurrentIndex(index)
        self.body_edit.setPlainText(content.get("body", ""))
        self.summary_edit.setPlainText(content.get("summary", ""))
        self.tags_edit.setText(" ".join(f"#{t}" for t in (content.get("tags") or [])))
        self.titles_list.clear()
        for title in content.get("titles") or []:
            self.titles_list.addItem(QListWidgetItem(title))
        if content.get("title"):
            if not content.get("titles"):
                self.titles_list.addItem(QListWidgetItem(content["title"]))
            for row in range(self.titles_list.count()):
                if self.titles_list.item(row).text() == content["title"]:
                    self.titles_list.setCurrentRow(row)
                    self._use_title(self.titles_list.item(row))
                    break
        self.banner.show_message(f"已载入草稿 #{content.get('id')}", "info")
        self._update_counts()


class DraftDialog(QDialog):
    """草稿箱：列出最近的草稿并可载入或删除。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("草稿箱")
        self.resize(680, 460)
        self.selected: dict | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        layout.addWidget(QLabel("双击草稿即可载入创作页"))

        self.list = QListWidget()
        layout.addWidget(self.list, 1)

        row = QHBoxLayout()
        row.addStretch(1)
        delete_button = QPushButton("删除选中")
        delete_button.setObjectName("Danger")
        delete_button.clicked.connect(self._delete)
        row.addWidget(delete_button)
        load_button = QPushButton("载入")
        load_button.setObjectName("Primary")
        load_button.clicked.connect(self._accept)
        row.addWidget(load_button)
        layout.addLayout(row)

        self.list.itemDoubleClicked.connect(lambda _: self._accept())
        self._contents: list[dict] = []
        self._reload()

    def _reload(self) -> None:
        self.list.clear()
        self._contents = db.list_contents(limit=200)
        if not self._contents:
            self.list.addItem(QListWidgetItem("暂无草稿"))
            return
        for content in self._contents:
            title = content.get("title") or content.get("topic") or "（无标题）"
            item = QListWidgetItem(
                f"#{content['id']}　[{content.get('platform', '')}]　{title[:40]}　"
                f"{content.get('updated_at', '')}"
            )
            item.setToolTip(content.get("body", "")[:400])
            self.list.addItem(item)

    def _current(self) -> dict | None:
        row = self.list.currentRow()
        if row < 0 or row >= len(self._contents):
            return None
        return self._contents[row]

    def _accept(self) -> None:
        content = self._current()
        if content is None:
            return
        self.selected = content
        self.accept()

    def _delete(self) -> None:
        content = self._current()
        if content is None:
            return
        db.delete_content(int(content["id"]))
        self._reload()
