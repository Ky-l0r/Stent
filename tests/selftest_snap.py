"""窗口贴边分屏、拖边缩放与 Snap 选择器的回归。

三段都不联网，也不需要 WebView2：

1. **纯几何**（``stent.window_snap``）：贴边判定、布局平铺、选择器命中的是
   缩略图里的哪一格、缩放夹边界。这些算错了不会有任何窗口报错——只会悄悄把
   窗口摆到任务栏底下，所以断言写得最细。
2. **预览层**（``webapp._SnapOverlay``）：真建一个 Win32 分层窗口，验它可见、
   位置等于画布、样式带鼠标穿透（不穿透就会挡住拖动本身）。
3. **窗口行为**（``webapp._NativeWindow``）：真建探针窗口，用脚本化的光标轨迹
   驱动拖动与缩放，验吸附落位、从吸附态拖开能还原、拖边到底不盖任务栏。

用法::

    python tests/selftest_snap.py
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
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

WS_POPUP = 0x80000000
WS_VISIBLE = 0x10000000

user32 = ctypes.windll.user32

_results: list[tuple[str, bool, str]] = []


def check(label: str, passed: bool, note: str = "") -> None:
    _results.append((label, passed, note))


def rect_of(hwnd: int) -> tuple[int, int, int, int]:
    r = wt.RECT()
    user32.GetWindowRect(wt.HWND(hwnd), ctypes.byref(r))
    return (r.left, r.top, r.right, r.bottom)


def pump(ms: int) -> None:
    """跑一会儿消息循环：工作线程对探针窗口的 SetWindowPos 会同步发消息过来。"""
    deadline = time.monotonic() + ms / 1000.0
    msg = wt.MSG()
    while time.monotonic() < deadline:
        while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        time.sleep(0.004)


def wait_until(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        pump(20)
    return predicate()


WORK = (0, 0, 1920, 1040)  # 1080p + 底部任务栏
WIDTH = WORK[2] - WORK[0]
HEIGHT = WORK[3] - WORK[1]


# --------------------------------------------------------------------------
# 第一段：纯几何
# --------------------------------------------------------------------------
def geometry_checks() -> None:
    from stent import window_snap as snap

    left, top, right, bottom = WORK

    def mode_at(point):
        return snap.SnapSession().update(point, WORK)

    # -- 四边与四角 --
    state = mode_at((left + 2, top + HEIGHT // 2))
    check("贴左边 → 左半屏", state.rect == (left, top, left + WIDTH // 2, bottom), str(state.rect))

    state = mode_at((right - 2, top + HEIGHT // 2))
    check(
        "贴右边 → 右半屏",
        state.rect == (left + WIDTH // 2, top, right, bottom),
        str(state.rect),
    )

    state = mode_at((left + 2, top + 2))
    check(
        "左上角 → 左上四分之一",
        state.rect == (left, top, left + WIDTH // 2, top + HEIGHT // 2),
        str(state.rect),
    )

    state = mode_at((left + 2, bottom - 2))
    check(
        "左下角 → 左下四分之一",
        state.rect == (left, top + HEIGHT // 2, left + WIDTH // 2, bottom),
        str(state.rect),
    )

    state = mode_at((right - 2, bottom - 2))
    check(
        "右下角 → 右下四分之一",
        state.rect == (left + WIDTH // 2, top + HEIGHT // 2, right, bottom),
        str(state.rect),
    )

    state = mode_at((left + WIDTH // 2, bottom - 2))
    check(
        "贴下边中部 → 下半屏",
        state.rect == (left, top + HEIGHT // 2, right, bottom),
        str(state.rect),
    )

    state = mode_at((left + WIDTH // 2, top + HEIGHT // 2))
    check("窗口中部 → 不吸附", state.mode == "idle" and state.rect is None, state.mode)

    # -- 吸附不能盖住任务栏：任何目标都必须落在工作区内 --
    inside = all(
        rect[0] >= left and rect[1] >= top and rect[2] <= right and rect[3] <= bottom
        for layout in snap.LAYOUTS
        for index in range(len(layout.zones))
        for rect in (snap.zone_rect(snap.LAYOUTS.index(layout), index, WORK),)
    )
    check("所有布局分区都在工作区内", inside)

    # -- 相邻分区必须严丝合缝：拼起来正好铺满工作区 --
    def union_area(layout_index: int) -> int:
        return sum(
            snap.rect_size(snap.zone_rect(layout_index, index, WORK))[0]
            * snap.rect_size(snap.zone_rect(layout_index, index, WORK))[1]
            for index in range(len(snap.LAYOUTS[layout_index].zones))
        )

    areas = [union_area(index) for index in range(len(snap.LAYOUTS))]
    check(
        "布局分区面积之和等于工作区",
        all(area == WIDTH * HEIGHT for area in areas),
        f"{areas} vs {WIDTH * HEIGHT}",
    )

    # -- 顶到上沿弹选择器，且卡片不会挡住「刚碰到上沿」这一点 --
    state = mode_at((left + WIDTH // 2, top + 2))
    picker = state.picker
    check(
        "顶到上沿 → 弹出选择器",
        state.mode == "picker" and picker is not None and state.hover is None,
        f"mode={state.mode} hover={state.hover}",
    )
    check(
        "选择器在顶带之外，不会误吸附",
        picker is not None
        and picker.card[1] > top + snap.EDGE_MARGIN
        and picker.card[1] <= top + 40
        and picker.card[3] < top + HEIGHT,
        str(picker.card if picker else None),
    )
    check(
        "选择器提供多种布局",
        len(snap.LAYOUTS) >= 4
        and len({layout.key for layout in snap.LAYOUTS}) == len(snap.LAYOUTS),
        f"{[layout.name for layout in snap.LAYOUTS]}",
    )

    # -- 在同一张卡片里选区域 --
    session = snap.SnapSession()
    session.update((left + WIDTH // 2, top + 2), WORK)
    thumb = snap.picker_geometry(WORK).thumbs[0]
    center = ((thumb[0] + thumb[2]) // 2, (thumb[1] + thumb[3]) // 2)
    state = session.update(center, WORK)
    check(
        "在选择器里悬停 → 预览对应区域",
        state.mode == "picker"
        and state.hover == (0, 1)
        and state.rect == (left + WIDTH // 2, top, right, bottom),
        f"hover={state.hover} rect={state.rect}",
    )
    # 选四宫格的第一格
    thumb = snap.picker_geometry(WORK).thumbs[3]
    quarter = ((thumb[0] + thumb[2]) // 2 - 20, (thumb[1] + thumb[3]) // 2 - 12)
    state = session.update(quarter, WORK)
    check(
        "换一张缩略图 → 预览跟着换",
        state.hover == (3, 0) and state.rect == (left, top, left + WIDTH // 2, top + HEIGHT // 2),
        f"hover={state.hover} rect={state.rect}",
    )

    # -- 记忆性：往下移动去够缩略图时卡片不能先消失 --
    state = session.update((quarter[0], quarter[1] + 6), WORK)
    check("光标在卡片内时选择器保持打开", state.mode == "picker", state.mode)
    state = session.update((left + WIDTH // 2, top + HEIGHT // 2), WORK)
    check("光标离开卡片后收起", state.mode == "idle" and not session.picker_open, state.mode)

    # -- 逐格命中：缩略图里每个分区点一下都应命中自己，不串格 --
    picker = snap.picker_geometry(WORK)
    misses = []
    for layout_index, thumb in enumerate(picker.thumbs):
        for zone_index in range(len(snap.LAYOUTS[layout_index].zones)):
            zone = snap.zone_rect(layout_index, zone_index, thumb)
            probe = ((zone[0] + zone[2]) // 2, (zone[1] + zone[3]) // 2)
            if snap.picker_hover(probe, picker) != (layout_index, zone_index):
                misses.append(f"{layout_index}/{zone_index}")
    check("缩略图逐格命中", not misses, " ".join(misses))

    # -- 窄屏：卡片要缩，不能溢出 --
    narrow = (0, 0, 800, 600)
    card = snap.picker_geometry(narrow).card
    check(
        "窄屏下选择器不溢出",
        card[0] >= narrow[0] and card[2] <= narrow[2] and card[3] <= narrow[3],
        str(card),
    )

    # -- 多显示器：负坐标（副屏在主屏左侧）也要正确 --
    left_monitor = (-1920, 0, 0, 1040)
    state = snap.SnapSession().update((-1918, 520), left_monitor)
    check(
        "负坐标显示器贴左边",
        state.rect == (-1920, 0, -960, 1040),
        str(state.rect),
    )

    # -- 预览绘制清单 --
    state = snap.SnapSession().update((left + 2, top + HEIGHT // 2), WORK)
    shapes = snap.preview_shapes(state)
    roles = [role for _rect, role in shapes]
    check(
        "区域预览 = 底色 + 边框",
        roles == ["zone", "zone-frame"] and shapes[0][0] == state.rect,
        str(roles),
    )
    session = snap.SnapSession()
    session.update((left + WIDTH // 2, top + 2), WORK)
    session.update(center, WORK)
    shapes = snap.preview_shapes(session.update(center, WORK))
    hot = [rect for rect, role in shapes if role == "slot-hot"]
    check(
        "选择器清单含卡片与全部缩略格",
        shapes[0][1] == "card" and len(hot) == 1 and sum(1 for _r, r in shapes if r == "slot") >= 8,
        f"格数={len(shapes)} 高亮={len(hot)}",
    )

    # -- 拖边缩放的边界 --
    start = (400, 300, 1200, 900)
    check(
        "拖右边只改宽度",
        snap.resize_rect(start, snap.edge_set("e"), 100, 999, (400, 300), WORK)
        == (400, 300, 1300, 900),
    )
    check(
        "拖左上角同时改两条边",
        snap.resize_rect(start, snap.edge_set("nw"), -50, -50, (400, 300), WORK)
        == (350, 250, 1200, 900),
    )
    check(
        "右下角贴到底不越过工作区",
        snap.resize_rect(start, snap.edge_set("se"), 5000, 5000, (400, 300), WORK)
        == (400, 300, 1920, 1040),
    )
    check(
        "不会缩到最小尺寸以下",
        snap.resize_rect(start, snap.edge_set("e"), -5000, 0, (400, 300), WORK)
        == (400, 300, 800, 900),
    )
    check(
        "比下限还小的窗口只许变大",
        # 吸附到四分之一屏后 960×540 已经小于最小尺寸，此时再拖右边只能变宽
        snap.resize_rect((0, 0, 960, 540), snap.edge_set("e"), -100, 0, (1080, 700), WORK)
        == (0, 0, 960, 540),
    )
    check(
        "左边也不能越过工作区左沿",
        snap.resize_rect(start, snap.edge_set("w"), -5000, 0, (400, 300), WORK)[0] == 0,
    )
    check(
        "认不出的边不触发缩放",
        snap.edge_set("") == frozenset() and snap.edge_set("x") == frozenset(),
    )


# --------------------------------------------------------------------------
# 第二段：预览层
# --------------------------------------------------------------------------
def overlay_checks() -> None:
    from stent import webapp as shell
    from stent.window_snap import DragState

    GWL_EXSTYLE = -20
    WS_EX_LAYERED = 0x00080000
    WS_EX_TRANSPARENT = 0x00000020
    WS_EX_NOACTIVATE = 0x08000000

    monitor = user32.MonitorFromPoint(wt.POINT(0, 0), 2)

    class MONITORINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wt.DWORD),
            ("rcMonitor", wt.RECT),
            ("rcWork", wt.RECT),
            ("dwFlags", wt.DWORD),
        ]

    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    user32.GetMonitorInfoW(monitor, ctypes.byref(info))
    canvas = (info.rcMonitor.left, info.rcMonitor.top, info.rcMonitor.right, info.rcMonitor.bottom)
    target = (canvas[0] + 120, canvas[1] + 80, canvas[0] + 720, canvas[1] + 620)

    overlay = shell._SnapOverlay()
    overlay.preview(DragState("zone", rect=target), canvas)
    visible = wait_until(
        lambda: bool(overlay._hwnd) and bool(user32.IsWindowVisible(overlay._hwnd))
    )
    check("预览层可见", visible, f"hwnd={overlay._hwnd}")
    if not visible:
        return

    hwnd = overlay._hwnd
    check("预览层铺满显示器画布", rect_of(hwnd) == canvas, f"{rect_of(hwnd)} vs {canvas}")

    ex_style = user32.GetWindowLongW(wt.HWND(hwnd), GWL_EXSTYLE)
    check(
        "预览层分层且不抢焦点",
        bool(ex_style & WS_EX_LAYERED) and bool(ex_style & WS_EX_NOACTIVATE),
        hex(ex_style),
    )
    check("预览层鼠标穿透", bool(ex_style & WS_EX_TRANSPARENT), hex(ex_style))

    # 窗口可见、样式正确，并不等于画出了东西：FillRect 曾经被错放到 gdi32，
    # WM_PAINT 每次都在异常里结束，窗口一直是块黑玻璃。这里同时验两件事——
    # 绘制帧计数，以及窗口内容里真的出现了主题色像素。
    painted = wait_until(lambda: overlay._painted > 0, 2.0)
    check("预览层执行了绘制", painted, f"帧数={overlay._painted}")

    probe_pixel = _print_window_pixel(hwnd, canvas, target)
    check(
        "预览层画出了主题色",
        probe_pixel == (0x7C, 0x5C, 0xE6),
        f"取样={probe_pixel} 期望={(0x7C, 0x5C, 0xE6)}",
    )

    probe = wt.POINT((target[0] + target[2]) // 2, (target[1] + target[3]) // 2)
    under = user32.WindowFromPoint(probe)
    root = user32.GetAncestor(under, 2) if under else 0
    check("预览层不拦截命中测试", int(root) != int(hwnd), f"under={under} overlay={hwnd}")

    overlay.hide()
    hidden = wait_until(lambda: not user32.IsWindowVisible(overlay._hwnd))
    check("预览层能隐藏", hidden)

    # 内容不变不重复重构：连着推同一个状态不该报错
    overlay.preview(DragState("zone", rect=target), canvas)
    overlay.preview(DragState("zone", rect=target), canvas)
    again = wait_until(lambda: bool(user32.IsWindowVisible(overlay._hwnd)))
    check("重复推送同一状态幂等", again)
    overlay.hide()
    overlay.close()
    wait_until(lambda: not user32.IsWindowVisible(overlay._hwnd))


# --------------------------------------------------------------------------
# 第三段：窗口行为（探针窗口 + 脚本化输入）
# --------------------------------------------------------------------------
def _print_window_pixel(
    hwnd: int, canvas: tuple[int, int, int, int], rect: tuple[int, int, int, int]
):
    """把窗口内容重绘到位图里，取目标区域中心的像素（颜色按 R, G, B 返回）。

    ``PrintWindow`` 比截屏可靠：截屏会受其它窗口遮挡影响，而这里问的是
    「这个窗口自己画了什么」。
    """
    gdi32 = ctypes.windll.gdi32
    width = canvas[2] - canvas[0]
    height = canvas[3] - canvas[1]
    screen = user32.GetDC(0)
    mem = gdi32.CreateCompatibleDC(screen)
    bitmap = gdi32.CreateCompatibleBitmap(screen, width, height)
    gdi32.SelectObject(mem, bitmap)
    user32.PrintWindow(wt.HWND(hwnd), mem, 2)  # PW_RENDERFULLCONTENT
    x = (rect[0] + rect[2]) // 2 - canvas[0]
    y = (rect[1] + rect[3]) // 2 - canvas[1]
    color = gdi32.GetPixel(mem, x, y)
    gdi32.DeleteObject(bitmap)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(0, screen)
    if color == 0xFFFFFFFF:
        return (-1, -1, -1)
    return (color & 0xFF, (color >> 8) & 0xFF, (color >> 16) & 0xFF)


def _make_probe() -> int:
    # 尺寸必须大于 MIN_SIZE（1080×700）：比最小尺寸还小的窗口一拖就顶到下限，
    # 量出来的位移全是夹紧后的值，看不出缩放到底有没有跟上鼠标。
    hwnd = user32.CreateWindowExW(
        0,
        "STATIC",
        "stent-snap-probe",
        WS_POPUP | WS_VISIBLE,
        200,
        100,
        1400,
        900,
        None,
        None,
        None,
        None,
    )
    return int(hwnd or 0)


def _work_area(hwnd: int) -> tuple[int, int, int, int]:
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


def _script(native, points: list[tuple[int, int]]) -> None:
    """把输入源换成一串光标轨迹。``points[0]`` 会被起始那次读取吃掉，放按下点即可。"""
    state = {"i": 0}

    def cursor():
        index = min(state["i"], len(points) - 1)
        state["i"] += 1
        return wt.POINT(points[index][0], points[index][1])

    native._cursor_point = cursor
    native._left_button_down = lambda: state["i"] <= len(points)


def native_checks() -> None:
    from stent import webapp as shell
    from stent.window_snap import SnapSession, zone_rect

    hwnd = _make_probe()
    if not hwnd:
        check("创建探针窗口", False, f"CreateWindowExW 失败：{ctypes.get_last_error()}")
        return

    shell._find_window_handle = lambda _pid: hwnd
    native = shell._NativeWindow()
    work = _work_area(hwnd)
    original = rect_of(hwnd)
    pump(50)

    # -- 直接吸附 --
    left_half = (work[0], work[1], work[0] + (work[2] - work[0]) // 2, work[3])
    native.apply_snap(left_half)
    wait_until(lambda: rect_of(hwnd) == left_half, 2.0)
    check("吸附到左半屏", rect_of(hwnd) == left_half, f"{rect_of(hwnd)} vs {left_half}")
    check(
        "吸附后记住原矩形", native._normal_before_snap == original, str(native._normal_before_snap)
    )

    # -- 从吸附态拖开：尺寸必须回到吸附前，而不是半屏 --
    press = (left_half[0] + 120, left_half[1] + 23)
    restored = native._restore_for_drag(*press)
    pump(60)
    check(
        "从吸附态拖开回到原尺寸",
        (restored[2] - restored[0], restored[3] - restored[1])
        == (original[2] - original[0], original[3] - original[1]),
        f"{restored}",
    )
    check(
        "拖开后窗口仍完整在工作区内",
        restored[0] >= work[0]
        and restored[1] >= work[1]
        and restored[2] <= work[2]
        and restored[3] <= work[3],
        f"{restored} vs 工作区 {work}",
    )
    check("拖开后不再算吸附态", not native._snapped)

    # -- 拖到左边缘松手：这条走的是真实循环 + 松手落位 --
    user32.SetWindowPos(wt.HWND(hwnd), None, 600, 300, 900, 600, 0x0004 | 0x0010)
    pump(60)
    before = rect_of(hwnd)
    mid_y = (work[1] + work[3]) // 2
    start = (before[0] + 120, before[1] + 23)
    _script(native, [start, (start[0] - 200, mid_y), (work[0] + 2, mid_y), (work[0] + 2, mid_y)])
    native.start_drag(*start)
    wait_until(lambda: not native._dragging.is_set(), 6.0)
    pump(80)
    check("拖到左边缘松手吸附左半屏", rect_of(hwnd) == left_half, f"{rect_of(hwnd)} vs {left_half}")

    # -- 拖到顶沿松手（没选区域）：最大化 --
    before = rect_of(hwnd)
    native.apply_snap(original)
    pump(60)
    before = rect_of(hwnd)
    start = (before[0] + 120, before[1] + 23)
    _script(native, [start, (start[0], work[1] + 2), (start[0], work[1] + 2)])
    native.start_drag(*start)
    wait_until(lambda: not native._dragging.is_set(), 6.0)
    wait_until(lambda: not native._animating.is_set(), 4.0)
    pump(120)
    check("顶到上沿松手最大化", native.is_maximized(), f"zoomed={native.is_maximized()}")
    native.toggle_maximize()
    wait_until(lambda: not native._animating.is_set(), 4.0)
    pump(100)

    # -- 拖边缩放：右边往里收 200px --
    native.apply_snap(original)
    pump(60)
    start_rect = rect_of(hwnd)
    press_y = (start_rect[1] + start_rect[3]) // 2
    _script(
        native,
        [
            (start_rect[2], press_y),
            (start_rect[2] - 100, press_y),
            (start_rect[2] - 200, press_y),
        ],
    )
    native.start_resize("e", start_rect[2], press_y)
    wait_until(lambda: not native._resizing.is_set(), 6.0)
    pump(80)
    shrunk = rect_of(hwnd)
    check(
        "拖右边改宽度",
        shrunk[0] == start_rect[0] and shrunk[2] == start_rect[2] - 200,
        f"{shrunk} vs {start_rect}",
    )

    # -- 拖下边到底：不许盖住任务栏 --
    native.apply_snap(original)
    pump(60)
    start_rect = rect_of(hwnd)
    mid_x = (start_rect[0] + start_rect[2]) // 2
    _script(
        native,
        [(mid_x, start_rect[3]), (mid_x, work[3] + 400), (mid_x, work[3] + 400)],
    )
    native.start_resize("s", mid_x, start_rect[3])
    wait_until(lambda: not native._resizing.is_set(), 6.0)
    pump(80)
    grown = rect_of(hwnd)
    check("拖下边到底停在任务栏上沿", grown[3] == work[3], f"{grown} vs 工作区下沿 {work[3]}")

    # 这台机器任务栏是自动隐藏的，工作区等于整屏，上面那条其实没测到「让出任务栏」。
    # 把工作区换成「底部留 40px 任务栏」的假值再拖一次，才是确定性的验证。
    native.apply_snap(original)
    pump(60)
    start_rect = rect_of(hwnd)
    fake_work = (work[0], work[1], work[2], work[3] - 40)
    native._work_rect_at = lambda _point: fake_work
    mid_x = (start_rect[0] + start_rect[2]) // 2
    _script(
        native,
        [(mid_x, start_rect[3]), (mid_x, work[3] + 400), (mid_x, work[3] + 400)],
    )
    native.start_resize("s", mid_x, start_rect[3])
    wait_until(lambda: not native._resizing.is_set(), 6.0)
    pump(80)
    check(
        "下边不许越过任务栏",
        rect_of(hwnd)[3] == fake_work[3],
        f"{rect_of(hwnd)} vs 任务栏上沿 {fake_work[3]}",
    )
    del native._work_rect_at  # 退回类上的真实实现

    # -- 最大化时不允许拖边 --
    native.apply_snap(original)
    pump(60)
    native.toggle_maximize()
    wait_until(lambda: not native._animating.is_set(), 4.0)
    pump(100)
    zoomed = native.is_maximized()
    refused = native.start_resize("e", rect_of(hwnd)[2] - 2, rect_of(hwnd)[1] + 300)
    check("最大化时拒绝拖边", zoomed and not refused, f"zoomed={zoomed} 返回={refused}")

    # -- 最大化状态下直接吸附：要真的退出最大化 --
    native.apply_snap(zone_rect(3, 0, work))
    pump(80)
    check(
        "最大化状态下也能吸附",
        rect_of(hwnd) == zone_rect(3, 0, work) and not native.is_maximized(),
        f"{rect_of(hwnd)} zoomed={native.is_maximized()}",
    )

    # -- 吸附判定与壳层同一套算路：分段一致性 --
    session = SnapSession()
    state = session.update((work[0] + 2, (work[1] + work[3]) // 2), work)
    check("壳层用的判定与几何模块一致", state.rect == left_half, str(state.rect))

    native._overlay.close()
    user32.DestroyWindow(wt.HWND(hwnd))


def main() -> int:
    geometry_checks()
    overlay_checks()
    native_checks()

    print("\n===== 贴边分屏 / 拖边缩放 / Snap 选择器 =====")
    passed = 0
    for label, ok, note in _results:
        print(f"[{'PASS' if ok else 'FAIL'}] {label:28s} {note}")
        passed += int(ok)
    print(f"----- {passed}/{len(_results)} 通过 -----")
    sys.stdout.flush()
    return 0 if passed == len(_results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
