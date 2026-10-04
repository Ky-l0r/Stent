"""可复用 UI 组件（卡片、状态条、空态、趋势图等）。

遵循企划书 5.3：卡片圆角 8-12px、按钮 6px、图标线性、留白充足。
"""

from __future__ import annotations

import re
from typing import Any, Callable, Iterable, Sequence

from PySide6.QtCore import QEasingCurve, QPoint, QPointF, QPropertyAnimation, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from . import icons
from .theme import current_theme, level_color, level_tint, palette, platform_color


class Card(QFrame):
    """基础卡片容器：白色圆角 + 细边框。"""

    def __init__(self, parent: QWidget | None = None, *, padding: int = 16, spacing: int = 10) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(padding, padding, padding, padding)
        self._layout.setSpacing(spacing)

    def body(self) -> QVBoxLayout:
        return self._layout

    def add(self, widget: QWidget, stretch: int = 0) -> QWidget:
        self._layout.addWidget(widget, stretch)
        return widget

    def add_layout(self, layout: Any, stretch: int = 0) -> Any:
        self._layout.addLayout(layout, stretch)
        return layout


class CardTitle(QWidget):
    """卡片标题行：图标 + 标题 + 右侧操作区。"""

    def __init__(
        self,
        title: str,
        *,
        icon: str = "",
        hint: str = "",
        theme: str = "light",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._theme = theme
        self._icon_name = icon
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.icon_label = QLabel()
        self.icon_label.setFixedSize(18, 18)
        self.icon_label.setVisible(bool(icon))
        layout.addWidget(self.icon_label)

        text_box = QVBoxLayout()
        text_box.setContentsMargins(0, 0, 0, 0)
        text_box.setSpacing(1)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("CardTitle")
        text_box.addWidget(self.title_label)
        self.hint_label = QLabel(hint)
        self.hint_label.setObjectName("CardHint")
        self.hint_label.setVisible(bool(hint))
        self.hint_label.setWordWrap(True)
        text_box.addWidget(self.hint_label)
        layout.addLayout(text_box, 1)

        self.actions = QHBoxLayout()
        self.actions.setContentsMargins(0, 0, 0, 0)
        self.actions.setSpacing(6)
        layout.addLayout(self.actions)

        self.apply_theme(theme)

    def add_action(self, widget: QWidget) -> QWidget:
        self.actions.addWidget(widget)
        return widget

    def set_hint(self, text: str) -> None:
        self.hint_label.setText(text)
        self.hint_label.setVisible(bool(text))

    def apply_theme(self, theme: str) -> None:
        self._theme = theme
        if self._icon_name:
            self.icon_label.setPixmap(icons.pixmap(self._icon_name, palette(theme).text_sub, 18))


class PageHeader(QWidget):
    """页面标题区：大标题 + 说明 + 右侧操作按钮。"""

    def __init__(self, title: str, subtitle: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        box = QVBoxLayout()
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(2)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("PageTitle")
        box.addWidget(self.title_label)
        self.subtitle_label = QLabel(subtitle)
        self.subtitle_label.setObjectName("PageSubtitle")
        self.subtitle_label.setWordWrap(True)
        self.subtitle_label.setVisible(bool(subtitle))
        box.addWidget(self.subtitle_label)
        layout.addLayout(box, 1)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(8)
        layout.addLayout(self.actions)

    def add_action(self, widget: QWidget) -> QWidget:
        self.actions.addWidget(widget)
        return widget

    def set_subtitle(self, text: str) -> None:
        self.subtitle_label.setText(text)
        self.subtitle_label.setVisible(bool(text))


class StatCard(Card):
    """指标卡：大数字 + 标签 + 可选副标。"""

    def __init__(self, label: str, value: str = "—", *, sub: str = "", accent: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent, padding=14, spacing=2)
        self._accent = accent
        self.value_label = QLabel(value)
        self.value_label.setObjectName("StatValue")
        self.label_label = QLabel(label)
        self.label_label.setObjectName("StatLabel")
        self.sub_label = QLabel(sub)
        self.sub_label.setObjectName("Faint")
        self.sub_label.setVisible(bool(sub))
        self.add(self.value_label)
        self.add(self.label_label)
        self.add(self.sub_label)
        self.apply_theme(current_theme())

    def apply_theme(self, theme: str) -> None:
        if getattr(self, "_accent", False):
            self.value_label.setStyleSheet(
                f"color: {palette(theme).accent}; background: transparent;"
            )

    def set_value(self, value: str, sub: str = "") -> None:
        self.value_label.setText(value)
        if sub:
            self.sub_label.setText(sub)
            self.sub_label.setVisible(True)


class EmptyState(QWidget):
    """空态占位：图标 + 主文案 + 次要说明 + 可选按钮。"""

    def __init__(
        self,
        text: str,
        *,
        hint: str = "",
        icon: str = "info",
        theme: str = "light",
        action: tuple[str, Callable[[], None]] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 32, 24, 32)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.icon_label = QLabel()
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon_name = icon
        self._theme = theme
        layout.addWidget(self.icon_label)

        self.text_label = QLabel(text)
        self.text_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.text_label.setObjectName("CardHint")
        layout.addWidget(self.text_label)

        self.hint_label = QLabel(hint)
        self.hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint_label.setObjectName("Faint")
        self.hint_label.setWordWrap(True)
        self.hint_label.setVisible(bool(hint))
        layout.addWidget(self.hint_label)

        if action:
            button = QPushButton(action[0])
            button.setObjectName("Primary")
            button.clicked.connect(action[1])
            row = QHBoxLayout()
            row.addStretch(1)
            row.addWidget(button)
            row.addStretch(1)
            layout.addLayout(row)
        self.apply_theme(theme)

    def apply_theme(self, theme: str) -> None:
        self._theme = theme
        self.icon_label.setPixmap(icons.pixmap(self._icon_name, palette(theme).text_faint, 32, 1.6))


class StatusBanner(QFrame):
    """内联提示条：info / warn / error / success。

    注意：页面顶部主视觉优先留给标题与主操作，日常状态请用
    :class:`StatusLight`（右上角指示灯）；本组件只用于需要用户立刻读到的
    重要信息（例如页面初始化失败）。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setVisible(False)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(8)
        self.icon_label = QLabel()
        self.icon_label.setFixedSize(16, 16)
        layout.addWidget(self.icon_label)
        self.text_label = QLabel()
        self.text_label.setWordWrap(True)
        layout.addWidget(self.text_label, 1)
        self.close_button = QPushButton("知道了")
        self.close_button.setObjectName("Ghost")
        self.close_button.clicked.connect(lambda: self.setVisible(False))
        layout.addWidget(self.close_button)
        self._level = "info"
        self._last: tuple[str, bool] | None = None

    def show_message(self, text: str, level: str = "info", *, closable: bool = True, **_kw: Any) -> None:
        theme = current_theme()
        self._level = level
        self._last = (text, closable)
        color = level_color(theme, level)
        self.text_label.setText(text)
        self.text_label.setStyleSheet(f"color: {color}; background: transparent;")
        icon_name = {"error": "alert", "warn": "alert", "warning": "alert", "success": "check"}.get(level, "info")
        self.icon_label.setPixmap(icons.pixmap(icon_name, color, 16))
        self.close_button.setVisible(closable)
        self.setStyleSheet(
            f"QFrame {{ background: {level_tint(theme, level)}; border: 1px solid {color};"
            f" border-radius: 8px; }}"
        )
        self.setVisible(True)

    def apply_theme(self, _theme: str) -> None:
        if self._last and self.isVisible():
            text, closable = self._last
            self.show_message(text, self._level, closable=closable)

    def clear(self) -> None:
        self.setVisible(False)
        self._last = None


class Toast(QLabel):
    """轻量提示，自动淡出。"""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setVisible(False)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._effect)
        self._animation = QPropertyAnimation(self._effect, b"opacity", self)
        self._animation.setDuration(260)
        self._animation.finished.connect(self._maybe_hide)

    def show_message(self, text: str, level: str = "info", *, duration_ms: int = 2400) -> None:
        theme = current_theme()
        p = palette(theme)
        color = level_color(theme, level)
        self.setText(text)
        self.setStyleSheet(
            f"background: {p.card}; border: 1px solid {color}; border-radius: 8px;"
            f" padding: 8px 16px; color: {p.text};"
        )
        self.adjustSize()
        self._reposition()
        self.setVisible(True)
        self.raise_()
        self._effect.setOpacity(0.0)
        self._animation.stop()
        self._animation.setStartValue(0.0)
        self._animation.setEndValue(1.0)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.start()
        self._hide_after = duration_ms
        from PySide6.QtCore import QTimer

        QTimer.singleShot(duration_ms, self._fade_out)
        self._fading = False

    def _fade_out(self) -> None:
        self._fading = True
        self._animation.stop()
        self._animation.setStartValue(self._effect.opacity())
        self._animation.setEndValue(0.0)
        self._animation.start()

    def _maybe_hide(self) -> None:
        if getattr(self, "_fading", False):
            self.setVisible(False)

    def _reposition(self) -> None:
        parent = self.parentWidget()
        if parent:
            x = (parent.width() - self.width()) // 2
            self.move(max(12, x), 18)

    def resizeEvent(self, event: Any) -> None:  # noqa: N802 - Qt 命名
        super().resizeEvent(event)
        self._reposition()


class TagChip(QLabel):
    """标签胶囊。"""

    def __init__(self, text: str, *, theme: str = "light", tone: str = "default", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.apply_theme(theme, tone)

    def apply_theme(self, theme: str, tone: str = "default") -> None:
        p = palette(theme)
        colors = {
            "default": (p.bg_alt, p.text_sub),
            "accent": (p.accent_soft, p.accent),
            "success": (p.tint_success, p.success),
            "warning": (p.tint_warn, p.warning),
            "danger": (p.tint_error, p.danger),
        }
        bg, fg = colors.get(tone, colors["default"])
        self.setStyleSheet(
            f"background: {bg}; color: {fg}; border-radius: 9px; padding: 2px 8px; font-size: 11.5px;"
        )


class FlowRow(QWidget):
    """自动换行的标签/按钮行（用 QHBoxLayout + 换行策略简化实现）。"""

    def __init__(self, parent: QWidget | None = None, *, spacing: int = 6) -> None:
        super().__init__(parent)
        from PySide6.QtWidgets import QGridLayout

        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(spacing)
        self._items: list[QWidget] = []
        self._per_row = 8

    def set_items(self, widgets: Iterable[QWidget], per_row: int = 8) -> None:
        for widget in self._items:
            self._grid.removeWidget(widget)
            widget.setParent(None)
        self._items = list(widgets)
        self._per_row = max(1, per_row)
        for index, widget in enumerate(self._items):
            self._grid.addWidget(widget, index // self._per_row, index % self._per_row)


class LineChart(QWidget):
    """极简折线图（自绘，避免引入 matplotlib 增大安装体积）。"""

    def __init__(self, parent: QWidget | None = None, *, theme: str = "light") -> None:
        super().__init__(parent)
        self._points: list[tuple[str, float]] = []
        self._theme = theme
        self._metric_label = ""
        self.setMinimumHeight(180)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_data(self, points: Sequence[tuple[str, float]], *, metric_label: str = "") -> None:
        self._points = [(str(x), float(y)) for x, y in points]
        self._metric_label = metric_label
        self.update()

    def apply_theme(self, theme: str) -> None:
        self._theme = theme
        self.update()

    def paintEvent(self, event: Any) -> None:  # noqa: N802 - Qt 命名
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p = palette(self._theme)
        rect = self.rect().adjusted(8, 8, -8, -8)

        painter.fillRect(self.rect(), QColor(p.card))

        if not self._points:
            painter.setPen(QColor(p.text_faint))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "暂无数据")
            return

        values = [v for _, v in self._points]
        vmax = max(values) or 1.0
        vmin = min(min(values), 0.0)
        span = (vmax - vmin) or 1.0

        # 网格线
        painter.setPen(QPen(QColor(p.border), 1, Qt.PenStyle.DashLine))
        grid_top = rect.top() + 14
        grid_bottom = rect.bottom() - 20
        for i in range(4):
            y = grid_top + (grid_bottom - grid_top) * i / 3
            painter.drawLine(rect.left(), int(y), rect.right(), int(y))

        count = len(self._points)
        left = rect.left() + 6
        right = rect.right() - 6
        step = (right - left) / max(1, count - 1)

        def point_at(index: int, value: float) -> QPointF:
            ratio = (value - vmin) / span
            return QPointF(left + step * index, grid_bottom - ratio * (grid_bottom - grid_top))

        # 面积
        area = QPainterPath()
        area.moveTo(point_at(0, self._points[0][1]))
        for index, (_, value) in enumerate(self._points):
            area.lineTo(point_at(index, value))
        area.lineTo(QPointF(right, grid_bottom))
        area.lineTo(QPointF(left, grid_bottom))
        area.closeSubpath()
        fill = QColor(p.accent)
        fill.setAlpha(38)
        painter.fillPath(area, fill)

        # 折线
        painter.setPen(QPen(QColor(p.accent), 2))
        path = QPainterPath()
        path.moveTo(point_at(0, self._points[0][1]))
        for index, (_, value) in enumerate(self._points):
            path.lineTo(point_at(index, value))
        painter.drawPath(path)

        # 数据点（点数少时才画）
        if count <= 32:
            painter.setBrush(QColor(p.card))
            painter.setPen(QPen(QColor(p.accent), 2))
            for index, (_, value) in enumerate(self._points):
                painter.drawEllipse(point_at(index, value), 3.2, 3.2)

        # 首尾标签
        painter.setPen(QColor(p.text_faint))
        font = QFont()
        font.setPointSize(8)
        painter.setFont(font)
        painter.drawText(
            QRectF(rect.left(), rect.bottom() - 18, rect.width() / 2, 18),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            self._points[0][0],
        )
        painter.drawText(
            QRectF(rect.center().x(), rect.bottom() - 18, rect.width() / 2, 18),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            self._points[-1][0],
        )
        # 峰值
        painter.setPen(QColor(p.text_sub))
        painter.drawText(
            QRectF(rect.left(), rect.top() - 4, rect.width(), 16),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            f"峰值 {_short(vmax)}" + (f" · {self._metric_label}" if self._metric_label else ""),
        )
        painter.end()


def _short(value: float) -> str:
    if value >= 100_000_000:
        return f"{value / 100_000_000:.1f}亿"
    if value >= 10_000:
        return f"{value / 10_000:.1f}万"
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.1f}"


class ScrollArea(QScrollArea):
    """透明背景的滚动容器，内部 widget 自适应宽度。"""

    def __init__(self, inner: QWidget | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        if inner is not None:
            self.setWidget(inner)


def labeled_row(label: str, widget: QWidget, *, label_width: int = 88) -> QWidget:
    """左侧固定宽度标签 + 右侧控件的行。"""
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(10)
    text = QLabel(label)
    text.setObjectName("SectionLabel")
    text.setFixedWidth(label_width)
    text.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    layout.addWidget(text)
    layout.addWidget(widget, 1)
    return row


def hline() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setStyleSheet("color: rgba(128,128,128,0.25);")
    line.setFixedHeight(1)
    return line


class BusyOverlay(QLabel):
    """覆盖式 loading 提示。"""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setVisible(False)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def show_text(self, text: str) -> None:
        p = palette(current_theme())
        self.setText(text)
        self.setStyleSheet(
            f"background: {p.card}; border: 1px solid {p.border}; border-radius: 8px;"
            f" padding: 8px 16px; color: {p.text_sub};"
        )
        self.adjustSize()
        self._reposition()
        self.setVisible(True)
        self.raise_()

    def _reposition(self) -> None:
        parent = self.parentWidget()
        if parent:
            self.move(
                max(8, (parent.width() - self.width()) // 2),
                max(8, (parent.height() - self.height()) // 2),
            )

    def resizeEvent(self, event: Any) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._reposition()


class ConfirmBar(QFrame):
    """人工确认条：勾选 + 主操作按钮（发布中心风控核心 UI）。"""

    confirmed = Signal(bool)
    triggered = Signal()

    def __init__(self, text: str, button_text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from PySide6.QtWidgets import QCheckBox

        self.setObjectName("Card")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(12)
        self.checkbox = QCheckBox(text)
        self.checkbox.setToolTip(text)
        self.checkbox.stateChanged.connect(lambda _: self.confirmed.emit(self.checkbox.isChecked()))
        layout.addWidget(self.checkbox, 1)
        self.button = QPushButton(button_text)
        self.button.setObjectName("Primary")
        self.button.setEnabled(False)
        self.button.clicked.connect(self.triggered.emit)
        layout.addWidget(self.button)
        self.checkbox.stateChanged.connect(lambda _: self.button.setEnabled(self.checkbox.isChecked()))

    def set_text(self, text: str) -> None:
        self.checkbox.setText(text)

    def reset(self) -> None:
        self.checkbox.setChecked(False)

    def set_busy(self, busy: bool, text: str = "正在执行…") -> None:
        self.button.setEnabled(not busy and self.checkbox.isChecked())
        self.button.setText(text if busy else "确认发布")


def make_button(
    text: str,
    *,
    icon: str = "",
    theme: str = "light",
    primary: bool = False,
    danger: bool = False,
    ghost: bool = False,
    tooltip: str = "",
) -> QPushButton:
    """统一风格的按钮工厂。"""
    button = QPushButton(text)
    if primary:
        button.setObjectName("Primary")
    elif danger:
        button.setObjectName("Danger")
    elif ghost:
        button.setObjectName("Ghost")
    if icon:
        color = palette(theme).on_accent if primary else palette(theme).text_sub
        button.setIcon(icons.icon(icon, color, 16))
    if tooltip:
        button.setToolTip(tooltip)
    return button


# --------------------------------------------------------------------------
# 状态指示灯
# --------------------------------------------------------------------------
def auto_summary(text: str, limit: int = 16) -> str:
    """把长提示压缩成一句短语，供指示灯展示。"""
    first = re.split(r"[。；;\n]", (text or "").strip())[0].strip()
    if not first:
        return "就绪"
    return first if len(first) <= limit else first[:limit] + "…"


class StatusLight(QLabel):
    """右上角状态指示灯（**常驻显示**）。

    把原先占一整行的提示条压缩成一枚小胶囊：
    - 圆点颜色表示状态（灰=就绪 / 紫=进行中 / 绿=正常 / 黄=降级 / 红=失败）
    - 空闲时显示中性的 ``● 就绪``，不会消失，保持页面右上角视觉稳定
    - 鼠标悬停或点击才展开完整详情，把页面顶部主视觉留给标题与主操作

    接口与 :class:`StatusBanner` 保持兼容（``show_message`` / ``clear``），
    页面无需区分两者；``clear()`` 的语义是「回到就绪」而不是隐藏。
    """

    #: 空闲时展示的文案
    IDLE_TEXT = "就绪"

    def __init__(self, theme: str = "light", parent: QWidget | None = None, *, idle_text: str = "") -> None:
        super().__init__(parent)
        self.setObjectName("StatusLight")
        self.setCursor(Qt.CursorShape.WhatsThisCursor)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self._theme = theme
        self._idle_text = idle_text or self.IDLE_TEXT
        self._level = "idle"
        self._detail = ""
        self._summary = self._idle_text
        self._render()
        # 注意：这里**不能**调用 setVisible(True)。构造期间控件还没有 parent，
        # 一旦被设为可见就会成为独立顶层窗口而闪现（Qt 会把无父控件当窗口）。
        # 加入布局后，父窗口 show 时它会自然显示。

    # -- 与 StatusBanner 对齐的接口 ---------------------------------------
    def show_message(
        self,
        text: str,
        level: str = "info",
        *,
        closable: bool = True,  # noqa: ARG002 - 兼容 StatusBanner 签名
        summary: str = "",
        detail: str = "",
        **_kw: Any,
    ) -> None:
        self._level = level or "info"
        self._detail = detail or text or ""
        self._summary = summary or auto_summary(text)
        self._render()
        self.setToolTip(self._detail)
        self._ensure_visible()

    def show_idle(self, text: str = "") -> None:
        """回到中性的「就绪」状态（指示灯依然可见）。"""
        self._level = "idle"
        self._detail = ""
        self._summary = text or self._idle_text
        self._render()
        self.setToolTip("当前没有需要提示的状态")
        self._ensure_visible()

    def clear(self) -> None:
        """回到「就绪」——指示灯常驻，不隐藏。"""
        self.show_idle()

    def _ensure_visible(self) -> None:
        """仅在已有父级（即已经进入界面树）时才显式显示，避免构造期闪现。"""
        try:
            if self.parent() is not None and not self.isVisible():
                self.setVisible(True)
        except RuntimeError:  # pragma: no cover
            pass

    # -- 视觉 -------------------------------------------------------------
    def _render(self) -> None:
        self.setText(f"● {self._summary}")
        color = level_color(self._theme, self._level)
        tint = level_tint(self._theme, self._level)
        self.setStyleSheet(
            f"#StatusLight {{"
            f" background: {tint};"
            f" border: 1px solid {self._border_for(color)};"
            f" border-radius: 11px;"
            f" padding: 2px 10px;"
            f" font-size: 11.5px;"
            f" color: {color};"
            f"}}"
            f"#StatusLight:hover {{ border-color: {level_color(self._theme, 'info')}; }}"
        )

    def _border_for(self, color: str) -> str:
        """就绪态用更淡的描边，避免常驻元素过于抢眼。"""
        if self._level == "idle":
            return palette(self._theme).border
        return color

    def apply_theme(self, theme: str) -> None:
        self._theme = theme
        self._render()

    # -- 交互 -------------------------------------------------------------
    def mousePressEvent(self, event: Any) -> None:  # noqa: N802 - Qt 命名
        QToolTip.showText(
            self.mapToGlobal(QPoint(0, self.height() + 6)),
            self._detail or self.toolTip(),
            self,
        )
        super().mousePressEvent(event)

    @property
    def detail(self) -> str:
        return self._detail

    @property
    def level(self) -> str:
        return self._level

    @property
    def is_idle(self) -> bool:
        return self._level == "idle"


# --------------------------------------------------------------------------
# 平台品牌色标签
# --------------------------------------------------------------------------
class PlatformBadgeDelegate(QStyledItemDelegate):
    """在表格单元格内绘制「平台品牌色圆点 + 文字」。

    数据来自单元格的 ``Qt.UserRole``（平台 key）。没有 key 时退回默认绘制。
    """

    DOT = 7.0

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: Any) -> None:  # noqa: N802
        key = index.data(Qt.ItemDataRole.UserRole)
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        if not key:
            super().paint(painter, option, index)
            return

        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        widget = opt.widget
        style = widget.style() if widget is not None else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, widget)

        theme = current_theme()
        p = palette(theme)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        rect = option.rect
        left = rect.left() + 10
        center_y = rect.center().y() + 1
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(platform_color(str(key)))))
        painter.drawEllipse(QPointF(left, center_y), self.DOT / 2, self.DOT / 2)

        text_left = left + self.DOT + 5
        text_rect = QRectF(
            text_left, rect.top(), max(0.0, rect.right() - text_left - 4), rect.height()
        )
        painter.setPen(QPen(QColor(p.text)))
        painter.drawText(
            text_rect,
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            text,
        )
        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index: Any):  # noqa: N802
        size = super().sizeHint(option, index)
        size.setWidth(size.width() + int(self.DOT) + 10)
        return size


def attach_platform_delegate(table: Any, column: int) -> PlatformBadgeDelegate:
    """给表格的某一列挂上平台品牌色委托。"""
    delegate = PlatformBadgeDelegate(table)
    table.setItemDelegateForColumn(column, delegate)
    return delegate


def platform_item(label: str, key: str) -> Any:
    """构造带平台 key 的表格单元格（供委托绘制圆点）。"""
    from PySide6.QtWidgets import QTableWidgetItem

    item = QTableWidgetItem(label)
    item.setData(Qt.ItemDataRole.UserRole, key or "")
    return item


# --------------------------------------------------------------------------
# 让每个组件成为「构建宿主」
# --------------------------------------------------------------------------
# 组件 __init__ 里创建的裸控件（如 QLabel(title)）会直接挂到组件名下，
# 而不是短暂成为顶层窗口。详见 ui/qt_guard.py。
from . import qt_guard as _qt_guard  # noqa: E402

for _component_cls in (
    Card,
    CardTitle,
    PageHeader,
    StatCard,
    EmptyState,
    StatusBanner,
    StatusLight,
    Toast,
    TagChip,
    FlowRow,
    LineChart,
    ScrollArea,
    BusyOverlay,
    ConfirmBar,
):
    _qt_guard.guarded_init(_component_cls)
del _component_cls
