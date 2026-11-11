import { useEffect, useRef, useState } from "react";
import { api, useFetch } from "../api";
import { useNavigate } from "react-router-dom";
import { Banner } from "../components";
import { useWorkout, type Sport, type Workout } from "../workout";
import { GymSession, gymMinutes, type GymExercise } from "./GymSession";
import type { WorkoutStep } from "./WorkoutChart";

interface Advice {
  summary: string;
  agree_with_suggestion: boolean;
  adjusted_steps: { minutes: number; pct_ftp_low: number; pct_ftp_high: number; label: string }[] | null;
  adjusted_exercises?: GymExercise[] | null;
  /** Set when the coach proposes a workout for a different sport than the selected one. */
  workout_sport?: Sport | null;
  cautions: string[];
}

// One turn of the conversation. Assistant turns keep the parsed advice for display; the raw text is what goes back to the model.
type Turn = { role: "user"; content: string } | { role: "assistant"; content: string; advice: Advice };

const KEY = "openrouter_key"; // remembered in this browser, never sent anywhere but the coach call

function loadKey(): string {
  try { return localStorage.getItem(KEY) ?? sessionStorage.getItem(KEY) ?? ""; } catch { return ""; }
}
function saveKey(key: string) {
  try {
    if (key) localStorage.setItem(KEY, key); else localStorage.removeItem(KEY);
    sessionStorage.removeItem(KEY); // older versions kept it here
  } catch { /* storage blocked: the key just won't be remembered */ }
}

