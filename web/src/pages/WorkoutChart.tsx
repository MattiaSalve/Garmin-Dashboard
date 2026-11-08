import { ZONE_COLORS, powerZone } from "../lib";

export interface WorkoutStep {
  minutes: number;
  label: string;
  watts_low: number;
  watts_high: number;
  pct_ftp: string;
}

/** Steps as blocks: width = minutes, height = target watts, colour = power zone. */
export function WorkoutChart({ steps, ftp }: { steps: WorkoutStep[]; ftp: number }) {
  const total = steps.reduce((a, s) => a + s.minutes, 0);
  const top = Math.max(ftp * 1.2, ...steps.map((s) => s.watts_high)) * 1.05;
  const W = 800;
  const H = 200;
  let x = 0;
  return (
    <svg viewBox={`0 0 ${W} ${H + 24}`} width="100%" role="img" aria-label="Workout steps">
      {steps.map((s, i) => {
        const w = (s.minutes / total) * W;
        const h = (s.watts_high / top) * H;
        const zone = powerZone((s.watts_low + s.watts_high) / 2 / ftp);
        const rect = (
          <g key={i}>
            <title>{`${s.label}: ${s.minutes.toFixed(0)} min, ${s.watts_low}–${s.watts_high} W (${s.pct_ftp} FTP)`}</title>
            <rect x={x} y={H - h} width={Math.max(w - 1, 1)} height={h} fill={ZONE_COLORS[zone]} rx={2} />
          </g>
        );
        x += w;
        return rect;
      })}
      <line x1={0} x2={W} y1={H - (ftp / top) * H} y2={H - (ftp / top) * H} stroke="#888" strokeDasharray="4 3" />
      <text x={4} y={H - (ftp / top) * H - 5} fontSize={12} fill="#888">FTP {ftp} W</text>
      <text x={0} y={H + 18} fontSize={12} fill="#888">0</text>
      <text x={W} y={H + 18} fontSize={12} fill="#888" textAnchor="end">{Math.round(total)} min</text>
    </svg>
  );
}
