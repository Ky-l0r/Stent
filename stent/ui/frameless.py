"""无边框窗口：把 Windows 标题栏的能力搬到应用自己的顶栏上。

对齐原生标题栏的完整行为：

======================  ==========================================================
长按顶栏拖拽             ``QWindow.startSystemMove()``（等价于原生 ``HTCAPTION`` 拖拽）
拖到屏幕边缘 / 顶部      系统 Aero Snap：左右分屏、顶部最大化
拖到顶部停住呼出布局     系统 Snap 布局选择器（Windows 11）
悬停最大化按钮呼出布局   ``WM_NCHITTEST`` 返回 ``HTMAXBUTTON``
双击顶栏最大化 / 还原    系统在 caption 拖拽中处理，另有 Qt 侧兜底
拖拽窗口边缘调整大小     ``WM_NCHITTEST`` 返回 ``HTLEFT`` / ``HTBOTTOMRIGHT`` …
最大化不遮挡任务栏       ``WM_GETMINMAXINFO`` 按显示器工作区修正
窗口圆角                 Windows 11 DWM 原生圆角；Windows 10 用 ``SetWindowRgn`` 降级
======================  ==========================================================

设计约束：窗口保持**不透明**（不启用 ``WA_TranslucentBackground``），
这样才能拿到 DWM 的真实圆角与系统阴影，也不会牺牲重绘性能。
"""

from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes
from typing import Any

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QPoint,
    QPropertyAnimation,
    QRect,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QMainWindow,
    QPushButton,
    QWidget,
)

from . import icons
from .qt_guard import guarded_init
from .theme import current_theme, palette

log = logging.getLogger(__name__)
IS_WINDOWS = sys.platform == "win32"

# --------------------------------------------------------------------------
# Win32 常量
# --------------------------------------------------------------------------
WM_NCHITTEST = 0x0084
WM_NCCALCSIZE = 0x0083
WM_NCLBUTTONDOWN = 0x00A1
WM_NCLBUTTONDBLCLK = 0x00A3
WM_NCMOUSEMOVE = 0x00A0
WM_NCMOUSELEAVE = 0x02A2
WM_GETMINMAXINFO = 0x0024
WM_SETTINGCHANGE = 0x001A

GWL_STYLE = -16
WS_THICKFRAME = 0x00040000
WS_CAPTION = 0x00C00000
WS_SYSMENU = 0x00080000
WS_MINIMIZEBOX = 0x00020000
WS_MAXIMIZEBOX = 0x00010000
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_FRAMECHANGED = 0x0020

HTCLIENT = 1
HTCAPTION = 2
HTMAXBUTTON = 9
HTLEFT = 10
HTRIGHT = 11
HTTOP = 12
HTTOPLEFT = 13
HTTOPRIGHT = 14
HTBOTTOM = 15
HTBOTTOMLEFT = 16
HTBOTTOMRIGHT = 17

DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWA_BORDER_COLOR = 34
DWMWCP_DEFAULT = 0
DWMWCP_DONOTROUND = 1
DWMWCP_ROUND = 2
DWMWA_COLOR_DEFAULT = 0xFFFFFFFF
DWMWA_COLOR_NONE = 0xFFFFFFFE


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


class MINMAXINFO(ctypes.Structure):
    _fields_ = [
        ("ptReserved", wintypes.POINT),
        ("ptMaxSize", wintypes.POINT),
        ("ptMaxPosition", wintypes.POINT),
        ("ptMinTrackSize", wintypes.POINT),
        ("ptMaxTrackSize", wintypes.POINT),
    ]


# --------------------------------------------------------------------------
# Win32 调用封装
# --------------------------------------------------------------------------
def hwnd_of(widget: QWidget) -> int:
    """取窗口句柄；失败返回 0。"""
    try:
        return int(widget.winId())
    except Exception:  # noqa: BLE001
        return 0


def set_dwm_corner(hwnd: int, preference: int) -> bool:
    """设置 DWM 窗口圆角偏好（Windows 11 build 22000+）。"""
    if not IS_WINDOWS or not hwnd:
        return False
    try:
        value = ctypes.c_int(preference)
        result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
            wintypes.HWND(hwnd),
            ctypes.c_uint(DWMWA_WINDOW_CORNER_PREFERENCE),
            ctypes.byref(value),
            ctypes.sizeof(value),
        )
        return result == 0
    except Exception:  # noqa: BLE001
        log.debug("DWM 圆角设置失败", exc_info=True)
        return False


