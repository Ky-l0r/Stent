"""DWM 圆角与投影的回归。

这两个能力全靠一次 ``DwmSetWindowAttribute`` 调用，失败时窗口照常可用、照常直角、
也没有异常——只有像素能说明问题。所以这里全部用像素采样断言：

- 角点像素必须等于窗口**后面**的背景色（说明角被 DWM 裁掉了）；
- 窗外 4px 的像素必须比背景暗（说明 DWM 画了投影）；
- 把圆角复位成 DONOTROUND，角点必须变回窗口自己的颜色——这一步是为了证明
  上面那条判据真的在测圆角，而不是背景碰巧同色；
- 最大化时角点必须仍是窗口自己的颜色（系统对最大化的窗口不圆角）。

Win10 没有这个属性，相关项会报「跳过」而不是失败。

用法::

    python tests/selftest_frame.py
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001 - 老终端不支持就退回默认编码
        pass

user32 = ctypes.WinDLL("user32")
gdi32 = ctypes.WinDLL("gdi32")
dwmapi = ctypes.WinDLL("dwmapi")

WS_POPUP = 0x80000000
WS_VISIBLE = 0x10000000
WS_EX_TOOLWINDOW = 0x00000080
SW_MAXIMIZE = 3
SW_RESTORE = 9

DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWCP_DEFAULT = 0
DWMWCP_DONOTROUND = 1
DWMWCP_ROUND = 2
WIN11_BUILD = 22000

BACKDROP_COLOR = (0, 160, 0)  # 背景窗口：纯绿
WINDOW_COLOR = (32, 112, 208)  # 探针窗口：纯蓝

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)


class WNDCLASS(ctypes.Structure):
    _fields_ = [
        ("style", wt.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wt.HINSTANCE),
        ("hIcon", wt.HICON),
        ("hCursor", wt.HANDLE),
        ("hbrBackground", wt.HBRUSH),
        ("lpszMenuName", wt.LPCWSTR),
        ("lpszClassName", wt.LPCWSTR),
    ]


user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASS)]
user32.CreateWindowExW.argtypes = [
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
user32.CreateWindowExW.restype = wt.HWND
dwmapi.DwmSetWindowAttribute.argtypes = [wt.HWND, wt.DWORD, ctypes.c_void_p, wt.DWORD]
dwmapi.DwmSetWindowAttribute.restype = ctypes.c_long
dwmapi.DwmGetWindowAttribute.argtypes = [wt.HWND, wt.DWORD, ctypes.c_void_p, wt.DWORD]
dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long

_results: list[tuple[str, bool, str]] = []
_wndprocs = []


def check(label: str, passed: bool, note: str = "") -> None:
    _results.append((label, passed, note))


def skip(label: str, note: str) -> None:
    _results.append((f"{label}（跳过）", True, note))


def build_number() -> int:
    return sys.getwindowsversion().build


def pixel(x: int, y: int) -> tuple[int, int, int]:
    dc = user32.GetDC(0)
    color = gdi32.GetPixel(dc, x, y)
    user32.ReleaseDC(0, dc)
    if color == 0xFFFFFFFF:
        return (-1, -1, -1)
    return (color & 0xFF, (color >> 8) & 0xFF, (color >> 16) & 0xFF)


def pump(ms: int = 150) -> None:
    deadline = time.monotonic() + ms / 1000
    msg = wt.MSG()
    while time.monotonic() < deadline:
        while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        time.sleep(0.004)


def make_window(name: str, color: tuple[int, int, int], rect: tuple[int, int, int, int]) -> int:
    proc = WNDPROC(lambda h, m, w, n: user32.DefWindowProcW(h, m, w, n))
    _wndprocs.append(proc)  # 回调必须留引用，否则会被 GC 掉
    wc = WNDCLASS()
    wc.lpfnWndProc = proc
    wc.hbrBackground = gdi32.CreateSolidBrush(color[2] << 16 | color[1] << 8 | color[0])
    wc.lpszClassName = name
    user32.RegisterClassW(ctypes.byref(wc))
    left, top, right, bottom = rect
    hwnd = user32.CreateWindowExW(
        WS_EX_TOOLWINDOW,
        name,
        name,
        WS_POPUP | WS_VISIBLE,
        left,
        top,
        right - left,
        bottom - top,
        None,
        None,
        None,
        None,
    )
    return int(hwnd or 0)


def set_corner(hwnd: int, value: int) -> int:
    data = ctypes.c_int(value)
    return dwmapi.DwmSetWindowAttribute(
        hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, ctypes.byref(data), ctypes.sizeof(data)
    )


def get_corner(hwnd: int) -> int:
    data = ctypes.c_int(-1)
    dwmapi.DwmGetWindowAttribute(
        hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, ctypes.byref(data), ctypes.sizeof(data)
    )
    return data.value


def rect_of(hwnd: int) -> tuple[int, int, int, int]:
    r = wt.RECT()
    user32.GetWindowRect(wt.HWND(hwnd), ctypes.byref(r))
    return (r.left, r.top, r.right, r.bottom)


def darker(a: tuple[int, int, int], b: tuple[int, int, int], threshold: int = 6) -> bool:
    """``a`` 是否比 ``b`` 暗（投影一定压暗背景）。

    按亮度和比，不逐通道比：背景是纯色时某些通道本来就是 0，逐通道「必须更暗」
    永远不成立——第一版就是这么误判成"没有阴影"的。
    """
    return sum(b) - sum(a) >= threshold


# --------------------------------------------------------------------------
# 第一段：进程内探针窗口
# --------------------------------------------------------------------------
def probe_checks() -> None:
    from stent import webapp as shell

    if build_number() < WIN11_BUILD:
        skip("DWM 圆角属性", f"build {build_number()} < {WIN11_BUILD}")
        return

    back = make_window("FrameProbeBack", BACKDROP_COLOR, (300, 200, 1000, 800))
    top = make_window("FrameProbeTop", WINDOW_COLOR, (420, 320, 820, 720))
    if not back or not top:
        check("创建探针窗口", False, f"back={back} top={top}")
        return
    pump(200)
    user32.SetForegroundWindow(wt.HWND(top))
    pump(200)

    left, upper, _right, bottom = rect_of(top)
    mid = (upper + bottom) // 2

    # 基线：直角窗口的四角应该是自己的颜色
    check(
        "未设圆角时窗口是直角",
        pixel(left + 1, upper + 1) == WINDOW_COLOR,
        f"角点={pixel(left + 1, upper + 1)}",
    )

    result = shell._apply_round_corners(top)  # noqa: SLF001 - 直接测壳层那段实现
    check("设置圆角成功", result and get_corner(top) == DWMWCP_ROUND, f"读回={get_corner(top)}")
    pump(250)

    corner = pixel(left + 1, upper + 1)
    inner = pixel(left + 8, upper + 8)
    check(
        "角被 DWM 裁掉（露出背景）",
        not (corner[1] > 120 and corner[2] > 150) and inner == WINDOW_COLOR,
        f"角点={corner} 内部={inner}",
    )
    shadow = pixel(left - 4, mid)
    check(
        "窗口外沿有投影",
        darker(shadow, BACKDROP_COLOR),
        f"窗外4px={shadow} 背景={BACKDROP_COLOR}",
    )

    # 复位成不圆角：角点必须变回窗口色，顺便证明上一条判据没在测背景
    set_corner(top, DWMWCP_DONOTROUND)
    pump(250)
    check(
        "复位后回到直角",
        pixel(left + 1, upper + 1) == WINDOW_COLOR
        and not darker(pixel(left - 4, mid), BACKDROP_COLOR),
        f"角点={pixel(left + 1, upper + 1)} 窗外={pixel(left - 4, mid)}",
    )

    # 圆角 + 最大化：系统应当把角切回直角
    set_corner(top, DWMWCP_ROUND)
    pump(200)
    user32.ShowWindow(wt.HWND(top), SW_MAXIMIZE)
    pump(400)
    zoomed = rect_of(top)
    # 最大化的窗口外沿有 1px DWM 边框，采样要往里挪 3px
    corner = pixel(zoomed[0] + 3, zoomed[1] + 3)
    check(
        "最大化时是直角",
        corner == WINDOW_COLOR,
        f"rect={zoomed} 角点+3={corner} 期望={WINDOW_COLOR}",
    )
    user32.ShowWindow(wt.HWND(top), SW_RESTORE)
    pump(300)

    user32.DestroyWindow(wt.HWND(top))
    user32.DestroyWindow(wt.HWND(back))


# --------------------------------------------------------------------------
# 第二段：真实应用窗口
# --------------------------------------------------------------------------
def app_checks() -> None:
    def find_app_window(pid: int) -> int:
        found: list[int] = []

        @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
        def collect(hwnd: int, _lparam: int) -> bool:
            owner = wt.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if owner.value == pid and user32.IsWindowVisible(hwnd):
                if user32.GetWindowTextLengthW(hwnd) > 0:
                    found.append(int(hwnd))
            return True

        user32.EnumWindows(ctypes.cast(collect, ctypes.c_void_p), 0)
        if not found:
            return 0
        return max(
            found, key=lambda h: (rect_of(h)[2] - rect_of(h)[0]) * (rect_of(h)[3] - rect_of(h)[1])
        )

    if build_number() < WIN11_BUILD:
        skip("真实窗口圆角", f"build {build_number()} < {WIN11_BUILD}")
        return

    proc = subprocess.Popen(
        [sys.executable, "main.py"],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    backdrop = 0
    try:
        hwnd = 0
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            hwnd = find_app_window(proc.pid)
            if hwnd:
                break
            time.sleep(0.5)
        if not hwnd:
            check("启动应用窗口", False, "60 秒内没找到窗口")
            return
        time.sleep(4.0)  # 等页面渲染完，圆角由 events.shown 应用

        check(
            "应用窗口读回圆角偏好",
            get_corner(hwnd) == DWMWCP_ROUND,
            f"读回={get_corner(hwnd)}",
        )

        # 摆到一块纯绿背景上，看角点是否露出背景色
        backdrop = make_window("FrameAppBack", BACKDROP_COLOR, (300, 200, 1400, 1000))
        user32.SetWindowPos(ctypes.c_void_p(hwnd), None, 380, 280, 900, 640, 0x0001 | 0x0010)
        user32.SetForegroundWindow(wt.HWND(hwnd))
        time.sleep(1.0)
        app_rect = rect_of(hwnd)
        corner = pixel(app_rect[0] + 1, app_rect[1] + 1)
        # 参照点要挑标题栏的空白处：文字有次像素抗锯齿，边缘像素是彩色的，
        # 取到那里会把「角点≠标题栏」判成通过
        titlebar = pixel(app_rect[0] + 300, app_rect[1] + 8)
        check(
            "应用窗口四角被裁掉",
            corner != titlebar and not (corner[1] > 100 and corner[2] > 150),
            f"角点={corner} 标题栏={titlebar}",
        )
        check(
            "应用窗口外沿有投影",
            darker(pixel(app_rect[0] - 4, (app_rect[1] + app_rect[3]) // 2), BACKDROP_COLOR),
            f"窗外={pixel(app_rect[0] - 4, (app_rect[1] + app_rect[3]) // 2)}",
        )

        # 最大化后回到直角
        user32.ShowWindow(wt.HWND(hwnd), SW_MAXIMIZE)
        time.sleep(1.0)
        zoomed = rect_of(hwnd)
        corner = pixel(zoomed[0] + 3, zoomed[1] + 3)
        titlebar = pixel(zoomed[0] + 300, zoomed[1] + 8)
        check(
            "最大化后是直角",
            corner == titlebar,
            f"rect={zoomed} 角点+3={corner} 标题栏={titlebar}",
        )
        user32.ShowWindow(wt.HWND(hwnd), SW_RESTORE)
        time.sleep(0.4)
    finally:
        if backdrop:
            user32.DestroyWindow(wt.HWND(backdrop))
        subprocess.run(
            ["taskkill", "/pid", str(proc.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )


def main() -> int:
    probe_checks()
    app_checks()

    print("\n===== DWM 圆角与投影 =====")
    passed = 0
    for label, ok, note in _results:
        print(f"[{'PASS' if ok else 'FAIL'}] {label:24s} {note}")
        passed += int(ok)
    print(f"----- {passed}/{len(_results)} 通过 -----")
    sys.stdout.flush()
    return 0 if passed == len(_results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
