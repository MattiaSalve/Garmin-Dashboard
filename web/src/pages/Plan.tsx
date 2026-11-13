import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, useFetch } from "../api";
import { Banner, Loading } from "../components";
import {
  STATUS, changeLine, fmtMinutes, fmtPace, goalLine, parsePace, shortDay,
  type DayStatus, type PlanChange, type PlanGoal,
} from "../lib";
import { loadKey } from "./Coach";
import { GymSession, type GymExercise } from "./GymSession";
import { WorkoutChart, type WorkoutStep } from "./WorkoutChart";

// ------------------------------------------------------------------ API shapes

export interface PlanSession {
  day: string; slot: number; sport: string; kind: string; minutes: number; planned_load: number;
  source: "auto" | "user" | "coach" | "adjusted"; locked: boolean; note: string; label: string;
}
interface DoneActivity { activity_id: number; name: string; sport: string; minutes: number; km: number; load: number }
interface PlanDayView { day: string; status: DayStatus; sessions: PlanSession[]; activities: DoneActivity[] }
interface PlanWeek {
  week_start: string; past: boolean; phase: string | null; note: string; target_hours: number | null; sessions: number | null;
  detailed: boolean; override_hours: number | null; planned_hours: number; planned_load: number; actual_hours: number;
  actual_load: number; days: PlanDayView[];
}
export interface Proposal {
  id: number; kind: string; reason: string; source: "rules" | "coach"; created: string;
  changes: (PlanChange & { after: { note?: string } | null })[];
}
interface Profile { sports: Record<string, number>; hours_per_week: number; sessions_per_week: number | null; long_day: number }
interface Setup {
  configured: boolean; profile: Profile; goals: PlanGoal[];
  options: { sports: string[]; goal_kinds: string[]; weekdays: string[]; kinds: Record<string, string[]>; kind_labels: Record<string, string>; max_goals: number };
}
interface PlanView { setup: Setup; today: string; weeks: PlanWeek[]; proposals: Proposal[] }

const SPORT_LABEL: Record<string, string> = { cycling: "Cycling", running: "Running", swimming: "Swimming", gym: "Gym" };
const GOAL_LABEL: Record<string, string> = {
  event: "An event on a date", volume: "Train a number of hours a week", performance: "Reach an FTP or threshold pace", general: "General fitness",
};
const PHASE_LABEL: Record<string, string> = { base: "Base", build: "Build", recovery: "Recovery", taper: "Taper", race: "Event week" };
const SOURCE_NOTE: Record<string, string> = { user: "edited by you", coach: "from the coach", adjusted: "adjusted" };

// ------------------------------------------------------------------ page

