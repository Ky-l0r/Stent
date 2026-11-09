// 发布中心：选平台 → 登录检测 → 内容与媒体 → 检查预览 → 人工确认 → 填写/提交。
//
// 风控（与 Qt 版、服务层一致）：
//   1. 必须勾选「我已核对」才能执行，接口再校验一次 confirmed=true；
//   2. 默认动作是 dry_run：只把表单填好，浏览器保持打开，由用户在页面里点发布；
//   3. 「直接发布」收进二次确认弹窗，并需要额外勾选风险知情。

import { api, shell, watchJob } from "../api.js";
import {
  checksHtml,
  confirmDialog,
  copyText,
  esc,
  fmtTime,
  openExternal,
  pill,
  promptDialog,
  setBusy,
  setStatus,
  toast,
} from "../ui.js";

const state = {
  platform: "",
  contentId: 0,
  media: [],
  prepared: null,
  env: null,
  history: [],
  job: null,
  confirmed: false,
  result: null,
};

let rootRef = null;
let ctxRef = null;
// 发布可能跑几分钟；切页后回来的进度不许再写旧页面或弹提示
let active = false;

const STATUS_TONE = {
  pending: "busy",
  draft_filled: "warn",
  published: "ok",
  failed: "error",
  cancelled: "idle",
};

export async function mount(root, ctx) {
  rootRef = root;
  ctxRef = ctx;
  active = true;
  state.prepared = null;
  state.result = null;
  state.confirmed = false;

  const platforms = ctx.meta?.publish_platforms || [];
  if (!platforms.length) {
    root.innerHTML = '<div class="empty">未取到发布平台清单，请确认接口服务正常</div>';
    return;
  }

  const draft = readDraft();
  state.contentId = Number(draft.content_id) || 0;
  state.media = Array.isArray(draft.media) ? draft.media.slice() : [];
  state.platform =
    draft.platform && platforms.some((p) => p.key === draft.platform)
      ? draft.platform
      : (platforms.find((p) => p.available) || platforms[0]).key;

  root.innerHTML = `
    <div class="page-head">
      <div>
        <h1 class="page-title">发布中心</h1>
        <p class="page-desc">检查通过后由你确认：默认只填好表单，最后一步在浏览器里点</p>
      </div>
      <div class="page-actions">
        <button class="btn" id="save-draft">保存草稿</button>
        <button class="btn btn--primary" id="prepare">检查并预览</button>
      </div>
    </div>

    <div id="env-slot"></div>

    <div class="grid-side">
      <div class="card">
        <label class="field"><span>目标平台</span>
          <select id="platform">
            ${platforms
              .map(
                (p) =>
                  `<option value="${esc(p.key)}"${p.key === state.platform ? " selected" : ""}>${esc(p.label)}${p.available ? "" : "（适配器不可用）"}</option>`
              )
              .join("")}
          </select>
        </label>
        <p class="muted" id="limit-hint" style="margin:-4px 0 10px;font-size:11.5px"></p>

        <div class="toolbar" id="login-row" style="margin-bottom:12px">
          <span id="login-pill" class="pill">未检测</span>
          <span class="muted" id="login-msg" style="font-size:12px">发布前需要先登录对应平台</span>
          <div class="spacer"></div>
          <button class="btn btn--sm" id="login-check">检测登录</button>
          <button class="btn btn--sm" id="login-btn">去登录</button>
        </div>

        <label class="field"><span>标题</span>
          <input id="title" type="text" value="${esc(draft.title || "")}" placeholder="平台标题，留空则用正文首句">
        </label>
        <label class="field"><span>正文</span>
          <textarea id="body" rows="10" placeholder="可粘贴，或在创作页点「送发布中心」带过来">${esc(draft.body || "")}</textarea>
        </label>
        <div class="field-row">
          <label class="field"><span>简介 / 摘要</span><input id="summary" type="text" value="${esc(draft.summary || "")}"></label>
          <label class="field"><span>标签（空格分隔）</span><input id="tags" type="text" value="${esc((draft.tags || []).join(" "))}"></label>
        </div>
        <div class="toolbar" style="margin-top:-6px">
          <span class="muted mono" id="media-list" style="font-size:11.5px">未选择媒体</span>
          <div class="spacer"></div>
          <button class="btn btn--sm" id="pick">选择媒体</button>
          <button class="btn btn--sm btn--ghost" id="clear-media">清空</button>
        </div>
      </div>

      <div>
        <div class="card" id="result-card">
          <h2 class="card__title">检查与预览</h2>
          <div class="empty empty--inset">点右上角「检查并预览」看检查结果与发布后的样子</div>
        </div>

        <div class="card hidden sticky-actions" id="action-card">
          <label class="checkbox" style="margin-top:0">
            <input type="checkbox" id="confirm">
            <span>我已核对平台、标题与正文，确认由我发起这次发布</span>
          </label>
          <div class="toolbar" style="margin-top:12px">
            <button class="btn btn--primary" id="do-fill" disabled>填写表单（不提交）</button>
            <button class="btn" id="do-publish" disabled>直接发布…</button>
          </div>
          <p class="card__hint" style="margin:8px 0 0">
            默认只填写，不点提交；浏览器会保持打开，你可以在里面核对后再点发布。
          </p>
        </div>

        <div class="card hidden" id="job-card">
          <div class="job-line">
            <span class="spinner"></span>
            <span id="job-msg">处理中…</span>
            <div class="spacer" style="flex:1"></div>
            <button class="btn btn--sm btn--ghost" id="job-cancel">取消</button>
          </div>
          <div class="progress" style="margin-top:10px"><div class="progress__fill" id="job-fill"></div></div>
          <p class="card__hint" id="job-hint" style="margin:8px 0 0"></p>
        </div>

        <div class="card" id="history-card">
          <div class="toolbar" style="margin-bottom:8px">
            <h2 class="card__title" style="margin:0">发布记录</h2>
            <div class="spacer"></div>
            <button class="btn btn--sm btn--ghost" id="refresh-history">刷新</button>
          </div>
          <div id="history"><div class="loading">正在读取…</div></div>
        </div>
      </div>
    </div>
  `;

  bind(root, ctx);
  renderMedia();
  updateLimitHint(root, ctx);
  updateLoginHint();
  await Promise.all([loadEnv(), loadHistory()]);
}

