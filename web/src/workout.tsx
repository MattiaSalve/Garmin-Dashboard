import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { daysAgo } from "./lib";
import type { HomeData } from "./pages/Home";

export type Sport = "cycling" | "running" | "swimming";
export const SPORTS: Sport[] = ["cycling", "running", "swimming"];
export const TIME_OPTIONS: Record<Sport, number[]> = {
  cycling: [30, 45, 60, 75, 90, 120, 150, 180],
  running: [20, 30, 40, 45, 60, 75, 90, 120],
  swimming: [20, 30, 40, 45, 60, 75, 90],
};
const SPORT_KEY = "workout_sport";

export type Workout = HomeData["workout"];

export function savedSport(): Sport {
  const saved = localStorage.getItem(SPORT_KEY) as Sport | null;
  return saved && SPORTS.includes(saved) ? saved : "cycling";
}

interface Accepted { day: string; sport: Sport; minutes: number; workout: Workout }

// An accepted coach workout is remembered in this browser until the end of the day, per Garmin account.
const acceptedKey = (email: string) => `accepted_workout:${email}`;

function loadAccepted(email: string): Accepted | null {
  try {
    const raw = localStorage.getItem(acceptedKey(email));
    if (!raw) return null;
    const a = JSON.parse(raw) as Accepted;
    if (a.day === daysAgo(0) && SPORTS.includes(a.sport) && a.workout?.steps) return a;
    localStorage.removeItem(acceptedKey(email)); // from an earlier day or unreadable
  } catch { /* storage blocked or corrupt: start without it */ }
  return null;
}

function storeAccepted(email: string, a: Accepted | null) {
  try {
    if (a) localStorage.setItem(acceptedKey(email), JSON.stringify(a)); else localStorage.removeItem(acceptedKey(email));
  } catch { /* the workout just won't survive a reload */ }
}

interface WorkoutState {
  sport: Sport;
  minutes: number;
  /** A workout the user accepted from the coach. It replaces the suggested one until sport or time changes. */
  override: Workout | null;
  setSport: (s: Sport) => void;
  setMinutes: (m: number) => void;
  applyWorkout: (w: Workout) => void;
  clearOverride: () => void;
}

const Ctx = createContext<WorkoutState | null>(null);

/** The sport, available time and accepted coach workout, shared by Home and the floating coach. */
export function WorkoutProvider({ email, children }: { email: string; children: ReactNode }) {
  const [restored] = useState(() => loadAccepted(email));
  const [sport, setSportState] = useState<Sport>(restored?.sport ?? savedSport());
  const [minutes, setMinutesState] = useState(restored?.minutes ?? 60);
  const [override, setOverride] = useState<Workout | null>(restored?.workout ?? null);

  const setMinutes = useCallback((m: number) => { setMinutesState(m); setOverride(null); storeAccepted(email, null); }, [email]);
  const applyWorkout = useCallback((w: Workout) => {
    setOverride(w);
    storeAccepted(email, { day: daysAgo(0), sport, minutes, workout: w });
  }, [email, sport, minutes]);
  const setSport = useCallback((next: Sport) => {
    try { localStorage.setItem(SPORT_KEY, next); } catch { /* private mode */ }
    setSportState(next);
    setOverride(null);
    storeAccepted(email, null);
    // Keep the chosen time if the new sport offers it, otherwise the closest option.
    setMinutesState((m) => TIME_OPTIONS[next].reduce((best, o) => (Math.abs(o - m) < Math.abs(best - m) ? o : best)));
  }, [email]);
  const clearOverride = useCallback(() => { setOverride(null); storeAccepted(email, null); }, [email]);

  const value = useMemo(
    () => ({ sport, minutes, override, setSport, setMinutes, applyWorkout, clearOverride }),
    [sport, minutes, override, setSport, setMinutes, applyWorkout, clearOverride],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useWorkout(): WorkoutState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useWorkout must be used inside WorkoutProvider");
  return v;
}
