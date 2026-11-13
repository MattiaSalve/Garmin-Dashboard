export const ZONE_COLORS = ["#90caf9", "#66bb6a", "#fdd835", "#fb8c00", "#e53935"];
export const CHANNEL_COLORS: Record<string, string> = {
  "Heart rate": "#e53935",
  Power: "#fb8c00",
  Speed: "#1e88e5",
  Elevation: "#9e9e9e",
  Cadence: "#8e24aa",
};
export const CHANNEL_ORDER = ["Heart rate", "Power", "Speed", "Elevation", "Cadence"];
export const POWER_ZONE_UPPER = [0.55, 0.75, 0.9, 1.05];

export const powerZone = (fraction: number): number => {
  const i = POWER_ZONE_UPPER.findIndex((upper) => fraction <= upper);
  return i === -1 ? 4 : i;
};

export const displaySport = (key: string | null | undefined): string =>
  key ? key.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase()) : "";

export function fmtHM(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return "–";
  const minutes = Math.round(seconds / 60);
  return `${Math.floor(minutes / 60)}:${String(minutes % 60).padStart(2, "0")}`;
}

export const fmt = (value: number | null | undefined, digits = 0, suffix = ""): string =>
  value === null || value === undefined || Number.isNaN(value)
    ? "–"
    : `${value.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits })}${suffix}`;

export const isoDate = (d: Date): string => {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
};

export const daysAgo = (n: number): string => {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return isoDate(d);
};

export function fmtDuration(seconds: number): string {
  return seconds < 60 ? `${seconds}s` : seconds < 3600 ? `${seconds / 60} min` : `${seconds / 3600} h`;
}

export type Channels = Record<string, { unit: string; values: (number | null)[] }>;

/** Trailing mean of `values` over the last `windowSeconds` (null = running mean from the start). */
export function trailingAverage(values: (number | null)[], minutes: (number | null)[], windowSeconds: number | null) {
  const out: (number | null)[] = new Array(values.length).fill(null);
  let sum = 0;
  let count = 0;
  let tail = 0;
  for (let i = 0; i < values.length; i++) {
    const v = values[i];
    const m = minutes[i];
    if (v === null || m === null) continue;
    sum += v;
    count++;
    if (windowSeconds !== null) {
      // Drop samples that fell out of the window.
      while (tail < i && (minutes[i] as number) * 60 - ((minutes[tail] ?? Infinity) as number) * 60 >= windowSeconds) {
        const t = values[tail];
        if (t !== null && minutes[tail] !== null) {
          sum -= t;
          count--;
        }
        tail++;
      }
    }
    out[i] = count ? sum / count : null;
  }
  return out;
}

/** Averages (and elevation gain) over the part of a ride between indices [from, to]. */
export function rangeStats(
  channels: Channels,
  minutes: (number | null)[],
  km: (number | null)[] | null,
  from: number,
  to: number,
): Record<string, string> {
  const stats: Record<string, string> = {};
  const m = minutes.slice(from, to + 1).filter((v): v is number => v !== null);
  if (m.length) {
    const span = Math.max(...m) - Math.min(...m);
    stats["Duration"] = span >= 60 ? `${Math.floor(span / 60)}h ${String(Math.floor(span % 60)).padStart(2, "0")}m` : `${span.toFixed(0)} min`;
  }
  if (km) {
    const k = km.slice(from, to + 1).filter((v): v is number => v !== null);
    if (k.length) stats["Distance"] = `${(Math.max(...k) - Math.min(...k)).toFixed(1)} km`;
  }
  for (const [title, { unit, values }] of Object.entries(channels)) {
    const v = values.slice(from, to + 1).filter((x): x is number => x !== null);
    if (!v.length) continue;
    if (title === "Elevation") {
      let gain = 0;
      for (let i = 1; i < v.length; i++) gain += Math.max(v[i] - v[i - 1], 0);
      stats["Elevation gain"] = `${gain.toFixed(0)} m`;
    } else {
      const mean = v.reduce((a, b) => a + b, 0) / v.length;
      stats[`Avg ${title.toLowerCase()}`] = `${mean.toFixed(title === "Speed" ? 1 : 0)} ${unit}`;
    }
  }
  return stats;
}

/** Keep roughly `max` evenly spaced indices of [from, to] so very long rides stay fast to draw. */
export function sampleIndices(from: number, to: number, max = 1500): number[] {
  const n = to - from + 1;
  const step = Math.max(1, Math.ceil(n / max));
  const out: number[] = [];
  for (let i = from; i <= to; i += step) out.push(i);
  if (out[out.length - 1] !== to) out.push(to);
  return out;
}

/** Web-mercator world position (0..1 on both axes) of a latitude/longitude. */
export function mercator(lat: number, lon: number): [number, number] {
  const s = Math.sin((lat * Math.PI) / 180);
  return [(lon + 180) / 360, 0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI)];
}

/**
 * Fit a route into a width x height box. Returns pixel points plus the (fractional) zoom and top-left
 * pixel offset, so map tiles can be laid under the same projection.
 */
export function fitRoute(route: [number, number][], width: number, height: number, pad = 28, maxZoom = 18) {
  const world = route.map(([la, lo]) => mercator(la, lo));
  const xs = world.map((p) => p[0]);
  const ys = world.map((p) => p[1]);
  const [minX, maxX, minY, maxY] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
  const spanX = Math.max(maxX - minX, 1e-9);
  const spanY = Math.max(maxY - minY, 1e-9);
  // 256 px tiles: world size in pixels is 256 * 2^zoom.
  const fit = Math.min((width - 2 * pad) / (spanX * 256), (height - 2 * pad) / (spanY * 256));
  const zoom = Math.min(Math.log2(fit), maxZoom);
  const scale = 256 * 2 ** zoom;
  const left = ((minX + maxX) / 2) * scale - width / 2;
  const top = ((minY + maxY) / 2) * scale - height / 2;
  return { zoom, left, top, points: world.map(([x, y]): [number, number] => [x * scale - left, y * scale - top]) };
}

