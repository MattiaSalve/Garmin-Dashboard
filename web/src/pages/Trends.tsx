import { useState } from "react";
import {
  Area, Bar, BarChart, CartesianGrid, ComposedChart, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { query, useFetch } from "../api";
import { ActivityRow, ActivityTable, Banner, ChartTooltip, Loading, Metric } from "../components";
import { ZONE_COLORS, daysAgo, displaySport, fmt, fmtDuration } from "../lib";

const CHARTS = [
  "Weekly distance", "Fitness and form", "Heart-rate zones", "Power curve",
  "Calendar", "Weekly training load", "Time by sport", "HRV and resting HR",
] as const;
type Chart = (typeof CHARTS)[number];

interface Range { start: string; end: string; sport: string | null }

export function Trends() {
  const [chart, setChart] = useState<Chart>("Weekly distance");
  const [start, setStart] = useState(daysAgo(365));
  const [end, setEnd] = useState(daysAgo(0));
  const [sport, setSport] = useState("");
  const filters = useFetch<{ sports: string[] }>("/filters");
  const range: Range = { start, end, sport: sport || null };

  return (
    <section className="card">
      <div className="row">
        <label className="field">Chart<select value={chart} onChange={(e) => setChart(e.target.value as Chart)}>{CHARTS.map((c) => <option key={c}>{c}</option>)}</select></label>
        <label className="field">From<input type="date" value={start} onChange={(e) => setStart(e.target.value)} /></label>
        <label className="field">To<input type="date" value={end} onChange={(e) => setEnd(e.target.value)} /></label>
        <label className="field">Sport
          <select value={sport} onChange={(e) => setSport(e.target.value)}>
            <option value="">All</option>
            {filters.data?.sports.map((s) => <option key={s} value={s}>{displaySport(s)}</option>)}
          </select>
        </label>
      </div>
      <div style={{ marginTop: 16 }}>
        {start > end ? <Banner kind="info">The start date must be before the end date.</Banner> : <ChartView chart={chart} range={range} />}
      </div>
    </section>
  );
}

function ChartView({ chart, range }: { chart: Chart; range: Range }) {
  const common = query({ start: range.start, end: range.end });
  const withSport = query({ start: range.start, end: range.end, sport: range.sport });
  switch (chart) {
    case "Weekly distance": return <Weekly path={`/trends/weekly${withSport}`} range={range} field="total_distance_km" title="Distance (km)" clickable />;
    case "Weekly training load": return <Weekly path={`/trends/weekly${withSport}`} range={range} field="total_training_load" title="Training load" />;
    case "Fitness and form": return <Fitness path={`/trends/fitness${common}`} />;
    case "Heart-rate zones": return <HrZones path={`/trends/hr-zones${withSport}`} />;
    case "Power curve": return <PowerCurve path={`/trends/power-curve${withSport}`} />;
    case "Calendar": return <Calendar path={`/trends/calendar${withSport}`} />;
    case "Time by sport": return <BySport path={`/trends/by-sport${common}`} />;
    case "HRV and resting HR": return <Recovery path={`/trends/recovery${common}`} />;
  }
}

function useRows<T>(path: string) {
  const r = useFetch<T[]>(path);
  const empty = r.data !== null && r.data.length === 0;
  return { ...r, empty };
}

function Wrap<T>({ state, empty = "No data in this range.", children }: { state: ReturnType<typeof useRows<T>>; empty?: string; children: (rows: T[]) => React.ReactNode }) {
  if (!state.data) return <Loading error={state.error} loading={state.loading} />;
  if (state.empty) return <Banner kind="info">{empty}</Banner>;
  return <>{children(state.data)}</>;
}

function Weekly({ path, range, field, title, clickable }: { path: string; range: Range; field: string; title: string; clickable?: boolean }) {
  const state = useRows<Record<string, number | string>>(path);
  const [week, setWeek] = useState<string | null>(null);
  const end = week ? new Date(new Date(week + "T12:00:00").getTime() + 6 * 86400000).toISOString().slice(0, 10) : null;
  const rides = useFetch<ActivityRow[]>(week ? `/activities${query({ start: week, end, sport: range.sport ? [range.sport] : null })}` : null);
  return (
    <Wrap state={state}>
      {(rows) => (
        <>
          {clickable ? <p className="muted">Click a week to see its rides.</p> : null}
          <ResponsiveContainer width="100%" height={300}>
            <BarChart data={rows} onClick={(e) => clickable && e?.activeLabel && setWeek(String(e.activeLabel))}>
              <CartesianGrid strokeOpacity={0.2} />
              <XAxis dataKey="week_start" minTickGap={30} tickFormatter={(s: string) => s.slice(2)} />
              <YAxis width={44} />
              <Tooltip content={({ payload, label }) => {
                const p = payload?.[0]?.payload;
                return p ? <ChartTooltip title={`Week of ${label}`} rows={[{ name: title, value: fmt(p[field], 1) }, { name: "Rides", value: String(p.activity_count) }]} /> : null;
              }} />
              <Bar dataKey={field} fill="#1e88e5" isAnimationActive={false} cursor={clickable ? "pointer" : undefined} />
            </BarChart>
          </ResponsiveContainer>
          {week ? <><h3>Week of {week} · {rides.data?.length ?? 0} activities</h3>{rides.data ? <ActivityTable rows={rides.data} /> : <Loading error={rides.error} loading={rides.loading} />}</> : null}
        </>
      )}
    </Wrap>
  );
}

function Fitness({ path }: { path: string }) {
  const state = useRows<{ day: string; fitness: number; fatigue: number; form: number }>(path);
  return (
    <Wrap state={state} empty="No training load data in this range.">
      {(rows) => (
        <>
          <p className="muted">Built from Garmin's per-activity training load across all sports. Fitness = 42-day average, fatigue = 7-day average, form = fitness minus fatigue.</p>
          <ResponsiveContainer width="100%" height={300}>
            <LineChart data={rows}>
              <CartesianGrid strokeOpacity={0.2} />
              <XAxis dataKey="day" minTickGap={40} />
              <YAxis width={40} />
              <Tooltip content={({ payload, label }) => payload?.length ? <ChartTooltip title={String(label)} rows={payload.map((p) => ({ name: String(p.name), value: fmt(p.value as number, 1), color: p.color }))} /> : null} />
              <Legend />
              <Line dataKey="fitness" stroke="#1e88e5" dot={false} isAnimationActive={false} />
              <Line dataKey="fatigue" stroke="#e53935" dot={false} isAnimationActive={false} />
              <Line dataKey="form" stroke="#43a047" dot={false} isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
        </>
      )}
    </Wrap>
  );
}

function HrZones({ path }: { path: string }) {
  const state = useRows<Record<string, number | string>>(path);
  const zones = [1, 2, 3, 4, 5].map((i) => `Zone ${i}`);
  return (
    <Wrap state={state} empty="No heart-rate zone data in this range.">
      {(rows) => {
        const totals = zones.map((z) => rows.reduce((a, r) => a + (Number(r[z]) || 0), 0));
        const sum = totals.reduce((a, b) => a + b, 0);
        return (
          <>
            <ResponsiveContainer width="100%" height={300}>
              <BarChart data={rows}>
                <CartesianGrid strokeOpacity={0.2} />
                <XAxis dataKey="week_start" minTickGap={30} tickFormatter={(s: string) => s.slice(2)} />
                <YAxis width={40} />
                <Tooltip content={({ payload, label }) => payload?.length ? <ChartTooltip title={`Week of ${label}`} rows={payload.map((p) => ({ name: String(p.name), value: `${fmt(p.value as number, 1)} h`, color: p.color }))} /> : null} />
                <Legend />
                {zones.map((z, i) => <Bar key={z} dataKey={z} stackId="z" fill={ZONE_COLORS[i]} isAnimationActive={false} />)}
              </BarChart>
            </ResponsiveContainer>
            <div className="grid">{zones.map((z, i) => <Metric key={z} label={z} value={`${sum ? ((totals[i] / sum) * 100).toFixed(0) : 0}%`} sub={`${totals[i].toFixed(1)} h`} />)}</div>
            <p className="muted">Zones as defined on your Garmin device. Rides without heart-rate data are skipped.</p>
          </>
        );
      }}
    </Wrap>
  );
}

function PowerCurve({ path }: { path: string }) {
  const state = useRows<{ seconds: number; watts: number }>(path);
  return (
    <Wrap state={state} empty="No power data in this range.">
      {(rows) => (
        <>
          <ResponsiveContainer width="100%" height={300}>
            <LineChart data={rows}>
              <CartesianGrid strokeOpacity={0.2} />
              <XAxis type="number" dataKey="seconds" scale="log" domain={["dataMin", "dataMax"]} ticks={rows.map((r) => r.seconds)} tickFormatter={fmtDuration} />
              <YAxis domain={["auto", "auto"]} width={44} />
              <Tooltip content={({ payload }) => payload?.length ? <ChartTooltip title={fmtDuration(payload[0].payload.seconds)} rows={[{ name: "Best average power (W)", value: fmt(payload[0].payload.watts) }]} /> : null} />
              <Line dataKey="watts" stroke="#fb8c00" isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
          <p className="muted">Best average power over each duration, across rides with a power meter in this range.</p>
        </>
      )}
    </Wrap>
  );
}

function Calendar({ path }: { path: string }) {
  const state = useRows<{ day: string; distance_km: number; hours: number; activity_count: number }>(path);
  return (
    <Wrap state={state} empty="No activities in this range.">
      {(rows) => {
        const byDay = new Map(rows.map((r) => [r.day, r]));
        const max = Math.max(...rows.map((r) => r.distance_km), 1);
        const first = new Date(rows[0].day + "T12:00:00");
        first.setDate(first.getDate() - ((first.getDay() + 6) % 7)); // back to Monday
        const last = new Date(rows[rows.length - 1].day + "T12:00:00");
        const weeks: Date[][] = [];
        for (const d = new Date(first); d <= last; d.setDate(d.getDate() + 7)) {
          weeks.push([...Array(7)].map((_, i) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + i, 12)));
        }
        return (
          <div className="scroll">
            <div style={{ display: "flex", gap: 3 }}>
              {weeks.map((w) => (
                <div key={w[0].toISOString()} style={{ display: "flex", flexDirection: "column", gap: 3 }}>
                  {w.map((d) => {
                    const key = d.toISOString().slice(0, 10);
                    const r = byDay.get(key);
                    return (
                      <div key={key} title={r ? `${key} · ${r.distance_km.toFixed(1)} km · ${r.hours.toFixed(1)} h · ${r.activity_count} activities` : key}
                        style={{ width: 13, height: 13, borderRadius: 2, background: r ? `rgba(30,136,229,${0.2 + 0.8 * (r.distance_km / max)})` : "var(--line)" }} />
                    );
                  })}
                </div>
              ))}
            </div>
            <p className="muted">Each square is a day; darker means more distance.</p>
          </div>
        );
      }}
    </Wrap>
  );
}

