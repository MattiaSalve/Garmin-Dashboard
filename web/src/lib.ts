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
