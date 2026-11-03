"""Stent 启动脚本（开发态直接 ``python main.py``）。

两套前端共用同一个业务层：

- ``python main.py``        → PySide6 桌面界面（现状，可回退）
- ``python main.py --web``  → Web 前端 + WebView2 独立窗口（见 docs/web-architecture.md）
"""

from __future__ import annotations

import multiprocessing
import sys


def _run() -> int:
    if "--web" in sys.argv:
        from stent.webapp import main as web_main

        return web_main([arg for arg in sys.argv[1:] if arg != "--web"])

    from stent.app import main

    return main(sys.argv)


if __name__ == "__main__":
    # PyInstaller + Playwright 子进程场景下必须保护入口
    multiprocessing.freeze_support()
    raise SystemExit(_run())
