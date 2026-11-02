# AGENTS.md

本文件是 Stent 仓库对**所有 AI 编码代理**（Claude Code、Codex、Cursor、Copilot、DSH 等）的强制约定。
人类贡献者请读 [CONTRIBUTING.md](CONTRIBUTING.md)。

---

## 一、这份文件怎么用

1. 每次开工前先读本文件，再按需读：
   - [CONTRIBUTING.md](CONTRIBUTING.md)：分层约定、提交规范、自检清单
   - [docs/development.md](docs/development.md)：打包、静态检查、平台改版取证流程、性能取舍
   - [docs/ui-internals.md](docs/ui-internals.md)：改 `stent/ui/frameless.py` 或窗口行为前**必读**
   - [CHANGELOG.md](CHANGELOG.md)：近几个版本修过什么、为什么这么改，最有参考价值
2. 冲突时的优先级：**用户当前指令 > 本文件 > CONTRIBUTING.md / docs/\* > README.md**。
   发现冲突要报告，不得静默选一边。
3. 本文件不是建议，是门禁。做不到时停下来问，不要绕过。

---

## 二、一次任务的完成定义

"改完了"不等于完成。以下六项全部达成才算完成，缺哪项就在汇报里明说卡在哪：

1. 改动已实现，范围与用户要求一致，没有夹带。
2. 本地验证通过，汇报里贴的是**真实命令输出**。
3. 文档已按需同步（见第八章）。
4. 已提交，提交信息符合第五章。
5. 已推送并确认成功（见第三章）。
6. 已向用户汇报：改了什么、为什么、验证结果、遗留风险。

---

## 三、每次改完自动推送（硬性要求）

### 3.1 触发条件

- 任何代码、资源、文档、构建脚本的改动，**验证通过后立即提交并推送**，不需要用户再提醒。
- 用户说"先别推""只改不提交"时，以用户指令为准，并在汇报中说明未推送。
- 只读任务（答疑、看代码、查资料）不产生提交，不要为了"有产出"去改文件。

### 3.2 推送前自查（任何一条不过就停）

- `git status --short` 里每个文件都是本次预期改动，没有临时脚本、截图、日志、`_data/` 产物。
- 没有真实 API Key、Cookie、`%APPDATA%\Stent` 拷贝、Playwright 浏览器目录。
  真实密钥一旦进入工作区或历史，**立即停止并告知用户**，不要自行改写历史。
- 提交信息符合第五章格式：中文、第一行不超过 72 字、结尾不加句号。
- 本地验证已按第七章跑过。
- 当前分支就是要推的分支（默认 `main`）；不确定就先问。

### 3.3 标准命令

```bash
git status --short
git add <明确路径>            # 仅当上面输出里没有意外文件时，才可用 git add -A
git commit -F - <<'EOF'
第一行说清改了什么

空一行写为什么——diff 已经说明了改动本身，这里补的是动机与取舍。
EOF
git fetch origin
git status -sb                # 确认领先/落后关系
git -c http.sslBackend=schannel push origin HEAD   # 本机必需，原因见 3.6
git log --oneline -1
git status -sb                # 确认无 ahead / behind
```

- 推送成功以 `git status -sb` 显示 `## main...origin/main` 且无 ahead 为准。
- 远端领先且无冲突时，用 `git pull --rebase origin main` 后再推。

### 3.4 禁止事项

- 禁止 `git push --force` / `--force-with-lease`；禁止改写已推送的历史（rebase / amend 已推送提交）。
- 禁止 `git commit --no-verify` 跳过钩子。
- 禁止"验证没过先推上去让 CI 看看"。
- 禁止推送 tag、创建 Release、合并或删除分支，除非用户明确要求。
- 禁止推送密钥、Cookie、`_data/`、`build/`、`dist/`、`.workbuddy/`。
- 禁止推送失败时反复重试或换方式绕过；失败就停下来说明原因。

### 3.5 失败处理

| 现象 | 处理 |
| --- | --- |
| push 被拒（non-fast-forward） | 停止，说明本地与远端差异；经用户确认后再 `pull --rebase`；绝不强推 |
| 认证失败 / 无凭据 | 停止，提示用户在本地完成推送或配置凭据；不要把 token 写进任何文件或 remote URL |
| 网络不可用 | 停止，说明提交已在本地、网络恢复后再推 |
| 推送后 CI 变红 | **在下一轮优先修复**；不得把这轮改动当作已完成 |

