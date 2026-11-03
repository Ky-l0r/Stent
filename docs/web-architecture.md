# Web 前端重构架构

> 状态：**P0 骨架已落地，P1 四模块可用，P2/P3 待实施**（见第七章）
> 本文回答三件事：整体架构长什么样、前后端各自负责什么、从 PySide6 迁过来哪些地方最容易翻车。

---

## 一、为什么这么改

原来的 Stent 是 PySide6 单进程桌面应用：页面、业务、平台自动化全在同一个 Python 进程里，
UI 通过 Qt 信号与 `AppContext` 串联。这套结构有两个硬约束：

1. **界面迭代成本高**。改一个卡片的排版要动 QSS + 控件层级 + 布局代码，预览还得跑起整个应用；
   而 Web 前端改样式是改 CSS，热更新即可看效果。
2. **表达力受限**。QSS 没有 flex/grid，复杂排版靠 `QHBoxLayout` 层层嵌套 + 手工计算高度
   （发布页的 `_sync_pane_limits()` 就是被逼出来的补丁）；流式文本、富文本编辑、
   过渡动画在 Web 侧都是原生能力。

目标不是"把 Qt 换掉"，而是**把界面从业务进程里拆出去**：
Python 保留全部业务能力（LLM、热榜、检查、平台自动化、SQLite），
前端只做渲染与交互，两边通过本机 HTTP 接口通信。这样即使将来换壳（Electron / Tauri /
甚至远程访问），业务层一行不用改。

---

## 二、整体架构

```
┌──────────────────────────────────────────────────────────────┐
│  桌面壳（Windows 进程）                                       │
│                                                              │
│  ┌────────────────────────┐      ┌─────────────────────────┐ │
│  │  WebView2 窗口          │      │  HTTP API 服务           │ │
│  │  （pywebview，无边框）   │◀────▶│  127.0.0.1:<随机端口>    │ │
│  │                        │ HTTP │  仅绑定回环 + Token 校验  │ │
│  │  index.html             │ SSE  │                         │ │
│  │  assets/app.js          │      │  stent/api/             │ │
│  │  （原生 ES Module）      │      │    server.py  路由/SSE   │ │
│  └────────────────────────┘      │    handlers.py 端点实现   │ │
│                                  └────────────┬────────────┘ │
│                                               │ 直接函数调用   │
│                                  ┌────────────▼────────────┐ │
│                                  │  业务层（原样保留）       │ │
│                                  │  services/  core/        │ │
│                                  │  platforms/（Playwright） │ │
│                                  └─────────────────────────┘ │
└──────────────────────────────────────────────────────────────┘
```

三条不变式：

1. **业务层不认识 HTTP**。`services/` 与 `core/` 里不出现 `http.server`、`json` 序列化、
   请求对象。它们仍是普通的 Python 函数/类，`tests/selftest_all.py` 继续直接调用它们。
2. **前端不认识业务细节**。页面里不写 prompt、不拼平台规范、不算检查规则，
   一律调接口。
3. **依赖方向不变**：`web(JS) → api → services → core / platforms`。
   `api/` 是新的一层，它只做「协议转换 + 参数校验 + 序列化」，**不写业务规则**。

### 进程与端口

- 单进程：先起 HTTP 服务（后台线程），再开 WebView2 窗口，两者同生共死。
- 端口用 `bind(("127.0.0.1", 0))` 让系统分配，避免和用户其它程序抢端口；
  端口只在进程内传给窗口的加载 URL，不落盘。
- **只绑回环地址**。局域网访问不到，也不需要防火墙例外。
- 启动时生成 32 字节随机 Token，注入页面 `<meta name="stent-token">`；
  所有 `/api/*` 请求必须带 `X-Stent-Token`，否则 403。
  这挡的是"本机其它进程顺手读一下你的草稿和 API Key 配置"。

### 为什么是 HTTP 而不是 JS 桥

pywebview 自带 `js_api` 可以把 Python 函数直接暴露给 JS，省掉一层。这里刻意不用：

| 维度 | JS 桥 | 本机 HTTP + SSE |
| --- | --- | --- |
| 流式输出 | 要靠轮询或回调分片 | SSE 原生支持，一行 `event:` 就到 |
| 可测试 | 必须在窗口里点 | `urllib` 直接打端点，CI 可跑 |
| 换壳成本 | 绑死 pywebview | 换 Electron/Tauri 只改壳 |
| 调试 | 只能看窗口 | 浏览器直接开 `http://127.0.0.1:<port>` 调试 |

代价是多一层序列化开销——对单人本地应用可以忽略。

---

## 三、前后端职责划分

### 前端（`stent/web/`）

| 负责 | 具体 |
| --- | --- |
| 渲染 | 页面结构、主题（浅色/暗色）、平台品牌色、空态与骨架屏 |
| 交互 | 筛选、双击送创作、标题选用、快捷键、拖拽分栏 |
| 状态 | 当前页面、筛选条件、生成中的临时文本（**内存态，不持久化**） |
| 错误呈现 | 把接口返回的 `message` 原样展示给用户，不自行翻译 |

