"""主窗口：顶栏 + 侧栏导航 + 卡化主内容区（企划书 5.2 布局结构）。

页面按需懒加载（企划书 4.3：延迟加载非首屏模块），保证冷启动速度。
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from . import icons
from .components import BusyOverlay, Toast, make_button
from .context import AppContext
from .theme import build_qss, palette
from .workers import WorkerHost

log = logging.getLogger(__name__)

#: (key, 标题, 图标, 页面模块, 类名)
NAV_ITEMS: tuple[tuple[str, str, str, str, str], ...] = (
    ("hotsearch", "热点发现", "trending-up", "hotsearch", "HotSearchPage"),
    ("create", "内容创作", "pen-line", "create", "CreatePage"),
    ("publish", "发布中心", "send", "publish", "PublishPage"),
    ("analytics", "数据分析", "chart", "analytics", "AnalyticsPage"),
    ("profile", "账号画像", "user", "profile", "ProfilePage"),
)


class MainWindow(QMainWindow, WorkerHost):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self._init_workers()
        self._pages: dict[str, QWidget] = {}
        self._nav_buttons: dict[str, QPushButton] = {}
        self._current = ""

        self.setWindowTitle(f"Stent · 社媒内容智能体 v{__version__}")
        self.setMinimumSize(1120, 720)
        self.resize(1380, 880)

        self._build_ui()
        self._wire_context()
        self.apply_theme(ctx.theme)
        self.navigate("hotsearch")

    # ------------------------------------------------------------------
    # 构建
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_topbar())

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self._build_sidebar())

        self.stack = QStackedWidget()
        self.stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        body.addWidget(self.stack, 1)
        root.addLayout(body, 1)

        self.setStatusBar(QStatusBar())
        self._status_label = QLabel("就绪")
        self.statusBar().addWidget(self._status_label)
        self._llm_status = QLabel()
        self.statusBar().addPermanentWidget(self._llm_status)

        self.toast = Toast(central)
        self.busy_overlay = BusyOverlay(central)
        self._refresh_llm_status()

    def _build_topbar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("TopBar")
        bar.setFixedHeight(58)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(18, 0, 18, 0)
        layout.setSpacing(12)

        self.logo_label = QLabel()
        self.logo_label.setFixedSize(26, 26)
        layout.addWidget(self.logo_label)

        brand_box = QVBoxLayout()
        brand_box.setContentsMargins(0, 0, 0, 0)
        brand_box.setSpacing(0)
        name = QLabel("Stent")
        name.setObjectName("BrandName")
        sub = QLabel("发现热点 · 创作文案 · 一键发布 · 数据分析")
        sub.setObjectName("BrandSub")
        brand_box.addWidget(name)
        brand_box.addWidget(sub)
        layout.addLayout(brand_box)

        layout.addStretch(1)

        self.profile_button = make_button("账号画像未设置", icon="user", theme=self.ctx.theme, ghost=True)
        self.profile_button.clicked.connect(lambda: self.navigate("profile"))
        layout.addWidget(self.profile_button)

        self.theme_button = make_button("", icon="moon", theme=self.ctx.theme, ghost=True, tooltip="切换深浅色")
        self.theme_button.setFixedWidth(40)
        self.theme_button.clicked.connect(self.ctx.toggle_theme)
        layout.addWidget(self.theme_button)

        self.settings_button = make_button("设置", icon="settings", theme=self.ctx.theme, ghost=True)
        self.settings_button.clicked.connect(self.open_settings)
        layout.addWidget(self.settings_button)

        return bar

    def _build_sidebar(self) -> QWidget:
        side = QFrame()
        side.setObjectName("Sidebar")
        side.setFixedWidth(206)
        layout = QVBoxLayout(side)
        layout.setContentsMargins(12, 16, 12, 14)
        layout.setSpacing(6)

        for key, title, icon_name, _module, _cls in NAV_ITEMS:
            button = QPushButton(f"  {title}")
            button.setObjectName("NavButton")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setIconSize(QSize(17, 17))
            button.setProperty("nav_icon", icon_name)
            button.clicked.connect(lambda _=False, k=key: self.navigate(k))
            layout.addWidget(button)
            self._nav_buttons[key] = button

        layout.addStretch(1)

        hint = QLabel("v1 · 只做 4 个模块\n热点 → 创作 → 发布 → 数据")
        hint.setObjectName("Faint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        return side

    # ------------------------------------------------------------------
    # 上下文接线
    # ------------------------------------------------------------------
    def _wire_context(self) -> None:
        self.ctx.navigate_requested.connect(self.navigate)
        self.ctx.theme_changed.connect(self.apply_theme)
        self.ctx.toast_requested.connect(self.show_toast)
        self.ctx.config_changed.connect(self._refresh_llm_status)
        self.ctx.data_changed.connect(self._on_data_changed)

    def _on_data_changed(self, domain: str) -> None:
        self._refresh_llm_status()
        self._maybe_refresh_profile_badge()

    # ------------------------------------------------------------------
    # 导航与页面
    # ------------------------------------------------------------------
    def navigate(self, key: str) -> None:
        if key == "settings":
            self.open_settings()
            return
        if key not in dict((i[0], i) for i in NAV_ITEMS):
            return
        page = self._page_for(key)
        if page is None:
            return
        self.stack.setCurrentWidget(page)
        self._current = key
        for nav_key, button in self._nav_buttons.items():
            button.setChecked(nav_key == key)
        try:
            page.on_show()  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            log.exception("页面 on_show 失败：%s", key)

    def _page_for(self, key: str) -> QWidget | None:
        if key in self._pages:
            return self._pages[key]
        spec = next((item for item in NAV_ITEMS if item[0] == key), None)
        if spec is None:
            return None
        _key, _title, _icon, module_name, class_name = spec
        try:
            import importlib

            module = importlib.import_module(f".pages.{module_name}", __package__)
            cls = getattr(module, class_name)
            page = cls(self.ctx)
        except Exception:  # noqa: BLE001 - 单页失败降级为提示页
            log.exception("页面加载失败：%s", key)
            page = self._error_page(spec[1])
        self._pages[key] = page
        self.stack.addWidget(page)
        if hasattr(page, "apply_theme"):
            page.apply_theme(self.ctx.theme)  # type: ignore[attr-defined]
        return page

    def _error_page(self, title: str) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label = QLabel(f"「{title}」页面加载失败。\n请查看日志：%s" % _log_hint())
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setObjectName("CardHint")
        layout.addWidget(label)
        return widget

    def open_settings(self) -> None:
        key = "settings"
        if key not in self._pages:
            from .pages.settings import SettingsPage

            page = SettingsPage(self.ctx)
            self._pages[key] = page
            self.stack.addWidget(page)
        page = self._pages[key]
        self.stack.setCurrentWidget(page)
        for button in self._nav_buttons.values():
            button.setChecked(False)
        self._current = key
        if hasattr(page, "on_show"):
            page.on_show()  # type: ignore[attr-defined]

    def open_page_with(self, key: str, **kwargs: object) -> None:
        """跳转到某页并传入参数（如「送入创作」）。"""
        page = self._page_for(key)
        self.navigate(key)
        handler = getattr(page, "receive", None) if page else None
        if callable(handler):
            try:
                handler(**kwargs)
            except Exception:  # noqa: BLE001
                log.exception("页面 %s 接收参数失败", key)

    # ------------------------------------------------------------------
    # 主题
    # ------------------------------------------------------------------
    def apply_theme(self, theme: str) -> None:
        self.setStyleSheet(build_qss(theme))
        p = palette(theme)
        icons.clear_cache()
        self.logo_label.setPixmap(icons.pixmap("sparkles", p.accent, 26, 1.9))
        for key, button in self._nav_buttons.items():
            icon_name = button.property("nav_icon") or "info"
            color = p.accent if key == self._current else p.text_sub
            button.setIcon(icons.icon(icon_name, color, 17))
        self.theme_button.setIcon(icons.icon("sun" if theme == "dark" else "moon", p.text_sub, 16))
        self.settings_button.setIcon(icons.icon("settings", p.text_sub, 16))
        self.profile_button.setIcon(icons.icon("user", p.text_sub, 16))
        for page in self._pages.values():
            if hasattr(page, "apply_theme"):
                try:
                    page.apply_theme(theme)  # type: ignore[attr-defined]
                except Exception:  # noqa: BLE001
                    log.exception("页面主题刷新失败")
        self._refresh_profile_badge()
        self._refresh_llm_status()

    # ------------------------------------------------------------------
    # 状态与提示
    # ------------------------------------------------------------------
    def _refresh_profile_badge(self) -> None:
        try:
            from ..services.profile import profile_service

            percent = profile_service.completeness()
            self.profile_button.setText("账号画像已就绪" if percent >= 60 else f"账号画像 {percent}%")
            self.profile_button.setToolTip(profile_service.to_prompt() or "尚未填写账号画像")
        except Exception:  # noqa: BLE001
            self.profile_button.setText("账号画像")

    def _maybe_refresh_profile_badge(self) -> None:
        self._refresh_profile_badge()

    def _refresh_llm_status(self) -> None:
        cm = self.ctx.config_manager
        if cm.ready():
            llm = cm.config.llm
            self._llm_status.setText(f"模型：{llm.model}")
            self._llm_status.setStyleSheet(f"color: {palette(self.ctx.theme).success};")
        else:
            self._llm_status.setText("未配置模型 · 点右上角「设置」")
            self._llm_status.setStyleSheet(f"color: {palette(self.ctx.theme).warning};")

    def set_status(self, text: str) -> None:
        self._status_label.setText(text)

    def show_toast(self, text: str, level: str = "info") -> None:
        self.toast.show_message(text, level)

    def show_busy(self, text: str = "处理中…") -> None:
        self.busy_overlay.show_text(text)

    def hide_busy(self) -> None:
        self.busy_overlay.setVisible(False)

    # ------------------------------------------------------------------
    # 收尾
    # ------------------------------------------------------------------
    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt 命名
        try:
            self.cancel_all()
            for page in self._pages.values():
                if hasattr(page, "cancel_all"):
                    page.cancel_all()  # type: ignore[attr-defined]
            self.wait_all(5000)
        except Exception:  # pragma: no cover
            log.exception("关闭时清理后台任务失败")
        try:
            self.ctx.db.close()
        except Exception:  # pragma: no cover
            pass
        super().closeEvent(event)


def _log_hint() -> str:
    from .. import paths

    return str(paths.log_path())
