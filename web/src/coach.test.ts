import { describe, expect, it } from "vitest";
import { toWorkout } from "./pages/Coach";

const advice = {
  summary: "Keep it easy.", agree_with_suggestion: false, cautions: ["HRV is low"],
  adjusted_steps: [
    { minutes: 10, pct_ftp_low: 50, pct_ftp_high: 60, label: "Warm-up" },
    { minutes: 40, pct_ftp_low: 60, pct_ftp_high: 72, label: "Zone 2" },
  ],
};

describe("toWorkout", () => {
  it("turns cycling steps into watts from FTP", () => {
    const w = toWorkout(advice, 250, "cycling", null, "");
    expect(w.minutes).toBe(50);
    expect(w.steps[1]).toMatchObject({ watts_low: 150, watts_high: 180, pct_ftp: "60–72%" });
    expect(w.purpose).toBe("Keep it easy.");
  });
  it("uses effort when no threshold pace is set for running", () => {
    const w = toWorkout(advice, 250, "running", null, "min/km");
    expect(w.steps[1].pace_fast).toBeUndefined();
    expect(w.steps[1].rpe).toBe("2–3"); // same scale as the suggested workouts (workouts._rpe)
  });
  it("turns running steps into paces from threshold pace", () => {
    const w = toWorkout(advice, 250, "running", 300, "min/km");
    expect(w.steps[1].pace_fast).toBe("6:57");
  });
  it("keeps a gym proposal as exercises with an estimated length", () => {
    const gym = {
      ...advice, adjusted_steps: null,
      adjusted_exercises: [{ name: "Split squat", group: "legs", sets: 3, reps: "6–8", rpe: 7, rest_s: 90 }],
    };
    const w = toWorkout(gym, 250, "gym", null, "");
    expect(w.steps).toEqual([]);
    expect(w.exercises?.[0].name).toBe("Split squat");
    expect(w.minutes).toBe(Math.round(6 + (3 * 135) / 60 + 1));
  });
});
