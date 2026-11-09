// 数据分析：概览、趋势、平台分布、内容排行、作品登记与指标同步。
// 所有数字都来自 /api/analytics/*，页面自己不做口径计算。

import { api, watchJob } from "../api.js";
import { lineChart } from "../chart.js";
import {
  confirmDialog,
  esc,
  fmtCount,
  fmtInt,
  fmtRate,
  fmtTime,
  openExternal,
  platformColor,
  setBusy,
  setStatus,
  toast,
} from "../ui.js";

const METRICS = [
  ["views", "播放 / 曝光"],
  ["likes", "点赞"],
  ["comments", "评论"],
  ["collects", "收藏"],
  ["shares", "分享"],
  ["engagement", "互动总量"],
  ["posts", "发布篇数"],
];

const RANK_BY = [
  ["engagement", "互动综合分"],
  ["engagement_rate", "互动率"],
  ["views", "播放"],
  ["likes", "点赞"],
  ["comments", "评论"],
  ["collects", "收藏"],
];

const RATING_TONE = { 优秀: "ok", 良好: "ok", 平均: "idle", 偏低: "warn" };

const state = {
  days: 30,
  platform: "",
  metric: "views",
  cumulative: true,
  by: "engagement",
  overview: null,
  breakdown: [],
  series: [],
  rank: [],
  posts: [],
  importable: [],
  llmReady: false,
  attr: null,
  job: null,
};

let ctxRef = null;
let rootRef = null;
// 统计可能跑几秒；切页后回来的响应不许再动全局状态灯或弹提示
let active = false;

export async function mount(root, ctx) {
  ctxRef = ctx;
  rootRef = root;
  active = true;
  state.attr = null;

  const platforms = platformOptions(ctx.meta);

  root.innerHTML = `
    <div class="page-head">
      <div>
        <h1 class="page-title">数据分析</h1>
        <p class="page-desc">汇总本机记录的表现数据；指标需要先同步，或登记已发布的作品</p>
      </div>
      <div class="page-actions">
        <button class="btn" id="attribute">归因回流</button>
        <button class="btn btn--primary" id="sync">同步指标</button>
      </div>
    </div>

    <div class="card">
      <div class="toolbar">
        <span class="muted" style="font-size:12px">统计范围</span>
        <select id="platform" title="按平台筛选">
          <option value="">全部平台</option>
          ${platforms
            .map((p) => `<option value="${esc(p.key)}">${esc(p.label)}</option>`)
            .join("")}
        </select>
        <div class="seg" id="days">
          ${[7, 30, 90]
            .map(
              (days) =>
                `<button type="button" data-days="${days}"${days === state.days ? ' class="active"' : ""}>近 ${days} 天</button>`
            )
            .join("")}
        </div>
        <div class="spacer"></div>
        <span class="muted" id="scope-note" style="font-size:12px"></span>
      </div>
    </div>

    <div id="hint"></div>
    <div class="card hidden" id="job-card">
      <div class="job-line">
        <span class="spinner"></span>
        <span id="job-msg">处理中…</span>
        <div class="spacer" style="flex:1"></div>
        <button class="btn btn--sm btn--ghost" id="job-cancel">取消</button>
      </div>
      <div class="progress" style="margin-top:10px"><div class="progress__fill" id="job-fill"></div></div>
    </div>

    <div class="kpi-grid" id="kpis">
      ${[1, 2, 3, 4, 5].map(() => '<div class="kpi"><div class="skeleton__line"></div></div>').join("")}
    </div>

    <div class="card" id="rate-card"><div class="skeleton"><div class="skeleton__line" style="width:46%"></div></div></div>

    <div class="card chart-card">
      <div class="toolbar">
        <h2 class="card__title" style="margin:0">趋势</h2>
        <div class="spacer"></div>
        <select id="metric" title="指标">
          ${METRICS.map(
            ([key, label]) => `<option value="${key}"${key === state.metric ? " selected" : ""}>${label}</option>`
          ).join("")}
        </select>
        <div class="seg" id="cumulative">
          <button type="button" data-cumulative="1" class="active">累计曲线</button>
          <button type="button" data-cumulative="0">单日表现</button>
        </div>
      </div>
      <div id="chart" style="margin-top:10px"></div>
    </div>

    <div class="grid-side">
      <div class="card">
        <div class="toolbar" style="margin-bottom:6px">
          <h2 class="card__title" style="margin:0">内容排行</h2>
          <div class="spacer"></div>
          <select id="rank-by" title="排行依据">
            ${RANK_BY.map(
              ([key, label]) => `<option value="${key}"${key === state.by ? " selected" : ""}>${label}</option>`
            ).join("")}
          </select>
        </div>
        <div class="table-wrap" id="rank"><div class="loading">正在统计…</div></div>
      </div>

      <div>
        <div class="card">
          <h2 class="card__title">平台分布</h2>
          <div id="breakdown"><div class="loading">正在统计…</div></div>
        </div>

        <div class="card" id="attr-card">
          <h2 class="card__title">归因回流</h2>
          <p class="card__hint">分析表现最好与最差的内容，提炼可复用的结构经验并写入账号画像。</p>
          <div id="attr-result"><div class="muted" style="font-size:12.5px">还没有运行过归因。</div></div>
        </div>
      </div>
    </div>

    <div class="card">
      <div class="toolbar" style="margin-bottom:10px">
        <h2 class="card__title" style="margin:0">作品记录</h2>
        <div class="spacer"></div>
        <span class="muted" id="post-count"></span>
      </div>
      <div class="toolbar" style="margin-bottom:10px">
        <select id="import-platform" title="登记平台">
          <option value="">自动识别平台</option>
          ${state.importable
            .map((p) => `<option value="${esc(p.key)}">${esc(p.label)}</option>`)
            .join("")}
        </select>
        <input id="import-url" type="text" placeholder="粘贴作品链接，登记到数据分析…">
        <button class="btn" id="import-btn">登记作品</button>
      </div>
      <div class="table-wrap" id="posts"><div class="loading">正在读取记录…</div></div>
    </div>
  `;

  bind(root);
  await loadAll();
}