**不做**：拼 prompt、算平台字数上限、决定敏感词、判断能否发布、访问 SQLite、
直接调 Playwright。

### 后端（`stent/api/`）

| 负责 | 具体 |
| --- | --- |
| 协议 | 路由、方法、状态码、JSON 结构、SSE 事件格式 |
| 校验 | 参数缺失/类型错误返回 400 + 中文说明，而不是抛栈 |
| 编排 | 调用 services，把面向 Qt 的返回值转成纯 dict |
| 并发 | 生成任务用线程 + `threading.Event` 取消；一个生成任务一个线程 |
| 安全 | Token 校验、请求体大小上限、只绑回环、日志不写 Key |

**不做**：写业务规则、直接 import `platforms/*` 的选择器、绕过 services 访问 `db`。

### 边界上的三处特例

- **文件选择**：浏览器拿不到真实路径，所以由壳层（pywebview）提供
  `window.pywebview.api.pick_files()`，返回绝对路径数组；纯浏览器模式下该按钮禁用并给出说明。
- **窗口控制**（最小化/最大化/关闭/拖拽）：属于壳层能力，走 `window.pywebview.api.*`，不走 HTTP。
- **长任务进度**（发布、指标同步）：HTTP 轮询 `GET /api/jobs/<id>`，
  不用 WebSocket——单人单机场景，1 秒一次轮询足够，少一层连接管理。

---

## 四、接口约定

统一前缀 `/api`，一律 JSON（`Content-Type: application/json; charset=utf-8`）。
成功返回业务数据本体，失败返回 `{"error": {"code": "...", "message": "中文说明"}}` + 4xx/5xx。

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | `/api/health` | 存活、版本、LLM 是否就绪 |
| GET | `/api/meta` | 平台清单、创作平台、主题 |
| GET/PUT | `/api/settings` | 读写配置（Key 只写不读） |
| POST | `/api/settings/test` | 测试 LLM 连通性 |
| GET/PUT | `/api/profile` | 六维画像 |
| POST | `/api/profile/draft` | 由自我介绍生成画像草稿 |
| POST/DELETE | `/api/profile/memory` | 追加 / 清空记忆 |
| GET | `/api/hot` | 聚合热榜（`?force=1` 强制刷新） |
| POST | `/api/create/stream` | 流式创作（**SSE**） |
| POST | `/api/create/titles` | 基于正文重生标题 |
| POST | `/api/create/quality` | 质量自检打分 |
| GET | `/api/publish/platforms` | 发布平台与限制 |
| POST | `/api/publish/prepare` | 格式适配 + 发布前检查 + 预览 |

### SSE 事件格式

流式创作是整条链路里唯一"服务端主动推"的场景：

```
event: ready
data: {"platform":"xiaohongshu"}

event: delta
data: {"section":"正文","text":"第一段…"}

event: done
data: {"result":{"body":"…","titles":["…"],"summary":"…","tags":["…"]}}

event: error
data: {"message":"模型返回了空内容"}
```

- 前端用 `fetch` + `ReadableStream` 读（不用 `EventSource`：它只支持 GET，参数要塞进 URL）。
- 客户端断开 → 服务端写 socket 抛 `BrokenPipe` → 置位该任务的 `cancel` → LLM 迭代器停下。
  **取消必须真的停掉上游流**，否则用户点了中断，token 还在烧。

---

## 五、页面映射

| 原 Qt 页面 | Web 页面 | 说明 |
| --- | --- | --- |
| `pages/hotsearch.py` | `#/hot` | 保留平台筛选 + 垂类 + 双击送创作 |
| `pages/create.py` | `#/create` | 三栏简化为「输入 / 生成中 / 结果」单列 + 侧栏，去掉多平台批量 |
| `pages/publish.py` | `#/publish` | P0 只做「适配 + 检查 + 预览」，真正提交留到 P2 |
| `pages/analytics.py` | `#/analytics` | P2 |
| `pages/profile.py` | `#/profile` | 六维表单 + 完整度 |
| `pages/settings.py` | `#/settings` | LLM 三件套 + 主题 |
| `pages/onboarding.py` | 首启遮罩 | 未配置 LLM 时在创作页顶部提示 |

**刻意精简的地方**（用户已确认"适当精简优化"）：

- 创作页不再一次出多平台版本，保留单平台；多平台是低频操作，且会让页面复杂度翻倍。
- 发布页去掉四步向导条，改成左「检查清单」右「预览」两栏（原版在 720p 下就压叠过）。
- 状态指示灯从每页常驻改为顶栏一个全局指示器：页面切换时不再各自维护状态。
- 热点页去掉右键菜单，改为行尾图标（复制 / 打开原链接）。

---

## 六、从 PySide6 迁移的重点环节

按"踩了会返工"的顺序排列。

