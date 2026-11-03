// 热点发现：平台 / 垂类 / 关键词三重筛选，双击一行送进创作。

import { api } from "../api.js";
import { copyText, debounce, esc, platformColor, setStatus, toast } from "../ui.js";

const filters = { keyword: "", category: "", platforms: new Set() };
let data = { items: [], platforms: [], categories: [] };

export async function mount(root, ctx) {
  root.innerHTML = `
    <div class="page-head">
      <div>
        <h1 class="page-title">热点发现</h1>
        <p class="page-desc">多平台并发聚合，双击任意一行送进创作</p>
      </div>
      <div class="page-actions">
        <input id="q" type="text" placeholder="搜索标题…" style="width:200px" value="${esc(filters.keyword)}">
        <button class="btn" id="refresh">刷新</button>
      </div>
    </div>
    <div class="card" id="filters"><span class="muted">正在加载…</span></div>
    <div class="card" id="list"><div class="loading">正在拉取各平台热榜…</div></div>
  `;

  root.querySelector("#q").addEventListener(
    "input",
    debounce((event) => {
      filters.keyword = event.target.value.trim();
      renderList(root, ctx);
    }, 180)
  );
  root.querySelector("#refresh").addEventListener("click", () => load(true, root, ctx));

  await load(false, root, ctx);
}

async function load(force, root, ctx) {
  setStatus("正在并发拉取各平台热榜…", "busy");
  try {
    data = await api.get(`/api/hot${force ? "?force=1" : ""}`);
  } catch (err) {
    setStatus("热榜拉取失败", "error");
    root.querySelector("#list").innerHTML = `<div class="empty">${esc(err.message)}</div>`;
    return;
  }

  const ok = data.platforms.filter((p) => p.count > 0).length;
  const stale = data.platforms.filter((p) => p.stale).length;
  setStatus(
    `${ok} 平台正常${stale ? ` · ${stale} 个用的是旧缓存` : ""} · ${data.items.length} 条`,
    stale ? "warn" : "ok"
  );

  renderFilters(root, ctx);
  renderList(root, ctx);
}

function renderFilters(root, ctx) {
  const host = root.querySelector("#filters");
  const chips = data.platforms
    .map((p) => {
      const active = filters.platforms.has(p.key) ? " active" : "";
      return `<button class="chip${active}" data-platform="${esc(p.key)}" title="${esc(p.error || "")}">
        ${esc(p.label)} ${p.count}${p.stale ? " ⚠" : ""}
      </button>`;
    })
    .join("");

  const cats = [{ name: "", count: data.items.length }, ...data.categories]
    .map((c) => {
      const value = c.name;
      const active = filters.category === value ? " active" : "";
      return `<button class="chip${active}" data-category="${esc(value)}">${esc(value || "全部垂类")} ${c.count}</button>`;
    })
    .join("");

  host.innerHTML = `<div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:10px">${chips}</div>
    <div style="display:flex;flex-wrap:wrap;gap:6px">${cats}</div>`;

  host.querySelectorAll("[data-platform]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const key = btn.dataset.platform;
      if (filters.platforms.has(key)) filters.platforms.delete(key);
      else filters.platforms.add(key);
      renderFilters(root, ctx);
      renderList(root, ctx);
    });
  });
  host.querySelectorAll("[data-category]").forEach((btn) => {
    btn.addEventListener("click", () => {
      filters.category = filters.category === btn.dataset.category ? "" : btn.dataset.category;
      renderFilters(root, ctx);
      renderList(root, ctx);
    });
  });
}

function visibleItems() {
  const keyword = filters.keyword.toLowerCase();
  return data.items.filter((item) => {
    if (filters.platforms.size && !filters.platforms.has(item.platform)) return false;
    if (filters.category && (item.category || "综合") !== filters.category) return false;
    if (keyword && !`${item.title} ${item.keyword || ""}`.toLowerCase().includes(keyword)) return false;
    return true;
  });
}

function renderList(root, ctx) {
  const host = root.querySelector("#list");
  const items = visibleItems();
  if (!items.length) {
    host.innerHTML = '<div class="empty">没有匹配的热点，换个关键词或取消筛选试试</div>';
    return;
  }

  host.innerHTML = `<div class="list">${items
    .map(
      (item) => `
      <div class="row" data-title="${esc(item.title)}" data-url="${esc(item.url)}" data-platform="${esc(item.platform)}">
        <span class="row__rank">${item.rank || ""}</span>
        <span class="row__dot" style="background:${platformColor(item.platform)}"></span>
        <span class="row__title">${esc(item.title)}</span>
        <span class="row__meta">${esc(item.heat || "")}</span>
        <span class="row__actions">
          <button class="btn btn--sm btn--ghost" data-act="copy">复制</button>
          <button class="btn btn--sm btn--ghost" data-act="open">原文</button>
        </span>
      </div>`
    )
    .join("")}</div>`;

  host.querySelectorAll(".row").forEach((row) => {
    row.addEventListener("dblclick", () => {
      sessionStorage.setItem(
        "stent.pending",
        JSON.stringify({ topic: row.dataset.title, source: row.dataset.title })
      );
      toast("已送入创作", "ok", 1600);
      ctx.navigate("create");
    });
    row.querySelector('[data-act="copy"]')?.addEventListener("click", (event) => {
      event.stopPropagation();
      copyText(row.dataset.title);
    });
    row.querySelector('[data-act="open"]')?.addEventListener("click", (event) => {
      event.stopPropagation();
      const url = row.dataset.url;
      if (!url) return toast("这条没有原链接", "warn", 1600);
      const bridge = window.pywebview?.api;
      if (bridge?.open_external) bridge.open_external(url);
      else window.open(url, "_blank", "noopener");
    });
  });
}