function platformOptions(meta) {
  const out = new Map();
  for (const row of meta?.publish_platforms || []) out.set(row.key, row.label);
  return [...out].map(([key, label]) => ({ key, label }));
}

function bind(root) {
  root.querySelector("#platform").addEventListener("change", (event) => {
    state.platform = event.target.value;
    loadAll();
  });
  root.querySelectorAll("#days button").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.days = Number(btn.dataset.days);
      root.querySelectorAll("#days button").forEach((other) => {
        other.classList.toggle("active", other === btn);
      });
      loadAll();
    });
  });
  root.querySelector("#metric").addEventListener("change", (event) => {
    state.metric = event.target.value;
    loadTrend();
  });
  root.querySelectorAll("#cumulative button").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.cumulative = btn.dataset.cumulative === "1";
      root.querySelectorAll("#cumulative button").forEach((other) => {
        other.classList.toggle("active", other === btn);
      });
      loadTrend();
    });
  });
  root.querySelector("#rank-by").addEventListener("change", (event) => {
    state.by = event.target.value;
    loadRank();
  });
  root.querySelector("#sync").addEventListener("click", () => syncMetrics());
  root.querySelector("#attribute").addEventListener("click", () => runAttribute());
  root.querySelector("#import-btn").addEventListener("click", () => registerPost());
  root.querySelector("#import-url").addEventListener("keydown", (event) => {
    if (event.key === "Enter") registerPost();
  });
  root.querySelector("#job-cancel")?.addEventListener("click", async () => {
    if (!state.job) return;
    await state.job.cancel();
  });
  ctxRef.onLeave(() => {
    active = false;
    // 离开页面只停轮询，服务端任务继续跑完（同步指标不该因为切页而中断）
    state.job?.stop?.();
    state.job = null;
  });
}

