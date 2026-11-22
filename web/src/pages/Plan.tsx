import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, useFetch } from "../api";
import { Banner, CollapsibleCard, Loading } from "../components";
import {
  STATUS, buildMessage, changeLine, fmtMinutes, fmtPace, goalLine, parsePace, shortDay, sportEmoji,
  type BuildResult, type DayStatus, type PlanChange, type PlanGoal,
} from "../lib";
import { loadKey } from "./Coach";
import { GymSession, type GymExercise } from "./GymSession";
import { WorkoutChart, type WorkoutStep } from "./WorkoutChart";
import { Button } from "@/ui/button";
import { Dialog, DialogContent } from "@/ui/dialog";
import { Pencil, Plus } from "lucide-react";

// ------------------------------------------------------------------ API shapes

export interface PlanSession {
  day: string; slot: number; sport: string; kind: string; minutes: number; planned_load: number;
  source: "auto" | "ai" | "user" | "coach" | "adjusted"; locked: boolean; note: string; label: string;
}
interface DoneActivity { activity_id: number; name: string; sport: string; minutes: number; km: number; load: number }
interface PlanDayView { day: string; status: DayStatus; sessions: PlanSession[]; activities: DoneActivity[] }
interface PlanWeek {
  week_start: string; past: boolean; phase: string | null; note: string; target_hours: number | null; sessions: number | null;
  detailed: boolean; override_hours: number | null; planned_hours: number; planned_load: number; actual_hours: number;
  actual_load: number; days: PlanDayView[]; builder: "ai" | "rules";
}
export interface Proposal {
  id: number; kind: string; reason: string; source: "rules" | "coach"; created: string;
  changes: (PlanChange & { after: { note?: string; exercises?: { name: string; sets: number; reps: string; rpe?: number }[] } | null })[];
}
interface Profile { sports: Record<string, number>; hours_per_week: number; sessions_per_week: number | null; long_day: number }
interface Athlete { equipment: string[]; effort: string }
interface Setup {
  configured: boolean; profile: Profile; goals: PlanGoal[]; athlete: Athlete; about: string;
  options: {
    sports: string[]; goal_kinds: string[]; weekdays: string[]; kinds: Record<string, string[]>; kind_labels: Record<string, string>; max_goals: number;
    equipment: Record<string, string>; efforts: Record<string, string>; about_max: number;
  };
}
interface AIState { model: string | null; request?: string; built: string | null; summary: string; topup_weeks: string[] }
interface PlanView { setup: Setup; today: string; weeks: PlanWeek[]; proposals: Proposal[]; ai: AIState }
type BuildMode = "all" | "topup" | "week";
type Message = { kind: "error" | "info"; text: string; details?: string[] };

const SPORT_LABEL: Record<string, string> = { cycling: "Cycling", running: "Running", swimming: "Swimming", gym: "Gym" };
const GOAL_LABEL: Record<string, string> = {
  event: "An event on a date", volume: "Train a number of hours a week", performance: "Reach an FTP or threshold pace", general: "General fitness",
};
const PHASE_LABEL: Record<string, string> = { base: "Base", build: "Build", recovery: "Recovery", taper: "Taper", race: "Event week" };
const SOURCE_NOTE: Record<string, string> = { user: "edited by you", coach: "from the coach", adjusted: "adjusted" };

// ------------------------------------------------------------------ page

const MODEL_KEY = "plan_model"; // the model chosen for building and reviewing the plan, remembered in this browser
const TOPUP_KEY = (day: string) => `plan_ai_topup:${day}`; // the automatic AI top-up runs once a day per tab

function useModel(): [string, (m: string) => void, string[]] {
  const models = useFetch<{ models: string[]; default: string }>("/coach/models");
  const [model, setModelState] = useState(() => { try { return localStorage.getItem(MODEL_KEY) ?? ""; } catch { return ""; } });
  const setModel = (m: string) => {
    setModelState(m);
    try { if (m) localStorage.setItem(MODEL_KEY, m); else localStorage.removeItem(MODEL_KEY); } catch { /* not remembered */ }
  };
  return [model || models.data?.default || "", setModel, models.data?.models ?? []];
}

function ModelPicker({ model, setModel, models }: { model: string; setModel: (m: string) => void; models: string[] }) {
  const listed = !model || models.includes(model);
  const [other, setOther] = useState(!listed);
  return (
    <>
      <label className="field" style={{ maxWidth: 320 }}>AI model
        <select value={other ? "other" : model} onChange={(e) => {
          if (e.target.value === "other") setOther(true); else { setOther(false); setModel(e.target.value); }
        }}>
          {models.map((m) => <option key={m}>{m}</option>)}
          <option value="other">Other…</option>
        </select>
      </label>
      {other ? (
        <label className="field">Model id
          <input value={listed ? "" : model} onChange={(e) => setModel(e.target.value.trim())} placeholder="provider/model-name" />
        </label>
      ) : null}
    </>
  );
}

