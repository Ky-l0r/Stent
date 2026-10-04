"""热点发现页（企划书 3.2 模块一）。

列表展示 + 关键词与垂类筛选 + 一键送入创作。
多平台并发拉取，结果本地缓存 5~10 分钟。
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...config import config_manager
from ...services.hotsearch import (
    DEFAULT_PLATFORMS,
    PLATFORM_LABELS,
    HotItem,
    PlatformResult,
    hotsearch_service,
)
from ..components import (
    Card,
    CardTitle,
    EmptyState,
    FlowRow,
    PageHeader,
    attach_platform_delegate,
    make_button,
    platform_item,
)
from .base import BasePage


class HotSearchPage(BasePage):
    key = "hotsearch"
    title = "热点发现"
    icon = "trending-up"

    def build(self) -> None:
        self._results: dict[str, PlatformResult] = {}
        self._filtered: list[HotItem] = []
        self._platform_chips: dict[str, QPushButton] = {}
        self._worker = None

        header = PageHeader(
            "热点发现",
            "聚合微博、抖音、知乎、B 站、百度、头条热榜，双击任意条目即可送入创作",
        )
        # 状态灯放在主操作左侧：日常状态只占一枚小胶囊，页面顶部留给标题与刷新按钮
        header.add_action(self.status_light)
        self.refresh_button = make_button("刷新热榜", icon="refresh", theme=self.ctx.theme, primary=True)
        self.refresh_button.clicked.connect(lambda: self.reload(force=True))
        header.add_action(self.refresh_button)
        self.add(header)

        # ---- 筛选工具条 ----
        toolbar = Card(padding=14, spacing=10)
        self.add(toolbar)
        title = CardTitle("筛选", icon="search", hint="平台、关键词与垂类可组合筛选；筛选在本地即时完成", theme=self.ctx.theme)
        self.toolbar_hint = title.hint_label
        toolbar.add(title)

        self.platform_row = FlowRow()
        toolbar.add(self.platform_row)
        self._build_platform_chips()

        filters = QWidget()
        filter_layout = QHBoxLayout(filters)
        filter_layout.setContentsMargins(0, 0, 0, 0)
        filter_layout.setSpacing(8)

        self.keyword_edit = QLineEdit()
        self.keyword_edit.setPlaceholderText("按关键词过滤标题（即时生效）")
        self.keyword_edit.setClearButtonEnabled(True)
        self.keyword_edit.textChanged.connect(self._apply_filters)
        filter_layout.addWidget(self.keyword_edit, 2)

        self.category_combo = QComboBox()
        self.category_combo.addItem("全部垂类", "")
        self.category_combo.currentIndexChanged.connect(self._apply_filters)
        filter_layout.addWidget(self.category_combo, 1)

        self.count_label = QLabel("尚未加载")
        self.count_label.setObjectName("CardHint")
        filter_layout.addWidget(self.count_label)

        toolbar.add(filters)

        # ---- 列表 ----
        table_card = Card(padding=12, spacing=8)
        self.add(table_card, 1)
        list_title = CardTitle("热榜列表", icon="trending-up", theme=self.ctx.theme)
        self.send_button = make_button("送入创作", icon="pen-line", theme=self.ctx.theme)
        self.send_button.clicked.connect(self._send_selected)
        self.send_button.setEnabled(False)
        list_title.add_action(self.send_button)
        self.copy_button = make_button("复制标题", icon="copy", theme=self.ctx.theme, ghost=True)
        self.copy_button.clicked.connect(self._copy_selected)
        self.copy_button.setEnabled(False)
        list_title.add_action(self.copy_button)
        table_card.add(list_title)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["平台", "排名", "标题", "热度", "垂类", "来源"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(False)
        self.table.setShowGrid(False)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, 78)
        self.table.setColumnWidth(1, 56)
        self.table.setColumnWidth(3, 92)
        self.table.setColumnWidth(4, 72)
        self.table.setColumnWidth(5, 84)
        self.table.doubleClicked.connect(lambda _: self._send_selected())
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        # 平台列用品牌色圆点标注来源，便于快速识别
        attach_platform_delegate(self.table, 0)
        table_card.add(self.table, 1)

        self.empty = EmptyState(
            "还没有热榜数据",
            hint="点击右上角「刷新热榜」从微博、抖音、知乎、B 站、百度、头条拉取最新热点",
            icon="trending-up",
            theme=self.ctx.theme,
            action=("立即刷新", lambda: self.reload(force=True)),
        )
        table_card.add(self.empty)
        self.empty.setVisible(False)
        self.table.setVisible(True)

    # ------------------------------------------------------------------
    def _build_platform_chips(self) -> None:
        enabled = set(getattr(config_manager.config, "hotlist_platforms", None) or DEFAULT_PLATFORMS)
        chips: list[QWidget] = []
        for key in DEFAULT_PLATFORMS:
            if key not in PLATFORM_LABELS:
                continue
            chip = QPushButton(PLATFORM_LABELS[key])
            chip.setCheckable(True)
            chip.setChecked(key in enabled)
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.setStyleSheet(_chip_qss(self.ctx.theme, chip.isChecked()))
            chip.toggled.connect(lambda checked, k=key, c=chip: self._on_chip_toggled(k, checked, c))
            self._platform_chips[key] = chip
            chips.append(chip)
        self.platform_row.set_items(chips, per_row=7)

    def _on_chip_toggled(self, key: str, checked: bool, chip: QPushButton) -> None:
        chip.setStyleSheet(_chip_qss(self.ctx.theme, checked))
        config_manager.update(hotlist_platforms=list(self._selected_platforms()))
        self._apply_filters()

    def _selected_platforms(self) -> list[str]:
        chosen = [k for k, c in self._platform_chips.items() if c.isChecked()]
        return chosen or list(DEFAULT_PLATFORMS)

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
        self.refresh_button.setText("拉取中…")
        self.banner.show_message("正在并发拉取各平台热榜…", "info", closable=False)
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
        self.refresh_button.setEnabled(True)
        self.refresh_button.setText("刷新热榜")
        self._worker = None

    def _on_error(self, message: str) -> None:
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

        self._refresh_category_combo()
        self._apply_filters()
        self.ctx.notify_data_changed("hot")

    def _refresh_category_combo(self) -> None:
        current = self.category_combo.currentData()
        self.category_combo.blockSignals(True)
        self.category_combo.clear()
        self.category_combo.addItem("全部垂类", "")
        for name, count in hotsearch_service.categories(self._results):
            self.category_combo.addItem(f"{name}（{count}）", name)
        index = self.category_combo.findData(current)
        self.category_combo.setCurrentIndex(max(0, index))
        self.category_combo.blockSignals(False)

    # ------------------------------------------------------------------
    # 筛选与渲染
    # ------------------------------------------------------------------
    def _apply_filters(self) -> None:
        keyword = self.keyword_edit.text().strip()
        category = self.category_combo.currentData() or ""
        items = hotsearch_service.filter_items(
            self._results,
            keyword=keyword,
            category=category,
            platforms=self._selected_platforms(),
        )
        self._filtered = items
        self._render(items, keyword=keyword, category=category)

    def _render(self, items: list[HotItem], *, keyword: str = "", category: str = "") -> None:
        total = sum(len(r.items) for r in self._results.values())
        if keyword or category:
            self.count_label.setText(f"筛选出 {len(items)} / {total} 条")
        else:
            self.count_label.setText(f"共 {total} 条热点")

        has_any = bool(self._results)
        self.table.setVisible(bool(items))
        self.empty.setVisible(not items and not has_any)
        if not items and has_any:
            self.table.setVisible(True)

        self.table.setRowCount(len(items))
        for row, item in enumerate(items):
            self._fill_row(row, item)
        self.send_button.setEnabled(False)
        self.copy_button.setEnabled(False)

    def _fill_row(self, row: int, item: HotItem) -> None:
        source = self._results.get(item.platform)
        cells = [
            PLATFORM_LABELS.get(item.platform, item.platform),
            str(item.rank or row + 1),
            item.title,
            item.heat or "—",
            item.category or "综合",
            source.source if source else "—",
        ]
        for column, text in enumerate(cells):
            if column == 0:
                cell = platform_item(text, item.platform)
            else:
                cell = QTableWidgetItem(text)
            if column == 1:
                cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if column == 2:
                cell.setToolTip(f"{item.title}\n\n垂类：{item.category}\n热度：{item.heat or '—'}\n{item.url}")
            self.table.setItem(row, column, cell)

    def _selected_item(self) -> HotItem | None:
        row = self.table.currentRow()
        if row < 0 or row >= len(self._filtered):
            return None
        return self._filtered[row]

    def _on_selection_changed(self) -> None:
        has = self._selected_item() is not None
        self.send_button.setEnabled(has)
        self.copy_button.setEnabled(has)

    # ------------------------------------------------------------------
    # 操作
    # ------------------------------------------------------------------
    def _send_selected(self) -> None:
        item = self._selected_item()
        if item is None:
            self.toast("请先选择一条热点", "warn")
            return
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
        for key, chip in self._platform_chips.items():
            chip.setStyleSheet(_chip_qss(theme, chip.isChecked()))
        if hasattr(self, "empty"):
            self.empty.apply_theme(theme)

    def set_status_message(self, text: str) -> None:
        window = self.window()
        if hasattr(window, "set_status"):
            window.set_status(text)  # type: ignore[attr-defined]


def _chip_qss(theme: str, checked: bool) -> str:
    from ..theme import palette

    p = palette(theme)
    if checked:
        return (
            f"QPushButton {{ background: {p.accent_soft}; color: {p.accent}; border: 1px solid {p.accent};"
            f" border-radius: 6px; padding: 4px 12px; font-weight: 600; }}"
        )
    return (
        f"QPushButton {{ background: {p.card}; color: {p.text_sub}; border: 1px solid {p.border_strong};"
        f" border-radius: 6px; padding: 4px 12px; }}"
        f"QPushButton:hover {{ border-color: {p.accent}; color: {p.text}; }}"
    )


def _open_url(url: str) -> None:
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices

    QDesktopServices.openUrl(QUrl(url))
