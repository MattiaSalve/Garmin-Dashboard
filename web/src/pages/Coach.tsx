import { useEffect, useState } from "react";
import { api, useFetch } from "../api";
import { Banner } from "../components";

interface Advice {
  summary: string;
  agree_with_suggestion: boolean;
  adjusted_steps: { minutes: number; pct_ftp_low: number; pct_ftp_high: number; label: string }[] | null;
  cautions: string[];
}

const KEY = "openrouter_key"; // kept for this browser tab only, never sent anywhere but the coach call

export function Coach({ minutes, ftp }: { minutes: number; ftp: number }) {
  const models = useFetch<{ models: string[]; default: string }>("/coach/models");
  const [model, setModel] = useState("");
  const [custom, setCustom] = useState("");
  const [apiKey, setApiKey] = useState(() => sessionStorage.getItem(KEY) ?? "");
  const [advice, setAdvice] = useState<Advice | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (models.data && !model) setModel(models.data.default);
  }, [models.data, model]);
  useEffect(() => setAdvice(null), [minutes, model, custom]);

  const chosen = model === "other" ? custom.trim() : model;

  async function ask() {
    sessionStorage.setItem(KEY, apiKey);
    setBusy(true);
    setError(null);
    try {
      setAdvice(await api<Advice>("/coach", { body: { minutes, model: chosen || null, api_key: apiKey } }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <details>
      <summary>AI coach</summary>
      <div className="row" style={{ margin: "12px 0" }}>
        <label className="field">OpenRouter API key
          <input type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)} autoComplete="off" placeholder="sk-or-…"
            title="Your own key from openrouter.ai/keys. Kept in this browser tab only." />
        </label>
        <label className="field">Model
          <select value={model} onChange={(e) => setModel(e.target.value)}>
            {models.data?.models.map((m) => <option key={m}>{m}</option>)}
            <option value="other">Other…</option>
          </select>
        </label>
        {model === "other" ? (
          <label className="field">Model id
            <input value={custom} onChange={(e) => setCustom(e.target.value)} placeholder="provider/model-name" />
          </label>
        ) : null}
        <button disabled={busy || (model === "other" && !chosen)} onClick={ask}>{busy ? "Asking…" : "Ask coach"}</button>
      </div>
      <p className="muted">Sends a summary of the last 7 days of recovery and training numbers (no names, places or account details) to OpenRouter, using your key.</p>
      {error ? <Banner kind="error">{error}</Banner> : null}
      {advice ? (
        <div className="card">
          <b>Coach · {advice.agree_with_suggestion ? "agrees with" : "suggests changing"} the plan</b>
          <p>{advice.summary}</p>
          <ul>{advice.cautions.map((c) => <li key={c}>{c}</li>)}</ul>
          {advice.adjusted_steps ? (
            <table>
              <thead><tr><th>Step</th><th className="num">Min</th><th className="num">Watts</th><th className="num">% FTP</th></tr></thead>
              <tbody>
                {advice.adjusted_steps.map((s, i) => (
                  <tr key={i}>
                    <td>{s.label}</td><td className="num">{Math.round(s.minutes)}</td>
                    <td className="num">{Math.round((s.pct_ftp_low * ftp) / 100)}–{Math.round((s.pct_ftp_high * ftp) / 100)}</td>
                    <td className="num">{s.pct_ftp_low}–{s.pct_ftp_high}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : null}
        </div>
      ) : null}
    </details>
  );
}
