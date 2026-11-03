// 内容创作：单平台流式生成，可随时中断；结果可自检、换标题、送发布。

import { api, stream } from "../api.js";
import { esc, copyText, setStatus, toast } from "../ui.js";

const state = {
  platform: "xiaohongshu",
  topic: "",
  body: "",
  titles: [],
  summary: "",
  tags: [],
  hint: "",
  running: false,
};

let controller = null;

export async function mount(root, ctx) {
  const platforms = ctx.meta?.creation_platforms || [];
  if (!platforms.length) {
    root.innerHTML = '<div class="empty">未取到创作平台清单，请确认接口服务正常</div>';
    return;
  }

  const pending = readPending();
  if (pending) {
    state.topic = pending.topic || state.topic;
  }

  root.innerHTML = `
    <div class="page-head">
      <div>
        <h1 class="page-title">内容创作</h1>
        <p class="page-desc">按平台体例产出正文、标题候选、简介与标签</p>
      </div>
      <div class="page-actions">
        <span id="llm-hint"></span>
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
              <input id="goal" type="text" value="互动涨粉">
            </label>
          </div>
          <details style="margin-bottom:12px">
            <summary class="muted" style="cursor:pointer;font-size:12.5px">受众 / 调性 / 补充要求</summary>
            <div style="margin-top:10px">
              <label class="field"><span>目标受众</span><input id="audience" type="text" placeholder="例：一二线城市职场女性"></label>
              <label class="field"><span>期望调性</span><input id="tone" type="text" placeholder="例：口语化、真诚、不喊口号"></label>
              <label class="field"><span>补充要求</span><textarea id="extra" rows="2" placeholder="例：不要超过 500 字；开头用提问"></textarea></label>
              <label class="field"><span>素材原文（可选）</span><textarea id="source" rows="3" placeholder="热点详情、采访记录、产品信息…">${esc(pending?.source || "")}</textarea></label>
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
          <p class="muted" id="hint" style="font-size:12px;margin:10px 0 0">${esc(state.hint)}</p>
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
    root.querySelector("#body-len").textContent = `（${body.value.length} 字）`;
  };
  body.addEventListener("input", () => {
    state.body = body.value;
    refreshLen();
  });
  refreshLen();

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
            root.querySelector("#hint").textContent = state.hint;
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

  root.querySelector("#retitle").addEventListener("click", async (event) => {
    if (!state.body.trim()) return toast("先生成正文", "warn");
    event.target.disabled = true;
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
      event.target.disabled = false;
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
    host.innerHTML = '<div class="empty" style="padding:16px 0">还没有标题候选</div>';
    return;
  }
  host.innerHTML = state.titles
    .map(
      (title, index) => `
      <div class="row" data-index="${index}">
        <span class="row__rank">${index + 1}</span>
        <span class="row__title">${esc(title)}</span>
        <span class="row__actions"><button class="btn btn--sm btn--ghost" data-act="copy">复制</button></span>
      </div>`
    )
    .join("");
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
