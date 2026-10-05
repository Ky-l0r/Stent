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
        from ..components import StatusLight
        from ..qt_guard import building

        with building(self):
            # 右上角状态指示灯：日常状态只占一枚小胶囊，详情悬停可见
            self.status_light = StatusLight(theme=ctx.theme)
            #: 兼容页面里既有的 self.banner.show_message(...) 调用
            self.banner = self.status_light
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
            self.status_light.apply_theme(theme)
        except Exception:  # pragma: no cover
            pass
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

    def set_status_message(self, text: str) -> None:
        """把进度写到主窗口底部的状态栏。

        放在基类而不是各页面里：页面随时可能被重写，而这个「进度往哪写」的约定
        不该跟着页面一起被删掉（v1.1 打包实测时就是因为热点页重写后漏了它，
        进度回调抛 AttributeError）。
        """
        window = self.window()
        setter = getattr(window, "set_status", None)
        if callable(setter):
            try:
                setter(text)
            except Exception:  # pragma: no cover - 状态栏异常不该影响主流程
                pass

    def closeEvent(self, event: Any) -> None:  # noqa: N802 - Qt 命名
        self.cancel_all()
        super().closeEvent(event)
