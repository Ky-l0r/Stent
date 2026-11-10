# 窗口实现说明

Stent 用无边框窗口把标题栏搬进页面：顶栏、按钮、状态灯都由前端画，风格与内容区统一。
窗口本身是 pywebview 的一个 `frameless=True` WinForms 窗口，里面嵌 WebView2。
这篇文档记录**为什么这么做**和**踩过的坑**，改 `stent/webapp.py` 里的 `_NativeWindow` 前先读它。

相关代码：[`stent/webapp.py`](../stent/webapp.py)（`_NativeWindow`、`_SnapOverlay`）、
[`stent/window_snap.py`](../stent/window_snap.py)（吸附与缩放的几何，纯函数）、
[`stent/web/assets/app.js`](../stent/web/assets/app.js)（`wireShell`）、
[`stent/web/assets/app.css`](../stent/web/assets/app.css)（标题栏）

---

## 核心矛盾

无边框窗口丢掉了系统非客户区，于是「拖动、最大化、最小化、还原」这几件事都得自己做，
而 pywebview 的 `Window` 在这几件事上并不完整：

| 需求 | pywebview 提供什么 | 结论 |
|---|---|---|
| 是否已最大化 | 只有 `state`（不随系统操作实时更新），**没有** `maximized` 属性 | 只能问 Win32 `IsZoomed` |
| 最大化 / 还原 | `maximize()` / `restore()` 是瞬时跳变 | 无边框窗口拿不到系统 zoom 动画，要自己做缓动 |
| 拖动 | `easy_drag` 会把整窗变成拖拽区（正文里选不中文字） | 自己做，且不能逐帧过 IPC |

因此壳层直接用 Win32 驱动窗口，前端只负责「告诉壳层发生了什么」。

---

## 三件事的做法

### 拖动

前端只在 `pointerdown` 时把按下点交给壳层，之后**由 Python 侧按光标位置摆窗口**
（120Hz 轮询 `GetCursorPos` + `SetWindowPos`），按下点与窗口左上角的偏移全程保持。

两条走不通的路（都实测过）：

- **逐帧回传位移**（旧实现）：每一帧过一次 JS→Python 的桥，必然比鼠标慢半拍。
- **`SendMessage(WM_NCLBUTTONDOWN + HTCAPTION)` 交给系统移动循环**：这条窗口是
  WinForms 的无边框 `WS_POPUP`，按下时鼠标捕获在 WebView2 的子窗口、属于另一个进程的线程，
  `ReleaseCapture` 跨线程无效，系统移动循环拿不到后续移动——四种样式组合实测都是零位移。

代价：拖动期间窗口位置由我们算，因此没有 Aero Snap / 贴边分屏（那是系统移动循环的副产品）。
贴边分屏因此自己做，见下一节。

### 贴边分屏与 Snap 布局选择器

几何全在 [`stent/window_snap.py`](../stent/window_snap.py)，是**纯函数**：吸附判定
（`SnapSession.update`）、布局平铺（`zone_rect`）、选择器命中（`picker_hover`）、
缩放边界（`resize_rect`）。它不 import 任何 Win32，所以能脱离窗口单测——
这类算错了不会报错，只会悄悄把窗口摆到任务栏底下，只靠肉眼验收不靠谱。

拖动循环每帧（8ms）做三件事：问几何「现在该预览哪块」、把结果推给预览层、照旧摆窗口。
状态没变就不重绘（`_SnapOverlay.preview` 比对 `(canvas, shapes)`）。

判定规则（`EDGE_MARGIN = 14`、`CORNER_BAND = 96`，都以**工作区**为基准）：

| 位置 | 结果 |
|---|---|
| 左 / 右边缘 | 左半屏 / 右半屏 |
| 四角 | 四分之一屏 |
| 下边缘中部 | 下半屏 |
| 上边缘中部 | 弹出布局选择器；松手时没选格子则最大化 |

两条容易踩的细节：

- **选择器卡片必须离上沿有距离**（`PICKER_GAP = 20 > EDGE_MARGIN`）。卡片贴着上沿时，
  光标刚碰到上沿就已经在缩略图上，一松手就误吸附到某个区域。
- **卡片要「粘住」**：光标从上沿往下移到缩略图的过程中会离开顶带，没有这段记忆
  就永远选不中（`SnapSession._picker_open`）。