export function Plan() {
  const { data, error, loading, reload } = useFetch<PlanView>("/plan");
  const [editingSetup, setEditingSetup] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<Message | null>(null);
  const [model, setModel, models] = useModel();
  const key = loadKey();
  const building = useRef(false);

  /** Build with the AI coach (the server falls back to the rules without a key or when the AI fails). */
  const build = async (mode: BuildMode, weekStart?: string, prompt?: string) => {
    if (building.current) return;
    building.current = true;
    setBusy(mode === "topup" ? "The AI coach is planning the coming weeks… this can take a minute or two."
      : key ? "The AI coach is building your plan… this can take a minute or two." : "Rebuilding the plan…");
    setMessage(null);
    try {
      const r = await api<BuildResult>("/plan/build", { body: { mode, week_start: weekStart ?? null, api_key: key || null, model: model || null, prompt: prompt ?? null } });
      setMessage(buildMessage(r, mode));
    } catch (e) {
      setMessage({ kind: "error", text: (e as Error).message });
    } finally {
      building.current = false;
      setBusy(null);
      reload();
    }
  };

  // Weekly top-up: the coming weeks the AI hasn't planned yet, once a day, only with a key (it lives in this browser).
  const topup = data?.setup.configured && key ? data.ai.topup_weeks.length : 0;
  useEffect(() => {
    if (!topup || !data) return;
    const flag = TOPUP_KEY(data.today);
    try { if (sessionStorage.getItem(flag)) return; sessionStorage.setItem(flag, "1"); } catch { return; }
    void build("topup");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [topup, data?.today]);

  if (!data) return <Loading error={error} loading={loading} />;
  const { setup } = data;

  const act = async (fn: () => Promise<unknown>, done?: string) => {
    setBusy("Working…");
    setMessage(null);
    try {
      await fn();
      if (done) setMessage({ kind: "info", text: done });
      reload();
    } catch (e) {
      setMessage({ kind: "error", text: (e as Error).message });
    } finally {
      setBusy(null);
    }
  };

  if (!setup.configured || editingSetup) {
    return (
      <SetupCard setup={setup} today={data.today} aiReady={!!key} onCancel={setup.configured ? () => setEditingSetup(false) : undefined}
        onSaved={() => { setEditingSetup(false); reload(); if (key) void build("all"); }} />
    );
  }
  const ai = data.ai;
  const now = data.weeks.filter((w) => w.detailed);
  const later = data.weeks.filter((w) => !w.detailed);
  return (
    <>
      <CollapsibleCard id="plan:overview" title="Training plan"
        aside={<Button variant="secondary" size="sm" onClick={() => setEditingSetup(true)}><Pencil size={14} /> Edit goals and schedule</Button>}>
        <ul className="plain">
          {setup.goals.length ? setup.goals.map((g, i) => <li key={i}>{i === 0 ? <b>{goalLine(g, data.today)}</b> : goalLine(g, data.today)}</li>)
            : <li>General fitness (no goal set)</li>}
        </ul>
        <p className="muted">
          {Object.entries(setup.profile.sports).filter(([, v]) => v > 0).map(([s]) => SPORT_LABEL[s] ?? s).join(" + ")}
          {" · "}{setup.profile.hours_per_week} h a week
          {setup.profile.sessions_per_week ? ` · ${setup.profile.sessions_per_week} sessions` : ""}
          {" · long session on "}{setup.options.weekdays[setup.profile.long_day]}
        </p>
        {ai.summary ? (
          <div className="ai-summary">
            <p>{ai.summary}</p>
            <p className="muted">Built by the AI coach{ai.model ? ` (${ai.model})` : ""}{ai.built ? ` on ${shortDay(ai.built)}` : ""}. Safety limits
              (how fast fitness rises, session length, your hours) are checked on every session.</p>
          </div>
        ) : (
          <p className="muted">
            {key ? "This plan was built by the rules. Regenerate to let the AI coach build it."
              : "This plan was built by the rules. Enter an OpenRouter key in Home → Ask coach to let the AI coach build it."}
          </p>
        )}
        <div className="row">
          {key ? <ModelPicker model={model} setModel={setModel} models={models} /> : null}
          <Button variant="secondary" disabled={!!busy}
            title={key ? "The AI coach rebuilds every automatic session from today on. Sessions you edited, locked or applied stay."
              : "Rebuild every automatic session from today on. Sessions you edited, locked or applied stay."}
            onClick={() => build("all")}>Regenerate</Button>
          <Button variant="secondary" disabled={!!busy}
            onClick={() => { if (confirm("Delete the plan, its goals and every edit?")) act(() => api("/plan", { method: "DELETE" })); }}>Start over</Button>
        </div>
        <PlanPrompt key={ai.request ?? ""} saved={ai.request ?? ""} model={model} busy={!!busy} hasKey={!!key}
          onBuild={(text) => build("all", undefined, text)} onReviewed={reload} />
        {busy ? <p><Banner kind="info">{busy}</Banner></p> : null}
        {message ? (
          <div><Banner kind={message.kind}>{message.text}
            {message.details?.length ? <ul>{message.details.map((d, i) => <li key={i}>{d}</li>)}</ul> : null}
          </Banner></div>
        ) : null}
      </CollapsibleCard>
      <Proposals proposals={data.proposals} onChange={reload} />
      <PlanCalendar weeks={now} today={data.today} setup={setup} onChange={reload}
        onHoursSet={key ? (ws) => build("week", ws) : undefined} />
      {later.length ? <LaterWeeks weeks={later} /> : null}
    </>
  );
}

// ------------------------------------------------------------------ setup

interface GoalDraft { kind: string; sport: string; date: string; target: string; name: string }

const toDraft = (g: PlanGoal): GoalDraft => ({
  kind: g.kind, sport: g.sport ?? "", date: g.date ?? "", name: g.name,
  target: g.target === null ? "" : g.kind === "performance" && g.sport !== "cycling" ? fmtPace(g.target) : String(g.target),
});

function SetupCard({ setup, today, aiReady, onSaved, onCancel }: {
  setup: Setup; today: string; aiReady: boolean; onSaved: () => void; onCancel?: () => void;
}) {
  const o = setup.options;
  const [shares, setShares] = useState<Record<string, number>>(() =>
    Object.fromEntries(o.sports.map((s) => [s, Math.round((setup.profile.sports[s] ?? 0) * 100)])));
  const [hours, setHours] = useState(String(setup.profile.hours_per_week));
  const [sessions, setSessions] = useState(setup.profile.sessions_per_week ? String(setup.profile.sessions_per_week) : "");
  const [longDay, setLongDay] = useState(setup.profile.long_day);
  const [goals, setGoals] = useState<GoalDraft[]>(() => setup.goals.map(toDraft));
  const [about, setAbout] = useState(setup.about ?? "");
  const [equipment, setEquipment] = useState<string[]>(setup.athlete.equipment);
  const [effort, setEffort] = useState(setup.athlete.effort);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const chosen = o.sports.filter((s) => shares[s] > 0);
  const endurance = chosen.filter((s) => s !== "gym");

  const setGoal = (i: number, patch: Partial<GoalDraft>) => setGoals((gs) => gs.map((g, j) => (j === i ? { ...g, ...patch } : g)));

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    const out = [];
    for (const [i, g] of goals.entries()) {
      let target: number | null = null;
      if (g.target.trim()) {
        target = g.kind === "performance" && g.sport !== "cycling" ? parsePace(g.target) : Number(g.target);
        if (target === null || Number.isNaN(target)) {
          setError(`Goal ${i + 1}: the target isn't a number${g.kind === "performance" && g.sport !== "cycling" ? " or a pace like 4:30" : ""}.`);
          return;
        }
      }
      out.push({ kind: g.kind, sport: g.kind === "general" || g.kind === "volume" ? null : g.sport || null, date: g.date || null, target, name: g.name.trim() });
    }
    setBusy(true);
    try {
      await api("/plan/setup", {
        method: "PUT",
        body: {
          profile: {
            sports: Object.fromEntries(chosen.map((s) => [s, shares[s] / 100])),
            hours_per_week: Number(hours), sessions_per_week: sessions ? Number(sessions) : null, long_day: longDay,
          },
          goals: out,
          athlete: { equipment, effort },
          about,
        },
      });
      onSaved();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card" onSubmit={save}>
      <h2>{setup.configured ? "Goals and schedule" : "Set up your training plan"}</h2>
      {!setup.configured ? (
        <p className="muted">
          Tell the app what you're training for and how much time you have. It builds a plan for the next weeks, places the
          sessions for you and suggests changes when your recovery or your real training differs from the plan. Nothing changes
          without your OK. These values are suggested from your last 12 weeks.
          {aiReady ? " The AI coach designs the plan (phases, hours and every session) within safety limits; it sees numbers only, never goal names, places or account details."
            : " Enter an OpenRouter key in Home → Ask coach and the AI coach designs the plan; without one, the built-in rules do."}
        </p>
      ) : null}

      <h3>Sports</h3>
      <p className="muted">Tick the sports you want in the plan. The share is roughly how many of the sessions go to each.</p>
      <div className="row">
        {o.sports.map((s) => (
          <label key={s} className="field">
            <span><input type="checkbox" checked={shares[s] > 0}
              onChange={(e) => setShares({ ...shares, [s]: e.target.checked ? (s === "gym" ? 20 : 50) : 0 })} /> {SPORT_LABEL[s] ?? s}</span>
            {shares[s] > 0 ? (
              <span><input type="number" min={5} max={100} step={5} value={shares[s]} style={{ width: 80 }}
                onChange={(e) => setShares({ ...shares, [s]: Math.max(0, Number(e.target.value)) })} /> %</span>
            ) : null}
          </label>
        ))}
      </div>

      <h3>Time</h3>
      <div className="row">
        <label className="field">Hours a week
          <input type="number" min={1} max={30} step={0.5} value={hours} onChange={(e) => setHours(e.target.value)} required style={{ width: 110 }} />
        </label>
        <label className="field">Sessions a week (blank = let the app pick)
          <input type="number" min={1} max={10} value={sessions} onChange={(e) => setSessions(e.target.value)} style={{ width: 110 }} />
        </label>
        <label className="field">Long session on
          <select value={longDay} onChange={(e) => setLongDay(Number(e.target.value))}>
            {o.weekdays.map((d, i) => <option key={d} value={i}>{d}</option>)}
          </select>
        </label>
      </div>
      <p className="muted">The plan starts near what you've done lately and ramps up gently towards these hours. Busy week? Set its hours on the week itself.</p>

      <h3>Goals</h3>
      <p className="muted">The first goal shapes the plan (for an event: build, recovery and taper weeks leading to the day). Names stay on this server and are never sent to the AI coach.</p>
      {goals.map((g, i) => (
        <div key={i} className="row goal-row">
          <label className="field">Goal
            <select value={g.kind} onChange={(e) => setGoal(i, { kind: e.target.value })}>
              {o.goal_kinds.map((k) => <option key={k} value={k}>{GOAL_LABEL[k] ?? k}</option>)}
            </select>
          </label>
          {g.kind === "event" || g.kind === "performance" ? (
            <label className="field">Sport
              <select value={g.sport} onChange={(e) => setGoal(i, { sport: e.target.value, target: "" })} required={g.kind === "performance"}>
                <option value="">{g.kind === "event" ? "Any" : "Choose…"}</option>
                {endurance.map((s) => <option key={s} value={s}>{SPORT_LABEL[s]}</option>)}
              </select>
            </label>
          ) : null}
          {g.kind === "event" || g.kind === "performance" ? (
            <label className="field">{g.kind === "event" ? "Date" : "By (optional)"}
              <input type="date" value={g.date} min={today} onChange={(e) => setGoal(i, { date: e.target.value })} required={g.kind === "event"} />
            </label>
          ) : null}
          {g.kind !== "general" ? (
            <label className="field">{g.kind === "event" ? "Expected hours (optional)" : g.kind === "volume" ? "Hours a week"
              : g.sport === "cycling" ? "FTP (W)" : g.sport === "swimming" ? "Pace /100 m (m:ss)" : "Pace /km (m:ss)"}
              <input value={g.target} onChange={(e) => setGoal(i, { target: e.target.value })} style={{ width: 120 }}
                inputMode={g.kind === "performance" && g.sport !== "cycling" ? "text" : "decimal"} required={g.kind !== "event"} />
            </label>
          ) : null}
          <label className="field">Name (optional)
            <input value={g.name} maxLength={80} onChange={(e) => setGoal(i, { name: e.target.value })} placeholder="e.g. Spring gran fondo" />
          </label>
          <Button type="button" variant="secondary" onClick={() => setGoals(goals.filter((_, j) => j !== i))}>Remove</Button>
        </div>
      ))}
      {goals.length < o.max_goals ? (
        <p><Button type="button" variant="secondary"
          onClick={() => setGoals([...goals, { kind: "event", sport: endurance[0] ?? "", date: "", target: "", name: "" }])}>Add a goal</Button></p>
      ) : null}
      {!goals.length ? <p className="muted">No goal: the plan keeps a steady 3-weeks-on, 1-week-easier rhythm for general fitness.</p> : null}

      <h3>About you and your setup</h3>
      <label className="field">Anything the coach should know (background, injuries, days you can't train, how you like to train)
        <textarea rows={3} maxLength={o.about_max} value={about} onChange={(e) => setAbout(e.target.value)}
          placeholder="e.g. I train at home, not in a gym. I like short, very hard strength sessions. No running on consecutive days." />
      </label>
      <p className="muted">Sent to the AI coach as written (plan and Home coach), so leave out personal details.</p>
      {shares.gym > 0 ? <StrengthSetup options={o} equipment={equipment} effort={effort} onEquipment={setEquipment} onEffort={setEffort} /> : null}

      {error ? <p><Banner kind="error">{error}</Banner></p> : null}
      <div className="row">
        <Button disabled={busy || !chosen.length}>{busy ? "Building…" : setup.configured ? "Save and rebuild" : "Build my plan"}</Button>
        {onCancel ? <Button type="button" variant="secondary" onClick={onCancel}>Cancel</Button> : null}
      </div>
      {setup.configured ? <p className="muted">Saving rebuilds the automatic sessions from today on. Sessions you edited, locked or applied stay.</p> : null}
    </form>
  );
}

function StrengthSetup({ options, equipment, effort, onEquipment, onEffort }: {
  options: Setup["options"]; equipment: string[]; effort: string; onEquipment: (e: string[]) => void; onEffort: (e: string) => void;
}) {
  const fullGym = equipment.includes("full_gym");
  const items = Object.keys(options.equipment).filter((k) => k !== "full_gym");
  const toggle = (k: string, on: boolean) => onEquipment(on ? [...equipment, k] : equipment.filter((x) => x !== k));
  return (
    <>
      <h4>Strength equipment</h4>
      <div className="row">
        <label className="field"><span>
          <input type="radio" name="gym-where" checked={fullGym} onChange={() => onEquipment(["full_gym"])} /> {options.equipment.full_gym}
        </span></label>
        <label className="field"><span>
          <input type="radio" name="gym-where" checked={!fullGym} onChange={() => onEquipment([])} /> At home or outdoors, with…
        </span></label>
      </div>
      {!fullGym ? (
        <>
          <div className="row">
            {items.map((k) => (
              <label key={k} className="field"><span>
                <input type="checkbox" checked={equipment.includes(k)} onChange={(e) => toggle(k, e.target.checked)} /> {options.equipment[k]}
              </span></label>
            ))}
          </div>
          <p className="muted">{equipment.length ? "Gym sessions only use exercises you can do with these and your bodyweight."
            : "Nothing ticked: bodyweight exercises only."}</p>
        </>
      ) : null}
      <h4>How hard</h4>
      <div className="row">
        {Object.entries(options.efforts).map(([k, label]) => (
          <label key={k} className="field"><span>
            <input type="radio" name="gym-effort" checked={effort === k} onChange={() => onEffort(k)} /> {label}
          </span></label>
        ))}
      </div>
      {effort === "failure" ? <p className="muted">Working sets go to failure. Beginners, amber days and short sessions stop one rep short; red days get mobility instead.</p> : null}
    </>
  );
}

// ------------------------------------------------------------------ proposals and the coach

export function Proposals({ proposals, onChange }: { proposals: Proposal[]; onChange: () => void }) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<number | null>(null);
  if (!proposals.length) return null;
  const act = async (p: Proposal, what: "apply" | "dismiss") => {
    setBusy(p.id);
    setError(null);
    try {
      await api(`/plan/proposals/${p.id}/${what}`, { method: "POST" });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
      onChange();
    }
  };
  return (
    <CollapsibleCard id="plan:proposals" title="Suggested changes">
      {proposals.map((p) => (
        <div key={p.id} className="proposal">
          <p><b>{p.source === "coach" ? "From the AI coach" : "From your training"}:</b> {p.reason}</p>
          <ul>
            {p.changes.map((c) => (
              <li key={`${c.day}:${c.slot}`}>{changeLine(c)}{c.after?.note ? <span className="muted"> · {c.after.note}</span> : null}
                {c.after?.exercises?.length ? <div className="muted">{c.after.exercises.map((e) => `${e.name} ${e.sets}×${e.reps}${e.rpe ? ` @${e.rpe}` : ""}`).join(" · ")}</div> : null}</li>
            ))}
          </ul>
          <div className="row">
            <Button disabled={busy === p.id} onClick={() => act(p, "apply")}>Apply</Button>
            <Button variant="secondary" disabled={busy === p.id} onClick={() => act(p, "dismiss")}>Dismiss</Button>
          </div>
        </div>
      ))}
      {error ? <p><Banner kind="error">{error}</Banner></p> : null}
    </CollapsibleCard>
  );
}

interface ReviewReply { summary: string; cautions: string[]; plan_proposal: Proposal | null; plan_skipped: string[] }

/** One box for telling the coach what you want: build a new plan from it, or ask for changes to the current one. */
function PlanPrompt({ saved, model, busy, hasKey, onBuild, onReviewed }: {
  saved: string; model: string; busy: boolean; hasKey: boolean; onBuild: (text: string) => void; onReviewed: () => void;
}) {
  const [text, setText] = useState(saved);
  const [reply, setReply] = useState<ReviewReply | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reviewing, setReviewing] = useState(false);
  const key = loadKey();
  const review = async () => {
    setReviewing(true);
    setError(null);
    setReply(null);
    try {
      const r = await api<ReviewReply>("/plan/review", {
        body: { api_key: key, model: model || null, history: text.trim() ? [{ role: "user", content: text.trim() }] : [] },
      });
      setReply(r);
      onReviewed();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setReviewing(false);
    }
  };
  return (
    <div>
      <label className="field">What do you want from your plan?
        <textarea rows={3} maxLength={1000} value={text} onChange={(e) => setText(e.target.value)} disabled={!hasKey}
          placeholder={hasKey ? "e.g. Focus on threshold work for the next 6 weeks, keep Wednesdays free, and make Sundays the long ride."
            : "Enter an OpenRouter key in Home → Ask coach to ask the AI coach."} />
      </label>
      <p className="muted">
        The coach sees your goals, recovery and recent training (no names, places or account details), and what you write here.
        “Build plan” writes a new plan from your request and remembers it for the weeks added later. “Suggest changes” leaves the
        plan as it is and lists edits to apply or dismiss. Sessions you edited or locked always stay.
      </p>
      <div className="row">
        <Button disabled={busy || reviewing || !hasKey} onClick={() => onBuild(text.trim())}>Build plan from this</Button>
        <Button variant="secondary" disabled={busy || reviewing || !hasKey} onClick={review}>{reviewing ? "The coach is reviewing…" : "Suggest changes"}</Button>
      </div>
      {error ? <Banner kind="error">{error}</Banner> : null}
      {reply ? (
        <div>
          <p>{reply.summary}</p>
          {reply.cautions.length ? <ul>{reply.cautions.map((c) => <li key={c}>{c}</li>)}</ul> : null}
          <p className="muted">{reply.plan_proposal ? "Its changes are listed under “Suggested changes”." : "No changes suggested."}</p>
          {reply.plan_skipped.length ? <p className="muted">Left out: {reply.plan_skipped.join(" ")}</p> : null}
        </div>
      ) : null}
    </div>
  );
}

