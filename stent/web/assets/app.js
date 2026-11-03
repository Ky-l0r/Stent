// 应用外壳：路由、主题、窗口控制、状态灯。
// 页面各自是一个 ES Module，导出 mount(root, ctx)；这里只负责加载与切换。

import { api, shell } from "./api.js";
import { esc, setStatus, toast } from "./ui.js";

const PAGES = {
  hot: ["热点发现", () => import("./pages/hot.js")],
  create: ["内容创作", () => import("./pages/create.js")],
  publish: ["发布中心", () => import("./pages/publish.js")],
  profile: ["账号画像", () => import("./pages/profile.js")],
  settings: ["设置", () => import("./pages/settings.js")],
};

export const state = { meta: null };

export function navigate(key) {
  if (location.hash === `#/${key}`) return;
  location.hash = `#/${key}`;
}

export async function refreshMeta() {
  state.meta = await api.get("/api/meta");
  return state.meta;
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme === "dark" ? "dark" : "light";
  const btn = document.getElementById("theme-btn");
  if (btn) btn.textContent = theme === "dark" ? "☀" : "◐";
}

async function toggleTheme() {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  applyTheme(next);
  try {
    await api.put("/api/settings", { theme: next });
  } catch (err) {
    toast(`主题保存失败：${err.message}`, "warn");
  }
}

async function route() {
  const key = location.hash.replace(/^#\/?/, "") || "hot";
  const entry = PAGES[key] || PAGES[key === "" ? "hot" : "hot"];
  document.querySelectorAll(".nav-item").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.route === key);
  });

  const view = document.getElementById("view");
  view.innerHTML = '<div class="loading">正在加载…</div>';
  // 状态灯是全局的：切页先归位，避免上一页的「正在拉取…」跟着飘过来
  setStatus("就绪", "idle");
  try {
    const mod = await entry[1]();
    view.innerHTML = "";
    document.title = `Stent · ${entry[0]}`;
    await mod.mount(view, { meta: state.meta, navigate, refreshMeta });
  } catch (err) {
    view.innerHTML = `<div class="empty">页面加载失败：${esc(err.message)}</div>`;
    setStatus("页面加载失败", "error");
  }
}

function wireShell() {
  // 自绘标题栏：没有原生边框，拖动靠把位移量交给壳层窗口
  const zone = document.getElementById("dragzone");
  let drag = null;
  zone.addEventListener("pointerdown", (event) => {
    if (!shell.available() || event.button !== 0) return;
    drag = { x: event.screenX, y: event.screenY };
    zone.setPointerCapture(event.pointerId);
  });
  zone.addEventListener("pointermove", (event) => {
    if (!drag) return;
    const dx = event.screenX - drag.x;
    const dy = event.screenY - drag.y;
    if (!dx && !dy) return;
    drag = { x: event.screenX, y: event.screenY };
    shell.call("move_window", dx, dy); // 不 await：拖拽不能等一次往返
  });
  zone.addEventListener("pointerup", (event) => {
    drag = null;
    try { zone.releasePointerCapture(event.pointerId); } catch { /* 已释放 */ }
  });
  zone.addEventListener("dblclick", () => shell.call("toggle_maximize"));

  document.querySelectorAll(".win-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const action = btn.dataset.win;
      if (action === "close") await shell.call("close");
      else if (action === "maximize") await shell.call("toggle_maximize");
      else await shell.call(action);
    });
  });
}

async function boot() {
  document.querySelectorAll(".nav-item").forEach((btn) => {
    btn.addEventListener("click", () => navigate(btn.dataset.route));
  });
  document.getElementById("theme-btn").addEventListener("click", toggleTheme);
  document.getElementById("status").addEventListener("click", () => {
    toast("所有数据都存在本机，接口只监听 127.0.0.1", "info", 4000);
  });
  window.addEventListener("hashchange", route);
  wireShell();

  try {
    const health = await api.get("/api/health");
    document.getElementById("version-tag").textContent = `v${health.version}`;
    state.meta = await api.get("/api/meta");
    applyTheme(state.meta.theme);
    setStatus(health.llm_ready ? "就绪" : "未配置模型", health.llm_ready ? "idle" : "warn");
  } catch (err) {
    setStatus("接口未就绪", "error");
    toast(`无法连接本机接口：${err.message}`, "error", 8000);
    document.getElementById("view").innerHTML =
      '<div class="empty">接口服务未启动。请从应用图标启动，或运行 python -m stent.webapp</div>';
    return;
  }
  await route();
}

boot();
