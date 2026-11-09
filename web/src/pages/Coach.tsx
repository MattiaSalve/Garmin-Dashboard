import { useEffect, useState } from "react";
import { api, useFetch } from "../api";
import { Banner } from "../components";

interface Advice {
  summary: string;
  agree_with_suggestion: boolean;
  adjusted_steps: { minutes: number; pct_ftp_low: number; pct_ftp_high: number; label: string }[] | null;
  cautions: string[];
}

// One turn of the conversation. Assistant turns keep the parsed advice for display; the raw text is what goes back to the model.
type Turn = { role: "user"; content: string } | { role: "assistant"; content: string; advice: Advice };

const KEY = "openrouter_key"; // kept for this browser tab only, never sent anywhere but the coach call

export function Coach({ minutes, ftp, sport, thresholdPace, paceUnit }: { minutes: number; ftp: number; sport: string; thresholdPace: number | null; paceUnit: string }) {
  const models = useFetch<{ models: string[]; default: string }>("/coach/models");
  const [model, setModel] = useState("");
  const [custom, setCustom] = useState("");
  const [apiKey, setApiKey] = useState(() => sessionStorage.getItem(KEY) ?? "");
  const goals = useFetch<{ goals: string }>("/coach/goals");
  const [goalsText, setGoalsText] = useState("");
  const [goalsSaved, setGoalsSaved] = useState(false);
  const [thread, setThread] = useState<Turn[]>([]);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (models.data && !model) setModel(models.data.default);
  }, [models.data, model]);
  useEffect(() => setThread([]), [minutes, model, custom, sport]);

  useEffect(() => {
    if (goals.data) setGoalsText(goals.data.goals);
  }, [goals.data]);

  async function saveGoals() {
    setError(null);
    try {
      await api("/coach/goals", { method: "PUT", body: { goals: goalsText } });
      setGoalsSaved(true);
      setThread([]); // earlier answers were based on the old goals
    } catch (e) {
      setError((e as Error).message);
    }
  }

  const chosen = model === "other" ? custom.trim() : model;

  // Sends the thread so far plus an optional new message. The server rebuilds the opening data summary each time.
  async function ask(message?: string) {
    sessionStorage.setItem(KEY, apiKey);
    setBusy(true);
    setError(null);
    const turns: Turn[] = message ? [...thread, { role: "user", content: message }] : [];
    try {
      const advice = await api<Advice>("/coach", {
        body: {
          minutes, model: chosen || null, api_key: apiKey,
          history: turns.map((t) => ({ role: t.role, content: t.content })),
        },
      });
      setThread([...turns, { role: "assistant", content: JSON.stringify(advice), advice }]);
      setDraft("");
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
        <button disabled={busy || (model === "other" && !chosen)} onClick={() => ask()}>{busy ? "Asking…" : thread.length ? "Start over" : "Ask coach"}</button>
      </div>
      <label className="field">Your goals and background
        <textarea rows={3} maxLength={1000} value={goalsText} placeholder="e.g. Ride 200 km in one day in June. Hold 40 km/h for an hour on the flat. I can train 6 hours a week and can't ride on Thursdays."
          onChange={(e) => { setGoalsText(e.target.value); setGoalsSaved(false); }} />
      </label>
      <p>
        <button type="button" onClick={saveGoals} disabled={goalsText === (goals.data?.goals ?? "")}>Save goals</button>
        {goalsSaved ? <span className="muted"> Saved. The coach uses it on every request.</span> : null}
      </p>
      <p className="muted">Sends a summary of the last 7 days of recovery and training numbers (no names, places or account details) to OpenRouter, using your key. Your goals text is sent as written, so leave out personal details.</p>
      {error ? <Banner kind="error">{error}</Banner> : null}
      {thread.map((t, i) =>
        t.role === "user" ? (
          <div key={i} className="card" style={{ marginLeft: 48 }}><b>You</b><p>{t.content}</p></div>
        ) : (
          <AdviceCard key={i} advice={t.advice} ftp={ftp} sport={sport} thresholdPace={thresholdPace} paceUnit={paceUnit} />
        ),
      )}
      {thread.length ? (
        <form className="row" onSubmit={(e) => { e.preventDefault(); if (draft.trim()) ask(draft.trim()); }}>
          <label className="field" style={{ flex: 1 }}>Reply to the coach
            <input value={draft} onChange={(e) => setDraft(e.target.value)} disabled={busy} maxLength={1000}
              placeholder="e.g. Make it 45 minutes, or swap the tempo for a hilly route" />
          </label>
          <button type="submit" disabled={busy || !draft.trim()}>{busy ? "Asking…" : "Send"}</button>
        </form>
      ) : null}
      {thread.length ? <p className="muted">Your messages are sent to OpenRouter too, so keep them free of personal details.</p> : null}
    </details>
  );
}

const pace = (s: number) => `${Math.floor(Math.round(s) / 60)}:${String(Math.round(s) % 60).padStart(2, "0")}`;

function AdviceCard({ advice, ftp, sport, thresholdPace, paceUnit }: { advice: Advice; ftp: number; sport: string; thresholdPace: number | null; paceUnit: string }) {
  // Cycling targets become watts; running targets become pace when a threshold pace is set.
  const target = (s: Advice["adjusted_steps"] extends (infer T)[] | null ? T : never) =>
    sport === "cycling" ? `${Math.round((s.pct_ftp_low * ftp) / 100)}–${Math.round((s.pct_ftp_high * ftp) / 100)} W`
      : thresholdPace && s.pct_ftp_low >= 55 ? `${pace((thresholdPace * 100) / s.pct_ftp_high)}–${pace((thresholdPace * 100) / s.pct_ftp_low)} ${paceUnit}` : "–";
  return (
    <div className="card">
      <b>Coach · {advice.agree_with_suggestion ? "agrees with" : "suggests changing"} the plan</b>
      <p>{advice.summary}</p>
      <ul>{advice.cautions.map((c) => <li key={c}>{c}</li>)}</ul>
      {advice.adjusted_steps ? (
        <table>
          <thead><tr><th>Step</th><th className="num">Min</th><th className="num">Target</th><th className="num">{sport === "cycling" ? "% FTP" : "% threshold"}</th></tr></thead>
          <tbody>
            {advice.adjusted_steps.map((s, i) => (
              <tr key={i}>
                <td>{s.label}</td><td className="num">{Math.round(s.minutes)}</td>
                <td className="num">{target(s)}</td>
                <td className="num">{s.pct_ftp_low}–{s.pct_ftp_high}%</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </div>
  );
}
