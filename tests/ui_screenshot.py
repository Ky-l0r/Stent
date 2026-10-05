"""把真实窗口的关键区域截图，用于人工/自动核对视觉效果。

用法::

    python tests/ui_screenshot.py            # 默认浅色主题
    python tests/ui_screenshot.py dark       # 暗色主题

产物写入 ``_data/screenshots/``：
- ``corner-<theme>.png``    左上角特写（验证窗口圆角与系统阴影）
- ``topbar-<theme>.png``    顶栏右侧特写（验证窗口控制按钮）
- ``full-<theme>.png``      整窗截图
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main(theme: str = "light") -> int:
    from stent.app import build_application
    from stent.core.db import db

    db.init()

    from stent.config import config_manager

    config_manager.update(onboarding_done=True, theme=theme)

    app = build_application(["stent-shot"])

    from PySide6.QtCore import QTimer

    from stent.ui.context import AppContext
    from stent.ui.main_window import MainWindow

    ctx = AppContext(config_manager)
    ctx.selftest = True
    window = MainWindow(ctx)
    # 截图期间置顶，避免被其它窗口遮挡
    from PySide6.QtCore import Qt

    window.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
    # 居中，这样角落截图能看到窗口四周的系统阴影
    from PySide6.QtWidgets import QApplication

    area = QApplication.primaryScreen().availableGeometry()
    width, height = min(1380, area.width() - 120), min(880, area.height() - 120)
    window.resize(width, height)
    window.move(
        area.x() + (area.width() - width) // 2,
        area.y() + (area.height() - height) // 2,
    )
    window.show()
    window.raise_()
    window.activateWindow()

    out_dir = Path(__file__).resolve().parent.parent / "_data" / "screenshots"
    out_dir.mkdir(parents=True, exist_ok=True)

    def capture() -> None:
        window.raise_()
        window.activateWindow()
        app.processEvents()
        screen = window.screen()
        shot = screen.grabWindow(0)  # 整个桌面（含窗口周围，便于看阴影）
        dpr = shot.devicePixelRatio() or 1.0

        def region(rect) -> object:
            scaled = type(rect)(
                int(rect.x() * dpr), int(rect.y() * dpr),
                int(rect.width() * dpr), int(rect.height() * dpr),
            )
            return shot.copy(scaled)

        from PySide6.QtCore import QRect

        frame = window.frameGeometry()
        # 左上角：含窗口外 90px，用于观察圆角与阴影
        corner = QRect(frame.x() - 90, frame.y() - 90, 420, 320)
        region(corner).save(str(out_dir / f"corner-{theme}.png"))

        # 顶栏右侧：窗口控制按钮
        topbar = QRect(frame.right() - 420, frame.y() - 10, 440, 80)
        region(topbar).save(str(out_dir / f"topbar-{theme}.png"))

        # 整窗
        region(frame).save(str(out_dir / f"full-{theme}.png"))

        print(f"截图已保存到 {out_dir}")
        print(f"  窗口几何: {frame.getRect()}  设备像素比: {dpr}")
        print(f"  corner-{theme}.png / topbar-{theme}.png / full-{theme}.png")
        sys.stdout.flush()
        window.close()
        app.processEvents()
        from stent.ui.workers import shutdown_workers

        shutdown_workers(3000)
        import os

        os._exit(0)

    QTimer.singleShot(1400, capture)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "light"))