### 3.6 本机推送通道实测（2026-11-03）

本机装了 GitHub 加速工具（Steam++/Watt Toolkit），它对 `github.com:443` 做 TLS 中间人，
签发的根证书是 `CN=SteamTools Certificate, O=BeyondDimension`。该根证书没有吊销列表，
Git 默认后端因此在握手阶段直接失败：

    fatal: unable to access 'https://github.com/Ky-l0r/Stent.git/':
    schannel: next InitializeSecurityContext failed: CRYPT_E_NO_REVOCATION_CHECK (0x80092012)

实测结论（照抄即可，不要再盲试）：

| 命令 | 结果 |
| --- | --- |
| `git push`（默认） | 失败 |
| `git -c http.schannelCheckRevoke=false push` | 失败（无效） |
| `git -c http.sslBackend=openssl push` | 失败（`unable to get local issuer certificate`，PortableGit 的 CA bundle 里没有那张根） |
| **`git -c http.sslBackend=schannel push`** | **成功，可稳定复现** |
| `git -c http.sslBackend=openssl -c http.sslCAInfo=<系统 bundle + 导出的 BeyondDimension 根>` | 成功，但步骤繁琐，仅作备用 |

- 前四种绕法的共同前提：证书链仍然完整校验，只是不再查询吊销状态。
- 根因应在环境侧解决（关掉加速工具、换系统 `C:\Program Files\Git\cmd\git.exe`、或补 CA），
  **这是本机特性，不是仓库该承担的配置**：不要把它写进 `.git/config` 提交上去，
  也不要改 remote 地址、不要写死代理、不要把 token 塞进 URL。
- 同理，`.pre-commit-config.yaml` 用 `repo: local` 而非社区的远端钩子仓库——
  本机拉不到 GitHub 时，远端钩子初始化失败会**阻断每一次 commit**。

### 3.7 分支现状

- 主干只有 `main`，本地与 origin 一致。CONTRIBUTING 里描述的 `fix/xxx` / `feat/xxx`
  分支是**对外部贡献者的 PR 流程**，仓库所有者在本机默认直接提交 `main`。
- 默认在 `main` 提交与推送；不擅自新建分支、切分支、合并分支。要动分支结构必须先问。

---

## 四、不要过度发挥

本章是本文件最重要的部分之一。项目要的是**准确完成被要求的那一件事**，不是看起来更专业。

### 4.1 默认行为

- 只改与本次要求直接相关的文件。改到第三个无关文件时停下来问。
- 不顺手重构、不顺手改名、不顺手升级依赖、不顺手补抽象层、不顺手加配置开关。
- **不主动全库格式化**。`ruff format` 已定型，只在改动文件上跑；
  确实需要全库重排时先问——那会产生几千行 diff，冲掉 git blame。
- 不为"以后可能需要"预留扩展点。不引入新依赖去解决手写十行就能解决的问题。
- 不把简单逻辑包装成三层抽象。

### 4.2 不许编造

- 不许声称跑过没跑的命令，不许编造测试条数、构建体积、CI 结论。
- 没验证的写"未执行"或"未验证"并说明原因（例如需要真实平台账号）。
- 不确定 API 是否存在时去读源码或依赖文档，不要凭印象写。

### 4.3 需求缺口：问，不要猜

- 用户只描述了 80% 时，问清剩下 20%，不要用"通常都这么做"补齐。
- 同一需求有多种合理解法且代价不同（改 UI 还是改数据、加字段还是加表、
  前端算还是落库）时，列出 2-3 个选项与各自代价，让用户决定。
- 涉及**产品取舍**的一律问：文案口径、统计口径、功能边界、交互流程。
- 用户的要求会触碰第七章的硬规则（尤其是发布风控）时，先指出冲突，等指示。

### 4.4 什么情况可以自行决定

- 拼写、标点、明显笔误、格式化、import 顺序、注释措辞。
- lint / 类型报错的直接修法。
- 已明确要求改动的必要配套（新增页面要接 `NAV_ITEMS`、新增平台要加品牌色）。
- 本文件或 docs 已写明处理方式的重复问题。

### 4.5 怎么问

