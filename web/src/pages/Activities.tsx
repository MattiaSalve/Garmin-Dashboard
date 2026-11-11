import { useMemo, useState } from "react";
import { query, useFetch } from "../api";
import { ActivityRow, ActivityTable, Loading, Metric } from "../components";
import { daysAgo, displaySport, fmt, fmtHM, isoDate } from "../lib";
import { useWorkout } from "../workout";
import { Week, useHomeData } from "./Home";

interface Summary {
  activity_count: number; total_distance_km: number; total_moving_duration_h: number; total_elevation_gain_m: number;
}

const delta = (now: number, before: number, unit: string, digits = 0) =>
  before ? `${now - before >= 0 ? "+" : ""}${(now - before).toFixed(digits)} ${unit}`.trim() : undefined;

export function Activities() {
  const week = useFetch<Summary>(`/summary${query({ start: daysAgo(6), end: daysAgo(0) })}`);
  const prev = useFetch<Summary>(`/summary${query({ start: daysAgo(13), end: daysAgo(7) })}`);
  const rows = useFetch<ActivityRow[]>(`/activities${query({ start: daysAgo(6), end: daysAgo(0) })}`);
  const s = week.data, p = prev.data;
  const { sport, minutes } = useWorkout();
  const home = useHomeData(minutes, sport);
  return (
    <>
    <section className="card">
      <h2>Last 7 days</h2>
      <Loading error={week.error ?? rows.error} loading={week.loading} />
      {s && p ? (
        <>
          <div className="grid">
            <Metric label="Activities" value={s.activity_count} sub={delta(s.activity_count, p.activity_count, "")} />
            <Metric label="Distance" value={`${fmt(s.total_distance_km)} km`} sub={delta(s.total_distance_km, p.total_distance_km, "km")} />
            <Metric label="Time" value={fmtHM(s.total_moving_duration_h * 3600)} sub={delta(s.total_moving_duration_h, p.total_moving_duration_h, "h", 1)} />
            <Metric label="Elevation" value={`${fmt(s.total_elevation_gain_m)} m`} sub={delta(s.total_elevation_gain_m, p.total_elevation_gain_m, "m")} />
          </div>
          <p className="muted">Changes compare with the 7 days before.</p>
        </>
      ) : null}
      {rows.data ? <ActivityTable rows={rows.data} /> : null}
    </section>
    {home.data ? <Week d={home.data} /> : <Loading error={home.error} loading={home.loading} />}
    </>
  );
}

interface Filters { sports: string[]; bounds: { km: number | null }; orders: string[] }

const PERIODS = ["All time", "Last 30 days", "Last 90 days", "This year", "Last year"] as const;

function periodRange(period: (typeof PERIODS)[number]): { start?: string; end?: string } {
  const year = new Date().getFullYear();
  switch (period) {
    case "Last 30 days": return { start: daysAgo(29) };
    case "Last 90 days": return { start: daysAgo(89) };
    case "This year": return { start: isoDate(new Date(year, 0, 1)) };
    case "Last year": return { start: isoDate(new Date(year - 1, 0, 1)), end: isoDate(new Date(year - 1, 11, 31)) };
    default: return {};
  }
}

export function Find() {
  const filters = useFetch<Filters>("/filters");
  const [text, setText] = useState("");
  const [sports, setSports] = useState<string[]>([]);
  const [period, setPeriod] = useState<(typeof PERIODS)[number]>("All time");
  const [order, setOrder] = useState("Newest first");
  const [num, setNum] = useState<Record<string, string>>({});

  const path = useMemo(
    () =>
      `/activities${query({
        ...periodRange(period), sport: sports, text, order,
        min_km: num.minKm, max_km: num.maxKm, min_elevation_m: num.minElev, max_elevation_m: num.maxElev,
        min_hours: num.minH, max_hours: num.maxH,
      })}`,
    [text, sports, period, order, num],
  );
  const rows = useFetch<ActivityRow[]>(filters.data?.bounds.km ? path : null);

  if (!filters.data) return <Loading error={filters.error} loading={filters.loading} />;
  if (!filters.data.bounds.km) return <div className="card"><p className="muted">No activities yet. Sync from Garmin first.</p></div>;

  const number = (key: string, label: string) => (
    <label className="field" style={{ width: 110 }}>{label}
      <input type="number" min={0} value={num[key] ?? ""} placeholder="any" onChange={(e) => setNum({ ...num, [key]: e.target.value })} />
    </label>
  );
  return (
    <section className="card">
      <h2>Find activities</h2>
      <div className="row">
        <label className="field">Search<input value={text} placeholder="Name or place" onChange={(e) => setText(e.target.value)} /></label>
        <label className="field">Sport
          <select multiple size={Math.min(filters.data.sports.length, 4) || 1} value={sports}
            onChange={(e) => setSports([...e.target.selectedOptions].map((o) => o.value))}>
            {filters.data.sports.map((s) => <option key={s} value={s}>{displaySport(s)}</option>)}
          </select>
        </label>
        <label className="field">Period
          <select value={period} onChange={(e) => setPeriod(e.target.value as (typeof PERIODS)[number])}>{PERIODS.map((p) => <option key={p}>{p}</option>)}</select>
        </label>
        <label className="field">Sort by
          <select value={order} onChange={(e) => setOrder(e.target.value)}>{filters.data.orders.map((o) => <option key={o}>{o}</option>)}</select>
        </label>
      </div>
      <div className="row" style={{ marginTop: 8 }}>
        {number("minKm", "Min km")}{number("maxKm", "Max km")}
        {number("minElev", "Min elev. (m)")}{number("maxElev", "Max elev. (m)")}
        {number("minH", "Min hours")}{number("maxH", "Max hours")}
        {sports.length ? <button className="secondary" onClick={() => setSports([])}>Clear sports</button> : null}
      </div>
      <p className="muted">{rows.data ? `${rows.data.length} activities` : ""}</p>
      <Loading error={rows.error} loading={rows.loading} />
      {rows.data ? <ActivityTable rows={rows.data} /> : null}
    </section>
  );
}