// ---------------------------------------------------------------------------
// 数据加载
// ---------------------------------------------------------------------------
async function loadAll() {
  setStatus("正在读取分析数据…", "busy");
  const days = state.days;
  const platform = state.platform ? `&platform=${encodeURIComponent(state.platform)}` : "";

  const [overview, trend, rank, posts] = await Promise.allSettled([
    api.get(`/api/analytics/overview?days=${days}${platform}`),
    api.get(
      `/api/analytics/trend?days=${days}&metric=${state.metric}&cumulative=${state.cumulative ? 1 : 0}${platform}`
    ),
    api.get(`/api/analytics/rank?days=${days}&by=${state.by}&limit=30`),
    api.get(`/api/analytics/posts?limit=200${platform}`),
  ]);

  const failures = [];
  if (overview.status === "fulfilled") {
    state.overview = overview.value.overview || null;
    state.breakdown = overview.value.breakdown || [];
    state.llmReady = Boolean(overview.value.llm_ready);
    state.importable = overview.value.importable_platforms || [];
    renderKpis();
    renderRate();
    renderBreakdown();
    renderImportOptions();
  } else {
    failures.push(`概览：${overview.reason?.message || "读取失败"}`);
  }

  if (trend.status === "fulfilled") {
    state.series = trend.value.series || [];
    renderTrend();
  } else {
    failures.push(`趋势：${trend.reason?.message || "读取失败"}`);
    rootRef.querySelector("#chart").innerHTML = "";
  }

  if (rank.status === "fulfilled") {
    state.rank = rank.value.items || [];
    renderRank();
  } else {
    failures.push(`排行：${rank.reason?.message || "读取失败"}`);
  }

  if (posts.status === "fulfilled") {
    state.posts = posts.value.items || [];
    renderPosts();
    renderHint();
  } else {
    failures.push(`作品记录：${posts.reason?.message || "读取失败"}`);
  }

  if (!active) return;
  const note = rootRef?.querySelector("#scope-note");
  if (note) {
    const label = platformOptions(ctxRef.meta).find((p) => p.key === state.platform)?.label;
    note.textContent = `${label || "全部平台"} · 近 ${state.days} 天`;
  }
  if (failures.length) {
    setStatus("部分数据读取失败", "warn");
    toast(failures.join("；"), "error", 6000);
  } else {
    const total = state.overview?.post_count ?? 0;
    setStatus(total ? `${total} 篇内容 · 近 ${state.days} 天` : "窗口内还没有数据", total ? "ok" : "idle");
  }
}

async function loadTrend() {
  const platform = state.platform ? `&platform=${encodeURIComponent(state.platform)}` : "";
  const host = rootRef?.querySelector("#chart");
  if (host) host.innerHTML = '<div class="loading">正在统计…</div>';
  try {
    const data = await api.get(
      `/api/analytics/trend?days=${state.days}&metric=${state.metric}&cumulative=${state.cumulative ? 1 : 0}${platform}`
    );
    state.series = data.series || [];
    renderTrend();
  } catch (err) {
    if (host) host.innerHTML = `<div class="empty empty--inset">${esc(err.message)}</div>`;
    toast(err.message, "error", 5000);
  }
}

async function loadRank() {
  const host = rootRef?.querySelector("#rank");
  if (host) host.innerHTML = '<div class="loading">正在统计…</div>';
  try {
    const data = await api.get(
      `/api/analytics/rank?days=${state.days}&by=${state.by}&limit=30`
    );
    state.rank = data.items || [];
    renderRank();
  } catch (err) {
    if (host) host.innerHTML = `<div class="empty empty--inset">${esc(err.message)}</div>`;
  }
}

async function loadPosts() {
  const platform = state.platform ? `&platform=${encodeURIComponent(state.platform)}` : "";
  try {
    const data = await api.get(`/api/analytics/posts?limit=200${platform}`);
    state.posts = data.items || [];
    renderPosts();
  } catch (err) {
    toast(err.message, "error", 5000);
  }
}