// ------------------------------------------------------------------ calendar

const DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function PlanCalendar({ weeks, today, setup, onChange, onHoursSet }: {
  weeks: PlanWeek[]; today: string; setup: Setup; onChange: () => void; onHoursSet?: (weekStart: string) => void;
}) {
  const [picked, setPicked] = useState<string | null>(null); // the day open in the dialog
  const [notice, setNotice] = useState<{ kind: "error" | "amber"; text: string } | null>(null);
  /** Drag a session onto another day. The server checks spacing and keeps its rules; its warnings are shown. */
  const move = async (from: { day: string; slot: number }, to: string) => {
    if (from.day === to) return;
    setNotice(null);
    try {
      const r = await api<{ warnings?: string[] }>(`/plan/day/${from.day}/${from.slot}/move`, { body: { to } });
      if (r?.warnings?.length) setNotice({ kind: "amber", text: r.warnings.join(" ") });
    } catch (e) {
      setNotice({ kind: "error", text: (e as Error).message });
    }
    onChange();
  };
  const day = weeks.flatMap((w) => w.days).find((d) => d.day === picked) ?? null;
  return (
    <CollapsibleCard id="plan:calendar" title="Calendar" aside={<span>Click a day to edit it, or drag a session to another day</span>}>
      {notice ? <p><Banner kind={notice.kind}>{notice.text}</Banner></p> : null}
      <div className="cal-grid" aria-hidden>
        {DOW.map((d) => <div key={d} className="cal-dow">{d}</div>)}
      </div>
      <div style={{ marginTop: 6 }}>
        {weeks.map((w) => <CalendarWeek key={w.week_start} week={w} today={today} setup={setup} onPick={setPicked} onMove={move} onChange={onChange} onHoursSet={onHoursSet} />)}
      </div>
      <Dialog open={!!day} onOpenChange={(o) => { if (!o) setPicked(null); }}>
        {day ? <DayDialog d={day} today={today} setup={setup} onChange={onChange} onClose={() => setPicked(null)} /> : null}
      </Dialog>
    </CollapsibleCard>
  );
}

