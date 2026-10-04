"""通用对话框：版本更新日志等。"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .. import __version__, paths

log = logging.getLogger(__name__)

_FALLBACK = f"""# Stent 更新日志

## v{__version__}

当前版本。完整更新日志文件缺失，请检查安装包中的 CHANGELOG.md。
"""


def read_changelog() -> str:
    """读取随包分发的 CHANGELOG.md；缺失时退回内置说明。"""
    try:
        path = paths.changelog_path()
        if path.exists():
            return path.read_text(encoding="utf-8")
        log.warning("未找到更新日志文件：%s", path)
    except Exception:  # noqa: BLE001
        log.warning("读取更新日志失败", exc_info=True)
    return _FALLBACK


class ChangelogDialog(QDialog):
    """版本更新日志（Markdown 渲染）。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("版本更新日志")
        self.setMinimumSize(680, 560)
        self.resize(760, 640)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)

        title = QLabel(f"Stent 更新日志　·　当前版本 v{__version__}")
        title.setObjectName("PageTitle")
        layout.addWidget(title)

        self.view = QTextEdit()
        self.view.setReadOnly(True)
        self.view.setObjectName("ChangelogView")
        self.view.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        try:
            self.view.setMarkdown(read_changelog())
        except Exception:  # noqa: BLE001 - 极老版本 Qt 不支持 setMarkdown
            self.view.setPlainText(read_changelog())
        layout.addWidget(self.view, 1)

        row = QHBoxLayout()
        row.addStretch(1)
        close_button = QPushButton("关闭")
        close_button.setObjectName("Primary")
        close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        close_button.clicked.connect(self.accept)
        row.addWidget(close_button)
        layout.addLayout(row)