function readDraft() {
  const raw = sessionStorage.getItem("stent.publishDraft");
  if (!raw) return {};
  sessionStorage.removeItem("stent.publishDraft");
  try {
    return JSON.parse(raw) || {};
  } catch {
    return {};
  }
}

function bind(root, ctx) {
  root.querySelector("#platform").addEventListener("change", () => {
    state.platform = root.querySelector("#platform").value;
    state.prepared = null;
    state.confirmed = false;
    root.querySelector("#confirm").checked = false;
    syncActions();
    updateLimitHint(root, ctx);
    updateLoginHint();
    renderResult(null);
  });

  root.querySelector("#prepare").addEventListener("click", (event) => prepare(event.target));
  root.querySelector("#save-draft").addEventListener("click", (event) => saveDraft(event.target));
  root.querySelector("#pick").addEventListener("click", pickMedia);
  root.querySelector("#clear-media").addEventListener("click", () => {
    state.media = [];
    renderMedia();
    markDirty();
  });

  root.querySelector("#login-check").addEventListener("click", (event) => login("check", event.target));
  root.querySelector("#login-btn").addEventListener("click", (event) => login("login", event.target));

  root.querySelector("#refresh-history").addEventListener("click", () => loadHistory(true));
  root.querySelector("#history").addEventListener("click", onHistoryClick);

  root.querySelector("#confirm").addEventListener("change", (event) => {
    state.confirmed = event.target.checked;
    syncActions();
  });
  root.querySelector("#do-fill").addEventListener("click", () => startPublish(true));
  root.querySelector("#do-publish").addEventListener("click", () => startPublish(false));
  root.querySelector("#job-cancel").addEventListener("click", async () => {
    if (!state.job) return;
    await state.job.cancel();
  });

  // 任何内容改动都让上一次检查失效：否则会拿旧检查结果去发新内容
  ["title", "body", "summary", "tags"].forEach((id) => {
    root.querySelector(`#${id}`).addEventListener("input", markDirty);
  });

  ctx.onLeave(() => {
    active = false;
    // 只停轮询：发布可能正停在浏览器里等用户确认，不能因为切页就取消
    state.job?.stop?.();
    state.job = null;
  });
}

