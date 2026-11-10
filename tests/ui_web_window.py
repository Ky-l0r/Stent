"""Web 壳窗口行为回归：最大化 ↔ 还原、顶栏拖动与鼠标同步。

三个都曾经出过错，且只有真实窗口才暴露：

- **「全屏之后不能缩小」**：旧实现用 ``getattr(window, "maximized", False)`` 判断
  当前状态，而 pywebview 的 Window 没有这个属性 —— 恒为 False，于是永远只调
  ``maximize()``，点第二下也回不来。
- **拖动延迟**：旧实现逐帧把位移从 JS 回传给 Python（``move_window``），每一帧
  都过一次桥，必然比鼠标慢半拍。
- **没有最大化动画**：无边框窗口拿不到系统的 zoom 动画。

测试分两段：

1. 进程内造一个真实的 Win32 弹窗，直接驱动 ``_NativeWindow`` 验证状态机
   （最大化矩形、还原点、连续往返），不依赖 pywebview 启动。
2. 真启动 ``main.py``，用 Win32 ``mouse_event`` 注入真实输入，从标题栏拖动、
   点最大化按钮，走完整链路：系统 → WebView2 → JS → js_api → 原生窗口。

用法::

    python tests/ui_web_window.py
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
WS_POPUP = 0x80000000
WS_VISIBLE = 0x10000000
HWND_TOPMOST = ctypes.c_void_p(-1)
user32 = ctypes.windll.user32

STEP_X = 24
STEP_Y = 15
STEPS = 6

_results: list[tuple[str, bool, str]] = []


def check(label: str, passed: bool, note: str = "") -> None:
    _results.append((label, passed, note))


def rect_of(hwnd: int) -> tuple[int, int, int, int]:
    r = wt.RECT()
    user32.GetWindowRect(wt.HWND(hwnd), ctypes.byref(r))
    return (r.left, r.top, r.right, r.bottom)


def work_area(hwnd: int) -> tuple[int, int, int, int]:
    class MONITORINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wt.DWORD),
            ("rcMonitor", wt.RECT),
            ("rcWork", wt.RECT),
            ("dwFlags", wt.DWORD),
        ]

    monitor = user32.MonitorFromWindow(wt.HWND(hwnd), 2)
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    user32.GetMonitorInfoW(monitor, ctypes.byref(info))
    w = info.rcWork
    return (w.left, w.top, w.right, w.bottom)


def _set_cursor(x: int, y: int) -> None:
    user32.SetCursorPos(int(x), int(y))


def pump(ms: int) -> None:
    """跑一会儿消息循环。

    进程内探针窗口属于本线程：工作线程调 ``SetWindowPos`` 会同步发
    ``WM_WINDOWPOSCHANGING`` 过来，主线程不泵消息就会一直阻塞。
    """
    deadline = time.monotonic() + ms / 1000.0
    msg = wt.MSG()
    while time.monotonic() < deadline:
        while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        time.sleep(0.004)


def _get_cursor() -> tuple[int, int]:
    point = wt.POINT()
    user32.GetCursorPos(ctypes.byref(point))
    return point.x, point.y


# --------------------------------------------------------------------------
# 第一段：进程内的真实弹窗
# --------------------------------------------------------------------------
def native_window_checks() -> bool:
    """用自建的 WS_POPUP 窗口驱动 ``_NativeWindow``，验证最大化/还原状态机。"""
    from stent import webapp as shell

    hwnd = user32.CreateWindowExW(
        0,
        "STATIC",
        "stent-window-probe",
        WS_POPUP | WS_VISIBLE,
        120,
        100,
        900,
        600,
        None,
        None,
        None,
        None,
    )
    if not hwnd:
        check("创建探针窗口", False, f"CreateWindowExW 失败：{ctypes.get_last_error()}")
        return False
    hwnd = int(hwnd)

    # 让 _NativeWindow 用这个窗口，而不是去枚举进程窗口
    shell._find_window_handle = lambda _pid: hwnd  # noqa: SLF001
    native = shell._NativeWindow()  # noqa: SLF001

    original = rect_of(hwnd)
    work = work_area(hwnd)

    def settle() -> None:
        for _ in range(120):
            pump(20)
            if not native._animating.is_set():  # noqa: SLF001
                break
        pump(150)

    native.toggle_maximize()
    settle()
    check(
        "最大化后贴合工作区",
        native.is_maximized() and rect_of(hwnd) == work,
        f"zoomed={native.is_maximized()} rect={rect_of(hwnd)} 工作区={work}",
    )

    native.toggle_maximize()
    settle()
    check(
        "还原后回到原尺寸",
        (not native.is_maximized()) and rect_of(hwnd) == original,
        f"zoomed={native.is_maximized()} rect={rect_of(hwnd)} 期望={original}",
    )

    stability = []
    for _ in range(3):
        native.toggle_maximize()
        settle()
        max_ok = native.is_maximized() and rect_of(hwnd) == work
        native.toggle_maximize()
        settle()
        normal_ok = (not native.is_maximized()) and rect_of(hwnd) == original
        stability.append(f"{max_ok}/{normal_ok}")
    check("连续往返三次稳定", all(s == "True/True" for s in stability), " ".join(stability))

    # 拖动会改变普通矩形，之后还原仍应回到新位置
    moved = (original[0] + 60, original[1] + 40)
    user32.SetWindowPos(hwnd, None, moved[0], moved[1], 0, 0, 0x0001 | 0x0004 | 0x0010)
    pump(150)
    moved_rect = rect_of(hwnd)
    native.toggle_maximize()
    settle()
    native.toggle_maximize()
    settle()
    check(
        "移动后仍能正确还原",
        (not native.is_maximized()) and rect_of(hwnd) == moved_rect,
        f"rect={rect_of(hwnd)} 期望={moved_rect}",
    )

    # 动画确实在中间态出现过（否则就是瞬移，等于没动画）
    frames: list[tuple[int, int, int, int]] = []
    watcher_done = threading.Event()

    def watch() -> None:
        while not watcher_done.is_set():
            frames.append(rect_of(hwnd))
            time.sleep(0.012)

    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()
    native.toggle_maximize()
    settle()
    watcher_done.set()
    watcher.join(1.0)
    distinct = {f for f in frames if f not in (moved_rect, work)}
    check("最大化有过渡动画", len(distinct) >= 3, f"中间态 {len(distinct)} 帧")

    user32.DestroyWindow(wt.HWND(hwnd))
    return True


# --------------------------------------------------------------------------
# 第二段：真启动应用，注入输入
# --------------------------------------------------------------------------
def find_app_window(pid: int) -> int:
    """按进程找宿主窗口：可见、有标题、面积最大的那个（标题会随切页变化）。"""
    candidates: list[int] = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def collect(hwnd: int, _lparam: int) -> bool:
        owner = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            if user32.GetWindowTextLengthW(hwnd) > 0:
                candidates.append(int(hwnd))
        return True

    user32.EnumWindows(ctypes.cast(collect, ctypes.c_void_p), 0)
    if not candidates:
        return 0

    def area(hwnd: int) -> int:
        left, top, right, bottom = rect_of(hwnd)
        return (right - left) * (bottom - top)

    return max(candidates, key=area)


def app_window_checks() -> None:
    proc = subprocess.Popen(
        [sys.executable, "main.py"],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
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
        check("启动应用窗口", True, f"hwnd={hwnd}")
        time.sleep(4.0)  # 等 WebView2 把页面渲染完（js_api 桥就绪）

        # 注入的输入只会落到最上层窗口：先把窗口钉到最前，再挪到标题栏不被
        # 别的置顶窗口（比如 IDE 的悬浮窗）遮挡的位置，否则整段测试会静默失败。
        # 判据是「点下面的窗口属于本进程」——最深处通常是 WebView2 的子窗口。
        def our_window_under_titlebar() -> bool:
            # 命中点最深处是 WebView2 的渲染子窗口（属于 msedgewebview2.exe），
            # 所以比「根祖先」而不是进程号
            point = wt.POINT(rect_of(hwnd)[0] + 120, rect_of(hwnd)[1] + 23)
            under = user32.WindowFromPoint(point)
            return int(user32.GetAncestor(under, 2)) == hwnd  # GA_ROOT

        left, top, right, bottom = rect_of(hwnd)
        width, height = right - left, bottom - top
        clear = False
        for x, y in ((60, 60), (60, 200), (400, 40), (60, 0), (0, 0), (300, 240)):
            # 注意不能带 SWP_NOZORDER：它会忽略 HWND_TOPMOST，置顶就失效了
            user32.SetWindowPos(
                ctypes.c_void_p(hwnd), HWND_TOPMOST, x, y, width, height, 0x0001 | 0x0010
            )
            time.sleep(0.3)
            if our_window_under_titlebar():
                clear = True
                break
        user32.SetForegroundWindow(wt.HWND(hwnd))
        time.sleep(0.4)
        check(
            "窗口标题栏在注入点之上",
            clear,
            f"rect={rect_of(hwnd)}",
        )
        if not clear:
            return

        # ---- 拖动：位移必须与鼠标位移逐个相等 ----
        original_cursor = _get_cursor()
        start_rect = rect_of(hwnd)
        start = (start_rect[0] + 120, start_rect[1] + 23)
        points = [(start[0] + STEP_X * i, start[1] + STEP_Y * i) for i in range(1, STEPS + 1)]
        expected = (STEP_X * STEPS, STEP_Y * STEPS)
        _set_cursor(*start)
        time.sleep(0.25)

        done = threading.Event()

        def inject() -> None:
            time.sleep(0.15)
            user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
            time.sleep(0.12)
            for x, y in points:
                _set_cursor(x, y)
                time.sleep(0.06)
            time.sleep(0.08)
            user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
            done.set()

        thread = threading.Thread(target=inject)
        thread.start()
        thread.join(8.0)
        done.wait(1.0)
        time.sleep(0.4)
        _set_cursor(*original_cursor)

        after = rect_of(hwnd)
        actual = (after[0] - start_rect[0], after[1] - start_rect[1])
        check(
            "拖动位移与鼠标一致",
            actual == expected,
            f"窗口位移={actual} 鼠标位移={expected} 误差={(actual[0] - expected[0], actual[1] - expected[1])}",
        )

        # ---- 最大化按钮 → 再点一次必须回到原尺寸（本次修复的主诉）----
        normal_rect = rect_of(hwnd)
        work = work_area(hwnd)
        button_y = normal_rect[1] + 23

        def click(x: int, y: int) -> None:
            _set_cursor(x, y)
            time.sleep(0.12)
            user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
            time.sleep(0.05)
            user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
            time.sleep(0.6)

        # 从右边缘倒数：padding 6 + 按钮 30 + gap 6 → 关闭中心 right-21，最大化 right-57
        maximize_x = normal_rect[2] - 57
        click(maximize_x, button_y)
        time.sleep(0.4)
        maximized_rect = rect_of(hwnd)
        check(
            "点最大化按钮铺满工作区",
            maximized_rect == work,
            f"rect={maximized_rect} 工作区={work}",
        )

        # 最大化之后窗口顶到 (0,0)，按钮位置要连 y 一起按当前矩形重算
        click(maximized_rect[2] - 57, maximized_rect[1] + 23)
        time.sleep(0.5)
        restored_rect = rect_of(hwnd)
        check(
            "再点一次能还原（全屏后不能缩小的主诉）",
            restored_rect == normal_rect,
            f"rect={restored_rect} 期望={normal_rect}",
        )

        # ---- 最小化按钮（「全屏之后不能缩小」的另一种读法）----
        click(restored_rect[2] - 93, restored_rect[1] + 23)
        time.sleep(0.5)
        iconic = bool(user32.IsIconic(wt.HWND(hwnd)))
        check("点最小化按钮能收起窗口", iconic, f"IsIconic={iconic}")
        user32.ShowWindow(wt.HWND(hwnd), 9)  # SW_RESTORE
        time.sleep(0.5)
        check(
            "从最小化恢复后位置不变",
            (not user32.IsIconic(wt.HWND(hwnd))) and rect_of(hwnd) == restored_rect,
            f"rect={rect_of(hwnd)} 期望={restored_rect}",
        )
    finally:
        subprocess.run(
            ["taskkill", "/pid", str(proc.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )


def main() -> int:
    native_window_checks()
    app_window_checks()

    print("\n===== Web 壳窗口行为检查 =====")
    passed = 0
    for label, ok, note in _results:
        print(f"[{'PASS' if ok else 'FAIL'}] {label:20s} {note}")
        passed += int(ok)
    print(f"----- {passed}/{len(_results)} 通过 -----")
    sys.stdout.flush()
    return 0 if passed == len(_results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
