import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useFetch } from "../api";
import { Banner, Loading, Metric } from "../components";
import { displaySport, fmt, fmtHM } from "../lib";
import { Highlight, RideChart, Samples } from "./RideChart";
import { RouteMap } from "./RouteMap";

type Row = Record<string, number | string | null>;

export function Ride() {
  const { id } = useParams();
  const details = useFetch<Row>(`/activities/${id}`);
  const samples = useFetch<Samples>(`/activities/${id}/samples`);
  const [highlight, setHighlight] = useState<Highlight>({ range: null, hover: null });
  const r = details.data;
  if (!r) return <Loading error={details.error} loading={details.loading} />;

  const n = (k: string) => r[k] as number | null;
  const moving = n("moving_duration_s") ?? 0;
  const speed = moving && n("distance_m") ? n("distance_m")! / 1000 / (moving / 3600) : null;
  const more: [string, string][] = [
    ["Max speed", fmt(n("max_speed_mps") === null ? null : n("max_speed_mps")! * 3.6, 1, " km/h")],
    ["Elevation loss", fmt(n("elevation_loss_m"), 0, " m")],
    ["Avg / max heart rate", `${fmt(n("avg_hr"))} / ${fmt(n("max_hr"))} bpm`],
    ["Avg / norm / max power", `${fmt(n("avg_power_w"))} / ${fmt(n("norm_power_w"))} / ${fmt(n("max_power_w"))} W`],
    ["Calories", fmt(n("calories"), 0, " kcal")],
    ["Training load", fmt(n("training_load"))],
    ["Training stress", fmt(n("training_stress_score"))],
    ["Aerobic / anaerobic effect", `${fmt(n("aerobic_te"), 1)} / ${fmt(n("anaerobic_te"), 1)}`],
    ["VO2max", fmt(n("vo2max"))],
  ];
  return (
    <section className="card">
      <p><Link to="/activities">← Back</Link></p>
      <h2>{String(r.activity_name || "Untitled")} · {displaySport(String(r.sport_type ?? ""))}</h2>
      <p className="muted">Started {String(r.start_time_local)}{r.location_name ? ` · ${r.location_name}` : ""}</p>
      <div className="grid">
        <Metric label="Distance" value={fmt(n("distance_m") === null ? null : n("distance_m")! / 1000, 1, " km")} />
        <Metric label="Moving time" value={fmtHM(n("moving_duration_s"))} />
        <Metric label="Avg speed" value={fmt(speed, 1, " km/h")} />
        <Metric label="Elevation gain" value={fmt(n("elevation_gain_m"), 0, " m")} />
      </div>
      <details>
        <summary>More stats</summary>
        <table><tbody>{more.map(([k, v]) => <tr key={k}><td>{k}</td><td className="num">{v}</td></tr>)}</tbody></table>
      </details>
      {samples.data?.route ? (
        <>
          <h3>Route</h3>
          <RouteMap route={samples.data.route} highlight={highlight} />
        </>
      ) : null}
      <h3>Ride charts</h3>
      {samples.loading ? <p className="muted">Loading second-by-second data…</p> : null}
      {samples.error ? <Banner kind="info">{samples.error}</Banner> : null}
      {samples.data ? <RideChart key={String(id)} samples={samples.data} onHighlight={setHighlight} /> : null}
    </section>
  );
}
