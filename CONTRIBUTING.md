# 参与贡献

感谢你想让 Stent 更好。本文说明怎么搭环境、改代码要守什么约定、提交前必须跑哪些自检。

> 用 AI 编码代理（Claude Code、Codex、Cursor 等）改这个仓库时，请让它先读 [AGENTS.md](AGENTS.md)：
> 那是给代理的门禁型约定（自动推送、不许过度发挥、验证分级、本机推送通道），比本文更严格。

**请先读 [README 的「已知限制」与「路线图」](README.md#已知限制)**——Stent 有明确的产品边界
（不做视频剪辑 / 多账号矩阵 / 批量发布），超出边界的改动大概率不会被合并，可以先开 Issue 讨论。

---

## 快速上手

```powershell
# 1. 克隆
git clone https://github.com/Ky-l0r/Stent.git
cd Stent

# 2. 装依赖（Python 3.10+，仅 Windows 验证过）
python -m pip install -r requirements-dev.txt

# 3. 要测发布功能才需要浏览器内核
python -m playwright install chromium

# 4. 装提交前钩子：提交时自动跑 ruff lint 与格式检查（与 CI 同一份配置）
pre-commit install

# 5. 跑源码
python main.py
```

首次运行会创建数据目录（开发态在 `_data/`）并弹出引导页。
`_data/` 与 `build/`、`dist/` 都已在 `.gitignore` 里，**不要提交本机产物**。
`pre-commit install` 每克隆一次装一次即可；不想装也能提交，只是 CI 仍会拦住不合规的代码。

---

## 开发文档

| 文档 | 什么时候读 |
|---|---|
| [docs/development.md](docs/development.md) | 打包、自检清单、性能取舍 |
| [docs/ui-internals.md](docs/ui-internals.md) | 改 `stent/ui/frameless.py` 或窗口行为前**必读** |
| [NOTICE](NOTICE) | 想知道哪些代码来自上游 Easel、改动了什么 |

---

## 项目分层

```
stent/
├── core/        存储与 LLM 客户端：不含界面与平台知识
├── services/    业务逻辑：热榜 / 创作 / 检查 / 发布编排 / 分析。可脱离 UI 单测
├── platforms/   平台适配器：每个平台一个文件，实现 platforms/base.py 的统一接口
└── ui/          PySide6 界面：页面在 ui/pages/，不写业务规则
```

依赖方向是单向的：`ui → services → core`，`services → platforms`。
**不要在 `core/` 里 import `ui/`**，也不要把平台选择器写进 service。

---

## 代码约定

- **风格**：PEP 8，行宽 100；模块与函数用中文 docstring 说清「为什么」，不重复「做了什么」；
- **类型**：新代码带类型注解，`from __future__ import annotations`；
- **导入**：包内用相对导入（`from . import paths`）；
- **重依赖惰性导入**：`playwright`、平台适配器、PySide6 子模块都要在函数内部 import——
  冷启动 < 3 秒靠的是这个，顶层 import 会把体积和启动时间吃回去；
- **HTTP**：热榜这类低延迟路径用标准库 `urllib`，不要「统一成 httpx」（原因见
  [docs/development.md](docs/development.md#性能指标与实现取舍)）；
- **密钥**：只走 `config.py` 的 keyring / DPAPI 通道，任何情况下不明文落盘、不打进日志；
- **写入要读回确认**：任何「往平台页面填东西」的代码，只有读回值匹配才算成功。
  `dispatchEvent` 一定成功、`required=False` 找不到也算过关——这两种静默失败都修过，别再引入；
- **注释与提交信息用中文**，专有名词与代码标识符保持原样。

---

## 代码质量工具

规则只有一处真源：`pyproject.toml` 里的 `[tool.ruff]`，pre-commit 与 CI 都读它，
**不要另建 `ruff.toml` 或在命令行里改规则**。

```powershell
ruff check .            # lint（CI 同样跑这条，必须零告警）
ruff format .           # 格式化（行宽 100）
ruff check --fix .      # 自动修可安全修复的部分
```

- 装了 pre-commit 的话，以上会在 `git commit` 时自动执行；
- 钩子用 `repo: local` 实现，只调用本地已装好的 ruff，**不联网**。
  不要改回社区的 ruff-pre-commit 远端仓库：本机与不少企业网络拉 GitHub 会被
  TLS 拦掉，而钩子初始化失败会直接阻断每一次提交；
- 想临时跳过用 `git commit --no-verify`，但 CI 仍会拦住——别养成习惯；
- 有正当理由不遵守某条规则时，**在那行写 `# noqa: 规则码 - 为什么`**，
  不要改全局配置。仓库里已有的 `BLE001`（浏览器写入兜底要吞异常）、
  `N802`（Qt 虚函数名必须驼峰）、`PLC0415`（重依赖懒加载）都是这么来的。

被忽略的规则与理由都写在 `pyproject.toml` 的 `ignore` 注释里，改之前先读一遍。

---

## 提交前必须跑的自检

```powershell
ruff check . && ruff format --check .                # 静态检查
python tests/check_project.py                        # 版本号 / CHANGELOG / 依赖口径一致
python tests/selftest_all.py                         # 服务层端到端（当前 171 项）
python tests/selftest_attribution_zh.py              # 归因链路中文化
$env:QT_QPA_PLATFORM='offscreen'; python main.py --selftest   # 界面层冒烟
```

再按你改动的区域补跑对应的回归脚本，清单见
[docs/development.md「发版前自检清单」](docs/development.md#发版前自检清单)。

> 修 bug 时，请**同时补一个能复现该 bug 的自检项**。这个仓库里大半的回归脚本
> （`ui_publish_layout.py`、`ui_draft_reload.py`、`bili_quill_js.py`）都是这么来的。

### 跑界面测试

`tests/ui_*.py` 大多需要真实窗口，在桌面会话里直接跑；只想验证「能不能构建」用上面的
`QT_QPA_PLATFORM=offscreen` 那条。截图类脚本会写到 `_data/screenshots/`。

---

## 提交与 Pull Request

### Commit message

第一行说清「改了什么」，不超过 72 字；需要时空一行写「为什么」——
**为什么**比**做了什么**重要，diff 已经说明了后者。

```
修复 B 站简介填不进去：改用 Quill 富文本节点并读回校验

简介不再是 textarea，旧选择器在新页面全部落空，导致填完标题就卡住。
现在定位 .ql-editor 并显式声明 source='user'（组件只在 user 源才向 Vue 模型 emit），
写完读回确认，不匹配就退回逐字输入。
```

不必强行加 `feat:` / `fix:` 前缀，但**不要只写「修复」「优化」**。

### Pull Request

- 一个 PR 解决一件事。排版修复和功能新增分开提；
- PR 描述里写清：问题现象 → 根因 → 修法 → 跑了哪些自检（贴输出）；
- 涉及界面视觉变化的，附截图（改前 / 改后）；
- 涉及平台适配器的，说明验证时的平台页面版本——选择器会随平台改版失效，
  日后的人需要知道这条验证的时间点；
- 若 PR 关闭某个 Issue，在描述里写 `Closes #编号`。

每个 PR 都会跑 [`.github/workflows/ci.yml`](.github/workflows/ci.yml)：
静态检查（ruff lint + format + 元数据一致性）、服务层自检（Python 3.10 / 3.13）、
界面无头冒烟。红了请先按上面的命令在本机复现，不要靠反复推提交试出来。

### 分支

从 `main` 切出 `fix/xxx` 或 `feat/xxx`，合并到 `main`。不直接推 `main`。

---

## 报问题

用 [Bug 报告模板](.github/ISSUE_TEMPLATE/bug_report.md)。发布类问题请务必附上：

- 复现步骤与目标平台；
- 应用内提示的**现场路径**（发布失败时会给出 dump 文件位置）；
- 数据目录下的 `logs/stent.log` 相关片段（**先检查有没有把 API Key 打进去**，
  日志本身不写密钥，但你自己补的上下文可能有）。

数据目录位置：开发态 `<仓库>/_data/`，打包态 `%APPDATA%\Stent\`。

---

## 许可

向本项目提交贡献即表示你同意这些贡献在 Apache License 2.0 下分发，
并且你有权这样授权。若你的改动参考了其他开源实现，请在 PR 里注明来源与许可证，
我们会一并更新 [NOTICE](NOTICE)。
