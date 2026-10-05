"""内容创作页（企划书 3.2 模块二）。

输入主题/热点/素材 + 目标平台 → 输出正文、多个标题候选、简介与标签。
账号画像影响输出风格；流式输出，可随时中断；可复制、可存草稿、可送发布。

界面要点（v1.0.2 打磨）：

- 左侧只留高频项，低频项收进「高级设置」折叠区；「补充要求」给足高度
- 内容类型默认跟随平台（只读），需要覆盖时勾选「自定义」才解锁编辑
- 标题候选单击选用，选中项右侧出现勾号；「当前标题」可直接编辑
- 正文与标签的字数超限即刻变红；标签改成一颗颗可复制、可删除的胶囊
- 生成过程给出步骤反馈（正在撰写正文 → 正在构思标题 …），不再是空白等待
- 底部操作栏吸底，正文再长也能随时「送入发布中心」
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
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
    CreationRequest,
    creator_service,
    parse_tags,
    parse_titles,
    style_for,
)
from ...services.profile import profile_service
from ..components import (
    Card,
    CardTitle,
    CollapsibleSection,
    EditableTagChip,
    FlowRow,
    PageHeader,
    StepIndicator,
    TitleCandidateDelegate,
    field_label,
    make_button,
)
from ..theme import palette
from .base import BasePage

log = logging.getLogger(__name__)


class CreatePage(BasePage):
    key = "create"
    title = "内容创作"
    icon = "pen-line"

    #: 流式分段 → 步骤提示文案（让等待中的用户知道模型在做什么）
    STEP_TEXT: dict[str, str] = {
        "正文": "正在撰写正文…",
        "标题": "正在构思标题…",
        "简介": "正在打磨简介…",
        "标签": "正在生成标签…",
    }

    def build(self) -> None:
        self._worker = None
        self._last_result = None
        self._seed: dict | None = None
        self._titles_buffer = ""
        self._tags_buffer = ""
        self._summary_buffer = ""
        self._body_buffer = ""
        self._tags: list[str] = []
        self._syncing_title = False

        header = PageHeader(
            "内容创作",
            "输入主题即可产出正文、标题候选、简介与标签；生成过程可随时中断（Ctrl+Enter 快捷生成）",
        )
        header.add_action(self.status_light)
        self.generate_button = make_button("开始生成", icon="sparkles", theme=self.ctx.theme, primary=True)
        self.generate_button.clicked.connect(self.generate)
        header.add_action(self.generate_button)
        # 中断按钮弱化处理：平时安静地待在主按钮旁边，悬停才亮红（停止=危险操作）
        self.stop_button = make_button("中断", icon="stop", theme=self.ctx.theme, stop=True)
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
        # 输出区是「预览/编辑」主场，给它更多宽度与高度
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([400, 720])
        self.add(splitter, 1)

        # 两侧控件都就位后再做一次平台联动（会同步右侧的字数提示）
        self._on_platform_changed()

        QShortcut(QKeySequence("Ctrl+Return"), self, activated=self.generate)
        QShortcut(QKeySequence("Ctrl+Enter"), self, activated=self.generate)

    # ------------------------------------------------------------------
    # 左侧：输入面板
    # ------------------------------------------------------------------
    def _build_input_panel(self) -> QWidget:
        card = Card(padding=16, spacing=10)
        card.setMinimumWidth(320)
        card.add(CardTitle("创作输入", icon="pen-line", theme=self.ctx.theme))

        self.topic_edit = QTextEdit()
        self.topic_edit.setPlaceholderText("想写什么？例如「通勤穿搭的 3 个显瘦技巧」")
        self.topic_edit.setFixedHeight(62)
        card.add(field_label("主题 / 选题", "必填"))
        card.add(self.topic_edit)

        self.source_edit = QTextEdit()
        self.source_edit.setPlaceholderText("可选：热点原文、素材片段或参考链接说明")
        self.source_edit.setFixedHeight(68)
        card.add(field_label("热点 / 素材", "可选"))
        card.add(self.source_edit)

        self.platform_combo = QComboBox()
        for item in creator_service.platforms():
            self.platform_combo.addItem(f"{item['label']}（{item['content_type']}）", item["key"])
        self.platform_combo.currentIndexChanged.connect(self._on_platform_changed)
        card.add(field_label("目标平台"))
        card.add(self.platform_combo)

        self.style_hint = QLabel()
        self.style_hint.setObjectName("Faint")
        self.style_hint.setWordWrap(True)
        card.add(self.style_hint)

        # 补充要求是权重最高的 prompt 输入区，给足高度，避免长文本频繁滚动
        self.extra_edit = QTextEdit()
        self.extra_edit.setPlaceholderText(
            "例如：必须提到三个具体品牌；面向 25-35 岁通勤女性；不要出现价格"
        )
        self.extra_edit.setMinimumHeight(112)
        card.add(field_label("补充要求", "越具体越贴近预期"))
        card.add(self.extra_edit)

        card.add(self._build_advanced_section())

        self.profile_hint = QLabel()
        self.profile_hint.setObjectName("Faint")
        self.profile_hint.setWordWrap(True)
        card.add(self.profile_hint)

        card.body().addStretch(1)
        return card

    def _build_advanced_section(self) -> QWidget:
        """低频项收进折叠区：默认收起，左侧面板才不至于又长又挤。"""
        section = CollapsibleSection("高级设置", icon="sliders", theme=self.ctx.theme, expanded=False)

        # 内容类型默认由平台决定，这里只读展示；要覆盖时勾选「自定义」
        self.content_type_edit = QLineEdit()
        self.content_type_edit.setReadOnly(True)
        self.content_type_custom = QCheckBox("自定义")
        self.content_type_custom.setToolTip("勾选后可覆盖平台默认的内容类型")
        self.content_type_custom.toggled.connect(self._on_content_type_custom)
        type_row = QWidget()
        type_layout = QHBoxLayout(type_row)
        type_layout.setContentsMargins(0, 0, 0, 0)
        type_layout.setSpacing(8)
        type_layout.addWidget(self.content_type_edit, 1)
        type_layout.addWidget(self.content_type_custom)
        self.content_type_hint = QLabel()
        self.content_type_hint.setObjectName("FieldHint")
        self.content_type_hint.setWordWrap(True)
        section.add(field_label("内容类型"))
        section.add(type_row)
        section.add(self.content_type_hint)

        self.goal_edit = QLineEdit("互动涨粉")
        section.add(field_label("内容目标"))
        section.add(self.goal_edit)

        self.tone_edit = QLineEdit()
        self.tone_edit.setPlaceholderText("可选，例如：轻松、犀利、专业")
        section.add(field_label("调性"))
        section.add(self.tone_edit)

        self.advanced_section = section
        return section

    # ------------------------------------------------------------------
    # 右侧：输出面板
    # ------------------------------------------------------------------
    def _build_output_panel(self) -> QWidget:
        # padding=0：内容区与底部吸底操作栏各自管理内边距
        card = Card(padding=0, spacing=0)
        root = card.body()

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(16, 14, 16, 10)
        layout.setSpacing(10)
        layout.addWidget(
            CardTitle(
                "创作结果",
                icon="sparkles",
                hint="正文流式生成，可随时中断后直接编辑",
                theme=self.ctx.theme,
            )
        )

        # 生成步骤提示：等待期间给出持续的活动反馈，而不是一片空白
        self.step_bar = StepIndicator(theme=self.ctx.theme)
        layout.addWidget(self.step_bar)

        self.result_splitter = QSplitter(Qt.Orientation.Vertical)
        self.result_splitter.setChildrenCollapsible(False)
        self.result_splitter.addWidget(self._build_body_box())
        self.result_splitter.addWidget(self._build_meta_box())
        # 正文占更多高度，符合「预览/编辑」的阅读习惯
        self.result_splitter.setStretchFactor(0, 5)
        self.result_splitter.setStretchFactor(1, 3)
        self.result_splitter.setSizes([420, 260])
        layout.addWidget(self.result_splitter, 1)

        self.quality_result = QLabel()
        self.quality_result.setObjectName("Faint")
        self.quality_result.setWordWrap(True)
        layout.addWidget(self.quality_result)

        self.result_hint = QLabel("尚未生成内容")
        self.result_hint.setObjectName("Faint")
        self.result_hint.setWordWrap(True)
        layout.addWidget(self.result_hint)

        root.addWidget(content, 1)
        root.addWidget(self._build_action_bar())
        return card

    def _build_body_box(self) -> QWidget:
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        head = QWidget()
        head_layout = QHBoxLayout(head)
        head_layout.setContentsMargins(0, 0, 0, 0)
        head_layout.setSpacing(8)
        head_layout.addWidget(field_label("正文", "可直接编辑"))
        head_layout.addStretch(1)
        self.body_count = QLabel("0 字")
        self.body_count.setObjectName("FieldHint")
        head_layout.addWidget(self.body_count)
        layout.addWidget(head)

        self.body_edit = QTextEdit()
        self.body_edit.setPlaceholderText("生成结果会在这里实时出现，你可以直接在此编辑")
        self.body_edit.textChanged.connect(self._update_counts)
        layout.addWidget(self.body_edit, 1)
        return box

    def _build_meta_box(self) -> QWidget:
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # 标题候选：单击选用（不再要求双击）
        head = QWidget()
        head_layout = QHBoxLayout(head)
        head_layout.setContentsMargins(0, 0, 0, 0)
        head_layout.setSpacing(8)
        head_layout.addWidget(field_label("标题候选", "单击选用"))
        head_layout.addStretch(1)
        self.regen_button = make_button("重新生成", icon="refresh", theme=self.ctx.theme, ghost=True)
        self.regen_button.clicked.connect(self.regenerate_titles)
        self.regen_button.setEnabled(False)
        head_layout.addWidget(self.regen_button)
        layout.addWidget(head)

        self.titles_list = QListWidget()
        self.titles_list.setObjectName("TitleList")
        self.titles_list.setMaximumHeight(94)
        self.titles_list.setItemDelegate(TitleCandidateDelegate(self.titles_list))
        self.titles_list.itemClicked.connect(self._use_title)
        self.titles_list.currentItemChanged.connect(lambda current, _prev: self._use_title(current))
        layout.addWidget(self.titles_list)

        title_row = QWidget()
        title_layout = QHBoxLayout(title_row)
        title_layout.setContentsMargins(0, 0, 0, 0)
        title_layout.setSpacing(8)
        title_layout.addWidget(field_label("当前标题"))
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("选中上方候选，或直接在这里写")
        self.title_edit.textChanged.connect(self._update_counts)
        title_layout.addWidget(self.title_edit, 1)
        layout.addWidget(title_row)

        layout.addWidget(field_label("简介"))
        self.summary_edit = QTextEdit()
        self.summary_edit.setPlaceholderText("用于发布时的摘要/简介字段")
        self.summary_edit.setFixedHeight(54)
        layout.addWidget(self.summary_edit)

        # 标签：胶囊化，点一下复制、点 × 删除
        tag_head = QWidget()
        tag_head_layout = QHBoxLayout(tag_head)
        tag_head_layout.setContentsMargins(0, 0, 0, 0)
        tag_head_layout.setSpacing(8)
        tag_head_layout.addWidget(field_label("标签", "点击复制 · × 删除"))
        tag_head_layout.addStretch(1)
        self.tags_count = QLabel("0 个")
        self.tags_count.setObjectName("FieldHint")
        tag_head_layout.addWidget(self.tags_count)
        layout.addWidget(tag_head)

        self.tags_flow = FlowRow(spacing=6)
        layout.addWidget(self.tags_flow)

        self.tag_add_edit = QLineEdit()
        self.tag_add_edit.setObjectName("TagAddEdit")
        self.tag_add_edit.setPlaceholderText("＋ 输入标签后回车添加")
        self.tag_add_edit.returnPressed.connect(self._add_tag)
        layout.addWidget(self.tag_add_edit)
        return box

    def _build_action_bar(self) -> QWidget:
        """吸底操作栏：正文再长，这两个按钮也一直可见可点。"""
        bar = QFrame()
        bar.setObjectName("ActionBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(16, 11, 16, 12)
        layout.setSpacing(8)

        self.quality_button = make_button("AI 质量自检", icon="check", theme=self.ctx.theme, ghost=True)
        self.quality_button.setEnabled(False)
        self.quality_button.clicked.connect(self.quality_check)
        layout.addWidget(self.quality_button)

        self.copy_body_button = make_button("复制正文", icon="copy", theme=self.ctx.theme, ghost=True)
        self.copy_body_button.clicked.connect(lambda: self._copy(self.body_edit.toPlainText(), "正文已复制"))
        layout.addWidget(self.copy_body_button)

        self.copy_all_button = make_button("复制全文", icon="copy", theme=self.ctx.theme, ghost=True)
        self.copy_all_button.clicked.connect(lambda: self._copy(self._full_text(), "全文已复制"))
        layout.addWidget(self.copy_all_button)

        layout.addStretch(1)
        self.save_button = make_button("存为草稿", icon="save", theme=self.ctx.theme)
        self.save_button.clicked.connect(lambda: self.save_draft(to_publish=False))
        layout.addWidget(self.save_button)
        self.publish_button = make_button("送入发布中心", icon="send", theme=self.ctx.theme, primary=True)
        self.publish_button.clicked.connect(lambda: self.save_draft(to_publish=True))
        layout.addWidget(self.publish_button)
        return bar

    # ------------------------------------------------------------------
    # 平台联动
    # ------------------------------------------------------------------
    def _on_platform_changed(self) -> None:
        key = self.platform_combo.currentData()
        style = style_for(key)
        # 内容类型与平台是强关联的：默认自动跟随，用户不必重复选择
        if not self.content_type_custom.isChecked():
            self.content_type_edit.setText(style.content_type)
        self.content_type_hint.setText(
            f"已按「{style.label}」自动设为「{style.content_type}」；"
            "如需改写请勾选右侧「自定义」"
            if not self.content_type_custom.isChecked()
            else f"自定义中（平台默认为「{style.content_type}」）"
        )
        self.style_hint.setText(
            f"平台规范：标题 ≤{style.title_max} 字；正文 {style.body_min}-{style.body_max} 字；"
            f"标签 ≤{style.tags_max} 个；建议发布时段 {style.best_hours}"
        )
        self._update_counts()

    def _on_content_type_custom(self, checked: bool) -> None:
        self.content_type_edit.setReadOnly(not checked)
        if not checked:
            self.content_type_edit.setText(style_for(self.platform_combo.currentData()).content_type)
        self._on_platform_changed()

    def apply_theme(self, theme: str) -> None:
        self.advanced_section.apply_theme(theme)
        self._update_counts()

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
        self.step_bar.start("正在连接模型…")
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
        self._tags = []
        self.body_edit.clear()
        self.summary_edit.clear()
        self.title_edit.clear()
        self.titles_list.clear()
        self.tag_add_edit.clear()
        self._render_tags()
        self.quality_result.clear()
        self.quality_button.setEnabled(False)
        self.result_hint.setText("生成中…")
        self.body_edit.setPlaceholderText("正在生成，内容会实时出现…")
        self._update_counts()

    def _on_stream(self, section: str, text: str) -> None:
        # 用分段变化驱动步骤提示：用户能看到模型此刻在写哪一部分
        step = self.STEP_TEXT.get(section)
        if step:
            self.step_bar.set_step(step)
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
        titles = parse_titles(self._titles_buffer)
        existing = [self.titles_list.item(i).text() for i in range(self.titles_list.count())]
        if len(titles) == len(existing):
            return
        self._fill_titles(titles)

    def _fill_titles(self, titles: list[str], *, select_first: bool = True) -> None:
        self._syncing_title = True
        try:
            self.titles_list.clear()
            for title in titles:
                item = QListWidgetItem(title)
                item.setToolTip(title)
                self.titles_list.addItem(item)
            if titles and select_first:
                self.titles_list.setCurrentRow(0)
        finally:
            self._syncing_title = False
        if titles and select_first:
            self._use_title(self.titles_list.item(0))

    def _render_tags_live(self) -> None:
        tags = parse_tags(self._tags_buffer)
        if tags != self._tags:
            self._tags = tags
            self._render_tags()

    def _on_result(self, result) -> None:
        self._last_result = result
        if result.body and self.body_edit.toPlainText().strip() != result.body.strip():
            self._body_buffer = result.body
            self.body_edit.setPlainText(result.body)

        titles = result.titles or parse_titles(self._titles_buffer)
        self._fill_titles(titles)

        summary = result.summary or self._summary_buffer.strip()
        if summary and self.summary_edit.toPlainText().strip() != summary:
            self.summary_edit.setPlainText(summary)

        self._tags = result.tags or parse_tags(self._tags_buffer)
        self._render_tags()

        self.body_edit.setPlaceholderText("生成结果会在这里实时出现，你可以直接在此编辑")
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
        self.step_bar.stop()
        self._worker = None
        self._update_counts()

    def _set_generating(self, busy: bool) -> None:
        self.generate_button.setEnabled(not busy)
        self.generate_button.setText("生成中…" if busy else "开始生成")
        self.stop_button.setEnabled(busy)
        for widget in (
            self.topic_edit,
            self.source_edit,
            self.platform_combo,
            self.extra_edit,
            self.content_type_edit,
            self.goal_edit,
            self.tone_edit,
        ):
            widget.setEnabled(not busy)

    # ------------------------------------------------------------------
    # 输出操作
    # ------------------------------------------------------------------
    def _use_title(self, item: QListWidgetItem | None) -> None:
        if self._syncing_title or item is None:
            return
        self.title_edit.setText(item.text())
        self.titles_list.viewport().update()

    def _current_title(self) -> str:
        """以「当前标题」输入框为准：用户可以直接改它。"""
        text = self.title_edit.text().strip()
        if text:
            return text
        item = self.titles_list.currentItem()
        return item.text() if item is not None else ""

    def _current_tags(self) -> list[str]:
        return list(self._tags)

    def _render_tags(self) -> None:
        chips = [self._make_tag_chip(tag) for tag in self._tags]
        self.tags_flow.set_items(chips)
        self._update_counts()

    def _make_tag_chip(self, tag: str) -> EditableTagChip:
        chip = EditableTagChip(tag, theme=self.ctx.theme)
        chip.clicked.connect(lambda name: self._copy(f"#{name}", f"已复制 #{name}"))
        chip.removed.connect(self._remove_tag)
        return chip

    def _remove_tag(self, tag: str) -> None:
        if tag in self._tags:
            self._tags.remove(tag)
            self._render_tags()

    def _add_tag(self) -> None:
        raw = self.tag_add_edit.text().strip()
        if not raw:
            return
        added = [t for t in parse_tags(raw) if t]
        new = [t for t in added if t not in self._tags]
        if not new:
            self.toast("标签已存在", "info")
            self.tag_add_edit.clear()
            return
        self._tags.extend(new)
        self.tag_add_edit.clear()
        self._render_tags()

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

    def _update_counts(self, *_args) -> None:
        """字数提示：超出平台建议区间时立刻变红，不用等发布前检查。"""
        if not hasattr(self, "body_count"):
            return  # 面板尚未构建完（平台联动的首次调用）
        p = palette(self.ctx.theme)
        style = style_for(self.platform_combo.currentData() or "xiaohongshu")

        body_len = len(self.body_edit.toPlainText())
        over_max = body_len > style.body_max
        under_min = body_len < style.body_min
        if over_max:
            text = f"{body_len} 字 / 建议 {style.body_min}-{style.body_max}　超出 {body_len - style.body_max} 字"
        elif under_min and body_len:
            text = f"{body_len} 字 / 建议 {style.body_min}-{style.body_max}　还差 {style.body_min - body_len} 字"
        else:
            text = f"{body_len} 字 / 建议 {style.body_min}-{style.body_max}"
        color = p.danger if over_max else (p.warning if under_min and body_len else p.text_faint)
        self._set_hint(self.body_count, text, color)

        tag_count = len(self._tags)
        self._set_hint(
            self.tags_count,
            f"{tag_count} 个 / 上限 {style.tags_max}",
            p.danger if tag_count > style.tags_max else p.text_faint,
        )

        title_len = len(self._current_title())
        if title_len > style.title_max:
            self._set_hint(
                self.result_hint,
                f"标题 {title_len} 字，超出「{style.label}」上限 {style.title_max} 字，发布前需精简",
                p.danger,
            )
        elif self.result_hint.property("hintColor") is not None:
            # 标题回到合规区间，撤掉红色警示并还原常规提示
            self.result_hint.setProperty("hintColor", None)
            self.result_hint.setStyleSheet("")
            if self._last_result is not None:
                self.result_hint.setText(self._last_result.usage_hint or "生成完成")

    def _set_hint(self, label: QLabel, text: str, color: str) -> None:
        """只在颜色变化时重设样式：QSS 重解析不便宜，而 textChanged 触发很频繁。"""
        if label.text() != text:
            label.setText(text)
        if label.property("hintColor") != color:
            label.setProperty("hintColor", color)
            label.setStyleSheet(f"color: {color}; background: transparent;")

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
        self.step_bar.set_step("正在重新生成标题…")
        self.banner.show_message("正在重新生成标题…", "info", closable=False)

        self._worker = self.run_task(
            lambda worker: creator_service.regenerate_titles(req, body, llm, cancel=worker.cancel_event),
            on_result=self._on_titles,
            on_error=lambda msg: self.banner.show_message(f"标题生成失败：{msg}", "error"),
            on_done=self._on_regen_done,
            name="titles",
        )

    def _on_titles(self, titles: list[str]) -> None:
        self._fill_titles(list(titles or []))
        self.banner.show_message("标题候选已更新", "success")

    def _on_regen_done(self) -> None:
        self.step_bar.stop()
        self.regen_button.setEnabled(True)
        self.regen_button.setText("重新生成")
        self._worker = None

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
        self.step_bar.set_step("正在做质量自检…")
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
        self.step_bar.stop()
        self.quality_button.setEnabled(True)
        self.quality_button.setText("AI 质量自检")
        self._worker = None

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
        self._tags = list(content.get("tags") or [])
        self._render_tags()
        self._fill_titles(list(content.get("titles") or []), select_first=False)
        if content.get("title"):
            if not content.get("titles"):
                self._fill_titles([content["title"]], select_first=False)
            for row in range(self.titles_list.count()):
                if self.titles_list.item(row).text() == content["title"]:
                    self.titles_list.setCurrentRow(row)
                    break
            self.title_edit.setText(content["title"])
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
