"""窗口贴边分屏的几何计算。

只算矩形，不碰 Win32：吸附判定、布局平铺、拖边缩放边界都能脱离窗口单测
（``tests/selftest_snap.py``）。真正摆窗口、画预览的是 ``webapp._NativeWindow``
与 ``webapp._SnapOverlay``。

坐标一律用屏幕坐标 ``(left, top, right, bottom)``，与 ``GetWindowRect`` 一致；
「工作区」指 ``GetMonitorInfoW`` 的 ``rcWork``，已经扣掉了任务栏——所有吸附目标
与缩放上限都从它算起，这是「不遮挡任务栏」的唯一保证点。
"""

from __future__ import annotations

from dataclasses import dataclass

Rect = tuple[int, int, int, int]

# 贴边判定的容差。放太宽会在窗口靠近边缘时误触发，太窄则要用户精准怼到像素。
EDGE_MARGIN = 14
# 角区宽度：落在角区里直接吸附四分之一屏，而不是半屏。
CORNER_BAND = 96
# 选择器卡片与屏幕上沿的距离，必须大于 EDGE_MARGIN：否则光标刚碰到上沿时
# 就已经落在卡片里的缩略图上，一松手就误吸附到某个区域。
PICKER_GAP = 20
PICKER_PAD = 10
THUMB_SIZE = (116, 76)
THUMB_GAP = 10
PICKER_INSET = 8


@dataclass(frozen=True)
class Layout:
    """一种分屏布局：``zones`` 是相对工作区的比例矩形 ``(x1, y1, x2, y2)``。"""

    key: str
    name: str
    zones: tuple[tuple[float, float, float, float], ...]


LAYOUTS: tuple[Layout, ...] = (
    Layout("halves", "左右两栏", ((0.0, 0.0, 0.5, 1.0), (0.5, 0.0, 1.0, 1.0))),
    Layout(
        "thirds",
        "三列",
        ((0.0, 0.0, 1 / 3, 1.0), (1 / 3, 0.0, 2 / 3, 1.0), (2 / 3, 0.0, 1.0, 1.0)),
    ),
    Layout(
        "main-side",
        "左主栏 + 右两栏",
        ((0.0, 0.0, 0.6, 1.0), (0.6, 0.0, 1.0, 0.5), (0.6, 0.5, 1.0, 1.0)),
    ),
    Layout(
        "quads",
        "四宫格",
        (
            (0.0, 0.0, 0.5, 0.5),
            (0.5, 0.0, 1.0, 0.5),
            (0.0, 0.5, 0.5, 1.0),
            (0.5, 0.5, 1.0, 1.0),
        ),
    ),
)

_QUADS = 3  # LAYOUTS 里四宫格的下标，角区吸附复用它


@dataclass(frozen=True)
class Picker:
    """选择器卡片的实际位置：整卡与四个缩略图（都是屏幕坐标）。"""

    card: Rect
    thumbs: tuple[Rect, ...]


@dataclass(frozen=True)
class DragState:
    """一次拖动中「此刻该预览什么 / 松手吸附到哪」的结论。

    ``mode``：
    ``idle`` 没有吸附意图；``zone`` 直接显示 ``rect`` 这块目标区域；
    ``picker`` 显示布局选择器，``hover`` 指向缩略图里的某个区域（``rect`` 随之给出）。
    """

    mode: str = "idle"
    rect: Rect | None = None
    picker: Picker | None = None
    hover: tuple[int, int] | None = None


def rect_size(rect: Rect) -> tuple[int, int]:
    return rect[2] - rect[0], rect[3] - rect[1]


def contains(rect: Rect, point: tuple[int, int]) -> bool:
    """点是否在矩形内（左闭右开，避免相邻区域边界上同时命中两处）。"""
    return rect[0] <= point[0] < rect[2] and rect[1] <= point[1] < rect[3]


def scale_rect(area: Rect, frac: tuple[float, float, float, float]) -> Rect:
    """把比例矩形铺到 ``area`` 上。

    相邻区域共用同一个比例端点，各自四舍五入后结果必然相等，所以不会出现
    一像素缝隙或重叠。
    """
    left, top, right, bottom = area
    width, height = right - left, bottom - top
    x1, y1, x2, y2 = frac
    return (
        left + round(width * x1),
        top + round(height * y1),
        left + round(width * x2),
        top + round(height * y2),
    )