def set_dwm_border_color(hwnd: int, color: str | None) -> bool:
    """把窗口边框色设为主题色，避免系统默认色与主题不搭。"""
    if not IS_WINDOWS or not hwnd:
        return False
    try:
        if color is None:
            value = ctypes.c_uint(DWMWA_COLOR_NONE)
        else:
            rgb = color.lstrip("#")
            r, g, b = int(rgb[0:2], 16), int(rgb[2:4], 16), int(rgb[4:6], 16)
            value = ctypes.c_uint((b << 16) | (g << 8) | r)  # COLORREF = 0x00BBGGRR
        result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
            wintypes.HWND(hwnd),
            ctypes.c_uint(DWMWA_BORDER_COLOR),
            ctypes.byref(value),
            ctypes.sizeof(value),
        )
        return result == 0
    except Exception:  # noqa: BLE001
        return False


def apply_round_region(hwnd: int, width: int, height: int, radius: int) -> bool:
    """Windows 10 降级方案：用窗口区域裁出圆角。"""
    if not IS_WINDOWS or not hwnd or width <= 0 or height <= 0:
        return False
    try:
        region = ctypes.windll.gdi32.CreateRoundRectRgn(
            0, 0, width + 1, height + 1, radius * 2, radius * 2
        )
        if not region:
            return False
        # SetWindowRgn 成功后由系统接管该 region，不能再释放
        return bool(ctypes.windll.user32.SetWindowRgn(wintypes.HWND(hwnd), region, True))
    except Exception:  # noqa: BLE001
        log.debug("SetWindowRgn 失败", exc_info=True)
        return False


def clear_round_region(hwnd: int) -> None:
    if not IS_WINDOWS or not hwnd:
        return
    try:
        ctypes.windll.user32.SetWindowRgn(wintypes.HWND(hwnd), None, True)
    except Exception:  # noqa: BLE001
        pass


def enable_native_frame(hwnd: int, enable: bool = True) -> bool:
    """给无边框窗口补上标准窗口样式位。

    **这一步是 Aero Snap 与过渡动画能否生效的关键。**
    ``Qt.FramelessWindowHint`` 生成的窗口是纯 ``WS_POPUP``，系统因此认为它
    「不可最大化、不可调整大小」，于是：

    - 拖到屏幕边缘不出现分屏预览、拖到顶部不最大化；
    - 悬停最大化按钮不弹出 Snap 布局选择器；
    - 最大化 / 最小化没有系统过渡动画。

    补上 ``WS_THICKFRAME | WS_MAXIMIZEBOX | WS_MINIMIZEBOX | WS_SYSMENU | WS_CAPTION``
    之后系统才把它当作普通窗口对待，上述能力全部回归；DWM 也会据此绘制窗口阴影。
    真正的标题栏区域由 ``WM_NCCALCSIZE`` 返回 0 抹掉，界面不会被系统边框侵占。
    """
    if not IS_WINDOWS or not hwnd:
        return False
    try:
        user32 = ctypes.windll.user32
        handle = wintypes.HWND(hwnd)
        style = user32.GetWindowLongW(handle, GWL_STYLE)
        native_bits = WS_THICKFRAME | WS_MAXIMIZEBOX | WS_MINIMIZEBOX | WS_SYSMENU | WS_CAPTION
        new_style = (style | native_bits) if enable else (style & ~native_bits)
        if new_style == style:
            return True
        user32.SetWindowLongW(handle, GWL_STYLE, new_style)
        user32.SetWindowPos(
            handle, None, 0, 0, 0, 0,
            SWP_FRAMECHANGED | SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER,
        )
        return True
    except Exception:  # noqa: BLE001
        log.debug("设置窗口原生样式失败", exc_info=True)
        return False


def _message_from(message: Any) -> Any:
    """把 Qt 传来的原生消息指针转成 MSG 结构。"""
    address = int(message)
    return ctypes.wintypes.MSG.from_address(address)


def _lparam_point(lparam: int) -> tuple[int, int]:
    """从 lParam 解出屏幕坐标（有符号 16 位）。"""
    x = ctypes.c_short(lparam & 0xFFFF).value
    y = ctypes.c_short((lparam >> 16) & 0xFFFF).value
    return x, y