- 一次问完，不要挤牙膏；给出选项与默认建议。
- 问题里包含：你要做什么、卡在哪、可选方案、各自代价。
- 问完就停。不要一边提问一边按自己的猜测把代码改了。

---

## 五、代码规范

门禁是 ruff（`pyproject.toml` 的 `[tool.ruff]` 是唯一真源，pre-commit 与 CI 读同一份）。

### 5.1 工具链与风格

- 行宽 100、目标 py310、双引号、空格缩进、LF（见 `.editorconfig` / `.gitattributes`）。
- 提交前 `ruff check .` 与 `ruff format --check .` 必须双绿。
- **不许为了让 CI 通过去调高 `line-length`、删除 `ignore` 里的理由注释、或往全局忽略里塞规则**。
  有正当理由就在那一行写 `# noqa: 规则码 - 为什么`。
- 现有 `ignore` 的每条都有原因（中文注释不硬折行、全角标点、刻意 noqa、`dict()` 构造 kwargs、
  短序列用 `+` 拼接），改之前先读注释。
- 新增规则要先说明收益与存量告警数量，别一次性引入几百条。

### 5.2 命名

| 类型 | 约定 | 示例 |
| --- | --- | --- |
| 界面页面 | `XxxPage`，文件 `ui/pages/xxx.py` | `CreatePage` / `pages/create.py` |
| 业务服务 | `XxxService` 或无状态模块函数 | `PublishService` → `services/publisher.py` |
| 平台适配器 | `XxxAdapter`，`key` 用小写平台名 | `XiaoHongShuAdapter` / `"xiaohongshu"` |
| 存储层 | `Database` 单例 + 按领域分组的方法 | `core/db.py` |
| 后台任务 | `XxxWorker` | `ui/workers.py` |
| 自检脚本 | `selftest_*.py` / `ui_*.py`，可直接 `python` 运行 | `tests/selftest_all.py` |

- 模块名用下划线（`douyin_title.py`），包内一律相对导入（`from . import paths`）。
- 面向用户的字符串用中文；日志用中文；代码标识符用英文。

### 5.3 分层与依赖方向

依赖只能单向，禁止反向：

| 层 | 路径 | 职责 | 禁止 |
| --- | --- | --- | --- |
| 界面层 | `stent/ui/` | PySide6 页面、控件、主题、图标 | 写业务规则、直接访问平台适配器内部选择器 |
| 服务层 | `stent/services/` | 热榜 / 创作 / 检查 / 发布编排 / 分析 / 画像 | import `ui/`；启动浏览器做界面该做的事 |
| 平台层 | `stent/platforms/` | 每个平台一个适配器，实现 `base.py` 接口 | 承载业务决策、被 service 绕过接口直接调用 |
| 核心层 | `stent/core/` | SQLite 存储、LLM 客户端 | import 上面任何一层 |

- `core/` 里出现 `from ..ui` 或 `from ..services` 就是错误。
- 平台选择器只能待在 `platforms/`；`services/` 里出现 `"#id"`、`[class*=]` 这类 CSS 选择器就是错误。

### 5.4 具体规则（都是踩过坑写下来的）

- **重依赖必须惰性导入**：`playwright`、平台适配器、PySide6 子模块一律在函数内部 import。
  冷启动 < 3 秒靠的就是这个，顶层 import 会把启动时间吃回去。
- **热点拉取用标准库 `urllib`，不要"统一成 httpx"**。httpx 0.28 在 Windows 上每次构造
  `Client` 约 3 秒（与网络无关），会直接吃掉整个延迟预算。原因见 `docs/development.md`。
- **写入必须读回确认**：任何"往平台页面填东西"的代码，只有读回值匹配才算成功。
  `dispatchEvent` 一定"成功"、`required=False` 找不到也算过关——这两种静默失败都修过，别再引入。
- **发布必须人工确认**：`PublishService.publish` 必须显式 `confirmed=True`，
  默认 `dry_run=True`（只填表不提交）。**不许加全自动批量发布开关**，这是产品红线，
  不是性能问题。dry-run 收尾统一走 `_browser.hold_for_review()`，不要退回上下文管理器自动 `close()`。