function markDirty() {
  if (!state.prepared) return;
  state.prepared = null;
  state.confirmed = false;
  const check = rootRef.querySelector("#confirm");
  if (check) check.checked = false;
  syncActions();
  const host = rootRef.querySelector("#result-card");
  if (host) {
    host.querySelector("#stale-note")?.remove();
    host.insertAdjacentHTML(
      "afterbegin",
      '<div class="banner" id="stale-note" style="margin-bottom:10px">内容已修改，请重新「检查并预览」后再发布。</div>'
    );
  }
}

function readForm() {
  return {
    content_id: state.contentId || undefined,
    platform: rootRef.querySelector("#platform").value,
    title: rootRef.querySelector("#title").value.trim(),
    body: rootRef.querySelector("#body").value.trim(),
    tags: rootRef
      .querySelector("#tags")
      .value.split(/[\s,，]+/)
      .filter(Boolean),
    summary: rootRef.querySelector("#summary").value.trim(),
    media: state.media,
  };
}

function syncActions() {
  const card = rootRef?.querySelector("#action-card");
  if (!card) return;
  const ready = Boolean(state.prepared?.passed && state.prepared?.supported);
  const actionable = ready && state.confirmed;
  card.querySelector("#do-fill").disabled = !actionable;
  card.querySelector("#do-publish").disabled = !actionable;
  card.querySelector("#do-fill").title = ready ? "" : "先通过检查并勾选确认";
  card.querySelector("#do-publish").title = ready ? "" : "先通过检查并勾选确认";
}

// ---------------------------------------------------------------------------
// 环境 / 登录
// ---------------------------------------------------------------------------
async function loadEnv() {
  try {
    state.env = await api.get("/api/publish/env");
  } catch (err) {
    state.env = { ready: false, title: "无法读取发布环境", detail: err.message };
  }
  renderEnv();
  updateLoginHint();
}

function renderEnv() {
  const host = rootRef?.querySelector("#env-slot");
  if (!host) return;
  const env = state.env;
  if (!env) {
    host.innerHTML = "";
    return;
  }
  if (env.ready) {
    // 借系统浏览器跑的时候仍然可用，但给一条「想用内置内核」的退路
    if (env.code === "system_browser") {
      host.innerHTML = `<div class="banner banner--info">
        <span>${esc(env.detail)}</span>
        <button class="btn btn--sm" id="install-env">下载内置内核备用</button>
      </div>`;
      host.querySelector("#install-env").addEventListener("click", (event) =>
        installEnv(event.target, true)
      );
    } else {
      host.innerHTML = "";
    }
    return;
  }
  host.innerHTML = `<div class="banner${env.code === "no_chromium" ? "" : " banner--error"}">
    <span>${esc(env.title)}：${esc(env.detail)}${env.size_hint ? `（需下载${esc(env.size_hint)}）` : ""}</span>
    <button class="btn btn--sm" id="install-env">一键配置</button>
  </div>`;
  host.querySelector("#install-env").addEventListener("click", (event) => installEnv(event.target));
}

async function installEnv(button, force = false) {
  setBusy(button, true, "配置中…");
  try {
    const data = await api.post("/api/publish/install", force ? { force: true } : {});
    showJob("正在配置发布环境…");
    const promise = watchJob(data.job.id, { onTick: updateJob });
    state.job = promise;
    const job = await promise;
    hideJob();
    if (!active || job.status === "detached") return;
    if (job.status === "error") toast(job.error, "error", 8000);
    else if (job.status === "cancelled") toast("已取消配置", "warn", 3000);
    else toast(job.message || "配置完成", "ok", 5000);
    await loadEnv();
  } catch (err) {
    toast(err.message, "error", 6000);
  } finally {
    state.job = null;
    setBusy(button, false);
  }
}

