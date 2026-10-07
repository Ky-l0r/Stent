"""内容创作页（企划书 3.2 模块二）。

输入主题/热点/素材 + 目标平台 → 输出正文、多个标题候选、简介与标签。
账号画像影响输出风格；流式输出，可随时中断；可复制、可存草稿、可送发布。

界面要点（v1.0.5 打磨）：

- 左侧：只留高频项；「内容类型 / 内容目标 / 受众 / 语气」做成快捷参数胶囊，点开即选
- 右侧分两层：顶部固定「正文编辑 / AI 质量自检」分段切换，中间内容可独立滚动，
  底部操作栏吸底——正文再长也不用滚到底去找「送入发布中心」
- 标题候选单击选用（右侧出现勾号），双击直接在行内改字，不再单占一个「当前标题」输入框
- 标签是真正的标签云：点胶囊复制、点 × 删除、末尾 + 号就地变输入框，过多自动折叠
- 正文纯白、辅助信息（标题候选 / 简介 / 标签）极浅灰，视线自然落在正文上
- AI 自检拆成「总分 → 四维 → 问题 → 建议」结构化展示，不再是一坨没排版的文字
"""

from __future__ import annotations

import hashlib
import logging

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ...core.db import db, now as db_now
from ...services.creator import (
    CreationRequest,
    creator_service,
    parse_tags,
    parse_titles,
    style_for,
)
from ...services.profile import profile_service
from ..components import (
    AdviceBar,
    AutoGrowTextEdit,
    Card,
    CardTitle,
    CopyMenuButton,
    FlowRow,
    PageHeader,
    ParamChip,
    ScoreReport,
    ScrollArea,
    SegmentedTabs,
    StepIndicator,
    TagCloud,
    TitleCandidateDelegate,
    field_label,
    make_button,
)
from .. import icons
from ..theme import palette
from .base import BasePage

log = logging.getLogger(__name__)

#: 快捷参数的默认值（也是「是否已自定义」的判断基准）
DEFAULT_GOAL = "互动涨粉"

#: 自检结果缓存的存储键：跨会话复用，避免同一版内容反复自检白烧 token
QUALITY_CACHE_KEY = "create_quality_report"

#: 快捷参数胶囊：(key, 显示名, 预设选项)
QUICK_PARAMS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("content_type", "内容类型", ("图文笔记", "口播稿", "长文", "视频简介", "短博")),
    ("goal", "内容目标", ("互动涨粉", "建立专业形象", "直接带货", "活动引流")),
    ("audience", "目标受众", ("学生党", "职场新人", "宝妈", "一二线白领", "中年男性")),
    ("tone", "语气风格", ("轻松口语", "犀利点评", "专业理性", "温暖治愈")),
)


