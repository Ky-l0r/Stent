// 接口客户端：所有请求都在这里出去，页面里不直接写 fetch。
// Token 由后端在返回 index.html 时注入，凭它挡住本机其它进程。

const TOKEN = document.querySelector('meta[name="stent-token"]')?.content || "";

function headers(extra = {}) {
  return { "X-Stent-Token": TOKEN, ...extra };
}

async function readError(resp) {
  try {
    const data = await resp.json();
    if (data?.error?.message) return data.error.message;
  } catch {
    /* 响应不是 JSON，走下面的兜底文案 */
  }
  return `请求失败（HTTP ${resp.status}）`;
}

async function request(method, path, body) {
  const init = { method, headers: headers() };
  if (body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  const resp = await fetch(path, init);
  if (!resp.ok) throw new Error(await readError(resp));
  return resp.json();
}

export const api = {
  get: (path) => request("GET", path),
  post: (path, body) => request("POST", path, body ?? {}),
  put: (path, body) => request("PUT", path, body ?? {}),
  del: (path, body) => request("DELETE", path, body ?? {}),
};

// 流式（SSE）。用 fetch + ReadableStream 而不是 EventSource：
// EventSource 只能发 GET，创作请求带一大坨参数，塞进 URL 不现实。
export async function stream(path, payload, { onEvent, signal } = {}) {
  const resp = await fetch(path, {
    method: "POST",
    headers: headers({ "Content-Type": "application/json" }),
    body: JSON.stringify(payload),
    signal,
  });
  if (!resp.ok) throw new Error(await readError(resp));
  if (!resp.body) throw new Error("浏览器不支持流式响应");

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let split;
    while ((split = buffer.indexOf("\n\n")) >= 0) {
      const frame = buffer.slice(0, split);
      buffer = buffer.slice(split + 2);
      const event = parseFrame(frame);
      if (event) onEvent?.(event.name, event.data);
    }
  }
}

function parseFrame(frame) {
  let name = "message";
  const dataLines = [];
  for (const line of frame.split("\n")) {
    if (line.startsWith("event:")) name = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
  }
  if (!dataLines.length) return null;
  try {
    return { name, data: JSON.parse(dataLines.join("\n")) };
  } catch {
    return { name, data: {} };
  }
}

// 壳层能力（窗口控制 / 原生文件选择）。浏览器模式下不存在，调用方要判空。
export const shell = {
  available: () => Boolean(window.pywebview?.api),
  async call(method, ...args) {
    const bridge = window.pywebview?.api;
    if (!bridge || typeof bridge[method] !== "function") return null;
    return bridge[method](...args);
  },
};

/**
 * 轮询一个后台任务直到终态。
 *
 * 为什么不用 SSE：任务结果不需要逐字推送，1 秒一次轮询足够；而 SSE 需要自己维护
 * 连接存活与重连，对「单人单机、最多几十次请求」的场景是过度设计。
 *
 * 返回的 Promise 额外带 `cancel()`：页面离开或用户点取消时调用，
 * 它会请求服务端置位取消信号并让本地轮询立即停下（不再等待任务真正收尾）。
 */
export function watchJob(id, { onTick, interval = 900 } = {}) {
  let stopped = false;
  let timer = 0;
  let failures = 0;
  let settle;
  const promise = new Promise((resolve, reject) => {
    settle = { resolve, reject };
    const tick = async () => {
      if (stopped) return;
      try {
        const data = await api.get(`/api/jobs?id=${encodeURIComponent(id)}`);
        failures = 0;
        const job = data.job;
        onTick?.(job);
        if (job.terminal) {
          stopped = true;
          resolve(job);
          return;
        }
      } catch (err) {
        // 任务被回收或接口短暂不可用：连续失败才判定失败，避免一次抖动就打断长任务
        failures += 1;
        if (failures >= 3) {
          stopped = true;
          reject(err);
          return;
        }
      }
      timer = setTimeout(tick, interval);
    };
    tick();
  });
  promise.cancel = async () => {
    if (stopped) return;
    stopped = true;
    clearTimeout(timer);
    settle.resolve({ status: "cancelled", message: "已取消", terminal: true, percent: -1 });
    try {
      await api.post("/api/jobs/cancel", { id });
    } catch {
      /* 取消失败也不影响本地停止轮询；任务会按自己的超时收尾 */
    }
  };
  // 离开页面时只停轮询、**不取消任务**：发布可能正停在浏览器里等用户确认
  promise.stop = () => {
    stopped = true;
    clearTimeout(timer);
    settle.resolve({ status: "detached", message: "已离开页面", terminal: true, percent: -1 });
  };
  return promise;
}