function BySport({ path }: { path: string }) {
  const state = useRows<{ sport_type: string; total_moving_duration_h: number }>(path);
  return (
    <Wrap state={state}>
      {(rows) => (
        <ResponsiveContainer width="100%" height={300}>
          <BarChart data={rows.map((r) => ({ ...r, sport: displaySport(r.sport_type) }))}>
            <CartesianGrid strokeOpacity={0.2} />
            <XAxis dataKey="sport" />
            <YAxis width={40} />
            <Tooltip content={({ payload, label }) => payload?.length ? <ChartTooltip title={String(label)} rows={[{ name: "Hours", value: fmt(payload[0].value as number, 1) }]} /> : null} />
            <Bar dataKey="total_moving_duration_h" fill="#1e88e5" isAnimationActive={false} />
          </BarChart>
        </ResponsiveContainer>
      )}
    </Wrap>
  );
}

function Recovery({ path }: { path: string }) {
  const state = useRows<Record<string, number | string | null>>(path);
  return (
    <Wrap state={state} empty="No recovery data in this range. Sync from Garmin, or use a full re-sync for older history.">
      {(rows) => {
        const data = rows.map((r) => ({
          ...r,
          band: r.hrv_baseline_low !== null && r.hrv_baseline_high !== null ? [r.hrv_baseline_low, r.hrv_baseline_high] : null,
        }));
        return (
          <>
            <div className="muted">Overnight HRV · dots nightly, line 7-day average, band Garmin baseline</div>
            <ResponsiveContainer width="100%" height={260}>
              <ComposedChart data={data}>
                <CartesianGrid strokeOpacity={0.2} />
                <XAxis dataKey="day" minTickGap={40} />
                <YAxis domain={["auto", "auto"]} width={40} />
                <Tooltip content={({ payload, label }) => {
                  const p = payload?.[0]?.payload;
                  return p ? <ChartTooltip title={String(label)} rows={[{ name: "HRV (ms)", value: fmt(p.hrv_avg) }, { name: "7-day avg (ms)", value: fmt(p.hrv_weekly_avg) }]} /> : null;
                }} />
                <Area dataKey="band" stroke="none" fill="#1e88e5" fillOpacity={0.15} isAnimationActive={false} connectNulls />
                <Line dataKey="hrv_avg" stroke="none" dot={{ r: 2, fill: "#1e88e5", fillOpacity: 0.5 }} isAnimationActive={false} connectNulls />
                <Line dataKey="hrv_weekly_avg" stroke="#1e88e5" dot={false} isAnimationActive={false} connectNulls />
              </ComposedChart>
            </ResponsiveContainer>
            <div className="muted">Resting heart rate</div>
            <ResponsiveContainer width="100%" height={160}>
              <LineChart data={data}>
                <CartesianGrid strokeOpacity={0.2} />
                <XAxis dataKey="day" minTickGap={40} />
                <YAxis domain={["auto", "auto"]} width={40} />
                <Tooltip content={({ payload, label }) => payload?.length ? <ChartTooltip title={String(label)} rows={[{ name: "Resting HR", value: fmt(payload[0].value as number) }]} /> : null} />
                <Line dataKey="rhr" stroke="#e53935" dot={false} isAnimationActive={false} connectNulls />
              </LineChart>
            </ResponsiveContainer>
          </>
        );
      }}
    </Wrap>
  );
}
