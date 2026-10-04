# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（onedir，企划书 4.5 打包方案）。

要点：
- 采用 onedir 而非 onefile：启动更快、避免与 Playwright 子进程冲突（企划书风险表）
- Chromium **不内置**：默认复用系统已安装的 Playwright 浏览器，缺失时由应用提示按需下载，
  以此控制安装体积（企划书 4.3「安装体积小于 200MB」）
- 裁掉不用的 Qt 模块与科学计算库，进一步瘦身
"""

from pathlib import Path

ROOT = Path(SPECPATH)
APP_NAME = "Stent"

datas = [
    (str(ROOT / "stent" / "resources"), "stent/resources"),
    (str(ROOT / "stent" / "skills"), "stent/skills"),
    (str(ROOT / "README.md"), "."),
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
    "PySide6.QtSvg",
]

# 明确排除：这些库会显著增大体积且 Stent 不使用
excludes = [
    "tkinter",
    "matplotlib",
    "numpy",
    "pandas",
    "scipy",
    "PIL",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtQuick",
    "PySide6.QtQml",
    "PySide6.Qt3DCore",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtBluetooth",
    "PySide6.QtNfc",
    "PySide6.QtPositioning",
    "PySide6.QtSensors",
    "PySide6.QtSerialPort",
    "PySide6.QtTest",
    "PySide6.QtDesigner",
    "PySide6.QtHelp",
    "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtSql",
    "PySide6.QtSpatialAudio",
    "PySide6.QtStateMachine",
    "PySide6.QtTextToSpeech",
    "PySide6.QtUiTools",
    "PySide6.QtWebChannel",
    "PySide6.QtWebSockets",
    "PySide6.QtNetworkAuth",
    "PySide6.QtRemoteObjects",
    "PySide6.QtScxml",
    "PySide6.QtGraphs",
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