预览层是 `_SnapOverlay`：一个覆盖目标显示器的 `WS_POPUP` 分层窗口，样式带
`WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE | WS_EX_TOPMOST | WS_EX_TOOLWINDOW`。
透明靠**色键**（底色刷洋红，`LWA_COLORKEY` 抠掉）叠**全局 alpha**：这样只用 GDI 的
`FillRect` / `FrameRect` 就能画出半透明区域，不必逐像素算 alpha（那要读回 DIB，慢一个
数量级）。代价是**配色里不能出现洋红**，且没有圆角。

为什么不用页面里的 div：预览区域常常比窗口本身还大（半屏、整屏），画在页面里会被窗口边界裁掉。
预览层必须在自己的线程上建窗口、自己跑消息循环——`js_api` 的调用线程不能被消息循环占住。
拖动一开始就 `warm_up()` 把窗口建好：建窗口要注册窗口类、等 DWM 分配表面，冷启动几十毫秒，
压在拖动循环里就是一次明显的卡顿，而且正好卡在预览该出现的那一帧。

### DWM 圆角与投影

无边框窗口默认是硬直角、没有投影。Win11 上只要给窗口设一个 DWM 属性就能两样都有：

```python
DwmSetWindowAttribute(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, c_int(DWMWCP_ROUND), 4)
```

这是**实测**结论，不是文档推断（探针窗口 + 屏幕像素采样，见下表）：

| 状态 | 角点像素 | 窗外 4px | 结论 |
|---|---|---|---|
| 什么都不设 | 窗口色 `(32,112,208)` | 背景色 `(0,160,0)` | 直角、无阴影 |
| 只设 ROUND | 背景色 `(0,153,0)` | `(0,146,0)` | **圆角 + 阴影同时出现** |
| ROUND + `DwmExtendFrameIntoClientArea(1px)` | 背景色 | `(0,146,0)` | 与上一行相同，说明 extend frame 是多余的 |
| 只 `DwmExtendFrameIntoClientArea`（圆角复位） | 窗口色 | `(0,160,0)` | 没有阴影——这条路在这条 `WS_POPUP` 窗口上不成立 |
| 最大化 | 窗口色 | — | 系统自动切回直角 |

所以不要被「阴影要靠 extend frame」这个流传很广的说法带偏：在这条无边框 `WS_POPUP`
窗口上它不产生阴影，还会在边缘留 1px 痕迹。

两个接线点：
- `window.events.shown`：原生窗口是 `webview.start()` 里才创建的，`create_window`
  那一刻还没有句柄，所以只能在窗口显示后设置；
- `_NativeWindow.handle()` 首次发现句柄时兜底再设一次（幂等，按句柄去重），
  避免事件没来导致窗口一直是直角。

Win10 没有这个属性，`DwmSetWindowAttribute` 返回 `E_INVALIDARG`，函数返回 False 并记
debug 日志——**圆角是锦上添花，不能因此让窗口起不来**。

有个细节值得记：最大化窗口的外沿有 1px DWM 边框，采样或对齐窗口内容时往里挪 3px 才准。

### 拖窗口边缘调整大小

前端在视口边缘 5px 内按下时调 `ShellApi.start_resize(edge, x, y)`，之后同样由 Python
侧轮询光标、按位移算矩形（`resize_rect`）。无边框窗口没有非客户区，系统不会替我们起
缩放的移动循环，`WM_NCHITTEST` 也指不上（鼠标始终在铺满客户区的 WebView2 子窗口上）。

边界一律夹在**光标所在显示器的工作区**里，这是「不遮挡任务栏」的唯一保证点：工作区
已经扣掉了任务栏，任何一条边都不可能越过它。跨显示器拖动时逐帧换用光标所在屏的工作区，
所以能把右边缘拖到副屏上。

最小尺寸（1080×700）由壳层自己夹，**没有**交给 pywebview 的 `min_size`：那会落到 WinForms
的 `MinimumSize` 上，连吸附结果一起夹。1080p 屏幕的一半只有 960 宽、四分之一只有 540 高，
一旦被夹就会顶大、连带压到任务栏上。现在下限只作用于手动拖边，并按 DPI 换算成物理像素；
比下限还小的窗口（吸附来的）只允许拖大。

### 从最大化 / 吸附态拖出来

