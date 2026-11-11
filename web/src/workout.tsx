import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { daysAgo } from "./lib";
import type { HomeData } from "./pages/Home";

export type Sport = "cycling" | "running" | "swimming" | "gym";
export const SPORTS: Sport[] = ["cycling", "running", "swimming", "gym"];
export const TIME_OPTIONS: Record<Sport, number[]> = {
  cycling: [30, 45, 60, 75, 90, 120, 150, 180],
  running: [20, 30, 40, 45, 60, 75, 90, 120],
  swimming: [20, 30, 40, 45, 60, 75, 90],
  gym: [20, 30, 45, 60, 75, 90],
};
const SPORT_KEY = "workout_sport";

export type Workout = HomeData["workout"];

export function savedSport(): Sport {
  const saved = localStorage.getItem(SPORT_KEY) as Sport | null;
  return saved && SPORTS.includes(saved) ? saved : "cycling";
}

/** A coach workout the user accepted for one sport, with the available time it was made for. */
interface Accepted { minutes: number; workout: Workout }
type AcceptedBySport = Partial<Record<Sport, Accepted>>;
interface Stored { day: string; bySport: AcceptedBySport }

// Accepted coach workouts are remembered in this browser until the end of the day, per Garmin account and per sport.
const acceptedKey = (email: string) => `accepted_workout:${email}`;

function loadAccepted(email: string): AcceptedBySport {
  try {
    const raw = localStorage.getItem(acceptedKey(email));
    if (!raw) return {};
    const stored = JSON.parse(raw) as Stored;
    if (stored.day === daysAgo(0) && stored.bySport) {
      return Object.fromEntries(
        Object.entries(stored.bySport).filter(([sport, a]) => SPORTS.includes(sport as Sport) && a?.workout),
      ) as AcceptedBySport;
    }
    localStorage.removeItem(acceptedKey(email)); // from an earlier day or in an older format
  } catch { /* storage blocked or corrupt: start without it */ }
  return {};
}

function storeAccepted(email: string, bySport: AcceptedBySport) {
  try {
    if (Object.keys(bySport).length) localStorage.setItem(acceptedKey(email), JSON.stringify({ day: daysAgo(0), bySport }));
    else localStorage.removeItem(acceptedKey(email));
  } catch { /* the workout just won't survive a reload */ }
}

interface WorkoutState {
  sport: Sport;
  minutes: number;
  /** The coach workout accepted for the current sport. It replaces the suggested one until the time changes. */
  override: Workout | null;
  /** Gym only: the session the user asked for today ("auto" lets the app choose). */
  gymFocus: string;
  setGymFocus: (f: string) => void;
  setSport: (s: Sport) => void;
  setMinutes: (m: number) => void;
  applyWorkout: (w: Workout) => void;
  clearOverride: () => void;
}

const Ctx = createContext<WorkoutState | null>(null);

/** The sport, available time and accepted coach workouts, shared by Home and the floating coach. */
export function WorkoutProvider({ email, children }: { email: string; children: ReactNode }) {
  const [accepted, setAccepted] = useState<AcceptedBySport>(() => loadAccepted(email));
  const [sport, setSportState] = useState<Sport>(savedSport);
  const [minutes, setMinutesState] = useState(() => accepted[sport]?.minutes ?? 60);
  const [gymFocus, setGymFocusState] = useState("auto");

  // Change the accepted workouts and keep the stored copy in step.
  const update = useCallback((change: (current: AcceptedBySport) => AcceptedBySport) => {
    setAccepted((current) => {
      const next = change(current);
      storeAccepted(email, next);
      return next;
    });
  }, [email]);
  const without = (current: AcceptedBySport, key: Sport) => { const { [key]: _gone, ...rest } = current; return rest; };

  // Changing the time makes the accepted workout for this sport stale; other sports keep theirs.
  const setMinutes = useCallback((m: number) => {
    setMinutesState(m);
    update((current) => without(current, sport));
  }, [sport, update]);
  // A different focus makes the accepted gym workout stale, like a different time does.
  const setGymFocus = useCallback((f: string) => {
    setGymFocusState(f);
    update((current) => without(current, "gym"));
  }, [update]);
  const applyWorkout = useCallback((w: Workout) => update((current) => ({ ...current, [sport]: { minutes, workout: w } })), [sport, minutes, update]);
  const clearOverride = useCallback(() => update((current) => without(current, sport)), [sport, update]);
  const setSport = useCallback((next: Sport) => {
    try { localStorage.setItem(SPORT_KEY, next); } catch { /* private mode */ }
    setSportState(next);
    // Back to a sport with an accepted workout: restore the time it was made for. Otherwise keep the chosen
    // time if the new sport offers it, or the closest option.
    setMinutesState((m) => accepted[next]?.minutes ?? TIME_OPTIONS[next].reduce((best, o) => (Math.abs(o - m) < Math.abs(best - m) ? o : best)));
  }, [accepted]);

  const override = accepted[sport]?.workout ?? null;
  const value = useMemo(
    () => ({ sport, minutes, override, gymFocus, setGymFocus, setSport, setMinutes, applyWorkout, clearOverride }),
    [sport, minutes, override, gymFocus, setGymFocus, setSport, setMinutes, applyWorkout, clearOverride],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useWorkout(): WorkoutState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useWorkout must be used inside WorkoutProvider");
  return v;
}
