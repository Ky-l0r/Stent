"""Stent 启动脚本。

- ``python main.py``              → Web 前端（WebView2 独立窗口，当前默认）
- ``python main.py --qt``         → 旧的 PySide6 界面（回退用，功能完整）
- ``python main.py --selftest``   → 仍走 PySide6：无头构建全部页面做冒烟（CI 用）

为什么 ``--selftest`` 归到 Qt 侧：Web 前端没有「无头构建页面」这回事，
它的等价检查是 ``tests/ui_web_smoke.py``（真浏览器渲染）。
"""

from __future__ import annotations

import multiprocessing
import sys

#: 命中任一参数就走旧的 PySide6 界面
_QT_FLAGS = {"--qt", "--selftest", "--reset-onboarding"}


def _run() -> int:
    if _QT_FLAGS & set(sys.argv[1:]):
        from stent.app import main

        return main([arg for arg in sys.argv if arg != "--qt"])

    from stent.webapp import main as web_main

    # --web 保留为兼容写法：以前默认是 Qt，老命令与旧文档里可能还带着它
    return web_main([arg for arg in sys.argv[1:] if arg != "--web"])


if __name__ == "__main__":
    # PyInstaller + Playwright 子进程场景下必须保护入口
    multiprocessing.freeze_support()
    raise SystemExit(_run())
