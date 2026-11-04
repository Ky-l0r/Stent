import { useEffect, useState } from "react";

import { api } from "../api";
import { useStore, type Meta } from "../store";

interface Preset {
  key: string;
  label: string;
  base_url: string;
  model: string;
}

interface SettingsResponse {
  llm: {
    provider: string;
    base_url: string;
    model: string;
    temperature: number;
    max_tokens: number;
  };
  theme: string;
  key_set: boolean;
  key_mode: string;
  presets: Preset[];
}

export default function SettingsPage() {
  const toast = useStore((s) => s.toast);
  const setStatus = useStore((s) => s.setStatus);
  const setTheme = useStore((s) => s.setTheme);
  const setMeta = useStore((s) => s.setMeta);

  const [data, setData] = useState<SettingsResponse | null>(null);
  const [provider, setProvider] = useState("deepseek");
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [temperature, setTemperature] = useState(0.7);
  const [maxTokens, setMaxTokens] = useState(4096);
  const [testResult, setTestResult] = useState<{ ok: boolean; text: string } | null>(null);
  const [testing, setTesting] = useState(false);

  const apply = (next: SettingsResponse) => {
    setData(next);
    setProvider(next.llm.provider);
    setBaseUrl(next.llm.base_url);
    setModel(next.llm.model);
    setTemperature(next.llm.temperature);
    setMaxTokens(next.llm.max_tokens);
  };

  useEffect(() => {
    (async () => {
      try {
        apply(await api.get<SettingsResponse>("/api/settings"));
      } catch (error) {
        toast(error instanceof Error ? error.message : String(error), "error");
      }
    })();
  }, [toast]);

  async function save(): Promise<boolean> {
    try {
      const payload: Record<string, unknown> = {
        llm: {
          provider,
          base_url: baseUrl.trim(),
          model: model.trim(),
          temperature,
          max_tokens: maxTokens,
        },
      };
      if (apiKey.trim()) payload.api_key = apiKey.trim();
      apply(await api.put<SettingsResponse>("/api/settings", payload));
      setApiKey("");
      setStatus("配置已保存", "ok");
      const meta = await api.get<Meta>("/api/meta");
      setMeta(meta);
      return true;
    } catch (error) {
      toast(error instanceof Error ? error.message : String(error), "error");
      return false;
    }
  }

  async function test() {
    setTesting(true);
    setTestResult(null);
    try {
      // 先保存再测：否则测的是旧配置，容易误导
      if (!(await save())) return;
      const res = await api.post<{ ok: boolean; message: string; sample?: string }>(
        "/api/settings/test",
        {}
      );
      setTestResult({ ok: res.ok, text: res.ok ? `${res.message}　返回：${res.sample ?? ""}` : res.message });
    } catch (error) {
      setTestResult({ ok: false, text: error instanceof Error ? error.message : String(error) });
    } finally {
      setTesting(false);
    }
  }

  const presets = data?.presets ?? [];

  return (
    <>
      <div className="page-head">
        <div>
          <h1 className="page-title">设置</h1>
          <p className="page-desc">只需要三项：接口地址、密钥、模型名称</p>
        </div>
        <div className="page-actions">
          <button className="btn" onClick={() => void test()} disabled={testing}>
            {testing ? "测试中…" : "测试连通性"}
          </button>
          <button
            className="btn btn--primary"
            onClick={async () => {
              if (await save()) toast("配置已保存", "ok");
            }}
          >
            保存
          </button>
        </div>
      </div>

      <div className="card">
        <h2 className="card__title">LLM 接入</h2>
        <label className="field">
          <span>服务商</span>
          <select
            value={provider}
            onChange={(e) => {
              setProvider(e.target.value);
              const preset = presets.find((p) => p.key === e.target.value);
              if (preset) {
                if (preset.base_url) setBaseUrl(preset.base_url);
                if (preset.model) setModel(preset.model);
              }
            }}
          >
            {presets.map((p) => (
              <option key={p.key} value={p.key}>
                {p.label}
              </option>
            ))}
          </select>
        </label>

        <label className="field">
          <span>API Base URL</span>
          <input
            type="text"
            value={baseUrl}
            placeholder="https://api.deepseek.com/v1"
            onChange={(e) => setBaseUrl(e.target.value)}
          />
        </label>

        <div className="field-row">
          <label className="field">
            <span>模型名称</span>
            <input type="text" value={model} placeholder="deepseek-chat" onChange={(e) => setModel(e.target.value)} />
          </label>
          <label className="field">
            <span>
              API Key{" "}
              {data?.key_set && <span className="muted">（已保存，留空表示不改）</span>}
            </span>
            <input
              type="password"
              value={apiKey}
              placeholder={data?.key_set ? "••••••••（已保存）" : "sk-..."}
              autoComplete="off"
              onChange={(e) => setApiKey(e.target.value)}
            />
          </label>
        </div>

        <div className="field-row">
          <label className="field">
            <span>温度（0-2）</span>
            <input
              type="number"
              step={0.1}
              min={0}
              max={2}
              value={temperature}
              onChange={(e) => setTemperature(Number(e.target.value))}
            />
          </label>
          <label className="field">
            <span>单次最大 token</span>
            <input
              type="number"
              min={256}
              step={256}
              value={maxTokens}
              onChange={(e) => setMaxTokens(Number(e.target.value))}
            />
          </label>
        </div>

        <p className="card__hint">密钥存储方式：{data?.key_mode ?? "未知"}</p>
        {testResult && (
          <p style={{ fontSize: 12.5, color: testResult.ok ? "var(--ok)" : "var(--danger)", margin: 0 }}>
            {testResult.text}
          </p>
        )}
      </div>

      <div className="card">
        <h2 className="card__title">外观</h2>
        <div style={{ display: "flex", gap: 8 }}>
          <button
            className="btn"
            onClick={() => {
              setTheme("light");
              void api.put("/api/settings", { theme: "light" });
            }}
          >
            浅色
          </button>
          <button
            className="btn"
            onClick={() => {
              setTheme("dark");
              void api.put("/api/settings", { theme: "dark" });
            }}
          >
            暗色
          </button>
        </div>
      </div>
    </>
  );
}
