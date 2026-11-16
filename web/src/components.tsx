import { ChevronDown } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { displaySport, fmt, fmtHM } from "./lib";

export function Metric({ label, value, sub, title }: { label: string; value: ReactNode; sub?: ReactNode; title?: string }) {
  return (
    <div className="metric" title={title}>
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      {sub ? <div className="sub">{sub}</div> : null}
    </div>
  );
}

export function Banner({ kind, children }: { kind: "green" | "amber" | "red" | "error" | "info"; children: ReactNode }) {
  return <div className={`banner ${kind}`}>{children}</div>;
}

export function Pills<T extends string | number>({
  options, value, onChange, label = String,
}: { options: T[]; value: T; onChange: (v: T) => void; label?: (v: T) => string }) {
  return (
    <span className="pills">
      {options.map((o) => (
        <button key={String(o)} className={o === value ? "on" : ""} onClick={() => onChange(o)}>
          {label(o)}
        </button>
      ))}
    </span>
  );
}

export function Loading({ error, loading }: { error: string | null; loading: boolean }) {
  if (error) return <Banner kind="error">{error}</Banner>;
  return loading ? <p className="muted">Loading…</p> : null;
}

export interface ActivityRow {
  activity_id: number;
  start_time_local: string;
  activity_name: string;
  sport_type: string | null;
  distance_m: number | null;
  moving_duration_s: number | null;
  elevation_gain_m: number | null;
  avg_hr: number | null;
  avg_power_w: number | null;
}

export function ActivityTable({ rows }: { rows: ActivityRow[] }) {
  if (!rows.length) return <p className="muted">No activities match.</p>;
  return (
    <div className="scroll">
      <table>
        <thead>
          <tr>
            <th>Date</th><th>Name</th><th>Sport</th>
            <th className="num">Distance</th><th className="num">Time</th><th className="num">Elev.</th>
            <th className="num">Avg HR</th><th className="num">Avg W</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.activity_id} className="click">
              <td>{r.start_time_local.slice(0, 10)}</td>
              <td><Link to={`/ride/${r.activity_id}`}>{r.activity_name || "Untitled"}</Link></td>
              <td>{displaySport(r.sport_type)}</td>
              <td className="num">{fmt(r.distance_m === null ? null : r.distance_m / 1000, 1, " km")}</td>
              <td className="num">{fmtHM(r.moving_duration_s)}</td>
              <td className="num">{fmt(r.elevation_gain_m, 0, " m")}</td>
              <td className="num">{fmt(r.avg_hr)}</td>
              <td className="num">{fmt(r.avg_power_w)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ChartTooltip({ title, rows }: { title?: string; rows: { name: string; value: string; color?: string }[] }) {
  return (
    <div className="tooltip">
      {title ? <div className="t">{title}</div> : null}
      {rows.map((r) => (
        <div key={r.name}>
          {r.color ? <span className="dot" style={{ background: r.color }} /> : null}
          {r.name}: <b>{r.value}</b>
        </div>
      ))}
    </div>
  );
}

const OPEN_KEY = (id: string) => `card_open:${id}`;

/** A card with a title that folds away. The state is remembered in this browser per `id`. */
export function CollapsibleCard({ id, title, aside, defaultOpen = true, children }: {
  id: string; title: ReactNode; aside?: ReactNode; defaultOpen?: boolean; children: ReactNode;
}) {
  const [open, setOpenState] = useState(() => {
    try { const v = localStorage.getItem(OPEN_KEY(id)); return v === null ? defaultOpen : v === "1"; } catch { return defaultOpen; }
  });
  const setOpen = (o: boolean) => {
    setOpenState(o);
    try { localStorage.setItem(OPEN_KEY(id), o ? "1" : "0"); } catch { /* not remembered */ }
  };
  return (
    <section className="card">
      <div className="card-head">
        <button type="button" className="fold" aria-expanded={open} onClick={() => setOpen(!open)}>
          <ChevronDown size={18} className={open ? "" : "-rotate-90"} aria-hidden />
          <h2>{title}</h2>
        </button>
        {aside ? <div className="card-aside">{aside}</div> : null}
      </div>
      {open ? <div className="fold-body">{children}</div> : null}
    </section>
  );
}