class BodyTextEdit(QTextEdit):
    """正文编辑框。

    ``QTextEdit`` 的默认 ``sizeHint`` 是按「14 行」给的（约 192px），而放进滚动容器后
    ``QScrollArea`` 判断「内容装不装得下」用的正是 ``sizeHint``——默认值偏大，会把标题、
    简介、标签一起顶到视口之外（表现为标签只剩半截、还像被底部操作栏盖住）。

    这里把 ``sizeHint`` 的高度对齐到最小高度：窗口宽裕时仍由 ``Expanding`` 吸收剩余空间，
    窗口局促时它是第一个让位的控件，辅助信息优先保持完整。
    """

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt 命名
        hint = super().sizeHint()
        return QSize(hint.width(), max(1, self.minimumHeight()))


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

    #: 标题候选列表：每条 32px，最多铺 3 条；空列表时也占一点高度，避免布局跳动
    TITLE_ROW_HEIGHT = 32
    TITLE_LIST_EMPTY_HEIGHT = 42
    TITLE_LIST_MAX_ROWS = 3

    def build(self) -> None:
        self._worker = None
        self._last_result = None
        self._seed: dict | None = None
        self._titles_buffer = ""
        self._tags_buffer = ""
        self._summary_buffer = ""
        self._body_buffer = ""
        self._syncing_title = False
        #: 正在编辑的草稿 id（从草稿箱或发布中心载入时设置；再次保存走更新）
        self._editing_id: int | None = None
        self._quality: dict | None = None
        #: 上面这份自检结果对应的内容指纹；内容一改就对不上，即「自检已过期」
        self._quality_fingerprint = ""
        self._cached_quality: dict | None = None
        self._load_quality_cache()

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

        # 发布中心的修改建议：跳过来改稿时钉在页面顶部，改完一条还能看下一条
        self.advice_bar = AdviceBar(theme=self.ctx.theme)
        self.add(self.advice_bar)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_input_panel())
        splitter.addWidget(self._build_output_panel())
        # 输出区是「预览/编辑」主场，给它更多宽度与高度
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([390, 730])
        self.add(splitter, 1)

        self._on_platform_changed()

        QShortcut(QKeySequence("Ctrl+Return"), self, activated=self.generate)
        QShortcut(QKeySequence("Ctrl+Enter"), self, activated=self.generate)

    # ------------------------------------------------------------------
    # 左侧：输入面板
    # ------------------------------------------------------------------
    def _build_input_panel(self) -> QWidget:
        card = Card(padding=16, spacing=10)
        card.setMinimumWidth(310)
        card.add(CardTitle("创作输入", icon="pen-line", theme=self.ctx.theme))

        self.topic_edit = QTextEdit()
        self.topic_edit.setPlaceholderText("想写什么？例如「通勤穿搭的 3 个显瘦技巧」")
        self.topic_edit.setFixedHeight(60)
        card.add(field_label("主题 / 选题", "必填"))
        card.add(self.topic_edit)

        self.source_edit = QTextEdit()
        self.source_edit.setPlaceholderText("可选：热点原文、素材片段或参考链接说明")
        self.source_edit.setFixedHeight(64)
        card.add(field_label("热点 / 素材", "可选"))
        card.add(self.source_edit)

        self.platform_combo = QComboBox()
        for item in creator_service.platforms():
            self.platform_combo.addItem(item["label"], item["key"])
        self.platform_combo.currentIndexChanged.connect(self._on_platform_changed)
        self.platform_field = field_label(
            "目标平台", help_text=self._platform_tooltip(self._platform_key())
        )
        card.add(self.platform_field)
        card.add(self.platform_combo)

        # 补充要求：给足但不过量的高度，长 prompt 够写又不挤占下面的快捷参数
        self.extra_edit = QTextEdit()
        self.extra_edit.setPlaceholderText(
            "例如：必须提到三个具体品牌；不要出现价格；结尾引导评论"
        )
        self.extra_edit.setMinimumHeight(86)
        card.add(field_label("补充要求", "越具体越贴近预期"))
        card.add(self.extra_edit)

        # 快捷参数：摊成胶囊平铺，一眼看得到当前设定，改动只要一次点击
        card.add(field_label("快捷参数", "点开选择"))
        self.params_row = FlowRow(spacing=6)
        self.param_chips: dict[str, ParamChip] = {}
        chips: list[QWidget] = []
        for key, label, options in QUICK_PARAMS:
            chip = ParamChip(label, options=options, theme=self.ctx.theme)
            chip.changed.connect(lambda _v, k=key: self._on_param_changed(k))
            self.param_chips[key] = chip
            chips.append(chip)
        self.params_row.set_items(chips)
        card.add(self.params_row)

        self.profile_hint = QLabel()
        self.profile_hint.setObjectName("Faint")
        self.profile_hint.setWordWrap(True)
        card.add(self.profile_hint)

        card.body().addStretch(1)
        return card

    def _on_param_changed(self, key: str) -> None:
        # 内容类型直接跟随参数变化；其余参数只影响下次生成
        if key == "content_type":
            self._refresh_param_placeholder()
        self._update_counts()

    def _refresh_param_placeholder(self) -> None:
        style = style_for(self._platform_key())
        chip = self.param_chips["content_type"]
        chip.setToolTip(f"内容类型：{chip.value() or f'跟随平台（{style.content_type}）'}")

    # ------------------------------------------------------------------
    # 右侧：输出面板
    # ------------------------------------------------------------------
    def _build_output_panel(self) -> QWidget:
        # padding=0：顶部固定栏、中部滚动区、底部吸底栏各自管理内边距
        card = Card(padding=0, spacing=0)
        root = card.body()

        root.addWidget(self._build_output_header())
        root.addWidget(self._build_step_bar())

        # 两个视图共用同一块区域：正文编辑 / AI 自检报告
        self.view_stack = QStackedWidget()
        self.view_stack.addWidget(self._build_editor_view())
        self.report_view = ScoreReport(theme=self.ctx.theme)
        report_holder = ScrollArea()
        report_inner = QWidget()
        report_layout = QVBoxLayout(report_inner)
        report_layout.setContentsMargins(16, 12, 16, 12)
        report_layout.setSpacing(10)
        report_layout.addWidget(self._build_stale_bar())
        report_layout.addWidget(self.report_view)
        report_layout.addStretch(1)
        report_holder.setWidget(report_inner)
        self.view_stack.addWidget(report_holder)
        root.addWidget(self.view_stack, 1)

        root.addWidget(self._build_action_bar())
        return card

    def _build_stale_bar(self) -> QWidget:
        """自检过期提示：内容改过之后，旧分数就不能再当作结论。"""
        bar = QFrame()
        bar.setObjectName("StaleBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(12, 9, 12, 9)
        layout.setSpacing(10)
        icon = QLabel()
        icon.setFixedSize(18, 18)
        icon.setPixmap(icons.pixmap("alert", palette(self.ctx.theme).warning, 18))
        layout.addWidget(icon)
        self.stale_label = QLabel()
        self.stale_label.setObjectName("StaleText")
        self.stale_label.setWordWrap(True)
        layout.addWidget(self.stale_label, 1)
        self.stale_button = make_button("重新自检", icon="sparkles", theme=self.ctx.theme, ghost=True)
        self.stale_button.setToolTip("内容已改动，重新自检才会消耗一次模型调用")
        self.stale_button.clicked.connect(lambda: self.quality_check(True))
        layout.addWidget(self.stale_button)
        self.stale_bar = bar
        bar.setVisible(False)
        return bar

    def _build_output_header(self) -> QWidget:
        """顶部固定栏：视图切换常驻，切到哪儿都不用滚动。"""
        bar = QWidget()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(16, 12, 16, 8)
        layout.setSpacing(10)
        self.view_tabs = SegmentedTabs(
            (("editor", "正文编辑"), ("report", "AI 质量自检")), theme=self.ctx.theme
        )
        self.view_tabs.changed.connect(self._on_view_changed)
        layout.addWidget(self.view_tabs)
        layout.addStretch(1)
        self.regen_button = make_button("重新生成标题", icon="refresh", theme=self.ctx.theme, ghost=True)
        self.regen_button.clicked.connect(self.regenerate_titles)
        self.regen_button.setEnabled(False)
        layout.addWidget(self.regen_button)
        return bar

    def _build_step_bar(self) -> QWidget:
        holder = QWidget()
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(16, 0, 16, 0)
        layout.setSpacing(0)
        self.step_bar = StepIndicator(theme=self.ctx.theme)
        layout.addWidget(self.step_bar)
        return holder

    def _build_editor_view(self) -> QWidget:
        """正文 / 标题 / 简介 / 标签：各块完整显示，装不下才整体滚动。

        这里**必须**保留滚动容器。旧版的问题是标签云报出虚高的最小高度
        （8 个标签算成 264px），把下面的内容顶出可视区，看着像「被一大块空白遮住」；
        那个根因已在 ``FlowLayout`` 里修掉。但反过来把滚动去掉同样不行：
        内容一多（正文长、标题三条、简介三行），总高必然超过可视区，
        ``QVBoxLayout`` 会开始挤压各块——标签云被压到最小高度以下、胶囊只剩半截，
        底部操作栏还会盖在内容上。滚动交给外层统一负责，每块才能保持完整。
        """
        scroll = ScrollArea()
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(16, 6, 16, 10)
        layout.setSpacing(6)

        # ---- 正文（最亮的一层，也是唯一吸收剩余高度的控件）----
        layout.addWidget(field_label("正文", "可直接编辑"))
        self.body_edit = BodyTextEdit()
        self.body_edit.setPlaceholderText("生成结果会在这里实时出现，你可以直接在此编辑")
        # 下限只给 5~6 行：窗口够大时正文会吸收全部剩余高度；窗口小到极限时，
        # 也要先保证下面的标题/简介/标签完整可见，而不是把正文留着、把标签挤成半截
        self.body_edit.setMinimumHeight(110)
        self.body_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.body_edit.textChanged.connect(self._update_counts)
        self.body_edit.textChanged.connect(self._on_content_edited)
        layout.addWidget(self.body_edit, 1)

        # ---- 标题候选（单击选用 / 双击改字）----
        layout.addWidget(field_label("标题候选", "单击选用 · 双击改字"))
        self.titles_list = QListWidget()
        self.titles_list.setObjectName("TitleList")
        self.titles_list.setFixedHeight(self.TITLE_LIST_EMPTY_HEIGHT)
        self.titles_list.setItemDelegate(TitleCandidateDelegate(self.titles_list))
        self.titles_list.itemClicked.connect(self._on_title_clicked)
        self.titles_list.itemChanged.connect(self._on_title_edited)
        layout.addWidget(self.titles_list)

        # ---- 简介（按内容长高，不再固定 62px 截断）----
        layout.addWidget(field_label("简介", "发布时的摘要字段"))
        self.summary_edit = AutoGrowTextEdit(min_height=58, max_height=112)
        self.summary_edit.setObjectName("SurfaceInput")
        self.summary_edit.setPlaceholderText("用于发布时的摘要/简介字段（内容多时会自动长高）")
        self.summary_edit.textChanged.connect(self._on_content_edited)
        layout.addWidget(self.summary_edit)

        # ---- 标签云（最小高度按「一行标签 + 内边距」给足，别让它被压成半截）----
        layout.addWidget(field_label("标签", "点击复制 · × 删除"))
        self.tag_cloud = TagCloud(theme=self.ctx.theme, placeholder="输入标签后回车")
        # 实测一行标签连内边距需要 48px；给足这个下限，空间紧张时也不会把胶囊切成半截
        # （标签变多、换到第二行时，sizeHint 会自己把这块撑高）
        self.tag_cloud.setMinimumHeight(48)
        self.tag_cloud.changed.connect(lambda _tags: self._update_counts())
        self.tag_cloud.changed.connect(lambda _tags: self._on_content_edited())
        layout.addWidget(self.tag_cloud)

        scroll.setWidget(inner)
        return scroll

    def _build_action_bar(self) -> QWidget:
        """吸底操作栏：正文再长，这两个按钮也一直可见可点。"""
        bar = QFrame()
        bar.setObjectName("ActionBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(16, 10, 16, 12)
        layout.setSpacing(10)

        # 字数状态：实时反映是否超出平台建议区间
        self.body_count = QLabel("0 字")
        self.body_count.setObjectName("FieldHint")
        layout.addWidget(self.body_count)
        layout.addStretch(1)

        # 质量自检：平时是安静的入口，出分后变成状态徽章，紧挨主操作
        self.quality_button = make_button("质量自检", icon="sparkles", theme=self.ctx.theme, ghost=True)
        self.quality_button.clicked.connect(self._on_quality_clicked)
        layout.addWidget(self.quality_button)

        self.copy_button = CopyMenuButton(
            (
                ("复制正文", lambda: self._copy(self.body_edit.toPlainText(), "正文已复制")),
                ("复制标题", lambda: self._copy(self._current_title(), "标题已复制")),
                ("复制全文", lambda: self._copy(self._full_text(), "全文已复制")),
            ),
            text="复制",
            theme=self.ctx.theme,
        )
        layout.addWidget(self.copy_button)

        self.save_button = make_button("存为草稿", icon="save", theme=self.ctx.theme)
        self.save_button.clicked.connect(lambda: self.save_draft(to_publish=False))
        layout.addWidget(self.save_button)
        self.publish_button = make_button("送入发布中心", icon="send", theme=self.ctx.theme, primary=True)
        self.publish_button.clicked.connect(lambda: self.save_draft(to_publish=True))
        layout.addWidget(self.publish_button)
        return bar

    # ------------------------------------------------------------------
    # 视图切换
    # ------------------------------------------------------------------
    def _on_view_changed(self, key: str) -> None:
        self.view_stack.setCurrentIndex(0 if key == "editor" else 1)
        if key == "report":
            self._refresh_report()

    def _show_report_tab(self) -> None:
        self.view_tabs.set_current("report")
        self.view_stack.setCurrentIndex(1)
        self._refresh_report()

    def _on_quality_clicked(self) -> None:
        """有分数就跳到报告页看；没跑过就先跑一次自检。"""
        if self._quality is not None:
            self._show_report_tab()
        else:
            self.quality_check()

    def _refresh_report(self) -> None:
        self.report_view.apply_theme(self.ctx.theme)
        self.report_view.set_report(self._quality or {})
        self._refresh_stale_bar()

    # ------------------------------------------------------------------
    # 自检结果缓存：同一版内容不重复烧 token
    # ------------------------------------------------------------------
    def _content_fingerprint(self) -> str:
        """当前内容的指纹。任一字段变了指纹就变，用来判断自检是否过期。"""
        payload = "\x1f".join(
            (
                self._platform_key(),
                self._current_title(),
                self.body_edit.toPlainText().strip(),
                self.summary_edit.toPlainText().strip(),
                ",".join(self._current_tags()),
            )
        )
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()

    def _load_quality_cache(self) -> None:
        """读回上一次的自检结果（跨会话保留，重启后仍可复用）。"""
        try:
            cached = db.get_kv(QUALITY_CACHE_KEY)
        except Exception:  # noqa: BLE001 - 缓存读不到就当没有
            cached = None
        if isinstance(cached, dict) and isinstance(cached.get("report"), dict):
            self._cached_quality = cached

    def _quality_is_stale(self) -> bool:
        return bool(self._quality) and self._quality_fingerprint != self._content_fingerprint()

    def _on_content_edited(self) -> None:
        """内容改动后：能对上缓存就复用，对不上就把旧结论标成过期。"""
        if not hasattr(self, "advice_bar"):
            return
        if self._quality is None and self._cached_quality is not None:
            if self._cached_quality.get("fingerprint") == self._content_fingerprint():
                self._adopt_cached_quality(self._cached_quality)
                return
        self._refresh_quality_button()
        self._refresh_stale_bar()

    def _adopt_cached_quality(self, cached: dict) -> None:
        report = cached.get("report")
        if not isinstance(report, dict) or not report:
            return
        self._quality = report
        self._quality_fingerprint = str(cached.get("fingerprint") or "")
        self._refresh_report()
        self._refresh_quality_button()

    def _save_quality_cache(self, report: dict) -> None:
        payload = {
            "fingerprint": self._quality_fingerprint,
            "at": db_now(),
            "platform": self._platform_key(),
            "report": report,
        }
        self._cached_quality = payload
        try:
            db.set_kv(QUALITY_CACHE_KEY, payload)
        except Exception:  # noqa: BLE001 - 存不下不影响本次结果
            log.debug("保存自检缓存失败", exc_info=True)

    def _refresh_stale_bar(self) -> None:
        bar = getattr(self, "stale_bar", None)
        if bar is None:
            return
        if not self._quality_is_stale():
            bar.setVisible(False)
            return
        at = str((self._cached_quality or {}).get("at") or "")
        self.stale_label.setText(
            "自检已过期：内容在这份报告之后改过了，分数不再代表当前版本"
            + (f"（上次自检 {at}）" if at else "")
            + "。不想再花一次模型调用，也可以直接发布。"
        )
        bar.setVisible(True)

    # ------------------------------------------------------------------
    # 平台联动
    # ------------------------------------------------------------------
    def _platform_key(self) -> str:
        if hasattr(self, "platform_combo"):
            return self.platform_combo.currentData() or "xiaohongshu"
        return "xiaohongshu"

    @staticmethod
    def _platform_tooltip(platform: str) -> str:
        style = style_for(platform)
        return (
            f"<b>{style.label} · 平台规范</b><br>"
            f"标题：≤ {style.title_max} 字<br>"
            f"正文：{style.body_min}-{style.body_max} 字<br>"
            f"标签：≤ {style.tags_max} 个<br>"
            f"建议发布：{style.best_hours}<br><br>"
            f"<b>平台禁忌</b><br>{style.taboos}"
        )

    def _on_platform_changed(self) -> None:
        # 平台规范不占版面：只在问号 tooltip 里按当前平台更新
        self.platform_field.set_help_text(self._platform_tooltip(self._platform_key()))
        self._refresh_param_placeholder()
        self._update_counts()

    def _effective_content_type(self) -> str:
        """内容类型默认跟随平台；用快捷参数改过才用自定义值。"""
        custom = self.param_chips["content_type"].value() if hasattr(self, "param_chips") else ""
        return custom or style_for(self._platform_key()).content_type

    def apply_theme(self, theme: str) -> None:
        self.platform_field.apply_theme(theme)
        self.report_view.apply_theme(theme)
        self.tag_cloud.apply_theme(theme)
        self.advice_bar.apply_theme(theme)
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
        # 发布中心「去修改」会同时带来建议列表：钉在页面顶部，边改边看
        advice = kwargs.get("advice")
        if advice:
            self.advice_bar.set_items(list(advice))

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

        # 发布中心「去修改」会带上 content_id，直接把这版草稿载回来改
        content_id = kwargs.get("content_id")
        if content_id:
            try:
                content = db.get_content(int(content_id))
            except Exception:  # noqa: BLE001
                content = None
            if content:
                self._load_content(content)
                self.banner.show_message(
                    f"已载入待修改的草稿 #{content_id}，改完记得重新「存为草稿」", "info"
                )
            else:
                self.toast(f"草稿 #{content_id} 不存在", "warn")

    # ------------------------------------------------------------------
    # 生成
    # ------------------------------------------------------------------
    def _request(self) -> CreationRequest | None:
        topic = self.topic_edit.toPlainText().strip()
        if not topic:
            self.toast("请先填写主题", "warn")
            return None
        values = {key: chip.value() for key, chip in self.param_chips.items()}
        return CreationRequest(
            topic=topic,
            platform=self.platform_combo.currentData() or "xiaohongshu",
            content_type=self._effective_content_type(),
            goal=values.get("goal") or DEFAULT_GOAL,
            audience=values.get("audience", ""),
            tone=values.get("tone", ""),
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
        self.view_tabs.set_current("editor")
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
        self._quality = None
        self._quality_fingerprint = ""
        self.body_edit.clear()
        self.summary_edit.clear()
        self.titles_list.clear()
        self._sync_title_list_height()
        self.tag_cloud.set_tags([])
        self._refresh_report()
        self._refresh_quality_button()
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
                item.setToolTip(f"{title}\n（单击选用，双击可直接改字）")
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
                self.titles_list.addItem(item)
            if titles and select_first:
                self.titles_list.setCurrentRow(0)
        finally:
            self._syncing_title = False
        self._sync_title_list_height()
        if titles and select_first:
            self._use_title(self.titles_list.item(0))

    def _sync_title_list_height(self) -> None:
        """列表高度跟着条数走：只有一条时不空占三行，也不把下面的简介挤出去。"""
        if not hasattr(self, "titles_list"):
            return
        rows = min(max(self.titles_list.count(), 0), self.TITLE_LIST_MAX_ROWS)
        height = self.TITLE_LIST_EMPTY_HEIGHT if rows == 0 else rows * self.TITLE_ROW_HEIGHT + 6
        if self.titles_list.height() != height:
            self.titles_list.setFixedHeight(height)

    def _render_tags_live(self) -> None:
        tags = parse_tags(self._tags_buffer)
        if tags != self.tag_cloud.tags():
            self.tag_cloud.set_tags(tags)
            self._update_counts()

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

        self.tag_cloud.set_tags(result.tags or parse_tags(self._tags_buffer))

        self.body_edit.setPlaceholderText("生成结果会在这里实时出现，你可以直接在此编辑")
        self.regen_button.setEnabled(True)
        self.banner.show_message(
            "生成完成，可直接编辑后「存为草稿」或「送入发布中心」", "success", summary="生成完成"
        )
        self.ctx.notify_data_changed("create")
        self._update_counts()

    def _on_error(self, message: str) -> None:
        self.banner.show_message(f"生成失败：{message}", "error")
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
        ):
            widget.setEnabled(not busy)
        for chip in self.param_chips.values():
            chip.setEnabled(not busy)

    # ------------------------------------------------------------------
    # 输出操作
    # ------------------------------------------------------------------
    def _on_title_clicked(self, item: QListWidgetItem | None) -> None:
        self._use_title(item)

    def _on_title_edited(self, item: QListWidgetItem) -> None:
        """双击改字后就地生效：把新标题写回候选与当前标题。"""
        if self._syncing_title:
            return
        text = item.text().strip()
        if not text:
            return
        item.setToolTip(f"{text}\n（单击选用，双击可直接改字）")
        self.titles_list.viewport().update()
        self._update_counts()
        self._on_content_edited()

    def _use_title(self, item: QListWidgetItem | None) -> None:
        if self._syncing_title or item is None:
            return
        self.titles_list.setCurrentItem(item)
        self.titles_list.viewport().update()
        self._update_counts()

    def _current_title(self) -> str:
        """当前标题 = 候选里选中的那一条（列表为空时可从正文首行兜底）。"""
        item = self.titles_list.currentItem()
        if item is not None:
            return item.text().strip()
        return ""

    def _current_tags(self) -> list[str]:
        return self.tag_cloud.tags()

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
        """字数提示：超出平台建议区间时立刻变红，不用等发布前检查。"""
        if not hasattr(self, "body_count"):
            return
        p = palette(self.ctx.theme)
        style = style_for(self._platform_key())

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

        title_len = len(self._current_title())
        if title_len > style.title_max:
            text += f"　·　标题 {title_len}/{style.title_max} 字超出"
            color = p.danger
        self._set_hint(self.body_count, text, color)

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
        self.regen_button.setText("重新生成标题")
        self._worker = None

    def _refresh_quality_button(self) -> None:
        """自检完成后收成状态徽章；内容改过就明确标成「已过期」。"""
        if self._quality:
            total = self._quality.get("total")
            score = f" {total}/40" if total is not None else ""
            if self._quality_is_stale():
                self.quality_button.setText(f"✨ 自检{score} · 已过期")
                self.quality_button.setToolTip(
                    "内容在自检之后改过了，这份报告只对上一版有效；点这里查看，或重新自检"
                )
            else:
                self.quality_button.setText(f"✨ 自检{score}")
                self.quality_button.setToolTip("点击查看完整自检报告（内容未变，无需重复自检）")
        else:
            self.quality_button.setText("质量自检")
            self.quality_button.setToolTip("让 AI 按钩子/平台匹配/信息价值/真人感四项打分")

    def quality_check(self, force: bool = False) -> None:
        """跑一次 AI 质量自检。

        内容没变就直接复用上次结果：自检要花一次模型调用，同一版内容重复跑纯属浪费。
        只有内容真的改了（``force=True`` 或指纹对不上）才会重新调用模型。
        """
        if self._last_result is None and not self.body_edit.toPlainText().strip():
            self.toast("请先生成内容", "warn")
            return

        fingerprint = self._content_fingerprint()
        if not force and self._quality and self._quality_fingerprint == fingerprint:
            self.toast("内容未改动，直接使用上次自检结果", "info")
            self._show_report_tab()
            return
        if not force and self._quality is None and self._cached_quality is not None:
            if self._cached_quality.get("fingerprint") == fingerprint:
                self._adopt_cached_quality(self._cached_quality)
                self.toast("已复用上次的自检结果（内容未改动）", "info")
                self._show_report_tab()
                return

        llm = self.ctx.require_llm()
        if llm is None:
            return
        from ...services.creator import CreationResult

        result = CreationResult(
            platform=self._platform_key(),
            body=self.body_edit.toPlainText().strip(),
            titles=[self._current_title()] if self._current_title() else [],
            summary=self.summary_edit.toPlainText().strip(),
            tags=self._current_tags(),
        )
        self._pending_quality_fingerprint = fingerprint
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
        self._quality = data or {}
        # 用发起时的指纹落库：自检期间用户可能又改了字，那份结果只对当时的内容有效
        self._quality_fingerprint = getattr(self, "_pending_quality_fingerprint", "") or ""
        self._save_quality_cache(self._quality)
        self._refresh_report()
        self._refresh_quality_button()
        self._show_report_tab()
        self.banner.show_message("质量自检完成（已缓存，内容不变时不会重复消耗）", "success")

    def _on_quality_done(self) -> None:
        self.step_bar.stop()
        self.quality_button.setEnabled(True)
        self._refresh_quality_button()
        self._worker = None

    # ------------------------------------------------------------------
    def _refresh_save_button(self) -> None:
        """正在编辑已有草稿时，把「会覆盖哪一条」写在按钮上，避免误存出重复内容。"""
        if self._editing_id:
            self.save_button.setText(f"更新草稿 #{self._editing_id}")
            self.save_button.setToolTip("将覆盖这条已有草稿，而不是新建一条")
        else:
            self.save_button.setText("存为草稿")
            self.save_button.setToolTip("")

    def save_draft(self, *, to_publish: bool) -> None:
        body = self.body_edit.toPlainText().strip()
        if not body:
            self.toast("正文为空，无法保存", "warn")
            return
        titles = [self.titles_list.item(i).text() for i in range(self.titles_list.count())]
        title = self._current_title() or (titles[0] if titles else "")
        seed = self._seed or {}
        fields = dict(
            topic=self.topic_edit.toPlainText().strip(),
            platform=self.platform_combo.currentData() or "",
            title=title,
            titles=titles,
            body=body,
            summary=self.summary_edit.toPlainText().strip(),
            tags=self._current_tags(),
            status="ready",
        )
        try:
            if self._editing_id:
                # 从发布中心「去修改」进来的：更新原草稿，而不是又存出一条新的
                db.update_content(self._editing_id, **fields)
                content_id = self._editing_id
                self.ctx.db.log_action("create", "update_draft", f"id={content_id}")
            else:
                content_id = db.create_content(
                    **fields,
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
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        content = dialog.selected if accepted else None
        dialog.setParent(None)
        dialog.deleteLater()
        if content is not None:
            self._load_content(content)

    def _load_content(self, content: dict) -> None:
        # 记住来源草稿 id：再次保存时更新这一条，避免「去修改」改出一堆重复草稿
        self._editing_id = int(content.get("id") or 0) or None
        self._refresh_save_button()
        self.topic_edit.setPlainText(content.get("topic", ""))
        index = self.platform_combo.findData(content.get("platform", ""))
        if index >= 0:
            self.platform_combo.setCurrentIndex(index)
        self.body_edit.setPlainText(content.get("body", ""))
        self.summary_edit.setPlainText(content.get("summary", ""))
        self.tag_cloud.set_tags(list(content.get("tags") or []))
        self._fill_titles(list(content.get("titles") or []), select_first=False)
        if content.get("title"):
            if not content.get("titles"):
                self._fill_titles([content["title"]], select_first=False)
            for row in range(self.titles_list.count()):
                if self.titles_list.item(row).text() == content["title"]:
                    self.titles_list.setCurrentRow(row)
                    break
        self.banner.show_message(f"已载入草稿 #{content.get('id')}", "info")
        self._update_counts()
        # 载入的如果是「刚自检过的那一版」，直接把上次报告恢复出来
        self._on_content_edited()


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
