import { useEffect } from "react";
import type { ReactElement } from "react";

import { api, initBackend } from "./api";
import CreatePage from "./pages/Create";
import HotPage from "./pages/Hot";
import ProfilePage from "./pages/Profile";
import PublishPage from "./pages/Publish";
import SettingsPage from "./pages/Settings";
import { useStore, type Meta, type Route } from "./store";

const NAV: { key: Route; label: string; icon: string }[] = [
  { key: "hot", label: "热点发现", icon: "◈" },
  { key: "create", label: "内容创作", icon: "✎" },
  { key: "publish", label: "发布中心", icon: "➤" },
  { key: "profile", label: "账号画像", icon: "◍" },
];

// React 19 不再提供全局 JSX 命名空间，这里显式用 ReactElement
const PAGES: Record<Route, () => ReactElement> = {
  hot: HotPage,
  create: CreatePage,
  publish: PublishPage,
  profile: ProfilePage,
  settings: SettingsPage,
};

export default function App() {
  const route = useStore((s) => s.route);
  const setRoute = useStore((s) => s.setRoute);
  const theme = useStore((s) => s.theme);
  const setTheme = useStore((s) => s.setTheme);
  const meta = useStore((s) => s.meta);
  const setMeta = useStore((s) => s.setMeta);
  const setStatus = useStore((s) => s.setStatus);
  const toasts = useStore((s) => s.toasts);
  const dismiss = useStore((s) => s.dismiss);

  // 启动：拿后端地址与令牌 → 拉元信息 → 应用主题
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        await initBackend();
        const [meta, health] = await Promise.all([
          api.get<Meta>("/api/meta"),
          api.get<{ llm_ready: boolean; version: string }>("/api/health"),
        ]);
        if (cancelled) return;
        setMeta(meta);
        setTheme(meta.theme === "dark" ? "dark" : "light");
        setStatus(health.llm_ready ? "就绪" : "未配置模型", health.llm_ready ? "idle" : "warn");
      } catch (error) {
        if (cancelled) return;
        const message = error instanceof Error ? error.message : String(error);
        setStatus("后端未就绪", "error");
        useStore.getState().toast(`无法连接后端：${message}`, "error");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [setMeta, setStatus, setTheme]);

  const Page = PAGES[route];

  return (
    <div className="app">
      <header className="titlebar">
        <div className="titlebar__drag">
          <span className="brand-dot" />
          <span className="brand-name">Stent</span>
          <span className="brand-sub">社媒内容智能体</span>
        </div>
        <div className="titlebar__buttons">
          <StatusPill />
          <button
            className="icon-btn"
            title="切换主题"
            onClick={() => {
              const next = theme === "dark" ? "light" : "dark";
              setTheme(next);
              api.put("/api/settings", { theme: next }).catch(() => {
                /* 主题保存失败不影响使用 */
              });
            }}
          >
            {theme === "dark" ? "☀" : "◐"}
          </button>
          <button className="win-btn" title="最小化" onClick={() => window.stent.windowAction("minimize")}>
            —
          </button>
          <button
            className="win-btn"
            title="最大化"
            onClick={() => window.stent.windowAction("toggle-maximize")}
          >
            ▢
          </button>
          <button
            className="win-btn win-btn--close"
            title="关闭"
            onClick={() => window.stent.windowAction("close")}
          >
            ✕
          </button>
        </div>
      </header>

      <div className="body">
        <nav className="sidebar">
          {NAV.map((item) => (
            <button
              key={item.key}
              className={`nav-item${route === item.key ? " active" : ""}`}
              onClick={() => setRoute(item.key)}
            >
              <span className="nav-icon">{item.icon}</span>
              {item.label}
            </button>
          ))}
          <div className="sidebar__gap" />
          <button
            className={`nav-item${route === "settings" ? " active" : ""}`}
            onClick={() => setRoute("settings")}
          >
            <span className="nav-icon">⚙</span>
            设置
          </button>
          <div className="sidebar__meta">v{meta?.version ?? "—"}</div>
        </nav>

        <main className="view">
          <Page />
        </main>
      </div>

      <div className="toast-host">
        {toasts.map((t) => (
          <div key={t.id} className="toast" data-level={t.level} onClick={() => dismiss(t.id)}>
            {t.text}
          </div>
        ))}
      </div>
    </div>
  );
}

function StatusPill() {
  const status = useStore((s) => s.status);
  return (
    <span className="status-pill" data-level={status.level} title={status.text}>
      <span className="status-pill__dot" />
      <span>{status.text}</span>
    </span>
  );
}
