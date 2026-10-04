"""主题与视觉规范（企划书 5.1 / 5.3）。

简洁 · 留白 · 卡片化 · 一屏完成 · 暗色可选
浅色为主（白底、灰卡片、单一强调色），暗色模式可切换。
圆角：卡片 8-12px，按钮 6px；内容区间距不小于 24px。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Palette:
    name: str
    bg: str
    bg_alt: str
    card: str
    card_hover: str
    border: str
    border_strong: str
    text: str
    text_sub: str
    text_faint: str
    accent: str
    accent_hover: str
    accent_soft: str
    on_accent: str
    success: str
    warning: str
    danger: str
    sidebar: str
    sidebar_active: str
    topbar: str
    shadow: str


LIGHT = Palette(
    name="light",
    bg="#F5F6F8",
    bg_alt="#EDEFF3",
    card="#FFFFFF",
    card_hover="#F8F9FB",
    border="#E4E7EC",
    border_strong="#D0D5DD",
    text="#1B1F27",
    text_sub="#5B6472",
    text_faint="#98A2B3",
    accent="#3B6EF6",
    accent_hover="#2F5FE0",
    accent_soft="#EAF0FE",
    on_accent="#FFFFFF",
    success="#12B76A",
    warning="#F79009",
    danger="#F04438",
    sidebar="#FFFFFF",
    sidebar_active="#EAF0FE",
    topbar="#FFFFFF",
    shadow="rgba(16, 24, 40, 0.06)",
)

DARK = Palette(
    name="dark",
    bg="#14161B",
    bg_alt="#1A1D24",
    card="#1E222A",
    card_hover="#252A34",
    border="#2C313C",
    border_strong="#3A404D",
    text="#E7E9EE",
    text_sub="#A3ABB9",
    text_faint="#6E7787",
    accent="#5B8DEF",
    accent_hover="#6F9BF2",
    accent_soft="#22304A",
    on_accent="#0F1116",
    success="#32D583",
    warning="#FDB022",
    danger="#F97066",
    sidebar="#1A1D24",
    sidebar_active="#22304A",
    topbar="#1A1D24",
    shadow="rgba(0, 0, 0, 0.35)",
)


def palette(theme: str) -> Palette:
    return DARK if (theme or "").lower() == "dark" else LIGHT


#: 中文优先字体栈（企划书 5.3：中文微软雅黑/思源黑体，英文 Inter 或系统默认）
FONT_FAMILY = '"Inter", "Microsoft YaHei UI", "Microsoft YaHei", "Source Han Sans SC", "PingFang SC", sans-serif'
MONO_FAMILY = '"Cascadia Mono", "Consolas", "JetBrains Mono", monospace'


def build_qss(theme: str) -> str:
    """生成全局样式表。"""
    p = palette(theme)
    return f"""
* {{
    font-family: {FONT_FAMILY};
    font-size: 13px;
    color: {p.text};
    outline: none;
}}

QWidget {{
    background: {p.bg};
}}

QMainWindow, QDialog {{
    background: {p.bg};
}}

/* ---------- 顶栏 ---------- */
#TopBar {{
    background: {p.topbar};
    border-bottom: 1px solid {p.border};
}}
#BrandName {{
    font-size: 16px;
    font-weight: 600;
    letter-spacing: 0.5px;
}}
#BrandSub {{
    font-size: 11px;
    color: {p.text_faint};
}}
#TopBar QPushButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 5px 10px;
}}
#TopBar QPushButton:hover {{
    background: {p.bg_alt};
    border-color: {p.border};
}}

/* ---------- 侧栏 ---------- */
#Sidebar {{
    background: {p.sidebar};
    border-right: 1px solid {p.border};
}}
#NavButton {{
    background: transparent;
    border: none;
    border-radius: 8px;
    padding: 9px 12px;
    text-align: left;
    font-size: 13.5px;
    color: {p.text_sub};
}}
#NavButton:hover {{
    background: {p.bg_alt};
    color: {p.text};
}}
#NavButton:checked {{
    background: {p.sidebar_active};
    color: {p.accent};
    font-weight: 600;
}}
#NavBadge {{
    font-size: 10.5px;
    color: {p.text_faint};
    padding: 1px 6px;
    border: 1px solid {p.border};
    border-radius: 8px;
}}

/* ---------- 卡片 ---------- */
#Card {{
    background: {p.card};
    border: 1px solid {p.border};
    border-radius: 10px;
}}
#CardTitle {{
    font-size: 14px;
    font-weight: 600;
}}
#CardHint {{
    font-size: 12px;
    color: {p.text_sub};
}}
#PageTitle {{
    font-size: 20px;
    font-weight: 600;
}}
#PageSubtitle {{
    font-size: 12.5px;
    color: {p.text_sub};
}}
#SectionLabel {{
    font-size: 12px;
    font-weight: 600;
    color: {p.text_sub};
}}
#Muted {{
    color: {p.text_sub};
    font-size: 12px;
}}
#Faint {{
    color: {p.text_faint};
    font-size: 11.5px;
}}
#StatValue {{
    font-size: 22px;
    font-weight: 600;
}}
#StatLabel {{
    font-size: 11.5px;
    color: {p.text_sub};
}}
#ScoreBadge {{
    font-size: 12px;
    font-weight: 600;
    padding: 2px 8px;
    border-radius: 10px;
}}

