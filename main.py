"""Stent 启动脚本（开发态直接 ``python main.py``）。"""

from __future__ import annotations

import multiprocessing
import sys


def _run() -> int:
    from stent.app import main

    return main(sys.argv)


if __name__ == "__main__":
    # PyInstaller + Playwright 子进程场景下必须保护入口
    multiprocessing.freeze_support()
    raise SystemExit(_run())
