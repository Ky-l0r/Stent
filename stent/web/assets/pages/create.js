// 内容创作：单平台流式生成，可随时中断；结果可自检、换标题、存草稿、送发布。

import { api, stream } from "../api.js";
import { esc, copyText, setBusy, setStatus, toast } from "../ui.js";

const state = {
  platform: "xiaohongshu",
  topic: "",
  body: "",
  titles: [],
  summary: "",
  tags: [],
  hint: "",
  running: false,
  contentId: 0,
};

let controller = null;

export async function mount(root, ctx) {
  const platforms = ctx.meta?.creation_platforms || [];
  if (!platforms.length) {
    root.innerHTML = '<div class="empty">未取到创作平台清单，请确认接口服务正常</div>';
    return;
  }

  const pending = readPending();
  const draft = readDraft();
  if (!draft && !pending) {
    // 从导航进来且没有带草稿 / 热点：清空上一次的内存态，避免「新建」其实还是旧内容
    state.contentId = 0;
    state.topic = "";
    state.body = "";
    state.titles = [];
    state.summary = "";
    state.tags = [];
    state.hint = "";
  }
  if (draft) {
    state.contentId = Number(draft.id) || 0;
    state.platform = draft.platform || state.platform;
    state.topic = draft.topic || "";
    state.body = draft.body || "";
    state.titles = Array.isArray(draft.titles) ? draft.titles : [];
    state.summary = draft.summary || "";
    state.tags = Array.isArray(draft.tags) ? draft.tags : [];
  }
  if (pending?.topic) {
    state.topic = pending.topic;
  }

  const params = draft?.params || {};
  const form = {
    goal: params.goal || "互动涨粉",
    audience: params.audience || "",
    tone: params.tone || "",
    extra: params.extra || draft?.extra || "",
    source: pending?.source || "",
  };
  if (!platforms.some((p) => p.key === state.platform)) {
    state.platform = platforms[0].key;
  }

  root.innerHTML = `
    <div class="page-head">
      <div>
        <h1 class="page-title">内容创作</h1>
        <p class="page-desc">按平台体例产出正文、标题候选、简介与标签</p>
      </div>
      <div class="page-actions">
        <span id="llm-hint"></span>
        <button class="btn" id="save-draft">${state.contentId ? "更新草稿" : "保存草稿"}</button>
        <button class="btn" id="stop" disabled>中断</button>
        <button class="btn btn--primary" id="run">生成</button>
      </div>
    </div>
    <div class="grid-side">
      <div>
        <div class="card">
          <label class="field"><span>主题 / 热点</span>
            <input id="topic" type="text" placeholder="例如：通勤穿搭怎么不显胖" value="${esc(state.topic)}">
          </label>
          <div class="field-row">
            <label class="field"><span>目标平台</span>
              <select id="platform">
                ${platforms
                  .map(
                    (p) =>
                      `<option value="${esc(p.key)}"${p.key === state.platform ? " selected" : ""}>${esc(p.label)} · ${esc(p.content_type)}</option>`
                  )
                  .join("")}
              </select>
            </label>
            <label class="field"><span>内容目标</span>
              <input id="goal" type="text" value="${esc(form.goal)}">
            </label>
          </div>
          <p class="muted" id="limit-hint" style="margin:-4px 0 12px;font-size:11.5px"></p>
          <details style="margin-bottom:12px"${form.audience || form.tone || form.extra || form.source ? " open" : ""}>
            <summary class="muted" style="cursor:pointer;font-size:12.5px">受众 / 调性 / 补充要求</summary>
            <div style="margin-top:10px">
              <label class="field"><span>目标受众</span><input id="audience" type="text" placeholder="例：一二线城市职场女性" value="${esc(form.audience)}"></label>
              <label class="field"><span>期望调性</span><input id="tone" type="text" placeholder="例：口语化、真诚、不喊口号" value="${esc(form.tone)}"></label>
              <label class="field"><span>补充要求</span><textarea id="extra" rows="2" placeholder="例：不要超过 500 字；开头用提问">${esc(form.extra)}</textarea></label>
              <label class="field"><span>素材原文（可选）</span><textarea id="source" rows="3" placeholder="热点详情、采访记录、产品信息…">${esc(form.source)}</textarea></label>
            </div>
          </details>
          <p class="muted" style="margin:0;font-size:12px">Ctrl+Enter 生成</p>
        </div>
      </div>

      <div>
        <div class="card">
          <h2 class="card__title">正文 <span class="muted" id="body-len"></span></h2>
          <textarea id="body" rows="12" placeholder="生成结果会流式出现在这里，也可以直接手写">${esc(state.body)}</textarea>
          <div style="display:flex;gap:8px;margin-top:10px;flex-wrap:wrap">
            <button class="btn btn--sm" id="quality">质量自检</button>
            <button class="btn btn--sm" id="retitle">换标题</button>
            <button class="btn btn--sm" id="copy">复制全文</button>
            <button class="btn btn--sm" id="topublish">送发布中心</button>
          </div>
          <p class="muted" id="hint" style="font-size:12px;margin:10px 0 0">${esc(state.hint || "正文只存在内存里，切走前记得存草稿")}</p>
        </div>

        <div class="card">
          <h2 class="card__title">标题候选</h2>
          <div id="titles" class="list"></div>
        </div>

        <div class="card">
          <h2 class="card__title">简介</h2>
          <textarea id="summary" rows="3" placeholder="平台简介 / 摘要">${esc(state.summary)}</textarea>
          <h2 class="card__title" style="margin-top:12px">标签</h2>
          <input id="tags" type="text" placeholder="空格分隔，例：通勤穿搭 平价好物" value="${esc(state.tags.join(" "))}">
        </div>

        <div class="card hidden" id="report">
          <h2 class="card__title">质量自检</h2>
          <div id="report-body"></div>
        </div>
      </div>
    </div>
  `;

  renderTitles(root);
  updateLlmHint(root, ctx);
  updateLimitHint(root, ctx);
  bind(root, ctx);
}

