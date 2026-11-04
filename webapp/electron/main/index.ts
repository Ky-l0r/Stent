/**
 * Electron 主进程：起 Python 后端 → 等它就绪 → 开窗口 → 收尾时把后端杀干净。
 *
 * 为什么由主进程托管后端（而不是让用户先手动跑 python）：
 * 打包后的客户端里没有「命令行」这个概念，双击图标就该能用。
 *
 * 令牌流：主进程生成随机令牌 → 通过命令行传给后端 → 渲染进程经 preload 的 IPC 取；
 * 这样页面既不需要读 index.html 里的 meta，也不用把令牌写进任何文件。
 */

import { app, BrowserWindow, dialog, ipcMain, shell } from "electron";
import { spawn, type ChildProcess } from "node:child_process";
import { randomBytes } from "node:crypto";
import fs from "node:fs";
import net from "node:net";
import path from "node:path";

import type { BackendInfo, WindowAction } from "../shared/types";

const isDev = !app.isPackaged;
const DEV_SERVER_URL = process.env.VITE_DEV_SERVER_URL ?? "";

let backend: ChildProcess | null = null;
let backendInfo: BackendInfo | null = null;
let mainWindow: BrowserWindow | null = null;
let lastBackendOutput = "";

/** 让系统分一个空闲端口：先监听 0 再关掉，比写死 8765 安全。 */
function findFreePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      const port = typeof address === "object" && address ? address.port : 0;
      server.close(() => (port ? resolve(port) : reject(new Error("取不到空闲端口"))));
    });
  });
}

/** 后端可执行文件 / 启动命令。 */
function resolveBackendCommand(): { command: string; args: string[]; cwd: string } {
  const override = process.env.STENT_BACKEND_EXE;
  if (override && fs.existsSync(override)) {
    return { command: override, args: [], cwd: path.dirname(override) };
  }
  if (app.isPackaged) {
    const exe = path.join(
      process.resourcesPath,
      "backend",
      process.platform === "win32" ? "Stent.exe" : "Stent"
    );
    return { command: exe, args: [], cwd: path.dirname(exe) };
  }
  // 开发态：直接跑仓库源码（webapp/ 的上一级就是仓库根）
  const repoRoot = path.resolve(__dirname, "..", "..", "..");
  return {
    command: process.env.STENT_PYTHON || "python",
    args: ["-m", "stent.webapp"],
    cwd: repoRoot,
  };
}

async function startBackend(): Promise<BackendInfo> {
  const port = await findFreePort();
  const token = randomBytes(24).toString("base64url");
  const command = resolveBackendCommand();
  const args = [
    ...command.args,
    "--headless",
    "--host",
    "127.0.0.1",
    "--port",
    String(port),
    "--token",
    token,
  ];

  console.log(`[stent] 启动后端：${command.command} ${args.join(" ")}`);
  backend = spawn(command.command, args, {
    cwd: command.cwd,
    windowsHide: true,
    env: { ...process.env, PYTHONUTF8: "1" },
  });

  const collect = (chunk: Buffer) => {
    lastBackendOutput = (lastBackendOutput + chunk.toString("utf8")).slice(-4000);
  };
  backend.stdout?.on("data", collect);
  backend.stderr?.on("data", collect);
  backend.on("exit", (code) => {
    console.log(`[stent] 后端退出，code=${code}`);
    backend = null;
  });

  const apiBase = `http://127.0.0.1:${port}`;
  const info: BackendInfo = { apiBase, token };

  // 轮询 /api/health：PyInstaller 的窗口版没有控制台，读不到 stdout，只能靠端口探测
  const deadline = Date.now() + 40_000;
  while (Date.now() < deadline) {
    if (backend === null) {
      throw new Error(`后端进程启动失败：\n${lastBackendOutput.slice(-800) || "（无输出）"}`);
    }
    try {
      const resp = await fetch(`${apiBase}/api/health`, {
        headers: { "X-Stent-Token": token },
      });
      if (resp.ok) {
        backendInfo = info;
        console.log(`[stent] 后端就绪：${apiBase}`);
        return info;
      }
    } catch {
      /* 还没起来，继续等 */
    }
    await new Promise((r) => setTimeout(r, 300));
  }
  throw new Error(`后端 40 秒内未就绪：\n${lastBackendOutput.slice(-800) || "（无输出）"}`);
}

