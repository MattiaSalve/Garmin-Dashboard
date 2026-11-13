import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { api, ApiError } from "./api";
import { daysAgo } from "./lib";

interface SyncState {
  busy: boolean;
  message: { kind: "info" | "error"; text: string } | null;
  run: (full?: boolean) => Promise<void>;
}

const SyncContext = createContext<SyncState | null>(null);

/** One sync state for the whole app, so the top-bar button and the Home banner agree. Syncs once a day per
 *  browser tab on open (Garmin revises last night's data, so this runs even if the server synced earlier). */
export function SyncProvider({ email, onDone, children }: { email: string; onDone: () => void; children: ReactNode }) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<SyncState["message"]>(null);
  const running = useRef(false);

  const run = useCallback(async (full = false, quiet = false) => {
    if (running.current) return;
    running.current = true;
    setBusy(true);
    if (!quiet) setMessage(null);
    try {
      const r = await api<{ new_activities: number; wellness_days: number; failed: string[] }>("/sync", { body: { full } });
      const failed = r.failed.length ? ` Couldn't download: ${r.failed.join(", ")}.` : "";
      setMessage({ kind: r.failed.length ? "error" : "info", text: `${r.new_activities} new activities, ${r.wellness_days} recovery days updated.${failed}` });
      sessionStorage.setItem(`synced:${email}`, daysAgo(0));
      onDone();
    } catch (err) {
      // 409: a sync is already running (another tab, or the server). Not worth an error message.
      if (!(err instanceof ApiError && err.status === 409)) setMessage({ kind: "error", text: (err as Error).message });
    } finally {
      running.current = false;
      setBusy(false);
    }
  }, [email, onDone]);

  useEffect(() => {
    if (sessionStorage.getItem(`synced:${email}`) !== daysAgo(0)) void run(false, true);
  }, [email, run]);

  return <SyncContext.Provider value={{ busy, message, run }}>{children}</SyncContext.Provider>;
}

export function useSync(): SyncState {
  const s = useContext(SyncContext);
  if (!s) throw new Error("useSync needs a SyncProvider");
  return s;
}