// ---------------------------------------------------------------------------
// 渲染
// ---------------------------------------------------------------------------
function renderHint() {
  const host = rootRef?.querySelector("#hint");
  if (!host) return;
  const total = state.posts.length;
  if (total === 0) {
    host.innerHTML = `<div class="banner banner--info">
      <span>还没有任何作品记录。要么在发布中心发一篇，要么把已经发过的作品链接登记进来。</span>
      <span style="display:flex;gap:8px">
        <button class="btn btn--sm" id="hint-register">登记已有作品</button>
        <button class="btn btn--sm btn--primary" id="hint-publish">去发布</button>
      </span>
    </div>`;
    host.querySelector("#hint-register")?.addEventListener("click", () => {
      rootRef.querySelector("#import-url")?.focus();
    });
    host.querySelector("#hint-publish")?.addEventListener("click", () => ctxRef.navigate("publish"));
    return;
  }
  const unsynced = state.posts.filter((p) => p.status === "published" && !p.metrics?.captured_at).length;
  host.innerHTML = unsynced
    ? `<div class="banner"><span>有 ${unsynced} 篇已发布作品还没有指标数据，点「同步指标」拉一次。</span></div>`
    : "";
}

function renderImportOptions() {
  const select = rootRef?.querySelector("#import-platform");
  if (!select) return;
  const current = select.value;
  select.innerHTML =
    '<option value="">自动识别平台</option>' +
    state.importable
      .map((p) => `<option value="${esc(p.key)}">${esc(p.label)}</option>`)
      .join("");
  if ([...select.options].some((option) => option.value === current)) select.value = current;
}

function renderKpis() {
  const host = rootRef?.querySelector("#kpis");
  const data = state.overview;
  if (!host) return;
  if (!data) {
    host.innerHTML = '<div class="empty">概览读取失败</div>';
    return;
  }
  const totals = data.totals || {};
  const cards = [
    ["总播放 / 曝光", fmtCount(totals.views), `${data.posts_with_metrics || 0} 篇有指标`],
    ["总点赞", fmtCount(totals.likes), ""],
    ["总评论", fmtCount(totals.comments), ""],
    ["总收藏", fmtCount(totals.collects), ""],
    ["发布篇数", String(data.post_count ?? 0), `窗口 ${data.days} 天`],
  ];
  host.innerHTML = cards
    .map(
      ([label, value, sub]) => `<div class="kpi">
        <div class="kpi__label">${label}</div>
        <div class="kpi__value">${esc(value)}</div>
        ${sub ? `<div class="kpi__sub">${esc(sub)}</div>` : ""}
      </div>`
    )
    .join("");
}

function renderRate() {
  const host = rootRef?.querySelector("#rate-card");
  const data = state.overview;
  if (!host || !data) return;
  const benchmark = data.benchmark || {};
  const rating = benchmark.rating;
  const ranges = (benchmark.engagement_ranges || []).map((v) => `${v}%`).join(" / ");
  const warnings = (data.warnings || []).map((w) => `<div class="kpi__sub">注意：${esc(w)}</div>`).join("");
  host.innerHTML = `
    <div class="toolbar" style="align-items:baseline">
      <span class="muted" style="font-size:12px">平均互动率</span>
      <strong style="font-size:20px">${esc(fmtRate(data.avg_engagement_rate))}</strong>
      <span class="muted" style="font-size:12px">加权 ${esc(fmtRate(data.aggregate_engagement_rate))}</span>
      ${rating ? `<span class="pill pill--${RATING_TONE[rating] || "idle"}">${esc(rating)}</span>` : ""}
      <div class="spacer"></div>
      <span class="muted" style="font-size:12px">
        ${benchmark.label ? `${esc(benchmark.label)}基准 ${esc(ranges)}` : "暂无平台基准"}
        ${data.latest_captured_at ? ` · 数据截至 ${esc(fmtTime(data.latest_captured_at))}` : ""}
      </span>
    </div>
    ${benchmark.note ? `<div class="kpi__sub">${esc(benchmark.note)}</div>` : ""}
    ${warnings}
  `;
}

function renderTrend() {
  const host = rootRef?.querySelector("#chart");
  if (!host) return;
  const series = state.series || [];
  const hasValue = series.some((row) => Number(row.value) > 0);
  if (!series.length || (!hasValue && state.metric !== "posts")) {
    host.innerHTML = `<div class="empty empty--state">
      <div class="empty__title">还没有可绘制的趋势</div>
      <div class="empty__hint">发布内容并同步指标后，这里会按天画出变化曲线。</div>
      <button class="btn btn--sm" id="chart-sync">去同步指标</button>
    </div>`;
    host.querySelector("#chart-sync")?.addEventListener("click", () => syncMetrics());
    return;
  }
  host.innerHTML = lineChart(series);
}

