import { useCallback, useEffect, useState } from "react";
import { SyncProvider, useSync } from "./sync";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { api, ApiError } from "./api";
import { Banner } from "./components";
import { Coach } from "./pages/Coach";
import { Activities, Find } from "./pages/Activities";
import { Home, useHomeData } from "./pages/Home";
import { Plan } from "./pages/Plan";
import { Progress } from "./pages/Progress";
import { Ride } from "./pages/Ride";
import { SettingsCard } from "./pages/Settings";
import { Trends } from "./pages/Trends";
import { WorkoutProvider, useWorkout } from "./workout";

interface Me {
  email: string;
}

function SignIn({ onSignedIn }: { onSignedIn: (me: Me) => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(true);
  const [pending, setPending] = useState<string | null>(null); // set while Garmin waits for the emailed code
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (pending) {
        onSignedIn(await api<Me>("/login/mfa", { body: { pending, code } }));
        return;
      }
      const r = await api<Me | { mfa_required: true; pending: string }>("/login", { body: { email, password, remember } });
      if ("mfa_required" in r) {
        setPending(r.pending);
        setPassword("");
      } else onSignedIn(r);
    } catch (err) {
      setError((err as Error).message);
      if (err instanceof ApiError && pending && err.message.includes("again")) restart(); // expired or out of tries
    } finally {
      setBusy(false);
    }
  }

  function restart() {
    setPending(null);
    setCode("");
  }

  return (
    <div className="signin card">
      <h2>Garmin dashboard</h2>
      {pending ? (
        <form onSubmit={submit}>
          <p className="muted">Garmin sent a security code to your email (or authenticator app). Enter it within 5 minutes.</p>
          <label className="field">Security code
            <input inputMode="numeric" autoComplete="one-time-code" value={code} onChange={(e) => setCode(e.target.value)}
              pattern="[0-9 ]{4,12}" autoFocus required />
          </label>
          {error ? <Banner kind="error">{error}</Banner> : null}
          <div className="row">
            <button disabled={busy}>{busy ? "Checking…" : "Verify"}</button>
            <button type="button" className="secondary" onClick={restart}>Back</button>
          </div>
        </form>
      ) : (
        <>
          <p className="muted">Sign in with your Garmin Connect account. Your password is sent to Garmin only and is never stored.</p>
          <form onSubmit={submit}>
            <label className="field">Garmin email
              <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="username" required />
            </label>
            <label className="field">Garmin password
              <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required />
            </label>
            <label><input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} /> Keep me signed in on this browser (30 days)</label>
            {error ? <Banner kind="error">{error}</Banner> : null}
            <button disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
          </form>
        </>
      )}
    </div>
  );
}

function SyncButton() {
  const { busy, message, run } = useSync();
  return (
    <>
      <button className="secondary" disabled={busy} onClick={() => run(false)}>{busy ? "Syncing…" : "Sync from Garmin"}</button>
      <button className="secondary" disabled={busy} title="Re-download the entire history" onClick={() => run(true)}>Full re-sync</button>
      {message ? <span className="muted" style={message.kind === "error" ? { color: "var(--bad)" } : undefined}>{message.text}</span> : null}
    </>
  );
}

/** Chat with the coach from any page. The panel stays mounted so the conversation survives closing it. */
function CoachWidget() {
  const [open, setOpen] = useState(false);
  const [opened, setOpened] = useState(false);
  const { sport, minutes } = useWorkout();
  const home = useHomeData(minutes, sport);
  // While the chat is open: stop the page behind from scrolling, and keep the chat inside the visible area
  // (the on-screen keyboard shrinks the visual viewport but not the layout viewport on most phones).
  useEffect(() => {
    if (!open) return;
    document.body.classList.add("coach-open");
    const vv = window.visualViewport;
    const root = document.documentElement;
    const fit = () => {
      if (!vv) return;
      root.style.setProperty("--coach-vh", `${vv.height}px`);
      root.style.setProperty("--coach-top", `${vv.offsetTop}px`);
    };
    fit();
    vv?.addEventListener("resize", fit);
    vv?.addEventListener("scroll", fit);
    return () => {
      document.body.classList.remove("coach-open");
      vv?.removeEventListener("resize", fit);
      vv?.removeEventListener("scroll", fit);
      root.style.removeProperty("--coach-vh");
      root.style.removeProperty("--coach-top");
    };
  }, [open]);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);
  return (
    <>
      <aside className={`card coach-panel${open ? " open" : ""}`} aria-label="AI coach" aria-hidden={!open}>
        <div className="coach-head">
          <h2>AI coach</h2>
          <button className="coach-close" onClick={() => setOpen(false)} aria-label="Close coach" tabIndex={open ? 0 : -1}>×</button>
        </div>
        {opened ? (home.data
          ? <Coach ftp={home.data.ftp} settings={home.data.settings} />
          : <p className="muted coach-body">Loading…</p>) : null}
      </aside>
      <button className={`coach-fab${open ? " hidden" : ""}`} onClick={() => { setOpen(true); setOpened(true); }}
        aria-expanded={open} tabIndex={open ? -1 : 0}>
        Ask coach
      </button>
    </>
  );
}

export default function App() {
  const [me, setMe] = useState<Me | null | undefined>(undefined); // undefined = still checking
  const [refresh, setRefresh] = useState(0);
  const bump = useCallback(() => setRefresh((n) => n + 1), []);

  useEffect(() => {
    api<Me>("/me").then(setMe).catch((e: ApiError) => setMe(e.status === 401 ? null : null));
    const out = () => setMe(null);
    window.addEventListener("signed-out", out);
    return () => window.removeEventListener("signed-out", out);
  }, []);

  if (me === undefined) return <div className="app"><p className="muted">Loading…</p></div>;
  if (me === null) return <div className="app"><SignIn onSignedIn={setMe} /></div>;

  const signOut = async () => {
    await api("/logout", { method: "POST" }).catch(() => undefined);
    setMe(null);
  };

  return (
    <WorkoutProvider email={me.email}>
    <SyncProvider email={me.email} onDone={bump}>
    <div className="app">
      <div className="topbar">
        <nav className="nav">
          <NavLink to="/" end>Home</NavLink>
          <NavLink to="/plan">Plan</NavLink>
          <NavLink to="/week">Week overview</NavLink>
          <NavLink to="/find">Find</NavLink>
          <NavLink to="/trends">Trends</NavLink>
          <NavLink to="/progress">Progress</NavLink>
          <NavLink to="/settings">Settings</NavLink>
        </nav>
        <div className="who">
          <SyncButton />
          <span>{me.email}</span>
          <button className="secondary" onClick={signOut}>Sign out</button>
        </div>
      </div>
      <Routes key={refresh}>
        <Route path="/" element={<Home />} />
        <Route path="/plan" element={<Plan />} />
        <Route path="/week" element={<Activities />} />
        <Route path="/activities" element={<Navigate to="/week" replace />} />
        <Route path="/find" element={<Find />} />
        <Route path="/trends" element={<Trends />} />
        <Route path="/progress" element={<Progress />} />
        <Route path="/ride/:id" element={<Ride />} />
        <Route path="/settings" element={<SettingsCard />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
      <CoachWidget />
    </div>
    </SyncProvider>
    </WorkoutProvider>
  );
}
