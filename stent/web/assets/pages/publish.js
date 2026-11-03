// 发布中心（P0）：格式适配 + 发布前检查 + 预览。
// 真正点按钮提交到平台属于 P2，这里明确标注，不假装能发。

import { api, shell } from "../api.js";
import { esc, setStatus, toast } from "../ui.js";

let media = [];

export async function mount(root, ctx) {
  const platforms = ctx.meta?.publish_platforms || [];
  if (!platforms.length) {
    root.innerHTML = '<div class="empty">未取到发布平台清单</div>';
    return;
  }

  const draft = readDraft();
  const first = platforms.find((p) => p.available) || platforms[0];

  root.innerHTML = `
    <div class="page-head">
      <div>
        <h1 class="page-title">发布中心</h1>
        <p class="page-desc">选平台 → 格式适配 → 发布前检查 → 预览</p>
      </div>
      <div class="page-actions">
        <button class="btn" id="pick">选择媒体</button>
        <button class="btn btn--primary" id="check">检查并预览</button>
      </div>
    </div>

    <div class="banner banner--info">
      <span>当前阶段只做「检查 + 预览」，不会向平台提交任何内容。</span>
      <span class="muted">自动发布在 P2 接入</span>
    </div>

    <div class="grid-side">
      <div class="card">
        <label class="field"><span>目标平台</span>
          <select id="platform">
            ${platforms
              .map(
                (p) =>
                  `<option value="${esc(p.key)}"${p.key === first.key ? " selected" : ""}>${esc(p.label)}${p.available ? "" : "（适配器不可用）"}</option>`
              )
              .join("")}
          </select>
        </label>
        <label class="field"><span>标题</span>
          <input id="title" type="text" value="${esc(draft.title || "")}" placeholder="平台标题">
        </label>
        <label class="field"><span>正文</span>
          <textarea id="body" rows="10" placeholder="可粘贴，或从创作页「送发布中心」带过来">${esc(draft.body || "")}</textarea>
        </label>
        <div class="field-row">
          <label class="field"><span>简介</span><input id="summary" type="text" value="${esc(draft.summary || "")}"></label>
          <label class="field"><span>标签（空格分隔）</span><input id="tags" type="text" value="${esc((draft.tags || []).join(" "))}"></label>
        </div>
        <p class="muted mono" id="media-list" style="margin:0">未选择媒体</p>
      </div>

      <div>
        <div class="card" id="result">
          <div class="empty">点右上角「检查并预览」看结果</div>
        </div>
      </div>
    </div>
  `;

  root.querySelector("#pick").addEventListener("click", async () => {
    if (!shell.available()) {
      return toast("浏览器模式拿不到真实文件路径，请用桌面窗口选择媒体", "warn", 5000);
    }
    const picked = await shell.call("pick_files", "media");
    media = Array.isArray(picked) ? picked : [];
    root.querySelector("#media-list").textContent = media.length
      ? media.map((p) => p.split(/[\\/]/).pop()).join("、")
      : "未选择媒体";
    if (media.length) toast(`已选择 ${media.length} 个文件`, "ok", 1600);
  });

  root.querySelector("#check").addEventListener("click", async (event) => {
    const platform = root.querySelector("#platform").value;
    const body = root.querySelector("#body").value.trim();
    if (!body) return toast("正文为空，先写点内容", "warn");

    event.target.disabled = true;
    event.target.textContent = "检查中…";
    setStatus("正在执行发布前检查…", "busy");
    try {
      const data = await api.post("/api/publish/prepare", {
        platform,
        title: root.querySelector("#title").value.trim(),
        body,
        tags: root.querySelector("#tags").value.split(/\s+/).filter(Boolean),
        summary: root.querySelector("#summary").value.trim(),
        media,
      });
      render(root, data);
      setStatus(
        data.checks?.passed ? `检查通过 · ${data.checks.score} 分` : "检查未通过",
        data.checks?.passed ? "ok" : "error"
      );
    } catch (err) {
      setStatus("检查失败", "error");
      toast(err.message, "error", 6000);
    } finally {
      event.target.disabled = false;
      event.target.textContent = "检查并预览";
    }
  });
}

function readDraft() {
  const raw = sessionStorage.getItem("stent.publishDraft");
  if (!raw) return {};
  try {
    return JSON.parse(raw) || {};
  } catch {
    return {};
  }
}

function render(root, data) {
  const checks = data.checks?.items || [];
  const notes = data.notes || [];

  const checkHtml = checks
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

  const noteHtml = notes.length
    ? `<div class="banner" style="margin-bottom:12px">${notes.map((n) => esc(n)).join("；")}</div>`
    : "";
  const supportHtml = data.supported
    ? ""
    : `<div class="banner" style="margin-bottom:12px">${esc(data.support_note || "该平台适配器不可用，可复制文案后人工发布")}</div>`;

  root.querySelector("#result").innerHTML = `
    <h2 class="card__title">检查结果</h2>
    <div style="display:flex;align-items:baseline;gap:10px;margin-bottom:12px">
      <span class="score">${data.checks?.score ?? 0}</span>
      <span class="muted">/ 100 · ${data.checks?.passed ? "通过" : "未通过"}</span>
    </div>
    ${supportHtml}${noteHtml}
    ${checkHtml || '<div class="empty">没有发现问题</div>'}
    <h2 class="card__title" style="margin-top:16px">预览（${esc(data.label || data.platform)}）</h2>
    <div class="preview">${esc(data.preview || "")}</div>
  `;
}
