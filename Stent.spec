# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（onedir，企划书 4.5 打包方案）。

要点：
- 采用 onedir 而非 onefile：启动更快、避免与 Playwright 子进程冲突（企划书风险表）
- Chromium **不内置**：默认复用系统已安装的 Playwright 浏览器，缺失时由应用提示按需下载，
  以此控制安装体积（企划书 4.3「安装体积小于 200MB」）
- 裁掉不用的 Qt 模块与科学计算库，进一步瘦身

瘦身说明（v1.1 实测 232.7MB → 约 190MB）：
``excludes`` 只能挡住 Python 侧的模块导入，**挡不住 Qt 的共享库**——
只要 PySide6 的 hook 认为某个模块被收集过，对应的 ``Qt6*.dll`` 仍会被拷进来。
因此这里额外在 ``COLLECT`` 阶段按文件名过滤二进制与数据：
- QML / Quick / Pdf / VirtualKeyboard：本项目是纯 Widgets + QtSvg，完全用不到
- ``opengl32sw.dll``：Qt 的软件 OpenGL 兜底实现，仅在使用 QOpenGLWidget /
  QtQuick 时才需要；纯 QWidget 走光栅绘制，不创建 GL 上下文
- ``translations/``：应用没有安装 QTranslator，Qt 自带译文永远不会被加载
若后续引入任何依赖 OpenGL / QML 的组件，请把对应条目从下面的过滤表里移除。
"""

from pathlib import Path

ROOT = Path(SPECPATH)
APP_NAME = "Stent"

datas = [
    (str(ROOT / "stent" / "resources"), "stent/resources"),
    (str(ROOT / "stent" / "skills"), "stent/skills"),
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
    "PySide6.QtSvg",
    # 以下模块都是**运行时动态导入**的，PyInstaller 的静态分析看不到它们：
    # - 页面按需懒加载：importlib.import_module(f".pages.{name}")
    # - 平台适配器按需加载：importlib.import_module(".xiaohongshu", "stent.platforms")
    # 漏掉的话，打包后的程序会出现「页面加载失败 / 适配器不可用」，
    # 而源码运行时一切正常（v1.1 打包时踩过）。
    "stent.ui.pages.hotsearch",
    "stent.ui.pages.create",
    "stent.ui.pages.publish",
    "stent.ui.pages.analytics",
    "stent.ui.pages.profile",
    "stent.ui.pages.settings",
    "stent.ui.pages.onboarding",
    "stent.platforms.xiaohongshu",
    "stent.platforms.douyin",
    "stent.platforms.zhihu",
    "stent.platforms.bilibili",
]

#: 不打包的 Qt 共享库（按文件名匹配，小写比较）
DROPPED_BINARIES: tuple[str, ...] = (
    "qt6quick.dll",
    "qt6qml.dll",
    "qt6qmlmodels.dll",
    "qt6qmlmeta.dll",
    "qt6qmlworkerscript.dll",
    "qt6pdf.dll",
    "qt6virtualkeyboard.dll",
    "opengl32sw.dll",
)

#: 不打包的数据目录 / 文件（相对 _internal 的路径前缀，小写比较）
DROPPED_DATA_PREFIXES: tuple[str, ...] = (
    "pyside6/translations/",
)

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

def _prune(items):
    """按名称过滤 PyInstaller 的 (dest, source, type) 三元组。"""
    kept = []
    for entry in items:
        dest = str(entry[0]).replace("\\", "/").lower()
        if dest.rsplit("/", 1)[-1] in DROPPED_BINARIES:
            continue
        if any(dest.startswith(prefix) for prefix in DROPPED_DATA_PREFIXES):
            continue
        kept.append(entry)
    return kept


# 剪掉体积大但用不到的 Qt 组件。
# 放在 excludes 之外，是因为这些是共享库 / 数据文件，不属于 Python 模块，
# excludes 拦不住它们。
coll = COLLECT(
    exe,
    _prune(a.binaries),
    _prune(a.datas),
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)