### 6.1 流式输出与中断（最容易做错）

Qt 版是 `Worker` 线程 + `Signal` 分片回主线程。Web 版是 SSE，但**中断语义**必须重做：
浏览器关连接 ≠ 服务端知道。做法是写事件时捕 `BrokenPipeError/ConnectionResetError`，
置位 `cancel`，再让 LLM 迭代器自然停下（`llm.stream_chat(cancel=event)` 已经支持）。
不这么做会出现"前端已显示中断，后台还在跑、还在扣费"。

### 6.2 页面级状态不再有"信号总线"

`AppContext` 的 `data_changed` / `config_changed` 信号在 Web 侧没有对应物。
替代方案：**每次进入页面重新拉一次数据**（数据量小，SQLite 本地毫秒级），
跨页面通信只保留一个 `navigate(hash)`。不要为对齐 Qt 而引入全局 store——那会把
Qt 的耦合照搬过来。

### 6.3 主题与视觉一致性

Qt 用 QSS + `theme.py` 常量。Web 侧把这些**翻译成 CSS 变量**（`--stent-primary: #7C5CE6` 等），
暗色主题只切 `data-theme="dark"` 上的一组变量，不在 JS 里判断颜色。
平台品牌色同样从 `theme.PLATFORM_COLORS` 抄一份到 CSS 变量表，**保持单一来源**：
改色时两边一起改，并在 `docs/web-architecture.md` 留注释。

### 6.4 长文本与富文本

创作页正文在 Qt 是 `QTextEdit`。Web 用 `<textarea>` + 自动增高即可——
不要引富文本编辑器：发布时填进平台的仍是纯文本，富文本会引入不可见的 HTML 噪音，
反而破坏 B 站 Quill 那类"读回校验"。

### 6.5 原生能力缺口

| 能力 | Qt | Web 侧做法 |
| --- | --- | --- |
| 选择媒体文件 | `QFileDialog` | 壳层 `pick_files()`（WebView2）；浏览器模式禁用 |
| 打开外部链接 | `QDesktopServices` | `<a target="_blank">`，壳层拦截 `new_window` 用系统浏览器打开 |
| 剪贴板 | `QClipboard` | `navigator.clipboard`（需 https 或 localhost，本项目是 localhost，可用） |
| 窗口拖拽/贴边 | Win32 + DWM | 壳层 frameless + `move()`；**Snap/Aero 需壳层补 Win32 调用**，P3 做 |
| 托盘 | `QSystemTrayIcon` | pywebview 无托盘，P3 用 pystray 或保留 Qt 托盘 |

### 6.6 打包

- WebView2 运行时：Win11 与多数 Win10 已内置；缺失时壳层要给出**可操作提示**
  （下载常青版运行时），不能白屏。
- `Stent.spec` 需要增加：`stent/web` 目录进 `datas`、`pywebview` 相关 hiddenimports。
- 体积：去掉 PySide6 后本可瘦一大截，但 P0 阶段**保留 PySide6**
  （旧界面仍可用，回退成本为零），等 Web 版稳定再摘。见第七章。

### 6.7 单实例与端口复用

用户双击两次会出现两个窗口、两份 SQLite 连接。P1 用命名互斥量（`CreateMutexW`）
保证单实例；第二次启动直接把已开窗口拉到前台。P0 阶段先不处理，但要记在案。

### 6.8 错误口径

Qt 版大量 `self.ctx.toast(...)`。Web 侧统一 `toast(message, level)`，
**服务端返回的 `message` 已经是中文可操作说明**（沿用 `services` 里既有的文案），
前端不要再包装成"请求失败，请重试"——那会把"API Base URL 少写了 /v1"这类
关键提示吞掉。

---

## 七、实施阶段

| 阶段 | 内容 | 状态 |
| --- | --- | --- |
| P0 | API 骨架（Token、路由、SSE）、壳层、主题变量、四个页面只读 | 已落地 |
| P1 | 热点 / 创作 / 画像 / 设置 四模块读写打通；SSE 中断 | 已落地 |
| P2 | 发布中心（含 Playwright 提交）、数据分析、草稿箱 | 待实施 |
| P3 | 壳层打磨：单实例、托盘、Snap、自动更新；摘除 PySide6 | 待实施 |

P2 之前 **不要删 `stent/ui/`**：它是回退路径，也是行为参照。
两边都跑得起来，才能逐页对照视觉与交互差异。

---

## 八、如何运行与验证

```powershell
# 源码运行（需要先装依赖：pip install pywebview）
python -m stent.webapp

# 只起服务，用浏览器调试前端（不需要 WebView2）
python -m stent.webapp --browser

# 接口自检（不需要窗口，CI 可跑）
python tests/selftest_api.py

# 指定端口，方便前端热调
python -m stent.webapp --browser --port 8765
```

`--browser` 模式下所有页面功能可用，只有「选择媒体文件」不可用（浏览器拿不到真实路径）。