function sessionName(s: PlanSession, setup: Setup): string {
  const kind = setup.options.kind_labels[s.kind] ?? s.kind;
  const t = s.kind === "event" ? "" : ` · ${fmtMinutes(s.minutes)}`;
  return `${kind}${t}`;
}

const DRAG_TYPE = "application/x-plan-session";

function CalendarWeek({ week, today, setup, onPick, onMove, onChange, onHoursSet }: {
  week: PlanWeek; today: string; setup: Setup; onPick: (day: string) => void; onMove: (from: { day: string; slot: number }, to: string) => void;
  onChange: () => void; onHoursSet?: (weekStart: string) => void;
}) {
  const [over, setOver] = useState<string | null>(null); // the day a session is being dragged over
  const [hours, setHours] = useState(week.override_hours === null ? "" : String(week.override_hours));
  const [error, setError] = useState<string | null>(null);
  const [showHours, setShowHours] = useState(false);
  useEffect(() => setHours(week.override_hours === null ? "" : String(week.override_hours)), [week.override_hours]);
  const saveHours = async (value: number | null) => {
    setError(null);
    try {
      await api(`/plan/week/${week.week_start}`, { method: "PUT", body: { hours: value } });
      onChange();
      onHoursSet?.(week.week_start); // the rules resized it right away; the AI coach then replans it
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const thisWeek = week.week_start <= today && today <= week.days[6]?.day;
  return (
    <div className="cal-week">
      <div className="cal-week-head">
        <b>{thisWeek ? "This week" : week.past ? "Last week" : `Week of ${shortDay(week.week_start)}`}
          {week.phase ? <span className="muted"> · {PHASE_LABEL[week.phase] ?? week.phase}</span> : null}
          {!week.past ? <span className="chip" title={week.builder === "ai" ? "Sessions written by the AI coach" : "Sessions written by the rules"}>
            {" · "}{week.builder === "ai" ? "AI coach" : "rules"}</span> : null}</b>
        <span className="muted">
          {week.past || thisWeek ? `${week.actual_hours.toFixed(1)} of ${week.planned_hours.toFixed(1)} h done` : `${week.planned_hours.toFixed(1)} h planned`}
          {" · load "}{week.past || thisWeek ? `${week.actual_load} / ` : ""}{week.planned_load}
          {!week.past ? <>{" · "}<Button variant="link" className="text-sm" onClick={() => setShowHours(!showHours)}>
            {week.override_hours !== null ? `${week.override_hours} h set` : "set hours"}</Button></> : null}
        </span>
      </div>
      {week.note ? <p className="muted">{week.note}</p> : null}
      {showHours && !week.past ? (
        <div className="row" style={{ marginBottom: 8 }}>
          <label className="field">Hours available this week
            <input type="number" min={0} max={30} step={0.5} value={hours} placeholder={String(setup.profile.hours_per_week)}
              onChange={(e) => setHours(e.target.value)} style={{ width: 110 }} />
          </label>
          <Button variant="secondary" disabled={!hours || Number(hours) === week.override_hours} onClick={() => saveHours(Number(hours))}>Set</Button>
          {week.override_hours !== null ? <Button variant="secondary" onClick={() => saveHours(null)}>Back to usual</Button> : null}
          {error ? <span className="basis-full text-sm text-bad">{error}</span> : null}
        </div>
      ) : null}
      <div className="cal-grid">
        {week.days.map((d) => {
          const sessions = d.sessions.filter((s) => s.kind !== "rest");
          const editable = d.day >= today;
          const dayNum = new Date(d.day + "T12:00:00").getDate();
          const label = `${shortDay(d.day)}: ${sessions.map((s) => s.label).join(", ") || "Rest"}`;
          return (
            <div key={d.day} role="button" tabIndex={0} aria-label={label} title={label} onClick={() => onPick(d.day)}
              onKeyDown={(e) => { if (e.target === e.currentTarget && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); onPick(d.day); } }}
              onDragOver={editable ? (e) => { if (e.dataTransfer.types.includes(DRAG_TYPE)) { e.preventDefault(); e.dataTransfer.dropEffect = "move"; setOver(d.day); } } : undefined}
              onDragLeave={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setOver((o) => (o === d.day ? null : o)); }}
              onDrop={editable ? (e) => {
                const raw = e.dataTransfer.getData(DRAG_TYPE);
                setOver(null);
                if (!raw) return;
                e.preventDefault();
                onMove(JSON.parse(raw), d.day);
              } : undefined}
              className={`cal-cell${d.day === today ? " today" : ""}${d.day < today ? " past" : ""}${over === d.day ? " drop" : ""}`}>
              <span className="cal-num">
                <span>{dayNum}</span>
                {STATUS[d.status].tone ? <span className={`dot ${STATUS[d.status].tone}`} style={{ margin: 0 }} title={STATUS[d.status].label} /> : null}
              </span>
              {sessions.map((s) => (
                <span key={s.slot} className={`cal-chip${editable ? " draggable" : ""}`} draggable={editable}
                  onDragStart={editable ? (e) => {
                    e.dataTransfer.setData(DRAG_TYPE, JSON.stringify({ day: s.day, slot: s.slot }));
                    e.dataTransfer.effectAllowed = "move";
                  } : undefined}
                  onDragEnd={() => setOver(null)}>
                  <span className="emoji">{sportEmoji(s.sport, s.kind)}</span>
                  <span className="name">{sessionName(s, setup)}{s.locked ? " 🔒" : ""}</span>
                </span>
              ))}
              {d.activities.map((a) => (
                <span key={a.activity_id} className="cal-chip done" title={`Done: ${a.name || "Activity"}`}>
                  <span className="emoji">✓</span>
                  <span className="name">{a.name || "Activity"}</span>
                </span>
              ))}
              {!sessions.length && !d.activities.length ? <span className="cal-rest">Rest</span> : null}
              {editable ? <span className="cal-plus" aria-hidden><Plus size={12} /></span> : null}
            </div>
          );
        })}
      </div>
    </div>
  );
}