function renderBreakdown() {
  const host = rootRef?.querySelector("#breakdown");
  if (!host) return;
  const rows = state.breakdown || [];
  if (!rows.length) {
    host.innerHTML = '<div class="empty empty--inset">还没有可比较的平台数据</div>';
    return;
  }
  const max = Math.max(...rows.map((row) => Number(row.avg_score) || 0), 1);
  host.innerHTML = rows
    .map((row) => {
      const width = Math.max(4, ((Number(row.avg_score) || 0) / max) * 100);
      return `<div class="bar-row">
        <div class="bar-row__top">
          <div class="bar-row__label">
            <span class="row__dot" style="background:${platformColor(row.platform)}"></span>
            <span>${esc(row.label || row.platform)}</span>
          </div>
          <span class="bar-row__value">${row.post_count} 篇 · 互动率 ${esc(fmtRate(row.avg_engagement_rate))}</span>
        </div>
        <div class="bar"><div class="bar__fill" style="width:${width.toFixed(1)}%"></div></div>
      </div>`;
    })
    .join("");
}

function renderRank() {
  const host = rootRef?.querySelector("#rank");
  if (!host) return;
  const rows = state.rank || [];
  if (!rows.length) {
    host.innerHTML = `<div class="empty empty--state">
      <div class="empty__title">窗口内还没有可排行的内容</div>
      <div class="empty__hint">先同步指标，或把发布窗口调到更早的时间段。</div>
    </div>`;
    return;
  }
  host.innerHTML = `<table class="table">
    <thead><tr>
      <th style="width:36px">#</th><th style="width:78px">平台</th><th>标题</th>
      <th class="num">播放</th><th class="num">点赞</th><th class="num">评论</th>
      <th class="num">收藏</th><th class="num">互动率</th>
    </tr></thead>
    <tbody>${rows
      .map(
        (row) => `<tr>
          <td class="num">${row.rank}</td>
          <td><span style="display:inline-flex;align-items:center;gap:6px">
            <span class="row__dot" style="background:${platformColor(row.platform)}"></span>${esc(row.platform_label || row.platform || "")}
          </span></td>
          <td class="title-cell"><div title="${esc(row.title)}">${esc(row.title || "（无标题）")}</div></td>
          <td class="num">${fmtInt(row.views)}</td>
          <td class="num">${fmtInt(row.likes)}</td>
          <td class="num">${fmtInt(row.comments)}</td>
          <td class="num">${fmtInt(row.collects)}</td>
          <td class="num">${fmtRate(row.engagement_rate)}</td>
        </tr>`
      )
      .join("")}</tbody>
  </table>`;
}

