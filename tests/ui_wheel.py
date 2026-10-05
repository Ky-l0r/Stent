"""用真实的鼠标滚轮事件验证「悬停滚动是否会误改数值」。

与 ``ui_smoke.py`` 里的 ``QApplication.sendEvent`` 不同，这里通过 Win32
``mouse_event`` 注入真实的滚轮输入，走完整的系统 → Qt 平台插件 → 控件链路，
因此能复现用户实际遇到的情况。

用法::

    python tests/ui_wheel.py
"""

from __future__ import annotations

import ctypes
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

MOUSEEVENTF_WHEEL = 0x0800
WHEEL_DELTA = 120


def _set_cursor(x: int, y: int) -> None:
    ctypes.windll.user32.SetCursorPos(int(x), int(y))


def _get_cursor() -> tuple[int, int]:
    import ctypes.wintypes as wt

    point = wt.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
    return point.x, point.y


def _wheel(clicks: int = 1) -> None:
    for _ in range(abs(clicks)):
        ctypes.windll.user32.mouse_event(
            MOUSEEVENTF_WHEEL, 0, 0, WHEEL_DELTA if clicks > 0 else -WHEEL_DELTA, 0
        )
        time.sleep(0.05)


def main() -> int:
    from stent.app import build_application
    from stent.core.db import db

    db.init()

    from stent.config import config_manager

    config_manager.update(onboarding_done=True, theme="light")

    app = build_application(["stent-wheel"])

    from PySide6.QtCore import Qt

    from stent.ui.context import AppContext
    from stent.ui.main_window import MainWindow

    ctx = AppContext(config_manager)
    ctx.selftest = True
    window = MainWindow(ctx)
    window.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
    area = app.primaryScreen().availableGeometry()
    window.resize(min(1400, area.width() - 100), min(950, area.height() - 100))
    window.move(area.x() + 40, area.y() + 40)
    window.show()
    window.raise_()
    window.activateWindow()
    for _ in range(10):
        app.processEvents()

    window.open_settings()
    for _ in range(10):
        app.processEvents()

    page = window.stack.currentWidget()
    original = _get_cursor()
    results: list[tuple[str, bool, str]] = []

    def probe(widget, label: str, step: int = 1) -> None:
        if widget is None:
            results.append((label, True, "跳过（控件不存在）"))
            return
        window.raise_()
        app.processEvents()
        widget.clearFocus()
        window.setFocus()
        for _ in range(4):
            app.processEvents()
        center = widget.mapToGlobal(widget.rect().center())
        _set_cursor(center.x(), center.y())
        time.sleep(0.25)
        for _ in range(6):
            app.processEvents()
        before = widget.value() if hasattr(widget, "value") else widget.currentIndex()
        focused = widget.hasFocus()
        _wheel(step)
        for _ in range(12):
            app.processEvents()
        time.sleep(0.15)
        for _ in range(12):
            app.processEvents()
        after = widget.value() if hasattr(widget, "value") else widget.currentIndex()
        changed = before != after
        results.append(
            (label, not changed, f"{before} -> {after}（悬停时 hasFocus={focused}）")
        )

    probe(page.provider_combo, "服务商（下拉框）")
    probe(page.timeout_spin, "请求超时（数值框）")
    probe(page.retries_spin, "失败自动重试（数值框）")
    probe(page.temperature_spin, "温度（小数框）")

    # 防误伤：下拉列表是独立弹出窗口，必须仍能用滚轮浏览候选
    from stent.ui.wheel_guard import guarded_target

    view = page.provider_combo.view()
    results.append(
        ("下拉候选列表不被拦截", guarded_target(view) is None, type(view).__name__)
    )
    # 输入框上的滚动应转交给页面，而不是被吞掉
    from stent.ui.wheel_guard import find_scroll_area

    results.append(
        ("数值框能找到所在滚动容器",
         find_scroll_area(page.timeout_spin) is not None,
         type(find_scroll_area(page.timeout_spin)).__name__)
    )

    _set_cursor(*original)
    print("\n===== 真实滚轮误改检查（悬停、不点击）=====")
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