/** Everything about one day: edit each planned session, add one, see what was done. */
function DayDialog({ d, today, setup, onChange, onClose }: {
  d: PlanDayView; today: string; setup: Setup; onChange: () => void; onClose: () => void;
}) {
  const editable = d.day >= today;
  const sessions = d.sessions.filter((s) => s.kind !== "rest");
  const [adding, setAdding] = useState(false);
  const st = STATUS[d.status];
  const date = new Date(d.day + "T12:00:00").toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" });
  return (
    <DialogContent title={date} description={st.label}>
      {sessions.length ? sessions.map((s) => (
        <div key={`${s.day}:${s.slot}`}>
          <h3 style={{ marginTop: 0 }}>{sportEmoji(s.sport, s.kind)} {s.label}{s.locked ? " 🔒" : ""}
            {SOURCE_NOTE[s.source] ? <span className="muted"> · {SOURCE_NOTE[s.source]}</span> : null}</h3>
          {editable
            ? <SessionEditor session={s} setup={setup} today={today} onClose={onClose} onChange={onChange} />
            : s.note ? <p className="muted">{s.note}</p> : null}
        </div>
      )) : <p className="muted">😴 Rest day, nothing planned.</p>}
      {d.activities.length ? (
        <>
          <h3>Done</h3>
          {d.activities.map((a) => (
            <div key={a.activity_id} className="muted">
              ✓ <Link to={`/ride/${a.activity_id}`}>{a.name || "Activity"}</Link> · {fmtMinutes(a.minutes)}{a.km ? ` · ${a.km} km` : ""}{a.load ? ` · load ${a.load}` : ""}
            </div>
          ))}
        </>
      ) : null}
      {editable && sessions.length < 2 ? (
        adding
          ? <><h3>New session</h3><SessionEditor day={d.day} setup={setup} today={today} onClose={onClose} onChange={onChange} /></>
          : <p style={{ marginTop: 12 }}><Button variant="secondary" onClick={() => setAdding(true)}><Plus size={14} /> Add session</Button></p>
      ) : null}
    </DialogContent>
  );
}