/* ---------- 按钮 ---------- */
QPushButton {{
    background: {p.card};
    border: 1px solid {p.border_strong};
    border-radius: 6px;
    padding: 6px 14px;
    color: {p.text};
}}
QPushButton:hover {{
    background: {p.card_hover};
    border-color: {p.accent};
}}
QPushButton:disabled {{
    color: {p.text_faint};
    background: {p.bg_alt};
    border-color: {p.border};
}}
QPushButton#Primary {{
    background: {p.accent};
    border: 1px solid {p.accent};
    color: {p.on_accent};
    font-weight: 600;
}}
QPushButton#Primary:hover {{
    background: {p.accent_hover};
    border-color: {p.accent_hover};
}}
QPushButton#Primary:disabled {{
    background: {p.border_strong};
    border-color: {p.border_strong};
    color: {p.bg};
}}
QPushButton#Danger {{
    color: {p.danger};
    border-color: {p.danger};
    background: transparent;
}}
QPushButton#Danger:hover {{
    background: {p.danger};
    color: #FFFFFF;
}}
QPushButton#Ghost {{
    background: transparent;
    border-color: transparent;
    color: {p.text_sub};
    padding: 5px 8px;
}}
QPushButton#Ghost:hover {{
    background: {p.bg_alt};
    color: {p.text};
}}

/* ---------- 输入控件 ---------- */
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox {{
    background: {p.card};
    border: 1px solid {p.border_strong};
    border-radius: 6px;
    padding: 6px 9px;
    selection-background-color: {p.accent};
    selection-color: {p.on_accent};
}}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
    border-color: {p.accent};
}}
QLineEdit:disabled, QTextEdit:disabled {{
    background: {p.bg_alt};
    color: {p.text_faint};
}}
QComboBox {{
    background: {p.card};
    border: 1px solid {p.border_strong};
    border-radius: 6px;
    padding: 5px 10px;
    min-height: 20px;
}}
QComboBox:hover {{ border-color: {p.accent}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: {p.card};
    border: 1px solid {p.border};
    border-radius: 6px;
    selection-background-color: {p.accent_soft};
    selection-color: {p.text};
    padding: 4px;
}}
QCheckBox, QRadioButton {{ spacing: 7px; background: transparent; }}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 15px; height: 15px;
    border: 1px solid {p.border_strong};
    background: {p.card};
}}
QCheckBox::indicator {{ border-radius: 4px; }}
QRadioButton::indicator {{ border-radius: 8px; }}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    background: {p.accent};
    border-color: {p.accent};
}}
QSlider::groove:horizontal {{ height: 4px; background: {p.border}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    width: 14px; height: 14px; margin: -5px 0;
    background: {p.accent}; border-radius: 7px;
}}

/* ---------- 列表 / 表格 ---------- */
QListWidget, QTreeWidget, QTableWidget {{
    background: {p.card};
    border: 1px solid {p.border};
    border-radius: 8px;
    padding: 4px;
}}
QListWidget::item {{
    border-radius: 6px;
    padding: 6px 8px;
    color: {p.text};
}}
QListWidget::item:hover {{ background: {p.card_hover}; }}
QListWidget::item:selected {{ background: {p.accent_soft}; color: {p.text}; }}
QTableWidget {{
    gridline-color: {p.border};
    selection-background-color: {p.accent_soft};
    selection-color: {p.text};
}}
QHeaderView::section {{
    background: {p.bg_alt};
    border: none;
    border-bottom: 1px solid {p.border};
    padding: 6px 8px;
    color: {p.text_sub};
    font-weight: 600;
}}
QTableWidget::item {{ padding: 4px; }}

/* ---------- 滚动条 ---------- */
QScrollBar:vertical {{
    background: transparent; width: 9px; margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {p.border_strong}; border-radius: 4px; min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{ background: {p.text_faint}; }}
QScrollBar:horizontal {{
    background: transparent; height: 9px; margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: {p.border_strong}; border-radius: 4px; min-width: 28px;
}}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}

/* ---------- 其他 ---------- */
QProgressBar {{
    background: {p.bg_alt};
    border: none;
    border-radius: 4px;
    height: 7px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{ background: {p.accent}; border-radius: 4px; }}
QToolTip {{
    background: {p.card};
    color: {p.text};
    border: 1px solid {p.border_strong};
    border-radius: 6px;
    padding: 5px 8px;
}}
QSplitter::handle {{ background: {p.border}; }}
QTabWidget::pane {{ border: 1px solid {p.border}; border-radius: 8px; background: {p.card}; }}
QTabBar::tab {{
    background: transparent;
    padding: 6px 14px;
    margin-right: 4px;
    border-radius: 6px;
    color: {p.text_sub};
}}
QTabBar::tab:selected {{ background: {p.accent_soft}; color: {p.accent}; font-weight: 600; }}
QMenu {{
    background: {p.card}; border: 1px solid {p.border}; border-radius: 8px; padding: 4px;
}}
QMenu::item {{ padding: 6px 20px 6px 12px; border-radius: 6px; }}
QMenu::item:selected {{ background: {p.accent_soft}; }}
QStatusBar {{ background: {p.topbar}; border-top: 1px solid {p.border}; color: {p.text_sub}; }}
QStatusBar::item {{ border: none; }}
"""


#: 语义色快捷取用
def level_color(theme: str, level: str) -> str:
    p = palette(theme)
    return {
        "error": p.danger,
        "warn": p.warning,
        "warning": p.warning,
        "info": p.accent,
        "success": p.success,
        "ok": p.success,
    }.get(level, p.text_sub)