# --------------------------------------------------------------------------
# 窗口控制按钮
# --------------------------------------------------------------------------
class WindowControlButton(QPushButton):
    """标题栏窗口控制按钮（最小化 / 最大化 / 全屏 / 关闭）。"""

    def __init__(
        self,
        icon_name: str,
        tooltip: str,
        parent: QWidget | None = None,
        *,
        danger: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("CloseButton" if danger else "WindowButton")
        self.setFixedSize(38, 28)
        self.setToolTip(tooltip)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setIconSize(QSize(16, 16))
        self.setFlat(True)
        self._icon_name = icon_name
        self._danger = danger
        self._theme = current_theme()
        self._hover = False
        self._refresh()

    def set_icon_name(self, icon_name: str, tooltip: str = "") -> None:
        self._icon_name = icon_name
        if tooltip:
            self.setToolTip(tooltip)
        self._refresh()

    def apply_theme(self, theme: str) -> None:
        self._theme = theme
        self._refresh()

    def _refresh(self) -> None:
        p = palette(self._theme)
        if self._danger and self._hover:
            color = "#FFFFFF"
        elif self._hover:
            color = p.text
        else:
            color = p.text_sub
        self.setIcon(icons.icon(self._icon_name, color, 16, 1.7))

    # -- hover：关闭按钮变红底白字 --
    def enterEvent(self, event: Any) -> None:  # noqa: N802
        self._hover = True
        self._refresh()
        super().enterEvent(event)

    def leaveEvent(self, event: Any) -> None:  # noqa: N802
        self._hover = False
        self._refresh()
        super().leaveEvent(event)

    def set_nc_hover(self, hovering: bool) -> None:
        """由原生 ``WM_NCMOUSEMOVE`` 驱动的 hover（用于 Snap 热区被系统接管时）。"""
        if hovering == self._hover:
            return
        self._hover = hovering
        self._refresh()


class WindowControls(QWidget):
    """最小化 / 最大化 / 关闭。"""

    minimize_requested = Signal()
    maximize_requested = Signal()
    close_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        self.minimize_button = WindowControlButton("win-min", "最小化", self)
        self.maximize_button = WindowControlButton("win-max", "最大化", self)
        self.close_button = WindowControlButton("win-close", "关闭", self, danger=True)

        for button in (self.minimize_button, self.maximize_button, self.close_button):
            button.setAutoDefault(False)
            layout.addWidget(button)

        self.minimize_button.clicked.connect(self.minimize_requested.emit)
        self.maximize_button.clicked.connect(self.maximize_requested.emit)
        self.close_button.clicked.connect(self.close_requested.emit)

    def apply_theme(self, theme: str) -> None:
        for button in (self.minimize_button, self.maximize_button, self.close_button):
            button.apply_theme(theme)

    def set_window_state(self, *, maximized: bool, fullscreen: bool) -> None:
        if maximized and not fullscreen:
            self.maximize_button.set_icon_name("win-restore", "向下还原")
        else:
            self.maximize_button.set_icon_name("win-max", "最大化")
        self.maximize_button.setEnabled(not fullscreen)


# --------------------------------------------------------------------------
# 顶栏
# --------------------------------------------------------------------------
class TitleBar(QFrame):
    """应用的顶栏，同时充当原生标题栏：

    - 空白处长按拖拽（触发系统 Snap）
    - 双击最大化 / 还原
    - 右侧内置窗口控制按钮
    """

    DOUBLE_CLICK_MS = 400

    def __init__(self, parent: QWidget | None = None, *, height: int = 58) -> None:
        super().__init__(parent)
        self.setObjectName("TopBar")
        self.setFixedHeight(height)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(18, 0, 10, 0)
        self._layout.setSpacing(12)
        self._manual_drag = False
        self._drag_offset = QPoint()
        self._last_toggle_ms = 0
        self._left_count = 0

        self.controls = WindowControls(self)
        self._layout.addStretch(1)
        self._layout.addWidget(self.controls)

        self.controls.minimize_requested.connect(self._minimize)
        self.controls.maximize_requested.connect(self._toggle_maximize)
        self.controls.close_requested.connect(self._close)

    # -- 内容插槽 ----------------------------------------------------------
    def body(self) -> QHBoxLayout:
        return self._layout

    def add_left(self, widget: QWidget) -> QWidget:
        """把控件加入顶栏左侧（保持添加顺序）。"""
        self._layout.insertWidget(self._left_count, widget)
        self._left_count += 1
        return widget

    def insert_before_controls(self, widget: QWidget) -> QWidget:
        """把控件插到窗口控制按钮之前（即顶栏右侧、按钮左边）。"""
        self._layout.insertWidget(self._layout.indexOf(self.controls), widget)
        return widget

    #: 语义别名，供调用方表达「右侧操作区」
    add_right = insert_before_controls

    # -- 拖拽 -------------------------------------------------------------
    def _frameless_window(self) -> "FramelessWindow | None":
        window = self.window()
        return window if isinstance(window, FramelessWindow) else None

    def mousePressEvent(self, event: Any) -> None:  # noqa: N802 - Qt 命名
        if event.button() == Qt.MouseButton.LeftButton:
            window = self._frameless_window()
            if window is not None and window.start_native_move():
                # 交给系统：原生拖拽、边缘 Snap、顶部布局选择器全部可用
                event.accept()
                return
            # 非 Windows 或原生调用失败时自己搬窗口
            self._manual_drag = True
            self._drag_offset = event.globalPosition().toPoint() - self.window().frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: Any) -> None:  # noqa: N802
        if self._manual_drag and event.buttons() & Qt.MouseButton.LeftButton:
            self.window().move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: Any) -> None:  # noqa: N802
        self._manual_drag = False
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: Any) -> None:  # noqa: N802
        """双击最大化 / 还原。

        系统在 caption 拖拽中通常已处理双击；这里的兜底仅在该事件真的
        传到 Qt 时才生效，并用时间去抖避免与系统行为叠加。
        """
        if event.button() == Qt.MouseButton.LeftButton:
            import time

            now = int(time.monotonic() * 1000)
            if now - self._last_toggle_ms > self.DOUBLE_CLICK_MS:
                self._last_toggle_ms = now
                self._toggle_maximize()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    # -- 操作转发 ----------------------------------------------------------
    def _minimize(self) -> None:
        window = self._frameless_window()
        if window is not None:
            window.minimize_window()
        else:
            self.window().showMinimized()

    def _toggle_maximize(self) -> None:
        window = self._frameless_window()
        if window is not None:
            window.toggle_maximize()
        elif self.window().isMaximized():
            self.window().showNormal()
        else:
            self.window().showMaximized()

    def _toggle_fullscreen(self) -> None:
        window = self._frameless_window()
        if window is not None:
            window.toggle_fullscreen()

    def _close(self) -> None:
        self.window().close()

    def apply_theme(self, theme: str) -> None:
        self.controls.apply_theme(theme)


