"""窗口行为回归：最大化 ↔ 还原、顶栏拖动与鼠标同步。

两个都曾经出过错，且都需要真实窗口才暴露：

- **最大化点不回去**：旧实现自己 ``setGeometry(工作区) + setWindowState``，
  Windows 的还原位置与 Qt 的 normalGeometry 各记一套，清掉状态标志后几何仍停在
  最大化矩形上，看起来「点了没反应」。
- **最大化越界**：补上 ``WS_THICKFRAME`` 之后系统认为自己有 8px 边框，
  1920×1080 的屏幕会得到 ``(-8, -8, 1936, 1096)`` 的窗口：四边被裁、压住任务栏。
- **拖动不同步**：用 ``WM_NCLBUTTONDOWN`` 兜底时 ``lParam`` 传 0，系统退回
  ``GetMessagePos()`` 取到过期的光标位置，整段拖动都带着偏移。

拖动部分用 Win32 ``mouse_event`` 注入真实输入，走完整的
系统 → 平台插件 → Qt 链路，因此能反映用户真实手感（与 ``ui_wheel.py`` 同思路）。

用法::

    python tests/ui_window.py
"""

from __future__ import annotations

import ctypes
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
user32 = ctypes.windll.user32

#: 拖动时每一步的位移（小于系统的拖动阈值，避免被判成双击/拖拽取消）
STEP_X = 24
STEP_Y = 15
STEPS = 6


def _set_cursor(x: int, y: int) -> None:
    user32.SetCursorPos(int(x), int(y))


def _get_cursor() -> tuple[int, int]:
    import ctypes.wintypes as wt

    point = wt.POINT()
    user32.GetCursorPos(ctypes.byref(point))
    return point.x, point.y


def main() -> int:
    from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget
    from stent.ui.frameless import FramelessWindow, TitleBar

    app = QApplication(sys.argv)
    area = app.primaryScreen().availableGeometry()

    class Probe(FramelessWindow):
        def __init__(self) -> None:
            super().__init__()
            central = QWidget()
            layout = QVBoxLayout(central)
            layout.setContentsMargins(0, 0, 0, 0)
            self.title_bar = TitleBar(height=58)
            layout.addWidget(self.title_bar)
            layout.addWidget(QLabel("window-state probe"), 1)
            self.setCentralWidget(central)

    window = Probe()
    original = (area.x() + 120, area.y() + 100, 900, 600)
    window.setGeometry(*original)
    window.show()
    window.raise_()
    window.activateWindow()

    def wait(ms: int) -> None:
        deadline = time.monotonic() + ms / 1000.0
        while time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.004)

    wait(500)
    results: list[tuple[str, bool, str]] = []

    # ---- 最大化：几何必须正好是工作区 ----
    window.toggle_maximize()
    wait(300)
    geo = window.geometry()
    results.append(
        (
            "最大化后贴合工作区",
            window.isMaximized() and geo == area,
            f"isMaximized={window.isMaximized()} geometry={geo.getRect()} 工作区={area.getRect()}",
        )
    )

    # ---- 再点一次必须回到原来的尺寸（本次修复的主诉）----
    window.toggle_maximize()
    wait(300)
    geo = window.geometry()
    results.append(
        (
            "还原后回到原尺寸",
            (not window.isMaximized()) and geo.getRect() == original,
            f"isMaximized={window.isMaximized()} geometry={geo.getRect()} 期望={original}",
        )
    )

    # ---- 连续往返三次仍然稳定（防止「只能还原一次」）----
    stable = True
    notes = []
    for _ in range(3):
        window.toggle_maximize()
        wait(220)
        max_ok = window.isMaximized() and window.geometry() == area
        window.toggle_maximize()
        wait(220)
        normal_ok = (not window.isMaximized()) and window.geometry().getRect() == original
        stable = stable and max_ok and normal_ok
        notes.append(f"{max_ok}/{normal_ok}")
    results.append(("连续往返三次稳定", stable, "（最大化/还原：" + " ".join(notes) + "）"))

    # ---- 拖动：窗口位移必须与鼠标位移逐个相等 ----
    window.setGeometry(*original)
    wait(200)
    start = (original[0] + 480, original[1] + 30)
    points = [(start[0] + STEP_X * i, start[1] + STEP_Y * i) for i in range(1, STEPS + 1)]
    expected = (STEP_X * STEPS, STEP_Y * STEPS)
    before = window.geometry().topLeft()
    original_cursor = _get_cursor()
    _set_cursor(*start)
    wait(200)
    done = threading.Event()

    def inject() -> None:
        # 按下之后留出时间让主线程进入系统移动循环，再开始移动；
        # 这里压缩成 60ms 一步，接近真实鼠标的采样率
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
    deadline = time.monotonic() + 5.0
    while not done.is_set() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    thread.join(1.0)
    wait(200)
    _set_cursor(*original_cursor)

    after = window.geometry().topLeft()
    actual = (after.x() - before.x(), after.y() - before.y())
    results.append(
        (
            "拖动位移与鼠标一致",
            actual == expected,
            f"窗口位移={actual} 鼠标位移={expected} 误差={(actual[0] - expected[0], actual[1] - expected[1])}",
        )
    )

    # ---- 拖动后再最大化/还原，位置仍应记住 ----
    window.toggle_maximize()
    wait(250)
    window.toggle_maximize()
    wait(250)
    results.append(
        (
            "拖动后仍能正确还原",
            (not window.isMaximized())
            and window.geometry().getRect()
            == (original[0] + expected[0], original[1] + expected[1], original[2], original[3]),
            f"geometry={window.geometry().getRect()}",
        )
    )

    print("\n===== 窗口行为检查 =====")
    ok = 0
    for label, passed, note in results:
        print(f"[{'PASS' if passed else 'FAIL'}] {label:16s} {note}")
        ok += int(passed)
    print(f"----- {ok}/{len(results)} 通过 -----")
    sys.stdout.flush()

    window.close()
    app.processEvents()

    import os

    os._exit(0 if ok == len(results) else 1)


if __name__ == "__main__":
    raise SystemExit(main())