/** Pace unit per sport ("" = watts or no pace). Mirrors SportProfile.pace_unit in workouts.py. */
export const PACE_UNIT: Record<string, string> = { cycling: "", running: "/km", swimming: "/100 m", gym: "" };
const PACE_SETTING: Record<string, string> = { running: "run_threshold_pace_s", swimming: "swim_threshold_pace_s" };

/** Threshold pace (seconds per pace unit) for a sport from the settings, or null when it isn't set. */
export function paceFor(sport: string, settings: Record<string, number | null | undefined>): number | null {
  const key = PACE_SETTING[sport];
  return (key && settings[key]) || null;
}

/** Rough 1-10 effort for an intensity as a fraction of threshold. Keep in step with workouts._rpe. */
export function rpeFor(fraction: number): string {
  const limits: [number, string][] = [[0.5, "rest"], [0.72, "2–3"], [0.83, "3–4"], [0.95, "5–6"], [1.05, "7"], [1.15, "8"]];
  for (const [limit, label] of limits) if (fraction <= limit) return label;
  return "9";
}

// ------------------------------------------------------------------ training plan

/** "45 min", "1 h", "1 h 15". Mirrors plan.fmt_minutes. */
export function fmtMinutes(minutes: number): string {
  const m = Math.round(minutes || 0);
  if (m < 60) return `${m} min`;
  return `${Math.floor(m / 60)} h${m % 60 ? ` ${String(m % 60).padStart(2, "0")}` : ""}`;
}

/** "4:30" for 270 seconds. */
export function fmtPace(seconds: number): string {
  const s = Math.round(seconds);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/** Seconds from "4:30", "4.30" or "270"; null when it can't be read. */
export function parsePace(text: string): number | null {
  const t = text.trim();
  const m = t.match(/^(\d{1,2})[:.](\d{1,2})$/);
  if (m) return Number(m[2]) < 60 ? Number(m[1]) * 60 + Number(m[2]) : null;
  return /^\d+$/.test(t) ? Number(t) : null;
}

export type DayStatus = "done" | "partial" | "missed" | "extra" | "rest" | "planned" | "today";

/** Chip text and colour class for a plan day's status (classes reuse the readiness dot colours). */
export const STATUS: Record<DayStatus, { label: string; tone: "good" | "caution" | "bad" | "nodata" | "" }> = {
  done: { label: "Done", tone: "good" },
  partial: { label: "Partly done", tone: "caution" },
  missed: { label: "Missed", tone: "bad" },
  extra: { label: "Unplanned", tone: "caution" },
  rest: { label: "Rest", tone: "nodata" },
  planned: { label: "Planned", tone: "" },
  today: { label: "Today", tone: "" },
};

/** "Tue 18 Nov" for an ISO date, in the browser's language. */
export function shortDay(iso: string): string {
  return new Date(iso + "T12:00:00").toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
}

export interface PlanChange {
  day: string;
  slot: number;
  before_label: string;
  after_label: string;
  after?: { note?: string } | null;
}

/** "Tue 18 Nov: Threshold ride, 1 h 15 → Endurance ride, 1 h" (a cleared session reads "→ Rest day"). */
export function changeLine(c: PlanChange, day: (iso: string) => string = shortDay): string {
  const before = c.before_label === "Nothing" ? "free" : c.before_label;
  const after = c.after_label === "Nothing" ? "Rest day" : c.after_label;
  return `${day(c.day)}${c.slot ? " (2nd)" : ""}: ${before} → ${after}`;
}

/** The time options of a sport plus `minutes` (e.g. today's planned 100-minute ride), sorted. */
export function withOption(options: number[], minutes: number | null | undefined): number[] {
  if (!minutes || options.includes(minutes)) return options;
  return [...options, minutes].sort((a, b) => a - b);
}

export interface PlanGoal { kind: string; sport: string | null; date: string | null; target: number | null; name: string }

const GOAL_SPORT: Record<string, string> = { cycling: "ride", running: "run", swimming: "swim" };

/** One-line description of a goal, e.g. "Gran fondo · 4 h ride on Sat 23 May (27 weeks)". */
export function goalLine(g: PlanGoal, today: string): string {
  const weeks = g.date ? Math.round((Date.parse(g.date) - Date.parse(today)) / (7 * 86400000)) : null;
  const when = g.date ? ` on ${shortDay(g.date)}${weeks !== null && weeks >= 1 ? ` (${weeks} week${weeks === 1 ? "" : "s"})` : ""}` : "";
  const sport = g.sport ? GOAL_SPORT[g.sport] ?? g.sport : "";
  let text: string;
  if (g.kind === "event") text = `${g.target ? `${fmtMinutes(g.target * 60)} ` : ""}${sport || "event"}${when}`;
  else if (g.kind === "volume") text = `${g.target ?? "?"} hours a week`;
  else if (g.kind === "performance") {
    const target = g.target === null ? "?" : g.sport === "cycling" ? `${g.target} W FTP`
      : `${fmtPace(g.target)} ${g.sport === "swimming" ? "/100 m" : "/km"} threshold pace`;
    text = `${target}${when}`;
  } else text = "General fitness";
  return g.name ? `${g.name} · ${text}` : text;
}
