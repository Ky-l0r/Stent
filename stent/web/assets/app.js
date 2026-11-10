// 应用外壳：路由、主题、窗口控制、状态灯。
// 页面各自是一个 ES Module，导出 mount(root, ctx)；这里只负责加载与切换。

import { api, shell } from "./api.js";
import { esc, setStatus, toast } from "./ui.js";

const PAGES = {
  hot: ["热点发现", () => import("./pages/hot.js")],
  create: ["内容创作", () => import("./pages/create.js")],
  drafts: ["草稿箱", () => import("./pages/drafts.js")],
  publish: ["发布中心", () => import("./pages/publish.js")],
  analytics: ["数据分析", () => import("./pages/analytics.js")],
  profile: ["账号画像", () => import("./pages/profile.js")],
  settings: ["设置", () => import("./pages/settings.js")],
};

export const state = { meta: null };

export function navigate(key) {
  const target = PAGES[key] ? key : "hot";
  if (location.hash === `#/${target}`) return;
  location.hash = `#/${target}`;
}

export async function refreshMeta() {
  state.meta = await api.get("/api/meta");
  return state.meta;
}

// 草稿数量角标：新建 / 删除草稿后各页都可以主动刷一次（见 ctx.refreshDrafts）
async function refreshDraftBadge() {
  const badge = document.getElementById("drafts-badge");
  if (!badge) return;
  try {
    const data = await api.get("/api/drafts?limit=1");
    const total = Number(data.total) || 0;
    badge.textContent = total > 99 ? "99+" : String(total);
    badge.classList.toggle("hidden", total === 0);
    badge.title = `${total} 篇草稿`;
  } catch {
    badge.classList.add("hidden");
  }
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

// 页面切换的竞态保护：快速连点导航时，先发起的 mount 不能把后一页的内容盖掉
let routeSeq = 0;
// 页面可以在离开时注册清理（例如停止任务轮询），避免切页后还在打接口
let cleanups = [];

function runCleanups() {
  const pending = cleanups;
  cleanups = [];
  for (const fn of pending) {
    try {
      fn();
    } catch {
      /* 清理失败不影响切页 */
    }
  }
}

async function route() {
  const raw = location.hash.replace(/^#\/?/, "");
  const key = PAGES[raw] ? raw : "hot";
  if (raw && raw !== key) {
    // 未知路由退回首页，同时把地址栏也改正，避免刷新后又回到坏链接
    history.replaceState(null, "", `#/${key}`);
  }
  const seq = ++routeSeq;
  runCleanups();

  document.querySelectorAll(".nav-item").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.route === key);
  });

  const view = document.getElementById("view");
  view.scrollTop = 0;
  view.innerHTML = '<div class="loading">正在加载…</div>';
  // 状态灯是全局的：切页先归位，避免上一页的「正在拉取…」跟着飘过来
  setStatus(state.meta?.llm_ready === false ? "未配置模型" : "就绪",
    state.meta?.llm_ready === false ? "warn" : "idle");

  try {
    // 元信息每次进页都刷新：设置页刚改完模型，创作页的「未配置」提示要立刻消失
    try {
      state.meta = await api.get("/api/meta");
    } catch {
      /* 元信息拿不到时沿用启动时那份，不阻断页面 */
    }
    if (seq !== routeSeq) return;

    const entry = PAGES[key];
    const mod = await entry[1]();
    if (seq !== routeSeq) return;

    view.innerHTML = "";
    document.title = `Stent · ${entry[0]}`;
    const ctx = {
      meta: state.meta,
      navigate,
      refreshMeta,
      refreshDrafts: refreshDraftBadge,
      onLeave(fn) {
        cleanups.push(fn);
      },
    };
    await mod.mount(view, ctx);
  } catch (err) {
    if (seq !== routeSeq) return;
    view.innerHTML = `<div class="empty">页面加载失败：${esc(err.message)}</div>`;
    setStatus("页面加载失败", "error");
  }
}

// 视口边缘多宽算「抓住了窗口边」。无边框窗口的视口就是窗口本身，
// 所以这里判断的是窗口的物理边缘。
const RESIZE_EDGE = 5;
const EDGE_CURSORS = {
  n: "ns-resize",
  s: "ns-resize",
  e: "ew-resize",
  w: "ew-resize",
  ne: "nesw-resize",
  sw: "nesw-resize",
  nw: "nwse-resize",
  se: "nwse-resize",
};

// 铺满工作区的窗口没有可拖的边（与系统一致）。这只是**光标**上的提示：
// 用工作区尺寸反推比每次追问壳层便宜。按下事件照样交给壳层——真最大化了它会
// 直接拒绝，而「恰好调整成铺满工作区但没最大化」的窗口仍然可以拖边。
function looksMaximized() {
  return (
    window.innerWidth >= screen.availWidth - 2 && window.innerHeight >= screen.availHeight - 2
  );
}

function resizeEdgeAt(x, y) {
  const w = window.innerWidth;
  const h = window.innerHeight;
  const left = x <= RESIZE_EDGE;
  const right = x >= w - 1 - RESIZE_EDGE;
  const top = y <= RESIZE_EDGE;
  const bottom = y >= h - 1 - RESIZE_EDGE;
  return `${top ? "n" : bottom ? "s" : ""}${left ? "w" : right ? "e" : ""}`;
}

function wireShell() {
  // 自绘标题栏：按下时只把「按下点的屏幕坐标」交给壳层，之后由 Python 侧
  // 按光标摆窗口（见 docs/ui-internals.md）。逐帧回传位移会慢半拍。
  const zone = document.getElementById("dragzone");
  zone.addEventListener("pointerdown", (event) => {
    if (!shell.available() || event.button !== 0) return;
    shell.call("start_drag", Math.round(event.screenX), Math.round(event.screenY));
  });
  zone.addEventListener("dblclick", () => shell.call("toggle_maximize"));

  // 边缘拖拽改尺寸：命中区只有 5px，不值得为它加 8 个 DOM 覆盖层，
  // 直接在捕获阶段拦下按下事件即可——顺带挡住标题栏的拖动。
  window.addEventListener(
    "pointerdown",
    (event) => {
      if (!shell.available() || event.button !== 0) return;
      const edge = resizeEdgeAt(event.clientX, event.clientY);
      if (!edge) return;
      event.preventDefault();
      event.stopPropagation();
      shell.call("start_resize", edge, Math.round(event.screenX), Math.round(event.screenY));
    },
    { capture: true },
  );
  window.addEventListener("pointermove", (event) => {
    if (!shell.available() || event.buttons !== 0) return;
    const edge = looksMaximized() ? "" : resizeEdgeAt(event.clientX, event.clientY);
    document.documentElement.style.cursor = EDGE_CURSORS[edge] || "";
  });
  // 光标从窗口边缘移出去之后不会再收到 pointermove，双箭头会留在窗口里
  document.addEventListener("pointerleave", () => {
    document.documentElement.style.cursor = "";
  });

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
  refreshDraftBadge();
  await route();
}

boot();