def zone_rect(layout_index: int, zone_index: int, area: Rect) -> Rect:
    layout = LAYOUTS[layout_index]
    return scale_rect(area, layout.zones[zone_index])


def picker_geometry(work: Rect) -> Picker:
    """选择器卡片：水平居中贴在工作区顶部，屏幕太窄时整体等比缩小。"""
    width = work[2] - work[0]
    count = len(LAYOUTS)
    thumb_w, thumb_h = THUMB_SIZE
    pad, gap = PICKER_PAD, THUMB_GAP
    total = pad * 2 + thumb_w * count + gap * (count - 1)
    limit = max(120, width - PICKER_INSET * 2)
    if total > limit:
        factor = limit / total
        thumb_w = max(48, int(thumb_w * factor))
        thumb_h = max(32, int(thumb_h * factor))
        pad = max(6, int(pad * factor))
        gap = max(4, int(gap * factor))
    card_w = pad * 2 + thumb_w * count + gap * (count - 1)
    card_h = pad * 2 + thumb_h
    left = work[0] + max(0, (width - card_w) // 2)
    top = work[1] + PICKER_GAP
    thumbs = tuple(
        (
            left + pad + index * (thumb_w + gap),
            top + pad,
            left + pad + index * (thumb_w + gap) + thumb_w,
            top + pad + thumb_h,
        )
        for index in range(count)
    )
    return Picker(card=(left, top, left + card_w, top + card_h), thumbs=thumbs)


def picker_hover(point: tuple[int, int], picker: Picker) -> tuple[int, int] | None:
    """光标落在哪个缩略图的哪个分区上；不在卡片里返回 None。"""
    for layout_index, thumb in enumerate(picker.thumbs):
        if not contains(thumb, point):
            continue
        zones = LAYOUTS[layout_index].zones
        for zone_index in range(len(zones)):
            if contains(zone_rect(layout_index, zone_index, thumb), point):
                return layout_index, zone_index
        # 缩略图右/下边界四舍五入可能差一像素：落到最后一个分区上，
        # 而不是让用户在最边上「怎么点都没反应」。
        return layout_index, len(zones) - 1
    return None


def preview_rect(layout_index: int, zone_index: int, work: Rect) -> Rect:
    return zone_rect(layout_index, zone_index, work)


class SnapSession:
    """拖动期间的吸附判定。

    需要一点记忆：选择器一旦弹出，只要光标还在卡片上就不能收起——否则用户
    从屏幕上沿往下移到缩略图的过程中卡片会先消失，根本选不中。
    """

    def __init__(self) -> None:
        self._picker_open = False
        self._work: Rect | None = None
        self._picker: Picker | None = None

    @property
    def picker_open(self) -> bool:
        return self._picker_open

    def reset(self) -> None:
        self._picker_open = False
        self._work = None
        self._picker = None

    def update(self, point: tuple[int, int], work: Rect) -> DragState:
        left, top, right, bottom = work
        if self._work != work:
            # 换显示器了：卡片位置与角区都跟着变，旧的选择状态作废。
            # 卡片几何按显示器缓存，拖动期间每 8ms 一次不该反复重算。
            self._picker_open = False
            self._work = work
            self._picker = picker_geometry(work)
        picker = self._picker or picker_geometry(work)
        x, y = point

        if self._picker_open and contains(picker.card, point):
            hover = picker_hover(point, picker)
            rect = preview_rect(hover[0], hover[1], work) if hover else None
            return DragState("picker", rect=rect, picker=picker, hover=hover)

        if y <= top + EDGE_MARGIN:
            if x <= left + CORNER_BAND:
                return DragState("zone", rect=zone_rect(_QUADS, 0, work))
            if x >= right - CORNER_BAND:
                return DragState("zone", rect=zone_rect(_QUADS, 1, work))
            self._picker_open = True
            hover = picker_hover(point, picker)
            rect = preview_rect(hover[0], hover[1], work) if hover else None
            return DragState("picker", rect=rect, picker=picker, hover=hover)

        self._picker_open = False

        if x <= left + EDGE_MARGIN:
            if y <= top + CORNER_BAND:
                return DragState("zone", rect=zone_rect(_QUADS, 0, work))
            if y >= bottom - CORNER_BAND:
                return DragState("zone", rect=zone_rect(_QUADS, 2, work))
            return DragState("zone", rect=(left, top, left + (right - left) // 2, bottom))

        if x >= right - EDGE_MARGIN:
            if y <= top + CORNER_BAND:
                return DragState("zone", rect=zone_rect(_QUADS, 1, work))
            if y >= bottom - CORNER_BAND:
                return DragState("zone", rect=zone_rect(_QUADS, 3, work))
            return DragState("zone", rect=(left + (right - left) // 2, top, right, bottom))

        if y >= bottom - EDGE_MARGIN:
            if x <= left + CORNER_BAND:
                return DragState("zone", rect=zone_rect(_QUADS, 2, work))
            if x >= right - CORNER_BAND:
                return DragState("zone", rect=zone_rect(_QUADS, 3, work))
            half = (bottom - top) // 2
            return DragState("zone", rect=(left, top + half, right, bottom))

        return DragState("idle")


def preview_shapes(state: DragState) -> tuple[tuple[Rect, str], ...]:
    """把拖动结论翻译成「画哪些矩形」，顺序即绘制顺序（后画的盖在上面）。

    颜色不在这里定：色值属于 Win32 的 COLORREF，交给壳层映射；这里只给角色名，
    这样绘制清单本身可以在没有窗口的情况下断言。
    """
    shapes: list[tuple[Rect, str]] = []
    if state.mode == "picker" and state.picker is not None:
        picker = state.picker
        shapes.append((picker.card, "card"))
        for layout_index, thumb in enumerate(picker.thumbs):
            for zone_index in range(len(LAYOUTS[layout_index].zones)):
                hot = state.hover == (layout_index, zone_index)
                role = "slot-hot" if hot else "slot"
                shapes.append((zone_rect(layout_index, zone_index, thumb), role))
    if state.rect is not None:
        shapes.append((state.rect, "zone"))
        shapes.append((state.rect, "zone-frame"))
    return tuple(shapes)


_EDGE_NAMES = {
    "n": frozenset({"top"}),
    "s": frozenset({"bottom"}),
    "e": frozenset({"right"}),
    "w": frozenset({"left"}),
    "ne": frozenset({"top", "right"}),
    "nw": frozenset({"top", "left"}),
    "se": frozenset({"bottom", "right"}),
    "sw": frozenset({"bottom", "left"}),
}


def edge_set(edge: str) -> frozenset[str]:
    """``"se"`` → ``{"bottom", "right"}``；认不出就当成没命中。"""
    return _EDGE_NAMES.get(str(edge or "").strip().lower(), frozenset())


def resize_rect(
    start: Rect,
    edges: frozenset[str],
    dx: int,
    dy: int,
    min_size: tuple[int, int],
    bounds: Rect,
) -> Rect:
    """按拖动位移算出新矩形。

    ``bounds`` 是光标所在显示器的工作区：每一条边都被夹在里面，因此**结果
    不可能盖住任务栏**（工作区已经扣掉任务栏），也不会跑到屏幕外。

    最小尺寸取「配置值 / 当前尺寸 / 屏幕尺寸」三者中最小的：吸附到四分之一屏
    时窗口本来就比最小尺寸小（1080p 的一半只有 960×540，而最小宽度是 1080），
    这时候再按最小尺寸夹会把窗口顶大、连带压住任务栏。已经比下限小的窗口
    只能被拖大，不能继续缩小。
    """
    left, top, right, bottom = start
    min_w = min(min_size[0], start[2] - start[0], bounds[2] - bounds[0])
    min_h = min(min_size[1], start[3] - start[1], bounds[3] - bounds[1])

    if "left" in edges:
        left = min(max(start[0] + dx, bounds[0]), start[2] - min_w)
    if "top" in edges:
        top = min(max(start[1] + dy, bounds[1]), start[3] - min_h)
    if "right" in edges:
        right = max(min(start[2] + dx, bounds[2]), left + min_w)
    if "bottom" in edges:
        bottom = max(min(start[3] + dy, bounds[3]), top + min_h)
    return (left, top, right, bottom)
