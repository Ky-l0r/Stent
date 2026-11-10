"""Web 前端桌面壳：一个 WebView2 窗口 + 一个只监听回环的接口服务。

它替代的是 ``python main.py``（PySide6 界面），但**不替代业务层**：
``stent/services`` 与 ``stent/platforms`` 一行没动，仍然由这个进程直接调用。

用法::

    python -m stent.webapp              # 桌面窗口（需要 pywebview + WebView2 运行时）
    python -m stent.webapp --browser    # 只起服务，用系统浏览器调试前端
    python -m stent.webapp --port 8765  # 固定端口，方便前端热调

与旧界面的关系：``stent/ui/`` 暂时保留，是回退路径与视觉对照物（见 docs/web-architecture.md）。
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

log = logging.getLogger(__name__)

WINDOW_TITLE = "Stent"
WINDOW_SIZE = (1380, 854)
MIN_SIZE = (1080, 700)
BACKGROUND = "#F6F5FA"

_IS_WINDOWS = sys.platform == "win32"

# --------------------------------------------------------------------------
# 原生窗口（Win32）
# --------------------------------------------------------------------------
# 无边框 + 自绘标题栏意味着窗口行为得自己做，三件事都踩过坑：
#
# * 拖动：前端逐帧算位移再调回来，每一帧都要过一次 JS→Python 的桥，必然慢半拍。
#   改成把「按下了标题栏」这件事告诉系统，由系统进自己的移动循环，1:1 跟手。
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
_VK_LBUTTON = 0x01
_MONITOR_DEFAULTTONEAREST = 0x00000002

_user32_cached: Any = None


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
        u.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(_RECT)]
        u.GetWindowRect.restype = wt.BOOL
        u.GetWindowPlacement.argtypes = [wt.HWND, ctypes.POINTER(_WINDOWPLACEMENT)]
        u.MonitorFromWindow.argtypes = [wt.HWND, wt.DWORD]
        u.MonitorFromWindow.restype = wt.HANDLE
        u.GetMonitorInfoW.argtypes = [wt.HANDLE, ctypes.POINTER(_MONITORINFO)]
        u.GetAsyncKeyState.argtypes = [ctypes.c_int]
        u.GetAsyncKeyState.restype = ctypes.c_short
        u.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
        u.GetCursorPos.restype = wt.BOOL
        u.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
        u.IsWindowVisible.argtypes = [wt.HWND]
        u.IsWindowVisible.restype = wt.BOOL
        u.GetWindowTextLengthW.argtypes = [wt.HWND]
        u.EnumWindows.argtypes = [ctypes.c_void_p, wt.LPARAM]
        _user32_cached = u
        return u

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

    # -- 拖动 ------------------------------------------------------------
    def start_drag(self, x: int, y: int) -> bool:
        """跟着光标移动窗口，直到松开左键。

        为什么不走「系统自己的移动循环」（``SendMessage(WM_NCLBUTTONDOWN,
        HTCAPTION)``）：这条窗口是 WinForms 的无边框 ``WS_POPUP``，鼠标按下时捕获在
        WebView2 的子窗口上、属于另一个进程的线程，``ReleaseCapture`` 跨线程无效，
        移动循环拿不到后续的鼠标移动，实测窗口纹丝不动。

        也不逐帧回传位移（旧实现）：每一帧都要过一次 JS→Python 的桥，慢半拍。
        这里在 Python 侧直接按光标位置摆窗口，中间没有任何往返：
        120Hz 轮询，肉眼就是跟手的。
        """
        if not _IS_WINDOWS:
            return False
        hwnd = self.handle()
        if not hwnd:
            return False
        if self._dragging.is_set():
            return True
        self._dragging.set()

        def run() -> None:
            u = _user32()
            try:
                point = wt.POINT()
                u.GetCursorPos(ctypes.byref(point))
                rect = self.rect()
                # 按下点与窗口左上角的偏移：整段拖动都保持这个偏移。
                # 优先用前端给的按下坐标——它比「收到调用时的光标」更准，
                # 后者可能已经被用户拖出了几像素。
                press_x = int(x) or point.x
                press_y = int(y) or point.y
                offset_x = rect[0] - press_x
                offset_y = rect[1] - press_y
                while u.GetAsyncKeyState(_VK_LBUTTON) & 0x8000:
                    u.GetCursorPos(ctypes.byref(point))
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
                self._dragging.clear()

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
        """自绘标题栏的「按下了」：由系统进入它自己的移动循环。

        前端只在 pointerdown 时调一次并带上按下点的屏幕坐标，之后每一帧都由
        系统自己跟手 —— 不再逐帧回传位移，那必然比鼠标慢半拍。
        """
        if not self._native.start_drag(int(x or 0), int(y or 0)):
            log.debug("原生拖动不可用（非 Windows）")
        else:
            log.debug("原生拖动：按下点 %s,%s", x, y)

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
    parser.add_argument(
        "--headless",
        action="store_true",
        help="只跑接口服务，不开窗口也不开浏览器（Electron 壳用）",
    )
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（只允许回环）")
    parser.add_argument("--port", type=int, default=0, help="端口，0 表示由系统分配")
    parser.add_argument(
        "--token",
        default="",
        help="指定访问令牌；不传则随机生成。Electron 壳用它把令牌与渲染进程对齐",
    )
    parser.add_argument("--debug", action="store_true", help="打开 WebView 调试工具与调试日志")
    return parser.parse_args(list(argv))


def run(argv: Sequence[str] | None = None) -> int:
    # 复用 Qt 版同一套日志：写 data_dir/logs/stent.log + stderr。
    # 打包后 console=False 没有控制台，只靠 basicConfig 的话日志等于丢失，
    # 用户报「白屏」时我们手上什么都没有。
    from .app import ensure_utf8_console, setup_logging

    ensure_utf8_console()
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    setup_logging(logging.DEBUG if args.debug else logging.INFO)

    if args.host not in {"127.0.0.1", "::1", "localhost"}:
        print("出于安全考虑，只允许监听回环地址（127.0.0.1）", file=sys.stderr)
        return 2

    paths.data_dir()
    # 令牌可以由壳层传入（Electron 自己生成再 spawn 本进程），也可从环境变量取
    token = args.token or os.environ.get("STENT_API_TOKEN") or None
    app = build_app(host=args.host, port=args.port, token=token)
    url = app.start()
    print(f"Stent 接口服务：{url}")
    print(f"数据目录：{paths.data_dir()}")

    if args.headless:
        # 机器可读的一行：壳层用它拿到真实端口与令牌（端口传 0 时是系统分配的）
        print(f"STENT_READY {url} {app.token}", flush=True)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            app.stop()
        return 0

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
        min_size=MIN_SIZE,
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
