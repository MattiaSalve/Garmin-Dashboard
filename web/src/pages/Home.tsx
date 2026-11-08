import { useState } from "react";
import {
  Area, Bar, BarChart, CartesianGrid, ComposedChart, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { ActivityRow, ActivityTable, Banner, ChartTooltip, Loading, Metric, Pills } from "../components";
import { query, useFetch } from "../api";
import { ZONE_COLORS, daysAgo, fmt, fmtDuration } from "../lib";
import { Coach } from "./Coach";
import { WorkoutChart, WorkoutStep } from "./WorkoutChart";

const HRV = "#1e88e5", RHR = "#e53935", SLEEP = "#8e24aa", LOAD = "#bdbdbd";
const TIME_OPTIONS = [30, 45, 60, 75, 90, 120, 150, 180];

interface Wellness {
  day: string; hrv_avg: number | null; hrv_baseline_low: number | null; hrv_baseline_high: number | null;
  hrv_status: string | null; rhr: number | null; sleep_score: number | null; sleep_s: number | null; bb_high: number | null;
}
interface Signal { name: string; status: string; value: string; explanation: string }
interface HomeData {
  today: string; has_wellness: boolean;
  readiness: { level: "green" | "amber" | "red"; score: number; headline: string; signals: Signal[]; hrv_range: [number, number] | null };
  today_row: Wellness | null; form: number | null; ftp: number;
  workout: { name: string; purpose: string; note: string; minutes: number; rationale: string[]; steps: WorkoutStep[] };
  wellness: Wellness[]; intensity: { day: string; load: number }[];
  week: { week_start: string; hours: number; distance_km: number; load: number; activity_count: number; avg_hours: number; avg_load: number; easy_h: number; moderate_h: number; hard_h: number };
  week_activities: ActivityRow[]; ramp: number | null; ramp_warning: number;
  settings: Record<string, number | null>;
  power_prs: { seconds: number; watts: number; previous_watts: number | null }[];
  year: { year: number; km: number; goal: number | null; days_elapsed: number; days_in_year: number; longest: { activity_name: string; day: string; distance_m: number } | null };
  ftp_trend: { month: string; ftp_estimate: number }[];
}

const signed = (v: number, digits = 0) => `${v >= 0 ? "+" : ""}${v.toFixed(digits)}`;

export function Home() {
  const [minutes, setMinutes] = useState(60);
  const { data, error, loading } = useFetch<HomeData>(`/home${query({ minutes })}`);
  if (!data) return <Loading error={error} loading={loading} />;
  return (
    <>
      <Today d={data} minutes={minutes} setMinutes={setMinutes} />
      <Recovery d={data} />
      <Week d={data} />
      <Records d={data} />
    </>
  );
}

function Today({ d, minutes, setMinutes }: { d: HomeData; minutes: number; setMinutes: (m: number) => void }) {
  const r = d.readiness;
  const t = d.today_row;
  const date = new Date(d.today + "T12:00:00").toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" });
  return (
    <section className="card">
      <h2>Today · {date}</h2>
      {!d.has_wellness ? <Banner kind="info">No recovery data yet. Press “Sync from Garmin” to download HRV, sleep and resting HR.</Banner> : null}
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
        <p className="muted">Each caution costs 15 points and each bad signal 35. 75+ is green, 45–74 amber, below 45 red. Set your own HRV range in Settings.</p>
      </details>

      <h3>Suggested workout · {d.workout.name}</h3>
      <label className="field" style={{ maxWidth: 280 }}>Time available: {minutes} min
        <input type="range" min={0} max={TIME_OPTIONS.length - 1} value={TIME_OPTIONS.indexOf(minutes)}
          onChange={(e) => setMinutes(TIME_OPTIONS[Number(e.target.value)])} />
      </label>
      <p className="muted">{d.workout.purpose} · {d.workout.minutes} min</p>
      <ul>{d.workout.rationale.map((x) => <li key={x}>{x}</li>)}</ul>
      <WorkoutChart steps={d.workout.steps} ftp={d.ftp} />
      <details>
        <summary>Steps</summary>
        <table>
          <thead><tr><th>Step</th><th className="num">Min</th><th className="num">Watts</th><th className="num">% FTP</th></tr></thead>
          <tbody>{d.workout.steps.map((s, i) => (
            <tr key={i}><td>{s.label}</td><td className="num">{Math.round(s.minutes)}</td><td className="num">{s.watts_low}–{s.watts_high}</td><td className="num">{s.pct_ftp}</td></tr>
          ))}</tbody>
        </table>
      </details>
      {d.workout.note ? <p className="muted">Note: {d.workout.note}</p> : null}
      <Coach minutes={minutes} ftp={d.ftp} />
    </section>
  );
}

function Recovery({ d }: { d: HomeData }) {
  const [days, setDays] = useState(30);
  const since = daysAgo(days - 1);
  const rows = d.wellness.filter((w) => w.day >= since);
  const loads = d.intensity.filter((l) => l.day >= since);
  const hasHrv = rows.some((w) => w.hrv_avg !== null);
  const data = rows.map((w) => ({
    ...w,
    band: w.hrv_baseline_low !== null && w.hrv_baseline_high !== null ? [w.hrv_baseline_low, w.hrv_baseline_high] : null,
  }));
  const shortDay = (s: string) => s.slice(5);
  return (
    <section className="card">
      <h2>Recovery trends <Pills options={[30, 90]} value={days} onChange={setDays} label={(n) => `${n} days`} /></h2>
      {!hasHrv ? <Banner kind="info">No HRV data in this range yet.</Banner> : (
        <>
          <div className="muted">HRV · shaded band is your Garmin baseline</div>
          <ResponsiveContainer width="100%" height={220}>
            <ComposedChart data={data}>
              <CartesianGrid strokeOpacity={0.2} />
              <XAxis dataKey="day" tickFormatter={shortDay} minTickGap={24} />
              <YAxis domain={["auto", "auto"]} width={40} />
              <Tooltip content={({ payload, label }) => {
                const p = payload?.[0]?.payload as Wellness | undefined;
                return p ? <ChartTooltip title={String(label)} rows={[
                  { name: "HRV (ms)", value: fmt(p.hrv_avg, 0), color: HRV }, { name: "Garmin status", value: p.hrv_status ?? "–" },
                ]} /> : null;
              }} />
              <Area dataKey="band" stroke="none" fill={HRV} fillOpacity={0.15} isAnimationActive={false} connectNulls />
              <Line dataKey="hrv_avg" stroke={HRV} dot={{ r: 3 }} connectNulls isAnimationActive={false} />
            </ComposedChart>
          </ResponsiveContainer>
          {loads.length ? (
            <ResponsiveContainer width="100%" height={80}>
              <BarChart data={loads}>
                <XAxis dataKey="day" tickFormatter={shortDay} minTickGap={24} />
                <YAxis width={40} />
                <Tooltip content={({ payload, label }) => payload?.length ? <ChartTooltip title={String(label)} rows={[{ name: "Training load", value: fmt(payload[0].value as number) }]} /> : null} />
                <Bar dataKey="load" fill={LOAD} isAnimationActive={false} />
              </BarChart>
            </ResponsiveContainer>
          ) : null}
        </>
      )}
      <div className="grid" style={{ marginTop: 12 }}>
        <SmallLine data={rows} dataKey="rhr" title="Resting HR (bpm)" color={RHR} />
        <SmallLine data={rows} dataKey="sleep_score" title="Sleep score" color={SLEEP} />
      </div>
    </section>
  );
}

function SmallLine({ data, dataKey, title, color }: { data: Wellness[]; dataKey: keyof Wellness; title: string; color: string }) {
  return (
    <div>
      <div className="muted">{title}</div>
      <ResponsiveContainer width="100%" height={160}>
        <LineChart data={data}>
          <CartesianGrid strokeOpacity={0.2} />
          <XAxis dataKey="day" tickFormatter={(s: string) => s.slice(5)} minTickGap={24} />
          <YAxis domain={["auto", "auto"]} width={36} />
          <Tooltip content={({ payload, label }) => payload?.length ? <ChartTooltip title={String(label)} rows={[{ name: title, value: fmt(payload[0].value as number) }]} /> : null} />
          <Line dataKey={dataKey} stroke={color} dot={{ r: 2 }} connectNulls isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

function Week({ d }: { d: HomeData }) {
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
      <h2>This week · since {start}</h2>
      <div className="grid">
        <Metric label="Hours" value={w.hours.toFixed(1)} sub={`${signed(w.hours - w.avg_hours, 1)} vs 4-wk avg`} />
        <Metric label="Distance" value={`${fmt(w.distance_km)} km`} />
        <Metric label="Load" value={fmt(w.load)} sub={`${signed(w.load - w.avg_load)} vs 4-wk avg`} />
        <Metric label="Activities" value={w.activity_count} />
      </div>
      {target ? <p><progress max={1} value={Math.min(w.hours / target, 1)} /><span className="muted">{w.hours.toFixed(1)} of {target.toFixed(0)} h weekly target</span></p> : null}
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
          ? <p><Banner kind="amber">Fitness rose {d.ramp.toFixed(1)} points in 7 days. Ramping this fast raises injury and illness risk.</Banner></p>
          : <p className="muted">Ramp rate: fitness {signed(d.ramp, 1)} in the last 7 days (keep it under {d.ramp_warning}).</p>
      ) : null}
      <ActivityTable rows={d.week_activities} />
    </section>
  );
}

function Records({ d }: { d: HomeData }) {
  const y = d.year;
  const expected = y.goal ? (y.goal * y.days_elapsed) / y.days_in_year : null;
  return (
    <section className="card">
      <h2>Records and milestones</h2>
      {d.power_prs.length ? (
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
          <Metric label={`Cycling ${y.year}`} value={`${fmt(y.km)} km`} sub={expected !== null ? `${signed(y.km - expected)} km vs pace` : "Set a yearly goal in Settings."} />
          {y.goal ? <><progress max={1} value={Math.min(y.km / y.goal, 1)} /><span className="muted">{((y.km / y.goal) * 100).toFixed(0)}% of {fmt(y.goal)} km</span></> : null}
        </div>
        {y.longest?.distance_m ? <Metric label="Longest ride this year" value={`${fmt(y.longest.distance_m / 1000)} km`} sub={`${y.longest.activity_name} · ${y.longest.day}`} /> : null}
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
