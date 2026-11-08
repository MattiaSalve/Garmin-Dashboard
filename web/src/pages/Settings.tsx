import { useEffect, useState } from "react";
import { api, useFetch } from "../api";
import { Banner, Loading } from "../components";

const FIELDS: { key: string; label: string; help?: string; step: number }[] = [
  { key: "ftp_w", label: "FTP (W)", help: "Used to turn workout targets into watts.", step: 1 },
  { key: "hrv_low", label: "HRV range low (ms)", help: "Leave empty to use Garmin's baseline.", step: 1 },
  { key: "hrv_high", label: "HRV range high (ms)", help: "Leave empty to use Garmin's baseline.", step: 1 },
  { key: "weekly_hours_target", label: "Weekly hours target", step: 0.5 },
  { key: "yearly_km_goal", label: "Yearly cycling goal (km)", step: 100 },
];

export function SettingsCard() {
  const { data, error, loading } = useFetch<Record<string, number | null>>("/settings");
  const [values, setValues] = useState<Record<string, string>>({});
  const [status, setStatus] = useState<{ kind: "info" | "error"; text: string } | null>(null);

  useEffect(() => {
    if (data) setValues(Object.fromEntries(FIELDS.map((f) => [f.key, data[f.key] === null || data[f.key] === undefined ? "" : String(data[f.key])])));
  }, [data]);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    const body = Object.fromEntries(FIELDS.map((f) => [f.key, values[f.key] === "" ? null : Number(values[f.key])]));
    try {
      await api("/settings", { method: "PUT", body });
      setStatus({ kind: "info", text: "Saved." });
    } catch (err) {
      setStatus({ kind: "error", text: (err as Error).message });
    }
  }

  return (
    <div className="card">
      <h2>Settings</h2>
      <Loading error={error} loading={loading} />
      {data ? (
        <form onSubmit={save}>
          <div className="grid">
            {FIELDS.map((f) => (
              <label className="field" key={f.key} title={f.help}>
                {f.label}
                <input type="number" min={0} step={f.step} placeholder="Not set" value={values[f.key] ?? ""}
                  onChange={(e) => setValues({ ...values, [f.key]: e.target.value })} />
                {f.help ? <span className="muted">{f.help}</span> : null}
              </label>
            ))}
          </div>
          <p><button>Save</button></p>
          {status ? <Banner kind={status.kind}>{status.text}</Banner> : null}
        </form>
      ) : null}
    </div>
  );
}
