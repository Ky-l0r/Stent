<!--
请先读 CONTRIBUTING.md。一个 PR 解决一件事；排版修复和功能新增请分开提。
-->

## 问题现象

<!-- 修了什么 bug / 加了什么能力。关联 Issue 写 Closes #编号 -->

## 根因与修法

<!-- 为什么这样改，而不是那样改。平台适配器改动请说明验证时的平台页面版本 -->

## 跑了哪些自检

```
<!-- 贴命令与结果，例如：
python tests/selftest_all.py     → 203 通过 / 0 失败
python tests/ui_web_window.py    → 12 项窗口行为全部通过
-->
```

- [ ] 服务层 `tests/selftest_all.py` 全绿
- [ ] 界面冒烟 `python tests/ui_web_smoke.py` 通过（未装浏览器时注明跳过）
- [ ] 按改动区域补跑了对应回归脚本（清单见 docs/development.md）
- [ ] 修 bug 时补了能复现该 bug 的自检项
- [ ] 界面视觉改动附了改前 / 改后截图

## 需要注意的

<!-- 数据库结构变更（老库如何迁移）、依赖新增、打包过滤表是否要动 -->