export function Plan() {
  const { data, error, loading, reload } = useFetch<PlanView>("/plan");
  const [editingSetup, setEditingSetup] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ kind: "error" | "info"; text: string } | null>(null);

  if (!data) return <Loading error={error} loading={loading} />;
  const { setup } = data;

  const act = async (fn: () => Promise<unknown>, done?: string) => {
    setBusy(true);
    setMessage(null);
    try {
      await fn();
      if (done) setMessage({ kind: "info", text: done });
      reload();
    } catch (e) {
      setMessage({ kind: "error", text: (e as Error).message });
    } finally {
      setBusy(false);
    }
  };

  if (!setup.configured || editingSetup) {
    return (
      <SetupCard setup={setup} today={data.today} onCancel={setup.configured ? () => setEditingSetup(false) : undefined}
        onSaved={() => { setEditingSetup(false); reload(); }} />
    );
  }
  const now = data.weeks.filter((w) => w.detailed);
  const later = data.weeks.filter((w) => !w.detailed);
  return (
    <>
      <section className="card">
        <div className="card-head">
          <h2>Training plan</h2>
          <button className="secondary" onClick={() => setEditingSetup(true)}>Edit goals and schedule</button>
        </div>
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
        <div className="row">
          <button className="secondary" disabled={busy} title="Rebuild every automatic session from today on. Sessions you edited, locked or applied stay."
            onClick={() => act(() => api("/plan/generate", { method: "POST" }), "Plan rebuilt. Your own sessions were kept.")}>Regenerate</button>
          <button className="secondary" disabled={busy}
            onClick={() => { if (confirm("Delete the plan, its goals and every edit?")) act(() => api("/plan", { method: "DELETE" })); }}>Start over</button>
        </div>
        {message ? <p><Banner kind={message.kind}>{message.text}</Banner></p> : null}
      </section>
      <Proposals proposals={data.proposals} onChange={reload} />
      <Review onDone={reload} />
      {now.map((w) => <WeekCard key={w.week_start} week={w} today={data.today} setup={setup} onChange={reload} />)}
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

function SetupCard({ setup, today, onSaved, onCancel }: { setup: Setup; today: string; onSaved: () => void; onCancel?: () => void }) {
  const o = setup.options;
  const [shares, setShares] = useState<Record<string, number>>(() =>
    Object.fromEntries(o.sports.map((s) => [s, Math.round((setup.profile.sports[s] ?? 0) * 100)])));
  const [hours, setHours] = useState(String(setup.profile.hours_per_week));
  const [sessions, setSessions] = useState(setup.profile.sessions_per_week ? String(setup.profile.sessions_per_week) : "");
  const [longDay, setLongDay] = useState(setup.profile.long_day);
  const [goals, setGoals] = useState<GoalDraft[]>(() => setup.goals.map(toDraft));
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
          <button type="button" className="secondary" onClick={() => setGoals(goals.filter((_, j) => j !== i))}>Remove</button>
        </div>
      ))}
      {goals.length < o.max_goals ? (
        <p><button type="button" className="secondary"
          onClick={() => setGoals([...goals, { kind: "event", sport: endurance[0] ?? "", date: "", target: "", name: "" }])}>Add a goal</button></p>
      ) : null}
      {!goals.length ? <p className="muted">No goal: the plan keeps a steady 3-weeks-on, 1-week-easier rhythm for general fitness.</p> : null}

      {error ? <p><Banner kind="error">{error}</Banner></p> : null}
      <div className="row">
        <button disabled={busy || !chosen.length}>{busy ? "Building…" : setup.configured ? "Save and rebuild" : "Build my plan"}</button>
        {onCancel ? <button type="button" className="secondary" onClick={onCancel}>Cancel</button> : null}
      </div>
      {setup.configured ? <p className="muted">Saving rebuilds the automatic sessions from today on. Sessions you edited, locked or applied stay.</p> : null}
    </form>
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
    <section className="card">
      <h2>Suggested changes</h2>
      {proposals.map((p) => (
        <div key={p.id} className="proposal">
          <p><b>{p.source === "coach" ? "From the AI coach" : "From your training"}:</b> {p.reason}</p>
          <ul>
            {p.changes.map((c) => (
              <li key={`${c.day}:${c.slot}`}>{changeLine(c)}{c.after?.note ? <span className="muted"> · {c.after.note}</span> : null}</li>
            ))}
          </ul>
          <div className="row">
            <button disabled={busy === p.id} onClick={() => act(p, "apply")}>Apply</button>
            <button className="secondary" disabled={busy === p.id} onClick={() => act(p, "dismiss")}>Dismiss</button>
          </div>
        </div>
      ))}
      {error ? <p><Banner kind="error">{error}</Banner></p> : null}
    </section>
  );
}

interface ReviewReply { summary: string; cautions: string[]; plan_proposal: Proposal | null; plan_skipped: string[] }

