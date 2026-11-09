import { ZONE_COLORS, powerZone } from "../lib";

export interface WorkoutStep {
  minutes: number;
  label: string;
  pct_ftp: string;
  frac_low: number;
  frac_high: number;
  // Cycling targets
  watts_low?: number;
  watts_high?: number;
  // Running targets (pace needs a threshold pace in Settings)
  rpe?: string;
  pace_fast?: string;
  pace_slow?: string;
  pace_unit?: string;
}

/** Text target for a step: watts for cycling, pace (or effort) for running. */
export function stepTarget(s: WorkoutStep): string {
  if (s.watts_low !== undefined) return `${s.watts_low}–${s.watts_high} W`;
  if (s.pace_fast) return `${s.pace_fast}–${s.pace_slow} ${s.pace_unit ?? ""}`;
  return s.rpe === "rest" ? "rest / walk" : `effort ${s.rpe}/10`;
}

/** Steps as blocks: width = minutes, height = intensity relative to threshold, colour = zone. */
export function WorkoutChart({ steps, ftp, sport }: { steps: WorkoutStep[]; ftp: number; sport: string }) {
  const total = steps.reduce((a, s) => a + s.minutes, 0);
  const top = Math.max(1.2, ...steps.map((s) => s.frac_high)) * 1.05;
  const W = 800;
  const H = 200;
  const thresholdY = H - (1 / top) * H;
  let x = 0;
  return (
    <svg viewBox={`0 0 ${W} ${H + 24}`} width="100%" role="img" aria-label="Workout steps">
      {steps.map((s, i) => {
        const w = (s.minutes / total) * W;
        const h = (s.frac_high / top) * H;
        const zone = powerZone((s.frac_low + s.frac_high) / 2);
        const rect = (
          <g key={i}>
            <title>{`${s.label}: ${s.minutes.toFixed(0)} min, ${stepTarget(s)} (${s.pct_ftp} of ${sport === "cycling" ? "FTP" : "threshold"})`}</title>
            <rect x={x} y={H - h} width={Math.max(w - 1, 1)} height={h} fill={ZONE_COLORS[zone]} rx={2} />
          </g>
        );
        x += w;
        return rect;
      })}
      <line x1={0} x2={W} y1={thresholdY} y2={thresholdY} stroke="#888" strokeDasharray="4 3" />
      <text x={4} y={thresholdY - 5} fontSize={12} fill="#888">{sport === "cycling" ? `FTP ${ftp} W` : "Threshold pace"}</text>
      <text x={0} y={H + 18} fontSize={12} fill="#888">0</text>
      <text x={W} y={H + 18} fontSize={12} fill="#888" textAnchor="end">{Math.round(total)} min</text>
    </svg>
  );
}
