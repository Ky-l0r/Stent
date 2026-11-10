"""启动期公共设施：控制台编码与日志。

从原 PySide6 入口 ``stent/app.py`` 里搬出来的——那套界面已经删了，但这两件事
与界面无关，Web 壳（``stent/webapp.py``）与自检脚本都要用。
"""

from __future__ import annotations

import logging
import sys

from . import paths


def ensure_utf8_console() -> None:
    """把标准输出 / 错误流切到 UTF-8。

    日志与自检输出都是中文。英文版 Windows（控制台编码 cp1252 / cp437）上直接
    print 会 UnicodeEncodeError，整个启动流程当场断掉。这不是「用户环境特殊」：
    CI 的 windows-latest 就是英文环境。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):  # 被重定向成非文本流时跳过
            continue


def setup_logging(level: int = logging.INFO) -> None:
    """日志同时写数据目录下的 ``logs/stent.log`` 与 stderr。

    打包后 ``console=False`` 没有控制台，只靠 basicConfig 的话日志等于丢失；
    用户报「白屏 / 起不来」时，文件日志是唯一能看的东西。
    """
    paths.data_dir()
    root = logging.getLogger()
    if root.handlers:
        return
    root.setLevel(level)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    try:
        file_handler = logging.FileHandler(paths.log_path(), encoding="utf-8")
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
    except Exception:  # pragma: no cover - 日志文件不可写时不影响启动
        pass

    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(fmt)
    root.addHandler(stream)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