function renderPosts() {
  const host = rootRef?.querySelector("#posts");
  if (!host) return;
  const count = rootRef.querySelector("#post-count");
  if (count) count.textContent = `共 ${state.posts.length} 条`;
  if (!state.posts.length) {
    host.innerHTML = '<div class="empty empty--inset">还没有作品记录</div>';
    return;
  }
  host.innerHTML = `<table class="table">
    <thead><tr>
      <th style="width:78px">平台</th><th>标题 / 链接</th><th style="width:82px">状态</th>
      <th class="num">播放</th><th class="num">点赞</th><th class="num">收藏</th>
      <th style="width:110px">最近同步</th><th style="width:120px"></th>
    </tr></thead>
    <tbody>${state.posts
      .map((row) => {
        const tone =
          row.status === "published"
            ? "ok"
            : row.status === "failed"
              ? "error"
              : row.status === "draft_filled"
                ? "warn"
                : "busy";
        const title = row.title || row.url || "（无标题）";
        return `<tr data-id="${row.id}">
          <td><span style="display:inline-flex;align-items:center;gap:6px">
            <span class="row__dot" style="background:${platformColor(row.platform)}"></span>${esc(row.platform_label)}
          </span></td>
          <td class="title-cell"><div title="${esc(title)}">${esc(title)}</div>
            ${row.error ? `<div class="kpi__sub" style="color:var(--danger)">${esc(row.error)}</div>` : ""}
          </td>
          <td><span class="pill pill--${tone}">${esc(row.status_label)}</span></td>
          <td class="num">${fmtInt(row.metrics?.views)}</td>
          <td class="num">${fmtInt(row.metrics?.likes)}</td>
          <td class="num">${fmtInt(row.metrics?.collects)}</td>
          <td class="muted" style="font-size:12px">${row.metrics?.captured_at ? esc(fmtTime(row.metrics.captured_at)) : "未同步"}</td>
          <td class="actions">
            ${row.url ? '<button class="btn btn--sm btn--ghost" data-act="open">打开</button>' : ""}
            <button class="btn btn--sm btn--ghost" data-act="delete">删除</button>
          </td>
        </tr>`;
      })
      .join("")}</tbody>
  </table>`;

  host.querySelectorAll("tr[data-id]").forEach((row) => {
    const id = Number(row.dataset.id);
    const item = state.posts.find((entry) => entry.id === id);
    if (!item) return;
    row.querySelector('[data-act="open"]')?.addEventListener("click", () => openExternal(item.url));
    row.querySelector('[data-act="delete"]')?.addEventListener("click", () => removePost(item));
  });
}

function renderAttr(result) {
  const host = rootRef?.querySelector("#attr-result");
  if (!host) return;
  if (!result) {
    host.innerHTML = '<div class="muted" style="font-size:12.5px">还没有运行过归因。</div>';
    return;
  }
  const experience = (result.structure_experience || [])
    .map(
      (item) => `<div class="check check--info"><div>
        <div class="check__head">${esc(item.name || "结构经验")}</div>
        <div class="check__body">
          ${item.structure ? `${esc(item.structure)}<br>` : ""}
          ${item.evidence ? `依据：${esc(item.evidence)}<br>` : ""}
          ${item.transferable_when ? `适用：${esc(item.transferable_when)}` : ""}
        </div></div></div>`
    )
    .join("");
  const list = (title, items) =>
    items && items.length
      ? `<div style="margin-top:8px"><div class="kpi__sub" style="color:var(--text)">${esc(title)}</div>
         <ul class="attr-list">${items.map((item) => `<li>${esc(item)}</li>`).join("")}</ul></div>`
      : "";
  host.innerHTML = `
    ${result.message ? `<div class="banner banner--${result.ok ? "ok" : "info"}" style="margin-bottom:10px">${esc(result.message)}</div>` : ""}
    ${result.summary ? `<div class="check check--info"><div><div class="check__body" style="white-space:pre-wrap">${esc(result.summary)}</div></div></div>` : ""}
    ${experience}
    ${list("表现好的原因", result.top_reasons)}
    ${list("表现差的原因", result.bottom_reasons)}
    ${list("需要避免", result.avoid)}
    ${list("下一步动作", result.next_actions)}
    ${!result.ok && !result.summary && !experience ? `<div class="muted" style="font-size:12.5px">${esc(result.message || "本次没有产出结论")}</div>` : ""}
  `;
}

// ---------------------------------------------------------------------------
// 动作
// ---------------------------------------------------------------------------
function showJob(label) {
  const card = rootRef?.querySelector("#job-card");
  if (!card) return;
  card.classList.remove("hidden");
  card.querySelector("#job-msg").textContent = label;
  card.querySelector("#job-fill").style.width = "0%";
}

function updateJob(job) {
  const card = rootRef?.querySelector("#job-card");
  if (!card || !job) return;
  card.querySelector("#job-msg").textContent = job.message || "处理中…";
  const fill = card.querySelector("#job-fill");
  fill.style.width = job.percent >= 0 ? `${job.percent}%` : "35%";
  fill.style.opacity = job.percent >= 0 ? "1" : ".55";
}

function hideJob() {
  rootRef?.querySelector("#job-card")?.classList.add("hidden");
}

