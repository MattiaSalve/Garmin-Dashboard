import { useMemo, useState } from "react";
import type { Highlight } from "./RideChart";
import { fitRoute } from "../lib";

const W = 800;
const H = 420;
const TILE_URL = (z: number, x: number, y: number) => `https://tile.openstreetmap.org/${z}/${x}/${y}.png`;

/**
 * The path of an outdoor activity. By default it is drawn on a plain background, so nothing about
 * where you rode leaves this page. The street map is opt-in: it loads tiles from openstreetmap.org,
 * which tells them roughly which area you are looking at.
 */
const STREETS_KEY = "route_street_map";

export function RouteMap({ route, highlight }: { route: [number, number, number][]; highlight: Highlight }) {
  // Remembered, so the choice is made once.
  const [streets, setStreetsState] = useState(() => localStorage.getItem(STREETS_KEY) === "1");
  const [tilesFailed, setTilesFailed] = useState(false);
  const setStreets = (on: boolean) => {
    localStorage.setItem(STREETS_KEY, on ? "1" : "0");
    setTilesFailed(false);
    setStreetsState(on);
  };
  const fit = useMemo(() => fitRoute(route.map(([la, lo]) => [la, lo]), W, H), [route]);
  const path = fit.points.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`).join("");
  const [start, end] = [fit.points[0], fit.points[fit.points.length - 1]];

  // The part of the route selected on the chart (route[k][2] is the chart's sample index).
  const [a, b] = highlight.range ?? [0, 0];
  const selected = highlight.range
    ? fit.points.filter((_, k) => route[k][2] >= a && route[k][2] <= b)
    : [];
  const selectedPath = selected.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`).join("");
  const hoverAt = highlight.hover === null ? -1 : route.findIndex((p) => p[2] >= highlight.hover!);
  const hoverPoint = hoverAt >= 0 ? fit.points[hoverAt] : null;

  const tiles = useMemo(() => {
    if (!streets) return [];
    const z = Math.max(0, Math.floor(fit.zoom));
    const size = 256 * 2 ** (fit.zoom - z); // tiles are stretched slightly to match the fractional zoom
    const count = 2 ** z;
    const out: { key: string; href: string; x: number; y: number }[] = [];
    for (let ty = Math.floor(fit.top / size); ty <= Math.floor((fit.top + H) / size); ty++) {
      for (let tx = Math.floor(fit.left / size); tx <= Math.floor((fit.left + W) / size); tx++) {
        if (ty < 0 || ty >= count) continue;
        out.push({ key: `${tx}/${ty}`, href: TILE_URL(z, ((tx % count) + count) % count, ty), x: tx * size - fit.left, y: ty * size - fit.top });
      }
    }
    return out.map((t) => ({ ...t, size }));
  }, [streets, fit]);

  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="Map of the route" style={{ background: "#f1f3f4", borderRadius: 8 }}>
        {tiles.map((t) => <image key={t.key} href={t.href} x={t.x} y={t.y} width={t.size + 0.5} height={t.size + 0.5} onError={() => setTilesFailed(true)} />)}
        <path d={path} fill="none" stroke="#fff" strokeWidth={7} strokeLinejoin="round" strokeLinecap="round" />
        <path d={path} fill="none" stroke="#1e88e5" strokeWidth={4} strokeOpacity={selectedPath ? 0.45 : 1} strokeLinejoin="round" strokeLinecap="round" />
        {selectedPath ? (
          <>
            <path d={selectedPath} fill="none" stroke="#fff" strokeWidth={9} strokeLinejoin="round" strokeLinecap="round" />
            <path d={selectedPath} fill="none" stroke="#fb8c00" strokeWidth={6} strokeLinejoin="round" strokeLinecap="round" />
          </>
        ) : null}
        {hoverPoint ? <circle cx={hoverPoint[0]} cy={hoverPoint[1]} r={6} fill="#fff" stroke="#212121" strokeWidth={3} /> : null}
        <circle cx={start[0]} cy={start[1]} r={7} fill="#43a047" stroke="#fff" strokeWidth={2}><title>Start</title></circle>
        <circle cx={end[0]} cy={end[1]} r={7} fill="#e53935" stroke="#fff" strokeWidth={2}><title>Finish</title></circle>
        {streets ? <text x={W - 6} y={H - 6} fontSize={11} textAnchor="end" fill="#333">© OpenStreetMap contributors</text> : null}
      </svg>
      <p>
        <label>
          <input type="checkbox" checked={streets} onChange={(e) => setStreets(e.target.checked)} />{" "}
          <b>Show street map</b>
        </label>{" "}
        <span className="muted">Loads tiles from openstreetmap.org, which can see roughly where this route is. Off by default.</span>
      </p>
      {streets && tilesFailed ? <p className="muted">Some map tiles didn't load. Check your connection, or that openstreetmap.org isn't blocked.</p> : null}
      <p className="muted">Drag across the chart below to highlight that part of the route; hover to see where you were.</p>
    </div>
  );
}
