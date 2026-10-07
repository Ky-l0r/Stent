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


def playwright_browsers_dir() -> Path:
    """Playwright 浏览器内核的**系统级**安装目录。

    沿用 Playwright 自己的规则：``PLAYWRIGHT_BROWSERS_PATH`` 优先；否则
    Windows 取 ``%LOCALAPPDATA%\\ms-playwright``，其他平台取
    ``~/.cache/ms-playwright``。

    特例是 ``0``——它表示「内核随应用一起分发」，是 Playwright 为整体打包
    准备的模式。Stent 不内置内核（企划书 4.3 控制安装体积），因此把它当作
    「没配置」处理，回落到系统目录。
    """
    override = (os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or "").strip()
    if override and override != "0":
        return Path(override).expanduser()
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
        return Path(base) / "ms-playwright"
    return Path.home() / ".cache" / "ms-playwright"


def ensure_playwright_browsers_path() -> str:
    """把 Playwright 的浏览器目录钉到系统级位置，返回最终生效的路径。

    **这是「明明装过内核，打包后却提示浏览器不可用」的根因修复。**

    Playwright 启动驱动进程前有这么一段（``playwright/_impl/_transport.py``）::

        if getattr(sys, "frozen", False) or globals().get("__compiled__"):
            env.setdefault("PLAYWRIGHT_BROWSERS_PATH", "0")

    只要它发现自己跑在 PyInstaller / Nuitka 冻结环境里，就会把内核目录默认
    指向**包内**的 ``playwright/driver/package/.local-browsers``。而 Stent 刻意
    不内置 Chromium，于是打包后必定找不到浏览器，用户看到的却是「请先安装
    playwright 与 chromium」——与真实原因南辕北辙。

    ``setdefault`` 只在变量不存在时写入，所以提前显式设置即可让 Playwright
    回到系统目录。用户自行配置过（且不是 ``0``）时完全尊重用户。
    """
    current = (os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or "").strip()
    if current and current != "0":
        return current
    target = playwright_browsers_dir()
    # 源码运行时 Playwright 本来就用系统目录，不必改动环境变量；
    # 只有冻结态（或被人显式设成 "0"）才需要纠正它的包内默认值。
    if is_frozen() or current == "0":
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(target)
    return str(target)


def exports_dir() -> Path:
    return _sub("exports")


def changelog_path() -> Path:
    """随包分发的更新日志（导航栏「版本更新日志」入口读取它）。"""
    return resource_dir() / "CHANGELOG.md"