function updateLimitHint(root, ctx) {
  const host = root.querySelector("#limit-hint");
  if (!host) return;
  const row = (ctx.meta?.publish_platforms || []).find(
    (p) => p.key === root.querySelector("#platform").value
  );
  if (!row) {
    host.textContent = "";
    return;
  }
  const limits = row.limits || {};
  const parts = [];
  if (limits.title_max) parts.push(`标题 ≤ ${limits.title_max} 字`);
  if (limits.body_max) parts.push(`正文 ≤ ${limits.body_max} 字`);
  if (limits.tags_max) parts.push(`标签 ≤ ${limits.tags_max} 个`);
  if (row.needs_media) parts.push("需要媒体文件");
  host.textContent = parts.length ? `该平台限制：${parts.join(" · ")}` : "";
}

function updateLoginHint() {
  const pillEl = rootRef?.querySelector("#login-pill");
  const msg = rootRef?.querySelector("#login-msg");
  if (!pillEl || !msg) return;
  const envReady = Boolean(state.env?.ready);
  const platform = state.platform;
  const row = (state.env?.platforms || []).find((p) => p.key === platform);
  const available = row ? row.available : true;
  if (!envReady) {
    pillEl.className = "pill pill--warn";
    pillEl.textContent = "环境未就绪";
    msg.textContent = "先完成上方配置；装一个 Edge / Chrome 也能直接复用，不必下载内核";
  } else if (!available) {
    pillEl.className = "pill pill--warn";
    pillEl.textContent = "适配器不可用";
    msg.textContent = "该平台暂不支持自动填写，可检查后复制文案人工发布";
  } else {
    pillEl.className = "pill";
    pillEl.textContent = "未检测";
    msg.textContent = "建议先检测登录状态；没登录会停在登录页";
  }
  const disabled = !envReady || !available;
  rootRef.querySelector("#login-check").disabled = disabled;
  rootRef.querySelector("#login-btn").disabled = disabled;
}

function setLoginPill(ok, text) {
  const pillEl = rootRef?.querySelector("#login-pill");
  if (!pillEl) return;
  pillEl.className = `pill pill--${ok ? "ok" : "warn"}`;
  pillEl.textContent = text;
}

async function login(action, button) {
  setBusy(button, true, action === "login" ? "等待登录…" : "检测中…");
  try {
    const data = await api.post("/api/publish/login", { platform: state.platform, action });
    showJob(action === "login" ? "已打开登录窗口，请在浏览器中完成登录…" : "正在检测登录状态…");
    const promise = watchJob(data.job.id, { onTick: updateJob });
    state.job = promise;
    const job = await promise;
    hideJob();
    if (!active || job.status === "detached") return;
    if (job.status === "error") {
      setLoginPill(false, "检测失败");
      toast(job.error, "error", 6000);
    } else if (job.status === "done") {
      const result = job.result || {};
      setLoginPill(Boolean(result.ok), result.ok ? "已登录" : "未登录");
      toast(result.message || (result.ok ? "登录成功" : "尚未登录"), result.ok ? "ok" : "warn", 5000);
    } else if (job.status === "cancelled") {
      toast("已取消", "warn", 3000);
    }
  } catch (err) {
    toast(err.message, "error", 6000);
  } finally {
    state.job = null;
    setBusy(button, false);
  }
}

// ---------------------------------------------------------------------------
// 媒体
// ---------------------------------------------------------------------------
async function pickMedia() {
  if (!shell.available()) {
    return toast("浏览器模式拿不到真实文件路径，请用桌面窗口选择媒体", "warn", 5000);
  }
  try {
    const picked = await shell.call("pick_files", "media");
    if (!Array.isArray(picked)) return;
    if (!picked.length) return;
    const merged = [...state.media];
    for (const path of picked) {
      if (!merged.includes(path)) merged.push(path);
    }
    state.media = merged;
    renderMedia();
    markDirty();
    toast(`已选择 ${picked.length} 个文件`, "ok", 1600);
  } catch (err) {
    toast(`选择文件失败：${err.message}`, "error", 5000);
  }
}