# --------------------------------------------------------------------------
# 无边框窗口
# --------------------------------------------------------------------------
class FramelessWindow(QMainWindow):
    """无边框主窗口基类。"""

    CORNER_RADIUS = 12
    RESIZE_BORDER = 6
    #: 最大化 / 还原过渡时长。
    #: 无边框窗口的过渡只能自己画，而 Qt widgets 走 CPU 光栅化——1920×1080
    #: 单帧重绘约 29ms，实测 35~40fps 已是这套渲染路径的上限。
    #: 因此时长取「帧数够看、总时长够短」的折中，宁短勿长。
    TRANSITION_MS = 130
    #: 最小化过渡时长（收缩 + 淡出，帧率更高）
    MINIMIZE_MS = 120

    #: (是否最大化, 是否全屏)
    state_changed = Signal(bool, bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self._corners_applied = False
        self._dwm_rounded = False
        self._native_frame = False
        self._hit_zones: list[tuple[QWidget, int]] = []
        self._nc_hovered: QWidget | None = None
        self.title_bar: TitleBar | None = None
        # 过渡动画状态
        self._animating = False
        self._animation: QPropertyAnimation | None = None
        self._opacity_animation: QPropertyAnimation | None = None
        self._pending_state: str | None = None
        self._restore_geometry: QRect | None = None
        self._content_frozen = False
        self._content_size_backup: tuple[QSize, QSize] = (QSize(0, 0), QSize(16777215, 16777215))

    # -- 注册 Snap 布局热区 ------------------------------------------------
    def register_hit_zone(self, widget: QWidget, code: int) -> None:
        """把控件登记为「非客户区按钮」，使系统在其上启用 Snap 布局选择器。"""
        self._hit_zones.append((widget, code))

    # ------------------------------------------------------------------
    # 过渡动画
    # ------------------------------------------------------------------
    #: 无边框窗口没有系统非客户区，DWM 不会播放最大化 / 最小化的缩放过渡，
    #: 因此这里用几何动画自己实现，观感与系统一致（OutCubic 缓动 + 淡出）。
    def _current_screen(self):
        screen = self.screen()
        if screen is None:
            screen = QApplication.screenAt(self.frameGeometry().center())
        return screen or QApplication.primaryScreen()

    def _work_area(self) -> QRect:
        return self._current_screen().availableGeometry()

    def _minimize_target(self) -> QRect:
        """最小化时收缩到的目标矩形：朝任务栏所在方向收拢。"""
        screen = self._current_screen()
        area = screen.availableGeometry()
        full = screen.geometry()
        geo = self.geometry()

        if area.bottom() < full.bottom():  # 任务栏在底部
            anchor = QPoint(geo.center().x(), full.bottom())
        elif area.top() > full.top():  # 顶部
            anchor = QPoint(geo.center().x(), full.top())
        elif area.left() > full.left():  # 左侧
            anchor = QPoint(full.left(), geo.center().y())
        elif area.right() < full.right():  # 右侧
            anchor = QPoint(full.right(), geo.center().y())
        else:  # 任务栏自动隐藏
            anchor = QPoint(geo.center().x(), full.bottom())

        width = max(90, int(geo.width() * 0.12))
        height = max(60, int(geo.height() * 0.12))
        return QRect(anchor.x() - width // 2, anchor.y() - height // 2, width, height)

    def _start_transition(self, target: QRect, state: str, *, duration: int, fade: bool = False) -> None:
        self._animating = True
        self._pending_state = state

        if state == "maximized":
            # 放大过程中窗口还是 normal 状态，若保留圆角会看到"到顶后圆角突然消失"，
            # 所以提前切成直角。
            hwnd = hwnd_of(self)
            if hwnd:
                set_dwm_corner(hwnd, DWMWCP_DONOTROUND)
                clear_round_region(hwnd)

        # 把内容按**固定尺寸**冻结：动画期间 Qt 无需逐帧重排整个界面，
        # 既省下布局开销，也消除了卡片文字重排的抖动。
        #   · 放大 / 还原 → 冻结成目标尺寸，内容先就位，窗口边框再跟上（逐步揭示）
        #   · 最小化     → 冻结成当前尺寸，内容保持原样被窗口裁剪（收缩渐隐）
        freeze_size = self.geometry().size() if state == "minimized" else target.size()
        self._freeze_content(freeze_size)
        # 内容在动画期间是静态的，告诉 Qt 只需重绘新暴露的区域（实测 +10% 帧率）
        self.setAttribute(Qt.WidgetAttribute.WA_StaticContents, True)

        animation = QPropertyAnimation(self, b"geometry", self)
        animation.setDuration(int(duration))
        animation.setStartValue(self.geometry())
        animation.setEndValue(target)
        animation.setEasingCurve(
            QEasingCurve.Type.OutCubic if state != "minimized" else QEasingCurve.Type.InCubic
        )
        animation.finished.connect(self._finish_transition)
        self._animation = animation

        if fade:
            # 仅用于最小化：几何收缩的同时淡出，掩盖小尺寸下的重绘毛刺
            opacity = QPropertyAnimation(self, b"windowOpacity", self)
            opacity.setDuration(int(duration))
            opacity.setStartValue(1.0)
            opacity.setEndValue(0.0)
            opacity.setEasingCurve(QEasingCurve.Type.InCubic)
            self._opacity_animation = opacity
            opacity.start()

        animation.start()

    def _freeze_content(self, size: QSize) -> None:
        central = self.centralWidget()
        if central is None:
            return
        if not self._content_frozen:
            self._content_size_backup = (central.minimumSize(), central.maximumSize())
            self._content_frozen = True
        central.setFixedSize(size)

    def _unfreeze_content(self) -> None:
        # 恢复常规重绘策略：静态内容假设只在动画期间成立
        if self.testAttribute(Qt.WidgetAttribute.WA_StaticContents):
            self.setAttribute(Qt.WidgetAttribute.WA_StaticContents, False)
            self.update()
        if not self._content_frozen:
            return
        central = self.centralWidget()
        if central is not None:
            minimum, maximum = self._content_size_backup
            central.setMinimumSize(minimum)
            central.setMaximumSize(maximum)
            # 解开固定尺寸后必须主动触发一次布局，否则 central widget 会停在
            # 动画结束时的小尺寸上（窗口最小化时尤其明显，因为不会再有布局事件）。
            central.updateGeometry()
            layout = self.layout()
            if layout is not None:
                layout.activate()
        self._content_frozen = False

    def _finish_transition(self) -> None:
        state = self._pending_state
        self._pending_state = None
        self._animating = False
        self._animation = None

        if state == "maximized":
            self.setWindowState(self.windowState() | Qt.WindowState.WindowMaximized)
        elif state == "normal":
            self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMaximized)
        elif state == "minimized":
            self._opacity_animation = None
            self.setWindowOpacity(1.0)
            # 先把几何复位，避免还原时窗口停在收缩后的小矩形
            if self._restore_geometry is not None:
                self.setGeometry(self._restore_geometry)
            self.showMinimized()
        self._unfreeze_content()
        self._sync_state()

    def _stop_transition(self) -> None:
        """中途打断动画（例如用户拖动窗口）。"""
        if self._animation is not None:
            self._animation.stop()
            self._animation = None
        if self._opacity_animation is not None:
            self._opacity_animation.stop()
            self._opacity_animation = None
        self._pending_state = None
        self._animating = False
        self.setWindowOpacity(1.0)
        self._unfreeze_content()
        self._apply_corners()

    def _animations_enabled(self) -> bool:
        """是否启用过渡动画（默认关闭，设置里可开启）。"""
        try:
            from ..config import config_manager

            return bool(getattr(config_manager.config, "ui_animations", False))
        except Exception:  # noqa: BLE001
            return False

    # -- 窗口操作 ----------------------------------------------------------
    def toggle_maximize(self) -> None:
        if self._animating:
            return
        if self.isFullScreen():
            self.toggle_fullscreen()
            return
        if self.isMinimized():
            # 从「最小化」直接切最大化：先恢复显示，否则 showMaximized 不会生效
            self.showNormal()

        if not self._animations_enabled():
            # 关闭动画时不走过渡，但顺序很关键：先把几何摆到位，再声明窗口状态。
            # 反过来的话，setGeometry 会清掉系统的 WS_MAXIMIZE 标志，
            # 导致 isMaximized() 变回 False（按钮图标与圆角就会错）。
            if self.isMaximized():
                target = self._restore_geometry or self._default_restore_geometry()
                self.setGeometry(target)
                self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMaximized)
            else:
                self._restore_geometry = self.geometry()
                self.setGeometry(self._work_area())
                self.setWindowState(self.windowState() | Qt.WindowState.WindowMaximized)
            self._sync_state()
            return
        if self.isMaximized():
            target = self._restore_geometry or self._default_restore_geometry()
            self._start_transition(target, "normal", duration=self.TRANSITION_MS)
        else:
            self._restore_geometry = self.geometry()
            self._start_transition(self._work_area(), "maximized", duration=self.TRANSITION_MS)

    def _default_restore_geometry(self) -> QRect:
        """没有记录到还原位置时的兜底（例如从 Snap 状态还原）。"""
        area = self._work_area()
        width = min(self.maximumWidth() if self.maximumWidth() < 16777215 else 1380, area.width())
        height = min(880, area.height())
        return QRect(
            area.x() + (area.width() - width) // 2,
            area.y() + (area.height() - height) // 2,
            width,
            height,
        )

    def minimize_window(self) -> None:
        if self._animating or self.isMinimized():
            return
        if not self._animations_enabled():
            self.showMinimized()
            return
        self._restore_geometry = self.geometry()
        self._start_transition(
            self._minimize_target(), "minimized", duration=self.MINIMIZE_MS, fade=True
        )

    def toggle_fullscreen(self) -> None:
        if self._animating:
            return
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()
        self._sync_state()

    # -- 圆角 -------------------------------------------------------------
    def _apply_corners(self) -> None:
        hwnd = hwnd_of(self)
        if not hwnd:
            return
        p = palette(current_theme())
        if self.isMaximized() or self.isFullScreen():
            # 贴边时不应该有圆角，否则屏幕角落会露出缝隙
            set_dwm_corner(hwnd, DWMWCP_DONOTROUND)
            clear_round_region(hwnd)
            self._dwm_rounded = True
        else:
            self._dwm_rounded = set_dwm_corner(hwnd, DWMWCP_ROUND)
            if not self._dwm_rounded:
                # Windows 10 没有 DWM 圆角，退回窗口区域裁剪
                apply_round_region(hwnd, self.width(), self.height(), self.CORNER_RADIUS)
        set_dwm_border_color(hwnd, p.border)
        self._corners_applied = True

    def refresh_corners(self) -> None:
        """主题或尺寸变化后重新应用圆角与边框色。"""
        self._apply_corners()

    # -- 状态同步 ---------------------------------------------------------
    def _sync_state(self) -> None:
        maximized = self.isMaximized()
        fullscreen = self.isFullScreen()
        if self.title_bar is not None:
            self.title_bar.controls.set_window_state(maximized=maximized, fullscreen=fullscreen)
        self._apply_corners()
        self.state_changed.emit(maximized, fullscreen)

    # -- Qt 事件 -----------------------------------------------------------
    def showEvent(self, event: Any) -> None:  # noqa: N802
        super().showEvent(event)
        hwnd = hwnd_of(self)
        if hwnd and not self._native_frame:
            # 补上 WS_THICKFRAME / WS_MAXIMIZEBOX 等样式位：
            # 这是 Snap 与系统过渡动画生效的前提，同时带来 DWM 阴影
            self._native_frame = enable_native_frame(hwnd, True)
        self._apply_corners()
        self._sync_state()

    # -- 系统拖拽 ----------------------------------------------------------
    def start_native_move(self) -> bool:
        """进入系统移动循环（等价于按住原生标题栏拖动）。

        直接发送 ``WM_NCLBUTTONDOWN + HTCAPTION``：Aero Snap、贴边分屏、
        拖到顶部呼出 Snap 布局选择器等行为全部由系统接管。
        ``SendMessage`` 会阻塞到用户松开鼠标，期间由系统内部消息循环驱动，
        界面照常重绘。
        """
        if not IS_WINDOWS:
            return False
        hwnd = hwnd_of(self)
        if not hwnd:
            return False
        self._stop_transition()  # 用户开始拖动时放弃进行中的过渡
        try:
            user32 = ctypes.windll.user32
            user32.ReleaseCapture()
            user32.SendMessageW(wintypes.HWND(hwnd), WM_NCLBUTTONDOWN, HTCAPTION, 0)
            # 拖拽可能让窗口吸附成最大化 / 半屏，需要同步按钮与圆角
            self._sync_state()
            return True
        except Exception:  # noqa: BLE001
            log.debug("系统拖拽调用失败", exc_info=True)
            return False

    def changeEvent(self, event: Any) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            self._sync_state()

    def resizeEvent(self, event: Any) -> None:  # noqa: N802
        super().resizeEvent(event)
        # 只有 Windows 10 的 SetWindowRgn 降级路径需要跟随尺寸重设区域；
        # DWM 圆角由系统维护，无需处理（否则会造成拖拽调整大小时闪烁）。
        if (
            IS_WINDOWS
            and self._corners_applied
            and not self._dwm_rounded
            and not self._animating
            and not self.isMaximized()
            and not self.isFullScreen()
        ):
            apply_round_region(hwnd_of(self), self.width(), self.height(), self.CORNER_RADIUS)

    # -- 原生消息 ----------------------------------------------------------
    def nativeEvent(self, eventType: Any, message: Any) -> Any:  # noqa: N802
        if IS_WINDOWS:
            try:
                if bytes(eventType) == b"windows_generic_MSG":
                    msg = _message_from(message)
                    result = self._handle_native(msg)
                    if result is not None:
                        return True, result
            except Exception:  # noqa: BLE001 - 原生消息处理绝不能让应用崩溃
                log.debug("原生消息处理异常", exc_info=True)
        return super().nativeEvent(eventType, message)

    def _handle_native(self, msg: Any) -> int | None:
        message_id = msg.message

        if message_id == WM_NCHITTEST:
            return self._hit_test(msg.lParam)

        if message_id == WM_NCCALCSIZE and int(msg.wParam):
            # 客户区 = 整个窗口：把 WS_THICKFRAME 带来的系统边框让给 Qt，
            # 否则界面会四周内缩约 8px。阴影与圆角仍由 DWM 绘制。
            return 0

        if message_id == WM_NCLBUTTONDOWN and int(msg.wParam) == HTMAXBUTTON:
            self.toggle_maximize()
            return 0

        if message_id == WM_NCMOUSEMOVE:
            x, y = _lparam_point(msg.lParam)
            self._update_nc_hover(self.mapFromGlobal(QPoint(x, y)))
            return None

        if message_id == WM_NCMOUSELEAVE:
            self._update_nc_hover(None)
            return None

        if message_id == WM_GETMINMAXINFO:
            self._apply_work_area(msg.lParam)
            return 0

        return None

    def _hit_test(self, lparam: int) -> int:
        """决定鼠标落在窗口的哪个部位：边框 / 自定义按钮 / 客户区。"""
        x, y = _lparam_point(lparam)
        pos = self.mapFromGlobal(QPoint(x, y))

        # 1) 自定义的「非客户区按钮」——让 Windows 11 在其上呼出 Snap 布局
        for widget, code in self._hit_zones:
            try:
                if widget.isVisible() and widget.rect().contains(widget.mapFrom(self, pos)):
                    return code
            except RuntimeError:  # pragma: no cover - 控件已销毁
                continue

        # 2) 边缘调整大小（最大化 / 全屏时不参与）
        if not self.isMaximized() and not self.isFullScreen():
            border = self.RESIZE_BORDER
            width, height = self.width(), self.height()
            left = pos.x() < border
            right = pos.x() >= width - border
            top = pos.y() < border
            bottom = pos.y() >= height - border
            if top and left:
                return HTTOPLEFT
            if top and right:
                return HTTOPRIGHT
            if bottom and left:
                return HTBOTTOMLEFT
            if bottom and right:
                return HTBOTTOMRIGHT
            if left:
                return HTLEFT
            if right:
                return HTRIGHT
            if top:
                return HTTOP
            if bottom:
                return HTBOTTOM

        # 3) 其余交回 Qt 处理鼠标事件
        return HTCLIENT

    def _update_nc_hover(self, pos: QPoint | None) -> None:
        """在系统接管按钮区域时，手动维持按钮的 hover 视觉。"""
        target: QWidget | None = None
        if pos is not None:
            for widget, _code in self._hit_zones:
                try:
                    if widget.isVisible() and widget.rect().contains(widget.mapFrom(self, pos)):
                        target = widget
                        break
                except RuntimeError:  # pragma: no cover
                    continue
        if target is self._nc_hovered:
            return
        if self._nc_hovered is not None and hasattr(self._nc_hovered, "set_nc_hover"):
            self._nc_hovered.set_nc_hover(False)  # type: ignore[attr-defined]
        self._nc_hovered = target
        if target is not None and hasattr(target, "set_nc_hover"):
            target.set_nc_hover(True)  # type: ignore[attr-defined]

    def _apply_work_area(self, lparam: int) -> None:
        """最大化时贴合显示器工作区，避免遮住任务栏。"""
        try:
            info = ctypes.cast(lparam, ctypes.POINTER(MINMAXINFO)).contents
            hwnd = hwnd_of(self)
            monitor = ctypes.windll.user32.MonitorFromWindow(
                wintypes.HWND(hwnd), 2  # MONITOR_DEFAULTTONEAREST
            )
            monitor_info = MONITORINFO()
            monitor_info.cbSize = ctypes.sizeof(MONITORINFO)
            if not ctypes.windll.user32.GetMonitorInfoW(monitor, ctypes.byref(monitor_info)):
                return
            work = monitor_info.rcWork
            screen = monitor_info.rcMonitor
            info.ptMaxPosition.x = work.left - screen.left
            info.ptMaxPosition.y = work.top - screen.top
            info.ptMaxSize.x = work.right - work.left
            info.ptMaxSize.y = work.bottom - work.top
            # 注意：不要动 ptMaxTrackSize —— 那是手动拖拽的尺寸上限，
            # 限制成工作区会导致窗口无法跨显示器拉伸。
        except Exception:  # noqa: BLE001
            log.debug("WM_GETMINMAXINFO 处理失败", exc_info=True)


# 让顶栏与其子控件成为「构建宿主」，避免内部裸控件闪现
for _cls in (WindowControlButton, WindowControls, TitleBar):
    guarded_init(_cls)
del _cls
