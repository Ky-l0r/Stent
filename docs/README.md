# 文档索引

| 文档 | 内容 |
|---|---|
| [`ui-internals.md`](ui-internals.md) | 无边框窗口的 Win32 实现：样式位补齐、`WM_NCCALCSIZE`、Snap / 圆角 / 阴影、过渡动画的取舍 |
| [`development.md`](development.md) | 打包发布、自检与回归清单、平台改版取证流程、性能指标与实现取舍 |
| [`web-architecture.md`](web-architecture.md) | Web 前端架构：整体结构、前后端职责划分、迁移期的取舍记录 |
| [`images/`](images/) | README 使用的界面截图（1380×854，已裁掉底部状态栏） |

仓库根目录另有：

- [`../README.md`](../README.md) —— 面向使用者的功能与流程说明
- [`../AGENTS.md`](../AGENTS.md) —— 面向 AI 编码代理的门禁型约定（自动推送、验证分级、忌讳）
- [`../CHANGELOG.md`](../CHANGELOG.md) —— 版本更新日志（应用内「版本更新日志」入口读取）
- [`../CONTRIBUTING.md`](../CONTRIBUTING.md) —— 贡献指南
- [`../NOTICE`](../NOTICE) —— 上游来源与修改声明

> 截图由 `tests/ui_web_smoke.py` 生成到临时目录（不入库），
> 再挑选、裁切后放进 `docs/images/`。
