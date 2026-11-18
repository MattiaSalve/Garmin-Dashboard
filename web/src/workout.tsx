import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api } from "./api";
import type { HomeData } from "./pages/Home";

export type Sport = "cycling" | "running" | "swimming" | "gym";
export const SPORTS: Sport[] = ["cycling", "running", "swimming", "gym"];
export const TIME_OPTIONS: Record<Sport, number[]> = {
  cycling: [30, 45, 60, 75, 90, 120, 150, 180],
  running: [20, 30, 40, 45, 60, 75, 90, 120],
  swimming: [20, 30, 40, 45, 60, 75, 90],
  gym: [20, 30, 45, 60, 75, 90],
};
export type Workout = HomeData["workout"];
export type HomeMode = "plan" | "other";

/** A coach workout the user accepted for one sport, with the available time it was made for. */
interface Accepted { minutes: number; workout: Workout }
type AcceptedBySport = Partial<Record<Sport, Accepted>>;
/** Today's Home choices. Kept on the server per account (the server drops it at midnight) so every device agrees. */
interface Stored { accepted?: AcceptedBySport; sport?: Sport; asPlanned?: boolean; mode?: HomeMode }

function cleanAccepted(raw: AcceptedBySport | undefined): AcceptedBySport {
  return Object.fromEntries(
    Object.entries(raw ?? {}).filter(([sport, a]) => SPORTS.includes(sport as Sport) && a?.workout),
  ) as AcceptedBySport;
}

const closest = (options: number[], m: number) => options.reduce((best, o) => (Math.abs(o - m) < Math.abs(best - m) ? o : best));

interface WorkoutState {
  sport: Sport;
  minutes: number;
  /** The coach workout accepted for the current sport. It replaces the suggested one until the time changes. */
  override: Workout | null;
  /** Gym only: the session the user asked for today ("auto" lets the app choose). */
  gymFocus: string;
  /** "Do the planned session anyway": ignore today's readiness downgrade. */
  asPlanned: boolean;
  setAsPlanned: (v: boolean) => void;
  /** Home shows today's plan, or a suggestion picked by sport and time. */
  mode: HomeMode;
  setMode: (m: HomeMode) => void;
  setGymFocus: (f: string) => void;
  setSport: (s: Sport) => void;
  setMinutes: (m: number) => void;
  /** Save a workout for a sport (the current one by default). For another sport this also switches to it. */
  applyWorkout: (w: Workout, target?: Sport) => void;
  clearOverride: () => void;
}

const Ctx = createContext<WorkoutState | null>(null);

/** The sport, available time and accepted coach workouts, shared by Home and the floating coach. */
export function WorkoutProvider({ children }: { email: string; children: ReactNode }) {
  const [loaded, setLoaded] = useState(false);
  const [accepted, setAccepted] = useState<AcceptedBySport>({});
  const [sport, setSportState] = useState<Sport>("cycling");
  const [minutes, setMinutesState] = useState(60);
  const [gymFocus, setGymFocusState] = useState("auto");
  const [asPlanned, setAsPlannedState] = useState(false);
  const [mode, setModeState] = useState<HomeMode>("plan");

  // The latest values, so a save always sends the whole state.
  const current = useRef<Stored>({});
  const save = useCallback((patch: Stored) => {
    current.current = { ...current.current, ...patch };
    api("/home-state", { method: "PUT", body: { state: current.current } }).catch(() => { /* kept for this tab only */ });
  }, []);

  useEffect(() => {
    let live = true;
    api<{ state: Stored }>("/home-state")
      .then(({ state }) => {
        if (!live) return;
        const acc = cleanAccepted(state.accepted);
        const sp = state.sport && SPORTS.includes(state.sport) ? state.sport : "cycling";
        current.current = { ...state, accepted: acc };
        setAccepted(acc);
        setSportState(sp);
        setMinutesState(acc[sp]?.minutes ?? 60);
        setAsPlannedState(!!state.asPlanned);
        setModeState(state.mode === "other" ? "other" : "plan");
      })
      .catch(() => { /* start from the defaults */ })
      .finally(() => { if (live) setLoaded(true); });
    return () => { live = false; };
  }, []);

  const without = (acc: AcceptedBySport, key: Sport) => { const { [key]: _gone, ...rest } = acc; return rest; };
  const dropAccepted = useCallback((key: Sport) => {
    const next = without(current.current.accepted ?? {}, key);
    setAccepted(next);
    save({ accepted: next });
  }, [save]);

  // Changing the time makes the accepted workout for this sport stale; other sports keep theirs.
  const setMinutes = useCallback((m: number) => {
    setMinutesState(m);
    if (current.current.accepted?.[sport]) dropAccepted(sport);
  }, [sport, dropAccepted]);
  // A different focus makes the accepted gym workout stale, like a different time does.
  const setGymFocus = useCallback((f: string) => {
    setGymFocusState(f);
    if (current.current.accepted?.gym) dropAccepted("gym");
  }, [dropAccepted]);
  const applyWorkout = useCallback((w: Workout, target: Sport = sport) => {
    // The new sport may not offer the current time: use the closest option it does.
    const m = target === sport ? minutes : closest(TIME_OPTIONS[target], minutes);
    const next = { ...(current.current.accepted ?? {}), [target]: { minutes: m, workout: w } };
    setAccepted(next);
    if (target !== sport) {
      setSportState(target);
      setMinutesState(m);
      save({ accepted: next, sport: target });
    } else save({ accepted: next });
  }, [sport, minutes, save]);
  const clearOverride = useCallback(() => dropAccepted(sport), [sport, dropAccepted]);
  const setSport = useCallback((next: Sport) => {
    setSportState(next);
    save({ sport: next });
    // Back to a sport with an accepted workout: restore the time it was made for. Otherwise keep the chosen
    // time if the new sport offers it, or the closest option.
    setMinutesState((m) => accepted[next]?.minutes ?? closest(TIME_OPTIONS[next], m));
  }, [accepted, save]);
  const setAsPlanned = useCallback((v: boolean) => { setAsPlannedState(v); save({ asPlanned: v }); }, [save]);
  const setMode = useCallback((m: HomeMode) => { setModeState(m); save({ mode: m }); }, [save]);

  const override = accepted[sport]?.workout ?? null;
  const value = useMemo(
    () => ({ sport, minutes, override, gymFocus, asPlanned, setAsPlanned, mode, setMode, setGymFocus, setSport, setMinutes, applyWorkout, clearOverride }),
    [sport, minutes, override, gymFocus, asPlanned, setAsPlanned, mode, setMode, setGymFocus, setSport, setMinutes, applyWorkout, clearOverride],
  );
  // Wait for the saved state so the first Home request already uses the right sport and time.
  return <Ctx.Provider value={value}>{loaded ? children : null}</Ctx.Provider>;
}

export function useWorkout(): WorkoutState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useWorkout must be used inside WorkoutProvider");
  return v;
}
