// 草稿箱：本地草稿的检索、续写、送发布与删除。
// 草稿就是 contents 表里 status='draft' 的记录，接口见 stent/api/handlers.py。

import { api } from "../api.js";
import {
  confirmDialog,
  copyText,
  debounce,
  esc,
  fmtTime,
  platformColor,
  setBusy,
  setStatus,
  toast,
} from "../ui.js";

const state = { items: [], total: 0, keyword: "", platform: "", loading: false };
let ctxRef = null;
let rootRef = null;

export async function mount(root, ctx) {
  ctxRef = ctx;
  rootRef = root;
  state.keyword = "";
  state.platform = "";

  const platforms = platformOptions(ctx.meta);

  root.innerHTML = `
    <div class="page-head">
      <div>
        <h1 class="page-title">草稿箱</h1>
        <p class="page-desc">所有还没发布的内容都存在本机；载入后可以继续生成或直接送发布</p>
      </div>
      <div class="page-actions">
        <button class="btn" id="refresh">刷新</button>
        <button class="btn btn--primary" id="new">新建内容</button>
      </div>
    </div>

    <div class="card">
      <div class="toolbar">
        <input id="q" type="text" placeholder="搜索主题、标题或正文…">
        <div class="seg" id="platform-filter">
          <button type="button" data-platform="" class="active">全部平台</button>
          ${platforms
            .map((p) => `<button type="button" data-platform="${esc(p.key)}">${esc(p.label)}</button>`)
            .join("")}
        </div>
        <div class="spacer"></div>
        <span class="muted" id="count"></span>
      </div>
    </div>

    <div class="card" id="list-card">
      <div class="loading">正在读取草稿…</div>
    </div>
  `;

  root.querySelector("#new").addEventListener("click", () => ctx.navigate("create"));
  root.querySelector("#refresh").addEventListener("click", () => load(true));
  root.querySelector("#q").addEventListener(
    "input",
    debounce((event) => {
      state.keyword = event.target.value.trim();
      renderList();
    }, 160)
  );
  root.querySelectorAll("#platform-filter button").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.platform = btn.dataset.platform;
      root.querySelectorAll("#platform-filter button").forEach((other) => {
        other.classList.toggle("active", other === btn);
      });
      renderList();
    });
  });

  await load(false);
}

function platformOptions(meta) {
  const seen = new Map();
  for (const row of meta?.publish_platforms || []) {
    seen.set(row.key, row.label);
  }
  for (const row of meta?.creation_platforms || []) {
    if (!seen.has(row.key)) seen.set(row.key, row.label);
  }
  return [...seen].map(([key, label]) => ({ key, label }));
}

async function load(force) {
  const host = rootRef?.querySelector("#list-card");
  if (!host || state.loading) return;
  state.loading = true;
  if (force) setBusy(rootRef.querySelector("#refresh"), true, "刷新中…");
  else host.innerHTML = skeletonRows();
  setStatus("正在读取草稿…", "busy");
  try {
    state.items = (await api.get("/api/drafts?limit=500")).items || [];
    state.total = state.items.length;
    setStatus(state.total ? `${state.total} 篇草稿` : "还没有草稿", "idle");
    renderList();
  } catch (err) {
    host.innerHTML = `<div class="empty empty--state">
      <div class="empty__title">草稿读取失败</div>
      <div class="empty__hint">${esc(err.message)}</div>
      <button class="btn" id="retry">重试</button>
    </div>`;
    host.querySelector("#retry")?.addEventListener("click", () => load(true));
    setStatus("草稿读取失败", "error");
  } finally {
    state.loading = false;
    setBusy(rootRef.querySelector("#refresh"), false);
  }
}

function visible() {
  const keyword = state.keyword.toLowerCase();
  return state.items.filter((item) => {
    if (state.platform && item.platform !== state.platform) return false;
    if (!keyword) return true;
    return `${item.topic} ${item.title} ${item.excerpt} ${(item.tags || []).join(" ")}`
      .toLowerCase()
      .includes(keyword);
  });
}

function skeletonRows() {
  return `<div class="skeleton">
    ${[68, 82, 54, 74].map((width) => `<div class="skeleton__line" style="width:${width}%"></div>`).join("")}
  </div>`;
}

