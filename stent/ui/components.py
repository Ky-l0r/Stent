"""可复用 UI 组件（卡片、状态条、空态、趋势图等）。

遵循企划书 5.3：卡片圆角 8-12px、按钮 6px、图标线性、留白充足。
"""

from __future__ import annotations

import math
import re
from typing import Any, Callable, Iterable, Sequence

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QObject,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLayoutItem,
    QProgressBar,
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

#: 表格自定义数据角色（各页共用）
PLATFORM_ROLE = Qt.ItemDataRole.UserRole + 1
#: 热度原始数值（float），供微型条形图计算比例
HEAT_ROLE = Qt.ItemDataRole.UserRole + 2


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


class TitleCandidateDelegate(QStyledItemDelegate):
    """标题候选列表：选中项右侧出现一枚勾选标记。

    「双击选用」是隐蔽且低效的操作；改成单击选中后，用一枚明确的勾号告诉用户
    「当前用的就是这一条」，比把提示写在标题里更直白。
    """

    MARK = 16

    def paint(  # noqa: N802
        self, painter: QPainter, option: QStyleOptionViewItem, index: Any
    ) -> None:
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        if selected:
            # 给右侧勾号让出位置，避免长标题压到标记上
            opt.rect = option.rect.adjusted(0, 0, -(self.MARK + 18), 0)
        super().paint(painter, opt, index)

        if not selected:
            return
        p = palette(current_theme())
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = option.rect
        box = QRectF(
            rect.right() - self.MARK - 10,
            rect.center().y() - self.MARK / 2 + 1,
            self.MARK,
            self.MARK,
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(p.accent)))
        painter.drawEllipse(box)
        pen = QPen(QColor(p.on_accent), 2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.drawLine(
            QPointF(box.left() + box.width() * 0.28, box.center().y()),
            QPointF(box.left() + box.width() * 0.44, box.bottom() - box.height() * 0.30),
        )
        painter.drawLine(
            QPointF(box.left() + box.width() * 0.44, box.bottom() - box.height() * 0.30),
            QPointF(box.right() - box.width() * 0.26, box.top() + box.height() * 0.30),
        )
        painter.restore()


class EditableTagChip(QFrame):
    """可交互的标签胶囊：点胶囊复制，点右侧 × 删除。

    旧版标签是一行纯文本 `#通勤穿搭 #平价好物`，既看不出有几个、也没法单独删。
    改成一颗颗胶囊后，标签数量一眼可数，删改都是单击完成。
    """

    #: 点击胶囊正文（复制）
    clicked = Signal(str)
    #: 点击 × （删除）
    removed = Signal(str)

    def __init__(
        self,
        text: str,
        *,
        theme: str = "light",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("TagPill")
        self._text = text
        self._theme = theme
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 2, 4, 2)
        layout.setSpacing(4)

        self.text_label = QLabel(f"#{text}")
        self.text_label.setObjectName("TagPillText")
        layout.addWidget(self.text_label)

        self.close_button = QPushButton("×")
        self.close_button.setObjectName("TagPillClose")
        self.close_button.setFixedSize(16, 16)
        self.close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_button.setToolTip(f"删除标签 #{text}")
        self.close_button.clicked.connect(lambda: self.removed.emit(self._text))
        layout.addWidget(self.close_button)

        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(f"点击复制 #{text}")
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

    @property
    def tag(self) -> str:
        return self._text

    def mouseReleaseEvent(self, event: Any) -> None:  # noqa: N802 - Qt 命名
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._text)
        super().mouseReleaseEvent(event)


