// 账号画像：六维表单 + 完整度 + 记忆回流。

import { api } from "../api.js";
import { confirmDialog, esc, setBusy, setStatus, toast } from "../ui.js";

let data = null;

export async function mount(root, ctx) {
  root.innerHTML = '<div class="loading">正在读取画像…</div>';
  try {
    data = await api.get("/api/profile");
  } catch (err) {
    root.innerHTML = `<div class="empty">${esc(err.message)}</div>`;
    return;
  }
  render(root, ctx);
}

function render(root, ctx) {
  const profile = data.profile || {};
  const dims = data.dimensions || [];
  const editable = dims.filter((d) => d.key !== "memory");
  const memory = profile.memory || [];

  root.innerHTML = `
    <div class="page-head">
      <div>
        <h1 class="page-title">账号画像</h1>
        <p class="page-desc">画像会作为高优先级上下文注入每一次创作</p>
      </div>
      <div class="page-actions">
        <span class="muted" id="completeness">完整度 ${data.completeness}%</span>
        <button class="btn btn--primary" id="save">保存画像</button>
      </div>
    </div>

    <div class="card">
      <progress id="bar" max="100" value="${data.completeness}" style="width:100%;height:6px"></progress>
      <p class="card__hint" style="margin-top:8px">${(data.missing || []).length ? `还缺：${esc((data.missing || []).join("、"))}` : "六个维度都填好了"}</p>
    </div>

    <div class="card">
      <h2 class="card__title">AI 生成画像草稿</h2>
      <p class="card__hint">粘贴一段自我介绍，让模型整理成结构化画像；生成后先预览，确认再保存。</p>
      <textarea id="intro" rows="4" placeholder="例：我是一二线城市的职场穿搭博主，主要在小红书更新通勤穿搭…"></textarea>
      <div style="margin-top:10px"><button class="btn" id="draft">生成草稿</button></div>
    </div>

    <div class="card">
      <h2 class="card__title">六维画像</h2>
      ${editable
        .map((dim) => {
          const value = profile[dim.key];
          const text = Array.isArray(value) ? value.join("、") : value || "";
          return `<label class="field">
            <span>${esc(dim.label)} · <span class="muted">${esc(dim.hint)}</span></span>
            <textarea data-dim="${esc(dim.key)}" rows="3" placeholder="${esc(dim.placeholder)}">${esc(text)}</textarea>
          </label>`;
        })
        .join("")}
    </div>

    <div class="card">
      <h2 class="card__title">记忆（来自数据分析回流）</h2>
      <div class="list" id="memory">
        ${
          memory.length
            ? memory
                .slice(-12)
                .reverse()
                .map(
                  (entry) => `<div class="row">
                    <span class="row__title" style="white-space:normal">${esc(typeof entry === "string" ? entry : entry.text || "")}</span>
                    <span class="row__meta">${esc(entry?.at || "")}</span>
                  </div>`
                )
                .join("")
            : '<div class="empty" style="padding:14px 0">还没有记忆条目</div>'
        }
      </div>
      <div style="display:flex;gap:8px;margin-top:10px">
        <input id="memory-input" type="text" placeholder="手动补一条经验，例：标题带数字的笔记收藏率更高">
        <button class="btn" id="memory-add">追加</button>
        <button class="btn btn--ghost" id="memory-clear">清空</button>
      </div>
    </div>
  `;

  root.querySelector("#save").addEventListener("click", async (event) => {
    const payload = {};
    root.querySelectorAll("[data-dim]").forEach((box) => {
      const key = box.dataset.dim;
      const raw = box.value.trim();
      payload[key] = key === "platforms" ? raw.split(/[、,，\n]+/).map((s) => s.trim()).filter(Boolean) : raw;
    });
    setBusy(event.target, true, "保存中…");
    try {
      data = await api.put("/api/profile", payload);
      setStatus("画像已保存", "ok");
      toast("画像已保存", "ok", 1600);
      render(root, ctx);
    } catch (err) {
      toast(err.message, "error", 5000);
    } finally {
      setBusy(event.target, false);
    }
  });

  root.querySelector("#draft").addEventListener("click", async (event) => {
    const text = root.querySelector("#intro").value.trim();
    if (!text) return toast("先粘贴一段自我介绍", "warn");
    setBusy(event.target, true, "生成中…");
    try {
      const res = await api.post("/api/profile/draft", { text });
      let filled = 0;
      Object.entries(res.draft || {}).forEach(([key, value]) => {
        const box = root.querySelector(`[data-dim="${key}"]`);
        if (!box) return;
        box.value = Array.isArray(value) ? value.join("、") : value;
        filled += 1;
      });
      toast(
        filled ? `已填入 ${filled} 个维度，确认后点保存` : "模型没有给出可用的画像字段，换个说法再试",
        filled ? "ok" : "warn",
        3000
      );
    } catch (err) {
      toast(err.message, "error", 6000);
    } finally {
      setBusy(event.target, false);
    }
  });

  const addMemory = async (event) => {
    const input = root.querySelector("#memory-input");
    const text = input.value.trim();
    if (!text) return input.focus();
    setBusy(event.target, true, "追加中…");
    try {
      data = await api.post("/api/profile/memory", { text });
      render(root, ctx);
      toast("已追加", "ok", 1500);
    } catch (err) {
      toast(err.message, "error", 5000);
    } finally {
      setBusy(event.target, false);
    }
  };
  root.querySelector("#memory-add").addEventListener("click", addMemory);
  root.querySelector("#memory-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter") addMemory(event);
  });

  root.querySelector("#memory-clear").addEventListener("click", async (event) => {
    const ok = await confirmDialog({
      title: "清空记忆",
      message: "记忆会作为创作时的高优先级上下文，清空后无法恢复。确定要清空吗？",
      confirmText: "清空",
      danger: true,
    });
    if (!ok) return;
    setBusy(event.target, true, "清空中…");
    try {
      data = await api.del("/api/profile/memory");
      render(root, ctx);
      toast("记忆已清空", "ok", 1500);
    } catch (err) {
      toast(err.message, "error", 5000);
    } finally {
      setBusy(event.target, false);
    }
  });
}