async function trackJob(jobId, onDone) {
  showJob("正在启动任务…");
  const promise = watchJob(jobId, { onTick: updateJob });
  state.job = promise;
  try {
    const job = await promise;
    hideJob();
    if (!active) return;
    if (job.status === "cancelled") return toast("已取消", "warn", 2000);
    if (job.status === "error") {
      toast(job.error || "任务失败", "error", 6000);
      return;
    }
    if (job.status === "done") await onDone(job.result || {}, job);
  } catch (err) {
    hideJob();
    toast(`任务状态读取失败：${err.message}`, "error", 6000);
  } finally {
    state.job = null;
  }
}

async function syncMetrics() {
  const button = rootRef?.querySelector("#sync");
  if (state.job) return toast("已有任务在进行", "warn", 1800);
  setBusy(button, true, "同步中…");
  setStatus("正在同步指标…", "busy");
  try {
    const data = await api.post("/api/analytics/sync", { force: false });
    await trackJob(data.job.id, async (result) => {
      const synced = Number(result.synced) || 0;
      const failed = Number(result.failed) || 0;
      setStatus(`同步完成 · 成功 ${synced} · 失败 ${failed}`, failed ? "warn" : "ok");
      toast(`同步完成：成功 ${synced} 条${failed ? `，失败 ${failed} 条` : ""}`, failed ? "warn" : "ok", 4000);
      const errors = result.errors || [];
      if (errors.length) {
        toast(`首条失败原因：${errors[0].error || "未知"}`, "warn", 6000);
      }
      await loadAll();
    });
  } catch (err) {
    toast(err.message, "error", 6000);
  } finally {
    setBusy(button, false);
  }
}

async function runAttribute() {
  const button = rootRef?.querySelector("#attribute");
  if (state.job) return toast("已有任务在进行", "warn", 1800);
  if (!state.llmReady) {
    const go = await confirmDialog({
      title: "还没有配置模型",
      message: "归因回流需要模型来解读数据。可以先看统计数据，或现在去设置里填 API。",
      confirmText: "去设置",
    });
    if (go) ctxRef.navigate("settings");
    return;
  }
  setBusy(button, true, "分析中…");
  setStatus("正在做归因分析…", "busy");
  try {
    const data = await api.post("/api/analytics/attribute", { days: state.days });
    await trackJob(data.job.id, async (result) => {
      state.attr = result;
      renderAttr(result);
      setStatus(result.ok ? "归因完成" : "归因未完成", result.ok ? "ok" : "warn");
      toast(result.message || (result.ok ? "归因完成" : "归因未完成"), result.ok ? "ok" : "warn", 5000);
    });
  } catch (err) {
    toast(err.message, "error", 6000);
  } finally {
    setBusy(button, false);
  }
}

async function registerPost() {
  const input = rootRef?.querySelector("#import-url");
  const platform = rootRef?.querySelector("#import-platform")?.value || "";
  const url = input?.value.trim() || "";
  if (!url) {
    input?.focus();
    return toast("请先粘贴作品链接", "warn", 2000);
  }
  const button = rootRef.querySelector("#import-btn");
  setBusy(button, true, "登记中…");
  try {
    const data = await api.post("/api/analytics/posts", { url, platform });
    input.value = "";
    toast(data.message || "已登记", data.duplicated ? "warn" : "ok", 3000);
    await loadPosts();
    renderHint();
    if (data.job?.id) {
      await trackJob(data.job.id, async (result) => {
        if (result?.ok) toast("指标已更新", "ok", 2000);
        else toast(result?.message || "指标暂未取到，可稍后再同步", "warn", 4000);
        await loadPosts();
        await loadAll();
      });
    }
  } catch (err) {
    toast(err.message, "error", 6000);
  } finally {
    setBusy(button, false);
  }
}

async function removePost(item) {
  const ok = await confirmDialog({
    title: "删除作品记录",
    message: `删除「${item.title || item.url}」及其全部指标快照？删除后无法恢复。`,
    confirmText: "删除",
    danger: true,
  });
  if (!ok) return;
  try {
    await api.del(`/api/analytics/posts?id=${item.id}`);
    toast("已删除", "ok", 1600);
    await loadAll();
  } catch (err) {
    toast(err.message, "error", 5000);
  }
}
