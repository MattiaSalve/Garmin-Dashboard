import {
  Area, CartesianGrid, ComposedChart, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { Link } from "react-router-dom";
import { useEffect, useState } from "react";
import { ActivityRow, Banner, ChartTooltip, Loading, Metric } from "../components";
import { api, query, useFetch } from "../api";
import { ZONE_COLORS, daysAgo, fmt, fmtDuration, withOption } from "../lib";
import { SPORTS, TIME_OPTIONS, useWorkout, type Sport } from "../workout";
import { useSync } from "../sync";
import { GymExercise, GymInfo, GymSession, MuscleStrip } from "./GymSession";
import { WorkoutChart, WorkoutStep, stepTarget } from "./WorkoutChart";

const HRV = "#1e88e5";
const SPORT_LABEL: Record<Sport, string> = { cycling: "Cycling", running: "Running", swimming: "Swimming", gym: "Gym" };
const LONGEST: Record<Sport, string> = { cycling: "ride", running: "run", swimming: "swim", gym: "session" };
interface Level { level: string; source: "set" | "estimated"; reasons: string[] }

interface Wellness {
  day: string; hrv_avg: number | null; hrv_baseline_low: number | null; hrv_baseline_high: number | null;
  hrv_status: string | null; rhr: number | null; sleep_score: number | null; sleep_s: number | null; bb_high: number | null;
}
interface Signal { name: string; status: string; value: string; explanation: string }
export interface HomeData {
  week_hours_all: number;
  today: string; has_wellness: boolean; sport: Sport; levels: Record<Sport, Level>; threshold_pace_s: number | null; pace_unit: string;
  readiness: { level: "green" | "amber" | "red"; score: number; headline: string; signals: Signal[]; hrv_range: [number, number] | null; has_today_data: boolean };
  today_row: Wellness | null; form: number | null; ftp: number;
  workout: { name: string; purpose: string; note: string; minutes: number; rationale: string[]; steps: WorkoutStep[]; exercises?: GymExercise[] };
  gym: GymInfo | null;
  wellness: Wellness[]; intensity: { day: string; load: number }[];
  week: { week_start: string; hours: number; distance_km: number; load: number; activity_count: number; avg_hours: number; avg_load: number; easy_h: number; moderate_h: number; hard_h: number };
  week_activities: ActivityRow[]; ramp: number | null; ramp_warning: number;
  settings: Record<string, number | null>;
  power_prs: { seconds: number; watts: number; previous_watts: number | null }[];
  year: { year: number; km: number; goal: number | null; days_elapsed: number; days_in_year: number; sessions: number; longest: { activity_name: string; day: string; distance_m: number } | null };
  ftp_trend: { month: string; ftp_estimate: number }[];
  ftp_hint: { estimate: number; current: number } | null;
  /** Today's session from the training plan, after today's readiness check (null without a plan). */
  plan_today: PlanToday | null;
  plan_proposals: number;
}

interface PlanSessionShort { sport: string; kind: string; minutes: number; label: string }
export interface PlanToday {
  planned: PlanSessionShort; sport: string; kind: string; minutes: number; changed: boolean; reason: string | null;
  others: PlanSessionShort[]; used: boolean;
}

const planMinutes = (m: number) => Math.min(600, Math.max(20, m));

export function useHomeData(minutes: number, sport: Sport) {
  const { gymFocus } = useWorkout();
  return useFetch<HomeData>(`/home${query({ minutes, sport, focus: sport === "gym" && gymFocus !== "auto" ? gymFocus : null })}`);
}

const signed = (v: number, digits = 0) => `${v >= 0 ? "+" : ""}${v.toFixed(digits)}`;

export function Home() {
  const { minutes, setMinutes, sport, setSport, override } = useWorkout();
  const { data, error, loading, reload } = useHomeData(minutes, sport);

  // Once a day, open Home on today's planned sport and length. After that the athlete's own choice sticks.
  const plan = data?.plan_today;
  useEffect(() => {
    if (!data || !plan || plan.sport === "rest" || !SPORTS.includes(plan.sport as Sport)) return;
    const key = `plan_applied:${data.today}`;
    try {
      if (sessionStorage.getItem(key)) return;
      sessionStorage.setItem(key, "1");
    } catch { return; }
    if (override) return; // a coach workout accepted today wins
    if (plan.sport !== sport) setSport(plan.sport as Sport);
    if (plan.planned.minutes !== minutes) setMinutes(planMinutes(plan.planned.minutes));
  }, [data, plan]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!data) return <Loading error={error} loading={loading} />;
  return (
    <>
      {data.plan_proposals ? (
        <p><Banner kind="info">
          Your plan has {data.plan_proposals === 1 ? "a suggested change" : `${data.plan_proposals} suggested changes`}.{" "}
          <Link to="/plan">Review on the Plan page →</Link>
        </Banner></p>
      ) : null}
      {data.ftp_hint ? <FtpHint hint={data.ftp_hint} onUpdated={reload} /> : null}
      <Today d={data} minutes={minutes} setMinutes={setMinutes} sport={sport} setSport={setSport} />
      <RecoverySummary d={data} />
    </>
  );
}

/** Offer to raise FTP when recent 20-minute power is clearly above it. Dismissing hides this estimate only. */
function FtpHint({ hint, onUpdated }: { hint: { estimate: number; current: number }; onUpdated: () => void }) {
  const key = `ftp-hint-dismissed:${hint.estimate}`;
  const [hidden, setHidden] = useState(() => localStorage.getItem(key) !== null);
  const [error, setError] = useState<string | null>(null);
  if (hidden) return null;
  const update = async () => {
    try {
      await api("/settings", { method: "PUT", body: { ftp_w: hint.estimate } });
      onUpdated();
    } catch (err) {
      setError((err as Error).message);
    }
  };
  return (
    <p>
      <Banner kind="info">
        Your best 20-minute power in the last 90 days suggests an FTP of about {hint.estimate} W (set: {hint.current} W).{" "}
        <button onClick={update}>Update FTP</button>{" "}
        <button className="secondary" onClick={() => { localStorage.setItem(key, "1"); setHidden(true); }}>Dismiss</button>
        {error ? <> · {error}</> : null}
      </Banner>
    </p>
  );
}

/** Upload the workout to Garmin Connect, scheduled for today, so it appears on the watch after its next sync. */
function SendToWatch({ name, sport, steps, exercises }: { name: string; sport: Sport; steps: WorkoutStep[]; exercises?: GymExercise[] }) {
  const [state, setState] = useState<{ busy: boolean; ok?: string; error?: string }>({ busy: false });
  const send = async () => {
    setState({ busy: true });
    try {
      const r = await api<{ replaced: boolean }>("/workout/push", {
        body: {
          sport, name,
          steps: steps.map(({ minutes, frac_low, frac_high, label }) => ({ minutes, frac_low, frac_high, label })),
          exercises: (exercises ?? []).map(({ name, group, sets, reps, rpe, rest_s, note }) => ({ name, group, sets, reps, rpe, rest_s, note: note ?? "" })),
        },
      });
      setState({ busy: false, ok: `${r.replaced ? "Replaced today's earlier copy. " : ""}Scheduled for today in Garmin Connect: sync your watch to get it.` });
    } catch (err) {
      setState({ busy: false, error: (err as Error).message });
    }
  };
  return (
    <p className="row">
      <button className="secondary" disabled={state.busy} onClick={send}>{state.busy ? "Sending…" : "Send to watch"}</button>
      {state.ok ? <span className="muted">{state.ok}</span> : null}
      {state.error ? <span style={{ color: "var(--bad)" }}>{state.error}</span> : null}
    </p>
  );
}

function Today({ d, minutes, setMinutes, sport, setSport }: {
  d: HomeData; minutes: number; setMinutes: (m: number) => void; sport: Sport; setSport: (s: Sport) => void;
}) {
  const p = d.plan_today;
  const options = withOption(TIME_OPTIONS[sport], p && p.planned.sport === sport ? planMinutes(p.planned.minutes) : null);
  const level = d.levels[sport];
  const { override, clearOverride, gymFocus, setGymFocus } = useWorkout();
  const sync = useSync();
  const w = override ?? d.workout;
  const r = d.readiness;
  const t = d.today_row;
  const date = new Date(d.today + "T12:00:00").toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" });
  return (
    <section className="card">
      <h2>Today · {date}</h2>
      {!d.has_wellness ? <Banner kind="info">No recovery data yet. Press “Sync from Garmin” to download HRV, sleep and resting HR.</Banner>
        : !r.has_today_data ? (
          <Banner kind="info">
            Last night's recovery isn't synced yet. Open Garmin Connect on your phone so the watch uploads, then{" "}
            <button className="secondary" disabled={sync.busy} onClick={() => sync.run()}>{sync.busy ? "Syncing…" : "Sync now"}</button>
          </Banner>
        ) : null}
      <p><Banner kind={r.level}>Readiness {r.score}/100 · {r.headline}</Banner></p>
      <div className="grid">
        <Metric label="HRV" value={t?.hrv_avg ? `${fmt(t.hrv_avg)} ms` : "–"} sub={r.hrv_range ? `range ${r.hrv_range[0].toFixed(0)}–${r.hrv_range[1].toFixed(0)}` : null} />
        <Metric label="Resting HR" value={t?.rhr ? `${fmt(t.rhr)} bpm` : "–"} />
        <Metric label="Sleep" value={t?.sleep_score ? fmt(t.sleep_score) : "–"} sub={t?.sleep_s ? `${(t.sleep_s / 3600).toFixed(1)} h` : null} />
        <Metric label="Body Battery" value={t?.bb_high ? fmt(t.bb_high) : "–"} title="Highest Body Battery level today." />
        <Metric label="Form" value={d.form === null ? "–" : signed(d.form)} title="Fitness minus fatigue. Negative means tired." />
      </div>
      <details>
        <summary>Why this verdict</summary>
        <ul style={{ listStyle: "none", paddingLeft: 0 }}>
          {r.signals.map((s) => (
            <li key={s.name}><span className={`dot ${s.status.replace(" ", "")}`} /><b>{s.name}</b> · {s.value} · {s.explanation}</li>
          ))}
        </ul>
        {w.rationale.length > 1 ? <ul>{w.rationale.slice(1).map((x) => <li key={x}>{x}</li>)}</ul> : null}
        <p className="muted">Each caution costs 15 points and each bad signal 35. 75+ is green, 45–74 amber, below 45 red. Set your own HRV range in Settings.</p>
      </details>

      {p ? <PlanLine p={p} sport={sport} minutes={minutes} setSport={setSport} setMinutes={setMinutes} /> : null}
      <h3>{override ? "Coach's workout" : w.steps.length || w.exercises?.length ? "Suggested workout" : "Suggestion"} · {w.name}</h3>
      {override ? <p><Banner kind="info">You accepted this workout from the coach. <button className="secondary" onClick={clearOverride}>Back to suggested workout</button></Banner></p> : null}
      <div className="row">
        <label className="field">Sport today
          <select value={sport} onChange={(e) => setSport(e.target.value as Sport)}>
            {SPORTS.map((x) => <option key={x} value={x}>{SPORT_LABEL[x]}</option>)}
          </select>
        </label>
        {sport === "gym" ? (
          <label className="field">What to train
            <select value={gymFocus} onChange={(e) => setGymFocus(e.target.value)}>
              <option value="auto">Let the app choose (recommended)</option>
              {Object.entries(d.gym?.focus_options ?? {}).map(([k, label]) => <option key={k} value={k}>{label}</option>)}
            </select>
          </label>
        ) : null}
      </div>
      <details>
        <summary>Level: {level.level} {sport} ({level.source === "set" ? "set in Settings" : "estimated"})</summary>
        <ul>{level.reasons.map((x) => <li key={x}>{x}</li>)}</ul>
        <p className="muted">
          Readiness and form use all your sports. The level picks how hard and long the {sport} session is. Change it in Settings if it is wrong.
        </p>
      </details>
      <label className="field" style={{ maxWidth: 280 }}>Time available: {minutes} min
        <input type="range" min={0} max={options.length - 1} value={Math.max(options.indexOf(minutes), 0)}
          onChange={(e) => setMinutes(options[Number(e.target.value)])} />
      </label>
      <p className="muted">{w.purpose}{w.steps.length || w.exercises?.length ? ` · about ${w.minutes} min` : ""}</p>
      {w.rationale[0] ? <p className="muted">{w.rationale[0]}</p> : null}
      {sport === "gym" && d.gym ? <MuscleStrip gym={d.gym} /> : null}
      {w.exercises?.length ? <GymSession exercises={w.exercises} /> : null}
      {w.steps.length ? <WorkoutChart steps={w.steps} ftp={d.ftp} sport={sport} /> : null}
      {sport !== "cycling" && !d.threshold_pace_s && w.steps.length
        ? <p className="muted">Add your threshold pace in Settings to see target paces; until then steps show effort on a 1–10 scale.</p> : null}
      {w.steps.length ? <details>
        <summary>Steps</summary>
        <table>
          <thead><tr><th>Step</th><th className="num">Min</th><th className="num">Target</th><th className="num">{sport === "cycling" ? "% FTP" : "% threshold"}</th></tr></thead>
          <tbody>{w.steps.map((s, i) => (
            <tr key={i}><td>{s.label}</td><td className="num">{Math.round(s.minutes)}</td><td className="num">{stepTarget(s)}</td><td className="num">{s.pct_ftp}</td></tr>
          ))}</tbody>
        </table>
      </details> : null}
      {w.note ? <p className="muted">Note: {w.note}</p> : null}
      {(sport === "gym" ? w.exercises?.length : w.steps.length)
        ? <SendToWatch key={`${sport}:${w.name}:${w.minutes}`} name={w.name} sport={sport} steps={w.steps} exercises={w.exercises} />
        : null}
    </section>
  );
}

/** "Plan: Threshold ride, 1 h 15 · today Endurance, because readiness is amber", with a shortcut back to the plan. */
function PlanLine({ p, sport, minutes, setSport, setMinutes }: {
  p: PlanToday; sport: Sport; minutes: number; setSport: (s: Sport) => void; setMinutes: (m: number) => void;
}) {
  const rest = p.planned.kind === "rest";
  const follows = p.used && minutes === planMinutes(p.planned.minutes);
  const canFollow = !rest && SPORTS.includes(p.planned.sport as Sport) && !follows;
  return (
    <p className="muted">
      <Link to="/plan">Plan</Link>: <b>{p.planned.label}</b>
      {p.others.length ? ` (+ ${p.others.map((o) => o.label).join(", ")})` : ""}
      {p.changed && p.reason ? <> · {p.reason}</> : null}
      {canFollow ? (
        <> · <button className="link" onClick={() => {
          if (p.planned.sport !== sport) setSport(p.planned.sport as Sport);
          setMinutes(planMinutes(p.planned.minutes));
        }}>Use the planned session</button></>
      ) : null}
      {!p.used && !rest && p.planned.sport === sport ? " · you picked a different session" : ""}
    </p>
  );
}

function RecoverySummary({ d }: { d: HomeData }) {
  const since = daysAgo(29);
  const rows = d.wellness.filter((w) => w.day >= since);
  const data = rows.map((w) => ({
    ...w,
    band: w.hrv_baseline_low !== null && w.hrv_baseline_high !== null ? [w.hrv_baseline_low, w.hrv_baseline_high] : null,
  }));
  const hasHrv = rows.some((w) => w.hrv_avg !== null);
  const r = d.readiness;
  return (
    <section className="card">
      <div className="card-head">
        <h2>Recovery · last 30 days</h2>
        <Link to="/trends">View trends →</Link>
      </div>
      {!hasHrv ? <Banner kind="info">No HRV data in this range yet.</Banner> : (
        <ResponsiveContainer width="100%" height={70}>
          <ComposedChart data={data}>
            <XAxis dataKey="day" hide />
            <YAxis domain={["auto", "auto"]} hide />
            <Tooltip content={({ payload, label }) => {
              const p = payload?.[0]?.payload as Wellness | undefined;
              return p ? <ChartTooltip title={String(label)} rows={[{ name: "HRV (ms)", value: fmt(p.hrv_avg, 0), color: HRV }]} /> : null;
            }} />
            <Area dataKey="band" stroke="none" fill={HRV} fillOpacity={0.15} isAnimationActive={false} connectNulls />
            <Line dataKey="hrv_avg" stroke={HRV} dot={false} connectNulls isAnimationActive={false} />
          </ComposedChart>
        </ResponsiveContainer>
      )}
      <p className="muted">
        {d.today_row?.hrv_avg ? `HRV ${fmt(d.today_row.hrv_avg)} ms` : "No HRV today"}
        {r.hrv_range ? ` · range ${r.hrv_range[0].toFixed(0)}–${r.hrv_range[1].toFixed(0)}` : ""}
      </p>
    </section>
  );
}


export function Week({ d }: { d: HomeData }) {
  const w = d.week;
  const target = d.settings.weekly_hours_target;
  const total = w.easy_h + w.moderate_h + w.hard_h;
  const split = [
    { name: "Easy (Z1–2)", hours: w.easy_h, color: ZONE_COLORS[1] },
    { name: "Moderate (Z3)", hours: w.moderate_h, color: ZONE_COLORS[2] },
    { name: "Hard (Z4–5)", hours: w.hard_h, color: ZONE_COLORS[4] },
  ];
  const start = new Date(w.week_start + "T12:00:00").toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
  return (
    <section className="card">
      <h2>This week · {d.sport} since {start}</h2>
      <div className="grid">
        <Metric label="Hours" value={w.hours.toFixed(1)} sub={`${signed(w.hours - w.avg_hours, 1)} vs 4-wk avg`} />
        {d.sport === "gym" ? null : <Metric label="Distance" value={`${fmt(w.distance_km)} km`} />}
        <Metric label="Load" value={fmt(w.load)} sub={`${signed(w.load - w.avg_load)} vs 4-wk avg`} />
        <Metric label="Activities" value={w.activity_count} />
      </div>
      {target ? <p><progress max={1} value={Math.min(d.week_hours_all / target, 1)} /><span className="muted">{d.week_hours_all.toFixed(1)} of {target.toFixed(0)} h weekly target (all sports)</span></p> : null}
      {total > 0 ? (
        <>
          <div style={{ display: "flex", height: 22, borderRadius: 6, overflow: "hidden", marginTop: 8 }}>
            {split.map((s) => <div key={s.name} title={`${s.name}: ${s.hours.toFixed(1)} h (${((s.hours / total) * 100).toFixed(0)}%)`} style={{ width: `${(s.hours / total) * 100}%`, background: s.color }} />)}
          </div>
          <div className="legend">{split.map((s) => <span key={s.name}><i style={{ background: s.color }} />{s.name} {s.hours.toFixed(1)} h</span>)}</div>
        </>
      ) : null}
      {d.ramp !== null ? (
        d.ramp > d.ramp_warning
          ? <p><Banner kind="amber">Fitness (all sports) rose {d.ramp.toFixed(1)} points in 7 days. Ramping this fast raises injury and illness risk.</Banner></p>
          : <p className="muted">Ramp rate: fitness (all sports) {signed(d.ramp, 1)} in the last 7 days (keep it under {d.ramp_warning}).</p>
      ) : null}
    </section>
  );
}

export function Records({ d }: { d: HomeData }) {
  const y = d.year;
  const expected = y.goal ? (y.goal * y.days_elapsed) / y.days_in_year : null;
  return (
    <section className="card">
      <h2>Records and milestones</h2>
      {d.sport !== "cycling" ? null : d.power_prs.length ? (
        <>
          <b>New power bests in the last 4 weeks</b>
          <div className="grid">
            {d.power_prs.slice(0, 8).map((p) => (
              <Metric key={p.seconds} label={fmtDuration(p.seconds)} value={`${fmt(p.watts)} W`} sub={p.previous_watts ? `+${fmt(p.watts - p.previous_watts)} W` : "first"} />
            ))}
          </div>
        </>
      ) : <p className="muted">No new power bests in the last 4 weeks.</p>}
      <div className="grid" style={{ marginTop: 12 }}>
        <div>
          <Metric label={`${SPORT_LABEL[d.sport]} ${y.year}`} value={d.sport === "gym" ? `${y.sessions} sessions` : `${fmt(y.km)} km`} sub={expected !== null && d.sport === "cycling" ? `${signed(y.km - expected)} km vs pace` : d.sport === "cycling" ? "Set a yearly goal in Settings." : null} />
          {y.goal && d.sport === "cycling" ? <><progress max={1} value={Math.min(y.km / y.goal, 1)} /><span className="muted">{((y.km / y.goal) * 100).toFixed(0)}% of {fmt(y.goal)} km</span></> : null}
        </div>
        {y.longest?.distance_m ? <Metric label={`Longest ${LONGEST[d.sport]} this year`} value={`${fmt(y.longest.distance_m / 1000)} km`} sub={`${y.longest.activity_name} · ${y.longest.day}`} /> : null}
      </div>
      {d.ftp_trend.length >= 2 ? (
        <>
          <h3>Estimated FTP from your best 20-minute efforts</h3>
          <ResponsiveContainer width="100%" height={200}>
            <LineChart data={d.ftp_trend}>
              <CartesianGrid strokeOpacity={0.2} />
              <XAxis dataKey="month" minTickGap={24} />
              <YAxis domain={["auto", "auto"]} width={40} />
              <Tooltip content={({ payload, label }) => payload?.length ? <ChartTooltip title={String(label)} rows={[{ name: "Estimate (W)", value: fmt(payload[0].value as number) }]} /> : null} />
              <Line dataKey="ftp_estimate" stroke={ZONE_COLORS[3]} dot connectNulls isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
        </>
      ) : null}
    </section>
  );
}
