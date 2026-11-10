"""桌面壳：一个 WebView2 窗口 + 一个只监听回环的接口服务。

窗口只是显示层，业务仍在本进程里：``stent/services`` 与 ``stent/platforms``
由它直接调用，接口层负责把这些能力暴露给页面（见 docs/web-architecture.md）。

用法::

    python -m stent.webapp              # 桌面窗口（需要 pywebview + WebView2 运行时）
    python -m stent.webapp --browser    # 只起服务，用系统浏览器调试前端
    python -m stent.webapp --port 8765  # 固定端口，方便前端热调
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import time
import webbrowser
from collections.abc import Sequence
from typing import Any

from . import paths
from .api import build_app
from .window_snap import (
    DragState,
    SnapSession,
    edge_set,
    preview_shapes,
    rect_size,
    resize_rect,
)

log = logging.getLogger(__name__)

WINDOW_TITLE = "Stent"
WINDOW_SIZE = (1380, 854)
# 手动拖边的下限（CSS 像素）。刻意不交给 pywebview 的 min_size：WinForms 的
# MinimumSize 会连吸附结果一起夹住，而 1080p 的一半只有 960 宽、四分之一只有
# 540 高，比这个下限还小——那样贴边分屏会被撑大并压到任务栏上。
# 现在下限只作用于用户手动拖边（见 _NativeWindow._min_size），吸附与最大化
# 是刻意的布局动作，不受它约束。
MIN_SIZE = (1080, 700)
BACKGROUND = "#F6F5FA"

_IS_WINDOWS = sys.platform == "win32"

# --------------------------------------------------------------------------
# 原生窗口（Win32）
# --------------------------------------------------------------------------
# 无边框 + 自绘标题栏意味着窗口行为得自己做：
#
# * 拖动：逐帧把位移回传给 Python 会慢半拍（每一帧都过一次 JS→Python 的桥），
#   交给系统的移动循环又走不通（鼠标捕获在 WebView2 的子窗口上，属于另一个
#   进程的线程，``ReleaseCapture`` 跨线程无效）。所以这里让 Python 直接按光标
#   摆窗口：120Hz 轮询，中间没有任何往返。
# * 代价：自己没有移动循环，系统的 Aero Snap 也不会发生。贴边分屏与 Snap
#   选择器因此由 ``window_snap`` 算、由 ``_SnapOverlay`` 画、在这里落地。
# * 还原：pywebview 的 ``Window`` 没有 ``maximized`` 属性（只有 ``state``），
#   拿它判断「能不能还原」永远是 False，于是窗口只能最大化、再也回不去。
# * 动画：无边框窗口拿不到系统的 zoom 动画（那是带标题栏的窗口才有的效果），
#   这里自己把窗口矩形动起来。

_SWP_NOSIZE = 0x0001
_SWP_NOZORDER = 0x0004
_SWP_NOACTIVATE = 0x0010
_SW_MAXIMIZE = 3
_SW_MINIMIZE = 6
_SW_RESTORE = 9
_SW_HIDE = 0
_SW_SHOWNOACTIVATE = 4
_VK_LBUTTON = 0x01
_MONITOR_DEFAULTTONEAREST = 0x00000002

# 预览层：无边框顶窗 + 分层透明
_WS_POPUP = 0x80000000
_WS_EX_LAYERED = 0x00080000
_WS_EX_TRANSPARENT = 0x00000020  # 鼠标穿透：预览层绝不能挡住拖动与点击
_WS_EX_TOOLWINDOW = 0x00000080
_WS_EX_TOPMOST = 0x00000008
_WS_EX_NOACTIVATE = 0x08000000
_LWA_COLORKEY = 0x00000001
_LWA_ALPHA = 0x00000002
_WM_PAINT = 0x000F
_WM_ERASEBKGND = 0x0014
_WM_MOUSEACTIVATE = 0x0021
_WM_DESTROY = 0x0002
_MA_NOACTIVATE = 3
_CS_HREDRAW = 0x0002
_CS_VREDRAW = 0x0001
_IDC_ARROW = 32512
_HWND_TOPMOST = -1

# 深度上取「标题栏中部」：从最大化 / 吸附态拖出来时，让标题栏继续压在光标下。
# 前端标题栏高 46px，取中值即可，误差不超过半个标题栏。
_TITLEBAR_GRIP = 23

# 预览层的配色。这里写死色值是必要的例外：它画在 Win32 窗口上，不是页面，
# 拿不到 app.css 的 ``--stent-*`` 变量；而且 COLORREF 是 0x00BBGGRR 序，
# 与 CSS 的 #RRGGBB 相反，混用会串色。
_COLOR_KEY = 0x00FF00FF  # 洋红做透明色键，正常配色里不会用到它
_PREVIEW_ACCENT = 0x00E65C7C  # #7C5CE6
_PREVIEW_WHITE = 0x00FFFFFF
_PREVIEW_CARD = 0x002E1B1F  # 近黑的卡片底，深浅主题下都能压住背景
_PREVIEW_SLOT = 0x008A85A0  # 缩略图里的空闲分区
_PREVIEW_ALPHA = 112

_user32_cached: Any = None
_gdi32_cached: Any = None


if _IS_WINDOWS:
    import ctypes
    import ctypes.wintypes as wt

    class _RECT(ctypes.Structure):
        _fields_ = [
            ("left", wt.LONG),
            ("top", wt.LONG),
            ("right", wt.LONG),
            ("bottom", wt.LONG),
        ]

    class _WINDOWPLACEMENT(ctypes.Structure):
        """``GetWindowPlacement`` 的结果：还原点记在 ``rcNormalPosition``。"""

        _fields_ = [
            ("length", wt.UINT),
            ("flags", wt.UINT),
            ("showCmd", wt.UINT),
            ("ptMinPosition", wt.POINT),
            ("ptMaxPosition", wt.POINT),
            ("rcNormalPosition", _RECT),
        ]

    class _MONITORINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wt.DWORD),
            ("rcMonitor", _RECT),
            ("rcWork", _RECT),
            ("dwFlags", wt.DWORD),
        ]

    class _PAINTSTRUCT(ctypes.Structure):
        _fields_ = [
            ("hdc", wt.HDC),
            ("fErase", wt.BOOL),
            ("rcPaint", _RECT),
            ("fRestore", wt.BOOL),
            ("fIncUpdate", wt.BOOL),
            ("rgbReserved", ctypes.c_byte * 32),
        ]

    _WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)

    class _WNDCLASS(ctypes.Structure):
        _fields_ = [
            ("style", wt.UINT),
            ("lpfnWndProc", _WNDPROC),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", wt.HINSTANCE),
            ("hIcon", wt.HICON),
            ("hCursor", wt.HANDLE),
            ("hbrBackground", wt.HBRUSH),
            ("lpszMenuName", wt.LPCWSTR),
            ("lpszClassName", wt.LPCWSTR),
        ]

    def _user32() -> Any:
        """取一份**私有**的 user32 并一次性声明签名。

        两个理由：``argtypes`` 挂在函数对象上，改写共享的 ``ctypes.windll.user32``
        会连带影响同进程里其它用 ctypes 调 user32 的代码（测试里就被坑过）；
        另外不声明签名时 64 位下句柄有被截断的风险。
        """
        global _user32_cached
        if _user32_cached is not None:
            return _user32_cached
        u = ctypes.WinDLL("user32")
        u.SetWindowPos.argtypes = [
            wt.HWND,
            wt.HWND,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint,
        ]
        u.SetWindowPos.restype = wt.BOOL
        u.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
        u.ShowWindow.restype = wt.BOOL
        u.IsZoomed.argtypes = [wt.HWND]
        u.IsZoomed.restype = wt.BOOL
        u.IsWindow.argtypes = [wt.HWND]
        u.IsWindow.restype = wt.BOOL
        u.IsWindowVisible.argtypes = [wt.HWND]
        u.IsWindowVisible.restype = wt.BOOL
        u.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(_RECT)]
        u.GetWindowRect.restype = wt.BOOL
        u.GetWindowPlacement.argtypes = [wt.HWND, ctypes.POINTER(_WINDOWPLACEMENT)]
        u.MonitorFromWindow.argtypes = [wt.HWND, wt.DWORD]
        u.MonitorFromWindow.restype = wt.HANDLE
        u.MonitorFromPoint.argtypes = [wt.POINT, wt.DWORD]
        u.MonitorFromPoint.restype = wt.HANDLE
        u.GetMonitorInfoW.argtypes = [wt.HANDLE, ctypes.POINTER(_MONITORINFO)]
        u.GetAsyncKeyState.argtypes = [ctypes.c_int]
        u.GetAsyncKeyState.restype = ctypes.c_short
        u.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
        u.GetCursorPos.restype = wt.BOOL
        u.GetDpiForWindow.argtypes = [wt.HWND]
        u.GetDpiForWindow.restype = wt.UINT
        u.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
        u.GetWindowTextLengthW.argtypes = [wt.HWND]
        u.EnumWindows.argtypes = [ctypes.c_void_p, wt.LPARAM]
        # -- 预览层用到的 --
        u.RegisterClassW.argtypes = [ctypes.POINTER(_WNDCLASS)]
        u.RegisterClassW.restype = wt.ATOM
        u.CreateWindowExW.argtypes = [
            wt.DWORD,
            wt.LPCWSTR,
            wt.LPCWSTR,
            wt.DWORD,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wt.HWND,
            wt.HMENU,
            wt.HINSTANCE,
            ctypes.c_void_p,
        ]
        u.CreateWindowExW.restype = wt.HWND
        u.DestroyWindow.argtypes = [wt.HWND]
        u.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
        u.DefWindowProcW.restype = ctypes.c_ssize_t
        u.SetLayeredWindowAttributes.argtypes = [wt.HWND, wt.DWORD, ctypes.c_byte, wt.DWORD]
        u.SetLayeredWindowAttributes.restype = wt.BOOL
        u.BeginPaint.argtypes = [wt.HWND, ctypes.POINTER(_PAINTSTRUCT)]
        u.BeginPaint.restype = wt.HDC
        u.EndPaint.argtypes = [wt.HWND, ctypes.POINTER(_PAINTSTRUCT)]
        u.InvalidateRect.argtypes = [wt.HWND, ctypes.POINTER(_RECT), wt.BOOL]
        u.UpdateWindow.argtypes = [wt.HWND]
        u.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
        u.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
        u.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
        u.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]
        u.DispatchMessageW.restype = ctypes.c_ssize_t
        u.PostQuitMessage.argtypes = [ctypes.c_int]
        u.LoadCursorW.argtypes = [wt.HINSTANCE, ctypes.c_void_p]
        u.LoadCursorW.restype = wt.HANDLE
        _user32_cached = u
        return u

    def _gdi32() -> Any:
        global _gdi32_cached
        if _gdi32_cached is not None:
            return _gdi32_cached
        g = ctypes.WinDLL("gdi32")
        g.CreateSolidBrush.argtypes = [wt.DWORD]
        g.CreateSolidBrush.restype = wt.HBRUSH
        g.FillRect.argtypes = [wt.HDC, ctypes.POINTER(_RECT), wt.HBRUSH]
        g.FillRect.restype = ctypes.c_int
        g.FrameRect.argtypes = [wt.HDC, ctypes.POINTER(_RECT), wt.HBRUSH]
        g.FrameRect.restype = ctypes.c_int
        _gdi32_cached = g
        return g

    def _module_handle() -> Any:
        return ctypes.WinDLL("kernel32").GetModuleHandleW(None)

    def _find_window_handle(pid: int) -> int:
        """找本进程的宿主窗口：可见、有标题、面积最大的那个。

        不按标题匹配：页面切页会改 ``document.title``，窗口标题跟着变，
        按标题找会在切过页之后失败。宿主窗口是本进程里面积最大的可见窗口。
        """
        u = _user32()
        best = [0, 0]

        @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
        def enum_proc(hwnd: int, _lparam: int) -> bool:
            owner = wt.DWORD()
            u.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if owner.value != pid or not u.IsWindowVisible(hwnd):
                return True
            if u.GetWindowTextLengthW(hwnd) <= 0:
                return True
            rect = _RECT()
            u.GetWindowRect(hwnd, ctypes.byref(rect))
            area = (rect.right - rect.left) * (rect.bottom - rect.top)
            if area > best[1]:
                best[0], best[1] = int(hwnd), area
            return True

        u.EnumWindows(ctypes.cast(enum_proc, ctypes.c_void_p), 0)
        return best[0]


class _SnapOverlay:
    """吸附预览层：一个覆盖目标显示器、鼠标可穿透的分层窗口。

    为什么不用页面里的 div：预览区域往往比窗口本身还大（半屏、整屏），
    画在页面里会被窗口边界裁掉；而且拖动时窗口本身在动，页面跟着抖。

    透明怎么做：底色刷成洋红并把它设为**色键**（该色像素完全透明），其余像素
    统一按全局 alpha 混合。这样只靠 GDI 的 ``FillRect`` / ``FrameRect`` 就能
    画出半透明区域，不必逐像素算 alpha（那需要读回 DIB，慢一个数量级），
    也不用区域裁剪（那会失去抗锯齿与圆角以外的所有自由度）。
    通篇不允许用洋红做配色，否则那一块会被抠成透明。

    窗口在自己的线程上建、自己跑消息循环：``js_api`` 的调用线程不能被
    消息循环占住，而 Win32 窗口只能在创建它的线程上收消息。
    """

    _CLASS = "StentSnapOverlay"
    _WM_APPLY = 0x8000 + 1  # WM_APP + 1
    _READY_TIMEOUT = 2.0

    def __init__(self) -> None:
        self._hwnd = 0
        self._thread: threading.Thread | None = None
        self._spawn_lock = threading.Lock()
        self._ready = threading.Event()
        self._lock = threading.Lock()
        self._wndproc: Any = None
        self._brushes: dict[int, Any] = {}
        self._pending: tuple[tuple[int, int, int, int], tuple[Any, ...]] | None = None
        self._visible = False
        self._last_key: Any = None

    # -- 对外 ------------------------------------------------------------
    def warm_up(self) -> None:
        """提前把窗口与消息循环建好。

        放到拖动开始那一刻做，而不是等第一次贴边：建窗口要注册窗口类、等 DWM
        给分层窗口分配表面，冷启动几十毫秒，压在拖动循环里就是一次明显的卡顿，
        而且正好卡在预览该出现的那一帧。
        """
        if not _IS_WINDOWS or self._hwnd:
            return
        threading.Thread(target=self._ensure, name="stent-snap-warm", daemon=True).start()

    def preview(self, state: DragState, canvas: tuple[int, int, int, int]) -> None:
        """显示（或更新）``state`` 对应的预览。内容没变就不重绘。"""
        shapes = preview_shapes(state)
        if not shapes:
            self.hide()
            return
        key = (canvas, shapes)
        with self._lock:
            if key == self._last_key:
                return
            self._last_key = key
            self._pending = (canvas, shapes)
            self._visible = True
        self._post()

    def hide(self) -> None:
        with self._lock:
            if not self._visible and self._pending is None:
                return
            self._pending = None
            self._last_key = None
            self._visible = False
        self._post()

    def close(self) -> None:
        """销毁窗口（退出前调用；进程结束时也会自动回收）。"""
        hwnd = self._hwnd
        if not hwnd:
            return
        self._hwnd = 0
        try:
            _user32().PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
        except Exception:  # noqa: BLE001
            log.debug("关闭预览层失败", exc_info=True)

    # -- 线程 ------------------------------------------------------------
    def _post(self) -> None:
        if not self._ensure():
            return
        try:
            _user32().PostMessageW(self._hwnd, self._WM_APPLY, 0, 0)
        except Exception:  # noqa: BLE001
            log.debug("预览层消息投递失败", exc_info=True)

    def _ensure(self) -> bool:
        if not _IS_WINDOWS:
            return False
        if self._hwnd:
            return True
        # warm_up 与拖动循环可能同时进来，窗口只能建一次
        with self._spawn_lock:
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._run, name="stent-snap-overlay", daemon=True
                )
                self._thread.start()
        self._ready.wait(self._READY_TIMEOUT)
        return bool(self._hwnd)

    def _run(self) -> None:
        try:
            u = _user32()
            instance = _module_handle()
            self._wndproc = _WNDPROC(self._on_message)
            wc = _WNDCLASS()
            wc.style = _CS_HREDRAW | _CS_VREDRAW
            wc.lpfnWndProc = self._wndproc
            wc.hInstance = instance
            wc.hCursor = u.LoadCursorW(None, ctypes.c_void_p(_IDC_ARROW))
            wc.hbrBackground = None
            wc.lpszClassName = self._CLASS
            # 类已存在（1410）不算错：重开窗口时正常会发生
            u.RegisterClassW(ctypes.byref(wc))
            style = _WS_EX_LAYERED | _WS_EX_TRANSPARENT | _WS_EX_TOOLWINDOW
            style |= _WS_EX_TOPMOST | _WS_EX_NOACTIVATE
            hwnd = u.CreateWindowExW(
                style,
                self._CLASS,
                "Stent 吸附预览",
                _WS_POPUP,
                0,
                0,
                0,
                0,
                None,
                None,
                instance,
                None,
            )
            if not hwnd:
                log.warning("预览层创建失败：吸附提示将不可见")
                self._ready.set()
                return
            u.SetLayeredWindowAttributes(
                hwnd, _COLOR_KEY, _PREVIEW_ALPHA, _LWA_COLORKEY | _LWA_ALPHA
            )
            self._hwnd = int(hwnd)
            self._ready.set()
            msg = wt.MSG()
            while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                u.TranslateMessage(ctypes.byref(msg))
                u.DispatchMessageW(ctypes.byref(msg))
        except Exception:  # noqa: BLE001
            log.warning("预览层线程异常退出", exc_info=True)
        finally:
            self._ready.set()
            self._hwnd = 0

    # -- 消息处理（都在预览层线程上） --------------------------------------
    def _on_message(self, hwnd: int, msg: int, wparam: int, lparam: int) -> int:
        try:
            if msg == self._WM_APPLY:
                self._apply(hwnd)
                return 0
            if msg == _WM_PAINT:
                self._paint(hwnd)
                return 0
            if msg == _WM_ERASEBKGND:
                return 1  # 整块由 WM_PAINT 自己铺，别让系统先刷一遍
            if msg == _WM_MOUSEACTIVATE:
                return _MA_NOACTIVATE
            if msg == _WM_DESTROY:
                _user32().PostQuitMessage(0)
                return 0
        except Exception:  # noqa: BLE001
            log.debug("预览层消息处理失败", exc_info=True)
        return _user32().DefWindowProcW(hwnd, msg, wparam, lparam)

    def _apply(self, hwnd: int) -> None:
        u = _user32()
        with self._lock:
            pending = self._pending
            visible = self._visible
        if not visible or pending is None:
            u.ShowWindow(hwnd, _SW_HIDE)
            return
        canvas, _shapes = pending
        width, height = rect_size(canvas)
        # 不带 SWP_NOZORDER：否则 HWND_TOPMOST 被忽略，预览会被应用窗口盖住
        u.SetWindowPos(hwnd, _HWND_TOPMOST, canvas[0], canvas[1], width, height, _SWP_NOACTIVATE)
        u.ShowWindow(hwnd, _SW_SHOWNOACTIVATE)
        u.InvalidateRect(hwnd, None, True)
        u.UpdateWindow(hwnd)

    def _brush(self, color: int) -> Any:
        brush = self._brushes.get(color)
        if brush is None:
            brush = _gdi32().CreateSolidBrush(color)
            self._brushes[color] = brush
        return brush

    def _paint(self, hwnd: int) -> None:
        u = _user32()
        ps = _PAINTSTRUCT()
        hdc = u.BeginPaint(hwnd, ctypes.byref(ps))
        try:
            with self._lock:
                pending = self._pending
            if pending is None:
                return
            canvas, shapes = pending
            width, height = rect_size(canvas)
            client = _RECT(0, 0, width, height)
            _gdi32().FillRect(hdc, ctypes.byref(client), self._brush(_COLOR_KEY))
            for rect, role in shapes:
                local = _RECT(
                    rect[0] - canvas[0],
                    rect[1] - canvas[1],
                    rect[2] - canvas[0],
                    rect[3] - canvas[1],
                )
                self._draw(hdc, local, role)
        finally:
            u.EndPaint(hwnd, ctypes.byref(ps))

    def _draw(self, hdc: int, rect: Any, role: str) -> None:
        g = _gdi32()
        if role == "zone-frame":
            # 2px 白框：深色背景上靠它勾出边界
            for offset in range(2):
                frame = _RECT(
                    rect.left + offset,
                    rect.top + offset,
                    rect.right - offset,
                    rect.bottom - offset,
                )
                g.FrameRect(hdc, ctypes.byref(frame), self._brush(_PREVIEW_WHITE))
            return
        color = {
            "card": _PREVIEW_CARD,
            "slot": _PREVIEW_SLOT,
            "slot-hot": _PREVIEW_ACCENT,
            "zone": _PREVIEW_ACCENT,
        }.get(role, _PREVIEW_ACCENT)
        g.FillRect(hdc, ctypes.byref(rect), self._brush(color))


class _NativeWindow:
    """宿主窗口的原生操作：拖动、最大化/还原（带动画）、最小化。

    句柄按进程查一次并缓存。所有方法在非 Windows 上返回 False / 空操作，
    由调用方退回 pywebview 自己的接口。
    """

    ANIM_MS = 170  # 与系统 zoom 动画的时长接近
    FRAMES = 12

    def __init__(self) -> None:
        self._hwnd = 0
        self._animating = threading.Event()
        self._dragging = threading.Event()
        self._resizing = threading.Event()
        self._snap = SnapSession()
        self._overlay = _SnapOverlay()
        # 吸附 / 最大化会改掉「普通矩形」，拖开时要能还原回用户上一手的样子
        self._snapped = False
        self._normal_before_snap: tuple[int, int, int, int] | None = None

    # -- 句柄 ------------------------------------------------------------
    def handle(self) -> int:
        if not _IS_WINDOWS:
            return 0
        u = _user32()
        if self._hwnd and u.IsWindow(self._hwnd):
            return self._hwnd
        self._hwnd = _find_window_handle(os.getpid())
        return self._hwnd

    def rect(self) -> tuple[int, int, int, int]:
        r = _RECT()
        _user32().GetWindowRect(self.handle(), ctypes.byref(r))
        return (r.left, r.top, r.right, r.bottom)

    def is_maximized(self) -> bool:
        hwnd = self.handle()
        return bool(hwnd and _user32().IsZoomed(hwnd))

    def _monitor_rects(
        self, point: Any
    ) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int]] | None:
        """光标所在显示器的 (整屏矩形, 工作区矩形)。

        贴边判定用工作区（已扣掉任务栏），预览层的画布用整屏矩形——这样贴边的
        区域也能画到屏幕边缘之外的部分。
        """
        u = _user32()
        monitor = u.MonitorFromPoint(point, _MONITOR_DEFAULTTONEAREST)
        if not monitor:
            return None
        info = _MONITORINFO()
        info.cbSize = ctypes.sizeof(_MONITORINFO)
        if not u.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return None
        m, w = info.rcMonitor, info.rcWork
        return (m.left, m.top, m.right, m.bottom), (w.left, w.top, w.right, w.bottom)

    def _work_rect_at(self, point: Any) -> tuple[int, int, int, int] | None:
        rects = self._monitor_rects(point)
        return rects[1] if rects else None

    def _work_rect(self) -> tuple[int, int, int, int] | None:
        """当前显示器的工作区（屏幕坐标）。任务栏在左/上时 ``left`` 不为 0。"""
        u = _user32()
        hwnd = self.handle()
        monitor = u.MonitorFromWindow(hwnd, _MONITOR_DEFAULTTONEAREST)
        if not monitor:
            return None
        info = _MONITORINFO()
        info.cbSize = ctypes.sizeof(_MONITORINFO)
        if not u.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return None
        w = info.rcWork
        return (w.left, w.top, w.right, w.bottom)

    def _placement(self) -> Any:
        placement = _WINDOWPLACEMENT()
        placement.length = ctypes.sizeof(_WINDOWPLACEMENT)
        _user32().GetWindowPlacement(self.handle(), ctypes.byref(placement))
        return placement

    # -- 输入读取 ---------------------------------------------------------
    # 拖动与缩放都是「轮询光标 + 轮询左键」，这两个读取单独成方法是为了
    # tests/selftest_snap.py 能换成脚本化的输入源：否则一条 8ms 轮询的循环
    # 在测试里只能靠注入真实鼠标去跑，结果不可复现。
    def _cursor_point(self) -> Any:
        point = wt.POINT()
        _user32().GetCursorPos(ctypes.byref(point))
        return point

    def _left_button_down(self) -> bool:
        return bool(_user32().GetAsyncKeyState(_VK_LBUTTON) & 0x8000)

    # -- 拖动 ------------------------------------------------------------
    def start_drag(self, x: int, y: int) -> bool:
        """跟着光标移动窗口，直到松开左键；一路判定贴边并显示吸附预览。

        为什么不走「系统自己的移动循环」（``SendMessage(WM_NCLBUTTONDOWN,
        HTCAPTION)``）：这条窗口是 WinForms 的无边框 ``WS_POPUP``，鼠标按下时捕获在
        WebView2 的子窗口上、属于另一个进程的线程，``ReleaseCapture`` 跨线程无效，
        移动循环拿不到后续的鼠标移动，实测窗口纹丝不动。

        也不逐帧回传位移（旧实现）：每一帧都要过一次 JS→Python 的桥，慢半拍。
        这里在 Python 侧直接按光标位置摆窗口，中间没有任何往返：
        120Hz 轮询，肉眼就是跟手的。

        代价是拿不到系统的 Aero Snap——所以吸附判定（``window_snap``）与预览
        （``_SnapOverlay``）都在这条循环里补齐，松手时才真正落位。
        """
        if not _IS_WINDOWS:
            return False
        hwnd = self.handle()
        if not hwnd:
            return False
        if self._dragging.is_set() or self._resizing.is_set():
            return True
        self._dragging.set()

        def run() -> None:
            u = _user32()
            state = DragState()
            try:
                point = self._cursor_point()
                # 按下点优先用前端给的坐标——它比「收到调用时的光标」更准，
                # 后者可能已经被用户拖出了几像素。
                press_x = int(x) or point.x
                press_y = int(y) or point.y
                rect = self._restore_for_drag(press_x, press_y)
                # 按下点与窗口左上角的偏移：整段拖动都保持这个偏移
                offset_x = rect[0] - press_x
                offset_y = rect[1] - press_y
                self._snap.reset()
                # 先把预览层建好：贴边往往就在几十毫秒后，那时再建会卡一帧
                self._overlay.warm_up()
                while self._left_button_down():
                    point = self._cursor_point()
                    rects = self._monitor_rects(point)
                    if rects is not None:
                        monitor, work = rects
                        state = self._snap.update((point.x, point.y), work)
                        self._overlay.preview(state, monitor)
                    u.SetWindowPos(
                        hwnd,
                        None,
                        point.x + offset_x,
                        point.y + offset_y,
                        0,
                        0,
                        _SWP_NOSIZE | _SWP_NOZORDER | _SWP_NOACTIVATE,
                    )
                    time.sleep(0.008)
            except Exception:  # noqa: BLE001
                log.debug("拖动失败", exc_info=True)
            finally:
                self._overlay.hide()
                self._snap.reset()
                try:
                    self._settle_after_drag(hwnd, state)
                except Exception:  # noqa: BLE001
                    log.debug("吸附落位失败", exc_info=True)
                finally:
                    self._dragging.clear()

        threading.Thread(target=run, daemon=True).start()
        return True

    def _restore_for_drag(self, press_x: int, press_y: int) -> tuple[int, int, int, int]:
        """把窗口从「最大化 / 已吸附」拉回普通矩形，返回新的矩形。

        不做这件事的后果：抓住最大化窗口的标题栏去拖，移动的是一整块铺满工作区
        的窗口；吸附过的窗口也一样——用户以为能拖开，实际是连着半屏一起挪。
        """
        rect = self.rect()
        zoomed = self.is_maximized()
        if not zoomed and not self._snapped:
            return rect
        if zoomed:
            # 退出最大化后 rcNormalPosition 就是普通矩形，交给系统算比自己记可靠
            _user32().ShowWindow(self.handle(), _SW_RESTORE)
            time.sleep(0.03)
            target = self.rect()
        else:
            target = self._normal_before_snap or rect
        self._snapped = False
        width, height = rect_size(target)
        # 光标在旧矩形里的相对横向位置，映射到新宽度上：抓在 30% 处，展开后还在 30% 处
        span = max(1, rect[2] - rect[0])
        ratio = min(max((press_x - rect[0]) / span, 0.0), 1.0)
        left = press_x - round(width * ratio)
        top = press_y - _TITLEBAR_GRIP
        # 夹回工作区：上面这套比例换算是按光标算的，抓在靠边处会把标题栏算到屏幕
        # 外面去（y 变成负数），窗口就再也拖不回来了。
        work = self._work_rect() or rect
        left = min(max(left, work[0]), max(work[0], work[2] - width))
        top = min(max(top, work[1]), max(work[1], work[3] - height))
        u = _user32()
        u.SetWindowPos(
            self.handle(),
            None,
            left,
            top,
            width,
            height,
            _SWP_NOZORDER | _SWP_NOACTIVATE,
        )
        return (left, top, left + width, top + height)

    def _settle_after_drag(self, hwnd: int, state: DragState) -> None:
        """松手：有目标就吸附过去；顶到上沿但没选区域就最大化。"""
        if state.rect is not None:
            self.apply_snap(state.rect)
        elif state.mode == "picker":
            self._animating.set()
            try:
                self._maximize(hwnd)
            finally:
                self._animating.clear()

    def apply_snap(self, rect: tuple[int, int, int, int]) -> bool:
        """把窗口贴到 ``rect``（工作区算出来的目标区域）。"""
        if not _IS_WINDOWS:
            return False
        hwnd = self.handle()
        if not hwnd:
            return False
        u = _user32()
        if u.IsZoomed(hwnd):
            # 最大化状态下 SetWindowPos 会被顶回工作区，必须先落状态
            u.ShowWindow(hwnd, _SW_RESTORE)
            time.sleep(0.03)
        if not self._snapped:
            self._normal_before_snap = self.rect()
        self._snapped = True
        width, height = rect_size(rect)
        u.SetWindowPos(hwnd, None, rect[0], rect[1], width, height, _SWP_NOZORDER | _SWP_NOACTIVATE)
        return True

    def _min_size(self) -> tuple[int, int]:
        """拖边缩放的物理像素下限。

        ``MIN_SIZE`` 是 CSS 像素（界面按它排版），物理像素要按窗口 DPI 放大，
        否则 150% 缩放的屏幕上窗口会被拖得比设计稿还小、界面压叠。
        """
        scale = 1.0
        try:
            dpi = _user32().GetDpiForWindow(self.handle())
            if dpi:
                scale = dpi / 96.0
        except Exception:  # noqa: BLE001 - 老系统没有这个接口，按 100% 处理
            scale = 1.0
        return round(MIN_SIZE[0] * scale), round(MIN_SIZE[1] * scale)

    # -- 拖边调整大小 ------------------------------------------------------
    def start_resize(self, edge: str, x: int, y: int) -> bool:
        """按住窗口边缘拖动改尺寸，直到松开左键。

        同样在 Python 侧按光标摆窗口：无边框窗口没有非客户区，系统不会替我们
        起缩放的移动循环。边界一律夹在光标所在显示器的工作区里，所以拖到底也
        不会盖住任务栏。
        """
        if not _IS_WINDOWS:
            return False
        edges = edge_set(edge)
        hwnd = self.handle()
        if not hwnd or not edges:
            return False
        if self.is_maximized() or self._dragging.is_set():
            return False  # 最大化时不允许改尺寸，与系统行为一致
        if self._resizing.is_set():
            return True
        self._resizing.set()
        self._snapped = False
        self._normal_before_snap = None
        self._overlay.hide()

        def run() -> None:
            u = _user32()
            try:
                point = self._cursor_point()
                press_x = int(x) or point.x
                press_y = int(y) or point.y
                start = self.rect()
                min_size = self._min_size()
                while self._left_button_down():
                    point = self._cursor_point()
                    bounds = self._work_rect_at(point) or start
                    rect = resize_rect(
                        start,
                        edges,
                        point.x - press_x,
                        point.y - press_y,
                        min_size,
                        bounds,
                    )
                    width, height = rect_size(rect)
                    u.SetWindowPos(
                        hwnd,
                        None,
                        rect[0],
                        rect[1],
                        width,
                        height,
                        _SWP_NOZORDER | _SWP_NOACTIVATE,
                    )
                    time.sleep(0.008)
            except Exception:  # noqa: BLE001
                log.debug("拖边调整大小失败", exc_info=True)
            finally:
                self._resizing.clear()

        threading.Thread(target=run, daemon=True).start()
        return True

    # -- 最大化 / 还原 / 最小化 -------------------------------------------
    def toggle_maximize(self) -> bool:
        if not _IS_WINDOWS:
            return False
        hwnd = self.handle()
        if not hwnd:
            return False
        if self._animating.is_set():
            return True  # 动画还在跑，忽略连点（否则两次动画会互相覆盖）
        self._animating.set()
        threading.Thread(target=self._toggle_worker, args=(hwnd,), daemon=True).start()
        return True

    def _toggle_worker(self, hwnd: int) -> None:
        try:
            if _user32().IsZoomed(hwnd):
                self._restore(hwnd)
            else:
                self._maximize(hwnd)
        except Exception:  # noqa: BLE001
            log.debug("窗口最大化切换失败", exc_info=True)
        finally:
            self._animating.clear()

    def _maximize(self, hwnd: int) -> None:
        u = _user32()
        normal = self.rect()
        work = self._work_rect()
        # 最大化后「拖开还原」应该回到这块被最大化的矩形，而不是更早的普通矩形
        self._snapped = False
        self._normal_before_snap = None
        self._overlay.hide()
        if work is None:
            u.ShowWindow(hwnd, _SW_MAXIMIZE)
            return
        # 顺序有讲究：
        # 1) 先让 Windows 进最大化状态——还原点（rcNormalPosition）由它自己记成
        #    当前矩形，这是唯一可靠的方式；SetWindowPlacement 手写 showCmd 在
        #    「窗口已经铺满工作区」时不会真的切状态，还会串用旧的 ptMaxPosition。
        # 2) 立刻把窗口拉回原矩形，让动画从原位置开始（同一 tick 内，不会画出来）。
        # 3) 动画到工作区。zoom 状态在 SetWindowPos 期间保持，结束时正好落位。
        u.ShowWindow(hwnd, _SW_MAXIMIZE)
        u.SetWindowPos(
            hwnd,
            None,
            normal[0],
            normal[1],
            normal[2] - normal[0],
            normal[3] - normal[1],
            _SWP_NOZORDER | _SWP_NOACTIVATE,
        )
        self._animate(work)

    def _restore(self, hwnd: int) -> None:
        work = self._work_rect()
        placement = self._placement()
        if work is not None:
            origin_x, origin_y = work[0], work[1]
            r = placement.rcNormalPosition
            self._animate(
                (r.left + origin_x, r.top + origin_y, r.right + origin_x, r.bottom + origin_y)
            )
        # 动画跑完后窗口已经坐在还原点上，此时清状态不会再跳
        _user32().ShowWindow(hwnd, _SW_RESTORE)

    def _animate(self, target: tuple[int, int, int, int]) -> None:
        """把窗口矩形从当前位置动到 ``target``（先快后慢，接近系统 zoom 手感）。"""
        u = _user32()
        hwnd = self.handle()
        start = self.rect()
        if start == target:
            return
        for index in range(1, self.FRAMES + 1):
            t = index / self.FRAMES
            eased = 1 - (1 - t) ** 3
            rect = [round(a + (b - a) * eased) for a, b in zip(start, target, strict=True)]
            u.SetWindowPos(
                hwnd,
                None,
                rect[0],
                rect[1],
                rect[2] - rect[0],
                rect[3] - rect[1],
                _SWP_NOZORDER | _SWP_NOACTIVATE,
            )
            time.sleep(self.ANIM_MS / 1000 / self.FRAMES)

    def minimize(self) -> bool:
        if not _IS_WINDOWS:
            return False
        hwnd = self.handle()
        if not hwnd:
            return False
        _user32().ShowWindow(hwnd, _SW_MINIMIZE)
        return True


def _has_webview() -> tuple[bool, str]:
    """探测 pywebview 与 WebView2 运行时是否可用。"""
    try:
        import webview  # noqa: F401, PLC0415
    except ImportError:
        return False, "未安装 pywebview（pip install pywebview）"
    except Exception as exc:  # pragma: no cover - 依赖存在但底层缺失
        return False, f"pywebview 加载失败：{exc}"
    return True, ""


class ShellApi:
    """暴露给前端的壳层能力（窗口控制、原生文件选择）。

    只有本机窗口里的页面能调到它——它不经过 HTTP，不存在的端口也就无从访问。
    """

    def __init__(self) -> None:
        # 私有命名很重要：pywebview 会反射 js_api 的公开属性来建 JS 桥，
        # 碰到 Window 对象就会去读 webview 的原生属性（在非 UI 线程上 → 一串 COM 异常）。
        self._window: Any = None
        self._native = _NativeWindow()
        # 非 Windows 上 pywebview 的 Window 没有 maximized 属性，状态只能自己记
        self._fallback_maximized = False

    # -- 窗口 ------------------------------------------------------------
    def minimize(self) -> None:
        if self._native.minimize():
            return
        if self._window is not None:
            self._window.minimize()

    def toggle_maximize(self) -> None:
        """最大化 ↔ 还原。

        旧实现靠 ``getattr(self._window, "maximized", False)`` 判断当前状态，
        而 pywebview 的 Window 上没有这个属性 —— 恒为 False，于是永远只调
        ``maximize()``，点第二下也回不来。改用 Win32 的 IsZoomed 判断，
        并自己做动画（无边框窗口没有系统的 zoom 动画）。
        """
        if self._native.toggle_maximize():
            return
        if self._window is None:
            return
        try:
            if self._fallback_maximized:
                self._window.restore()
            else:
                self._window.maximize()
        except Exception:  # pragma: no cover - 窗口已销毁
            log.debug("窗口最大化切换失败", exc_info=True)
            return
        self._fallback_maximized = not self._fallback_maximized

    def close(self) -> None:
        if self._window is not None:
            self._window.destroy()

    def start_drag(self, x: int = 0, y: int = 0) -> None:
        """自绘标题栏的「按下了」：由壳层按光标摆窗口，直到松开。

        前端只在 pointerdown 时调一次并带上按下点的屏幕坐标，之后每一帧都由
        Python 侧轮询光标、直接 ``SetWindowPos``——不再逐帧回传位移，那必然比
        鼠标慢半拍。贴边吸附也在这条循环里判定，松手才落位。
        """
        if not self._native.start_drag(int(x or 0), int(y or 0)):
            log.debug("原生拖动不可用（非 Windows）")
        else:
            log.debug("原生拖动：按下点 %s,%s", x, y)

    def start_resize(self, edge: str = "", x: int = 0, y: int = 0) -> None:
        """按住了窗口边缘：``edge`` 取 ``n`` / ``s`` / ``e`` / ``w`` 或四角组合。

        前端在视口边缘 5px 内按下时调用。尺寸最终夹在工作区里，不会盖住任务栏；
        最大化状态下直接忽略（与系统一致：铺满屏的窗口没有可拖的边）。
        """
        if not self._native.start_resize(edge, int(x or 0), int(y or 0)):
            log.debug("原生调整大小不可用：edge=%s", edge)

    def open_external(self, url: str) -> bool:
        """用系统浏览器打开外链，不要在应用窗口里跳走。"""
        target = str(url or "").strip()
        if not target.lower().startswith(("http://", "https://")):
            return False
        webbrowser.open(target)
        return True

    # -- 文件 ------------------------------------------------------------
    def pick_files(self, kind: str = "media") -> list[str]:
        """弹原生文件选择框，返回绝对路径数组。

        浏览器做不到这件事（``<input type=file>`` 拿到的是不可读的伪路径），
        所以媒体选择必须由壳层代劳。
        """
        if self._window is None:
            return []
        try:
            import webview  # noqa: PLC0415
        except ImportError:  # pragma: no cover
            return []

        if kind == "image":
            file_types = ("图片 (*.png;*.jpg;*.jpeg;*.webp)", "所有文件 (*.*)")
        else:
            file_types = (
                "媒体文件 (*.png;*.jpg;*.jpeg;*.webp;*.mp4;*.mov)",
                "所有文件 (*.*)",
            )
        try:
            picked = self._window.create_file_dialog(
                webview.OPEN_DIALOG, allow_multiple=True, file_types=file_types
            )
        except Exception:  # pragma: no cover - 对话框被取消或不可用
            log.debug("文件选择失败", exc_info=True)
            return []
        return [str(p) for p in (picked or [])]


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="stent-webapp", description="Stent Web 前端桌面壳")
    parser.add_argument("--browser", action="store_true", help="不起窗口，用系统浏览器打开")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（只允许回环）")
    parser.add_argument("--port", type=int, default=0, help="端口，0 表示由系统分配")
    parser.add_argument(
        "--token",
        default="",
        help="指定访问令牌；不传则随机生成（调试时想固定令牌用）",
    )
    parser.add_argument("--debug", action="store_true", help="打开 WebView 调试工具与调试日志")
    return parser.parse_args(list(argv))


def run(argv: Sequence[str] | None = None) -> int:
    # 日志写 data_dir/logs/stent.log + stderr。打包后 console=False 没有控制台，
    # 只靠 basicConfig 的话日志等于丢失，用户报「白屏」时我们手上什么都没有。
    from .bootstrap import ensure_utf8_console, setup_logging

    ensure_utf8_console()
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    setup_logging(logging.DEBUG if args.debug else logging.INFO)

    if args.host not in {"127.0.0.1", "::1", "localhost"}:
        print("出于安全考虑，只允许监听回环地址（127.0.0.1）", file=sys.stderr)
        return 2

    paths.data_dir()
    # 令牌也可以从环境变量取：想固定令牌做调试时不必改命令行
    token = args.token or os.environ.get("STENT_API_TOKEN") or None
    app = build_app(host=args.host, port=args.port, token=token)
    url = app.start()
    print(f"Stent 接口服务：{url}")
    print(f"数据目录：{paths.data_dir()}")

    ok, reason = _has_webview()
    if args.browser or not ok:
        if not ok and not args.browser:
            print(f"未能启动桌面窗口：{reason}", file=sys.stderr)
            print("已改用系统浏览器打开；装好 pywebview 后会使用独立窗口。", file=sys.stderr)
        webbrowser.open(url)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n正在退出…")
        finally:
            app.stop()
        return 0

    import webview  # noqa: PLC0415 - 惰性导入：没有窗口模式时不该加载它

    shell = ShellApi()
    window = webview.create_window(
        WINDOW_TITLE,
        url,
        width=WINDOW_SIZE[0],
        height=WINDOW_SIZE[1],
        # 下限由壳层自己夹（见 MIN_SIZE 的注释）：交给 WinForms 会连吸附一起夹住
        min_size=(1, 1),
        frameless=True,
        easy_drag=False,
        background_color=BACKGROUND,
        js_api=shell,
    )
    shell._window = window  # noqa: SLF001 - 同一模块内注入窗口引用
    # 有这条日志才能区分「窗口开了」和「页面真的渲染了」——白屏时看日志即可定位
    window.events.loaded += lambda *_: log.info("窗口页面已加载：%s", url)
    try:
        webview.start(debug=args.debug)
    finally:
        app.stop()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return run(argv)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