- **抖音作品信息走轻量通道**：`services/douyin_title.py` 纯标准库、不启浏览器，拿不到才回退 Playwright。
- **打包是 onedir，不要改回 onefile**（原因见 `Stent.spec` 头部）。别删
  `paths.ensure_playwright_browsers_path()`：冻结态不纠正 `PLAYWRIGHT_BROWSERS_PATH` 必然报"浏览器不可用"。
- **中文输出要防编码**：`stent/app.py` 的 `ensure_utf8_console()` 与两个 selftest 脚本的
  `reconfigure(encoding="utf-8")` 不是冗余——CI 的 windows-latest 是英文环境（cp1252），
  删掉就 UnicodeEncodeError。本地复现：`PYTHONIOENCODING=cp1252 python tests/selftest_all.py`。
- **异常处理**：允许在平台适配器里吞异常以换取"单个平台失败不影响整体"，但必须
  （a）`log.warning` 留下可诊断信息；（b）返回给用户的中文说明不能是"未知错误"。
  禁止把异常吞成 `None` 继续执行到下一屏。
- **版本号只有一处真源**：`stent/__init__.py` 的 `__version__`；`pyproject.toml` 用
  `dynamic = ["version"]` 读它。不要在任何地方再写死版本号。

### 5.5 UI 与主题

- 颜色只从 `stent/ui/theme.py` 取，不写死十六进制；平台品牌色在 `PLATFORM_COLORS`。
- 图标用 `stent/ui/icons.py` 的内联 Lucide SVG，不要引入图片资源或 emoji 当图标。
- 支持浅色 / 暗色双主题，新增控件必须两套主题都看过；语义色要随主题变暗
  （例如警告底色用暗金棕而非浅黄）。
- 布局不许写死像素高度导致小窗口下压叠；发布页的 `_sync_pane_limits()` 就是为此而写。
- 动效与窗口行为改动前读 `docs/ui-internals.md`，无边框窗口依赖 Win32 / DWM 调用。

### 5.6 注释与文案

- 注释解释"为什么"，不重复"做了什么"。绕过上游缺陷、与直觉相反的写法必须写清原因。
- 注释、docstring、提交信息用中文；不加装饰性分隔线（源码里已有的
  `# ----------` 分组风格可沿用）。
- 用户可见文案要具体、说人话：不写"操作失败"，写"简介未自动填写，请手动补上"。
- README 与界面文案沿用现有的 `✅ / ❌ / ⚠️` 与标题 emoji 风格；但不要给代码注释、
  日志、提交信息加装饰性 emoji。

---

## 六、文件管理

### 6.1 目录职责

| 目录 | 放什么 |
| --- | --- |
| `stent/ui/` | 主窗口、无边框窗口、主题、图标、共享控件、后台任务线程 |
| `stent/ui/pages/` | 七个页面：`hotsearch` `create` `publish` `analytics` `profile` `settings` `onboarding` |
| `stent/services/` | 热榜聚合、创作、画像、检查、发布编排、抖音抓取、分析 |
| `stent/platforms/` | `base.py` 接口 + 每平台一个适配器 + `_browser.py` 共用工具 |
| `stent/core/` | `db.py`（SQLite）、`llm.py`（OpenAI 兼容客户端） |
| `stent/resources/` | `sensitive_words.txt` 等随包资源 |
| `stent/skills/` | 从 Easel 裁剪的 prompt 资产，**只作查阅参考**，不是可执行代码 |
| `tests/` | 自检与回归脚本（可直接运行），以及 `check_project.py` |
| `docs/` | 开发文档与截图 |
| `.github/` | CI、Issue 与 PR 模板 |

### 6.2 新文件放哪（按顺序判定）

1. 新页面 → `stent/ui/pages/<key>.py`，并在 `main_window.py` 的 `NAV_ITEMS`
   加一行五元组 `(key, 标题, 图标名, 模块名, 类名)`，图标名要同时加进 `icons.py`。
2. 新业务能力 → `stent/services/<能力>.py`，不碰 UI，能脱离 Qt 单测。
3. 新平台 → `stent/platforms/<key>.py` 实现 `PlatformAdapter`，然后四处配套：
   `platforms/__init__.py` 的 `_PLATFORM_SPECS` 与 `PLATFORM_ORDER`（域名规则见
   `PLATFORM_DOMAINS` / `POST_ID_PATTERNS`）、`ui/theme.py` 的 `PLATFORM_COLORS`、
   `tests/selftest_all.py` 里"四个平台"相关断言。
