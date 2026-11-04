import { useEffect, useRef, useState } from "react";

import { api, stream } from "../api";
import { useStore } from "../store";

interface CreationResult {
  body: string;
  titles: string[];
  summary: string;
  tags: string[];
  hint: string;
}

interface QualityReport {
  score?: number;
  hook?: number;
  platform_fit?: number;
  value?: number;
  human?: number;
  issues?: string[];
  suggestions?: string[];
}

const EMPTY: CreationResult = { body: "", titles: [], summary: "", tags: [], hint: "" };

export default function CreatePage() {
  const meta = useStore((s) => s.meta);
  const toast = useStore((s) => s.toast);
  const setStatus = useStore((s) => s.setStatus);
  const setRoute = useStore((s) => s.setRoute);
  const setPublishDraft = useStore((s) => s.setPublishDraft);
  const consumePendingTopic = useStore((s) => s.consumePendingTopic);

  const [platform, setPlatform] = useState("xiaohongshu");
  const [topic, setTopic] = useState("");
  const [goal, setGoal] = useState("互动涨粉");
  const [audience, setAudience] = useState("");
  const [tone, setTone] = useState("");
  const [extra, setExtra] = useState("");
  const [source, setSource] = useState("");

  const [result, setResult] = useState<CreationResult>(EMPTY);
  const [running, setRunning] = useState(false);
  const [report, setReport] = useState<QualityReport | null>(null);
  const ctrl = useRef<AbortController | null>(null);
  // 标题是逐行流出来的：先攒原始文本，再整体解析，避免在 setState 回调里再 setState
  const titlesBuf = useRef("");

  useEffect(() => {
    const pending = consumePendingTopic();
    if (pending) setTopic(pending);
  }, [consumePendingTopic]);

  const platforms = meta?.creation_platforms ?? [];
  const llmReady = meta?.llm_ready ?? false;

  const parseTitles = (text: string) =>
    text
      .split("\n")
      .map((line) => line.replace(/^\s*(?:\d+[.、)．]|[-*·•])\s*/, "").trim())
      .filter(Boolean)
      .slice(0, 5);

  async function run() {
    if (!topic.trim()) return toast("请先填写主题", "warn");
    if (running) return;

    setRunning(true);
    ctrl.current = new AbortController();
    setResult(EMPTY);
    titlesBuf.current = "";
    setReport(null);
    setStatus("正在生成…", "busy");

    try {
      await stream(
        "/api/create/stream",
        {
          topic,
          platform,
          goal,
          audience,
          tone,
          extra,
          source_text: source,
        },
        {
          signal: ctrl.current.signal,
          onEvent: (name, data) => {
            if (name === "delta") {
              const section = String(data.section ?? "");
              const text = String(data.text ?? "");
              if (section === "标题") {
                titlesBuf.current += text;
                const parsed = parseTitles(titlesBuf.current);
                setResult((r) => ({ ...r, titles: parsed }));
              } else if (section === "简介") {
                setResult((r) => ({ ...r, summary: r.summary + text }));
              } else if (section === "标签") {
                setResult((r) => ({ ...r, tags: Array.from(new Set((r.tags.join(" ") + " " + text).split(/\s+/).filter(Boolean))) }));
              } else {
                setResult((r) => ({ ...r, body: r.body + text }));
              }
            } else if (name === "done") {
              const payload = (data.result ?? {}) as Record<string, unknown>;
              setResult({
                body: String(payload.body ?? ""),
                titles: (payload.titles as string[]) ?? [],
                summary: String(payload.summary ?? ""),
                tags: (payload.tags as string[]) ?? [],
                hint: String(data.hint ?? ""),
              });
              setStatus("生成完成", "ok");
            } else if (name === "error") {
              setStatus("生成失败", "error");
              toast(String(data.message ?? "生成失败"), "error");
            }
          },
        }
      );
    } catch (error) {
      const aborted = error instanceof DOMException && error.name === "AbortError";
      const message = error instanceof Error ? error.message : String(error);
      setStatus(aborted ? "已中断" : "生成失败", aborted ? "warn" : "error");
      toast(aborted ? "已中断，已生成的部分保留" : message, aborted ? "warn" : "error");
    } finally {
      setRunning(false);
      ctrl.current = null;
    }
  }

  async function retitle() {
    if (!result.body.trim()) return toast("先生成正文", "warn");
    try {
      const data = await api.post<{ titles: string[] }>("/api/create/titles", {
        topic: topic || "未命名主题",
        platform,
        body: result.body,
        count: 3,
      });
      setResult((r) => ({ ...r, titles: data.titles }));
      toast("标题已更新", "ok");
    } catch (error) {
      toast(error instanceof Error ? error.message : String(error), "error");
    }
  }

  async function quality() {
    if (!result.body.trim()) return toast("先生成正文", "warn");
    try {
      const data = await api.post<{ report: QualityReport }>("/api/create/quality", {
        platform,
        body: result.body,
        titles: result.titles,
        summary: result.summary,
        tags: result.tags,
      });
      setReport(data.report);
    } catch (error) {
      toast(error instanceof Error ? error.message : String(error), "error");
    }
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1 className="page-title">内容创作</h1>
          <p className="page-desc">按平台体例产出正文、标题候选、简介与标签</p>
        </div>
        <div className="page-actions">
          {!llmReady && (
            <button className="btn btn--sm" onClick={() => setRoute("settings")}>
              未配置模型，去设置
            </button>
          )}
          <button className="btn" onClick={() => ctrl.current?.abort()} disabled={!running}>
            中断
          </button>
          <button className="btn btn--primary" onClick={() => void run()} disabled={running}>
            {running ? "生成中…" : "生成"}
          </button>
        </div>
      </div>

      <div className="grid-side">
        <div className="card">
          <label className="field">
            <span>主题 / 热点</span>
            <input
              type="text"
              placeholder="例如：通勤穿搭怎么不显胖"
              value={topic}
              onChange={(e) => setTopic(e.target.value)}
            />
          </label>

          <div className="field-row">
            <label className="field">
              <span>目标平台</span>
              <select value={platform} onChange={(e) => setPlatform(e.target.value)}>
                {platforms.map((p) => (
                  <option key={p.key} value={p.key}>
                    {p.label} · {p.content_type}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>内容目标</span>
              <input type="text" value={goal} onChange={(e) => setGoal(e.target.value)} />
            </label>
          </div>

          <details>
            <summary className="muted" style={{ cursor: "pointer", fontSize: 12.5 }}>
              受众 / 调性 / 补充要求
            </summary>
            <div style={{ marginTop: 10 }}>
              <label className="field">
                <span>目标受众</span>
                <input
                  type="text"
                  placeholder="例：一二线城市职场女性"
                  value={audience}
                  onChange={(e) => setAudience(e.target.value)}
                />
              </label>
              <label className="field">
                <span>期望调性</span>
                <input
                  type="text"
                  placeholder="例：口语化、真诚、不喊口号"
                  value={tone}
                  onChange={(e) => setTone(e.target.value)}
                />
              </label>
              <label className="field">
                <span>补充要求</span>
                <textarea rows={2} value={extra} onChange={(e) => setExtra(e.target.value)} />
              </label>
              <label className="field">
                <span>素材原文（可选）</span>
                <textarea rows={3} value={source} onChange={(e) => setSource(e.target.value)} />
              </label>
            </div>
          </details>
        </div>

        <div>
          <div className="card">
            <h2 className="card__title">
              正文 <span className="muted">（{result.body.length} 字）</span>
            </h2>
            <textarea
              rows={12}
              value={result.body}
              placeholder="生成结果会流式出现在这里，也可以直接手写"
              onChange={(e) => setResult((r) => ({ ...r, body: e.target.value }))}
            />
            <div style={{ display: "flex", gap: 8, marginTop: 10, flexWrap: "wrap" }}>
              <button className="btn btn--sm" onClick={() => void quality()}>
                质量自检
              </button>
              <button className="btn btn--sm" onClick={() => void retitle()}>
                换标题
              </button>
              <button
                className="btn btn--sm"
                onClick={() => {
                  const tags = result.tags.map((t) => (t.startsWith("#") ? t : `#${t}`)).join(" ");
                  void navigator.clipboard.writeText([result.body, tags].filter(Boolean).join("\n\n"));
                  toast("已复制", "ok");
                }}
              >
                复制全文
              </button>
              <button
                className="btn btn--sm"
                onClick={() => {
                  if (!result.body.trim()) return toast("还没有正文", "warn");
                  setPublishDraft({
                    platform,
                    title: result.titles[0] ?? "",
                    titles: result.titles,
                    body: result.body,
                    tags: result.tags,
                    summary: result.summary,
                  });
                  toast("已送入发布中心", "ok");
                  setRoute("publish");
                }}
              >
                送发布中心
              </button>
            </div>
            {result.hint && <p className="muted" style={{ fontSize: 12, margin: "10px 0 0" }}>{result.hint}</p>}
          </div>

          <div className="card">
            <h2 className="card__title">标题候选</h2>
            {result.titles.length === 0 && <div className="empty" style={{ padding: "14px 0" }}>还没有标题候选</div>}
            {result.titles.map((title, index) => (
              <div className="row" key={`${title}-${index}`}>
                <span className="row__rank">{index + 1}</span>
                <span className="row__title">{title}</span>
                <span className="row__actions">
                  <button
                    className="btn btn--sm btn--ghost"
                    onClick={() => {
                      void navigator.clipboard.writeText(title);
                      toast("已复制", "ok");
                    }}
                  >
                    复制
                  </button>
                </span>
              </div>
            ))}
          </div>

          <div className="card">
            <h2 className="card__title">简介</h2>
            <textarea
              rows={3}
              value={result.summary}
              onChange={(e) => setResult((r) => ({ ...r, summary: e.target.value }))}
            />
            <h2 className="card__title" style={{ marginTop: 12 }}>
              标签
            </h2>
            <input
              type="text"
              placeholder="空格分隔，例：通勤穿搭 平价好物"
              value={result.tags.join(" ")}
              onChange={(e) =>
                setResult((r) => ({ ...r, tags: e.target.value.split(/\s+/).filter(Boolean) }))
              }
            />
          </div>

          {report && (
            <div className="card">
              <h2 className="card__title">质量自检</h2>
              <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: 10 }}>
                <span className="score">{report.score ?? 0}</span>
                <span className="muted">
                  / 100 · 钩子 {report.hook ?? "-"} · 平台 {report.platform_fit ?? "-"} · 价值{" "}
                  {report.value ?? "-"} · 真人感 {report.human ?? "-"}
                </span>
              </div>
              {(report.issues ?? []).map((issue, index) => (
                <div className="check check--warn" key={`issue-${index}`}>
                  <div>
                    <div className="check__head">问题</div>
                    <div className="check__body">{issue}</div>
                  </div>
                </div>
              ))}
              {(report.suggestions ?? []).map((tip, index) => (
                <div className="check check--info" key={`tip-${index}`}>
                  <div>
                    <div className="check__head">建议</div>
                    <div className="check__body">{tip}</div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </>
  );
}
