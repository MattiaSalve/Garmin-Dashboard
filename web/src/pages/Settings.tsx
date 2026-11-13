import { useEffect, useState } from "react";
import { api, useFetch } from "../api";
import { Banner, Loading } from "../components";

const LEVEL_OPTIONS = [["", "Estimate from my training and fitness"], ["1", "Beginner"], ["2", "Intermediate"], ["3", "Advanced"]];

const GYM_GOALS = [["", "General strength (default)"], ["2", "Support my endurance training"], ["3", "Build muscle"]];

const FIELDS: { key: string; label: string; help?: string; step: number; levels?: boolean; options?: string[][] }[] = [
  { key: "ftp_w", label: "FTP (W)", help: "Used to turn workout targets into watts.", step: 1 },
  { key: "hrv_low", label: "HRV range low (ms)", help: "Leave empty to use Garmin's baseline.", step: 1 },
  { key: "hrv_high", label: "HRV range high (ms)", help: "Leave empty to use Garmin's baseline.", step: 1 },
  { key: "weekly_hours_target", label: "Weekly hours target", step: 0.5 },
  { key: "yearly_km_goal", label: "Yearly cycling goal (km)", step: 100 },
  { key: "level_cycling", label: "Cycling level", help: "Sets how hard and long suggested rides are.", step: 1, levels: true },
  { key: "level_running", label: "Running level", help: "Beginners get run/walk and fewer intervals.", step: 1, levels: true },
  { key: "level_swimming", label: "Swimming level", help: "Beginners get technique-focused sessions.", step: 1, levels: true },
  { key: "level_gym", label: "Gym level", help: "Beginners get fewer sets and shorter sessions.", step: 1, levels: true },
  { key: "gym_goal", label: "Gym focus", help: "Picks the rep ranges and favours legs and core if the gym supports your cycling, running or swimming.", step: 1, options: GYM_GOALS },
  { key: "run_threshold_pace_s", label: "Running threshold pace (sec/km)", help: "Pace you could hold for about an hour, e.g. 300 = 5:00/km. Turns running steps into target paces.", step: 5 },
  { key: "swim_threshold_pace_s", label: "Swimming threshold pace (sec/100 m)", help: "Pace you could hold for about 20 minutes of continuous swimming, e.g. 110 = 1:50/100 m.", step: 1 },
  { key: "pool_length_m", label: "Pool length (m)", help: "Swim workouts sent to the watch count lengths of this pool.", step: 1 },
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
                {f.levels || f.options ? (
                  <select value={values[f.key] ?? ""} onChange={(e) => setValues({ ...values, [f.key]: e.target.value })}>
                    {(f.options ?? LEVEL_OPTIONS).map(([v, text]) => <option key={v} value={v}>{text}</option>)}
                  </select>
                ) : (
                  <input type="number" min={0} step={f.step} placeholder="Not set" value={values[f.key] ?? ""}
                    onChange={(e) => setValues({ ...values, [f.key]: e.target.value })} />
                )}
                {f.help ? <span className="muted">{f.help}</span> : null}
              </label>
            ))}
          </div>
          <p><button>Save</button></p>
          {status ? <Banner kind={status.kind}>{status.text}</Banner> : null}
        </form>
      ) : null}
      <DeleteAccount />
    </div>
  );
}

/** Removes this app's copy of the user's data (not Garmin's) and signs out everywhere. */
function DeleteAccount() {
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function remove() {
    setBusy(true);
    setError(null);
    try {
      await api("/account", { method: "DELETE" });
      localStorage.clear(); // includes the OpenRouter key and chat state kept in this browser
      sessionStorage.clear();
      window.dispatchEvent(new Event("signed-out"));
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  }

  return (
    <details style={{ marginTop: "1.5rem" }}>
      <summary>Delete my data</summary>
      <p className="muted">
        Deletes everything this dashboard stores about you: synced activities and recovery data, settings, coach goals and
        remembered sign-ins on every browser. Your Garmin Connect account and data are not touched; signing in again
        downloads your history afresh.
      </p>
      <label className="field" style={{ maxWidth: 280 }}>Type <b>delete</b> to confirm
        <input value={confirm} onChange={(e) => setConfirm(e.target.value)} autoComplete="off" />
      </label>
      <p><button className="danger" disabled={busy || confirm.trim().toLowerCase() !== "delete"} onClick={remove}>
        {busy ? "Deleting…" : "Delete my data"}
      </button></p>
      {error ? <Banner kind="error">{error}</Banner> : null}
    </details>
  );
}
