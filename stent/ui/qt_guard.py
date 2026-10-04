"""Qt 控件父级守卫 —— 消除「切换页面时小窗口一闪而过」。

问题
----
Qt 中 ``parent=None`` 的控件是**独立顶层窗口**。在「构造控件 → 被 addWidget 到
布局」这段窗口期内，Qt 会把它当作顶层窗口处理并可能创建/显示原生窗口，
表现为界面上「小窗口突然出现又消失」。Stent 的页面里有约两百处
``QLabel("文字")`` 这类未传 parent 的构造，因此在首次进入某个页面时尤为明显。

做法
----
应用启动时给常用控件类加一层轻量包装：控件构造完成时若仍无 parent，
且当前处于某个「构建宿主」作用域内，就立即挂到该宿主下。
控件从诞生起就属于某个窗口，不会出现顶层窗口闪现。

对真正的顶层窗口（``QDialog`` / ``QMainWindow`` / ``QMenu`` 等）不做处理。
可用 :func:`set_enabled` 关闭该行为以便排查问题。
"""

from __future__ import annotations

import contextlib
import logging
from typing import Any

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QGroupBox,
    QLabel,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QTableWidget,
    QTextEdit,
    QTreeWidget,
    QWidget,
)

log = logging.getLogger(__name__)

#: 需要守卫的控件类（均为「内容控件」，正常都应挂在某个容器里）
GUARDED_CLASSES: tuple[type, ...] = (
    QWidget,
    QLabel,
    QFrame,
    QPushButton,
    QCheckBox,
    QRadioButton,
    QLineEdit,
    QTextEdit,
    QPlainTextEdit,
    QComboBox,
    QSpinBox,
    QDoubleSpinBox,
    QListWidget,
    QTableWidget,
    QTreeWidget,
    QProgressBar,
    QSlider,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QGroupBox,
)

#: 构建宿主栈：栈顶即当前正在构建的容器
_HOSTS: list[QWidget] = []
_PATCHED: set[type] = set()
_enabled = True


def set_enabled(value: bool) -> None:
    """临时开关（排查闪烁来源时可用）。"""
    global _enabled
    _enabled = value


def is_enabled() -> bool:
    return _enabled


@contextlib.contextmanager
def building(host: QWidget):
    """声明「host 正在构建」，其内部新建的无 parent 控件会自动挂到 host 下。"""
    _HOSTS.append(host)
    try:
        yield host
    finally:
        try:
            _HOSTS.pop()
        except IndexError:  # pragma: no cover
            pass


def guarded_init(cls: type) -> type:
    """类装饰器：让该类的实例在构建期间成为「构建宿主」。

    这样 ``__init__`` 里创建的裸控件会直接挂到实例自身，而不是成为顶层窗口。
    """
    original = cls.__init__

    def wrapper(self: Any, *args: Any, **kwargs: Any) -> None:
        if not _enabled or not _HOSTS:
            original(self, *args, **kwargs)
            return
        with building(self):
            original(self, *args, **kwargs)

    wrapper.__wrapped__ = original  # type: ignore[attr-defined]
    cls.__init__ = wrapper  # type: ignore[method-assign]
    return cls


def _patch(cls: type) -> None:
    if cls in _PATCHED:
        return
    original = cls.__init__

    def wrapper(self: Any, *args: Any, **kwargs: Any) -> None:
        original(self, *args, **kwargs)
        if not _enabled or not _HOSTS:
            return
        host = _HOSTS[-1]
        # host is self 的情况出现在「宿主类自身的 super().__init__」里，必须跳过
        if host is self:
            return
        try:
            if self.parent() is None:
                self.setParent(host)
        except RuntimeError:  # pragma: no cover - 底层对象已销毁
            pass

    wrapper.__wrapped__ = original  # type: ignore[attr-defined]
    cls.__init__ = wrapper  # type: ignore[method-assign]
    _PATCHED.add(cls)


def install() -> None:
    """安装守卫。需在任何界面控件创建之前调用一次。"""
    for cls in GUARDED_CLASSES:
        try:
            _patch(cls)
        except Exception:  # pragma: no cover - 个别类不可写时跳过
            log.debug("无法为 %s 安装父级守卫", cls.__name__, exc_info=True)


def uninstall() -> None:
    """还原所有被包装的构造函数（测试用）。"""
    for cls in list(_PATCHED):
        original = getattr(cls.__init__, "__wrapped__", None)
        if original is not None:
            cls.__init__ = original  # type: ignore[method-assign]
    _PATCHED.clear()