4. 存储或 LLM 能力 → `stent/core/`，且不得反向依赖上层。
5. 共享控件 / 主题 / 图标 → `ui/components.py`、`ui/theme.py`、`ui/icons.py`。
6. 回归脚本 → `tests/`，命名 `selftest_*.py` 或 `ui_*.py`，同时登记进
   `docs/development.md` 的「按改动区域选跑的回归」表。
7. 都不合适 → **先问**，不要新建顶层包。

### 6.3 入库 / 不入库

**不入库**（`.gitignore` 已覆盖，仍要自查）：

- `_data/`（开发态数据库、日志、截图）、`_reference/`（上游参考代码）、`build/`、`dist/`
- `.ruff_cache/`、`__pycache__/`、`*.pyc`、`.pytest_cache/`
- `.workbuddy/`（本机 AI 工具的工作区数据）
- 任何 `*.log`、导出报表、`stent.log` 片段

**必须入库**：

- `pyproject.toml`、`requirements*.txt`、`Stent.spec`、`build.ps1`
- `.editorconfig`、`.gitattributes`、`.pre-commit-config.yaml`、`.github/workflows/ci.yml`
- `docs/images/` 下的 README 截图（由 `tests/ui_screenshot.py` 生成后挑选裁切）

### 6.4 临时文件

- 需要脚本或中间产物时写到 `_data/`（已忽略）或系统临时目录，任务结束前删除。
- 不在仓库根目录新建 `test.py`、`debug_xxx.py`、`xxx_backup.md`。
- 不留 `.bak`、`.orig`、注释掉的旧代码。

---

## 七、验证门禁

### 7.1 按改动分级

| 改动内容 | 必跑 |
| --- | --- |
| 纯文档 / 纯注释 | 不跑 lint；核对文中命令与链接仍准确；动了 CHANGELOG 版本标题要跑 `python tests/check_project.py` |
| 任意 `.py` | `ruff check .` + `ruff format --check .` |
| `core/`、`services/` | 上项 + `python tests/selftest_all.py`（当前 171 项） |
| 归因、画像记忆、中文文案 | 上项 + `python tests/selftest_attribution_zh.py` |
| `ui/`（含 `pages/`） | 上项 + `$env:QT_QPA_PLATFORM='offscreen'; python main.py --selftest`（7 个页面全构建）+ 对应 `ui_*.py` 回归 |
| `platforms/` | 上项 + selftest 里的平台断言；涉及真实页面的改动**标注"需真机验证"** |
| `pyproject.toml`、依赖、CI、构建脚本 | 全部门禁 + `python tests/check_project.py`；改依赖后本地 `pip install -r requirements.txt` 跑一次源码 |
| 准备发版 | 全部 + `pwsh -File build.ps1 -Clean`（约 2 分半，产物约 190MB） |

### 7.2 常用命令

```powershell
ruff check .                                  # lint（CI 同款，必须零告警）
ruff format --check .                         # 格式校验
python tests/check_project.py                 # 版本号 / CHANGELOG / 依赖口径一致
python tests/selftest_all.py                  # 服务层端到端 171 项
python tests/selftest_attribution_zh.py       # 归因链路中文化
$env:QT_QPA_PLATFORM='offscreen'; python main.py --selftest   # 界面无头构建
python tests/ui_smoke.py                      # 真实窗口双主题冒烟
pwsh -File build.ps1 -Clean                   # 打包
```

### 7.3 硬规则

- **服务层自检不许引入第三方依赖**。`core/`、`services/` 能在只装 Python 的环境跑完，
  CI 的服务层作业也刻意不装 PySide6 / Playwright——一旦偷偷 import，CI 会挂，这是有意的守门。
- 结果如实报告：通过条数与失败数、是否跑了完整门禁。只跑子集就写明"未跑完整门禁"及风险点。
- 平台相关的真机行为（扫码登录、真实投稿、指标抓取）无法在 CI 验证，必须标注"需真机验证"。
- CI 红了要当轮修复。静态检查挂了先本地复现，别反复推提交试错。

---

## 八、文档同步

