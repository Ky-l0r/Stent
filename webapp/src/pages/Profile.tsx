import { useEffect, useState } from "react";

import { api } from "../api";
import { useStore } from "../store";

interface Dimension {
  key: string;
  label: string;
  hint: string;
  placeholder: string;
}

interface MemoryEntry {
  text?: string;
  at?: string;
}

interface ProfileResponse {
  profile: Record<string, unknown> & { memory?: MemoryEntry[] };
  dimensions: Dimension[];
  completeness: number;
  missing: string[];
}

const EMPTY: ProfileResponse = { profile: {}, dimensions: [], completeness: 0, missing: [] };

export default function ProfilePage() {
  const toast = useStore((s) => s.toast);
  const setStatus = useStore((s) => s.setStatus);

  const [data, setData] = useState<ProfileResponse>(EMPTY);
  const [form, setForm] = useState<Record<string, string>>({});
  const [intro, setIntro] = useState("");
  const [memoryText, setMemoryText] = useState("");
  const [drafting, setDrafting] = useState(false);

  const applyData = (next: ProfileResponse) => {
    setData(next);
    const editable = next.dimensions.filter((d) => d.key !== "memory");
    const values: Record<string, string> = {};
    for (const dim of editable) {
      const raw = next.profile[dim.key];
      values[dim.key] = Array.isArray(raw) ? raw.join("、") : String(raw ?? "");
    }
    setForm(values);
  };

  useEffect(() => {
    (async () => {
      try {
        applyData(await api.get<ProfileResponse>("/api/profile"));
      } catch (error) {
        toast(error instanceof Error ? error.message : String(error), "error");
      }
    })();
  }, [toast]);

  async function save() {
    const payload: Record<string, unknown> = {};
    for (const [key, value] of Object.entries(form)) {
      payload[key] =
        key === "platforms"
          ? value.split(/[、,，\n]+/).map((s) => s.trim()).filter(Boolean)
          : value;
    }
    try {
      applyData(await api.put<ProfileResponse>("/api/profile", payload));
      setStatus("画像已保存", "ok");
      toast("画像已保存", "ok");
    } catch (error) {
      toast(error instanceof Error ? error.message : String(error), "error");
    }
  }

  async function draft() {
    if (!intro.trim()) return toast("先粘贴一段自我介绍", "warn");
    setDrafting(true);
    try {
      const res = await api.post<{ draft: Record<string, string> }>("/api/profile/draft", {
        text: intro,
      });
      setForm((prev) => ({ ...prev, ...res.draft }));
      toast("草稿已填入表单，确认后点保存", "ok");
    } catch (error) {
      toast(error instanceof Error ? error.message : String(error), "error");
    } finally {
      setDrafting(false);
    }
  }

  const memory = data.profile.memory ?? [];

  return (
    <>
      <div className="page-head">
        <div>
          <h1 className="page-title">账号画像</h1>
          <p className="page-desc">画像会作为高优先级上下文注入每一次创作</p>
        </div>
        <div className="page-actions">
          <span className="muted">完整度 {data.completeness}%</span>
          <button className="btn btn--primary" onClick={() => void save()}>
            保存画像
          </button>
        </div>
      </div>

      <div className="card">
        <progress value={data.completeness} max={100} style={{ width: "100%", height: 6 }} />
        <p className="card__hint" style={{ marginTop: 8 }}>
          {data.missing.length ? `还缺：${data.missing.join("、")}` : "六个维度都填好了"}
        </p>
      </div>

      <div className="card">
        <h2 className="card__title">AI 生成画像草稿</h2>
        <p className="card__hint">粘贴一段自我介绍，让模型整理成结构化画像；生成后先预览，确认再保存。</p>
        <textarea
          rows={4}
          value={intro}
          placeholder="例：我是一二线城市的职场穿搭博主，主要在小红书更新通勤穿搭…"
          onChange={(e) => setIntro(e.target.value)}
        />
        <div style={{ marginTop: 10 }}>
          <button className="btn" onClick={() => void draft()} disabled={drafting}>
            {drafting ? "生成中…" : "生成草稿"}
          </button>
        </div>
      </div>

      <div className="card">
        <h2 className="card__title">六维画像</h2>
        {data.dimensions
          .filter((d) => d.key !== "memory")
          .map((dim) => (
            <label className="field" key={dim.key}>
              <span>
                {dim.label} · <span className="muted">{dim.hint}</span>
              </span>
              <textarea
                rows={3}
                placeholder={dim.placeholder}
                value={form[dim.key] ?? ""}
                onChange={(e) => setForm((prev) => ({ ...prev, [dim.key]: e.target.value }))}
              />
            </label>
          ))}
      </div>

      <div className="card">
        <h2 className="card__title">记忆（来自数据分析回流）</h2>
        {memory.length === 0 && <div className="empty" style={{ padding: "14px 0" }}>还没有记忆条目</div>}
        {memory
          .slice(-12)
          .reverse()
          .map((entry, index) => (
            <div className="row" key={index}>
              <span className="row__title" style={{ whiteSpace: "normal" }}>
                {entry.text ?? ""}
              </span>
              <span className="row__meta">{entry.at ?? ""}</span>
            </div>
          ))}
        <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
          <input
            type="text"
            placeholder="手动补一条经验，例：标题带数字的笔记收藏率更高"
            value={memoryText}
            onChange={(e) => setMemoryText(e.target.value)}
          />
          <button
            className="btn"
            onClick={async () => {
              if (!memoryText.trim()) return;
              try {
                applyData(await api.post<ProfileResponse>("/api/profile/memory", { text: memoryText }));
                setMemoryText("");
                toast("已追加", "ok");
              } catch (error) {
                toast(error instanceof Error ? error.message : String(error), "error");
              }
            }}
          >
            追加
          </button>
          <button
            className="btn btn--ghost"
            onClick={async () => {
              try {
                applyData(await api.del<ProfileResponse>("/api/profile/memory"));
                toast("记忆已清空", "ok");
              } catch (error) {
                toast(error instanceof Error ? error.message : String(error), "error");
              }
            }}
          >
            清空
          </button>
        </div>
      </div>
    </>
  );
}
