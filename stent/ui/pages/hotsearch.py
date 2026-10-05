"""热点发现页（企划书 3.2 模块一）。

列表展示 + 关键词与垂类筛选 + 一键送入创作。
多平台并发拉取，结果本地缓存 5~10 分钟。

界面要点（v1.0.1 打磨）：

- 筛选区压成两行：平台胶囊一行、关键词 AND 垂类一行，把高度还给列表
- 平台不再单独占一列（同一平台重复几十次），改成标题左侧的品牌色图标
- 排名用金银铜徽章、热度用微型条形图 + 分级配色，扫一眼就能抓到重点
- 单击高亮选中行（底色加深），操作统一收到最右侧的「前往创作 / 打开网页」
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from ...config import config_manager
from ...services.hotsearch import (
    ALL_PLATFORMS,
    DEFAULT_PLATFORMS,
    PLATFORM_LABELS,
    HotItem,
    PlatformResult,
    hotsearch_service,
)
from ..components import (
    HEAT_ROLE,
    PLATFORM_ROLE,
    Card,
    CardTitle,
    CategoryPicker,
    EmptyState,
    FilterChip,
    FlowRow,
    PageHeader,
    RowActionsDelegate,
    SkeletonList,
    attach_hot_table_visuals,
    make_button,
    parse_heat,
)
from .base import BasePage

#: 表格列
COL_RANK, COL_TITLE, COL_HEAT, COL_CATEGORY, COL_ACTIONS = range(5)


class HotSearchPage(BasePage):
    key = "hotsearch"
    title = "热点发现"
    icon = "trending-up"

    def build(self) -> None:
        self._results: dict[str, PlatformResult] = {}
        self._filtered: list[HotItem] = []
        self._platform_chips: dict[str, FilterChip] = {}
        self._worker = None

        header = PageHeader(
            "热点发现",
            "聚合微博、抖音、知乎、B 站、百度、头条等热榜；单击选中，再用行尾按钮送入创作或打开原网页",
        )
        # 状态灯放在主操作左侧：日常状态只占一枚小圆点，页面顶部留给标题与刷新按钮
        header.add_action(self.status_light)
        self.refresh_button = make_button("刷新热榜", icon="refresh", theme=self.ctx.theme, primary=True)
        self.refresh_button.clicked.connect(lambda: self.reload(force=True))
        header.add_action(self.refresh_button)
        self.add(header)

        self._build_toolbar()
        self._build_table()

    # ------------------------------------------------------------------
    # 筛选区
    # ------------------------------------------------------------------
    def _build_toolbar(self) -> None:
        toolbar = Card(padding=12, spacing=8)
        self.add(toolbar)

        platforms = QWidget()
        platform_layout = QHBoxLayout(platforms)
        platform_layout.setContentsMargins(0, 0, 0, 0)
        platform_layout.setSpacing(10)
        platform_label = QLabel("平台")
        platform_label.setObjectName("SectionLabel")
        platform_label.setFixedWidth(30)
        platform_layout.addWidget(platform_label, 0, Qt.AlignmentFlag.AlignTop)
        self.platform_row = FlowRow(spacing=6)
        platform_layout.addWidget(self.platform_row, 1)
        toolbar.add(platforms)
        self._build_platform_chips()

        filters = QWidget()
        filter_layout = QHBoxLayout(filters)
        filter_layout.setContentsMargins(0, 0, 0, 0)
        filter_layout.setSpacing(8)

        self.keyword_edit = QLineEdit()
        self.keyword_edit.setPlaceholderText("按关键词过滤标题（即时生效）")
        self.keyword_edit.setClearButtonEnabled(True)
        self.keyword_edit.setToolTip("关键词与垂类为「与」关系：先选垂类，再在结果里搜词")
        self.keyword_edit.textChanged.connect(self._apply_filters)
        filter_layout.addWidget(self.keyword_edit, 1)

        # 明确写出「AND」：搜索与垂类叠加生效，避免用户以为筛选失效
        and_label = QLabel("AND")
        and_label.setObjectName("Faint")
        filter_layout.addWidget(and_label)

        self.category_picker = CategoryPicker(columns=2)
        self.category_picker.changed.connect(self._apply_filters)
        filter_layout.addWidget(self.category_picker)

        self.clear_button = make_button("清除筛选", icon="x", theme=self.ctx.theme, ghost=True)
        self.clear_button.setToolTip("清空关键词与垂类筛选")
        self.clear_button.clicked.connect(self._clear_filters)
        filter_layout.addWidget(self.clear_button)

        filter_layout.addStretch(1)
        self.count_label = QLabel("尚未加载")
        self.count_label.setObjectName("CardHint")
        filter_layout.addWidget(self.count_label)

        toolbar.add(filters)

    def _build_platform_chips(self) -> None:
        enabled = set(getattr(config_manager.config, "hotlist_platforms", None) or DEFAULT_PLATFORMS)
        chips: list[QWidget] = []
        for key in ALL_PLATFORMS:
            if key not in PLATFORM_LABELS:
                continue
            chip = FilterChip(
                PLATFORM_LABELS[key],
                checked=key in enabled,
                tooltip=f"聚合「{PLATFORM_LABELS[key]}」热榜" + ("" if key in DEFAULT_PLATFORMS else "（垂类频道）"),
            )
            chip.toggled.connect(lambda checked, k=key: self._on_chip_toggled(k, checked))
            self._platform_chips[key] = chip
            chips.append(chip)
        self.platform_row.set_items(chips)

    def _on_chip_toggled(self, key: str, checked: bool) -> None:
        if not any(chip.isChecked() for chip in self._platform_chips.values()):
            # 全部取消会让列表彻底空掉，这里直接拦住并说明原因
            chip = self._platform_chips.get(key)
            if chip is not None:
                chip.blockSignals(True)
                chip.setChecked(True)
                chip.blockSignals(False)
            self.toast("至少保留一个平台", "warn")
            return
        config_manager.update(hotlist_platforms=list(self._selected_platforms()))
        # 平台变了，各垂类的可选数量也跟着变
        self._refresh_categories()
        self._apply_filters()

    def _selected_platforms(self) -> list[str]:
        chosen = [k for k, c in self._platform_chips.items() if c.isChecked()]
        return chosen or list(DEFAULT_PLATFORMS)

    # ------------------------------------------------------------------
    # 列表区
    # ------------------------------------------------------------------
    def _build_table(self) -> None:
        table_card = Card(padding=12, spacing=8)
        self.add(table_card, 1)

        list_title = CardTitle("热榜列表", icon="trending-up", theme=self.ctx.theme)
        self.copy_button = make_button("复制标题", icon="copy", theme=self.ctx.theme, ghost=True)
        self.copy_button.setToolTip("复制当前选中条目的标题")
        self.copy_button.clicked.connect(self._copy_selected)
        self.copy_button.setEnabled(False)
        list_title.add_action(self.copy_button)
        table_card.add(list_title)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["排名", "标题", "热度", "垂类", "操作"])
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(42)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(False)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        header_view = self.table.horizontalHeader()
        header_view.setSectionResizeMode(COL_TITLE, QHeaderView.ResizeMode.Stretch)
        for column in (COL_RANK, COL_HEAT, COL_CATEGORY, COL_ACTIONS):
            header_view.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
        header_view.setHighlightSections(False)
        self.table.setColumnWidth(COL_RANK, 60)
        self.table.setColumnWidth(COL_HEAT, 132)
        self.table.setColumnWidth(COL_CATEGORY, 76)
        self.table.setColumnWidth(COL_ACTIONS, 158)
        # 表头与数据同向对齐：排名/垂类/操作居中，热度右对齐（便于纵向比较数值）
        for column, alignment in (
            (COL_RANK, Qt.AlignmentFlag.AlignCenter),
            (COL_TITLE, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            (COL_HEAT, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
            (COL_CATEGORY, Qt.AlignmentFlag.AlignCenter),
            (COL_ACTIONS, Qt.AlignmentFlag.AlignCenter),
        ):
            header_item = self.table.horizontalHeaderItem(column)
            if header_item is not None:
                header_item.setTextAlignment(alignment)

        # 行底色 / 排名徽章 / 标题平台图标 / 热度条形图 统一由委托绘制
        self._delegates = attach_hot_table_visuals(
            self.table,
            title_column=COL_TITLE,
            rank_column=COL_RANK,
            heat_column=COL_HEAT,
        )
        self.heat_delegate = self._delegates["heat"]
        self.actions_delegate = RowActionsDelegate(self.table, column=COL_ACTIONS).attach(self.table)
        self.actions_delegate.primary_clicked = self._send_row
        self.actions_delegate.secondary_clicked = self._open_row
        self.table.setItemDelegateForColumn(COL_ACTIONS, self.actions_delegate)

        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        table_card.add(self.table, 1)

        # 骨架屏覆盖在列表区域之上：刷新时撑住版面，不出现留白或闪烁
        self.skeleton = SkeletonList(rows=10).attach(self.table)

        self.empty = EmptyState(
            "还没有热榜数据",
            hint="点击右上角「刷新热榜」从微博、抖音、知乎、B 站、百度、头条拉取最新热点",
            icon="trending-up",
            theme=self.ctx.theme,
            action=("立即刷新", lambda: self.reload(force=True)),
        )
        table_card.add(self.empty)
        self.empty.setVisible(False)

        self.filter_empty = EmptyState(
            "没有符合条件的热点",
            hint="试试减少关键词、换一个垂类，或把平台选全",
            icon="search",
            theme=self.ctx.theme,
            action=("清除筛选", self._clear_filters),
        )
        table_card.add(self.filter_empty)
        self.filter_empty.setVisible(False)

    # ------------------------------------------------------------------
    # 加载
    # ------------------------------------------------------------------
    def on_first_show(self) -> None:
        if getattr(self.ctx, "selftest", False):
            return
        self.reload(force=False)

    def reload(self, *, force: bool = False) -> None:
        if self._worker is not None and self._worker.isRunning():
            self.toast("正在拉取中，请稍候", "info")
            return
        self.refresh_button.setEnabled(False)
        self.refresh_button.setText("刷新中…")
        self.refresh_button.setToolTip("正在并发拉取各平台热榜…")
        # 刷新期间用骨架屏占住列表区域，而不是清空列表造成闪烁
        self.skeleton.start()
        self.ctx.db.log_action("hotsearch", "fetch_start", f"force={force}")

        platforms = self._selected_platforms()

        def job(worker):
            return hotsearch_service.fetch_all(
                platforms,
                force=force,
                on_progress=lambda label, percent: worker.emit_progress(f"{label} 完成", percent),
            )

        self._worker = self.run_task(
            job,
            on_progress=self._on_progress,
            on_result=self._on_loaded,
            on_error=self._on_error,
            on_done=self._on_done,
            name="hotsearch",
        )

    def _on_progress(self, message: str, percent: int) -> None:
        self.set_status_message(f"热榜：{message}（{percent}%）")

    def _on_done(self) -> None:
        self.skeleton.stop()
        self.refresh_button.setEnabled(True)
        self.refresh_button.setText("刷新热榜")
        self.refresh_button.setToolTip("")
        self._worker = None

    def _on_error(self, message: str) -> None:
        self.skeleton.stop()
        self.banner.show_message(f"热榜拉取失败：{message}", "error")
        self.toast("热榜拉取失败", "error")

    def _on_loaded(self, results: dict[str, PlatformResult]) -> None:
        self._results = results or {}
        ok = [r for r in self._results.values() if r.ok]
        failed = [r for r in self._results.values() if not r.ok]
        stale = [r for r in ok if r.stale]
        cached = [r for r in ok if r.from_cache]

        parts = [f"成功 {len(ok)} 个平台、共 {sum(len(r.items) for r in ok)} 条热点"]
        if cached:
            parts.append(f"其中 {len(cached)} 个来自本地缓存")
        if stale:
            parts.append("注意：" + "、".join(r.label for r in stale) + " 使用历史缓存")
        if failed:
            parts.append("失败：" + "、".join(f"{r.label}（{r.error}）" for r in failed))
        level = "warn" if (failed or stale) else "success"

        # 状态灯摘要：一眼看清「几个平台正常 / 几个降级」
        # 普通缓存命中属于正常工作方式，只有「过期降级」才标黄
        if failed:
            summary = f"{len(ok)} 平台正常 · {len(failed)} 失败"
        elif stale:
            summary = f"{len(ok)} 平台 · {len(stale)} 缓存"
        else:
            summary = f"{len(ok)} 平台正常"
        self.banner.show_message("；".join(parts), level, summary=summary)

        self._refresh_categories()
        self._apply_filters()
        self.ctx.notify_data_changed("hot")

    def _refresh_categories(self) -> None:
        """按当前平台选择刷新垂类选项（数量取自未筛选的全量数据）。"""
        counts = hotsearch_service.category_counts(
            self._results, platforms=self._selected_platforms()
        )
        self.category_picker.set_categories(counts)

    # ------------------------------------------------------------------
    # 筛选与渲染
    # ------------------------------------------------------------------
    def _clear_filters(self) -> None:
        self.keyword_edit.clear()
        self.category_picker.clear_selection()
        self._apply_filters()

    def _apply_filters(self) -> None:
        keyword = self.keyword_edit.text().strip()
        categories = self.category_picker.selected()
        items = hotsearch_service.filter_items(
            self._results,
            keyword=keyword,
            categories=categories,
            platforms=self._selected_platforms(),
        )
        self._filtered = items
        self._render(items, keyword=keyword, categories=categories)

    def _render(
        self, items: list[HotItem], *, keyword: str = "", categories: list[str] | None = None
    ) -> None:
        total = sum(len(r.items) for r in self._results.values())
        filtered = bool(keyword or categories)
        if filtered:
            self.count_label.setText(f"筛选出 {len(items)} / {total} 条")
        else:
            self.count_label.setText(f"共 {total} 条热点")

        has_any = bool(self._results)
        self.table.setVisible(bool(items))
        self.empty.setVisible(not items and not has_any)
        self.filter_empty.setVisible(not items and has_any)
        if not items:
            self._set_copy_enabled(False)
            return

        self.table.setRowCount(len(items))
        for row, item in enumerate(items):
            self._fill_row(row, item)
        # 条形图按当前列表的峰值归一化，列表一变就要重算
        self.heat_delegate.set_peak(
            max((parse_heat(item.heat) for item in items), default=0.0)
        )
        self._set_copy_enabled(False)

    def _set_copy_enabled(self, enabled: bool) -> None:
        self.copy_button.setEnabled(enabled)

    def _fill_row(self, row: int, item: HotItem) -> None:
        rank = QTableWidgetItem(str(item.rank or row + 1))
        rank.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.table.setItem(row, COL_RANK, rank)

        title = QTableWidgetItem(item.title)
        title.setData(PLATFORM_ROLE, item.platform)
        title.setToolTip(
            f"{item.title}\n\n平台：{PLATFORM_LABELS.get(item.platform, item.platform)}"
            f"\n垂类：{item.category or '综合'}\n热度：{item.heat or '—'}\n{item.url or '（该频道不提供原链接）'}"
        )
        self.table.setItem(row, COL_TITLE, title)

        heat = QTableWidgetItem(item.heat or "—")
        heat.setData(HEAT_ROLE, parse_heat(item.heat))
        heat.setToolTip("热度为各平台口径，仅用于同一列表内的相对比较")
        self.table.setItem(row, COL_HEAT, heat)

        category = QTableWidgetItem(item.category or "综合")
        category.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.table.setItem(row, COL_CATEGORY, category)

        action = QTableWidgetItem("")
        action.setData(Qt.ItemDataRole.UserRole, item.url or "")
        self.table.setItem(row, COL_ACTIONS, action)

    def _selected_item(self) -> HotItem | None:
        row = self.table.currentRow()
        if row < 0 or row >= len(self._filtered):
            return None
        return self._filtered[row]

    def _on_selection_changed(self) -> None:
        self._set_copy_enabled(self._selected_item() is not None)

    # ------------------------------------------------------------------
    # 操作
    # ------------------------------------------------------------------
    def _send_row(self, row: int) -> None:
        if 0 <= row < len(self._filtered):
            self.table.selectRow(row)
            self._send_item(self._filtered[row])

    def _open_row(self, row: int) -> None:
        if 0 <= row < len(self._filtered):
            item = self._filtered[row]
            self.table.selectRow(row)
            if item.url:
                _open_url(item.url)

    def _send_selected(self) -> None:
        item = self._selected_item()
        if item is None:
            self.toast("请先选择一条热点", "warn")
            return
        self._send_item(item)

    def _send_item(self, item: HotItem) -> None:
        seed = hotsearch_service.to_draft_seed(item)
        window = self.window()
        if hasattr(window, "open_page_with"):
            window.open_page_with("create", seed=seed)  # type: ignore[attr-defined]
        self.toast(f"已送入创作：{item.title[:18]}", "success")

    def _copy_selected(self) -> None:
        item = self._selected_item()
        if item is None:
            return
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText(item.title)
        self.toast("标题已复制到剪贴板", "success")

    def _show_context_menu(self, pos) -> None:
        item = self._selected_item()
        if item is None:
            return
        menu = QMenu(self)
        menu.addAction("送入创作", self._send_selected)
        menu.addAction("复制标题", self._copy_selected)
        if item.url:
            menu.addAction("打开原链接", lambda: _open_url(item.url))
        menu.exec(self.table.viewport().mapToGlobal(pos))

    # ------------------------------------------------------------------
    def apply_theme(self, theme: str) -> None:
        for widget in (self.empty, self.filter_empty):
            if hasattr(widget, "apply_theme"):
                widget.apply_theme(theme)
        self.table.viewport().update()


def _open_url(url: str) -> None:
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices

    QDesktopServices.openUrl(QUrl(url))