/** Windows 下 spawn 的是引导器，真正干活的是子进程，必须整树杀。 */
function stopBackend(): void {
  const child = backend;
  backend = null;
  if (!child?.pid) return;
  if (process.platform === "win32") {
    spawn("taskkill", ["/pid", String(child.pid), "/T", "/F"], {
      stdio: "ignore",
      windowsHide: true,
    });
    return;
  }
  try {
    child.kill("SIGTERM");
  } catch {
    /* 已退出 */
  }
}

function createWindow(info: BackendInfo): void {
  mainWindow = new BrowserWindow({
    width: 1380,
    height: 854,
    minWidth: 1080,
    minHeight: 700,
    frame: false,
    backgroundColor: "#F6F5FA",
    show: false,
    webPreferences: {
      preload: path.join(__dirname, "..", "preload", "index.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  mainWindow.once("ready-to-show", () => mainWindow?.show());
  mainWindow.on("closed", () => {
    mainWindow = null;
  });

  // 渲染进程的报错只出现在 DevTools 里，打包后根本看不到；
  // 转到主进程日志，用户报「白屏」时能直接看数据目录下的 logs。
  mainWindow.webContents.on("console-message", (_event, level, message) => {
    if (level >= 2) console.error(`[renderer] ${message}`);
  });
  mainWindow.webContents.on("did-finish-load", () => {
    console.log("[stent] 窗口页面已加载");
  });
  mainWindow.webContents.on("did-fail-load", (_event, code, description) => {
    console.error(`[stent] 页面加载失败：${code} ${description}`);
  });

  // 外链一律交给系统浏览器，别在应用窗口里跳走
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith("http://") || url.startsWith("https://")) shell.openExternal(url);
    return { action: "deny" };
  });

  if (DEV_SERVER_URL) {
    void mainWindow.loadURL(DEV_SERVER_URL);
  } else {
    void mainWindow.loadFile(path.join(__dirname, "..", "renderer", "index.html"));
  }

  ipcMain.handle("stent:backend", () => info);

  ipcMain.handle("stent:pick-files", async (_event, kind: "media" | "image" = "media") => {
    const filters =
      kind === "image"
        ? [{ name: "图片", extensions: ["png", "jpg", "jpeg", "webp"] }]
        : [
            {
              name: "媒体文件",
              extensions: ["png", "jpg", "jpeg", "webp", "mp4", "mov"],
            },
          ];
    const result = await dialog.showOpenDialog({
      properties: ["openFile", "multiSelections"],
      filters,
    });
    return result.canceled ? [] : result.filePaths;
  });

  ipcMain.handle("stent:open-external", (_event, url: string) => {
    if (typeof url === "string" && /^https?:\/\//i.test(url)) void shell.openExternal(url);
  });

  ipcMain.on("stent:window", (event, action: WindowAction) => {
    const win = BrowserWindow.fromWebContents(event.sender);
    if (!win) return;
    if (action === "minimize") win.minimize();
    else if (action === "close") win.close();
    else if (action === "toggle-maximize") {
      win.isMaximized() ? win.unmaximize() : win.maximize();
    }
  });
}

// 单实例：第二次双击把已有窗口拉到前台，而不是再起一套后端
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (!mainWindow) return;
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.focus();
  });

  app.whenReady().then(async () => {
    try {
      const info = await startBackend();
      createWindow(info);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      console.error(`[stent] ${message}`);
      dialog.showErrorBox("Stent 启动失败", `${message}\n\n日志：数据目录下的 logs/stent.log`);
      app.quit();
    }
  });

  app.on("window-all-closed", () => app.quit());
  app.on("before-quit", stopBackend);
  process.on("exit", stopBackend);

  if (isDev) console.log("[stent] 开发模式：后端跑仓库源码，窗口加载 dist/renderer");
}
