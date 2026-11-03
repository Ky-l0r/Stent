"""Web 前端桌面壳：一个 WebView2 窗口 + 一个只监听回环的接口服务。

它替代的是 ``python main.py``（PySide6 界面），但**不替代业务层**：
``stent/services`` 与 ``stent/platforms`` 一行没动，仍然由这个进程直接调用。

用法::

    python -m stent.webapp              # 桌面窗口（需要 pywebview + WebView2 运行时）
    python -m stent.webapp --browser    # 只起服务，用系统浏览器调试前端
    python -m stent.webapp --port 8765  # 固定端口，方便前端热调

与旧界面的关系：``stent/ui/`` 暂时保留，是回退路径与视觉对照物（见 docs/web-architecture.md）。
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
import webbrowser
from collections.abc import Sequence
from typing import Any

from . import paths
from .api import build_app

log = logging.getLogger(__name__)

WINDOW_TITLE = "Stent"
WINDOW_SIZE = (1380, 854)
MIN_SIZE = (1080, 700)
BACKGROUND = "#F6F5FA"


def _has_webview() -> tuple[bool, str]:
    """探测 pywebview 与 WebView2 运行时是否可用。"""
    try:
        import webview  # noqa: F401, PLC0415
    except ImportError:
        return False, "未安装 pywebview（pip install pywebview）"
    except Exception as exc:  # pragma: no cover - 依赖存在但底层缺失
        return False, f"pywebview 加载失败：{exc}"
    return True, ""


class ShellApi:
    """暴露给前端的壳层能力（窗口控制、原生文件选择）。

    只有本机窗口里的页面能调到它——它不经过 HTTP，不存在的端口也就无从访问。
    """

    def __init__(self) -> None:
        self.window: Any = None

    # -- 窗口 ------------------------------------------------------------
    def minimize(self) -> None:
        if self.window is not None:
            self.window.minimize()

    def toggle_maximize(self) -> None:
        if self.window is None:
            return
        # pywebview 各平台能力不完全一致，按可用方法退让
        try:
            if getattr(self.window, "maximized", False) and hasattr(self.window, "restore"):
                self.window.restore()
            elif hasattr(self.window, "maximize"):
                self.window.maximize()
        except Exception:  # pragma: no cover - 窗口已销毁
            log.debug("窗口最大化切换失败", exc_info=True)

    def close(self) -> None:
        if self.window is not None:
            self.window.destroy()

    def move_window(self, dx: int, dy: int) -> None:
        """自绘标题栏拖动：按屏幕坐标位移（WebView 里拿不到窗口原生拖拽）。"""
        if self.window is None:
            return
        try:
            self.window.move(int(self.window.x + dx), int(self.window.y + dy))
        except Exception:  # pragma: no cover
            log.debug("窗口移动失败", exc_info=True)

    def open_external(self, url: str) -> bool:
        """用系统浏览器打开外链，不要在应用窗口里跳走。"""
        target = str(url or "").strip()
        if not target.lower().startswith(("http://", "https://")):
            return False
        webbrowser.open(target)
        return True

    # -- 文件 ------------------------------------------------------------
    def pick_files(self, kind: str = "media") -> list[str]:
        """弹原生文件选择框，返回绝对路径数组。

        浏览器做不到这件事（``<input type=file>`` 拿到的是不可读的伪路径），
        所以媒体选择必须由壳层代劳。
        """
        if self.window is None:
            return []
        try:
            import webview  # noqa: PLC0415
        except ImportError:  # pragma: no cover
            return []

        if kind == "image":
            file_types = ("图片 (*.png;*.jpg;*.jpeg;*.webp)", "所有文件 (*.*)")
        else:
            file_types = (
                "媒体文件 (*.png;*.jpg;*.jpeg;*.webp;*.mp4;*.mov)",
                "所有文件 (*.*)",
            )
        try:
            picked = self.window.create_file_dialog(
                webview.OPEN_DIALOG, allow_multiple=True, file_types=file_types
            )
        except Exception:  # pragma: no cover - 对话框被取消或不可用
            log.debug("文件选择失败", exc_info=True)
            return []
        return [str(p) for p in (picked or [])]


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="stent-webapp", description="Stent Web 前端桌面壳")
    parser.add_argument("--browser", action="store_true", help="不起窗口，用系统浏览器打开")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（只允许回环）")
    parser.add_argument("--port", type=int, default=0, help="端口，0 表示由系统分配")
    parser.add_argument("--debug", action="store_true", help="打开 WebView 调试工具与调试日志")
    return parser.parse_args(list(argv))


def run(argv: Sequence[str] | None = None) -> int:
    from .app import ensure_utf8_console  # 复用同一套控制台编码处理

    ensure_utf8_console()
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if args.host not in {"127.0.0.1", "::1", "localhost"}:
        print("出于安全考虑，只允许监听回环地址（127.0.0.1）", file=sys.stderr)
        return 2

    paths.data_dir()
    app = build_app(host=args.host, port=args.port)
    url = app.start()
    print(f"Stent 接口服务：{url}")
    print(f"数据目录：{paths.data_dir()}")

    ok, reason = _has_webview()
    if args.browser or not ok:
        if not ok and not args.browser:
            print(f"未能启动桌面窗口：{reason}", file=sys.stderr)
            print("已改用系统浏览器打开；装好 pywebview 后会使用独立窗口。", file=sys.stderr)
        webbrowser.open(url)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n正在退出…")
        finally:
            app.stop()
        return 0

    import webview  # noqa: PLC0415 - 惰性导入：没有窗口模式时不该加载它

    shell = ShellApi()
    window = webview.create_window(
        WINDOW_TITLE,
        url,
        width=WINDOW_SIZE[0],
        height=WINDOW_SIZE[1],
        min_size=MIN_SIZE,
        frameless=True,
        easy_drag=False,
        background_color=BACKGROUND,
        js_api=shell,
    )
    shell.window = window
    try:
        webview.start(debug=args.debug)
    finally:
        app.stop()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return run(argv)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
