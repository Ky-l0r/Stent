"""统一路径管理。

开发态：数据写在仓库根目录下的 ``_data/``，便于调试与查看。
打包态：数据写在 ``%APPDATA%\\Stent``，程序目录保持只读。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "Stent"


def is_frozen() -> bool:
    """是否运行在 PyInstaller 打包产物中。"""
    return bool(getattr(sys, "frozen", False))


def install_dir() -> Path:
    """程序安装目录（exe 所在目录 / 源码根目录）。"""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resource_dir() -> Path:
    """只读资源目录（skills、图标、敏感词表）。"""
    if is_frozen():
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
        return install_dir() / "_internal"
    return install_dir()


def skills_dir() -> Path:
    """随包分发的 prompt 资产目录（源自 Easel SKILL，已裁剪）。"""
    return resource_dir() / "skills"


def data_dir() -> Path:
    """可写数据根目录。

    可用环境变量 ``STENT_DATA_DIR`` 覆盖（自动化测试与绿色版部署使用）。
    """
    override = os.environ.get("STENT_DATA_DIR")
    if override:
        path = Path(override).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        return path
    if is_frozen():
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        path = Path(base) / APP_NAME
    else:
        path = install_dir() / "_data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _sub(name: str) -> Path:
    path = data_dir() / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_path() -> Path:
    return data_dir() / "config.json"


def db_path() -> Path:
    return data_dir() / "stent.db"


def log_path() -> Path:
    return _sub("logs") / "stent.log"


def cache_dir() -> Path:
    return _sub("cache")


def drafts_dir() -> Path:
    return _sub("drafts")


def outputs_dir() -> Path:
    return _sub("outputs")


def media_dir() -> Path:
    return _sub("media")


def browser_dir() -> Path:
    """Playwright 持久化登录态目录。"""
    return _sub("browser")


def exports_dir() -> Path:
    return _sub("exports")
