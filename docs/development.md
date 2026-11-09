# 开发与构建

面向想改代码、自己打包或排查平台适配问题的人。产品功能与使用流程见
[../README.md](../README.md)，无边框窗口的实现细节见 [ui-internals.md](ui-internals.md)。

---

## 环境

| 项目 | 要求 |
|---|---|
| 操作系统 | Windows 10 / 11（窗口实现依赖 Win32 与 DWM 接口） |
| Python | 3.10+（开发实测 3.13） |
| 运行依赖 | `requirements.txt`：PySide6、openai、keyring、playwright |
| 开发依赖 | `requirements-dev.txt`：额外加 pyinstaller、pytest |

```powershell
python -m pip install -r requirements-dev.txt
python -m playwright install chromium    # 只有要测发布功能时才需要
```

跑源码：

```powershell
python main.py
```

开发态数据写在仓库根目录 `_data/`（已在 `.gitignore` 里），打包态写在 `%APPDATA%\Stent\`。
`_data/` 下的 SQLite、日志与截图都是本机产物，**不要提交**。

---

## 打包

一键打包（onedir 目录包，双击即用）：

```powershell
pwsh -File build.ps1 -Clean
```

脚本依次执行：检查 Python → 安装依赖 → 静态检查（ruff lint / format）→ 服务层自检 →
界面自检 → PyInstaller 打包。产物位于 `dist\Stent\Stent.exe`，一次约 2 分半。

| 参数 | 作用 |
|---|---|
| `-SkipDeps` | 跳过依赖安装 |
| `-SkipTests` | 跳过自检（不推荐） |
| `-InstallBrowser` | 顺便安装 Playwright Chromium 到当前用户目录 |
| `-Clean` | 打包前清理 `build/` 与 `dist/` |

### 为什么是 onedir 而不是 onefile

见 [`Stent.spec`](../Stent.spec) 头部说明，要点：

- onefile 每次启动要把包解到临时目录，冷启动明显变慢；
- onefile 的临时解压与 Playwright 的子进程模型容易冲突；
- **Chromium 不内置**：优先复用系统已装的 Playwright 内核；没有时**自动回退到本机
  Edge / Chrome**（Playwright 的 `channel` 模式），因此用户侧不必先下载 150MB。
  冻结态下 `paths.ensure_playwright_browsers_path()` 会把
  `PLAYWRIGHT_BROWSERS_PATH` 从包内 `.local-browsers` 纠正到用户目录——不纠正必然报「浏览器不可用」。

### 瘦身

v1.1 实测 232.7MB → 约 190MB。`excludes` 只能挡住 Python 侧的模块导入，**挡不住 Qt 的共享库**，
因此在 `COLLECT` 阶段按文件名再过滤一遍二进制与数据（QML / Quick / Pdf / VirtualKeyboard、
`opengl32sw.dll`、`translations/`）。**若后续引入依赖 OpenGL / QML 的组件，记得把对应条目从过滤表里移除。**

### 分发

把 `dist\Stent` 整个目录压缩即可发给用户。发布功能会先用本机 Edge / Chrome，
没有可复用的浏览器时再任选：

- 把打包机上的 `%USERPROFILE%\AppData\Local\ms-playwright` 一并分发；
- 让用户自己执行 `python -m playwright install chromium`（应用内也有一键下载）。

---

## 静态检查与格式化

规则集中在 `pyproject.toml` 的 `[tool.ruff]`，pre-commit 与 CI 读同一份配置。

```powershell
ruff check .              # lint
ruff format .             # 格式化（行宽 100，双引号，LF）
ruff check --fix .        # 自动修可安全修复的部分
```

| 配置 | 值 | 为什么 |
|---|---|---|
| `line-length` | 100 | 与 [CONTRIBUTING](../CONTRIBUTING.md#代码约定) 的约定一致 |
| `target-version` | py310 | 最低支持版本，pyupgrade 不会建议 3.11+ 写法 |
| `ignore` | `E501` `RUF001~003` `RUF100` `C408` `RUF005` | 中文按视觉宽度换行；`，。「」` 会被当成歧义字符；`noqa` 标注的是刻意行为；`dict(...)` 构造 kwargs 更贴近调用签名；短序列用 `+` 拼接更自然 |

被忽略的规则都在 `pyproject.toml` 里写了理由。**不要为了过检查去改全局配置**——
真有正当理由，就在那一行写 `# noqa: 规则码 - 原因`。

### CI

[`.github/workflows/ci.yml`](../.github/workflows/ci.yml) 三个作业：

| 作业 | 环境 | 跑什么 |
|---|---|---|
| 静态检查 | ubuntu / py3.12 | `ruff check .`、`ruff format --check .`、`tests/check_project.py` |
| 服务层自检 | windows / py3.10 + py3.13 | `selftest_all.py`、`selftest_attribution_zh.py` |
| 界面冒烟 | windows / py3.12 | 装 `requirements.txt` 后 `QT_QPA_PLATFORM=offscreen python main.py --selftest` |

服务层自检作业**故意不装任何第三方依赖**：`core/` 与 `services/` 一旦偷偷 import
PySide6 或 playwright，这一步就会挂——这是 CI 在替我们守住分层。

---

## 发版前自检清单

按顺序跑，任何一步失败都不要继续打包。

