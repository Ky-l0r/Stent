"""Stent 应用入口。

职责：日志、数据库初始化、QApplication、首次启动引导、主窗口。
支持 ``--selftest`` 在无界面环境下构建全部页面做冒烟验证。
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Sequence

from . import __version__, paths
from .core.db import db

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# 日志
# --------------------------------------------------------------------------
def setup_logging(level: int = logging.INFO) -> None:
    paths.data_dir()
    root = logging.getLogger()
    if root.handlers:
        return
    root.setLevel(level)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    try:
        file_handler = logging.FileHandler(paths.log_path(), encoding="utf-8")
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
    except Exception:  # pragma: no cover - 日志文件不可写时不影响启动
        pass

    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(fmt)
    root.addHandler(stream)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


# --------------------------------------------------------------------------
# 应用
# --------------------------------------------------------------------------
def build_application(argv: Sequence[str]):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    # 必须在任何界面控件创建之前安装：消除切换页面时的小窗口闪现
    from .ui.qt_guard import install as install_qt_guard

    install_qt_guard()

    QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)
    app = QApplication(list(argv))
    app.setApplicationName("Stent")
    app.setApplicationDisplayName("Stent")
    app.setOrganizationName("Stent")
    app.setApplicationVersion(__version__)
    return app


def parse_args(argv: Sequence[str]) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(prog="Stent", description="Stent · 桌面端社媒内容智能体")
    parser.add_argument("--selftest", action="store_true", help="构建全部页面做冒烟测试后退出")
    parser.add_argument("--reset-onboarding", action="store_true", help="强制显示首次启动引导")
    parser.add_argument("--debug", action="store_true", help="输出调试日志")
    args, rest = parser.parse_known_args(list(argv)[1:])
    return args, rest


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    args, qt_args = parse_args(argv)
    setup_logging(logging.DEBUG if args.debug else logging.INFO)

    log.info("Stent %s 启动，数据目录：%s", __version__, paths.data_dir())
    db.init()

    from .config import config_manager

    if args.reset_onboarding:
        config_manager.update(onboarding_done=False)

    app = build_application([argv[0], *qt_args])

    from .ui.context import AppContext
    from .ui.main_window import MainWindow
    from .ui.pages.onboarding import OnboardingDialog, should_show_onboarding

    ctx = AppContext(config_manager)
    ctx.selftest = bool(args.selftest)

    if args.selftest:
        return run_selftest(app, ctx)

    if should_show_onboarding(ctx):
        dialog = OnboardingDialog(ctx)
        dialog.exec()

    window = MainWindow(ctx)
    window.show()
    exit_code = app.exec()

    # 退出前确保后台线程都已结束，否则 QThread 在运行中被析构会终止进程
    from .ui.workers import shutdown_workers

    if not shutdown_workers(8000):
        log.warning("仍有后台任务未结束，强制退出以避免 QThread 析构崩溃")
        logging.shutdown()
        os._exit(exit_code)
    return exit_code


# --------------------------------------------------------------------------
# 冒烟测试
# --------------------------------------------------------------------------
def run_selftest(app, ctx) -> int:
    """在无界面环境（QT_QPA_PLATFORM=offscreen）下构建全部页面。"""
    from .ui.main_window import NAV_ITEMS, MainWindow

    results: list[tuple[str, bool, str]] = []
    window = MainWindow(ctx)
    window.show()
    app.processEvents()

    for key, title, _icon, _module, _cls in NAV_ITEMS:
        try:
            window.navigate(key)
            app.processEvents()
            page = window.stack.currentWidget()
            page.on_show()
            app.processEvents()
            results.append((f"{key}（{title}）", True, type(page).__name__))
        except Exception as exc:  # noqa: BLE001
            log.exception("页面自检失败：%s", key)
            results.append((f"{key}（{title}）", False, f"{type(exc).__name__}: {exc}"))

    try:
        window.open_settings()
        app.processEvents()
        results.append(("settings（设置）", True, "SettingsPage"))
    except Exception as exc:  # noqa: BLE001
        log.exception("设置页自检失败")
        results.append(("settings（设置）", False, f"{type(exc).__name__}: {exc}"))

    from .ui.pages.onboarding import OnboardingDialog

    try:
        dialog = OnboardingDialog(ctx)
        dialog.show()
        app.processEvents()
        dialog.close()
        results.append(("onboarding（引导页）", True, "OnboardingDialog"))
    except Exception as exc:  # noqa: BLE001
        log.exception("引导页自检失败")
        results.append(("onboarding（引导页）", False, f"{type(exc).__name__}: {exc}"))

    print("\n===== Stent 界面自检 =====")
    ok_count = 0
    for name, ok, note in results:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}　{note}")
        ok_count += int(ok)
    print(f"----- {ok_count}/{len(results)} 通过 -----\n")

    window.close()
    app.processEvents()

    from .ui.workers import shutdown_workers

    shutdown_workers(4000)
    return 0 if ok_count == len(results) else 1

if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
