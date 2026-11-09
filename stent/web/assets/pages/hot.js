// 热点发现：平台 / 垂类 / 关键词三重筛选，双击一行送进创作。

import { api } from "../api.js";
import {
  copyText,
  debounce,
  esc,
  openExternal,
  platformColor,
  setBusy,
  setStatus,
  toast,
} from "../ui.js";

const filters = { keyword: "", category: "", platforms: new Set() };
let data = { items: [], platforms: [], categories: [] };
// 页面是否还挂在界面上：热榜请求可能几秒才回来，切页后不能再改全局状态灯
let active = false;

export async function mount(root, ctx) {
  filters.platforms.clear();
  active = true;
  ctx.onLeave(() => {
    active = false;
  });
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
  root.querySelector("#refresh").addEventListener("click", (event) => load(true, root, ctx, event.target));

  await load(false, root, ctx);
}

async function load(force, root, ctx, button) {
  const host = root.querySelector("#list");
  const refresh = button || root.querySelector("#refresh");
  setBusy(refresh, true, force ? "刷新中…" : "加载中…");
  setStatus("正在并发拉取各平台热榜…", "busy");
  if (force) host.innerHTML = '<div class="loading">正在刷新各平台热榜…</div>';
  try {
    data = await api.get(`/api/hot${force ? "?force=1" : ""}`);
  } catch (err) {
    if (!active) return;
    setStatus("热榜拉取失败", "error");
    host.innerHTML = `<div class="empty empty--state">
      <div class="empty__title">热榜拉取失败</div>
      <div class="empty__hint">${esc(err.message)}</div>
      <button class="btn" id="retry">重试</button>
    </div>`;
    host.querySelector("#retry")?.addEventListener("click", (event) => load(true, root, ctx, event.target));
    return;
  } finally {
    setBusy(refresh, false);
  }

  if (!active) return;
  const ok = data.platforms.filter((p) => p.count > 0).length;
  const stale = data.platforms.filter((p) => p.stale).length;
  const failed = data.platforms.filter((p) => p.count === 0 && p.error);
  setStatus(
    `${ok} 平台正常${stale ? ` · ${stale} 个用的是旧缓存` : ""}${failed.length ? ` · ${failed.length} 个失败` : ""} · ${data.items.length} 条`,
    failed.length ? "warn" : stale ? "warn" : "ok"
  );

  renderFilters(root, ctx);
  renderList(root, ctx);
}

function renderFilters(root, ctx) {
  const host = root.querySelector("#filters");
  const chips = data.platforms
    .map((p) => {
      const active = filters.platforms.has(p.key) ? " active" : "";
      const tone = p.count === 0 && p.error ? "（失败）" : p.stale ? "（缓存）" : "";
      return `<button class="chip${active}" data-platform="${esc(p.key)}" title="${esc(p.error || "点击按平台筛选")}">
        ${esc(p.label)} ${p.count}${tone}
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

  const failures = data.platforms.filter((p) => p.count === 0 && p.error);
  const failureHtml = failures.length
    ? `<div class="banner" style="margin-top:10px;margin-bottom:0">
        <span>${failures.map((p) => `${esc(p.label)}：${esc(p.error)}`).join("；")}</span>
        <button class="btn btn--sm" id="retry-failed">重试</button>
      </div>`
    : "";

  host.innerHTML = `<div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:10px">${chips}</div>
    <div style="display:flex;flex-wrap:wrap;gap:6px">${cats}</div>${failureHtml}`;

  host.querySelector("#retry-failed")?.addEventListener("click", (event) =>
    load(true, root, ctx, event.target)
  );
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
    const allFailed = data.items.length === 0 && data.platforms.some((p) => p.error);
    host.innerHTML = allFailed
      ? `<div class="empty empty--state">
          <div class="empty__title">这次没有拿到任何热榜</div>
          <div class="empty__hint">各平台都返回了错误，多半是网络或平台限流；点「重试」再拉一次。</div>
          <button class="btn" id="empty-retry">重试</button>
        </div>`
      : `<div class="empty empty--state">
          <div class="empty__title">没有匹配的热点</div>
          <div class="empty__hint">换个关键词，或取消平台 / 垂类筛选。</div>
        </div>`;
    host.querySelector("#empty-retry")?.addEventListener("click", (event) =>
      load(true, root, ctx, event.target)
    );
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
          <button class="btn btn--sm btn--ghost" data-act="create">去创作</button>
        </span>
      </div>`
    )
    .join("")}</div>`;

  host.querySelectorAll(".row").forEach((row) => {
    const send = () => {
      const source = [row.dataset.title, row.dataset.url].filter(Boolean).join("\n");
      sessionStorage.setItem("stent.pending", JSON.stringify({ topic: row.dataset.title, source }));
      toast("已送入创作", "ok", 1600);
      ctx.navigate("create");
    };
    row.addEventListener("dblclick", send);
    row.querySelector('[data-act="create"]')?.addEventListener("click", (event) => {
      event.stopPropagation();
      send();
    });
    row.querySelector('[data-act="copy"]')?.addEventListener("click", (event) => {
      event.stopPropagation();
      copyText(row.dataset.title);
    });
    row.querySelector('[data-act="open"]')?.addEventListener("click", (event) => {
      event.stopPropagation();
      openExternal(row.dataset.url);
    });
  });
}
