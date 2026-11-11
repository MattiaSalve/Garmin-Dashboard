export interface GymExercise {
  name: string;
  group: string;
  sets: number;
  reps: string;
  rpe: number;
  rest_s: number;
  note?: string;
}

export interface GymInfo {
  goal: string;
  goal_label: string;
  days_since: Record<string, number | null>;
  unlabelled: boolean;
  labels: Record<string, string>;
  focus_options: Record<string, string>;
}

const GROUP_LABEL: Record<string, string> = { legs: "Legs", push: "Push", pull: "Pull", core: "Core", mobility: "Mobility" };

const rest = (s: number) => (s >= 60 ? `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}` : `${s} s`);
const ago = (d: number | null) => (d === null ? "not in your data" : d === 0 ? "today" : d === 1 ? "yesterday" : `${d} days ago`);

/** Rough session length: a set takes about 45 s plus its rest, a minute to set up each exercise, a 6-minute warm-up. */
export function gymMinutes(exercises: GymExercise[]): number {
  return Math.round(6 + exercises.reduce((a, e) => a + (e.sets * (45 + e.rest_s)) / 60 + 1, 0));
}

/** The exercises of a gym session: sets, reps, effort (RPE) and rest. */
export function GymSession({ exercises }: { exercises: GymExercise[] }) {
  return (
    <div className="scroll">
      <table>
        <thead>
          <tr><th>Exercise</th><th>Muscles</th><th className="num">Sets</th><th className="num">Reps</th><th className="num" title="Rating of perceived exertion, 1–10. 10 = nothing left in the tank.">RPE</th><th className="num">Rest</th></tr>
        </thead>
        <tbody>
          {exercises.map((e, i) => (
            <tr key={i} title={e.note || undefined}>
              <td style={{ whiteSpace: "normal" }}>{e.name}{e.note ? <div className="muted" style={{ fontSize: "0.8rem" }}>{e.note}</div> : null}</td>
              <td>{GROUP_LABEL[e.group] ?? e.group}</td>
              <td className="num">{e.sets}</td>
              <td className="num">{e.reps}</td>
              <td className="num">{e.rpe}</td>
              <td className="num">{rest(e.rest_s)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** How long ago each muscle group was last trained, from the exercise sets Garmin recorded. */
export function MuscleStrip({ gym }: { gym: GymInfo }) {
  return (
    <div>
      <div className="legend" style={{ marginTop: 0 }}>
        {Object.entries(gym.days_since).map(([g, d]) => (
          <span key={g}><i style={{ background: d === null ? "var(--line)" : d < 2 ? "#e53935" : d < 4 ? "#fdd835" : "#66bb6a" }} />
            <b>{gym.labels[g] ?? g}</b> {ago(d)}</span>
        ))}
      </div>
      <p className="muted">
        Goal: {gym.goal_label} (change in Settings). Last trained comes from the exercises your watch recorded in strength activities
        {gym.unlabelled ? "; some sessions had no labelled exercises, so they count as full body" : ""}.
      </p>
    </div>
  );
}
