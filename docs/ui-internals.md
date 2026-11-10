# 窗口实现说明

Stent 用无边框窗口把标题栏搬进页面：顶栏、按钮、状态灯都由前端画，风格与内容区统一。
窗口本身是 pywebview 的一个 `frameless=True` WinForms 窗口，里面嵌 WebView2。
这篇文档记录**为什么这么做**和**踩过的坑**，改 `stent/webapp.py` 里的 `_NativeWindow` 前先读它。

相关代码：[`stent/webapp.py`](../stent/webapp.py)（`_NativeWindow`）、
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
| `tests/ui_web_window.py` | 进程内真实 Win32 弹窗验状态机 + 真启动应用注入鼠标走完整链路：最大化贴合工作区、还原回原尺寸、往返稳定、过渡动画真有中间帧、拖动 1:1、最小化与恢复 |
| `tests/ui_web_smoke.py` | 真浏览器渲染冒烟（页面、主题、路由、控制台报错） |

改窗口行为时跑第一个；改前端视觉时跑第二个。

---

## 尚缺的能力

无边框窗口没有系统非客户区，下面这些在旧 Qt 版里靠 `WM_NCHITTEST` / `WM_GETMINMAXINFO`
补齐，v1.2 删掉该壳后一并失去，目前**没有**：

- 贴边分屏（Aero Snap）与拖到顶部最大化；
- 拖窗口四边 / 四角调整大小；
- `F11` 全屏；
- DWM 圆角与阴影（`WS_THICKFRAME` 带来的那套）。

要补的话，正路是给这个窗口装一个 `WM_NCHITTEST` / `WM_NCCALCSIZE` 的 wndproc 钩子
（`SetWindowLongPtrW(GWLP_WNDPROC)`），前端把拖拽区矩形报给壳层，系统就会自己接管拖动与分屏。
