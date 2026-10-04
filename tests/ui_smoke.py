"""真实窗口下的 UI 冒烟：遍历全部页面并在两套主题间切换。

与 ``main.py --selftest``（离屏、只构建页面）不同，本脚本在**真实窗口**中运行，
用于验证实际渲染路径：主题切换、表格委托重绘、状态灯配色、页面懒加载。

用法::

    python tests/ui_smoke.py          # 自动跑完并退出
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PAGES = ("hotsearch", "create", "publish", "analytics", "profile")
STEPS: list[tuple[str, str]] = []
for _theme in ("light", "dark"):
    for _page in PAGES:
        STEPS.append((_theme, _page))
STEPS.append(("light", "settings"))


def main() -> int:
    from stent.app import build_application, setup_logging
    from stent.core.db import db

    setup_logging()
    db.init()

    from stent.config import config_manager

    config_manager.update(onboarding_done=True)

    app = build_application(["stent-ui-smoke"])

    from PySide6.QtCore import QEvent, QObject, QTimer
    from PySide6.QtWidgets import QDialog, QMainWindow, QWidget

    from stent.ui.context import AppContext
    from stent.ui.main_window import MainWindow

    # 统计「不应出现的顶层窗口」：切换页面时若控件没有 parent，
    # Qt 会把它们当成独立窗口短暂显示，就是用户看到的「小窗口一闪而过」
    flashes: list[str] = []

    class FlashSpy(QObject):
        def eventFilter(self, obj, event):  # noqa: N802
            if event.type() == QEvent.Type.Show and isinstance(obj, QWidget):
                try:
                    if obj.isWindow() and obj.parent() is None and not isinstance(obj, (QMainWindow, QDialog)):
                        name = type(obj).__name__
                        if not obj.objectName().startswith("qt_"):
                            flashes.append(f"{name}({obj.objectName()})")
                except RuntimeError:
                    pass
            return False

    spy = FlashSpy()
    app.installEventFilter(spy)

    ctx = AppContext(config_manager)
    window = MainWindow(ctx)
    window.show()
    flashes.clear()  # 主窗口自身的显示不算

    results: list[tuple[str, str, bool, str]] = []
    index = 0

    def step() -> None:
        nonlocal index
        if index >= len(STEPS):
            finish()
            return
        theme, page = STEPS[index]
        index += 1
        try:
            ctx.set_theme(theme)
            if page == "settings":
                window.open_settings()
            else:
                window.navigate(page)
            app.processEvents()
            widget = window.stack.currentWidget()
            status = getattr(widget, "status_light", None)
            note = f"{type(widget).__name__}"
            if status is not None:
                note += f" · 灯={status.text() or '（无）'}"
            results.append((theme, page, True, note))
        except Exception as exc:  # noqa: BLE001
            results.append((theme, page, False, f"{type(exc).__name__}: {exc}"))
        QTimer.singleShot(420, step)

    def finish() -> None:
        print("\n===== 真实窗口 UI 冒烟 =====")
        ok = 0
        for theme, page, passed, note in results:
            print(f"[{'PASS' if passed else 'FAIL'}] {theme:5s} {page:10s} {note}")
            ok += int(passed)
        flash_ok = not flashes
        print(
            f"[{'PASS' if flash_ok else 'FAIL'}] 切换页面时的小窗口闪现：{len(flashes)} 次"
            + (f"　{flashes[:6]}" if flashes else "（已消除）")
        )
        total = len(results) + 1
        print(f"----- {ok + int(flash_ok)}/{total} 通过 -----")
        sys.stdout.flush()
        window.close()
        app.processEvents()
        from stent.ui.workers import shutdown_workers

        alive = shutdown_workers(12000)
        os._exit(0 if ok == len(results) and flash_ok and alive else 1)

    QTimer.singleShot(300, step)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
