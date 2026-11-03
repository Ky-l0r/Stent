// 与页面无关的小工具：转义、提示、状态灯、平台色。

export function esc(text) {
  return String(text ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

export function toast(message, level = "info", timeout = 3200) {
  const host = document.getElementById("toast-host");
  if (!host) return;
  const node = document.createElement("div");
  node.className = "toast";
  node.dataset.level = level;
  node.textContent = message;
  host.appendChild(node);
  setTimeout(() => {
    node.style.opacity = "0";
    node.style.transition = "opacity .2s";
    setTimeout(() => node.remove(), 220);
  }, timeout);
}

// 顶栏状态灯：busy / ok / warn / error / idle
export function setStatus(text, level = "idle") {
  const pill = document.getElementById("status");
  if (!pill) return;
  pill.dataset.level = level;
  pill.querySelector(".status-pill__text").textContent = text;
  pill.title = text;
}

export function platformColor(key) {
  return `var(--platform-${key || "unknown"}, var(--muted))`;
}

export function fmtCount(n) {
  const value = Number(n) || 0;
  if (value >= 10000) return `${(value / 10000).toFixed(1)} 万`;
  return String(value);
}

// 双击标题以外的交互里，最简单的「点一下复制」提示
export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast("已复制", "ok", 1500);
  } catch {
    toast("复制失败：当前环境不允许访问剪贴板", "warn");
  }
}

export function debounce(fn, delay = 200) {
  let timer = 0;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), delay);
  };
}
