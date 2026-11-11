import { useRef, useState } from "react";
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

/** Steps as blocks: width = minutes, height = intensity relative to threshold, colour = zone. Hover a block for its details. */
export function WorkoutChart({ steps, ftp, sport }: { steps: WorkoutStep[]; ftp: number; sport: string }) {
  const [hover, setHover] = useState<{ i: number; x: number; y: number } | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const total = steps.reduce((a, s) => a + s.minutes, 0);
  const top = Math.max(1.2, ...steps.map((s) => s.frac_high)) * 1.05;
  const W = 800;
  const H = 200;
  const thresholdY = H - (1 / top) * H;
  const cycling = sport === "cycling";
  let x = 0;
  let elapsed = 0;
  const starts = steps.map((s) => { const at = elapsed; elapsed += s.minutes; return at; });
  const tip = hover ? steps[hover.i] : null;
  const move = (i: number) => (e: React.MouseEvent) => {
    const r = box.current?.getBoundingClientRect();
    if (r) setHover({ i, x: e.clientX - r.left, y: e.clientY - r.top });
  };
  return (
    <div className="workout-chart" ref={box} onMouseLeave={() => setHover(null)}>
      <svg viewBox={`0 0 ${W} ${H + 24}`} width="100%" role="img" aria-label="Workout steps">
        {steps.map((s, i) => {
          const w = (s.minutes / total) * W;
          const h = (s.frac_high / top) * H;
          const zone = powerZone((s.frac_low + s.frac_high) / 2);
          const rect = (
            <g key={i} onMouseEnter={move(i)} onMouseMove={move(i)} style={{ cursor: "default" }}>
              <rect x={x} y={0} width={Math.max(w - 1, 1)} height={H} fill="transparent" />
              <rect x={x} y={H - h} width={Math.max(w - 1, 1)} height={h} fill={ZONE_COLORS[zone]} rx={2}
                opacity={hover && hover.i !== i ? 0.45 : 1} stroke={hover?.i === i ? "#888" : "none"} strokeWidth={2} />
            </g>
          );
          x += w;
          return rect;
        })}
        <line x1={0} x2={W} y1={thresholdY} y2={thresholdY} stroke="#888" strokeDasharray="4 3" pointerEvents="none" />
        <text x={4} y={thresholdY - 5} fontSize={12} fill="#888">{cycling ? `FTP ${ftp} W` : "Threshold pace"}</text>
        <text x={0} y={H + 18} fontSize={12} fill="#888">0</text>
        <text x={W} y={H + 18} fontSize={12} fill="#888" textAnchor="end">{Math.round(total)} min</text>
      </svg>
      {hover && tip ? (
        <div className="tooltip workout-tip" style={{ left: hover.x, top: hover.y }}>
          <div><b>{tip.label}</b></div>
          <div>Duration: <b>{Math.round(tip.minutes)} min</b> <span className="muted">({Math.round(starts[hover.i])}–{Math.round(starts[hover.i] + tip.minutes)} min in)</span></div>
          <div>Target: <b>{stepTarget(tip)}</b></div>
          <div>{cycling ? `${tip.pct_ftp} of FTP (${ftp} W)` : `${tip.pct_ftp} of threshold`}</div>
        </div>
      ) : null}
    </div>
  );
}
