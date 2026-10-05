"""验证最大化 / 还原 / 最小化的过渡动画确实在逐帧执行。

判定方式：触发操作后连续采样窗口几何（与不透明度），
- 若采样到多个**中间值**，说明有过渡过程；
- 若一步到位（首帧即终值），说明没有动画。

用法::

    python tests/ui_animation.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> int:
    from stent.app import build_application
    from stent.core.db import db

    db.init()

    from stent.config import config_manager

    # 过渡动画默认关闭，本用例显式开启后再验证逐帧过渡
    config_manager.update(onboarding_done=True, theme="light", ui_animations=True)

    app = build_application(["stent-anim"])

    from PySide6.QtCore import Qt

    from stent.ui.context import AppContext
    from stent.ui.main_window import MainWindow

    ctx = AppContext(config_manager)
    ctx.selftest = True
    window = MainWindow(ctx)
    window.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
    area = app.primaryScreen().availableGeometry()
    window.resize(1100, 720)
    window.move(area.x() + 80, area.y() + 60)
    window.show()
    window.raise_()
    window.activateWindow()

    def wait(ms: int) -> None:
        deadline = time.monotonic() + ms / 1000.0
        while time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.004)

    def sample(attribute: str, count: int = 26, interval: float = 0.012) -> list:
        values = []
        for _ in range(count):
            app.processEvents()
            values.append(getattr(window, attribute)())
            time.sleep(interval)
        return values

    wait(400)
    results: list[tuple[str, bool, str]] = []
    original = window.geometry()

    # ---- 最大化 ----
    window.toggle_maximize()
    widths = sample("width")
    wait(320)
    maximized_ok = window.isMaximized()
    distinct = sorted(set(widths))
    animated = len(distinct) >= 3
    results.append(
        ("最大化过程逐帧过渡",
         animated,
         f"宽度采样 {len(distinct)} 个不同值：{distinct[:3]} … {distinct[-2:]}")
    )
    results.append(
        ("最大化结束状态正确",
         maximized_ok and window.geometry() == area,
         f"isMaximized={maximized_ok} geometry={window.geometry().getRect()} vs {area.getRect()}")
    )
    results.append(
        ("动画后内容尺寸已复位",
         (not window._content_frozen) and window.centralWidget().width() == window.width(),
         f"内容={window.centralWidget().size().toTuple()} 窗口={window.size().toTuple()}"
         "（高度差为状态栏，属正常）")
    )

    # ---- 还原 ----
    window.toggle_maximize()
    widths = sample("width")
    wait(320)
    distinct = sorted(set(widths))
    restored_animated = len(distinct) >= 3
    results.append(
        ("还原过程逐帧过渡",
         restored_animated,
         f"宽度采样 {len(distinct)} 个不同值：{distinct[:3]} … {distinct[-2:]}")
    )
    results.append(
        ("还原结束状态正确",
         (not window.isMaximized()) and window.geometry() == original,
         f"isMaximized={window.isMaximized()} geometry={window.geometry().getRect()} vs {original.getRect()}")
    )

    # ---- 关闭动画后应瞬间切换（性能逃生舱口）----
    from stent.config import config_manager as _cm

    _cm.update(ui_animations=False)
    wait(150)
    enabled = window._animations_enabled()
    was_maximized = window.isMaximized()
    window.toggle_maximize()
    widths_off = sample("width", count=8, interval=0.01)
    wait(300)
    instant = (not enabled) and len(set(widths_off)) <= 2 and window.isMaximized()
    results.append(
        ("关闭动画后瞬间切换",
         instant,
         f"开关={enabled} 切换前 max={was_maximized} 切换后 max={window.isMaximized()} "
         f"宽度值 {len(set(widths_off))} 个 几何={window.geometry().getRect()}")
    )
    window.toggle_maximize()
    wait(300)
    _cm.update(ui_animations=True)
    wait(150)

    # ---- 最小化 ----
    before_min = window.geometry()
    window.minimize_window()
    opacities = sample("windowOpacity")
    wait(320)
    minimized = window.isMinimized()
    distinct_op = sorted({round(v, 3) for v in opacities})
    results.append(
        ("最小化过程有淡出 + 收缩",
         len(distinct_op) >= 3,
         f"不透明度采样 {len(distinct_op)} 个不同值：{distinct_op[:3]} … {distinct_op[-2:]}")
    )
    results.append(
        ("最小化结束状态正确",
         minimized and window.geometry() == before_min,
         f"isMinimized={minimized} geometry={window.geometry().getRect()} vs {before_min.getRect()}")
    )
    results.append(
        ("最小化后不透明度已复位",
         abs(window.windowOpacity() - 1.0) < 0.01,
         str(window.windowOpacity()))
    )

    # 还原窗口与配置（过渡动画默认关闭）
    window.showNormal()
    wait(250)
    _cm.update(ui_animations=False)
    wait(120)

    print("\n===== 过渡动画检查 =====")
    ok = 0
    for label, passed, note in results:
        print(f"[{'PASS' if passed else 'FAIL'}] {label:22s} {note}")
        ok += int(passed)
    print(f"----- {ok}/{len(results)} 通过 -----")
    sys.stdout.flush()

    window.close()
    app.processEvents()
    from stent.ui.workers import shutdown_workers

    shutdown_workers(3000)
    import os

    os._exit(0 if ok == len(results) else 1)


if __name__ == "__main__":
    raise SystemExit(main())
