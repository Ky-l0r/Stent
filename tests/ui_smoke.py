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
    # Qt 会把它们当成独立窗口短暂显示，就是用户看到的「小窗口一闪而过」。
    # 只在页面导航期间采样，并排除 Qt 自身用于 tooltip / 弹出菜单的内部窗口。
    flashes: list[str] = []
    INTERNAL_HINTS = ("qt_", "qtooltip", "tooltip")
    INTERNAL_CLASSES = {"QTipLabel", "QMenu", "QComboBoxPrivateContainer", "QToolTip"}

    class FlashSpy(QObject):
        def __init__(self) -> None:
            super().__init__()
            self.active = False

        def eventFilter(self, obj, event):  # noqa: N802
            if not self.active:
                return False
            if event.type() == QEvent.Type.Show and isinstance(obj, QWidget):
                try:
                    name = obj.objectName()
                    cls = type(obj).__name__
                    if cls in INTERNAL_CLASSES or any(h in name.lower() for h in INTERNAL_HINTS):
                        return False
                    if (
                        obj.isWindow()
                        and obj.parent() is None
                        and not isinstance(obj, (QMainWindow, QDialog))
                    ):
                        flashes.append(f"{cls}({name}) {obj.size().toTuple()}")
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
            spy.active = True
            if page == "settings":
                window.open_settings()
            else:
                window.navigate(page)
            app.processEvents()
            spy.active = False
            widget = window.stack.currentWidget()
            status = getattr(widget, "status_light", None)
            note = f"{type(widget).__name__}"
            if status is not None:
                # 指示灯现在是富文本，取纯文本摘要再打印，避免日志里出现 HTML
                summary = getattr(status, "summary", None) or status.text()
                note += f" · 灯={summary or '（无）'}"
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

        # 无边框窗口的关键不变量
        from PySide6.QtCore import Qt as _Qt

        from stent.ui.frameless import HTMAXBUTTON, hwnd_of

        checks: list[tuple[str, bool, str]] = []
        flags = window.windowFlags()
        checks.append(
            ("无边框窗口标志", bool(flags & _Qt.WindowType.FramelessWindowHint), str(flags))
        )
        checks.append(("窗口句柄有效", hwnd_of(window) != 0, str(hwnd_of(window))))
        controls = window.title_bar.controls if window.title_bar else None
        checks.append(("窗口控制按钮齐备", controls is not None, type(controls).__name__))
        if controls is not None:
            visible = all(
                b.isVisible()
                for b in (
                    controls.minimize_button,
                    controls.maximize_button,
                    controls.close_button,
                )
            )
            checks.append(("三个按钮可见", visible, ""))
        checks.append(("DWM 圆角已启用", bool(window._dwm_rounded), str(window._dwm_rounded)))
        checks.append(("原生窗口样式已启用", bool(window._native_frame), str(window._native_frame)))

        geo = window.geometry()
        lp = lambda x, y: ((y & 0xFFFF) << 16) | (x & 0xFFFF)  # noqa: E731
        checks.append(("边缘命中:左", window._hit_test(lp(geo.left() + 2, geo.center().y())) == 10, ""))
        checks.append(("边缘命中:右下", window._hit_test(lp(geo.right() - 2, geo.bottom() - 2)) == 17, ""))
        checks.append(
            ("边缘命中:客户区", window._hit_test(lp(geo.center().x(), geo.center().y())) == 1, "")
        )
        if controls is not None:
            center = controls.maximize_button.mapToGlobal(controls.maximize_button.rect().center())
            checks.append(
                ("最大化按钮是 Snap 热区",
                 window._hit_test(lp(center.x(), center.y())) == HTMAXBUTTON, "")
            )

        window.showMaximized()
        app.processEvents()
        screen = window.screen().availableGeometry()
        checks.append(("最大化贴合工作区", window.geometry() == screen, f"{window.geometry().getRect()} vs {screen.getRect()}"))
        window.showNormal()
        app.processEvents()

        # 滚轮守卫：悬停滚动不应误改数值，聚焦后才允许
        from PySide6.QtCore import QPoint, QPointF

        from PySide6.QtGui import QWheelEvent

        def send_wheel(target) -> None:
            event = QWheelEvent(
                QPointF(target.rect().center()),
                QPointF(target.mapToGlobal(target.rect().center())),
                QPoint(0, 0),
                QPoint(0, 120),
                _Qt.MouseButton.NoButton,
                _Qt.KeyboardModifier.NoModifier,
                _Qt.ScrollPhase.NoScrollPhase,
                False,
            )
            app.sendEvent(target, event)

        window.open_settings()
        app.processEvents()
        settings_page = window.stack.currentWidget()
        spin = getattr(settings_page, "retries_spin", None)
        if spin is not None:
            spin.clearFocus()
            app.processEvents()
            before = spin.value()
            send_wheel(spin)
            app.processEvents()
            checks.append(("滚轮守卫：未聚焦不改值", spin.value() == before, f"{before} -> {spin.value()}"))
            spin.setFocus()
            app.processEvents()
            baseline = spin.value()
            send_wheel(spin)
            app.processEvents()
            # 完全禁用滚轮改值（聚焦后仍应保持不变），键盘与手动输入不受影响
            checks.append(("滚轮守卫：聚焦也不改值", spin.value() == baseline, f"{baseline} -> {spin.value()}"))
            spin.clearFocus()

        # 标签背景应与卡片一致（曾经因 QWidget 底色产生过暗色块）
        from PySide6.QtWidgets import QLabel as _QLabel

        from stent.ui.theme import current_theme as _current_theme
        from stent.ui.theme import palette as _palette

        window.ctx.set_theme("dark")
        for _ in range(8):
            app.processEvents()
        settings_page = window.stack.currentWidget()
        settings_page.repaint()
        app.processEvents()

        form_label = None
        for candidate in settings_page.findChildren(_QLabel):
            if candidate.text() == "服务商" and candidate.isVisible():
                form_label = candidate
                break
        if form_label is not None:
            form_label.repaint()
            app.processEvents()
            from collections import Counter as _Counter

            image = form_label.grab().toImage()
            transparent = 0
            opaque: _Counter = _Counter()
            total = 0
            for y in range(image.height()):
                for x in range(image.width()):
                    color = image.pixelColor(x, y)
                    total += 1
                    if color.alpha() == 0:
                        transparent += 1
                    else:
                        opaque[(color.red(), color.green(), color.blue())] += 1
            card_bg = _palette(_current_theme()).card
            card_rgb = tuple(int(card_bg.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
            if total == 0 or len(opaque) < 2:
                checks.append(("表单标签背景不遮挡卡片", True, "跳过（控件未渲染出内容）"))
            elif transparent > total * 0.4:
                # 背景透明 —— 标签会直接显示卡片底色，不会再出现暗色块
                checks.append(
                    ("表单标签背景不遮挡卡片", True, f"背景透明（{transparent}/{total} 像素）")
                )
            else:
                background = opaque.most_common(1)[0][0]
                checks.append(
                    ("表单标签背景不遮挡卡片",
                     background == card_rgb,
                     f"#{background[0]:02x}{background[1]:02x}{background[2]:02x} vs {card_bg}")
                )
        else:
            checks.append(("表单标签背景不遮挡卡片", True, "跳过（未找到标签）"))

        # 标签云里的控件必须都待在框内。
        # 曾经的 bug：``TagCloud.editor`` 构造时没显式收起，而 ``qt_guard`` 会把无
        # parent 的控件挂到构建宿主名下，于是这个还没轮到上场的输入框以默认几何
        # (0,0,640,480) 可见地趴在标签栏上，露出第二层白底和一条对不齐的虚线边框。
        window.ctx.set_theme("light")
        window.navigate("create")
        for _ in range(10):
            app.processEvents()
        create_page = window.stack.currentWidget()
        cloud = getattr(create_page, "tag_cloud", None)
        if cloud is not None:
            cloud.set_tags(["缅北电诈", "反诈", "防骗指南", "跨境打击", "电诈覆灭"])
            for _ in range(10):
                app.processEvents()
            stray = []
            for child in cloud.children():
                if not isinstance(child, QWidget) or not child.isVisible():
                    continue
                g = child.geometry()
                if (
                    g.x() < 0
                    or g.y() < 0
                    or g.right() > cloud.width()
                    or g.bottom() > cloud.height()
                ):
                    stray.append(
                        f"{type(child).__name__}#{child.objectName()} "
                        f"geo={g.getRect()} 超出 {cloud.width()}x{cloud.height()}"
                    )
            checks.append(
                ("标签云内控件不越界（无杂散底/边框）", not stray, "；".join(stray))
            )
        else:
            checks.append(("标签云内控件不越界（无杂散底/边框）", True, "跳过（未找到标签云）"))

        for name, passed, note in checks:
            print(f"[{'PASS' if passed else 'FAIL'}] {name}" + (f"　{note}" if note else ""))
            ok += int(passed)

        flash_ok = not flashes
        print(
            f"[{'PASS' if flash_ok else 'FAIL'}] 切换页面时的小窗口闪现：{len(flashes)} 次"
            + (f"　{flashes[:6]}" if flashes else "（已消除）")
        )
        total = len(results) + len(checks) + 1
        print(f"----- {ok + int(flash_ok)}/{total} 通过 -----")
        sys.stdout.flush()
        window.close()
        app.processEvents()
        from stent.ui.workers import pending_workers, shutdown_workers

        if pending_workers():
            print(f"[INFO] 仍有 {len(pending_workers())} 个后台网络任务未结束，走强制退出路径")
        shutdown_workers(12000)
        all_ok = ok == len(results) + len(checks) and flash_ok
        os._exit(0 if all_ok else 1)

    QTimer.singleShot(300, step)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
