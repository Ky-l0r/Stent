# 无边框窗口实现说明

Stent 用无边框窗口把 Windows 系统标题栏的能力全部搬到应用自己的顶栏上，让顶栏与内容区风格统一。
这篇文档记录**为什么这么做**和**踩过的坑**，改 `stent/ui/frameless.py` 前先读它。

相关代码：[`stent/ui/frameless.py`](../stent/ui/frameless.py)、[`stent/ui/main_window.py`](../stent/ui/main_window.py)、
[`stent/ui/theme.py`](../stent/ui/theme.py)

---

## 核心矛盾

`FramelessWindowHint` 生成的窗口是纯 `WS_POPUP`。系统会认为它「不可最大化、不可调整大小」，于是：

- 拖到屏幕边缘不分屏；
- 悬停最大化按钮不弹 Snap 布局选择器；
- 最大化没有过渡动画；
- 没有系统阴影。

Stent 的做法是**把原生能力要回来，同时不让系统边框侵占界面**：

1. 启动时用 `SetWindowLongW` 补齐标准窗口样式位
   `WS_THICKFRAME | WS_MAXIMIZEBOX | WS_MINIMIZEBOX | WS_SYSMENU | WS_CAPTION`；
2. 再用 `WM_NCCALCSIZE` 返回 0，把系统非客户区（标题栏 + 边框）抹掉。

样式位带来原生行为，`WM_NCCALCSIZE` 保证视觉仍是自定义的。

---

## 各能力对应的消息

| 能力 | 实现 |
|---|---|
| 拖动移动 + Aero Snap + 贴边分屏 | 顶栏按下时发 `WM_NCLBUTTONDOWN + HTCAPTION`（等价于按住原生标题栏），后续全部交给系统 |
| 边缘调整大小 | `WM_NCHITTEST` 在四边 / 四角返回 `HTLEFT` / `HTBOTTOMRIGHT` 等命中码 |
| 悬停最大化按钮弹 Snap 选择器 | 「最大化」按钮在 `WM_NCHITTEST` 中返回 `HTMAXBUTTON` |
| 最大化不遮任务栏 | `WM_GETMINMAXINFO` 贴合显示器工作区（`MONITORINFO.rcWork`） |
| 圆角 | Windows 11 走 DWM 原生圆角 `DWMWA_WINDOW_CORNER_PREFERENCE`，边框色跟随主题；Windows 10 无此 API，降级为 `SetWindowRgn` 区域裁剪 |
| 阴影 | 由 `WS_THICKFRAME` 一并带来 |

---

## 过渡动画：为什么默认关闭

无边框窗口丢掉了系统非客户区，DWM 不再播放最大化 / 最小化的缩放过渡。若要动画，只能自己用
`QPropertyAnimation` 对窗口几何做缓动（最大化 / 还原 130ms `OutCubic`，最小化 120ms `InCubic` + 淡出）。

为压低每帧成本做了两件事：

- 动画期间把内容尺寸**冻结**——不逐帧重排，同时消除卡片文字抖动；
- 开启 `WA_StaticContents`，只重绘新暴露的区域。

**实测结论**：Qt 界面是 CPU 光栅化，1920×1080 单帧重绘约 29ms，动画帧率上限约 35~40fps。
与其给一个掉帧的动画，不如瞬间切换来得利落，因此**默认关闭**。

想要过渡效果：「设置 → 通用 → 启用窗口过渡动画」。Snap、圆角、阴影等其余行为不受这个开关影响。

---

## 回归验证

| 脚本 | 验证什么 |
|---|---|
| `tests/ui_smoke.py` | 真实窗口双主题冒烟（含无边框检查） |
| `tests/ui_animation.py` | 最大化 / 还原 / 最小化过渡逐帧验证 |
| `tests/ui_wheel.py` | 真实滚轮注入（Win32 输入），防「悬停误改数值」回归 |
| `tests/ui_contrast.py` | 主题对比度，找出与背景撞色的文字 |
| `tests/ui_screenshot.py` | 抓窗口关键区域截图，人工核对视觉 |

改窗口消息处理时至少跑前两个；调主题色时跑 `ui_contrast.py`。
