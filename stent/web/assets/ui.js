// 与页面无关的小工具：转义、提示、状态灯、平台色、对话框、按钮忙碌态。
// 约定：这里不出现业务判断，页面把接口返回的中文文案原样交给 toast。

export function esc(text) {
  return String(text ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

export function toast(message, level = "info", timeout = 3200) {
  const host = document.getElementById("toast-host");
  if (!host) return;
  const node = document.createElement("div");
  node.className = "toast";
  node.dataset.level = level;
  node.textContent = message;
  host.appendChild(node);
  setTimeout(() => {
    node.style.opacity = "0";
    node.style.transition = "opacity .2s";
    setTimeout(() => node.remove(), 220);
  }, timeout);
}

// 顶栏状态灯：busy / ok / warn / error / idle
export function setStatus(text, level = "idle") {
  const pill = document.getElementById("status");
  if (!pill) return;
  pill.dataset.level = level;
  pill.querySelector(".status-pill__text").textContent = text;
  pill.title = text;
}

export function platformColor(key) {
  return `var(--platform-${key || "unknown"}, var(--muted))`;
}

export function fmtCount(n) {
  const value = Number(n) || 0;
  if (value >= 10000) return `${(value / 10000).toFixed(1)} 万`;
  return String(value);
}

// 表格里的数字：加千分位，缺失值显示「—」而不是 0，避免「没数据」看成「真是 0」
export function fmtInt(n) {
  if (n === null || n === undefined || n === "") return "—";
  const value = Number(n);
  if (!Number.isFinite(value)) return "—";
  return value.toLocaleString("zh-CN");
}

// 互动率在服务层已经是百分数（3.5 表示 3.5%），这里不要再乘 100
export function fmtRate(rate) {
  if (rate === null || rate === undefined) return "—";
  const value = Number(rate);
  if (!Number.isFinite(value)) return "—";
  return `${value.toFixed(1)}%`;
}

// 时间戳统一是 "YYYY-MM-DD HH:MM:SS"；这里只做「刚刚 / N 分钟前 / 日期」的粗粒度展示
export function fmtTime(text) {
  const raw = String(text || "").trim();
  if (!raw) return "";
  const stamp = new Date(raw.replace(" ", "T"));
  if (Number.isNaN(stamp.getTime())) return raw;
  const diff = Date.now() - stamp.getTime();
  if (diff >= 0 && diff < 60_000) return "刚刚";
  if (diff >= 0 && diff < 3_600_000) return `${Math.floor(diff / 60_000)} 分钟前`;
  if (diff >= 0 && diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} 小时前`;
  const pad = (n) => String(n).padStart(2, "0");
  const sameYear = stamp.getFullYear() === new Date().getFullYear();
  const date = `${pad(stamp.getMonth() + 1)}-${pad(stamp.getDate())}`;
  return sameYear ? `${date} ${pad(stamp.getHours())}:${pad(stamp.getMinutes())}` : `${stamp.getFullYear()}-${date}`;
}

// 双击标题以外的交互里，最简单的「点一下复制」提示
export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast("已复制", "ok", 1500);
  } catch {
    toast("复制失败：当前环境不允许访问剪贴板", "warn");
  }
}

export function openExternal(url) {
  const link = String(url || "").trim();
  if (!link) return toast("这条没有原链接", "warn", 1600);
  const bridge = window.pywebview?.api;
  if (bridge?.open_external) bridge.open_external(link);
  else window.open(link, "_blank", "noopener");
}

export function debounce(fn, delay = 200) {
  let timer = 0;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), delay);
  };
}

// 按钮忙碌态：调用方只管开关，文案与禁用都收在这里，避免每页各写一套
export function setBusy(button, busy, busyText = "处理中…") {
  if (!button) return;
  if (busy) {
    if (!button.dataset.idleText) button.dataset.idleText = button.textContent;
    button.disabled = true;
    button.dataset.busy = "1";
    button.innerHTML = `<span class="spinner"></span> ${esc(busyText)}`;
  } else {
    button.disabled = false;
    delete button.dataset.busy;
    if (button.dataset.idleText) button.textContent = button.dataset.idleText;
  }
}

// ---------- 检查清单（创作页质量自检 / 发布页检查共用） ----------
const LEVEL_LABEL = { error: "必须修改", warn: "建议修改", info: "提示" };

export function checksHtml(items) {
  const rows = (items || [])
    .map((item) => {
      const level = item.level === "error" ? "error" : item.level === "warn" ? "warn" : "info";
      const suggestion = item.suggestion
        ? `<br><span class="check__fix">建议：${esc(item.suggestion)}</span>`
        : "";
      return `<div class="check check--${level}">
        <div>
          <div class="check__head">${LEVEL_LABEL[level]} · ${esc(item.title || "")}</div>
          <div class="check__body">${esc(item.detail || "")}${suggestion}</div>
        </div>
      </div>`;
    })
    .join("");
  return rows || '<div class="empty empty--inset">没有发现问题</div>';
}

// 状态标签：发布记录用它区分「待你确认 / 已发布 / 失败」
export function pill(label, tone = "idle") {
  return `<span class="pill pill--${esc(tone)}">${esc(label)}</span>`;
}

export function emptyState(title, hint = "", action = "") {
  return `<div class="empty empty--state">
    <div class="empty__title">${esc(title)}</div>
    ${hint ? `<div class="empty__hint">${esc(hint)}</div>` : ""}
    ${action}
  </div>`;
}

// ---------- 对话框 ----------
function openDialog({ title, body, actions }) {
  const dialog = document.createElement("dialog");
  dialog.className = "dialog";
  dialog.innerHTML = `
    <form method="dialog" class="dialog__panel">
      <h3 class="dialog__title">${esc(title)}</h3>
      <div class="dialog__body">${body}</div>
      <div class="dialog__actions">${actions}</div>
    </form>`;
  document.body.appendChild(dialog);
  dialog.addEventListener("close", () => dialog.remove());
  dialog.showModal();
  return dialog;
}

/**
 * 确认框：返回 Promise<boolean>。
 * 传了 checkbox 时必须勾选才能确认——发布这类不可逆动作靠它拦一道。
 */
export function confirmDialog({
  title,
  message,
  confirmText = "确定",
  cancelText = "取消",
  danger = false,
  checkbox = "",
}) {
  return new Promise((resolve) => {
    let settled = false;
    const finish = (value) => {
      if (settled) return;
      settled = true;
      resolve(value);
    };
    const dialog = openDialog({
      title,
      body: `<p class="dialog__text">${esc(message)}</p>${
        checkbox
          ? `<label class="checkbox"><input type="checkbox" id="dlg-check"><span>${esc(checkbox)}</span></label>`
          : ""
      }`,
      actions: `<button class="btn" value="cancel" type="button" data-act="cancel">${esc(cancelText)}</button>
        <button class="btn ${danger ? "btn--danger" : "btn--primary"}" value="ok" type="button" data-act="ok">${esc(confirmText)}</button>`,
    });
    const okBtn = dialog.querySelector('[data-act="ok"]');
    const check = dialog.querySelector("#dlg-check");
    if (check) {
      okBtn.disabled = true;
      check.addEventListener("change", () => {
        okBtn.disabled = !check.checked;
      });
    }
    dialog.querySelector('[data-act="cancel"]').addEventListener("click", () => {
      finish(false);
      dialog.close();
    });
    okBtn.addEventListener("click", () => {
      finish(true);
      dialog.close();
    });
    dialog.addEventListener("cancel", (event) => {
      event.preventDefault();
      finish(false);
      dialog.close();
    });
  });
}

/** 单行输入框：用于「标记已发布」填链接、重命名标题。返回字符串或 null。 */
export function promptDialog({
  title,
  label = "",
  value = "",
  placeholder = "",
  confirmText = "确定",
}) {
  return new Promise((resolve) => {
    let settled = false;
    const finish = (result) => {
      if (settled) return;
      settled = true;
      resolve(result);
    };
    const dialog = openDialog({
      title,
      body: `<label class="field"><span>${esc(label)}</span>
        <input id="dlg-input" type="text" value="${esc(value)}" placeholder="${esc(placeholder)}"></label>`,
      actions: `<button class="btn" type="button" data-act="cancel">取消</button>
        <button class="btn btn--primary" type="button" data-act="ok">${esc(confirmText)}</button>`,
    });
    const input = dialog.querySelector("#dlg-input");
    input.focus();
    input.select();
    const submit = () => {
      finish(input.value.trim());
      dialog.close();
    };
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        submit();
      }
    });
    dialog.querySelector('[data-act="cancel"]').addEventListener("click", () => {
      finish(null);
      dialog.close();
    });
    dialog.querySelector('[data-act="ok"]').addEventListener("click", submit);
    dialog.addEventListener("cancel", (event) => {
      event.preventDefault();
      finish(null);
      dialog.close();
    });
  });
}