class CollapsibleSection(QWidget):
    """可折叠分组：一行标题 + 可收起的正文区。

    输入面板控件一多就会「又长又挤」，把低频项收进折叠区，
    常用项就能拿到更大的空间。
    """

    def __init__(
        self,
        title: str,
        *,
        icon: str = "sliders",
        theme: str = "light",
        expanded: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._theme = theme
        self._icon_name = icon
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.header = QPushButton(f"  {title}")
        self.header.setObjectName("CollapsibleHeader")
        self.header.setCheckable(True)
        self.header.setChecked(expanded)
        self.header.setCursor(Qt.CursorShape.PointingHandCursor)
        self.header.setIconSize(QSize(14, 14))
        self.header.toggled.connect(self._on_toggled)
        layout.addWidget(self.header)

        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(2, 0, 0, 0)
        self.body_layout.setSpacing(8)
        layout.addWidget(self.body)
        self.body.setVisible(expanded)

        self._refresh_icon()

    def add(self, widget: QWidget) -> QWidget:
        self.body_layout.addWidget(widget)
        return widget

    def add_layout(self, layout: Any) -> Any:
        self.body_layout.addLayout(layout)
        return layout

    def set_expanded(self, expanded: bool) -> None:
        self.header.setChecked(expanded)

    def is_expanded(self) -> bool:
        return self.header.isChecked()

    def _on_toggled(self, checked: bool) -> None:
        self.body.setVisible(checked)
        self._refresh_icon()

    def _refresh_icon(self) -> None:
        p = palette(self._theme)
        name = "chevron-down" if self.header.isChecked() else "chevron-right"
        self.header.setIcon(icons.icon(name, p.text_sub, 14))

    def apply_theme(self, theme: str) -> None:
        self._theme = theme
        self._refresh_icon()


class StepIndicator(QFrame):
    """生成步骤提示条：把「模型正在做什么」明明白白写出来。

    等待流式输出的那几秒如果界面毫无动静，用户会怀疑是不是卡住了；
    这里用「步骤文字 + 不确定进度条」给出持续的活动反馈。
    """

    def __init__(self, theme: str = "light", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("StepBar")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 7, 12, 7)
        layout.setSpacing(10)

        self.text_label = QLabel("")
        self.text_label.setObjectName("StepText")
        layout.addWidget(self.text_label)

        self.bar = QProgressBar()
        # 0/0 = 不确定进度：模型返回节奏不可预测，走「来回滚动」比假装百分比诚实
        self.bar.setRange(0, 0)
        self.bar.setTextVisible(False)
        self.bar.setFixedWidth(90)
        self.bar.setFixedHeight(4)
        layout.addWidget(self.bar)
        layout.addStretch(1)
        self.setVisible(False)

    def start(self, text: str) -> None:
        self.text_label.setText(text)
        self.setVisible(True)
        self.bar.setVisible(True)

    def set_step(self, text: str) -> None:
        self.text_label.setText(text)
        if not self.isVisible():
            self.setVisible(True)

    def stop(self) -> None:
        self.setVisible(False)


class FilterChip(QPushButton):
    """筛选胶囊（可选中）。

    选中态由 QSS 的 ``#FilterChip:checked`` 渲染成**实心强调色 + 反白文字**，
    未选中态是安静的描边胶囊——旧版白底紫边几乎看不出是否选中。
    """

    def __init__(
        self,
        text: str,
        *,
        checked: bool = False,
        tooltip: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(text, parent)
        self.setObjectName("FilterChip")
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        if tooltip:
            self.setToolTip(tooltip)


class FlowLayout(QLayout):
    """按可用宽度自动换行的流式布局。

    筛选胶囊、分类标签这类「数量不定、宽度不一」的元素用网格硬排会在窄窗口下
    溢出，这里按行高动态折行，窗口缩放时自动重排。
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        margin: int = 0,
        h_spacing: int = 6,
        v_spacing: int = 6,
    ) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self._h_spacing = h_spacing
        self._v_spacing = v_spacing
        self.setContentsMargins(margin, margin, margin, margin)

    # -- QLayout 接口 ----------------------------------------------------
    def addItem(self, item: QLayoutItem) -> None:  # noqa: N802 - Qt 命名
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:  # noqa: N802
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int) -> QLayoutItem | None:  # noqa: N802
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    # -- 折行计算 --------------------------------------------------------
    def _do_layout(self, rect: QRect, *, test_only: bool) -> int:
        margins = self.contentsMargins()
        area = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        x, y = area.x(), area.y()
        line_height = 0
        for item in self._items:
            widget = item.widget()
            # 只跳过「被显式隐藏」的项：父窗口尚未 show 时 isVisible() 也是 False，
            # 若按 isVisible() 判断，布局在首次计算高度时会得到 0。
            if widget is not None and widget.isHidden():
                continue
            hint = item.sizeHint()
            if line_height and x + hint.width() > area.right() + 1:
                x = area.x()
                y += line_height + self._v_spacing
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._h_spacing
            line_height = max(line_height, hint.height())
        return y + line_height - rect.y() + margins.bottom()


class FlowRow(QWidget):
    """自动换行的标签 / 按钮行。"""

    def __init__(self, parent: QWidget | None = None, *, spacing: int = 6) -> None:
        super().__init__(parent)
        self._flow = FlowLayout(self, h_spacing=spacing, v_spacing=spacing)
        policy = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self._items: list[QWidget] = []

    def set_items(self, widgets: Iterable[QWidget], per_row: int | None = None) -> None:
        """替换全部子项。``per_row`` 仅作兼容保留：换行由布局按宽度自动决定。"""
        for widget in self._items:
            self._flow.removeWidget(widget)
            widget.setParent(None)
            widget.deleteLater()
        self._items = list(widgets)
        for widget in self._items:
            widget.setParent(self)
            self._flow.addWidget(widget)
            widget.show()
        self.updateGeometry()

    def items(self) -> list[QWidget]:
        return list(self._items)


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


def field_label(text: str, hint: str = "") -> QWidget:
    """字段标题行：加粗字段名 + 右侧可选说明。

    表单里字段一多，「标签」和「内容」就容易糊成一片；统一用 #FieldLabel
    把字段名立起来，视线可以按标题快速跳读。
    """
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(8)
    name = QLabel(text)
    name.setObjectName("FieldLabel")
    layout.addWidget(name)
    layout.addStretch(1)
    if hint:
        note = QLabel(hint)
        note.setObjectName("FieldHint")
        layout.addWidget(note)
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
    stop: bool = False,
    tooltip: str = "",
) -> QPushButton:
    """统一风格的按钮工厂。"""
    button = QPushButton(text)
    if primary:
        button.setObjectName("Primary")
    elif stop:
        # 弱化的「中断 / 停止」：平时安静，悬停才亮红（见 #StopButton）
        button.setObjectName("StopButton")
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
        """始终只留一枚小圆点 + 中性文字，不画边框、不铺底色。

        平台健康检查是低频信息，旧版「绿边框 + 绿底 + 绿字」比主操作按钮还显眼；
        现在状态只由圆点颜色表达（绿=正常 / 黄=降级 / 红=失败 / 灰=就绪），
        完整详情留在悬停提示里，页面右上角始终只有一个视觉焦点。
        """
        theme = self._theme
        p = palette(theme)
        dot_color = level_color(theme, self._level)
        self.setText(
            f'<span style="color:{dot_color}; font-size:9px;">●</span>'
            f'<span style="color:{p.text_sub};"> {self._summary}</span>'
        )
        self.setStyleSheet(
            f"#StatusLight {{"
            f" background: transparent;"
            f" border: none;"
            f" padding: 2px 4px;"
            f" font-size: 11.5px;"
            f"}}"
            f"#StatusLight:hover {{ color: {p.text}; }}"
        )

    @property
    def summary(self) -> str:
        """纯文本摘要（``text()`` 现在是富文本，这里给出可断言的原文）。"""
        return self._summary

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
# 热榜表格专用绘制：行底色 / 排名徽章 / 热度条形图 / 标题平台图标
# --------------------------------------------------------------------------
_HEAT_RE = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*(亿|万|w|W|k|K)?")


def parse_heat(text: str) -> float:
    """把「130.5万」「1.2亿」「1148823」这类热度文本还原成可比较的数值。

    热度在服务层已经格式化成人话，条形图却需要原始量级，这里做一次反解。
    解析不出来时返回 0，调用方据此退化（不画条、数字用弱化色）。
    """
    raw = (text or "").strip()
    if not raw or raw in {"—", "-", "暂无", "N/A"}:
        return 0.0
    match = _HEAT_RE.search(raw)
    if not match:
        return 0.0
    try:
        value = float(match.group(1))
    except ValueError:
        return 0.0
    unit = (match.group(2) or "").lower()
    if unit == "亿":
        value *= 100_000_000
    elif unit in ("万", "w"):
        value *= 10_000
    elif unit == "k":
        value *= 1_000
    return value


def heat_tone(theme: str, ratio: float) -> str:
    """按「相对当前列表峰值」的比例给出热度色：红=爆、橙=热、灰=温。"""
    p = palette(theme)
    if ratio >= 0.6:
        return p.heat_hot
    if ratio >= 0.3:
        return p.heat_warm
    return p.heat_cool


class RowBackgroundDelegate(QStyledItemDelegate):
    """统一绘制行底色：悬停微亮、选中加深（暗色主题下变浅）。

    选中态不再铺满强调色——整行淡紫在长列表里既刺眼，也让标题文字对比不稳。
    改为「行底色加深 + 左侧 3px 强调色竖条」：克制，但一眼能定位到当前行。
    """

    ACCENT_BAR = 3

    def _paint_row(self, painter: QPainter, option: QStyleOptionViewItem, index: Any) -> None:
        # 悬停整行而不是「鼠标下的那个单元格」：Qt 的 State_MouseOver 只作用于单个
        # 单元格，左右移动时整行高亮会一段一段地闪，因此改由 _RowHoverFilter 记录行号。
        hovered_row = getattr(option.widget, "_hovered_row", -1)
        hovered = hovered_row == index.row()
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        if not (selected or hovered):
            return
        p = palette(current_theme())
        # 只填自己这一格：Qt 不会把委托的画笔裁剪到单元格，若在这里 fillRect 整行，
        # 后绘制的列会把前面列已经画好的文字整行盖掉（选中/悬停行会「变空白」）。
        rect = option.rect
        painter.save()
        painter.fillRect(rect, QColor(p.row_selected if selected else p.row_hover))
        if selected and index.column() == 0:
            # 选中行的左侧强调竖条只由第一列负责，否则每列都会画一条
            painter.fillRect(
                QRect(rect.left(), rect.top(), self.ACCENT_BAR, rect.height()), QColor(p.accent)
            )
        painter.restore()

    def paint(  # noqa: N802
        self, painter: QPainter, option: QStyleOptionViewItem, index: Any
    ) -> None:
        self._paint_row(painter, option, index)
        super().paint(painter, option, index)


class _RowHoverFilter(QObject):
    """跟踪鼠标所在的行，并只重绘受影响的两行。

    整行悬停高亮需要「知道当前在哪一行」，而 Qt 只告诉委托「哪个单元格被悬停」；
    这里在 viewport 上记录行号，顺便把重绘限制在该行，避免整表刷新造成拖影。
    """

    def __init__(self, table: Any) -> None:
        super().__init__(table)
        self._table = table
        self._row = -1
        table._hovered_row = -1
        table.viewport().installEventFilter(self)

    def _row_rect(self, row: int) -> QRect:
        table = self._table
        model = table.model()
        rect = table.visualRect(model.index(row, 0))
        last = model.columnCount() - 1
        if last > 0:
            rect = rect.united(table.visualRect(model.index(row, last)))
        return rect

    def eventFilter(self, obj: Any, event: Any) -> bool:  # noqa: N802 - Qt 命名
        try:
            if event.type() == QEvent.Type.MouseMove:
                row = self._table.rowAt(event.position().toPoint().y())
            elif event.type() == QEvent.Type.Leave:
                row = -1
            else:
                return False
        except RuntimeError:  # pragma: no cover - 表格已销毁
            return False
        if row != self._row:
            previous, self._row = self._row, row
            self._table._hovered_row = row
            for candidate in (previous, row):
                if candidate >= 0:
                    self._table.viewport().update(self._row_rect(candidate))
        return False


class RankBadgeDelegate(RowBackgroundDelegate):
    """排名列：1-3 名用金银铜圆形徽章，4-10 名深色数字，10 名以后浅灰数字。

    排名是热榜里最有信息量的一列，纯文本数字扫不出重点；徽章让前三名一眼可见。
    """

    DIAMETER = 22.0

    def paint(  # noqa: N802
        self, painter: QPainter, option: QStyleOptionViewItem, index: Any
    ) -> None:
        self._paint_row(painter, option, index)
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "").strip()
        try:
            rank = int(float(text))
        except ValueError:
            rank = 0
        p = palette(current_theme())
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = option.rect
        if 1 <= rank <= 3:
            color = {1: p.badge_gold, 2: p.badge_silver, 3: p.badge_bronze}[rank]
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(color)))
            painter.drawEllipse(
                QPointF(rect.center().x(), rect.center().y()), self.DIAMETER / 2, self.DIAMETER / 2
            )
            font = QFont(option.font)
            font.setPixelSize(11)
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(QPen(QColor(p.on_badge)))
        else:
            font = QFont(option.font)
            font.setBold(rank <= 10)
            painter.setFont(font)
            painter.setPen(QPen(QColor(p.text if rank <= 10 else p.text_faint)))
        painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), text)
        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index: Any) -> QSize:  # noqa: N802
        size = super().sizeHint(option, index)
        size.setHeight(max(size.height(), int(self.DIAMETER) + 14))
        return size


class HeatBarDelegate(RowBackgroundDelegate):
    """热度列：右对齐数值 + 左侧微型条形图，并按热度高低着色。

    纯数字看不出「爆」和「温」的差别；条形图给出同一列表内的相对量级，
    颜色（红=爆 / 橙=热 / 灰=温）则给出绝对档位。
    """

    BAR_WIDTH = 34
    BAR_HEIGHT = 4
    GAP = 8
    PAD = 10

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._peak = 0.0

    def set_peak(self, value: float) -> None:
        """设置当前列表的热度峰值（条形图按它归一化）。"""
        self._peak = max(0.0, float(value or 0.0))

    def paint(  # noqa: N802
        self, painter: QPainter, option: QStyleOptionViewItem, index: Any
    ) -> None:
        self._paint_row(painter, option, index)
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "").strip()
        raw = index.data(HEAT_ROLE)
        try:
            value = float(raw) if raw is not None else parse_heat(text)
        except (TypeError, ValueError):
            value = parse_heat(text)

        theme = current_theme()
        p = palette(theme)
        rect = option.rect
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        font = QFont(option.font)
        font.setBold(True)
        painter.setFont(font)
        metrics = QFontMetrics(font)
        label = text or "—"
        text_width = metrics.horizontalAdvance(label)
        text_rect = QRect(
            rect.right() - self.PAD - text_width, rect.top(), text_width, rect.height()
        )
        if value <= 0:
            painter.setPen(QPen(QColor(p.text_faint)))
            painter.drawText(text_rect, int(Qt.AlignmentFlag.AlignCenter), label)
            painter.restore()
            return

        ratio = min(1.0, value / self._peak) if self._peak > 0 else 1.0
        color = QColor(heat_tone(theme, ratio))
        painter.setPen(QPen(color))
        painter.drawText(text_rect, int(Qt.AlignmentFlag.AlignCenter), label)

        bar_left = rect.left() + self.PAD
        available = text_rect.left() - self.GAP - bar_left
        if available >= 10:
            width = float(min(self.BAR_WIDTH, available))
            top = rect.center().y() - self.BAR_HEIGHT / 2 + 1
            painter.setPen(Qt.PenStyle.NoPen)
            track = QColor(p.border_strong)
            track.setAlpha(110)
            painter.setBrush(QBrush(track))
            painter.drawRoundedRect(
                QRectF(bar_left, top, width, self.BAR_HEIGHT), 2, 2
            )
            painter.setBrush(QBrush(color))
            painter.drawRoundedRect(
                QRectF(bar_left, top, max(3.0, width * ratio), self.BAR_HEIGHT), 2, 2
            )
        painter.restore()


#: 平台 → 品牌色图标里的单字（无官方 logo 授权，用品牌色 + 单字做可识别的图标）
PLATFORM_GLYPHS: dict[str, str] = {
    "weibo": "微",
    "douyin": "抖",
    "zhihu": "知",
    "bilibili": "B",
    "baidu": "百",
    "toutiao": "头",
    "rednote": "红",
    "it-news": "IT",
    "ai-news": "AI",
    "60s": "60",
}


class TitleCellDelegate(RowBackgroundDelegate):
    """标题列：最左侧一枚平台品牌色图标，其后是标题文字。

    平台名单独占一列时，同一平台的几十行会重复同一个词，既冗余又白占宽度；
    这里把「来源」压缩成一枚品牌色小图标挂在标题最左侧。
    """

    GLYPH = 18.0
    GAP = 8
    PAD = 10

    def paint(  # noqa: N802
        self, painter: QPainter, option: QStyleOptionViewItem, index: Any
    ) -> None:
        self._paint_row(painter, option, index)
        key = str(index.data(PLATFORM_ROLE) or "")
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")

        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        widget = opt.widget
        style = widget.style() if widget is not None else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, widget)

        p = palette(current_theme())
        rect = option.rect
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        left = float(rect.left() + self.PAD)
        if key:
            side = self.GLYPH
            box = QRectF(left, rect.center().y() - side / 2 + 1, side, side)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(platform_color(key))))
            painter.drawRoundedRect(box, 5, 5)
            glyph = PLATFORM_GLYPHS.get(key) or (key[:1] or "?").upper()
            font = QFont(option.font)
            font.setPixelSize(10 if len(glyph) > 1 else 11)
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(QPen(QColor("#FFFFFF")))
            painter.drawText(box, int(Qt.AlignmentFlag.AlignCenter), glyph)
            left += side + self.GAP

        font = QFont(option.font)
        painter.setFont(font)
        painter.setPen(QPen(QColor(p.text)))
        available = max(0, int(rect.right() - self.PAD - left))
        elided = QFontMetrics(font).elidedText(text, Qt.TextElideMode.ElideRight, available)
        painter.drawText(
            QRect(int(left), rect.top(), available, rect.height()),
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            elided,
        )
        painter.restore()


class SkeletonList(QWidget):
    """列表骨架屏：刷新期间用占位条撑住表格区域，避免留白或闪烁。"""

    ROW_HEIGHT = 34
    BAR_HEIGHT = 8
    #: 拿不到真实列宽时的兜底比例：(起始位置, 宽度)
    SEGMENTS: tuple[tuple[float, float], ...] = (
        (0.014, 0.018),
        (0.042, 0.400),
        (0.620, 0.085),
        (0.730, 0.055),
        (0.800, 0.170),
    )

    def __init__(self, parent: QWidget | None = None, *, rows: int = 9) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._rows = max(1, rows)
        self._view: Any = None
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(60)
        self._timer.timeout.connect(self._tick)
        self.setVisible(False)

    def attach(self, view: Any) -> "SkeletonList":
        """挂到某个视图的 viewport 上，并跟随其尺寸变化。"""
        self.setParent(view.viewport())
        self._view = view
        view.viewport().installEventFilter(self)
        self.setGeometry(view.viewport().rect())
        return self

    def eventFilter(self, obj: Any, event: Any) -> bool:  # noqa: N802 - Qt 命名
        if event.type() == QEvent.Type.Resize:
            try:
                self.setGeometry(obj.rect())
            except RuntimeError:  # pragma: no cover - 控件已销毁
                return False
        return False

    def start(self) -> None:
        parent = self.parentWidget()
        if parent is not None:
            self.setGeometry(parent.rect())
        self._phase = 0.0
        self.setVisible(True)
        self.raise_()
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        self.setVisible(False)

    def _tick(self) -> None:
        self._phase = (self._phase + 0.09) % 1.0
        self.update()

    def _segments(self) -> list[tuple[float, float]]:
        """占位条的位置：优先按表格真实列宽对齐，取不到时退回固定比例。"""
        view = getattr(self, "_view", None)
        if view is not None:
            try:
                model = view.model()
                count = model.columnCount() if model is not None else 0
                spans: list[tuple[float, float]] = []
                for column in range(count):
                    width = view.columnWidth(column)
                    if width <= 12:
                        continue
                    left = view.columnViewportPosition(column)
                    pad = 10.0
                    usable = width - pad * 2
                    spans.append((left + pad, max(8.0, usable * 0.6)))
                if spans:
                    return spans
            except RuntimeError:  # pragma: no cover - 视图已销毁
                pass
        return [
            (self.width() * start, max(6.0, self.width() * width))
            for start, width in self.SEGMENTS
        ]

    def paintEvent(self, event: Any) -> None:  # noqa: N802 - Qt 命名
        p = palette(current_theme())
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), QColor(p.card))
        row_height = max(1, min(self.ROW_HEIGHT, self.height() // self._rows))
        bar_color = QColor(p.border_strong)
        segments = self._segments()
        for index in range(self._rows):
            top = index * row_height
            if top + row_height > self.height():
                break
            wave = 0.5 + 0.5 * math.sin((self._phase + index * 0.07) * 2 * math.pi)
            painter.setOpacity(0.28 + 0.34 * wave)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(bar_color))
            center_y = top + row_height / 2 - self.BAR_HEIGHT / 2
            for left, bar_width in segments:
                painter.drawRoundedRect(
                    QRectF(left, center_y, bar_width, self.BAR_HEIGHT), 4, 4
                )
        painter.setOpacity(1.0)


def attach_hot_table_visuals(
    table: Any, *, title_column: int, rank_column: int, heat_column: int
) -> dict[str, Any]:
    """给热榜类表格装配「行底色 + 排名徽章 + 标题图标 + 热度条形图」。"""
    row = RowBackgroundDelegate(table)
    table.setItemDelegate(row)
    rank = RankBadgeDelegate(table)
    table.setItemDelegateForColumn(rank_column, rank)
    heat = HeatBarDelegate(table)
    table.setItemDelegateForColumn(heat_column, heat)
    title = TitleCellDelegate(table)
    table.setItemDelegateForColumn(title_column, title)
    table.viewport().setMouseTracking(True)
    # 整行悬停 + 按行重绘（见 _RowHoverFilter）
    hover = _RowHoverFilter(table)
    return {"row": row, "rank": rank, "heat": heat, "title": title, "hover": hover}


class RowActionsDelegate(RowBackgroundDelegate):
    """操作列：在行内绘制「前往创作 / 打开网页」两个按钮。

    刻意不用 ``setCellWidget``：几百行 × 两个 QPushButton 会带来可观的构建与
    重绘开销，而这里只需要「画出来 + 命中判断 + 悬停反馈」。
    """

    GAP = 6
    PAD = 8
    HEIGHT = 24

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        column: int = 0,
        primary_text: str = "前往创作",
        secondary_text: str = "打开网页",
    ) -> None:
        super().__init__(parent)
        self._column = column
        self._primary_text = primary_text
        self._secondary_text = secondary_text
        self._table: Any = None
        self._hover: tuple[int, int] | None = None
        #: 回调入参为行号
        self.primary_clicked: Callable[[int], None] | None = None
        self.secondary_clicked: Callable[[int], None] | None = None

    def attach(self, table: Any) -> "RowActionsDelegate":
        """绑定表格，用于跟踪鼠标悬停到哪个按钮上。"""
        self._table = table
        table.viewport().setMouseTracking(True)
        table.viewport().installEventFilter(self)
        return self

    # -- 悬停反馈 --------------------------------------------------------
    def eventFilter(self, obj: Any, event: Any) -> bool:  # noqa: N802 - Qt 命名
        table = self._table
        if table is None:
            return False
        if event.type() in (QEvent.Type.MouseMove, QEvent.Type.Leave):
            hovered: tuple[int, int] | None = None
            if event.type() == QEvent.Type.MouseMove:
                pos = event.position().toPoint()
                index = table.indexAt(pos)
                if index.isValid() and index.column() == self._column:
                    option = QStyleOptionViewItem()
                    option.font = table.font()
                    cell = table.visualRect(index)
                    for slot, box in enumerate(self._button_rects(cell, option)):
                        if box.contains(pos):
                            hovered = (index.row(), slot)
                            break
            if hovered != self._hover:
                previous = self._hover
                self._hover = hovered
                # 只重绘受影响的两行：整表 update() 在大列表上会明显拖影、掉帧
                rows = {
                    row
                    for row in (
                        previous[0] if previous else -1,
                        hovered[0] if hovered else -1,
                    )
                    if row >= 0
                }
                for row in rows:
                    item = table.item(row, self._column)
                    if item is not None:
                        table.viewport().update(table.visualRect(table.indexFromItem(item)))
        return False

    # -- 绘制 ------------------------------------------------------------
    def _button_rects(self, rect: QRect, option: QStyleOptionViewItem) -> tuple[QRect, QRect]:
        font = QFont(option.font)
        font.setPixelSize(12)
        metrics = QFontMetrics(font)
        available = max(60, rect.width() - self.PAD * 2)
        space = max(40, available - self.GAP)
        width_a = metrics.horizontalAdvance(self._primary_text)
        width_b = metrics.horizontalAdvance(self._secondary_text)
        total = max(1, width_a + width_b)
        first_width = int(space * width_a / total)
        top = int(rect.center().y() - self.HEIGHT / 2)
        first = QRect(rect.left() + self.PAD, top, first_width, self.HEIGHT)
        second = QRect(
            first.right() + 1 + self.GAP, top, space - first_width, self.HEIGHT
        )
        return first, second

    def paint(  # noqa: N802
        self, painter: QPainter, option: QStyleOptionViewItem, index: Any
    ) -> None:
        self._paint_row(painter, option, index)
        url = str(index.data(Qt.ItemDataRole.UserRole) or "")
        first, second = self._button_rects(option.rect, option)
        p = palette(current_theme())
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        font = QFont(option.font)
        font.setPixelSize(12)
        painter.setFont(font)
        specs = (
            (first, self._primary_text, True, True),
            (second, self._secondary_text, False, bool(url)),
        )
        for slot, (box, label, primary, enabled) in enumerate(specs):
            hovered = enabled and self._hover == (index.row(), slot)
            if primary:
                background = p.accent_soft if enabled else p.bg_alt
                border = p.accent if enabled else p.border
                foreground = p.accent if enabled else p.text_faint
            else:
                background = p.card_hover if enabled else p.bg_alt
                border = p.border_strong if enabled else p.border
                foreground = p.text_sub if enabled else p.text_faint
            if hovered:
                background = p.accent_soft
                border = p.accent
                foreground = p.accent
            painter.setPen(QPen(QColor(border), 1))
            painter.setBrush(QBrush(QColor(background)))
            painter.drawRoundedRect(QRectF(box), 6, 6)
            painter.setPen(QPen(QColor(foreground)))
            painter.drawText(box, int(Qt.AlignmentFlag.AlignCenter), label)
        painter.restore()

    # -- 命中 ------------------------------------------------------------
    def editorEvent(  # noqa: N802
        self, event: Any, model: Any, option: QStyleOptionViewItem, index: Any
    ) -> bool:
        if index.column() != self._column:
            return False
        if event.type() != QEvent.Type.MouseButtonRelease:
            return False
        if event.button() != Qt.MouseButton.LeftButton:
            return False
        try:
            pos = event.position().toPoint()
        except AttributeError:  # pragma: no cover - 极老版本 Qt
            pos = event.pos()
        first, second = self._button_rects(option.rect, option)
        if first.contains(pos):
            if self.primary_clicked is not None:
                self.primary_clicked(index.row())
            return True
        url = str(index.data(Qt.ItemDataRole.UserRole) or "")
        if second.contains(pos) and url:
            if self.secondary_clicked is not None:
                self.secondary_clicked(index.row())
            return True
        return False


class CategoryPicker(QPushButton):
    """垂类选择器：一枚胶囊按钮 + 「两列铺开、可滚动」的复选弹层。

    垂类有二十多个，全部摊在筛选区会把列表挤没；做成弹层后既能一次看全，
    又不占用列表高度。弹层用自带 ``Qt.Popup`` 的浮层 + 滚动区实现——
    ``QMenu`` + ``QWidgetAction`` 在条目多时尺寸协商不稳，会出现选项互相压字。
    """

    changed = Signal()

    #: 弹层最大高度：超出后由滚动条接管（用户明确要求「放不下就给滚动条」）
    MAX_HEIGHT = 340

    def __init__(self, parent: QWidget | None = None, *, columns: int = 2) -> None:
        super().__init__("垂类：全部", parent)
        from PySide6.QtWidgets import QGridLayout, QScrollArea, QVBoxLayout

        self.setObjectName("FilterChip")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self._columns = max(1, columns)
        self._selected: set[str] = set()
        self._boxes: dict[str, Any] = {}

        self._popup = QFrame(self, Qt.WindowType.Popup)
        self._popup.setObjectName("PopupPanel")
        self._popup.setFrameShape(QFrame.Shape.NoFrame)
        outer = QVBoxLayout(self._popup)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(0)

        self._area = QScrollArea(self._popup)
        self._area.setWidgetResizable(True)
        self._area.setFrameShape(QFrame.Shape.NoFrame)
        self._area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._panel = QWidget()
        self._grid = QGridLayout(self._panel)
        self._grid.setContentsMargins(8, 6, 8, 6)
        self._grid.setHorizontalSpacing(18)
        self._grid.setVerticalSpacing(4)
        self._area.setWidget(self._panel)
        outer.addWidget(self._area)

        self.clicked.connect(self._toggle_popup)

    # -- 数据 ------------------------------------------------------------
    def set_categories(self, counts: Sequence[tuple[str, int]]) -> None:
        """按「垂类（数量）」重建两列选项。

        已选中但当前平台下没有数据的垂类会被保留（数量记 0）——否则用户切换平台后
        勾选项会莫名消失，看起来像是筛选自己失效了。
        """
        from PySide6.QtWidgets import QCheckBox

        for box in self._boxes.values():
            self._grid.removeWidget(box)
            box.setParent(None)
            box.deleteLater()
        self._boxes.clear()

        options = list(counts)
        known = {name for name, _count in options}
        options += [(name, 0) for name in sorted(self._selected - known)]
        for index, (name, count) in enumerate(options):
            box = QCheckBox(f"{name}（{count}）")
            box.setChecked(name in self._selected)
            box.setCursor(Qt.CursorShape.PointingHandCursor)
            box.stateChanged.connect(lambda _state, n=name: self._on_toggle(n))
            self._grid.addWidget(box, index // self._columns, index % self._columns)
            self._boxes[name] = box
        self._sync_label()
        self._resize_popup()

    def selected(self) -> list[str]:
        return [name for name in self._boxes if name in self._selected]

    def clear_selection(self) -> None:
        if not self._selected:
            return
        self._selected.clear()
        for box in self._boxes.values():
            box.blockSignals(True)
            box.setChecked(False)
            box.blockSignals(False)
        self._sync_label()
        self.changed.emit()

    def _on_toggle(self, name: str) -> None:
        box = self._boxes.get(name)
        if box is None:
            return
        if box.isChecked():
            self._selected.add(name)
        else:
            self._selected.discard(name)
        self._sync_label()
        self.changed.emit()

    def _sync_label(self) -> None:
        chosen = self.selected()
        if not chosen:
            self.setText("垂类：全部")
            self.setToolTip("当前不限垂类")
            return
        if len(chosen) == 1:
            self.setText(f"垂类：{chosen[0]}")
        else:
            self.setText(f"垂类：{chosen[0]} 等 {len(chosen)} 项")
        self.setToolTip("已选垂类：" + "、".join(chosen))

    # -- 弹层 ------------------------------------------------------------
    def _resize_popup(self) -> None:
        """按实际内容定尺寸：装得下就贴合，装不下就固定最大高度并交给滚动条。"""
        hint = self._panel.sizeHint()
        margins = self._area.frameWidth() * 2 + 12
        width = max(220, hint.width() + margins + 18)
        # 多给 6px 余量，避免内容刚好贴合时冒出一条用不上的滚动条
        height = hint.height() + margins + 6
        self._popup.setFixedSize(width, min(self.MAX_HEIGHT, height))

    def _toggle_popup(self) -> None:
        if self._popup.isVisible():
            self._popup.hide()
            return
        self._resize_popup()
        # 让浮层尽量与按钮左对齐；右侧越界时向左回收，避免跑出窗口
        origin = self.mapToGlobal(QPoint(0, self.height() + 4))
        screen = self.screen() or QApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            if origin.x() + self._popup.width() > available.right():
                origin.setX(max(available.left(), available.right() - self._popup.width()))
        self._popup.move(origin)
        self._popup.show()
        self._popup.raise_()


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
    EditableTagChip,
    CollapsibleSection,
    StepIndicator,
    FilterChip,
    CategoryPicker,
    FlowRow,
    LineChart,
    ScrollArea,
    BusyOverlay,
    ConfirmBar,
    SkeletonList,
):
    _qt_guard.guarded_init(_component_cls)
del _component_cls