interface Preview { workout: { name: string; purpose: string; minutes: number; steps: WorkoutStep[]; exercises?: GymExercise[] } | null; ftp: number }

/** Edit, move, lock or delete a planned session, or add a new one on `day`. */
function SessionEditor({ session, day, setup, today, onClose, onChange }: {
  session?: PlanSession; day?: string; setup: Setup; today: string; onClose: () => void; onChange: () => void;
}) {
  const o = setup.options;
  const chosen = o.sports.filter((s) => (setup.profile.sports[s] ?? 0) > 0);
  const [sport, setSport] = useState(session?.sport ?? chosen[0] ?? "cycling");
  const [kind, setKind] = useState(session?.kind ?? "endurance");
  const [minutes, setMinutes] = useState(String(session?.minutes ?? 60));
  const [note, setNote] = useState(session?.note ?? "");
  const [locked, setLocked] = useState(session?.locked ?? false);
  const [moveTo, setMoveTo] = useState("");
  const [warnings, setWarnings] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const kinds = o.kinds[sport] ?? [];
  useEffect(() => { if (!kinds.includes(kind)) setKind(kinds[sport === "gym" ? 2 : 1] ?? kinds[0]); }, [sport]); // eslint-disable-line react-hooks/exhaustive-deps

  const url = session ? `/plan/day/${session.day}/${session.slot}` : `/plan/day/${day}`;
  const run = async (fn: () => Promise<{ warnings?: string[] } | unknown>, close = true) => {
    setError(null);
    try {
      const r = (await fn()) as { warnings?: string[] } | undefined;
      const w = r?.warnings ?? [];
      setWarnings(w);
      if (close && !w.length) onClose();
      onChange();
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const save = () => run(() => api(url, {
    method: session ? "PATCH" : "POST", body: { sport, kind, minutes: Number(minutes), note, locked },
  }));
  const showPreview = async () => {
    if (!session) return;
    try { setPreview(await api<Preview>(`${url}/workout`)); } catch (e) { setError((e as Error).message); }
  };
  const changed = session && (session.sport !== sport || session.kind !== kind || session.minutes !== Number(minutes));

  return (
    <div className="editor">
      <div className="row">
        <label className="field">Sport
          <select value={sport} onChange={(e) => setSport(e.target.value)}>
            {o.sports.map((s) => <option key={s} value={s}>{SPORT_LABEL[s] ?? s}</option>)}
          </select>
        </label>
        <label className="field">Session
          <select value={kind} onChange={(e) => setKind(e.target.value)}>
            {kinds.map((k) => <option key={k} value={k}>{o.kind_labels[k] ?? k}</option>)}
          </select>
        </label>
        <label className="field">Minutes
          <input type="number" min={10} max={600} step={5} value={minutes} onChange={(e) => setMinutes(e.target.value)} style={{ width: 90 }} />
        </label>
        <label className="field">Note
          <input value={note} maxLength={300} onChange={(e) => setNote(e.target.value)} placeholder="optional" />
        </label>
        <label><input type="checkbox" checked={locked} onChange={(e) => setLocked(e.target.checked)} /> Lock</label>
      </div>
      <div className="row" style={{ marginTop: 8 }}>
        <Button onClick={save}>{session ? "Save" : "Add"}</Button>
        {session ? (
          <>
            <Button variant="secondary" onClick={() => run(() => api(url, { method: "DELETE" }))}>Delete</Button>
            <label className="field">Move to
              <input type="date" min={today} value={moveTo} onChange={(e) => setMoveTo(e.target.value)} />
            </label>
            <Button variant="secondary" disabled={!moveTo} onClick={() => run(() => api(`${url}/move`, { body: { to: moveTo } }))}>Move</Button>
            <Button variant="secondary" disabled={!!changed} title={changed ? "Save first" : undefined} onClick={showPreview}>Show workout</Button>
          </>
        ) : null}
        <Button variant="secondary" onClick={onClose}>Close</Button>
      </div>
      <p className="muted">Locked sessions stay as they are when the plan is rebuilt or adjusted. Edited sessions are kept too.</p>
      {warnings.map((w) => <p key={w}><Banner kind="amber">{w}</Banner></p>)}
      {error ? <p><Banner kind="error">{error}</Banner></p> : null}
      {preview?.workout ? (
        <div>
          <p className="muted">{preview.workout.name} · {preview.workout.purpose}</p>
          {preview.workout.exercises?.length ? <GymSession exercises={preview.workout.exercises} /> : null}
          {preview.workout.steps.length ? <WorkoutChart steps={preview.workout.steps} ftp={preview.ftp} sport={sport} /> : null}
          <p className="muted">As planned. On the day, Home adjusts it to your readiness.</p>
        </div>
      ) : null}
    </div>
  );
}

function LaterWeeks({ weeks }: { weeks: PlanWeek[] }) {
  return (
    <CollapsibleCard id="plan:later" title="Later weeks" defaultOpen={false}>
      <p className="muted">The sessions are filled in four weeks ahead, as the plan rolls forward. With a key, the AI coach plans each new
        week when you open this page.</p>
      <div className="scroll">
        <table>
          <thead><tr><th>Week of</th><th>Phase</th><th className="num">Hours</th><th className="num">Sessions</th><th>Targets by</th></tr></thead>
          <tbody>
            {weeks.map((w) => (
              <tr key={w.week_start}>
                <td>{shortDay(w.week_start)}</td>
                <td>{w.phase ? PHASE_LABEL[w.phase] ?? w.phase : "–"}</td>
                <td className="num">{w.target_hours?.toFixed(1) ?? "–"}{w.override_hours !== null ? " (set)" : ""}</td>
                <td className="num">{w.sessions ?? "–"}</td>
                <td>{w.builder === "ai" ? "AI coach" : "rules"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </CollapsibleCard>
  );
}
