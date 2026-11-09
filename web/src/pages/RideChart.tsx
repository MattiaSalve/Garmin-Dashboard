import { useEffect, useMemo, useState } from "react";
import { Area, CartesianGrid, ComposedChart, Line, ReferenceArea, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { ChartTooltip, Metric, Pills } from "../components";
import { CHANNEL_COLORS, CHANNEL_ORDER, Channels, rangeStats, sampleIndices, trailingAverage } from "../lib";

export interface Samples {
  minutes: (number | null)[];
  km: (number | null)[] | null;
  channels: Channels;
  route: [number, number, number][] | null; // [lat, lon, sample index]
}

const SMOOTHING: Record<string, number | null> = { Raw: 0, "3 s": 3, "30 s": 30 };

/** What the chart is pointing at, as sample indices: the dragged or zoomed part, and the hovered sample. */
export interface Highlight { range: [number, number] | null; hover: number | null }

export function RideChart({ samples, onHighlight }: { samples: Samples; onHighlight?: (h: Highlight) => void }) {
  const titles = CHANNEL_ORDER.filter((t) => t in samples.channels);
  const optional = titles.filter((t) => t !== "Elevation");
  const hasElevation = titles.includes("Elevation");
  const [on, setOn] = useState<string[]>(() => (hasElevation ? [] : optional.slice(0, 1)));
  const [axis, setAxis] = useState<"Distance" | "Time">(samples.km ? "Distance" : "Time");
  const [smooth, setSmooth] = useState("30 s");
  const [zoom, setZoom] = useState<[number, number] | null>(null);
  const [drag, setDrag] = useState<{ from: number; to: number } | null>(null);

  const xs = axis === "Distance" && samples.km ? samples.km : samples.minutes;
  const xTitle = axis === "Distance" && samples.km ? "Distance (km)" : "Moving time (min)";
  const shown = [...(hasElevation ? ["Elevation"] : []), ...on];

  const values = useMemo(() => {
    const out: Record<string, (number | null)[]> = {};
    for (const t of titles) {
      const v = samples.channels[t].values;
      out[t] = t === "Power" && SMOOTHING[smooth] ? trailingAverage(v, samples.minutes, SMOOTHING[smooth]) : v;
    }
    return out;
  }, [samples, smooth, titles.join()]); // eslint-disable-line react-hooks/exhaustive-deps

  const [from, to] = useMemo(() => {
    if (!zoom) return [0, xs.length - 1];
    let a = xs.findIndex((x) => x !== null && x >= zoom[0]);
    let b = xs.length - 1;
    while (b > 0 && (xs[b] === null || (xs[b] as number) > zoom[1])) b--;
    if (a < 0) a = 0;
    return [a, Math.max(a, b)];
  }, [zoom, xs]);

  const [hover, setHover] = useState<number | null>(null);
  // First sample at or after an x position (km or minutes).
  const indexAt = (x: number) => Math.max(0, xs.findIndex((v) => v !== null && v >= x));
  const dragRange: [number, number] | null = drag ? [indexAt(Math.min(drag.from, drag.to)), indexAt(Math.max(drag.from, drag.to))] : null;
  const rangeKey = dragRange ? dragRange.join() : zoom ? `${from},${to}` : "";
  useEffect(() => {
    onHighlight?.({ range: rangeKey ? (rangeKey.split(",").map(Number) as [number, number]) : null, hover });
  }, [rangeKey, hover]); // eslint-disable-line react-hooks/exhaustive-deps

  const data = useMemo(
    () => sampleIndices(from, to).filter((i) => xs[i] !== null).map((i) => {
      const row: Record<string, number | null> = { x: xs[i], i };
      for (const t of shown) row[t] = values[t][i];
      return row;
    }),
    [from, to, xs, values, shown.join()], // eslint-disable-line react-hooks/exhaustive-deps
  );

  const stats = useMemo(() => {
    const picked: Channels = {};
    for (const t of shown) picked[t] = { unit: samples.channels[t].unit, values: values[t] };
    return rangeStats(picked, samples.minutes, axis === "Distance" ? samples.km : null, from, to);
  }, [shown.join(), values, from, to, axis, samples]); // eslint-disable-line react-hooks/exhaustive-deps

  const toggle = (t: string) => setOn(on.includes(t) ? on.filter((x) => x !== t) : [...on, t]);
  const label = (e: { activeLabel?: string | number } | null) => (e && e.activeLabel !== undefined ? Number(e.activeLabel) : null);

  return (
    <div>
      <div className="row">
        {optional.map((t) => (
          <label key={t}>
            <input type="checkbox" checked={on.includes(t)} onChange={() => toggle(t)} />{" "}
            <span style={{ color: CHANNEL_COLORS[t] }}>●</span> {t}
          </label>
        ))}
        {samples.km ? <Pills options={["Distance", "Time"] as const} value={axis} onChange={(a) => { setAxis(a); setZoom(null); }} /> : null}
        {on.includes("Power") ? <><span className="muted">Power smoothing</span><Pills options={Object.keys(SMOOTHING)} value={smooth} onChange={setSmooth} /></> : null}
        {zoom ? <button className="secondary" onClick={() => setZoom(null)}>Reset zoom</button> : null}
      </div>
      {!shown.length ? <p className="muted">Switch on a channel above to plot it.</p> : (
        <>
          {!zoom ? <p className="muted">Drag across the chart to zoom in on that part of the ride.</p> : null}
          <ResponsiveContainer width="100%" height={380}>
            <ComposedChart
              data={data}
              onMouseDown={(e) => { const x = label(e); if (x !== null) setDrag({ from: x, to: x }); }}
              onMouseMove={(e) => {
                const x = label(e);
                if (drag && x !== null) setDrag({ ...drag, to: x });
                const row = data[Number(e.activeTooltipIndex)];
                setHover(row && typeof row.i === "number" ? row.i : null);
              }}
              onMouseUp={() => {
                if (drag && Math.abs(drag.to - drag.from) > 0) setZoom([Math.min(drag.from, drag.to), Math.max(drag.from, drag.to)]);
                setDrag(null);
              }}
              onMouseLeave={() => { setDrag(null); setHover(null); }}
            >
              <CartesianGrid strokeOpacity={0.2} />
              <XAxis type="number" dataKey="x" domain={["dataMin", "dataMax"]} tickFormatter={(v: number) => v.toFixed(axis === "Distance" ? 0 : 0)} label={{ value: xTitle, position: "insideBottom", offset: -4 }} height={44} />
              {shown.map((t) => (
                <YAxis key={t} yAxisId={t} hide={t !== "Elevation"} orientation="left" width={t === "Elevation" ? 44 : 0}
                  domain={t === "Elevation" ? ["auto", "auto"] : ["auto", "auto"]} />
              ))}
              <Tooltip
                isAnimationActive={false}
                content={({ payload }) => {
                  const row = payload?.[0]?.payload as Record<string, number | null> | undefined;
                  if (!row) return null;
                  return (
                    <ChartTooltip
                      title={`${xTitle.split(" (")[0]}: ${(row.x as number).toFixed(2)} ${axis === "Distance" ? "km" : "min"}`}
                      rows={shown.map((t) => ({
                        name: `${t} (${samples.channels[t].unit})`,
                        value: row[t] === null || row[t] === undefined ? "–" : (row[t] as number).toFixed(2),
                        color: CHANNEL_COLORS[t],
                      }))}
                    />
                  );
                }}
              />
              {shown.map((t) =>
                t === "Elevation" ? (
                  <Area key={t} yAxisId={t} dataKey={t} stroke={CHANNEL_COLORS[t]} fill={CHANNEL_COLORS[t]} fillOpacity={0.3} strokeOpacity={0.7} dot={false} isAnimationActive={false} connectNulls />
                ) : (
                  <Line key={t} yAxisId={t} dataKey={t} stroke={CHANNEL_COLORS[t]} strokeOpacity={0.7} dot={false} isAnimationActive={false} connectNulls />
                ),
              )}
              {drag ? <ReferenceArea yAxisId={shown[0]} x1={drag.from} x2={drag.to} fill="#888" fillOpacity={0.2} /> : null}
            </ComposedChart>
          </ResponsiveContainer>
          <h3>Averages — {zoom ? "selected part" : "whole ride"}</h3>
          <div className="grid">{Object.entries(stats).map(([k, v]) => <Metric key={k} label={k} value={v} />)}</div>
        </>
      )}
    </div>
  );
}