function readPending() {
  const raw = sessionStorage.getItem("stent.pending");
  if (!raw) return null;
  sessionStorage.removeItem("stent.pending");
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

function readDraft() {
  const raw = sessionStorage.getItem("stent.draft");
  if (!raw) return null;
  sessionStorage.removeItem("stent.draft");
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

function limitsFor(ctx, platform) {
  const row = (ctx.meta?.publish_platforms || []).find((p) => p.key === platform);
  return row?.limits || null;
}

function updateLimitHint(root, ctx) {
  const host = root.querySelector("#limit-hint");
  if (!host) return;
  const limits = limitsFor(ctx, root.querySelector("#platform").value);
  host.textContent = limits
    ? `该平台上限：标题 ≤ ${limits.title_max} 字 · 正文 ${limits.body_min}–${limits.body_max} 字 · 标签 ≤ ${limits.tags_max} 个`
    : "该平台没有配置字数上限，发布前检查时会再核一次";
}

function updateLlmHint(root, ctx) {
  const hint = root.querySelector("#llm-hint");
  const ready = ctx.meta?.llm_ready;
  hint.innerHTML = ready
    ? ""
    : '<button class="btn btn--sm" id="go-settings">未配置模型，去设置</button>';
  hint.querySelector("#go-settings")?.addEventListener("click", () => ctx.navigate("settings"));
}

function bind(root, ctx) {
  const runBtn = root.querySelector("#run");
  const stopBtn = root.querySelector("#stop");
  const body = root.querySelector("#body");

  const readForm = () => ({
    topic: root.querySelector("#topic").value.trim(),
    platform: root.querySelector("#platform").value,
    goal: root.querySelector("#goal").value.trim(),
    audience: root.querySelector("#audience").value.trim(),
    tone: root.querySelector("#tone").value.trim(),
    extra: root.querySelector("#extra").value.trim(),
    source_text: root.querySelector("#source").value.trim(),
  });

  const refreshLen = () => {
    const limits = limitsFor(ctx, root.querySelector("#platform").value);
    const count = body.value.length;
    const over = limits && count > limits.body_max;
    const box = root.querySelector("#body-len");
    box.textContent = limits ? `（${count} / ${limits.body_max} 字）` : `（${count} 字）`;
    box.style.color = over ? "var(--danger)" : "";
    box.title = over ? `超出平台正文上限 ${count - limits.body_max} 字，发布前检查会拦下` : "";
  };
  body.addEventListener("input", () => {
    state.body = body.value;
    refreshLen();
  });
  refreshLen();

  // 手改简介 / 标签也要同步进内存态：复制全文、存草稿、送发布都读 state
  root.querySelector("#summary").addEventListener("input", (event) => {
    state.summary = event.target.value;
  });
  root.querySelector("#tags").addEventListener("input", (event) => {
    state.tags = event.target.value.split(/[\s,，]+/).filter(Boolean);
  });

  root.querySelector("#platform").addEventListener("change", () => {
    state.platform = root.querySelector("#platform").value;
    updateLimitHint(root, ctx);
    refreshLen();
  });

  // 切页时中断生成：否则界面已经走了，token 还在烧
  ctx.onLeave(() => controller?.abort());

  async function run() {
    const form = readForm();
    if (!form.topic) return toast("请先填写主题", "warn");
    if (state.running) return;

    state.running = true;
    controller = new AbortController();
    runBtn.disabled = true;
    stopBtn.disabled = false;
    setStatus("正在生成…", "busy");

    body.value = "";
    root.querySelector("#summary").value = "";
    root.querySelector("#tags").value = "";
    root.querySelector("#titles").innerHTML = "";
    root.querySelector("#report").classList.add("hidden");
    state.body = "";
    state.titles = [];
    state.summary = "";
    state.tags = [];

    try {
      await stream("/api/create/stream", form, {
        signal: controller.signal,
        onEvent: (name, payload) => {
          if (name === "delta") {
            if (payload.section === "标题") {
              state.titles = parseTitles((state.titles.join("\n") + payload.text).split("\n"));
              renderTitles(root);
              return;
            }
            if (payload.section === "简介") {
              const box = root.querySelector("#summary");
              box.value += payload.text;
              state.summary = box.value;
              return;
            }
            if (payload.section === "标签") {
              state.tags = (root.querySelector("#tags").value + payload.text)
                .split(/\s+/)
                .filter(Boolean);
              root.querySelector("#tags").value = state.tags.join(" ");
              return;
            }
            body.value += payload.text;
            state.body = body.value;
            refreshLen();
            body.scrollTop = body.scrollHeight;
          } else if (name === "done") {
            const result = payload.result || {};
            state.body = result.body || body.value;
            state.titles = result.titles || [];
            state.summary = result.summary || "";
            state.tags = result.tags || [];
            state.hint = payload.hint || "";
            body.value = state.body;
            root.querySelector("#summary").value = state.summary;
            root.querySelector("#tags").value = state.tags.join(" ");
            root.querySelector("#hint").textContent =
              state.hint || "生成完成。核对措辞后再存草稿或送发布。";
            renderTitles(root);
            refreshLen();
            setStatus("生成完成", "ok");
          } else if (name === "error") {
            setStatus("生成失败", "error");
            toast(payload.message || "生成失败", "error", 6000);
          }
        },
      });
    } catch (err) {
      if (err.name === "AbortError") {
        setStatus("已中断", "warn");
        toast("已中断，已生成的部分保留", "warn");
      } else {
        setStatus("生成失败", "error");
        toast(err.message, "error", 6000);
      }
    } finally {
      state.running = false;
      controller = null;
      runBtn.disabled = false;
      stopBtn.disabled = true;
    }
  }

  runBtn.addEventListener("click", run);
  stopBtn.addEventListener("click", () => controller?.abort());

  root.querySelector("#topic").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) run();
  });

  root.querySelector("#copy").addEventListener("click", () => {
    const tags = state.tags.map((t) => (t.startsWith("#") ? t : `#${t}`)).join(" ");
    copyText([state.body, tags].filter(Boolean).join("\n\n"));
  });

  root.querySelector("#topublish").addEventListener("click", () => {
    if (!state.body.trim()) return toast("还没有正文", "warn");
    sessionStorage.setItem(
      "stent.publishDraft",
      JSON.stringify({
        content_id: state.contentId || undefined,
        platform: root.querySelector("#platform").value,
        title: state.titles[0] || "",
        titles: state.titles,
        body: state.body,
        tags: state.tags,
        summary: state.summary,
      })
    );
    toast("已送入发布中心", "ok", 1600);
    ctx.navigate("publish");
  });

  root.querySelector("#save-draft").addEventListener("click", async () => {
    const button = root.querySelector("#save-draft");
    const form = readForm();
    if (!form.topic && !state.body.trim()) {
      return toast("先写点主题或正文，再存草稿", "warn", 2200);
    }
    setBusy(button, true, "保存中…");
    try {
      const data = await api.post("/api/drafts", {
        id: state.contentId || undefined,
        topic: form.topic,
        platform: form.platform,
        title: state.titles[0] || "",
        titles: state.titles,
        body: state.body,
        summary: state.summary,
        tags: state.tags,
        extra: form.extra,
        params: { goal: form.goal, audience: form.audience, tone: form.tone },
      });
      state.contentId = data.draft.id;
      button.dataset.idleText = "更新草稿";
      ctx.refreshDrafts?.();
      toast(data.created ? "草稿已保存" : "草稿已更新", "ok", 1800);
      setStatus("草稿已保存", "ok");
    } catch (err) {
      toast(err.message, "error", 5000);
    } finally {
      setBusy(button, false);
    }
  });

  root.querySelector("#retitle").addEventListener("click", async (event) => {
    if (!state.body.trim()) return toast("先生成正文", "warn");
    setBusy(event.target, true, "生成中…");
    try {
      const form = readForm();
      const data = await api.post("/api/create/titles", {
        topic: form.topic || "未命名主题",
        platform: form.platform,
        body: state.body,
        count: 3,
      });
      state.titles = data.titles || [];
      renderTitles(root);
      toast("标题已更新", "ok", 1600);
    } catch (err) {
      toast(err.message, "error", 5000);
    } finally {
      setBusy(event.target, false);
    }
  });

  root.querySelector("#quality").addEventListener("click", async (event) => {
    if (!state.body.trim()) return toast("先生成正文", "warn");
    event.target.disabled = true;
    event.target.textContent = "自检中…";
    try {
      const data = await api.post("/api/create/quality", {
        platform: root.querySelector("#platform").value,
        body: state.body,
        titles: state.titles,
        summary: state.summary,
        tags: state.tags,
      });
      renderReport(root, data.report);
    } catch (err) {
      toast(err.message, "error", 6000);
    } finally {
      event.target.disabled = false;
      event.target.textContent = "质量自检";
    }
  });
}