function renderMedia() {
  const host = rootRef?.querySelector("#media-list");
  if (!host) return;
  host.textContent = state.media.length
    ? `已选 ${state.media.length} 个：${state.media.map((p) => p.split(/[\\/]/).pop()).join("、")}`
    : "未选择媒体";
}

// ---------------------------------------------------------------------------
// 检查 / 预览
// ---------------------------------------------------------------------------
async function prepare(button) {
  const form = readForm();
  if (!form.body) return toast("正文为空，先写点内容", "warn", 2200);
  setBusy(button, true, "检查中…");
  setStatus("正在执行发布前检查…", "busy");
  try {
    const data = await api.post("/api/publish/prepare", form);
    state.prepared = {
      ...data,
      passed: Boolean(data.checks?.passed),
      supported: Boolean(data.supported),
    };
    rootRef.querySelector("#stale-note")?.remove();
    renderResult(data);
    syncActions();
    const passed = data.checks?.passed;
    setStatus(passed ? `检查通过 · ${data.checks.score} 分` : "检查未通过", passed ? "ok" : "error");
  } catch (err) {
    setStatus("检查失败", "error");
    toast(err.message, "error", 6000);
  } finally {
    setBusy(button, false);
  }
}

function renderResult(data) {
  const host = rootRef?.querySelector("#result-card");
  if (!host) return;
  const actionCard = rootRef.querySelector("#action-card");
  if (!data) {
    host.innerHTML = `
      <h2 class="card__title">检查与预览</h2>
      <div class="empty empty--inset">内容或平台已修改，请重新检查</div>`;
    actionCard.classList.add("hidden");
    return;
  }
  const checks = data.checks || {};
  const notes = data.notes || [];
  const passed = Boolean(checks.passed);
  const supported = Boolean(data.supported);
  const banners = [];
  if (!supported) {
    banners.push(`<div class="banner">${esc(data.support_note || "该平台适配器不可用，可复制文案后人工发布")}</div>`);
  }
  if (notes.length) {
    banners.push(`<div class="banner">${notes.map((n) => esc(n)).join("；")}</div>`);
  }
  host.innerHTML = `
    <div class="toolbar" style="margin-bottom:10px">
      <h2 class="card__title" style="margin:0">检查与预览</h2>
      <div class="spacer"></div>
      <span class="score">${checks.score ?? 0}</span>
      <span class="muted" style="font-size:12px">/ 100 · ${passed ? "通过" : "未通过"}</span>
    </div>
    ${banners.join("")}
    ${checksHtml(checks.items)}
    <h2 class="card__title" style="margin-top:14px">发布后长这样（${esc(data.label || data.platform || "")}）</h2>
    <div class="preview">${esc(data.preview || "")}</div>
    <div class="toolbar" style="margin-top:10px">
      <button class="btn btn--sm btn--ghost" id="copy-preview">复制文案</button>
      <span class="muted" style="font-size:12px">${passed ? "检查通过，可在右侧确认后执行" : "存在阻断问题，改完再检查"}</span>
    </div>
  `;
  host.querySelector("#copy-preview").addEventListener("click", () => {
    copyText(data.preview || "");
  });
  actionCard.classList.toggle("hidden", !(passed && supported));
}

