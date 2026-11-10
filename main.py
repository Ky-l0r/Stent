"""Stent 启动脚本。

- ``python main.py``            → WebView2 独立窗口（唯一入口）
- ``python main.py --browser``  → 只起服务，用系统浏览器调试前端

界面相关的检查不走这个脚本：``tests/ui_web_smoke.py``（真浏览器渲染）、
``tests/ui_web_window.py``（真窗口 + 注入鼠标验最大化 / 拖动 / 最小化）。
"""

from __future__ import annotations

import multiprocessing
import sys


def _run() -> int:
    from stent.webapp import main as web_main

    # --web 保留为兼容写法：早期默认是 Qt 界面，老命令与旧文档里可能还带着它
    return web_main([arg for arg in sys.argv[1:] if arg != "--web"])


if __name__ == "__main__":
    # PyInstaller + Playwright 子进程场景下必须保护入口
    multiprocessing.freeze_support()
    raise SystemExit(_run())
