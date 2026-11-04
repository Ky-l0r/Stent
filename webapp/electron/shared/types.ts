/** 主进程与渲染进程之间的契约。改动这里要同时改 preload 与 src/bridge.ts。 */

export interface BackendInfo {
  /** 形如 http://127.0.0.1:38217 */
  apiBase: string;
  /** 每次启动随机生成，随请求头 X-Stent-Token 发出 */
  token: string;
}

export type WindowAction = "minimize" | "toggle-maximize" | "close";

export interface StentBridge {
  /** 取后端地址与令牌；后端未就绪时会 reject */
  backend(): Promise<BackendInfo>;
  /** 原生文件选择（浏览器给不了真实路径，只能壳层代劳） */
  pickFiles(kind?: "media" | "image"): Promise<string[]>;
  windowAction(action: WindowAction): void;
  openExternal(url: string): void;
}
