import { useCallback, useEffect, useMemo, useState } from "react";

import { api } from "../api";
import { useStore } from "../store";

interface HotItem {
  platform: string;
  title: string;
  rank: number;
  url: string;
  heat: string;
  keyword: string;
  category: string;
}

interface PlatformRow {
  key: string;
  label: string;
  count: number;
  stale: boolean;
  error: string;
}

interface HotResponse {
  items: HotItem[];
  platforms: PlatformRow[];
  categories: { name: string; count: number }[];
}

const PLATFORM_COLORS: Record<string, string> = {
  weibo: "#e6162d",
  douyin: "#fe2c55",
  zhihu: "#0f88eb",
  bilibili: "#fb7299",
  baidu: "#2932e1",
  toutiao: "#f04142",
  rednote: "#ff2e4d",
  "it-news": "#4a5568",
  "60s": "#6e6880",
};

export default function HotPage() {
  const setRoute = useStore((s) => s.setRoute);
  const setPendingTopic = useStore((s) => s.setPendingTopic);
  const setStatus = useStore((s) => s.setStatus);
  const toast = useStore((s) => s.toast);

  const [data, setData] = useState<HotResponse>({ items: [], platforms: [], categories: [] });
  const [keyword, setKeyword] = useState("");
  const [platforms, setPlatforms] = useState<Set<string>>(new Set());
  const [category, setCategory] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(
    async (force: boolean) => {
      setLoading(true);
      setStatus(force ? "正在强制刷新各平台热榜…" : "正在并发拉取各平台热榜…", "busy");
      try {
        const next = await api.get<HotResponse>(`/api/hot${force ? "?force=1" : ""}`);
        setData(next);
        setError("");
        const okCount = next.platforms.filter((p) => p.count > 0).length;
        const stale = next.platforms.filter((p) => p.stale).length;
        setStatus(
          `${okCount} 平台正常${stale ? ` · ${stale} 个用的旧缓存` : ""} · ${next.items.length} 条`,
          stale ? "warn" : "ok"
        );
      } catch (e) {
        const message = e instanceof Error ? e.message : String(e);
        setError(message);
        setStatus("热榜拉取失败", "error");
      } finally {
        setLoading(false);
      }
    },
    [setStatus]
  );

  useEffect(() => {
    void load(false);
  }, [load]);

  const items = useMemo(() => {
    const key = keyword.trim().toLowerCase();
    return data.items.filter((item) => {
      if (platforms.size && !platforms.has(item.platform)) return false;
      if (category && (item.category || "综合") !== category) return false;
      if (key && !`${item.title} ${item.keyword}`.toLowerCase().includes(key)) return false;
      return true;
    });
  }, [data.items, keyword, platforms, category]);

  const togglePlatform = (key: string) => {
    setPlatforms((prev) => {
      const next = new Set(prev);
      next.has(key) ? next.delete(key) : next.add(key);
      return next;
    });
  };

  return (
    <>
      <div className="page-head">
        <div>
          <h1 className="page-title">热点发现</h1>
          <p className="page-desc">多平台并发聚合，双击任意一行送进创作</p>
        </div>
        <div className="page-actions">
          <input
            type="text"
            placeholder="搜索标题…"
            style={{ width: 200 }}
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
          />
          <button className="btn" onClick={() => void load(true)} disabled={loading}>
            {loading ? "拉取中…" : "刷新"}
          </button>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 12 }}>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 10 }}>
          {data.platforms.map((p) => (
            <button
              key={p.key}
              className={`chip${platforms.has(p.key) ? " active" : ""}`}
              title={p.error}
              onClick={() => togglePlatform(p.key)}
            >
              {p.label} {p.count}
              {p.stale ? " ⚠" : ""}
            </button>
          ))}
          {!data.platforms.length && <span className="muted">正在加载平台…</span>}
        </div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
          <button
            className={`chip${category === "" ? " active" : ""}`}
            onClick={() => setCategory("")}
          >
            全部垂类 {data.items.length}
          </button>
          {data.categories.map((c) => (
            <button
              key={c.name}
              className={`chip${category === c.name ? " active" : ""}`}
              onClick={() => setCategory(category === c.name ? "" : c.name)}
            >
              {c.name} {c.count}
            </button>
          ))}
        </div>
      </div>

      <div className="card">
        {error && <div className="empty">拉取失败：{error}</div>}
        {!error && loading && !items.length && <div className="loading">正在拉取各平台热榜…</div>}
        {!error && !loading && !items.length && (
          <div className="empty">没有匹配的热点，换个关键词或取消筛选试试</div>
        )}
        {items.map((item, index) => (
          <div
            key={`${item.platform}-${item.rank}-${index}`}
            className="row"
            onDoubleClick={() => {
              setPendingTopic(item.title);
              toast("已送入创作", "ok");
              setRoute("create");
            }}
          >
            <span className="row__rank">{item.rank || ""}</span>
            <span
              className="row__dot"
              style={{ background: PLATFORM_COLORS[item.platform] ?? "var(--muted)" }}
            />
            <span className="row__title">{item.title}</span>
            <span className="row__meta">{item.heat}</span>
            <span className="row__actions">
              <button
                className="btn btn--sm btn--ghost"
                onClick={() => {
                  navigator.clipboard.writeText(item.title).then(
                    () => toast("已复制", "ok"),
                    () => toast("复制失败", "warn")
                  );
                }}
              >
                复制
              </button>
              <button
                className="btn btn--sm btn--ghost"
                onClick={() => {
                  if (item.url) void window.stent.openExternal(item.url);
                  else toast("这条没有原链接", "warn");
                }}
              >
                原文
              </button>
            </span>
          </div>
        ))}
      </div>
    </>
  );
}