| 触发 | 必须更新 |
| --- | --- |
| 用户可见行为变化 | `CHANGELOG.md`（在"未发布"版本下按 修复 / 改进 / 其他 小节） |
| 发版 | `stent/__init__.py` 的 `__version__` + `CHANGELOG.md` 版本标题一起改（CI 会校验） |
| 能力或已知限制变化 | `README.md`（含「已知限制」「路线图」表） |
| 构建流程、自检清单、平台改版取证 | `docs/development.md` |
| 无边框窗口 / Win32 / 主题实现 | `docs/ui-internals.md` |
| 分层、代码约定、提交规范 | `CONTRIBUTING.md` |
| 新增或调整回归脚本 | `docs/development.md` 的回归表 |
| 本文件与 CONTRIBUTING 冲突 | 先改本文件，再同步 CONTRIBUTING |

- 测试条数、体积、耗时这类易过期数字，写在 `docs/development.md` 并注明测量日期。
- 文档与代码不一致时以代码为准，同时修正文档；判断不了就先问。
- 文档更新与代码改动放在同一次提交，不要留"稍后补文档"。

---

## 九、安全与隐私

- API Key 只走 `stent/config.py`：优先 Windows 凭据管理器（keyring），降级 DPAPI 加密文件，
  都不可用时只留内存。**绝不明文落盘、绝不打进日志**。
- 日志、崩溃信息、报告里不得出现 Key、Cookie、用户平台账号名。提交 issue 前先自查。
- 平台登录态存在 `paths.browser_dir()` 的持久化目录里，该目录**不得入库、不得打包分发**。
- 不新增统计、埋点、崩溃上报 SDK；不把内容发往用户未配置的模型端点。
- 示例与文档一律用占位符（`sk-...`），不要贴真实值。

---

## 十、提交前自检清单

- [ ] 改动范围与用户要求一致，没有夹带无关修改
- [ ] `ruff check .` 与 `ruff format --check .` 通过
- [ ] 对应分级的自检已跑，结果真实（贴输出）
- [ ] 文档已按第八章同步
- [ ] 提交信息：中文、第一行 ≤72 字、空行后写为什么
- [ ] `git status --short` 无临时文件、无 `_data/`、无密钥
- [ ] 已 push，且 `git status -sb` 无 ahead / behind
- [ ] 推完确认 CI 结果；红了当轮修

---

## 十一、汇报格式

固定四段，简短，不写场面话：

1. **改了什么**：文件清单 + 每个文件一句话。
2. **为什么**：一句话说明动机，涉及取舍的把代价写出来。
3. **验证**：跑了哪些命令、真实结果是什么（通过条数 / 失败数）。
4. **未做 / 风险**：没有就写"无"；有就写清哪些没验证、为什么。

---

## 十二、常用命令速查

```bash
# 看状态与差异
git status --short && git diff --stat

# 提交并推送（第三章流程）
git add <路径> && git commit -F - <<'EOF'
修复 xxx：一句话说清

为什么这么改。
EOF
git fetch origin && git status -sb
git -c http.sslBackend=schannel push origin HEAD   # 本机必需，见 3.6
git status -sb
```

```powershell
# 门禁一条龙（提交前最少跑这些）
ruff check . ; ruff format --check . ; python tests/selftest_all.py

# 界面相关再加
$env:QT_QPA_PLATFORM='offscreen'; python main.py --selftest

# 让源码跑起来看看（改 UI 时优先用这个，别每次打包）
python main.py

# 打包
pwsh -File build.ps1 -Clean
```

---

## 十三、AI 代理特别提醒

- 这个仓库的注释与文档大量写了**为什么**（尤其是 `docs/development.md` 的
  「平台改版时怎么取证」与 CHANGELOG 的修复记录）。改代码前先读，能避免重踩已经修过的坑。
- 平台前端随时改版，选择器失效是常态而非 bug。遇到发布失败：
  **先用 `python tests/bili_probe.py` 之类只读探针取证，再改选择器**，
  不要凭猜反复试——那会真的往平台后台提交东西。
- 不要"为了让测试通过"去改断言。断言挂了先判断是断言过时还是代码回归，
  说不清就先问。
- 端到端验证受限于本机没有真实平台账号：涉及真实登录与投稿的改动，
  实现完标注"需真机验证"，并在汇报里说清哪些环节没验证。
