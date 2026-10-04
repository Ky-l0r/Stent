# Stent 内置 SKILL 资产（源自 Easel，已裁剪）

本目录保存 Stent 从上游项目 [Easel](https://github.com/ZJU-REAL/Easel)（Apache-2.0）
提取的 prompt 知识与平台规则。它们**不是可执行代码**，而是供人查阅、供后续维护
prompt 时对照的参考资料。

## 来源与处理方式

| 本目录文件 | 上游路径 | 处理方式 |
|---|---|---|
| `hotlist-apis.md` | `skills/shared/hotlist-apis.md` | 直接复用（热榜数据源清单，Stent 已按此实现 `services/hotsearch.py`） |
| `platform-specs.md` | `skills/openclaw/social-content/references/platform-specs.md` | 直接复用（平台字数/体例/时段规范，已内联进 `services/creator.py` 的 `PLATFORM_STYLES`） |
| `content-formats.md` | `skills/openclaw/social-content/references/content-formats.md` | 直接复用（内容形态组织方式） |
| `hashtag-strategy.md` | `skills/openclaw/social-content/references/hashtag-strategy.md` | 直接复用（标签策略） |
| `quality-gate/SKILL.md` 等 | `skills/openclaw/skill-quality-gate/**` | 复用并裁剪（评审维度已简化为创作页的「AI 质量自检」四维打分） |
| `publish-checklist.md` | `skills/openclaw/skill-publish-checklist/SKILL.md` | 复用并裁剪（检查项已落到 `services/precheck.py`） |
| `risk-scanner.md`、`washing-patterns.md`、`copyright-guide.md` | `skills/openclaw/skill-risk-scanner/**` | 复用并裁剪（风险规则已落到 `services/precheck.py` 与 `resources/sensitive_words.txt`） |
| `copy-frameworks.md` | `skills/openclaw/copywriting/references/copy-frameworks.md` | 直接复用（文案框架） |
| `hook-formulas.md` | `skills/openclaw/skill-hook-generator/references/hook-formulas.md` | 直接复用（标题钩子公式，用于「重新生成标题」） |
| `platform-traits.md` | `skills/openclaw/skill-cross-platform-diff/references/platform-traits.md` | 直接复用（跨平台差异） |

## 未纳入的部分

按企划书 2.3「不做的事」与 4.2「剥离」，以下上游能力**不参与打包**：
视频剪辑、AI 生视频、AI 音乐、声音克隆、短剧生成、小说创作、论文解读、
内容日历、直播策划、商单方案、多账号矩阵、团队协作、云端同步。

## 许可证

以上内容版权归 Easel 项目原作者所有，以 Apache License 2.0 授权。
完整许可文本见仓库根目录 `LICENSE`，来源声明见根目录 `NOTICE`。
本目录内文件若与上游存在差异，均属于为适配 Stent 而做的裁剪，未改变原意。
