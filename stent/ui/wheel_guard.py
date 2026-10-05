"""滚轮守卫：避免鼠标悬停在数值 / 下拉控件上滚动时误改参数。

问题
----
``QSpinBox`` / ``QDoubleSpinBox`` / ``QComboBox`` / ``QSlider`` 默认响应滚轮，
用户本意是滚动页面，却把参数改了。

踩过的坑
--------
最初用 ``QApplication.installEventFilter()`` 做全局拦截——**实测无效**。
事件过滤器只作用于「安装它的那个对象」所接收的事件，装在 ``QApplication`` 上
并不能拦下发往 ``QSpinBox`` 的滚轮事件（用 Win32 ``mouse_event`` 注入真实滚轮
验证：四个控件全部被改动）。

做法
----
重写 ``QApplication.notify()``——它是所有事件分发的必经入口，可靠且开销极小。
拦下滚轮后**转交给最近的滚动容器**，这样「数值不变，页面照常滚动」。
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QSlider,
    QWidget,
)

log = logging.getLogger(__name__)

#: 需要守卫的控件类型（它们的默认滚轮行为会改数据）
GUARDED_TYPES: tuple[type, ...] = (QAbstractSpinBox, QComboBox, QSlider)


def find_scroll_area(widget: QWidget) -> QAbstractScrollArea | None:
    """向上找到最近的滚动容器，用于把滚轮事件转交出去。"""
    parent = widget.parentWidget()
    while parent is not None:
        if isinstance(parent, QAbstractScrollArea):
            return parent
        parent = parent.parentWidget()
    return None


def guarded_target(widget: QWidget) -> QWidget | None:
    """找到该控件自身或其祖先中的受守卫控件。

    必须检查祖先：``QAbstractSpinBox`` 内部是 ``QLineEdit``，鼠标悬停时滚轮
    事件的实际接收者是那个内部行编辑框，而不是 spinbox 本身——只判断接收者
    类型会漏掉全部数值框。

    同时限定在**同一窗口**内，避免误伤 ``QComboBox`` 展开后的下拉列表
    （它是独立弹出窗口，用户需要能用滚轮浏览候选）。
    """
    window = widget.window()
    current: QWidget | None = widget
    while current is not None:
        if isinstance(current, GUARDED_TYPES) and current.window() is window:
            return current
        current = current.parentWidget()
    return None


class WheelSafeApplication(QApplication):
    """把「悬停滚动不误改数值」做进事件分发入口。"""

    def notify(self, receiver: QObject, event: QEvent) -> bool:  # noqa: D102 - Qt 虚函数
        if event.type() == QEvent.Type.Wheel and isinstance(receiver, QWidget):
            target = guarded_target(receiver)
            if target is not None:
                # 数值 / 下拉一律不响应滚轮；把滚动意图交给所在页面，
                # 避免"鼠标停在输入框上时页面滚不动"的别扭体验。
                scroll_area = find_scroll_area(target)
                if scroll_area is not None:
                    try:
                        QApplication.sendEvent(scroll_area.viewport(), event)
                    except Exception:  # pragma: no cover - 转交失败不影响主流程
                        log.debug("滚轮事件转交失败", exc_info=True)
                event.ignore()
                return True
        return super().notify(receiver, event)
