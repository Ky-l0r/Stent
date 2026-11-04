/** 与主进程 / 后端的唯一出口：页面里不直接写 fetch。 */

import type { StentBridge } from "../electron/shared/types";

declare global {
  interface Window {
    stent: StentBridge;
  }
}

export interface BackendInfo {
  apiBase: string;
  token: string;
}

let info: BackendInfo | null = null;

export async function initBackend(): Promise<BackendInfo> {
  info = await window.stent.backend();
  return info;
}

function base(): string {
  if (!info) throw new Error("后端尚未就绪");
  return info.apiBase;
}

function headers(extra: Record<string, string> = {}): Record<string, string> {
  return { "X-Stent-Token": info?.token ?? "", ...extra };
}

async function readError(resp: Response): Promise<string> {
  try {
    const data = await resp.json();
    if (data?.error?.message) return String(data.error.message);
  } catch {
    /* 不是 JSON，用下面的兜底 */
  }
  return `请求失败（HTTP ${resp.status}）`;
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method, headers: headers() };
  if (body !== undefined) {
    init.headers = headers({ "Content-Type": "application/json" });
    init.body = JSON.stringify(body);
  }
  const resp = await fetch(`${base()}${path}`, init);
  if (!resp.ok) throw new Error(await readError(resp));
  return (await resp.json()) as T;
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body: unknown = {}) => request<T>("POST", path, body),
  put: <T>(path: string, body: unknown = {}) => request<T>("PUT", path, body),
  del: <T>(path: string, body: unknown = {}) => request<T>("DELETE", path, body),
};

export interface StreamHandlers {
  onEvent: (name: string, data: Record<string, unknown>) => void;
  signal?: AbortSignal;
}

/** 流式（SSE）。用 fetch + ReadableStream：EventSource 只能 GET，参数塞不进 URL。 */
export async function stream(
  path: string,
  body: unknown,
  { onEvent, signal }: StreamHandlers
): Promise<void> {
  const resp = await fetch(`${base()}${path}`, {
    method: "POST",
    headers: headers({ "Content-Type": "application/json" }),
    body: JSON.stringify(body),
    signal,
  });
  if (!resp.ok) throw new Error(await readError(resp));
  if (!resp.body) throw new Error("浏览器不支持流式响应");

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let split = buffer.indexOf("\n\n");
    while (split >= 0) {
      const frame = buffer.slice(0, split);
      buffer = buffer.slice(split + 2);
      split = buffer.indexOf("\n\n");

      let name = "message";
      const dataLines: string[] = [];
      for (const line of frame.split("\n")) {
        if (line.startsWith("event:")) name = line.slice(6).trim();
        else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
      }
      if (!dataLines.length) continue;
      try {
        onEvent(name, JSON.parse(dataLines.join("\n")) as Record<string, unknown>);
      } catch {
        onEvent(name, {});
      }
    }
  }
}
