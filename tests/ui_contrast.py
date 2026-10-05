"""主题对比度检查：找出「暗色主题下文字比背景还暗」这类渲染异常。

逐个抓取可见标签的真实渲染像素，比较文字与背景的亮度。
在暗色主题下，文字应当比背景**亮**；浅色主题下则应当更**暗**。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def scan(theme: str = "dark") -> list[tuple[str, str, str, str]]:
    from stent.app import build_application
    from stent.core.db import db

    db.init()

    from stent.config import config_manager

    config_manager.update(onboarding_done=True, theme=theme)

    app = build_application(["stent-contrast"])

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QLabel

    from stent.ui.context import AppContext
    from stent.ui.main_window import MainWindow

    ctx = AppContext(config_manager)
    ctx.selftest = True
    window = MainWindow(ctx)
    window.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
    area = app.primaryScreen().availableGeometry()
    window.resize(min(1500, area.width() - 60), min(950, area.height() - 60))
    window.move(area.x() + 20, area.y() + 20)
    window.show()
    window.raise_()
    for _ in range(6):
        app.processEvents()

    problems: list[tuple[str, str, str, str]] = []

    def analyze(page_name: str) -> None:
        for label in window.stack.currentWidget().findChildren(QLabel):
            text = label.text().strip()
            if not text or not label.isVisible() or label.width() < 6 or label.height() < 6:
                continue
            try:
                image = label.grab().toImage()
            except Exception:  # noqa: BLE001
                continue
            if image.width() < 4 or image.height() < 4:
                continue
            buckets: dict[tuple[int, int, int], int] = {}
            for y in range(image.height()):
                for x in range(image.width()):
                    color = image.pixelColor(x, y)
                    if color.alpha() < 128:
                        continue
                    key = (color.red(), color.green(), color.blue())
                    buckets[key] = buckets.get(key, 0) + 1
            if len(buckets) < 2:
                continue
            lum = lambda c: 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]  # noqa: E731
            ranked = sorted(buckets.items(), key=lambda kv: -kv[1])
            bg = ranked[0][0]
            # 文字像素：与背景差异最大且占据一定比例的颜色
            candidates = [
                (c, n) for c, n in buckets.items()
                if n >= 3 and abs(lum(c) - lum(bg)) > 25
            ]
            if not candidates:
                continue
            text_color = max(candidates, key=lambda kv: abs(lum(kv[0]) - lum(bg)))[0]
            bg_lum, fg_lum = lum(bg), lum(text_color)
            if theme == "dark" and fg_lum < bg_lum:
                problems.append(
                    (page_name, text[:26],
                     f"#{bg[0]:02x}{bg[1]:02x}{bg[2]:02x}",
                     f"#{text_color[0]:02x}{text_color[1]:02x}{text_color[2]:02x}")
                )
            elif theme == "light" and fg_lum > bg_lum + 60:
                problems.append(
                    (page_name, text[:26],
                     f"#{bg[0]:02x}{bg[1]:02x}{bg[2]:02x}",
                     f"#{text_color[0]:02x}{text_color[1]:02x}{text_color[2]:02x}")
                )

    for key in ("hotsearch", "create", "publish", "analytics", "profile"):
        window.navigate(key)
        for _ in range(4):
            app.processEvents()
        analyze(key)
    window.open_settings()
    for _ in range(6):
        app.processEvents()
    analyze("settings")

    window.close()
    app.processEvents()
    return problems


def main() -> int:
    theme = sys.argv[1] if len(sys.argv) > 1 else "dark"
    problems = scan(theme)
    print(f"\n===== 对比度检查（{theme} 主题）=====")
    if not problems:
        print("未发现异常：所有可见文字与背景的明暗关系正确")
    else:
        seen = set()
        for page, text, bg, fg in problems:
            sig = (page, text)
            if sig in seen:
                continue
            seen.add(sig)
            print(f"[异常] {page:10s} 背景={bg} 文字={fg}  {text!r}")
        print(f"共 {len(seen)} 处")
    import os

    os._exit(1 if problems else 0)


if __name__ == "__main__":
    raise SystemExit(main())
