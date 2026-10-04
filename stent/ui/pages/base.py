"""页面基类：统一主题响应、后台任务管理与懒加载钩子。"""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QVBoxLayout, QWidget

from ..context import AppContext
from ..workers import WorkerHost


class BasePage(QWidget, WorkerHost):
    """所有页面的公共父类。

    子类实现 :meth:`build`（构建界面）与可选的 :meth:`apply_theme`、
    :meth:`on_show`（切换到本页时触发，用于懒刷新）。
    """

    #: 页面 key（与主窗口导航一致）
    key: str = ""
    #: 页面标题
    title: str = ""
    #: 图标名
    icon: str = ""

    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._init_workers()
        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(24, 20, 24, 24)
        self._root.setSpacing(16)
        self._loaded = False
        try:
            self.build()
        except Exception:  # noqa: BLE001 - 单页构建失败不应拖垮整个应用
            import logging

            logging.getLogger(__name__).exception("页面 %s 构建失败", self.key)
            from ..components import StatusBanner

            banner = StatusBanner(self)
            banner.show_message(f"页面初始化失败，请查看日志（{self.key}）", "error")
            self._root.addWidget(banner)
        ctx.theme_changed.connect(self._on_theme_changed)

    # -- 生命周期 --------------------------------------------------------
    def build(self) -> None:
        """构建界面，子类实现。"""

    def on_show(self) -> None:
        """页面被切换到前台时调用（首次与每次都会触发）。"""
        if not self._loaded:
            self._loaded = True
            self.on_first_show()

    def on_first_show(self) -> None:
        """首次显示时的懒加载逻辑（如加载列表数据）。"""

    def _on_theme_changed(self, theme: str) -> None:
        try:
            self.apply_theme(theme)
        except Exception:  # pragma: no cover
            import logging

            logging.getLogger(__name__).exception("页面 %s 主题切换失败", self.key)

    def apply_theme(self, theme: str) -> None:
        """主题变更时的视觉刷新，子类可重写。"""

    # -- 便捷方法 --------------------------------------------------------
    def add(self, widget: QWidget, stretch: int = 0) -> QWidget:
        self._root.addWidget(widget, stretch)
        return widget

    def add_layout(self, layout: Any, stretch: int = 0) -> Any:
        self._root.addLayout(layout, stretch)
        return layout

    def toast(self, text: str, level: str = "info") -> None:
        self.ctx.toast(text, level)

    def closeEvent(self, event: Any) -> None:  # noqa: N802 - Qt 命名
        self.cancel_all()
        super().closeEvent(event)