```powershell
# 1. 静态检查（lint + 格式 + 版本号/依赖口径）
ruff check . ; ruff format --check . ; python tests/check_project.py

# 2. 服务层端到端（配置 / 数据库 / 热榜解析 / 创作 / 检查 / 发布 / 分析）
python tests/selftest_all.py                       # 当前 171 项通过 / 0 失败

# 3. 归因链路中文化（假 LLM 返回中英混排 JSON）
python tests/selftest_attribution_zh.py

# 4. 界面层：无头构建全部页面
$env:QT_QPA_PLATFORM='offscreen'; python main.py --selftest

# 5. 真实窗口冒烟（双主题 + 无边框检查）
python tests/ui_smoke.py
```

### 按改动区域选跑的回归

| 改了什么 | 跑这个 | 它盯住什么 |
|---|---|---|
| 发布中心布局 | `tests/ui_publish_layout.py` | 检查栏卡片不被裁切 / 压扁、确认文案不截断、分栏不压叠预览 |
| 草稿与创作输入 | `tests/ui_draft_reload.py` | 补充要求与快捷参数在往返中不丢 |
| B 站表单写入 | `tests/bili_quill_js.py` | 真实 Chromium 里的富文本写入与分区点选 |
| 编辑区 / 标签云排版 | `tests/ui_tagcloud_diag.py` | 排版异常时取证用 |
| 滚轮与数值控件 | `tests/ui_wheel.py` | 真实 Win32 滚轮注入，防悬停误改数值 |
| 窗口动画 | `tests/ui_animation.py` | 最大化 / 还原 / 最小化过渡逐帧 |
| 主题配色 | `tests/ui_contrast.py` | 找出与背景撞色的文字 |
| 视觉核对 | `tests/ui_screenshot.py` | 抓关键区域截图到 `_data/screenshots/` |

> 改 UI 时建议用 `python main.py` 直接跑源码验证，不必每次都重新打包；确认无误后再打包分发。

---

## 平台改版时怎么取证

平台前端一改版，选择器就失效。**先用只读探针取证，再改代码**——不要凭猜改选择器反复试，
那会真的往平台后台提交东西。

```powershell
python tests/bili_probe.py            # dump 投稿页表单结构 + 实时分区树（只读）
python tests/bili_probe.py --drafts   # dump 稿件管理里的草稿列表
```

取证时注意三类典型失效（v1.1.2 都遇到过）：

1. **控件类型变了**：B 站简介从 `textarea` 变成 Quill 富文本（真实可编辑节点是 `.ql-editor`），
   老的 `[class*="desc"] textarea` 一个都命中不了；写 Quill 必须显式声明 `source='user'`，
   否则「界面看着有字、提交时是空的」；
2. **交互结构变了**：B 站分区下拉里**没有搜索框**了，选项类名从 `.bcc-option` 换成
   `.drop-f-item / .drop-t-item`（一级 / 二级两栏），改成「先点一级、再点二级」；
   分区表不再写死，改为在已登录的投稿页里 `fetch` 官方 `archive/pre` 接口取**实时分区树**；
3. **「动作发出去了」不等于「内容写进去了」**：`el.dispatchEvent` 一定成功、`required=False`
   的查找找不到也算过关，于是失败全部静默。所有写入路径都要**读回确认**，只有真的写进去才算数。

另外，四个平台的 `dry_run` 收尾统一走 `_browser.hold_for_review()`：保持浏览器窗口打开，直到
①检测到用户点了发布、②用户自己关掉窗口、③到达时间上限（默认 10 分钟）。
不要退回上下文管理器自动 `close()` 的写法——那会在表单刚填好时把窗口关掉。

---

## 性能指标与实现取舍

| 项目 | 目标 | 实现手段 | 实测 |
|---|---|---|---|
| 冷启动 | < 3 秒 | 页面懒加载；适配器惰性导入（不加载 Playwright） | 窗口出现 < 1 秒 |
| 热点拉取 | < 2 秒 | 多平台并发 + 同一平台多源竞速；结果本地缓存 5~10 分钟 | 首次 ~2.2 秒 / 缓存 **0.006 秒** |
| LLM 首字 | < 1 秒 | `stream=True` 流式输出；失败自动重试 2 次（已吐字则不重试，避免内容重复） | 取决于服务商 |
| 数据归因 | 增量 < 1 秒 | SQLite 增量拉取，只同步 `last_synced` 超期的内容 | — |
| 安装体积 | < 200MB | 裁剪 PySide6 模块；Chromium 按需安装；排除 numpy / matplotlib 等 | 约 190MB（不含浏览器内核） |

两条容易被「顺手优化」掉的取舍：

- **热点拉取刻意用标准库 `urllib` 而非 `httpx`**：实测 httpx 0.28 在 Windows 上每次构造 `Client`
  需要约 3 秒（与网络无关），会直接吃掉整个延迟预算。
- **抖音作品信息走轻量通道**：`stent/services/douyin_title.py` 纯标准库、不启浏览器，
  从移动端分享页 / 详情接口取标题与互动数据，拿不到才回退 Playwright。
  既避开「桌面端页面标题渲染成『抖音创作者中心』」的老问题，也省掉一次十几秒的浏览器启动。

---

## 代码约定

见 [../CONTRIBUTING.md](../CONTRIBUTING.md#代码约定)。
