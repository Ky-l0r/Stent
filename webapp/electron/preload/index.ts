/** 预加载脚本：只暴露一组窄接口，渲染进程拿不到 Node。 */

import { contextBridge, ipcRenderer } from "electron";

import type { BackendInfo, StentBridge, WindowAction } from "../shared/types";

const bridge: StentBridge = {
  backend: () => ipcRenderer.invoke("stent:backend") as Promise<BackendInfo>,
  pickFiles: (kind = "media") => ipcRenderer.invoke("stent:pick-files", kind) as Promise<string[]>,
  windowAction: (action: WindowAction) => ipcRenderer.send("stent:window", action),
  openExternal: (url: string) => ipcRenderer.invoke("stent:open-external", url) as Promise<void>,
};

contextBridge.exposeInMainWorld("stent", bridge);
