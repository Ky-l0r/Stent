// 设置：LLM 三件套 + 主题。密钥只写不读——页面上永远显示不出已存的 Key。

import { api } from "../api.js";
import { esc, setStatus, toast } from "../ui.js";

let data = null;

export async function mount(root, ctx) {
  root.innerHTML = '<div class="loading">正在读取配置…</div>';
  try {
    data = await api.get("/api/settings");
  } catch (err) {
    root.innerHTML = `<div class="empty">${esc(err.message)}</div>`;
    return;
  }
  render(root, ctx);
}

function render(root, ctx) {
  const llm = data.llm || {};
  const presets = data.presets || [];

  root.innerHTML = `
    <div class="page-head">
      <div>
        <h1 class="page-title">设置</h1>
        <p class="page-desc">只需要三项：接口地址、密钥、模型名称</p>
      </div>
      <div class="page-actions">
        <button class="btn" id="test">测试连通性</button>
        <button class="btn btn--primary" id="save">保存</button>
      </div>
    </div>

    <div class="card">
      <h2 class="card__title">LLM 接入</h2>
      <label class="field"><span>服务商</span>
        <select id="provider">
          ${presets
            .map(
              (p) =>
                `<option value="${esc(p.key)}"${p.key === llm.provider ? " selected" : ""}>${esc(p.label)}</option>`
            )
            .join("")}
        </select>
      </label>
      <label class="field"><span>API Base URL</span>
        <input id="base_url" type="text" value="${esc(llm.base_url || "")}" placeholder="https://api.deepseek.com/v1">
      </label>
      <div class="field-row">
        <label class="field"><span>模型名称</span>
          <input id="model" type="text" value="${esc(llm.model || "")}" placeholder="deepseek-chat">
        </label>
        <label class="field"><span>API Key ${data.key_set ? '<span class="muted">（已保存，留空表示不改）</span>' : ""}</span>
          <input id="api_key" type="password" placeholder="${data.key_set ? "••••••••（已保存）" : "sk-..."}" autocomplete="off">
        </label>
      </div>
      <div class="field-row">
        <label class="field"><span>温度（0-2）</span>
          <input id="temperature" type="number" step="0.1" min="0" max="2" value="${Number(llm.temperature ?? 0.7)}">
        </label>
        <label class="field"><span>单次最大 token</span>
          <input id="max_tokens" type="number" min="256" step="256" value="${Number(llm.max_tokens ?? 4096)}">
        </label>
      </div>
      <p class="card__hint">密钥存储方式：${esc(data.key_mode || "未知")}</p>
      <div id="test-result" class="muted" style="font-size:12.5px"></div>
    </div>

    <div class="card">
      <h2 class="card__title">外观</h2>
      <div style="display:flex;gap:8px">
        <button class="btn" data-theme="light">浅色</button>
        <button class="btn" data-theme="dark">暗色</button>
      </div>
    </div>
  `;

  root.querySelector("#provider").addEventListener("change", (event) => {
    const preset = presets.find((p) => p.key === event.target.value);
    if (!preset) return;
    if (preset.base_url) root.querySelector("#base_url").value = preset.base_url;
    if (preset.model) root.querySelector("#model").value = preset.model;
  });

  const collect = () => ({
    llm: {
      provider: root.querySelector("#provider").value,
      base_url: root.querySelector("#base_url").value.trim(),
      model: root.querySelector("#model").value.trim(),
      temperature: Number(root.querySelector("#temperature").value || 0.7),
      max_tokens: Number(root.querySelector("#max_tokens").value || 4096),
    },
    api_key: root.querySelector("#api_key").value,
  });

  root.querySelector("#save").addEventListener("click", async () => {
    try {
      data = await api.put("/api/settings", collect());
      root.querySelector("#api_key").value = "";
      setStatus("配置已保存", "ok");
      toast("配置已保存", "ok", 1600);
      await ctx.refreshMeta();
      render(root, ctx);
    } catch (err) {
      toast(err.message, "error", 5000);
    }
  });

  root.querySelector("#test").addEventListener("click", async (event) => {
    event.target.disabled = true;
    event.target.textContent = "测试中…";
    const box = root.querySelector("#test-result");
    box.textContent = "";
    try {
      // 先保存再测：否则测的是旧配置，容易误导
      data = await api.put("/api/settings", collect());
      const res = await api.post("/api/settings/test", {});
      box.textContent = res.ok ? `${res.message}　返回：${res.sample || ""}` : res.message;
      box.style.color = res.ok ? "var(--ok)" : "var(--danger)";
      if (res.ok) await ctx.refreshMeta();
    } catch (err) {
      box.textContent = err.message;
      box.style.color = "var(--danger)";
    } finally {
      event.target.disabled = false;
      event.target.textContent = "测试连通性";
    }
  });

  root.querySelectorAll("[data-theme]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const theme = btn.dataset.theme;
      document.documentElement.dataset.theme = theme;
      try {
        await api.put("/api/settings", { theme });
        toast(theme === "dark" ? "已切到暗色" : "已切到浅色", "ok", 1400);
      } catch (err) {
        toast(err.message, "error", 4000);
      }
    });
  });
}
