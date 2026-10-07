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
    # 输入控件底色：暗色下刻意比卡片再亮一档，
    # 否则「深灰输入框」压在「深灰卡片」上，边界糊成一片、看不出哪里可以输入
    input_bg: str
    # 次级内容面：标题候选 / 简介 / 标签这类「辅助信息」用极浅底色，
    # 与纯白的主输入框（正文）拉开层级，视线自然落在正文上
    surface: str
    # 描边
    border: str
    border_strong: str
    # 文字
    text: str
    text_sub: str
    text_faint: str
    # 列表选中行底色：浅色主题下「加深」、暗色主题下「提亮」，
    # 刻意不用强调色铺底——大面积淡紫会让长列表显得刺眼，也难以与文字形成稳定对比。
    row_selected: str
    row_hover: str
    # 热度分级（爆 / 热 / 温）与排名徽章（金 / 银 / 铜）
    heat_hot: str
    heat_warm: str
    heat_cool: str
    badge_gold: str
    badge_silver: str
    badge_bronze: str
    on_badge: str
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
    input_bg="#FFFFFF",
    surface="#F7F7FB",
    border="#E7E1F3",
    border_strong="#D5CDE7",
    text="#231D33",
    text_sub="#6A6280",
    text_faint="#A29BB5",
    row_selected="#EFE9FA",
    row_hover="#F9F7FE",
    heat_hot="#E5453B",
    heat_warm="#D98410",
    heat_cool="#9A93AC",
    badge_gold="#D9A22B",
    badge_silver="#98A2B3",
    badge_bronze="#C07B45",
    on_badge="#FFFFFF",
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
    # 比卡片亮一档：让「可输入」这件事在暗色下也一眼看得出来
    input_bg="#3B3748",
    surface="#2A2731",
    border="#3C3846",
    border_strong="#4B4657",
    text="#EDEAF4",
    text_sub="#ADA7BE",
    text_faint="#7E7891",
    row_selected="#3A3547",
    row_hover="#35323F",
    heat_hot="#F2786F",
    heat_warm="#E9A93C",
    heat_cool="#8B85A0",
    badge_gold="#C99A34",
    badge_silver="#8E97A8",
    badge_bronze="#A96C3C",
    on_badge="#1B1823",
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


# --------------------------------------------------------------------------
# 勾号图标
# --------------------------------------------------------------------------
# QSS 的 ::indicator 只支持贴图片，画不出矢量勾，所以按主题色生成一张 PNG 缓存复用。
_CHECK_ICON_CACHE: dict[str, str] = {}


def check_icon_url(color: str) -> str:
    """生成勾号图标并返回 QSS 可用的路径；失败时返回空串（退化为纯色块）。"""
    if color in _CHECK_ICON_CACHE:
        return _CHECK_ICON_CACHE[color]
    url = ""
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QColor, QPainter, QPen, QPixmap

        from .. import paths

        target = paths.cache_dir() / f"check-{color.lstrip('#').lower()}.png"
        if not target.exists():
            side = 32  # 2x，缩放到 15px 的指示器仍然清晰
            pixmap = QPixmap(side, side)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(
                QPen(
                    QColor(color),
                    side * 0.17,
                    Qt.PenStyle.SolidLine,
                    Qt.PenCapStyle.RoundCap,
                    Qt.PenJoinStyle.RoundJoin,
                )
            )
            painter.drawLine(int(side * 0.24), int(side * 0.52), int(side * 0.43), int(side * 0.72))
            painter.drawLine(int(side * 0.43), int(side * 0.72), int(side * 0.77), int(side * 0.28))
            painter.end()
            pixmap.save(str(target), "PNG")
        url = target.as_posix()
    except Exception:  # noqa: BLE001 - 生成失败不影响主题加载
        url = ""
    _CHECK_ICON_CACHE[color] = url
    return url


