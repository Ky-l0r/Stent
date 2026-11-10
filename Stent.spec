# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（onedir，企划书 4.5 打包方案）。

要点：
- 采用 onedir 而非 onefile：启动更快、避免与 Playwright 子进程冲突（企划书风险表）
- Chromium **不内置**：默认复用系统已安装的 Playwright 浏览器，缺失时由应用提示按需下载，
  以此控制安装体积（企划书 4.3「安装体积小于 200MB」）
- 窗口用系统自带的 WebView2（pywebview），不打包浏览器内核

v1.2 起界面只剩 pywebview 一套（Qt 与 Electron 壳已删），因此这里不再需要
按文件名过滤 Qt 共享库；``excludes`` 只留纯体积项。
"""

from pathlib import Path

ROOT = Path(SPECPATH)
APP_NAME = "Stent"

datas = [
    (str(ROOT / "stent" / "resources"), "stent/resources"),
    (str(ROOT / "stent" / "skills"), "stent/skills"),
    # Web 前端静态资源：漏掉这一条，打包后窗口会白屏（源码运行却正常）
    (str(ROOT / "stent" / "web"), "stent/web"),
    (str(ROOT / "README.md"), "."),
    (str(ROOT / "CHANGELOG.md"), "."),
    (str(ROOT / "NOTICE"), "."),
    (str(ROOT / "LICENSE"), "."),
]

hiddenimports = [
    "keyring.backends.Windows",
    "keyring.backends.SecretService",
    "playwright",
    "playwright.sync_api",
    "playwright._impl._driver",
    "openai",
    "httpx",
    # 以下模块都是**运行时动态导入**的，PyInstaller 的静态分析看不到它们：
    # 平台适配器按需加载：importlib.import_module(".xiaohongshu", "stent.platforms")
    # 漏掉的话，打包后的程序会出现「适配器不可用」，而源码运行时一切正常（v1.1 踩过）。
    "stent.platforms.xiaohongshu",
    "stent.platforms.douyin",
    "stent.platforms.zhihu",
    "stent.platforms.bilibili",
    # 窗口壳与接口层都是运行时才导入的
    "stent.api",
    "stent.api.server",
    "stent.api.handlers",
    "stent.webapp",
    "webview",
    "webview.platforms.edgechromium",
]

# 明确排除：这些库会显著增大体积且 Stent 不使用
excludes = [
    "tkinter",
    "matplotlib",
    "numpy",
    "pandas",
    "scipy",
    "PIL",
]

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)