抓标题栏拖一个最大化窗口，必须先回到普通矩形，否则移动的是一整块铺满工作区的窗口。
`_restore_for_drag` 的做法：`ShowWindow(SW_RESTORE)` 让系统给出还原矩形（比自己记可靠），
光标横向位置按比例映射到新宽度上（抓在 30% 处，展开后还在 30% 处），纵向让标题栏继续
压在光标下（`_TITLEBAR_GRIP = 23`），最后把结果夹回工作区——不夹的话标题栏会被算到
屏幕上方，窗口再也拖不回来。吸附态则用 `_normal_before_snap` 记下的矩形还原。

同一个「普通矩形」概念还有两处要维护：`_maximize` 要清掉吸附态（此时还原点应该是当前矩形），
手动拖边也要清（用户已经自己决定了尺寸，不该再被旧矩形覆盖）。

### 最大化 / 还原（带动画）

```
最大化：ShowWindow(SW_MAXIMIZE)          ← 先落状态，还原点由 Windows 自己记成当前矩形
        SetWindowPos(原矩形)             ← 同一 tick 内拉回来，动画从原位置起跑
        SetWindowPos × 12 帧缓动到工作区  ← 170ms，先快后慢
还原：  SetWindowPos × 12 帧缓动回还原点   ← 最大化状态下改矩形是允许的，状态保持
        ShowWindow(SW_RESTORE)           ← 落位后再清状态，不会二次跳变
```

`SetWindowPlacement` 手写 `showCmd` 的写法**不行**：窗口已经铺满工作区时它不会真的切状态，
而且会串用旧的 `ptMaxPosition`，窗口最后停在「原位置 + 工作区尺寸」。

### 最小化 / 关闭

`ShowWindow(SW_MINIMIZE)`（系统自带动画）与 `window.destroy()`。

---

## 踩过的坑

- **`argtypes` 要挂在私有实例上**：`ctypes.WinDLL("user32")`，不要改写共享的
  `ctypes.windll.user32`。签名是挂在函数对象上的，改共享实例会污染同进程里其它 ctypes 调用
  （测试里就被反噬过：`GetWindowRect` 突然要求 `LP_RECT`）。
- **`HWND_TOPMOST` 与 `SWP_NOZORDER` 互斥**：带上 `NOZORDER` 时 `hWndInsertAfter` 被忽略，
  置顶静默失效。
- **`SendMessage` 会阻塞到操作结束**：拖动、动画都要放工作线程，不能占着 `js_api` 的调用线程。
- **注入输入的测试要先置顶窗口**：被别的置顶窗口挡住时，鼠标事件根本到不了页面，
  测试会表现成「拖动没反应」，其实是输入没送达。

---

## 回归验证

| 脚本 | 验证什么 |
|---|---|
| `tests/ui_web_window.py` | 进程内真实 Win32 弹窗验状态机 + 真启动应用注入鼠标走完整链路：最大化贴合工作区、还原回原尺寸、往返稳定、过渡动画真有中间帧、拖动 1:1、拖边改尺寸、贴边吸附左半屏、上沿松手最大化、最小化与恢复 |
| `tests/selftest_snap.py` | 贴边 / 吸附 / 选择器几何（含多显示器、窄屏、任务栏边界）、预览层窗口样式与鼠标穿透、脚本化输入驱动的吸附落位与缩放夹边界 |
| `tests/selftest_frame.py` | 圆角与投影：探针窗口的角点 / 窗外像素采样、复位成直角后判据仍成立、最大化自动直角，以及真实应用窗口的同一组断言 |
| `tests/ui_web_smoke.py` | 真浏览器渲染冒烟（页面、主题、路由、控制台报错） |

改窗口行为时跑前三个；改前端视觉时跑最后一个。

> `tests/ui_web_window.py` 里注入输入的两条经验：最小化再恢复之后，WebView2 的渲染进程
> 可能长时间收不到 `pointerdown`（用外部 `ShowWindow` 恢复时 WinForms 的 `WindowState`
> 不会跟着回到 `Normal`），所以最小化检查排在最末；另外每次注入前都要重新置顶并允许重试。

---

## 尚缺的能力

无边框窗口没有系统非客户区，下面这些还没有：

- 把窗口拖到屏幕顶部自动最大化（现在顶到上沿是弹布局选择器，松手不选才最大化——
  这是有意取舍：Win11 的顶部手势就是布局选择器）；
- Win+方向键等系统快捷键驱动的吸附（那需要 `WM_GETMINMAXINFO` / 系统移动循环，
  而本壳层的拖动是自己摆窗口的）；
- 窗口非激活时的边框强调色（`DWMWA_BORDER_COLOR`）：当前窗口没有可见边框，
  加它会让无边框窗口多出一条细边，暂不需要。