// ---------------------------------------------------------------------------
// 发布
// ---------------------------------------------------------------------------
async function startPublish(dryRun) {
  if (!state.confirmed) return toast("请先勾选「我已核对」", "warn", 2200);
  if (!state.prepared?.passed) return toast("请先通过发布前检查", "warn", 2200);

  if (!dryRun) {
    const ok = await confirmDialog({
      title: "直接发布到平台",
      message:
        "这会打开浏览器并点击平台的发布按钮，内容会立即公开，无法由 Stent 撤回。\n请确认标题、正文、标签与媒体都正确。",
      confirmText: "仍然直接发布",
      danger: true,
      checkbox: "我知道这会直接提交，且需要我自己到平台处理后续",
    });
    if (!ok) return;
  }

  const form = readForm();
  const button = rootRef.querySelector(dryRun ? "#do-fill" : "#do-publish");
  setBusy(button, true, dryRun ? "启动中…" : "发布中…");
  showJob(dryRun ? "正在打开浏览器并填写表单…" : "正在打开浏览器并执行发布…");
  rootRef.querySelector("#job-hint").textContent = dryRun
    ? "表单填好后浏览器会保持打开，你可以在里面核对并点发布；关掉窗口也会结束本次操作。"
    : "请不要关闭浏览器窗口，等待流程自动完成。";
  setStatus(dryRun ? "正在填写表单…" : "正在发布…", "busy");

  try {
    const data = await api.post("/api/publish/start", {
      ...form,
      confirmed: true,
      dry_run: dryRun,
    });
    state.contentId = data.content_id || state.contentId;
    const promise = watchJob(data.job.id, { onTick: updateJob });
    state.job = promise;
    const job = await promise;
    hideJob();
    if (!active || job.status === "detached") return;
    if (job.status === "cancelled") {
      setStatus("已取消", "warn");
      toast("已取消本次操作", "warn", 3000);
    } else if (job.status === "error") {
      setStatus("发布失败", "error");
      renderOutcome({ ok: false, message: job.error }, dryRun);
      toast(job.error, "error", 8000);
    } else if (job.status === "done") {
      renderOutcome(job.result || {}, dryRun);
    }
    await loadHistory();
    await ctxRef.refreshDrafts?.();
  } catch (err) {
    hideJob();
    setStatus("发布失败", "error");
    toast(err.message, "error", 8000);
  } finally {
    state.job = null;
    setBusy(button, false);
  }
}

function renderOutcome(result, dryRun) {
  const host = rootRef?.querySelector("#result-card");
  if (!host) return;
  // 一次执行结束后必须重新勾选：避免连点两次把同一篇重复发出去
  state.confirmed = false;
  const confirmBox = rootRef.querySelector("#confirm");
  if (confirmBox) confirmBox.checked = false;
  syncActions();
  const warnings = result.warnings || [];
  const warningHtml = warnings.length
    ? `<div class="banner" style="margin-top:10px">注意：${warnings.map((w) => esc(w)).join("；")}</div>`
    : "";
  let banner;
  if (result.ok) {
    banner = dryRun
      ? `<div class="banner banner--ok" style="margin-top:10px">
          <span>${esc(result.message || "已填写完成")}。发完之后回到这里，在「发布记录」里把它标记为已发布。</span>
        </div>`
      : `<div class="banner banner--ok" style="margin-top:10px">
          <span>发布成功${result.url ? `：${esc(result.url)}` : "（未取到链接）"}</span>
          ${result.url ? '<button class="btn btn--sm" id="open-post">打开</button>' : ""}
        </div>`;
    setStatus(dryRun ? "已填写，等待你确认" : "发布成功", dryRun ? "warn" : "ok");
  } else {
    banner = `<div class="banner banner--error" style="margin-top:10px">发布未完成：${esc(result.message || "未知原因")}</div>`;
    setStatus("发布未完成", "error");
  }
  host.insertAdjacentHTML("beforeend", banner + warningHtml);
  if (result.url) {
    host.querySelector("#open-post")?.addEventListener("click", () => openExternal(result.url));
  }
  toast(result.ok ? (dryRun ? "已填写完成" : "发布成功") : "发布未完成", result.ok ? "ok" : "error", 4000);
}

