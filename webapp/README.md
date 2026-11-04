# Stent Web 前端（Electron + React）

这是 Stent 的新前端。旧的两套界面仍然保留，都能跑：

| 入口 | 说明 |
| --- | --- |
| `python main.py` | pywebview 壳 + 原生 HTML（v1.2 的默认，轻量、无 Node） |
| `python main.py --qt` | 最早的 PySide6 界面（回退用） |
| `npm run dev`（本目录） | **Electron 壳 + React**，本文档的主角 |

三者共用同一个 Python 后端：`stent/api` 暴露的 16 条 HTTP / SSE 路由。

## 目录结构

```
webapp/
├── electron/
│   ├── main/index.ts      主进程：起后端 → 等就绪 → 开窗口 → 退出时杀进程树
│   ├── preload/index.ts   预加载：只暴露 window.stent 一组窄接口
│   └── shared/types.ts    两端共用的类型
├── src/
│   ├── App.tsx            外壳（标题栏 / 侧栏 / 路由 / 提示）
│   ├── api.ts             fetch + SSE 封装，页面不直接写请求
│   ├── store.ts           zustand：路由、主题、跨页草稿
│   ├── styles.css         设计变量与组件样式（与 Qt 版同色系）
│   └── pages/             热点 / 创作 / 发布 / 画像 / 设置
├── index.html
├── vite.config.ts
├── tsconfig*.json
└── electron-builder.json
```

## 开发

```powershell
cd webapp
npm install
npm run dev          # 编译 + 起 Electron（后端由主进程自动拉起）
```

主进程在开发态直接跑仓库源码（`python -m stent.webapp --headless`），
所以不需要另外开一个终端跑后端；改了 Python 代码重启 Electron 即可。

指定别的解释器：

```powershell
$env:STENT_PYTHON = "C:\Python313\python.exe"; npm run dev
```

## 打包

Electron 包**不含 Python**——它把 PyInstaller 的产物当资源塞进去：

```powershell
# 1. 先在仓库根目录产出后端（产物在 dist\Stent）
pwsh -File build.ps1

# 2. 再打前端
cd webapp
npm run package:dir   # 免安装的目录版，产物在 webapp\release\win-unpacked
```

`electron-builder.json` 的 `extraResources` 把 `../dist/Stent` 整个目录复制到
`resources/backend/`，主进程按 `process.resourcesPath/backend/Stent.exe` 启动它。

想跳过第 1 步做联调，可以用环境变量直接指向已有的后端：

```powershell
$env:STENT_BACKEND_EXE = "D:\Subject\Stent\dist-web2\Stent\Stent.exe"
npm run dev
```

## 与 pywebview 版的差异（都是有意的）

- **拖拽**用 `-webkit-app-region: drag`，不再需要 JS 逐帧上报位移。
- **令牌**由主进程生成、命令行传给后端、经 IPC 交给渲染进程；
  pywebview 版是后端生成后注入 HTML。
- **文件选择**走 IPC 调 `dialog.showOpenDialog`，返回真实路径。
- **单实例**用 `app.requestSingleInstanceLock()`，第二次双击把窗口拉到前台。
- 产物多约 150MB（Electron 自带 Chromium），换来渲染一致性、DevTools 与后续自动更新的空间。

## 已知限制

- 目前只出免安装目录版（`--dir`）；NSIS 安装包需要签名配置，等要发版再补。
- 打包后端与前端是两步，尚未合并成一条命令。