function renderList() {
  const host = rootRef?.querySelector("#list-card");
  if (!host) return;
  const items = visible();
  const count = rootRef.querySelector("#count");
  if (count) {
    count.textContent = state.keyword || state.platform
      ? `筛选出 ${items.length} / ${state.total} 篇`
      : `共 ${state.total} 篇`;
  }

  if (!state.total) {
    host.innerHTML = `<div class="empty empty--state">
      <div class="empty__title">还没有草稿</div>
      <div class="empty__hint">在创作页点「保存草稿」，或在发布页把写好的内容存下来，之后会出现在这里。</div>
      <button class="btn btn--primary" id="go-create">去创作</button>
    </div>`;
    host.querySelector("#go-create")?.addEventListener("click", () => ctxRef.navigate("create"));
    return;
  }

  if (!items.length) {
    host.innerHTML = `<div class="empty empty--state">
      <div class="empty__title">没有匹配的草稿</div>
      <div class="empty__hint">换个关键词，或把平台筛选切回「全部平台」。</div>
    </div>`;
    return;
  }

  host.innerHTML = `<div class="list">${items
    .map(
      (item) => `
      <div class="row draft-row" data-id="${item.id}">
        <span class="row__dot" style="background:${platformColor(item.platform)}"></span>
        <div class="draft-row__main">
          <div class="draft-row__title">${esc(item.title || item.topic || "（未命名草稿）")}</div>
          <div class="draft-row__meta">
            ${esc(item.platform_label || "未选平台")} · ${item.chars} 字 · 更新于 ${esc(fmtTime(item.updated_at))}
            ${item.excerpt && item.title && item.title !== item.topic ? ` · ${esc(item.excerpt)}` : ""}
          </div>
        </div>
        <span class="row__actions">
          <button class="btn btn--sm" data-act="edit">继续创作</button>
          <button class="btn btn--sm" data-act="publish">送发布</button>
          <button class="btn btn--sm btn--ghost" data-act="copy">复制正文</button>
          <button class="btn btn--sm btn--ghost" data-act="delete">删除</button>
        </span>
      </div>`
    )
    .join("")}</div>`;

  host.querySelectorAll(".draft-row").forEach((row) => {
    const id = Number(row.dataset.id);
    const item = items.find((entry) => entry.id === id);
    if (!item) return;
    row.querySelector('[data-act="edit"]').addEventListener("click", () => openInCreate(item));
    row.querySelector('[data-act="publish"]').addEventListener("click", () => openInPublish(item));
    row.querySelector('[data-act="copy"]').addEventListener("click", () => {
      if (!item.body.trim()) return toast("这篇草稿还没有正文", "warn", 1600);
      copyText(item.body);
    });
    row.querySelector('[data-act="delete"]').addEventListener("click", () => remove(item));
  });
}

function openInCreate(item) {
  sessionStorage.setItem("stent.draft", JSON.stringify(item));
  ctxRef.navigate("create");
}

function openInPublish(item) {
  if (!item.body.trim()) return toast("这篇草稿还没有正文，先补上再发布", "warn", 2200);
  sessionStorage.setItem(
    "stent.publishDraft",
    JSON.stringify({
      content_id: item.id,
      platform: item.platform || "",
      title: item.title || (item.titles || [])[0] || "",
      titles: item.titles || [],
      body: item.body,
      tags: item.tags || [],
      summary: item.summary || "",
      media: item.media || [],
    })
  );
  ctxRef.navigate("publish");
}

async function remove(item) {
  const ok = await confirmDialog({
    title: "删除草稿",
    message: `确定删除「${item.title || item.topic || "未命名草稿"}」吗？删除后无法恢复。`,
    confirmText: "删除",
    danger: true,
  });
  if (!ok) return;
  try {
    await api.del(`/api/drafts?id=${item.id}`);
    state.items = state.items.filter((entry) => entry.id !== item.id);
    state.total = state.items.length;
    renderList();
    ctxRef?.refreshDrafts?.();
    toast("草稿已删除", "ok", 1600);
  } catch (err) {
    toast(err.message, "error", 5000);
  }
}