function showJob(text) {
  const card = rootRef?.querySelector("#job-card");
  if (!card) return;
  card.classList.remove("hidden");
  card.querySelector("#job-msg").textContent = text;
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

// ---------------------------------------------------------------------------
// 保存草稿
// ---------------------------------------------------------------------------
async function saveDraft(button) {
  const form = readForm();
  if (!form.body && !rootRef.querySelector("#title").value.trim()) {
    return toast("先写点内容再存草稿", "warn", 2200);
  }
  setBusy(button, true, "保存中…");
  try {
    const data = await api.post("/api/drafts", {
      id: state.contentId || undefined,
      platform: form.platform,
      title: form.title,
      body: form.body,
      summary: form.summary,
      tags: form.tags,
      media: state.media,
    });
    state.contentId = data.draft.id;
    await ctxRef.refreshDrafts?.();
    toast(data.created ? "草稿已保存" : "草稿已更新", "ok", 1800);
  } catch (err) {
    toast(err.message, "error", 5000);
  } finally {
    setBusy(button, false);
  }
}

// ---------------------------------------------------------------------------
// 发布记录
// ---------------------------------------------------------------------------
async function loadHistory(showBusy) {
  const host = rootRef?.querySelector("#history");
  const button = rootRef?.querySelector("#refresh-history");
  if (!host) return;
  if (showBusy) setBusy(button, true, "刷新中…");
  try {
    const data = await api.get("/api/publish/history?limit=30");
    state.history = data.items || [];
    renderHistory();
  } catch (err) {
    host.innerHTML = `<div class="empty empty--inset">${esc(err.message)}</div>`;
  } finally {
    if (showBusy) setBusy(button, false);
  }
}

function renderHistory() {
  const host = rootRef?.querySelector("#history");
  if (!host) return;
  if (!state.history.length) {
    host.innerHTML = '<div class="empty empty--inset">还没有发布记录</div>';
    return;
  }
  host.innerHTML = state.history
    .map((row) => {
      const open = row.status === "pending" || row.status === "draft_filled";
      return `<div class="row" data-post="${row.id}" style="align-items:flex-start;flex-wrap:wrap">
        <span class="row__dot" style="background:var(--platform-${esc(row.platform)}, var(--muted));margin-top:6px"></span>
        <div class="draft-row__main">
          <div class="draft-row__title">${esc(row.title || "（无标题）")}</div>
          <div class="draft-row__meta">
            ${esc(row.platform_label)} · ${esc(fmtTime(row.published_at || row.created_at))}
            ${row.error ? ` · <span style="color:var(--danger)">${esc(row.error)}</span>` : ""}
          </div>
        </div>
        <span style="margin-top:2px">${pill(row.status_label, STATUS_TONE[row.status] || "idle")}</span>
        <span class="row__actions" style="opacity:1;margin-top:2px">
          ${row.url ? '<button class="btn btn--sm btn--ghost" data-act="open">打开</button>' : ""}
          ${open ? '<button class="btn btn--sm" data-act="mark">标记已发布</button>' : ""}
          ${open ? '<button class="btn btn--sm btn--ghost" data-act="fail">标记失败</button>' : ""}
          ${row.status === "failed" ? '<button class="btn btn--sm btn--ghost" data-act="cancel">归档</button>' : ""}
        </span>
      </div>`;
    })
    .join("");
}

async function onHistoryClick(event) {
  const button = event.target.closest("[data-act]");
  if (!button) return;
  const row = event.target.closest("[data-post]");
  if (!row) return;
  const postId = Number(row.dataset.post);
  const item = state.history.find((entry) => entry.id === postId);
  if (!item) return;
  const act = button.dataset.act;

  if (act === "open") return openExternal(item.url);

  if (act === "mark") {
    const url = await promptDialog({
      title: "标记为已发布",
      label: "作品链接（可留空，之后可在数据分析里补）",
      value: item.url || "",
      placeholder: "https://…",
      confirmText: "标记已发布",
    });
    if (url === null) return;
    await markPost(postId, "published", url);
    return;
  }

  if (act === "fail") {
    const reason = await promptDialog({
      title: "标记为发布失败",
      label: "原因（可留空）",
      placeholder: "例：浏览器里提示图片不合格",
      confirmText: "标记失败",
    });
    if (reason === null) return;
    await markPost(postId, "failed", "", reason);
    return;
  }

  if (act === "cancel") {
    await markPost(postId, "cancelled", "", "归档旧记录");
  }
}

async function markPost(postId, action, url = "", reason = "") {
  try {
    const data = await api.post("/api/publish/mark", {
      post_id: postId,
      action,
      url,
      reason,
    });
    toast(`已标记为${data.post?.status_label || "新的状态"}`, "ok", 2000);
    await loadHistory();
  } catch (err) {
    toast(err.message, "error", 5000);
  }
}