export function Coach({ ftp, thresholdPace, paceUnit }: { ftp: number; thresholdPace: number | null; paceUnit: string }) {
  const { minutes, sport, applyWorkout, gymFocus } = useWorkout();
  const navigate = useNavigate();
  const [applied, setApplied] = useState<number | null>(null);
  const keepThread = useRef(false); // accepting a workout for another sport switches sport; that must not wipe the chat
  const models = useFetch<{ models: string[]; default: string }>("/coach/models");
  const [model, setModel] = useState("");
  const [custom, setCustom] = useState("");
  const [apiKey, setApiKey] = useState(loadKey);
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
  useEffect(() => {
    if (keepThread.current) { keepThread.current = false; return; }
    setThread([]); setApplied(null);
  }, [minutes, model, custom, sport, gymFocus]);

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
    setBusy(true);
    setError(null);
    const turns: Turn[] = message ? [...thread, { role: "user", content: message }] : [];
    try {
      const advice = await api<Advice>("/coach", {
        body: {
          minutes, sport, focus: sport === "gym" && gymFocus !== "auto" ? gymFocus : null, model: chosen || null, api_key: apiKey,
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

  const needsKey = useState(() => !loadKey())[0];
  const body = useRef<HTMLDivElement>(null);
  useEffect(() => { body.current?.scrollTo({ top: body.current.scrollHeight, behavior: "smooth" }); }, [thread, busy]);
  const canSend = !busy && !(model === "other" && !chosen);
  const send = () => { if (canSend && draft.trim()) ask(draft.trim()); };

  return (
    <div className="coach">
      <div className="coach-body" ref={body}>
        <details open={needsKey} onFocus={(e) => {
          // Once the keyboard is up, bring the field being edited into view.
          const field = e.target as HTMLElement;
          setTimeout(() => field.scrollIntoView({ block: "center", behavior: "smooth" }), 300);
        }}>
          <summary>Coach settings (API key, model, goals)</summary>
          <div className="row" style={{ margin: "12px 0" }}>
            <label className="field">OpenRouter API key
              <input type="password" value={apiKey} onChange={(e) => { setApiKey(e.target.value); saveKey(e.target.value); }} autoComplete="off" placeholder="sk-or-…"
                title="Your own key from openrouter.ai/keys. Saved in this browser so you only enter it once." />
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
          </div>
          <label className="field">Your goals and background
            <textarea rows={3} maxLength={1000} value={goalsText} placeholder="e.g. Ride 200 km in one day in June. Hold 40 km/h for an hour on the flat. I can train 6 hours a week and can't ride on Thursdays."
              onChange={(e) => { setGoalsText(e.target.value); setGoalsSaved(false); }} />
          </label>
          <p>
            <button type="button" onClick={saveGoals} disabled={goalsText === (goals.data?.goals ?? "")}>Save goals</button>
            {goalsSaved ? <span className="muted"> Saved. The coach uses it on every request.</span> : null}
          </p>
          <p className="muted">Sends a summary of the last 7 days of recovery and training numbers (no names, places or account details) to OpenRouter, using your key. Your goals text and messages are sent as written, so leave out personal details.</p>
        </details>
        {error ? <Banner kind="error">{error}</Banner> : null}
        {!thread.length && !busy ? (
          <p className="muted">Ask anything about your training, or get a second opinion on today's workout.</p>
        ) : null}
        {thread.map((t, i) =>
          t.role === "user" ? (
            <div key={i} className="card" style={{ marginLeft: 48 }}><b>You</b><p>{t.content}</p></div>
          ) : (
            <AdviceCard key={i} advice={t.advice} ftp={ftp} sport={t.advice.workout_sport ?? sport} selectedSport={sport} thresholdPace={thresholdPace} paceUnit={paceUnit}
              applied={applied === i}
              onAccept={() => {
                const target = t.advice.workout_sport ?? sport;
                if (target !== sport) keepThread.current = true;
                applyWorkout(toWorkout(t.advice, ftp, target, thresholdPace, paceUnit), target);
                setApplied(i);
                navigate("/");
              }} />
          ),
        )}
        {busy ? <p className="muted">Coach is thinking…</p> : null}
      </div>
      <form className="coach-composer" onSubmit={(e) => { e.preventDefault(); send(); }}>
        <textarea rows={2} value={draft} onChange={(e) => setDraft(e.target.value)} disabled={busy} maxLength={1000}
          aria-label="Message to the coach"
          placeholder={thread.length ? "Reply, e.g. make it 45 minutes" : "Write your own question, e.g. I only have 40 minutes and my legs are heavy"}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }} />
        <div className="coach-actions">
          {thread.length
            ? <button type="button" className="secondary" disabled={!canSend} onClick={() => ask()}>Start over</button>
            : <button type="button" className="secondary" disabled={!canSend} onClick={() => ask()}>Advise on today's workout</button>}
          <button type="submit" disabled={!canSend || !draft.trim()}>Send</button>
        </div>
      </form>
    </div>
  );
}

const pace = (s: number) => `${Math.floor(Math.round(s) / 60)}:${String(Math.round(s) % 60).padStart(2, "0")}`;

const SPORT_NAME: Record<string, string> = { cycling: "cycling", running: "running", swimming: "swimming", gym: "gym" };

function AdviceCard({ advice, ftp, sport, selectedSport, thresholdPace, paceUnit, applied, onAccept }: { advice: Advice; ftp: number; sport: string; selectedSport: string; thresholdPace: number | null; paceUnit: string; applied: boolean; onAccept: () => void }) {
  // Cycling targets become watts; running targets become pace when a threshold pace is set.
  const target = (s: Advice["adjusted_steps"] extends (infer T)[] | null ? T : never) =>
    sport === "cycling" ? `${Math.round((s.pct_ftp_low * ftp) / 100)}–${Math.round((s.pct_ftp_high * ftp) / 100)} W`
      : thresholdPace && s.pct_ftp_low >= 55 ? `${pace((thresholdPace * 100) / s.pct_ftp_high)}–${pace((thresholdPace * 100) / s.pct_ftp_low)} ${paceUnit}` : "–";
  return (
    <div className="card">
      <b>Coach · {sport !== selectedSport
        ? `suggests a ${SPORT_NAME[sport] ?? sport} workout instead`
        : `${advice.agree_with_suggestion ? "agrees with" : "suggests changing"} the plan`}</b>
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
      {advice.adjusted_exercises?.length ? <GymSession exercises={advice.adjusted_exercises} /> : null}
      {advice.adjusted_steps || advice.adjusted_exercises?.length ? (
        <p>
          <button type="button" onClick={onAccept} disabled={applied}>{applied ? "Applied to Today ✓" : sport !== selectedSport ? `Use this workout (switches to ${SPORT_NAME[sport] ?? sport})` : "Use this workout"}</button>
          <span className="muted"> {applied ? "Shown as today's workout on Home." : sport !== selectedSport ? `Saved under ${SPORT_NAME[sport] ?? sport}; your ${selectedSport} workout stays as it was.` : "Replaces the suggested workout on Home."}</span>
        </p>
      ) : null}
    </div>
  );
}

/** Turns the coach's proposed steps into the same shape as a suggested workout, so Home can show and chart it. */
export function toWorkout(advice: Advice, ftp: number, sport: string, thresholdPace: number | null, paceUnit: string): Workout {
  if (sport === "gym" && advice.adjusted_exercises?.length) {
    return {
      name: "Coach's version", purpose: advice.summary, note: "", minutes: gymMinutes(advice.adjusted_exercises),
      rationale: advice.cautions, steps: [], exercises: advice.adjusted_exercises,
    };
  }
  const steps: WorkoutStep[] = (advice.adjusted_steps ?? []).map((s) => {
    const low = s.pct_ftp_low, high = s.pct_ftp_high;
    const step: WorkoutStep = {
      minutes: s.minutes, label: s.label, pct_ftp: `${low}–${high}%`, frac_low: low / 100, frac_high: high / 100,
    };
    if (sport === "cycling") {
      step.watts_low = Math.round((low * ftp) / 100);
      step.watts_high = Math.round((high * ftp) / 100);
    } else {
      step.rpe = high < 50 ? "rest" : String(Math.min(10, Math.max(1, Math.round(high / 10))));
      step.pace_unit = paceUnit;
      if (thresholdPace && low >= 55) {
        step.pace_fast = pace((thresholdPace * 100) / high);
        step.pace_slow = pace((thresholdPace * 100) / low);
      }
    }
    return step;
  });
  return {
    name: "Coach's version", purpose: advice.summary, note: "", minutes: Math.round(steps.reduce((a, s) => a + s.minutes, 0)),
    rationale: advice.cautions, steps,
  };
}