def build_qss(theme: str) -> str:
    """生成全局样式表。"""
    p = palette(theme)
    check_url = check_icon_url(p.on_accent)
    check_image = f"image: url({check_url});" if check_url else ""
    return f"""
* {{
    font-family: {FONT_FAMILY};
    font-size: 13px;
    color: {p.text};
    outline: none;
}}

QMainWindow, QDialog {{
    background: {p.bg};
}}

/* 刻意**不**给所有 QWidget 刷背景色。
   QSS 里一旦写了通配的 QWidget 背景规则，它同样会作用到卡片、顶栏内部那些
   作为布局容器的裸 QWidget 上：于是白色卡片里会浮出一块比卡片更深的页面底色矩形
   （平台筛选行、搜索行都中过招）。容器默认透明后，底色统一由卡片/顶栏决定。 */

/* 文本类控件一律透明背景，避免在卡片里形成暗色块（QFormLayout 自动生成的标签尤其明显）。 */
QLabel, QCheckBox, QRadioButton, QGroupBox {{
    background: transparent;
}}

/* ---------- 顶栏 ---------- */
#TopBar {{
    background: {p.topbar};
    border-bottom: 1px solid {p.border};
}}
/* 顶栏内的容器控件必须显式透明：全局 QWidget 规则会把它们的底色刷成页面背景，
   于是「Stent」品牌区会比顶栏其它地方深一块，看起来像贴了一张脏色块。 */
#TopBar QWidget {{
    background: transparent;
}}
#BrandBox {{
    background: transparent;
}}
#BrandName {{
    font-size: 16px;
    font-weight: 700;
    letter-spacing: 0.4px;
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

/* ---------- 窗口控制按钮（无边框窗口自绘标题栏）---------- */
#TopBar QPushButton#WindowButton,
#TopBar QPushButton#CloseButton {{
    background: transparent;
    border: none;
    border-radius: 6px;
    padding: 0;
}}
#TopBar QPushButton#WindowButton:hover {{
    background: {p.bg_alt};
    border: none;
}}
#TopBar QPushButton#WindowButton:pressed {{
    background: {p.border};
}}
#TopBar QPushButton#WindowButton:disabled {{
    background: transparent;
}}
#TopBar QPushButton#CloseButton:hover {{
    background: {p.danger};
    border: none;
}}
#TopBar QPushButton#CloseButton:pressed {{
    background: {p.danger};
    border: none;
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
    font-size: 15px;
    font-weight: 600;
}}
#CardHint {{
    font-size: 12.5px;
    color: {p.text_sub};
}}
#PageTitle {{
    font-size: 23px;
    font-weight: 700;
    letter-spacing: 0.2px;
}}
#PageSubtitle {{
    font-size: 13px;
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
#StatValue[empty="true"] {{
    color: {p.text_faint};
    font-weight: 500;
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
/* 健康检查属于低频信息：默认无边框、无底色，只有真正异常时才着色，
   这样页面右上角的视觉焦点始终留给主操作按钮。 */
#StatusLight {{
    background: transparent;
    border: none;
    padding: 2px 4px;
    font-size: 11.5px;
    color: {p.text_sub};
}}
#StatusLight:hover {{
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

/* ---------- 中断按钮 ---------- */
/* 弱化处理：平时只是一枚安静的灰字按钮，不跟紫色主操作抢焦点；
   悬停时才亮出红底红字，符合「停止 / 危险操作」的心理模型。 */
QPushButton#StopButton {{
    background: transparent;
    border: 1px solid {p.border};
    color: {p.text_sub};
    padding: 6px 13px;
}}
QPushButton#StopButton:hover {{
    background: {p.tint_error};
    border-color: {p.danger};
    color: {p.danger};
}}
QPushButton#StopButton:pressed {{
    background: {p.danger};
    border-color: {p.danger};
    color: #FFFFFF;
}}
QPushButton#StopButton:disabled {{
    background: transparent;
    border-color: {p.border};
    color: {p.text_faint};
}}

/* ---------- 底部操作栏（吸底） ---------- */
/* 卡片底部固定一条操作栏：上方用细分隔线切开，即使正文很长也一直可见可点。
   卡片圆角是 10px，这里要给底边补上同样的圆角，否则矩形会顶出圆角外。 */
#ActionBar {{
    background: {p.card};
    border: none;
    border-top: 1px solid {p.border};
    border-bottom-left-radius: 10px;
    border-bottom-right-radius: 10px;
}}

/* ---------- 字段标签 ---------- */
#FieldLabel {{
    font-size: 12.5px;
    font-weight: 600;
    color: {p.text_sub};
    background: transparent;
}}
#FieldHint {{
    font-size: 11.5px;
    color: {p.text_faint};
    background: transparent;
}}

/* ---------- 运行环境阻塞提示 ---------- */
/* 环境没装好 = 后面所有步骤都走不通，所以它必须比任何普通提示都显眼：
   暖色底 + 实线描边 + 大标题 + 一个主操作按钮。 */
#EnvCard {{
    background: {p.tint_warn};
    border: 1px solid {p.warning};
    border-radius: 10px;
}}
#EnvTitle {{
    font-size: 14.5px;
    font-weight: 700;
    color: {p.text};
    background: transparent;
}}
#EnvDetail {{
    font-size: 12.5px;
    color: {p.text_sub};
    background: transparent;
}}
#EnvProgress {{
    background: {p.border};
    border: none;
    border-radius: 3px;
    height: 6px;
    max-height: 6px;
}}
#EnvProgress::chunk {{
    background: {p.warning};
    border-radius: 3px;
}}

/* ---------- 安全模式标语 ---------- */
/* 默认行为本身是「安全」的，用让人安心的绿色常驻在操作区上方。 */
#SafeStrip {{
    background: {p.tint_success};
    border: 1px solid {p.success};
    border-radius: 8px;
}}
#SafeStripTitle {{
    font-size: 12.5px;
    font-weight: 600;
    color: {p.success};
    background: transparent;
}}
#SafeStripText {{
    font-size: 11.5px;
    color: {p.text_sub};
    background: transparent;
}}

/* ---------- 安全模式徽章 ---------- */
/* 静态提示压成一行小字，贴在「确认发布」旁边，把纵向空间还给预览。 */
#SafeBadge {{
    background: {p.tint_success};
    border: 1px solid {p.success};
    border-radius: 11px;
    padding: 3px 10px;
    color: {p.success};
    font-size: 11.5px;
}}

/* ---------- 确认发布按钮 ---------- */
/* 勾选确认后要一眼看出「现在可以点了」：实心强调色 + 加粗 + 更大的点击面积；
   未勾选时彻底哑光，避免误以为可以直接点。 */
QPushButton#ConfirmButton {{
    background: {p.accent};
    border: 1px solid {p.accent};
    color: {p.on_accent};
    font-weight: 700;
    font-size: 13.5px;
    padding: 9px 26px;
    border-radius: 7px;
}}
QPushButton#ConfirmButton:hover {{
    background: {p.accent_hover};
    border-color: {p.accent_hover};
}}
QPushButton#ConfirmButton:disabled {{
    background: {p.bg_alt};
    border-color: {p.border};
    color: {p.text_faint};
    font-weight: 600;
}}

/* ---------- 检查结果列表 ---------- */
#CheckList {{
    background: {p.card};
    border: 1px solid {p.border};
    border-radius: 8px;
    padding: 4px;
}}
#CheckList::item {{
    border-radius: 6px;
    padding: 6px 8px;
    color: {p.text};
}}
#CheckList::item:hover {{ background: {p.card_hover}; }}

/* ---------- 发布记录抽屉 ---------- */
#Drawer {{
    background: {p.bg};
}}
#DrawerHeader {{
    background: {p.card};
    border-bottom: 1px solid {p.border};
}}
#DrawerTitle {{
    font-size: 15px;
    font-weight: 600;
    background: transparent;
}}
#StatusSummary {{
    font-size: 12px;
    color: {p.text_sub};
    background: transparent;
}}

/* ---------- 分段切换（Tabs） ---------- */
/* 用分段控件而不是 QTabWidget：胶囊化的分段更轻，和筛选胶囊视觉同源。 */
#Segmented {{
    background: {p.bg_alt};
    border: none;
    border-radius: 9px;
}}
QPushButton#SegmentTab {{
    background: transparent;
    border: none;
    border-radius: 7px;
    padding: 6px 16px;
    color: {p.text_sub};
    font-size: 12.5px;
}}
QPushButton#SegmentTab:hover {{
    color: {p.text};
}}
QPushButton#SegmentTab:checked {{
    background: {p.card};
    color: {p.accent};
    font-weight: 600;
}}

/* ---------- 快捷参数胶囊 ---------- */
QPushButton#ParamChip {{
    background: {p.card};
    border: 1px solid {p.border_strong};
    border-radius: 13px;
    padding: 4px 12px;
    min-height: 20px;
    color: {p.text_sub};
    font-size: 12.5px;
}}
QPushButton#ParamChip:hover {{
    border-color: {p.accent};
    color: {p.accent};
}}
QPushButton#ParamChip[active="true"] {{
    background: {p.accent_soft};
    border-color: {p.accent};
    color: {p.accent};
    font-weight: 600;
}}

/* ---------- 标签云 ---------- */
#TagCloud {{
    background: {p.surface};
    border: 1px solid {p.border};
    border-radius: 8px;
}}
QPushButton#TagAddPill {{
    background: transparent;
    border: 1px dashed {p.border_strong};
    border-radius: 11px;
    padding: 2px 11px;
    min-height: 18px;
    color: {p.text_faint};
    font-size: 12px;
}}
QPushButton#TagAddPill:hover {{
    border-color: {p.accent};
    color: {p.accent};
}}

/* ---------- 检查报告卡 ---------- */
/* 报告不再用「红字大字」砸用户：整条用浅色底卡片包住，文字回归常规深灰，
   只有左侧一枚小圆点承担「这是问题」的信号。 */
#CheckCard {{
    background: {p.card};
    border: 1px solid {p.border};
    border-left: 3px solid {p.border_strong};
    border-radius: 8px;
}}
#CheckCard[level="error"] {{
    background: {p.tint_error};
    border: 1px solid {p.border};
    border-left: 3px solid {p.danger};
}}
#CheckCard[level="warn"] {{
    background: {p.tint_warn};
    border: 1px solid {p.border};
    border-left: 3px solid {p.warning};
}}
#CheckCard[level="info"] {{
    background: {p.surface};
    border: 1px solid {p.border};
    border-left: 3px solid {p.border_strong};
}}
#CheckTitle {{
    font-size: 13px;
    font-weight: 600;
    color: {p.text};
    background: transparent;
}}
#CheckDetail {{
    font-size: 12.5px;
    color: {p.text_sub};
    background: transparent;
}}
#CheckSuggestion {{
    font-size: 12.5px;
    color: {p.text_sub};
    background: transparent;
}}
#CheckGroup {{
    font-size: 12px;
    font-weight: 600;
    color: {p.text_sub};
    background: transparent;
}}
QPushButton#SuggestionToggle {{
    background: transparent;
    border: none;
    border-radius: 6px;
    padding: 4px 8px;
    color: {p.text_sub};
    font-size: 12.5px;
    text-align: left;
}}
QPushButton#SuggestionToggle:hover {{
    background: {p.bg_alt};
    color: {p.text};
}}

/* ---------- AI 自检报告 ---------- */
#ReportPanel {{
    background: {p.surface};
    border: 1px solid {p.border};
    border-radius: 9px;
}}
#ReportScore {{
    font-size: 30px;
    font-weight: 700;
    background: transparent;
}}
#ReportScoreUnit {{
    font-size: 12.5px;
    color: {p.text_faint};
    background: transparent;
}}
#ReportVerdict {{
    font-size: 13px;
    font-weight: 600;
    background: transparent;
}}
#ReportSectionTitle {{
    font-size: 12.5px;
    font-weight: 600;
    color: {p.text_sub};
    background: transparent;
}}
#ReportItem {{
    font-size: 12.5px;
    color: {p.text};
    background: transparent;
}}
#ReportMuted {{
    font-size: 12px;
    color: {p.text_faint};
    background: transparent;
}}
#DimBar {{
    background: {p.border};
    border: none;
    border-radius: 3px;
    height: 5px;
    max-height: 5px;
}}
#DimBar::chunk {{
    background: {p.accent};
    border-radius: 3px;
}}

/* ---------- 创作页：发布中心建议 / 自检过期 ---------- */
/* 建议横条常驻在创作页顶部：从发布中心跳过来改稿时，提示不能只闪一下就没了 */
#AdviceBar {{
    background: {p.tint_warn};
    border: 1px solid {p.border};
    border-left: 3px solid {p.warning};
    border-radius: 9px;
}}
#AdviceTitle {{
    font-size: 13px;
    font-weight: 600;
    color: {p.text};
    background: transparent;
}}
#AdviceItem {{
    font-size: 12.5px;
    color: {p.text_sub};
    background: transparent;
}}
QPushButton#AdviceToggle {{
    background: transparent;
    border: none;
    border-radius: 6px;
    padding: 3px 8px;
    color: {p.text_sub};
    font-size: 12.5px;
}}
QPushButton#AdviceToggle:hover {{
    background: {p.bg_alt};
    color: {p.text};
}}
#StaleBar {{
    background: {p.tint_warn};
    border: 1px solid {p.border};
    border-radius: 8px;
}}
#StaleText {{
    font-size: 12.5px;
    color: {p.text_sub};
    background: transparent;
}}

/* ---------- 可折叠卡片 ---------- */
QPushButton#CardFold {{
    background: transparent;
    border: none;
    border-radius: 6px;
    padding: 6px 8px;
    color: {p.text};
    font-size: 13.5px;
    font-weight: 600;
    text-align: left;
}}
QPushButton#CardFold:hover {{
    background: {p.bg_alt};
}}

/* ---------- 数据分析：空状态任务清单 ---------- */
#GuideCard {{
    background: {p.surface};
    border: 1px dashed {p.border_strong};
    border-radius: 10px;
}}
#GuideStep {{
    font-size: 13px;
    color: {p.text};
    background: transparent;
}}
#GuideStepDone {{
    font-size: 13px;
    color: {p.text_faint};
    background: transparent;
}}
#GuideHint {{
    font-size: 12px;
    color: {p.text_faint};
    background: transparent;
}}

/* ---------- 高级设置入口按钮 ---------- */
/* 虚线描边 = 「这里还能展开」，默认不抢视线，但一眼能看出是可点的补充项 */
QPushButton#AdvancedButton {{
    background: transparent;
    border: 1px dashed {p.border_strong};
    border-radius: 7px;
    padding: 8px 12px;
    color: {p.text_sub};
    text-align: left;
}}
QPushButton#AdvancedButton:hover {{
    background: {p.accent_soft};
    border-color: {p.accent};
    color: {p.accent};
}}

/* ---------- 高级设置弹窗 ---------- */
#DialogSection {{
    background: {p.bg_alt};
    border: 1px solid {p.border};
    border-radius: 9px;
}}
#DialogSectionTitle {{
    font-size: 13px;
    font-weight: 600;
    color: {p.text};
    background: transparent;
}}
#DialogHint {{
    font-size: 11.5px;
    color: {p.text_faint};
    background: transparent;
}}

/* ---------- 生成步骤提示条 ---------- */
#StepBar {{
    background: {p.accent_soft};
    border: 1px solid {p.border};
    border-radius: 8px;
}}
#StepText {{
    color: {p.accent};
    font-size: 12.5px;
    font-weight: 600;
    background: transparent;
}}
#StepBar QProgressBar {{
    background: {p.border};
    border: none;
    border-radius: 2px;
    height: 4px;
    max-height: 4px;
}}
#StepBar QProgressBar::chunk {{
    background: {p.accent};
    border-radius: 2px;
}}

/* ---------- 标签胶囊（可点击复制 / 可删除） ---------- */
#TagPill {{
    background: {p.accent_soft};
    border: 1px solid transparent;
    border-radius: 11px;
}}
#TagPill:hover {{
    border-color: {p.accent};
}}
#TagPillText {{
    color: {p.accent};
    font-size: 12px;
    background: transparent;
}}
#TagPillClose {{
    background: transparent;
    border: none;
    border-radius: 7px;
    padding: 0;
    color: {p.text_faint};
    font-size: 13px;
    font-weight: 600;
}}
#TagPillClose:hover {{
    background: {p.danger};
    color: #FFFFFF;
}}
#TagAddEdit {{
    background: {p.input_bg};
    border: 1px dashed {p.border_strong};
    border-radius: 11px;
    padding: 2px 10px;
    min-height: 18px;
    color: {p.text};
    font-size: 12px;
}}
#TagAddEdit:focus {{
    border: 1px solid {p.accent};
}}

/* ---------- 标题候选 ---------- */
QListWidget#TitleList {{
    background: {p.surface};
    border: 1px solid {p.border};
    border-radius: 7px;
    padding: 4px;
}}
QListWidget#TitleList::item {{
    border-radius: 6px;
    padding: 5px 8px;
    color: {p.text};
}}
QListWidget#TitleList::item:hover {{ background: {p.bg_alt}; }}
QListWidget#TitleList::item:selected {{
    background: {p.accent_soft};
    color: {p.accent};
    font-weight: 600;
}}

/* ---------- 筛选胶囊 ---------- */
/* 选中态用「实心强调色 + 反白文字」，未选中态保持安静的描边胶囊。
   旧版是白底 + 紫色描边，选中与否几乎看不出来。 */
QPushButton#FilterChip {{
    background: transparent;
    border: 1px solid {p.border_strong};
    border-radius: 13px;
    padding: 3px 13px;
    min-height: 20px;
    color: {p.text_sub};
    font-size: 12.5px;
}}
QPushButton#FilterChip:hover {{
    background: {p.bg_alt};
    border-color: {p.accent};
    color: {p.text};
}}
QPushButton#FilterChip:checked {{
    background: {p.accent};
    border-color: {p.accent};
    color: {p.on_accent};
    font-weight: 600;
}}
QPushButton#FilterChip:checked:hover {{
    background: {p.accent_hover};
    border-color: {p.accent_hover};
}}

/* ---------- 表格行内操作 ---------- */
QPushButton#RowAction {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 3px 8px;
    color: {p.accent};
    font-size: 12px;
    text-align: center;
}}
QPushButton#RowAction:hover {{
    background: {p.accent_soft};
    border-color: {p.accent};
}}
QPushButton#RowAction:disabled {{
    color: {p.text_faint};
    background: transparent;
    border-color: transparent;
}}

/* ---------- 表头浮层（两列垂类选择器 / 下拉弹层） ---------- */
#PopupPanel {{
    background: {p.card};
    border: 1px solid {p.border_strong};
    border-radius: 8px;
}}
#PopupPanel QScrollArea {{
    background: transparent;
    border: none;
}}
#PopupPanel QWidget {{
    background: transparent;
}}
#PopupPanel QCheckBox {{
    padding: 1px 2px;
}}

/* ---------- 输入控件 ---------- */
/* 输入框用比卡片更亮的 input_bg + 明确描边：
   暗色主题下如果沿用卡片底色，输入框会「陷进」卡片里，看不出哪里能打字。 */
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox {{
    background: {p.input_bg};
    border: 1px solid {p.border_strong};
    border-radius: 7px;
    padding: 7px 10px;
    selection-background-color: {p.accent};
    selection-color: {p.on_accent};
}}
QLineEdit:hover, QTextEdit:hover, QPlainTextEdit:hover,
QSpinBox:hover, QDoubleSpinBox:hover {{
    border-color: {p.text_faint};
}}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
    border-color: {p.accent};
}}
QLineEdit:disabled, QTextEdit:disabled, QPlainTextEdit:disabled {{
    background: {p.bg_alt};
    color: {p.text_faint};
    border-color: {p.border};
}}
/* 只读输入框（如「内容类型」自动填充值）：弱化底色，暗示不用手输 */
QLineEdit:read-only {{
    background: {p.bg_alt};
    color: {p.text_sub};
}}
/* 次级输入框：简介这类辅助信息用极浅底色，和纯白的正文拉开层级 */
QTextEdit#SurfaceInput {{
    background: {p.surface};
    border: 1px solid {p.border};
}}
QTextEdit#SurfaceInput:focus {{
    border-color: {p.accent};
    background: {p.input_bg};
}}
QComboBox {{
    background: {p.input_bg};
    border: 1px solid {p.border_strong};
    border-radius: 7px;
    padding: 6px 10px;
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
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{
    border-color: {p.accent};
}}
QCheckBox::indicator:checked {{
    background: {p.accent};
    border-color: {p.accent};
    {check_image}
}}
QRadioButton::indicator:checked {{
    background: {p.accent};
    border-color: {p.accent};
}}
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{
    background: {p.bg_alt};
    border-color: {p.border};
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
    background: {p.card};
    border: none;
    border-radius: 0;
    padding: 0;
    selection-background-color: transparent;
    selection-color: {p.text};
}}
/* 选中与悬停的行底色由委托统一绘制（见 RowHoverDelegate）：
   QSS 只负责"不要用系统高亮色盖住它"，避免出现紫色大色块。 */
QTableWidget::item {{
    padding: 7px 10px;
    border: none;
    background: transparent;
}}
QTableWidget::item:selected,
QTableWidget::item:hover {{
    background: transparent;
    color: {p.text};
}}
QHeaderView {{
    background: transparent;
    border: none;
}}
QHeaderView::section {{
    background: {p.bg_alt};
    border: none;
    border-bottom: 1px solid {p.border};
    padding: 9px 10px;
    color: {p.text_sub};
    font-size: 12px;
    font-weight: 600;
}}
QHeaderView::section:first {{
    border-top-left-radius: 8px;
}}
QHeaderView::section:last {{
    border-top-right-radius: 8px;
}}
QTableCornerButton::section {{
    background: {p.bg_alt};
    border: none;
}}

/* ---------- 滚动条 ---------- */
/* 默认几乎隐形（只留一条极淡的槽），鼠标移到滚动区域上才显形：
   预览区那种大块文本里，一条粗滚动条比内容本身还抢眼。 */
QScrollBar:vertical {{
    background: transparent; width: 9px; margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: transparent; border-radius: 4px; min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{ background: {p.text_faint}; }}
*:hover > QScrollBar::handle:vertical,
QScrollArea:hover QScrollBar::handle:vertical,
QTextEdit:hover QScrollBar::handle:vertical,
QAbstractScrollArea:hover QScrollBar::handle:vertical {{
    background: {p.border_strong};
}}
QScrollBar:horizontal {{
    background: transparent; height: 9px; margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: transparent; border-radius: 4px; min-width: 28px;
}}
QScrollBar::handle:horizontal:hover {{ background: {p.text_faint}; }}
QAbstractScrollArea:hover QScrollBar::handle:horizontal {{
    background: {p.border_strong};
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
