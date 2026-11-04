import { useEffect, useState } from "react";

import { api } from "../api";
import { useStore } from "../store";

interface CheckItem {
  level: string;
  title: string;
  detail: string;
  suggestion?: string;
}

interface PrepareResponse {
  platform: string;
  label: string;
  preview: string;
  notes: string[];
  checks: { passed: boolean; score: number; items: CheckItem[] };
  supported: boolean;
  support_note: string;
}

export default function PublishPage() {
  const meta = useStore((s) => s.meta);
  const draft = useStore((s) => s.publishDraft);
  const setStatus = useStore((s) => s.setStatus);
  const toast = useStore((s) => s.toast);

  const platforms = meta?.publish_platforms ?? [];
  const [platform, setPlatform] = useState(platforms[0]?.key ?? "xiaohongshu");
  const [title, setTitle] = useState(draft?.title ?? "");
  const [body, setBody] = useState(draft?.body ?? "");
  const [summary, setSummary] = useState(draft?.summary ?? "");
  const [tags, setTags] = useState((draft?.tags ?? []).join(" "));
  const [media, setMedia] = useState<string[]>([]);
  const [result, setResult] = useState<PrepareResponse | null>(null);
  const [checking, setChecking] = useState(false);

  useEffect(() => {
    if (draft) {
      setTitle(draft.title);
      setBody(draft.body);
      setSummary(draft.summary);
      setTags(draft.tags.join(" "));
      setPlatform(draft.platform);
    }
  }, [draft]);

  async function prepare() {
    if (!body.trim()) return toast("正文为空，先写点内容", "warn");
    setChecking(true);
    setStatus("正在执行发布前检查…", "busy");
    try {
      const data = await api.post<PrepareResponse>("/api/publish/prepare", {
        platform,
        title,
        body,
        tags: tags.split(/\s+/).filter(Boolean),
        summary,
        media,
      });
      setResult(data);
      setStatus(
        data.checks?.passed ? `检查通过 · ${data.checks.score} 分` : "检查未通过",
        data.checks?.passed ? "ok" : "error"
      );
    } catch (error) {
      setStatus("检查失败", "error");
      toast(error instanceof Error ? error.message : String(error), "error");
    } finally {
      setChecking(false);
    }
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1 className="page-title">发布中心</h1>
          <p className="page-desc">选平台 → 格式适配 → 发布前检查 → 预览</p>
        </div>
        <div className="page-actions">
          <button
            className="btn"
            onClick={async () => {
              const picked = await window.stent.pickFiles("media");
              setMedia(picked);
              if (picked.length) toast(`已选择 ${picked.length} 个文件`, "ok");
            }}
          >
            选择媒体
          </button>
          <button className="btn btn--primary" onClick={() => void prepare()} disabled={checking}>
            {checking ? "检查中…" : "检查并预览"}
          </button>
        </div>
      </div>

      <div className="banner banner--info">
        <span>当前阶段只做「检查 + 预览」，不会向平台提交任何内容。</span>
        <span className="muted">自动发布在 P2 接入</span>
      </div>

      <div className="grid-side">
        <div className="card">
          <label className="field">
            <span>目标平台</span>
            <select value={platform} onChange={(e) => setPlatform(e.target.value)}>
              {platforms.map((p) => (
                <option key={p.key} value={p.key}>
                  {p.label}
                  {p.available ? "" : "（适配器不可用）"}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>标题</span>
            <input type="text" value={title} onChange={(e) => setTitle(e.target.value)} />
          </label>
          <label className="field">
            <span>正文</span>
            <textarea
              rows={10}
              value={body}
              placeholder="可粘贴，或从创作页「送发布中心」带过来"
              onChange={(e) => setBody(e.target.value)}
            />
          </label>
          <div className="field-row">
            <label className="field">
              <span>简介</span>
              <input type="text" value={summary} onChange={(e) => setSummary(e.target.value)} />
            </label>
            <label className="field">
              <span>标签（空格分隔）</span>
              <input type="text" value={tags} onChange={(e) => setTags(e.target.value)} />
            </label>
          </div>
          <p className="muted mono" style={{ margin: 0 }}>
            {media.length ? media.map((p) => p.split(/[\\/]/).pop()).join("、") : "未选择媒体"}
          </p>
        </div>

        <div className="card">
          {!result && <div className="empty">点右上角「检查并预览」看结果</div>}
          {result && (
            <>
              <h2 className="card__title">检查结果</h2>
              <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: 12 }}>
                <span className="score">{result.checks?.score ?? 0}</span>
                <span className="muted">/ 100 · {result.checks?.passed ? "通过" : "未通过"}</span>
              </div>
              {!result.supported && result.support_note && (
                <div className="banner">{result.support_note}</div>
              )}
              {result.notes.length > 0 && <div className="banner">{result.notes.join("；")}</div>}
              {(result.checks?.items ?? []).map((item, index) => {
                const level = item.level === "error" ? "error" : item.level === "warn" ? "warn" : "info";
                const label = level === "error" ? "必须修改" : level === "warn" ? "建议修改" : "提示";
                return (
                  <div className={`check check--${level}`} key={`${item.title}-${index}`}>
                    <div>
                      <div className="check__head">
                        {label} · {item.title}
                      </div>
                      <div className="check__body">
                        {item.detail}
                        {item.suggestion ? <><br />建议：{item.suggestion}</> : null}
                      </div>
                    </div>
                  </div>
                );
              })}
              {!(result.checks?.items ?? []).length && <div className="empty">没有发现问题</div>}
              <h2 className="card__title" style={{ marginTop: 16 }}>
                预览（{result.label || result.platform}）
              </h2>
              <div className="preview">{result.preview}</div>
            </>
          )}
        </div>
      </div>
    </>
  );
}