function parseTitles(lines) {
  return lines
    .map((line) => line.replace(/^\s*(?:\d+[.、)．]|[-*·•])\s*/, "").trim())
    .filter(Boolean)
    .slice(0, 5);
}

function renderTitles(root) {
  const host = root.querySelector("#titles");
  if (!host) return;
  if (!state.titles.length) {
    host.innerHTML = '<div class="empty empty--inset">还没有标题候选</div>';
    return;
  }
  host.innerHTML = state.titles
    .map(
      (title, index) => `
      <div class="row" data-index="${index}">
        <span class="row__rank">${index + 1}</span>
        <span class="row__title">${esc(title)}</span>
        <span class="row__actions">
          <button class="btn btn--sm btn--ghost" data-act="use">${index === 0 ? "已选用" : "选用"}</button>
          <button class="btn btn--sm btn--ghost" data-act="copy">复制</button>
        </span>
      </div>`
    )
    .join("");
  host.querySelectorAll('[data-act="use"]').forEach((btn, index) => {
    btn.addEventListener("click", () => {
      if (index === 0) return;
      const [picked] = state.titles.splice(index, 1);
      state.titles.unshift(picked);
      renderTitles(root);
      toast("已设为发布标题", "ok", 1500);
    });
  });
  host.querySelectorAll('[data-act="copy"]').forEach((btn, index) => {
    btn.addEventListener("click", () => copyText(state.titles[index]));
  });
}

function renderReport(root, report) {
  const card = root.querySelector("#report");
  const body = root.querySelector("#report-body");
  card.classList.remove("hidden");
  const items = (report?.items || [])
    .map((item) => {
      const level = item.level === "error" ? "error" : item.level === "warn" ? "warn" : "info";
      const label = level === "error" ? "必须修改" : level === "warn" ? "建议修改" : "提示";
      return `<div class="check check--${level}">
        <div>
          <div class="check__head">${esc(label)} · ${esc(item.title || "")}</div>
          <div class="check__body">${esc(item.detail || "")}${item.suggestion ? `<br>建议：${esc(item.suggestion)}` : ""}</div>
        </div>
      </div>`;
    })
    .join("");
  body.innerHTML = `
    <div style="display:flex;align-items:baseline;gap:10px;margin-bottom:10px">
      <span class="score">${report?.score ?? 0}</span>
      <span class="muted">/ 100 · ${report?.passed ? "通过" : "未通过"}</span>
    </div>${items || '<div class="empty">没有发现问题</div>'}`;
}
