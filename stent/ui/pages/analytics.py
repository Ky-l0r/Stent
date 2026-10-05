"""数据分析页（企划书 3.2 模块四）。

展示单篇内容表现（播放/点赞/评论/收藏）、按时间与平台查看趋势，
并把表现良好的内容结构回流到账号画像。
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ...core.db import db
from ...platforms import keys as platform_keys, platform_label
from ...services.analytics import AnalyticsService
from ..components import (
    Card,
    CardTitle,
    EmptyState,
    LineChart,
    PageHeader,
    ScrollArea,
    StatCard,
    attach_platform_delegate,
    attach_row_visuals,
    center_columns,
    fit_table_height,
    make_button,
    platform_item,
)
from ..theme import palette
from .base import BasePage

log = logging.getLogger(__name__)


class _GuideStep(QWidget):
    """引导清单里的一步：左侧状态点 + 主文案 + 小字说明。"""

    def __init__(self, theme: str = "light", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.dot = QLabel()
        self.dot.setFixedSize(9, 9)
        layout.addWidget(self.dot)
        box = QVBoxLayout()
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(1)
        self.label = QLabel()
        self.label.setObjectName("GuideStep")
        box.addWidget(self.label)
        self.hint = QLabel()
        self.hint.setObjectName("GuideHint")
        box.addWidget(self.hint)
        layout.addLayout(box, 1)
        self._done = False

    def set_text(self, label: str, hint: str) -> None:
        self.label.setText(label)
        self.hint.setText(hint)

    def set_done(self, done: bool) -> None:
        self._done = done
        p = palette(self._theme)
        color = p.success if done else p.text_faint
        self.dot.setStyleSheet(
            f"background: {color if done else 'transparent'};"
            f" border: 1px solid {color}; border-radius: 5px;"
        )
        self.label.setObjectName("GuideStepDone" if done else "GuideStep")
        style = self.label.style()
        style.unpolish(self.label)
        style.polish(self.label)

    def apply_theme(self, theme: str) -> None:
        self._theme = theme
        self.set_done(self._done)


class AnalyticsPage(BasePage):
    key = "analytics"
    title = "数据分析"
    icon = "chart"

    #: 内容排行默认展示条数，其余折叠
    RANK_PREVIEW = 5

    def build(self) -> None:
        self._service = AnalyticsService()
        self._worker = None
        self._has_data = False
        self._rank_items: list[dict] = []
        self._rank_expanded = False

        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(16)

        # ---- 零数据引导（任务清单）----
        self.guide_card, self.guide_rows, self.guide_action = self._build_guide()
        layout.addWidget(self.guide_card)
        self.guide_card.setVisible(False)

        # ---- 概览 ----
        stat_row = QHBoxLayout()
        stat_row.setSpacing(12)
        self.stat_cards = {
            "views": StatCard("总播放/曝光"),
            "likes": StatCard("总点赞"),
            "comments": StatCard("总评论"),
            "collects": StatCard("总收藏"),
            "posts": StatCard("发布篇数"),
        }
        for card in self.stat_cards.values():
            stat_row.addWidget(card, 1)
        layout.addLayout(stat_row)

        self.rate_card = Card(padding=14, spacing=4)
        rate_row = QHBoxLayout()
        rate_row.setSpacing(14)
        self.rate_label = QLabel("平均互动率 —")
        self.rate_label.setObjectName("CardTitle")
        rate_row.addWidget(self.rate_label)
        self.benchmark_label = QLabel()
        self.benchmark_label.setObjectName("CardHint")
        rate_row.addWidget(self.benchmark_label)
        rate_row.addStretch(1)
        self.latest_label = QLabel()
        self.latest_label.setObjectName("Faint")
        rate_row.addWidget(self.latest_label)
        self.rate_card.add_layout(rate_row)
        layout.addWidget(self.rate_card)

        # ---- 趋势 ----
        trend_card = Card(padding=14, spacing=10)
        trend_title = CardTitle("趋势", icon="chart", hint="按时间、平台、主题维度查看变化", theme=self.ctx.theme)
        self.trend_card_title = trend_title
        trend_card.add(trend_title)

        controls = QHBoxLayout()
        controls.setSpacing(8)
        self.days_combo = QComboBox()
        for label, days in (("近 7 天", 7), ("近 30 天", 30), ("近 90 天", 90), ("近 180 天", 180)):
            self.days_combo.addItem(label, days)
        self.days_combo.setCurrentIndex(1)
        self.days_combo.currentIndexChanged.connect(lambda _: self.reload())
        controls.addWidget(self.days_combo)

        self.metric_combo = QComboBox()
        for label, key in (
            ("播放", "views"),
            ("点赞", "likes"),
            ("评论", "comments"),
            ("收藏", "collects"),
            ("分享", "shares"),
            ("互动总量", "engagement"),
            ("发布篇数", "posts"),
        ):
            self.metric_combo.addItem(label, key)
        self.metric_combo.currentIndexChanged.connect(lambda _: self.reload())
        controls.addWidget(self.metric_combo)

        self.platform_combo = QComboBox()
        self.platform_combo.addItem("全部平台", "")
        for key in ("xiaohongshu", "douyin", "zhihu", "bilibili"):
            self.platform_combo.addItem(platform_label(key), key)
        self.platform_combo.currentIndexChanged.connect(lambda _: self.reload())
        controls.addWidget(self.platform_combo)

        self.cumulative_combo = QComboBox()
        self.cumulative_combo.addItem("累计曲线", True)
        self.cumulative_combo.addItem("单日表现", False)
        self.cumulative_combo.currentIndexChanged.connect(lambda _: self.reload())
        controls.addWidget(self.cumulative_combo)
        controls.addStretch(1)
        trend_card.add_layout(controls)

        self.chart = LineChart(theme=self.ctx.theme)
        trend_card.add(self.chart, 1)
        # 图表空着时给一张占位插画 + 一条去路，而不是一行几乎看不见的「暂无数据」
        self.trend_empty = EmptyState(
            "还没有可绘制的趋势",
            hint="完成发布并同步指标后，这里会按天画出变化曲线",
            icon="chart",
            theme=self.ctx.theme,
            action=("去发布中心", lambda: self._goto("publish")),
        )
        trend_card.add(self.trend_empty)
        self.trend_empty.setVisible(False)
        layout.addWidget(trend_card)

        # ---- 排行 ----
        rank_card = Card(padding=14, spacing=8)
        rank_title = CardTitle("内容排行", icon="trending-up", theme=self.ctx.theme)
        # 日期筛选：只看某个时间段的表现，避免半年前的老爆款一直霸榜
        self.rank_days_combo = QComboBox()
        for label, days in (("近 7 天", 7), ("近 30 天", 30), ("近 90 天", 90), ("近 180 天", 180), ("全部", 3650)):
            self.rank_days_combo.addItem(label, days)
        self.rank_days_combo.setCurrentIndex(1)
        self.rank_days_combo.currentIndexChanged.connect(lambda _: self.reload())
        rank_title.add_action(self.rank_days_combo)

        self.rank_by_combo = QComboBox()
        for label, key in (
            ("互动综合分", "engagement"),
            ("互动率", "engagement_rate"),
            ("播放", "views"),
            ("点赞", "likes"),
            ("评论", "comments"),
            ("收藏", "collects"),
        ):
            self.rank_by_combo.addItem(label, key)
        self.rank_by_combo.currentIndexChanged.connect(lambda _: self.reload())
        rank_title.add_action(self.rank_by_combo)

        self.delete_button = make_button("删除选中", icon="trash", theme=self.ctx.theme, ghost=True)
        self.delete_button.setToolTip("把导入错或不再需要的记录连同它的指标一起删掉")
        self.delete_button.clicked.connect(self.delete_selected)
        self.delete_button.setEnabled(False)
        rank_title.add_action(self.delete_button)
        rank_card.add(rank_title)

        self.rank_table = QTableWidget(0, 8)
        self.rank_table.setHorizontalHeaderLabels(
            ["#", "平台", "标题", "播放", "点赞", "评论", "收藏", "互动率"]
        )
        self.rank_table.verticalHeader().setVisible(False)
        self.rank_table.verticalHeader().setDefaultSectionSize(40)
        self.rank_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.rank_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.rank_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.rank_table.setShowGrid(False)
        self.rank_table.setWordWrap(False)
        self.rank_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.rank_table.horizontalHeader().setHighlightSections(False)
        self.rank_table.setColumnWidth(0, 40)
        self.rank_table.setColumnWidth(1, 78)
        for column in range(3, 8):
            self.rank_table.setColumnWidth(column, 78)
        self.rank_table.setMinimumHeight(180)
        attach_platform_delegate(self.rank_table, 1)
        # 选中样式与热点发现保持一致（行底色加深 + 左侧强调条）
        self._rank_visuals = attach_row_visuals(self.rank_table)
        self.rank_table.itemSelectionChanged.connect(self._on_rank_selection)
        rank_card.add(self.rank_table, 1)

        # 默认只展示前 5 条，其余折叠——列表再长也不挤压下面的区块
        self.rank_more_button = make_button("", theme=self.ctx.theme, ghost=True)
        self.rank_more_button.clicked.connect(self._toggle_rank_all)
        self.rank_more_button.setVisible(False)
        rank_card.add(self.rank_more_button)
        self._rank_expanded = False

        self.rank_empty = EmptyState(
            "还没有可分析的数据",
            hint="先在「发布中心」完成发布并标记为已发布，再回到这里点击「同步指标」",
            icon="chart",
            theme=self.ctx.theme,
            action=("去发布中心", lambda: self._goto("publish")),
        )
        rank_card.add(self.rank_empty)
        self.rank_empty.setVisible(False)
        layout.addWidget(rank_card, 1)

        # ---- 平台分布 ----
        platform_card = Card(padding=14, spacing=8)
        platform_card.add(CardTitle("平台分布", icon="send", theme=self.ctx.theme))
        self.platform_table = QTableWidget(0, 6)
        self.platform_table.setHorizontalHeaderLabels(["平台", "篇数", "平均播放", "平均点赞", "平均收藏", "水平"])
        self.platform_table.verticalHeader().setVisible(False)
        self.platform_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.platform_table.setShowGrid(False)
        self.platform_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.platform_table.setMinimumHeight(132)
        attach_platform_delegate(self.platform_table, 0)
        self._platform_visuals = attach_row_visuals(self.platform_table)
        platform_card.add(self.platform_table, 1)
        layout.addWidget(platform_card)

        # ---- 归因 ----
        attribute_card = Card(padding=14, spacing=8)
        attribute_title = CardTitle(
            "归因回流",
            icon="sparkles",
            hint="用 AI 分析表现最好与最差的内容，把可复用的结构经验写进账号画像的「记忆」",
            theme=self.ctx.theme,
        )
        self.attribute_button = make_button("开始归因", icon="sparkles", theme=self.ctx.theme)
        self.attribute_button.clicked.connect(self.attribute)
        attribute_title.add_action(self.attribute_button)
        attribute_card.add(attribute_title)
        self.attribute_view = QTextEdit()
        self.attribute_view.setReadOnly(True)
        self.attribute_view.setPlaceholderText("归因结果会显示在这里，并自动写入画像记忆")
        self.attribute_view.setMinimumHeight(140)
        attribute_card.add(self.attribute_view, 1)
        layout.addWidget(attribute_card)

        layout.addStretch(1)

        scroll = ScrollArea(inner)
        self.add(scroll, 1)

        # ---- 头部（放在滚动区外，始终可见）----
        header = PageHeader("数据分析", "同步平台指标、查看趋势与排行，并把经验回流到账号画像")
        header.add_action(self.status_light)
        self.sync_button = make_button("同步指标", icon="refresh", theme=self.ctx.theme, primary=True)
        self.sync_button.clicked.connect(lambda: self.sync(force=False))
        header.add_action(self.sync_button)
        # 全量刷新是「数据对不上时的补救手段」，加图标与明确措辞，别再让它隐形
        self.full_sync_button = make_button(
            "强制全量刷新", icon="refresh", theme=self.ctx.theme, ghost=True,
            tooltip="忽略增量间隔，重新拉取全部已发布内容的指标",
        )
        self.full_sync_button.clicked.connect(lambda: self.sync(force=True))
        header.add_action(self.full_sync_button)
        # 有数据时引导卡会收起，所以入口在主按钮旁边也放一个
        self.register_button = make_button("登记已有作品", icon="link", theme=self.ctx.theme, ghost=True)
        self.register_button.setToolTip("从账号导入作品，或粘贴作品链接，把它们纳入指标同步")
        self.register_button.clicked.connect(self.open_register)
        header.add_action(self.register_button)
        self._root.insertWidget(0, header)

    # ------------------------------------------------------------------
    # 零数据引导
    # ------------------------------------------------------------------
    def _build_guide(self) -> tuple[QWidget, list, QPushButton]:
        card = QFrame()
        card.setObjectName("GuideCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        title = QLabel("还没有数据，先走完这三步")
        title.setObjectName("CardTitle")
        layout.addWidget(title)

        rows = []
        for _ in range(3):
            row = _GuideStep(self.ctx.theme)
            layout.addWidget(row)
            rows.append(row)

        row = QHBoxLayout()
        row.setSpacing(8)
        # 已经在平台上发过的作品也能纳入分析：粘贴链接即可，不必经过 Stent 发布
        register = make_button("登记已有作品", icon="link", theme=self.ctx.theme, ghost=True)
        register.setToolTip("从账号导入作品，或粘贴作品链接，把它们纳入指标同步")
        register.clicked.connect(self.open_register)
        row.addWidget(register)
        row.addStretch(1)
        action = make_button("去创作页", icon="pen-line", theme=self.ctx.theme, primary=True)
        action.clicked.connect(self._on_guide_action)
        row.addWidget(action)
        layout.addLayout(row)
        return card, rows, action

    def _on_guide_action(self) -> None:
        has_drafts = bool(db.list_contents(limit=1))
        self._goto("create" if not has_drafts else "publish")

    def _goto(self, key: str) -> None:
        window = self.window()
        if hasattr(window, "navigate"):
            window.navigate(key)  # type: ignore[attr-defined]

    # ------------------------------------------------------------------
    # 登记平台已有作品
    # ------------------------------------------------------------------
    def open_register(self) -> None:
        """把「平台上早就发过、Stent 不知道」的作品登记进来。"""
        dialog = RegisterPostDialog(self.ctx.theme, self, self._service)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        links = dialog.links() if accepted else []
        import_platform = dialog.import_platform() if accepted else ""
        dialog.setParent(None)
        dialog.deleteLater()
        if import_platform:
            self._import_from_account(import_platform)
            return
        if links:
            self._register_links(links)

    # ------------------------------------------------------------------
    def _import_from_account(self, platform: str) -> None:
        """从已登录账号拉取作品列表并自动登记（比逐条粘贴省事）。"""
        if self._worker is not None and self._worker.isRunning():
            self.toast("正在处理中，请稍候", "info")
            return
        label = platform_label(platform)
        self.banner.show_message(
            f"正在从{label}账号读取作品列表（需要已登录，请稍候）…", "info", closable=False
        )

        def job(worker):
            return self._service.discover_account_posts(platform, limit=50)

        self._worker = self.run_task(
            job,
            on_result=self._on_account_imported,
            on_error=lambda msg: self.banner.show_message(f"导入失败：{msg}", "error"),
            on_done=self._on_register_done,
            name="discover-posts",
        )

    def _on_account_imported(self, result: dict) -> None:
        ok = bool(result.get("ok"))
        level = "success" if ok and result.get("added") else "warn"
        self.banner.show_message(
            str(result.get("message") or "导入完成"), level,
            summary=f"导入 {result.get('added', 0)} 篇" if ok else "导入未完成",
        )
        if ok and result.get("added"):
            self.toast(f"已从账号导入 {result['added']} 篇作品", "success")
            self.reload()
            self.sync(force=False)
        else:
            self.toast("没有导入新的作品", "warn")

    def _register_links(self, links: list[str]) -> None:
        if self._worker is not None and self._worker.isRunning():
            self.toast("正在处理中，请稍候", "info")
            return
        self.banner.show_message(f"正在登记 {len(links)} 个作品链接…", "info", closable=False)

        def job(worker):
            return self._service.register_posts(links)

        self._worker = self.run_task(
            job,
            on_result=self._on_registered,
            on_error=lambda msg: self.banner.show_message(f"登记失败：{msg}", "error"),
            on_done=self._on_register_done,
            name="register-posts",
        )

    def _on_registered(self, result: dict) -> None:
        added = int(result.get("added", 0))
        skipped = int(result.get("skipped", 0))
        failed = int(result.get("failed", 0))
        details = result.get("details") or []
        failures = [d for d in details if "已登记" not in str(d.get("message", "")) and d.get("message")]

        if added:
            self.banner.show_message(
                f"已登记 {added} 篇作品"
                + (f"，跳过重复 {skipped} 篇" if skipped else "")
                + (f"，失败 {failed} 篇" if failed else "")
                + "。正在同步这些作品的指标…",
                "success" if not failed else "warn",
                summary=f"已登记 {added} 篇",
            )
            self.toast(f"已登记 {added} 篇作品", "success")
            self.reload()
            self.sync(force=False)
        else:
            message = "；".join(str(d.get("message")) for d in failures[:3]) or "没有可登记的新链接"
            self.banner.show_message(message, "warn", summary="没有新增作品")
            self.toast("没有新增作品", "warn")

    def _on_register_done(self) -> None:
        self._worker = None

    # ------------------------------------------------------------------
    def on_first_show(self) -> None:
        self.reload()

    def reload(self) -> None:
        days = self.days_combo.currentData() or 30
        platform = self.platform_combo.currentData() or None
        metric = self.metric_combo.currentData() or "views"
        cumulative = bool(self.cumulative_combo.currentData())
        rank_days = self.rank_days_combo.currentData() or 30
        try:
            overview = self._service.overview(days=days, platform=platform)
            trend = self._service.trend(days=days, platform=platform, metric=metric, cumulative=cumulative)
            # 排行有自己的日期筛选，取足够多条以便「展开全部」
            ranked = self._service.rank_contents(
                days=rank_days, by=self.rank_by_combo.currentData() or "engagement", limit=200
            )
            breakdown = self._service.platform_breakdown(days=days)
        except Exception as exc:  # noqa: BLE001
            log.exception("数据分析加载失败")
            self.banner.show_message(f"数据加载失败：{exc}", "error")
            return

        self._render_overview(overview)
        self._render_trend(trend, metric)
        self._render_rank(ranked)
        self._render_breakdown(breakdown)

    def _render_overview(self, overview: dict) -> None:
        totals = overview.get("totals") or {}
        post_count = int(overview.get("post_count", 0) or 0)
        # 一条数据都没有时不要甩五个「0」给用户：那看起来像系统坏了。
        # 用「--」+ 一句引导，把「还没开始」和「真的是 0」区分开。
        has_data = post_count > 0 or any(
            float(totals.get(k) or 0) for k in ("views", "likes", "comments", "collects")
        )
        self._has_data = has_data
        if has_data:
            self.stat_cards["views"].set_value(_short(totals.get("views", 0)))
            self.stat_cards["likes"].set_value(_short(totals.get("likes", 0)))
            self.stat_cards["comments"].set_value(_short(totals.get("comments", 0)))
            self.stat_cards["collects"].set_value(_short(totals.get("collects", 0)))
            self.stat_cards["posts"].set_value(str(post_count))
        else:
            for card in self.stat_cards.values():
                card.set_empty()

        rate = overview.get("aggregate_engagement_rate")
        avg = overview.get("avg_engagement_rate")
        self.rate_label.setText(
            f"平均互动率 {_pct(avg)}　·　加权互动率 {_pct(rate)}" if has_data else "平均互动率 —"
        )
        benchmark = overview.get("benchmark") or {}
        rating = benchmark.get("rating") or "—"
        ranges = benchmark.get("engagement_ranges") or []
        range_text = "~".join(f"{r:g}%" for r in ranges) if ranges else "—"
        self.benchmark_label.setText(
            f"平台水平：{rating}（参考区间 {range_text}）" if has_data else "发布并同步后，这里会显示你的平台水平"
        )
        self.latest_label.setText(f"数据截至：{overview.get('latest_captured_at') or '暂无'}")

        self._refresh_guide(overview)
        self._set_filters_enabled(has_data)

        warnings = overview.get("warnings") or []
        if not has_data:
            # 没数据属于「还没开始」而不是「出错了」，不要用黄点制造焦虑
            self.banner.clear()
        elif warnings:
            self.banner.show_message(
                "；".join(str(w) for w in warnings), "warn", summary=f"{len(warnings)} 项数据提示"
            )
        else:
            self.banner.clear()

    def _set_filters_enabled(self, enabled: bool) -> None:
        """零数据时把筛选器置灰：能点但点了没反应，比灰着更让人困惑。"""
        for combo in (self.days_combo, self.metric_combo, self.platform_combo, self.cumulative_combo):
            combo.setEnabled(enabled)
        tip = "" if enabled else "同步数据后可用"
        for combo in (self.days_combo, self.metric_combo, self.platform_combo, self.cumulative_combo):
            combo.setToolTip(tip)

    def _refresh_guide(self, overview: dict) -> None:
        """零数据时把页面变成一张「任务清单」，告诉用户下一步该做什么。

        一旦真的有了数据（无论是自己发布还是导入的已有作品），这张卡就该消失——
        它的使命只是「引导第一次跑通」，不是常驻装饰。
        """
        published = len([p for p in db.list_posts(limit=200) if p.get("status") == "published"])
        drafts = len(db.list_contents(limit=1))
        synced = bool(overview.get("latest_captured_at"))
        steps = [
            (drafts > 0, "创作并保存一篇内容", "也可以直接导入平台上已发布的作品"),
            (published > 0, "发布内容，或导入平台上已有的作品", "去发布中心，或用「登记已有作品」"),
            (synced, "点击右上角「同步指标」拉取平台数据", "同步后这里会出现图表"),
        ]
        for index, (done, label, hint) in enumerate(steps):
            row = self.guide_rows[index]
            row.set_done(done)
            row.set_text(f"{index + 1}. {label}", hint)
        # 有任何真实指标数据就收起引导卡
        self.guide_card.setVisible(not (published and synced))
        self.guide_action.setText("去创作页" if drafts == 0 else "去发布中心")

    def _render_trend(self, trend: list[dict], metric: str) -> None:
        label = self.metric_combo.currentText()
        points = [(str(row.get("date", "")), float(row.get("value") or 0)) for row in trend or []]
        self.chart.apply_theme(self.ctx.theme)
        self.chart.set_data(points, metric_label=label)
        has = bool(points)
        self.chart.setVisible(has)
        self.trend_empty.setVisible(not has)
        if not has:
            self.trend_card_title.set_hint("完成发布并同步指标后，这里会按天画出变化曲线")
        else:
            self.trend_card_title.set_hint(f"共 {len(points)} 个数据点")

    def _render_rank(self, ranked: list[dict]) -> None:
        self._rank_items = list(ranked)
        total = len(ranked)
        # 默认只显示前 5 条：排行榜的价值在前几名，铺满整屏反而挤压其它区块
        visible = total if self._rank_expanded else min(total, self.RANK_PREVIEW)
        rows = ranked[:visible]

        self.rank_table.setRowCount(len(rows))
        for row, item in enumerate(rows):
            platform = item.get("platform", "")
            cells = [
                str(item.get("rank", row + 1)),
                platform_label(platform),
                item.get("title") or item.get("topic") or "（无标题）",
                _views_text(item.get("views"), platform),
                _short(item.get("likes")),
                _short(item.get("comments")),
                _short(item.get("collects")),
                _pct(item.get("engagement_rate")),
            ]
            for column, text in enumerate(cells):
                if column == 1:
                    cell = platform_item(text, platform)
                else:
                    cell = QTableWidgetItem(text)
                if column != 2:
                    cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                cell.setToolTip(
                    "该平台不公开播放量" if column == 3 and text == "—" else text
                )
                self.rank_table.setItem(row, column, cell)
        # 数值列居中，纵向比较时数字才对得齐
        center_columns(self.rank_table, (0, 3, 4, 5, 6, 7))
        # 表格高度贴合内容：展开后格子跟着变高，且不再有「表格内滚 + 页面滚」两层滚动
        fit_table_height(self.rank_table, min_height=180)

        has = bool(rows)
        self.rank_table.setVisible(has)
        self.rank_empty.setVisible(not has)
        hidden = total - visible
        self.rank_more_button.setVisible(total > self.RANK_PREVIEW)
        if self._rank_expanded and total > self.RANK_PREVIEW:
            self.rank_more_button.setText(f"收起，只看前 {self.RANK_PREVIEW} 条")
        elif hidden > 0:
            self.rank_more_button.setText(f"展开其余 {hidden} 条")
        self.delete_button.setEnabled(False)

    def _on_rank_selection(self) -> None:
        self.delete_button.setEnabled(self._selected_rank_post() is not None)

    def _selected_rank_post(self) -> dict | None:
        row = self.rank_table.currentRow()
        items = getattr(self, "_rank_items", [])
        if row < 0 or row >= len(items):
            return None
        post_id = items[row].get("post_id") or items[row].get("id")
        if not post_id:
            return None
        try:
            return db.get_post(int(post_id))
        except Exception:  # noqa: BLE001
            return None

    def _toggle_rank_all(self) -> None:
        self._rank_expanded = not self._rank_expanded
        self._render_rank(getattr(self, "_rank_items", []))

    def delete_selected(self) -> None:
        """删掉选中的记录（导入错的作品需要有地方清理）。"""
        post = self._selected_rank_post()
        if post is None:
            self.toast("请先在排行里选中一条记录", "warn")
            return
        title = post.get("title") or post.get("url") or f"#{post.get('id')}"
        answer = QMessageBox.question(
            self,
            "删除这条记录",
            f"将删除「{title[:40]}」及其全部指标数据，且无法恢复。\n\n"
            "如果它只是导入错了，删除后可以重新登记。确定删除吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        result = self._service.delete_post(int(post["id"]))
        if result.get("ok"):
            self.toast("已删除该记录", "success")
            self.reload()
            self.ctx.notify_data_changed("posts")
        else:
            self.toast(str(result.get("message") or "删除失败"), "error")

    def _render_breakdown(self, breakdown: list[dict]) -> None:
        self.platform_table.setRowCount(len(breakdown))
        for row, item in enumerate(breakdown):
            cells = [
                platform_label(item.get("platform", "")) or item.get("label") or "未知",
                str(item.get("post_count", 0)),
                _short(item.get("avg_views")),
                _short(item.get("avg_likes")),
                _short(item.get("avg_collects")),
                str(item.get("rating") or "—"),
            ]
            for column, text in enumerate(cells):
                if column == 0:
                    cell = platform_item(text, item.get("platform", ""))
                else:
                    cell = QTableWidgetItem(text)
                self.platform_table.setItem(row, column, cell)

    # ------------------------------------------------------------------
    def sync(self, *, force: bool) -> None:
        if self._worker is not None and self._worker.isRunning():
            self.toast("正在同步中…", "info")
            return
        self.sync_button.setEnabled(False)
        self.full_sync_button.setEnabled(False)
        self.banner.show_message("正在从各平台拉取指标（增量更新）…", "info", closable=False)

        def job(worker):
            return self._service.sync_metrics(
                force=force,
                on_progress=lambda message, percent=-1: worker.emit_progress(message, percent),
            )

        self._worker = self.run_task(
            job,
            on_progress=lambda msg, pct: self.banner.show_message(msg, "info", closable=False),
            on_result=self._on_synced,
            on_error=lambda msg: self.banner.show_message(f"同步失败：{msg}", "error"),
            on_done=self._on_sync_done,
            name="sync-metrics",
        )

    def _on_synced(self, result: dict) -> None:
        synced = result.get("synced", 0)
        failed = result.get("failed", 0)
        skipped = result.get("skipped", 0)
        errors = result.get("errors") or []
        level = "success" if synced and not failed else ("warn" if synced or skipped else "warn")
        text = f"同步完成：成功 {synced} 条，失败 {failed} 条，跳过 {skipped} 条"
        if errors:
            first = errors[0]
            detail = first.get("error") if isinstance(first, dict) else str(first)
            text += f"；首个错误：{detail}"
        summary = f"同步完成 · {synced} 条" if not failed else f"{failed} 条未同步"
        self.banner.show_message(text, level, summary=summary)
        self.reload()
        self.ctx.notify_data_changed("metrics")

    def _on_sync_done(self) -> None:
        self.sync_button.setEnabled(True)
        self.full_sync_button.setEnabled(True)
        self._worker = None

    # ------------------------------------------------------------------
    def attribute(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self.toast("正在处理中…", "info")
            return
        self.attribute_button.setEnabled(False)
        self.attribute_button.setText("分析中…")
        self.attribute_view.setPlainText("正在分析表现数据…")
        days = self.days_combo.currentData() or 30

        def job(worker):
            return self._service.attribute(
                days=days,
                on_progress=lambda message, percent=-1: worker.emit_progress(message, percent),
            )

        self._worker = self.run_task(
            job,
            on_progress=lambda msg, pct: self.banner.show_message(msg, "info", closable=False),
            on_result=self._on_attributed,
            on_error=self._on_attribute_error,
            on_done=self._on_attribute_done,
            name="attribute",
        )

    def _on_attributed(self, result: dict) -> None:
        lines: list[str] = []
        if not result.get("ok"):
            lines.append(result.get("message") or "归因未能完成")
        stats = result.get("stats") or {}
        if stats:
            lines.append(
                f"样本 {stats.get('post_count', 0)} 篇　平均互动率 {_pct(stats.get('avg_engagement_rate'))}"
                f"　头部/平均 提升 {stats.get('top_lift') or '—'}x"
            )
        if result.get("insight"):
            lines.append("\n【结论】" + str(result["insight"]))
        for key, title in (("structure_experience", "【可复用结构】"), ("avoid", "【应避免】"), ("summary", "【总结】")):
            value = result.get(key)
            if value:
                if isinstance(value, (list, tuple)):
                    lines.append("\n" + title + "\n" + "\n".join(f"· {v}" for v in value))
                else:
                    lines.append("\n" + title + "\n" + str(value))
        warnings = result.get("warnings") or []
        for warning in warnings:
            lines.append(f"\n⚠ {warning}")
        if result.get("memory_saved"):
            lines.append("\n已写入账号画像「记忆」，下次创作会自动参考。")
        self.attribute_view.setPlainText("\n".join(lines) or "没有可分析的数据")
        self.banner.show_message(
            "归因完成" + ("，经验已回流到画像" if result.get("memory_saved") else ""),
            "success" if result.get("ok") else "warn",
        )
        self.ctx.notify_data_changed("profile")

    def _on_attribute_error(self, message: str) -> None:
        self.attribute_view.setPlainText(f"归因失败：{message}")
        self.banner.show_message(f"归因失败：{message}", "error")

    def _on_attribute_done(self) -> None:
        self.attribute_button.setEnabled(True)
        self.attribute_button.setText("开始归因")
        self._worker = None

    # ------------------------------------------------------------------
    def apply_theme(self, theme: str) -> None:
        if hasattr(self, "chart"):
            self.chart.apply_theme(theme)
        for name in ("rank_empty", "trend_empty"):
            empty = getattr(self, name, None)
            if empty is not None:
                empty.apply_theme(theme)
        for row in getattr(self, "guide_rows", []):
            row.apply_theme(theme)


class RegisterPostDialog(QDialog):
    """把「Stent 不知道的作品」纳入指标同步。

    提供两条路：
    1. **从账号导入**（推荐）：一次拉取账号下全部作品，适合「早就发过一批」的场景；
       需要该平台已登录，且适配器支持列作品。
    2. **粘贴链接**：逐个登记，任何平台都能用，也不需要登录。
    """

    def __init__(
        self,
        theme: str = "light",
        parent: QWidget | None = None,
        service: AnalyticsService | None = None,
    ) -> None:
        super().__init__(parent)
        self._theme = theme
        self._service = service or AnalyticsService()
        self._import_platform = ""
        self.setWindowTitle("登记已有作品")
        self.setMinimumWidth(580)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)

        title = QLabel("登记平台上已发布的作品")
        title.setObjectName("PageTitle")
        layout.addWidget(title)

        intro = QLabel(
            "把你在平台上早就发过的作品纳入指标同步，之后就能在趋势与排行里看到它们。"
        )
        intro.setObjectName("DialogHint")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        # ---- 方式一：从账号导入 ----
        import_box = QFrame()
        import_box.setObjectName("DialogSection")
        import_layout = QVBoxLayout(import_box)
        import_layout.setContentsMargins(14, 12, 14, 12)
        import_layout.setSpacing(8)
        import_title = QLabel("方式一 · 从账号导入（推荐）")
        import_title.setObjectName("DialogSectionTitle")
        import_layout.addWidget(import_title)
        import_hint = QLabel(
            "自动读取账号下的全部作品并登记，适合一次补齐历史内容。"
            "需要该平台已登录（可在「设置 → 平台账号」里登录）。"
        )
        import_hint.setObjectName("DialogHint")
        import_hint.setWordWrap(True)
        import_layout.addWidget(import_hint)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.account_combo = QComboBox()
        for key in self._service.importable_platforms():
            supported = self._service.supports_account_import(key)
            suffix = "" if supported else "（暂不支持自动导入）"
            self.account_combo.addItem(f"{platform_label(key)}{suffix}", key if supported else "")
        if self.account_combo.count() == 0:
            self.account_combo.addItem("暂无可导入的平台", "")
        self.account_combo.currentIndexChanged.connect(lambda _: self._refresh_import_hint())
        row.addWidget(self.account_combo, 1)
        self.import_button = QPushButton("从账号导入")
        self.import_button.setObjectName("Primary")
        self.import_button.clicked.connect(self._accept_import)
        row.addWidget(self.import_button)
        import_layout.addLayout(row)
        self.import_hint_label = QLabel()
        self.import_hint_label.setObjectName("DialogHint")
        self.import_hint_label.setWordWrap(True)
        import_layout.addWidget(self.import_hint_label)
        layout.addWidget(import_box)

        # ---- 方式二：粘贴链接 ----
        paste_box = QFrame()
        paste_box.setObjectName("DialogSection")
        paste_layout = QVBoxLayout(paste_box)
        paste_layout.setContentsMargins(14, 12, 14, 12)
        paste_layout.setSpacing(8)
        paste_title = QLabel("方式二 · 粘贴作品链接")
        paste_title.setObjectName("DialogSectionTitle")
        paste_layout.addWidget(paste_title)
        paste_hint = QLabel(
            "一行一个链接，平台按域名自动识别，不需要登录。"
            "目前支持 B 站 / 抖音 / 知乎；小红书网页端拿不到可靠数据，暂不支持登记。"
        )
        paste_hint.setObjectName("DialogHint")
        paste_hint.setWordWrap(True)
        paste_layout.addWidget(paste_hint)

        self.links_edit = QTextEdit()
        self.links_edit.setPlaceholderText(
            "https://www.bilibili.com/video/BV1xx411c7mD\n"
            "https://www.douyin.com/video/7234567890\n"
            "https://zhuanlan.zhihu.com/p/123456"
        )
        self.links_edit.setMinimumHeight(120)
        self.links_edit.textChanged.connect(self._refresh_preview)
        paste_layout.addWidget(self.links_edit)

        self.preview = QLabel("尚未填写链接")
        self.preview.setObjectName("DialogHint")
        self.preview.setWordWrap(True)
        paste_layout.addWidget(self.preview)

        fallback = QHBoxLayout()
        fallback.setSpacing(8)
        fallback.addWidget(QLabel("识别不出的链接按此平台登记"))
        self.platform_combo = QComboBox()
        self.platform_combo.addItem("不登记（跳过）", "")
        for key in self._service.importable_platforms():
            self.platform_combo.addItem(platform_label(key), key)
        self.platform_combo.currentIndexChanged.connect(lambda _: self._refresh_preview())
        fallback.addWidget(self.platform_combo)
        fallback.addStretch(1)
        paste_layout.addLayout(fallback)
        layout.addWidget(paste_box)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        bottom.addWidget(cancel)
        self.ok_button = QPushButton("登记链接")
        self.ok_button.setObjectName("Primary")
        self.ok_button.setEnabled(False)
        self.ok_button.clicked.connect(self.accept)
        bottom.addWidget(self.ok_button)
        layout.addLayout(bottom)

        self._refresh_import_hint()

    # -- 方式一 ----------------------------------------------------------
    def _refresh_import_hint(self) -> None:
        key = self.account_combo.currentData()
        self.import_button.setEnabled(bool(key))
        if not key:
            self.import_hint_label.setText("该平台暂不支持自动导入，请用下面的粘贴方式。")
            return
        self.import_hint_label.setText(
            f"将读取{platform_label(key)}账号下的已发布作品（最多 50 篇），已登记过的会自动跳过。"
            f"需要先在「设置 → 平台账号」里登录{platform_label(key)}。"
        )

    def _accept_import(self) -> None:
        self._import_platform = str(self.account_combo.currentData() or "")
        self.accept()

    def import_platform(self) -> str:
        return self._import_platform

    # -- 方式二 ----------------------------------------------------------
    def links(self) -> list[str]:
        out: list[str] = []
        for line in self.links_edit.toPlainText().splitlines():
            text = line.strip()
            if text and text not in out:
                out.append(text)
        return out

    def _refresh_preview(self) -> None:
        links = self.links()
        if not links:
            self.preview.setText("尚未填写链接")
            self.ok_button.setEnabled(False)
            return
        from ...platforms import platform_from_url

        allowed = set(self._service.importable_platforms())
        fallback = self.platform_combo.currentData() or ""
        known: list[str] = []
        unknown: list[str] = []
        for link in links:
            key = platform_from_url(link) or fallback
            if key and key in allowed:
                known.append(key)
            else:
                unknown.append(link)
        parts = []
        if known:
            counts: dict[str, int] = {}
            for key in known:
                counts[key] = counts.get(key, 0) + 1
            parts.append(
                "将登记：" + "、".join(f"{platform_label(k)} {v} 篇" for k, v in counts.items())
            )
        if unknown:
            parts.append(f"{len(unknown)} 条无法登记（平台不支持或无法识别），已跳过")
        self.preview.setText("；".join(parts) or "没有可登记的链接")
        self.ok_button.setEnabled(bool(known))


def _short(value) -> str:
    if value is None:
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number >= 100_000_000:
        return f"{number / 100_000_000:.1f}亿"
    if number >= 10_000:
        return f"{number / 10_000:.1f}万"
    if number.is_integer():
        return str(int(number))
    return f"{number:.1f}"


def _views_text(value, platform: str) -> str:
    """播放量文案：平台不公开时显示「—」，而不是容易误读的 0。"""
    if not _platform_exposes_views(platform):
        return "—"
    return _short(value)


def _platform_exposes_views(platform: str) -> bool:
    try:
        from ...platforms import get_adapter

        adapter = get_adapter(platform)
        if adapter is None:
            return True
        return bool(getattr(adapter, "exposes_views", True))
    except Exception:  # noqa: BLE001
        return True


def _pct(value) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):.2f}%"
    except (TypeError, ValueError):
        return str(value)
