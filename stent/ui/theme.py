"""主题与视觉规范（企划书 5.1 / 5.3）。

简洁 · 留白 · 卡片化 · 一屏完成 · 暗色可选

配色基线：以**淡紫**为唯一强调色的中性色系统，浅色与暗色共用同一套语义槽位。
暗色模式使用**灰黑**而非纯黑，降低长时间使用的压迫感。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Palette:
    name: str
    # 背景层次
    bg: str
    bg_alt: str
    card: str
    card_hover: str
    # 描边
    border: str
    border_strong: str
    # 文字
    text: str
    text_sub: str
    text_faint: str
    # 强调色（淡紫）
    accent: str
    accent_hover: str
    accent_soft: str
    on_accent: str
    # 语义色
    success: str
    warning: str
    danger: str
    # 语义底色（状态灯/标签/提示条背景）
    tint_info: str
    tint_warn: str
    tint_error: str
    tint_success: str
    # 结构区
    sidebar: str
    sidebar_active: str
    topbar: str
    shadow: str


LIGHT = Palette(
    name="light",
    bg="#F6F4FB",
    bg_alt="#EDE9F6",
    card="#FFFFFF",
    card_hover="#F8F6FD",
    border="#E7E1F3",
    border_strong="#D5CDE7",
    text="#231D33",
    text_sub="#6A6280",
    text_faint="#A29BB5",
    accent="#7C5CE6",
    accent_hover="#6A49D6",
    accent_soft="#F1ECFD",
    on_accent="#FFFFFF",
    success="#12A66A",
    warning="#D98410",
    danger="#E5453B",
    tint_info="#F1ECFD",
    tint_warn="#FDF4E4",
    tint_error="#FCEDEC",
    tint_success="#E8F8F0",
    sidebar="#FBFAFE",
    sidebar_active="#F1ECFD",
    topbar="#FFFFFF",
    shadow="rgba(60, 40, 110, 0.07)",
)

#: 暗色刻意避开纯黑：背景取灰黑并带极轻的紫调，长时间阅读更舒适
DARK = Palette(
    name="dark",
    bg="#232128",
    bg_alt="#2A2731",
    card="#2E2B37",
    card_hover="#35323F",
    border="#3C3846",
    border_strong="#4B4657",
    text="#EDEAF4",
    text_sub="#ADA7BE",
    text_faint="#7E7891",
    accent="#A98BF7",
    accent_hover="#B79CF9",
    accent_soft="#382F4E",
    on_accent="#1B1823",
    success="#3DD68C",
    warning="#E9A93C",
    danger="#F2786F",
    tint_info="#2E2942",
    tint_warn="#3A3324",
    tint_error="#3A2827",
    tint_success="#1F3A2D",
    sidebar="#1F1D24",
    sidebar_active="#382F4E",
    topbar="#1F1D24",
    shadow="rgba(0, 0, 0, 0.30)",
)


def palette(theme: str) -> Palette:
    return DARK if (theme or "").lower() == "dark" else LIGHT


# --------------------------------------------------------------------------
# 全局当前主题
# --------------------------------------------------------------------------
# 供自绘组件（表格委托、图表）在没有 theme 参数时取用。
_current_theme = "light"


def set_current_theme(theme: str) -> None:
    global _current_theme
    _current_theme = "dark" if str(theme).lower() == "dark" else "light"


def current_theme() -> str:
    return _current_theme


#: 中文优先字体栈（企划书 5.3：中文微软雅黑/思源黑体，英文 Inter 或系统默认）
FONT_FAMILY = '"Inter", "Microsoft YaHei UI", "Microsoft YaHei", "Source Han Sans SC", "PingFang SC", sans-serif'
MONO_FAMILY = '"Cascadia Mono", "Consolas", "JetBrains Mono", monospace'


#: 平台品牌色（用于列表中的来源标识）
PLATFORM_COLORS: dict[str, str] = {
    "xiaohongshu": "#FF2E4D",  # 小红书红
    "douyin": "#FE2C55",  # 抖音红（品牌色取自其官方红）
    "zhihu": "#0F88EB",  # 知乎蓝
    "bilibili": "#FB7299",  # B 站粉
    "weibo": "#E6162D",  # 微博红
    "baidu": "#2932E1",  # 百度蓝
    "toutiao": "#F04142",  # 今日头条红
    "rednote": "#FF2E4D",
    "unknown": "#8C86A0",
}


def platform_color(key: str) -> str:
    return PLATFORM_COLORS.get((key or "").lower(), PLATFORM_COLORS["unknown"])


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
    color: {p.accent};
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
    background: {p.accent_soft};
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
#SidebarFooter {{
    background: transparent;
    border-top: 1px solid {p.border};
}}
#VersionButton {{
    background: transparent;
    border: 1px solid {p.border};
    border-radius: 6px;
    padding: 5px 10px;
    color: {p.text_sub};
    font-size: 12px;
    text-align: left;
}}
#VersionButton:hover {{
    background: {p.accent_soft};
    border-color: {p.accent};
    color: {p.accent};
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

/* ---------- 状态指示灯 ---------- */
#StatusLight {{
    background: {p.bg_alt};
    border: 1px solid {p.border};
    border-radius: 11px;
    padding: 2px 10px;
    font-size: 11.5px;
    color: {p.text_sub};
}}
#StatusLight:hover {{
    border-color: {p.accent};
    color: {p.text};
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
    padding: 6px 9px;
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


def level_color(theme: str, level: str) -> str:
    """语义色快捷取用。"""
    p = palette(theme)
    return {
        "error": p.danger,
        "warn": p.warning,
        "warning": p.warning,
        "info": p.accent,
        "success": p.success,
        "ok": p.success,
        "busy": p.accent,
        "idle": p.text_sub,
    }.get(level, p.text_sub)


def level_tint(theme: str, level: str) -> str:
    """语义底色（随主题变化，暗色下不会出现刺眼的浅黄/浅红）。"""
    p = palette(theme)
    return {
        "error": p.tint_error,
        "warn": p.tint_warn,
        "warning": p.tint_warn,
        "info": p.tint_info,
        "success": p.tint_success,
        "ok": p.tint_success,
        "busy": p.tint_info,
        "idle": p.bg_alt,
    }.get(level, p.tint_info)


def level_dot(theme: str, level: str) -> str:
    """状态指示灯的点色。"""
    return level_color(theme, level)