/** Ask the AI coach to look over this week and next. Its changes arrive as a proposal above. */
function Review({ onDone }: { onDone: () => void }) {
  const models = useFetch<{ models: string[]; default: string }>("/coach/models");
  const [model, setModel] = useState("");
  const [message, setMessage] = useState("");
  const [reply, setReply] = useState<ReviewReply | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const key = loadKey();
  const ask = async () => {
    setBusy(true);
    setError(null);
    setReply(null);
    try {
      const r = await api<ReviewReply>("/plan/review", {
        body: {
          api_key: key, model: model || models.data?.default || null,
          history: message.trim() ? [{ role: "user", content: message.trim() }] : [],
        },
      });
      setReply(r);
      setMessage("");
      onDone();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="card">
      <details>
        <summary>Ask the AI coach to review the plan</summary>
        <p className="muted">
          The coach sees this week and next, how you followed the last two weeks, your recovery and your goals (no names, places or
          account details). Any changes it suggests appear above for you to apply or dismiss. Uses the OpenRouter key from the
          “Ask coach” panel{key ? "" : " (none entered yet)"}.
        </p>
        <label className="field" style={{ maxWidth: 320 }}>Model
          <select value={model || models.data?.default || ""} onChange={(e) => setModel(e.target.value)}>
            {models.data?.models.map((m) => <option key={m}>{m}</option>)}
          </select>
        </label>
        <label className="field">Anything to tell the coach? (optional)
          <textarea rows={2} maxLength={1000} value={message} onChange={(e) => setMessage(e.target.value)}
            placeholder="e.g. I can't train on Thursday next week, and I'd like a longer ride on Sunday instead of Saturday." />
        </label>
        <p><button disabled={busy} onClick={ask}>{busy ? "The coach is reviewing…" : "Review my plan"}</button></p>
        {error ? <Banner kind="error">{error}</Banner> : null}
        {reply ? (
          <div>
            <p>{reply.summary}</p>
            {reply.cautions.length ? <ul>{reply.cautions.map((c) => <li key={c}>{c}</li>)}</ul> : null}
            <p className="muted">{reply.plan_proposal ? "Its changes are listed under “Suggested changes”." : "No changes suggested."}</p>
            {reply.plan_skipped.length ? <p className="muted">Left out: {reply.plan_skipped.join(" ")}</p> : null}
          </div>
        ) : null}
      </details>
    </section>
  );
}

// ------------------------------------------------------------------ weeks and days

function WeekCard({ week, today, setup, onChange }: { week: PlanWeek; today: string; setup: Setup; onChange: () => void }) {
  const [open, setOpen] = useState<string | null>(null); // "day:slot", or "day:new" for a new session
  const [hours, setHours] = useState(week.override_hours === null ? "" : String(week.override_hours));
  const [error, setError] = useState<string | null>(null);
  useEffect(() => setHours(week.override_hours === null ? "" : String(week.override_hours)), [week.override_hours]);
  const saveHours = async (value: number | null) => {
    setError(null);
    try {
      await api(`/plan/week/${week.week_start}`, { method: "PUT", body: { hours: value } });
      onChange();
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const thisWeek = week.week_start <= today && today <= week.days[6]?.day;
  return (
    <section className="card">
      <div className="card-head">
        <h2>{thisWeek ? "This week" : week.past ? "Last week" : `Week of ${shortDay(week.week_start)}`}
          {week.phase ? <span className="muted"> · {PHASE_LABEL[week.phase] ?? week.phase}</span> : null}</h2>
        <span className="muted">
          {week.past || thisWeek ? `${week.actual_hours.toFixed(1)} of ${week.planned_hours.toFixed(1)} h done` : `${week.planned_hours.toFixed(1)} h planned`}
          {" · load "}{week.past || thisWeek ? `${week.actual_load} / ` : ""}{week.planned_load}
        </span>
      </div>
      {week.note ? <p className="muted">{week.note}</p> : null}
      <div className="plan-days">
        {week.days.map((d) => (
          <DayRow key={d.day} d={d} today={today} setup={setup} open={open} setOpen={setOpen} onChange={onChange} />
        ))}
      </div>
      {!week.past ? (
        <div className="row" style={{ marginTop: 12 }}>
          <label className="field">Hours available this week
            <input type="number" min={0} max={30} step={0.5} value={hours} placeholder={String(setup.profile.hours_per_week)}
              onChange={(e) => setHours(e.target.value)} style={{ width: 110 }} />
          </label>
          <button className="secondary" disabled={!hours || Number(hours) === week.override_hours} onClick={() => saveHours(Number(hours))}>Set</button>
          {week.override_hours !== null ? <button className="secondary" onClick={() => saveHours(null)}>Back to usual</button> : null}
          {error ? <span style={{ color: "var(--bad)" }}>{error}</span> : null}
        </div>
      ) : null}
    </section>
  );
}

function DayRow({ d, today, setup, open, setOpen, onChange }: {
  d: PlanDayView; today: string; setup: Setup; open: string | null; setOpen: (k: string | null) => void; onChange: () => void;
}) {
  const st = STATUS[d.status];
  const editable = d.day >= today;
  const sessions = d.sessions.filter((s) => s.kind !== "rest");
  const canAdd = editable && sessions.length < 2;
  return (
    <div className={`plan-day${d.day === today ? " today" : ""}`}>
      <div className="plan-day-head">
        <b>{shortDay(d.day)}</b>
        <span className="chip">{st.tone ? <span className={`dot ${st.tone}`} /> : null}{st.label}</span>
      </div>
      <div className="plan-day-body">
        {sessions.length ? sessions.map((s) => {
          const key = `${s.day}:${s.slot}`;
          return (
            <div key={key}>
              <button className="session" disabled={!editable} onClick={() => setOpen(open === key ? null : key)} aria-expanded={open === key}>
                {s.label}{s.locked ? " 🔒" : ""}
              </button>
              {SOURCE_NOTE[s.source] ? <span className="muted"> · {SOURCE_NOTE[s.source]}</span> : null}
              {s.note ? <div className="muted">{s.note}</div> : null}
              {open === key ? <SessionEditor session={s} setup={setup} today={today} onClose={() => setOpen(null)} onChange={onChange} /> : null}
            </div>
          );
        }) : <span className="muted">Rest</span>}
        {d.activities.map((a) => (
          <div key={a.activity_id} className="muted">
            ✓ <Link to={`/ride/${a.activity_id}`}>{a.name || "Activity"}</Link> · {fmtMinutes(a.minutes)}{a.km ? ` · ${a.km} km` : ""}{a.load ? ` · load ${a.load}` : ""}
          </div>
        ))}
        {canAdd ? (
          open === `${d.day}:new`
            ? <SessionEditor day={d.day} setup={setup} today={today} onClose={() => setOpen(null)} onChange={onChange} />
            : <button className="link" onClick={() => setOpen(`${d.day}:new`)}>+ Add session</button>
        ) : null}
      </div>
    </div>
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
        <button onClick={save}>{session ? "Save" : "Add"}</button>
        {session ? (
          <>
            <button className="secondary" onClick={() => run(() => api(url, { method: "DELETE" }))}>Delete</button>
            <label className="field">Move to
              <input type="date" min={today} value={moveTo} onChange={(e) => setMoveTo(e.target.value)} />
            </label>
            <button className="secondary" disabled={!moveTo} onClick={() => run(() => api(`${url}/move`, { body: { to: moveTo } }))}>Move</button>
            <button className="secondary" disabled={!!changed} title={changed ? "Save first" : undefined} onClick={showPreview}>Show workout</button>
          </>
        ) : null}
        <button className="secondary" onClick={onClose}>Close</button>
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
    <section className="card">
      <h2>Later weeks</h2>
      <p className="muted">The sessions are filled in four weeks ahead, as the plan rolls forward.</p>
      <div className="scroll">
        <table>
          <thead><tr><th>Week of</th><th>Phase</th><th className="num">Hours</th><th className="num">Sessions</th></tr></thead>
          <tbody>
            {weeks.map((w) => (
              <tr key={w.week_start}>
                <td>{shortDay(w.week_start)}</td>
                <td>{w.phase ? PHASE_LABEL[w.phase] ?? w.phase : "–"}</td>
                <td className="num">{w.target_hours?.toFixed(1) ?? "–"}{w.override_hours !== null ? " (set)" : ""}</td>
                <td className="num">{w.sessions ?? "–"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
