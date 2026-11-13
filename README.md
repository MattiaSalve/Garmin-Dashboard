# garmin-dashboards

Dashboard for Garmin Connect data: activities, recovery (HRV, resting HR, sleep), training load, a multi-week training plan that adapts to what you actually do, and a daily workout suggestion with an optional AI coach. FastAPI backend, React frontend.

## Run (development)
```
uv sync
cd web && npm install && npm run build && cd ..
GARMIN_DASHBOARDS_LOCAL=1 uv run garmin-dashboards     # http://localhost:8000
```
For frontend work run `npm run dev` in `web/` (Vite proxies `/api` to port 8000) alongside `uv run garmin-dashboards`.

Tests: `uv run pytest` and `cd web && npx vitest run`.

## Using it
- **Sign in** with your Garmin Connect account. If two-step verification is on, Garmin emails a code; enter it on the second screen within 5 minutes. Tick "Keep me signed in" to stay signed in for 30 days on that browser.
- **Sync**: the app syncs once a day when you open it, and from the top-bar button. The first sync downloads 120 days of recovery data and your full activity history.
- **Home** shows today's readiness (HRV, resting HR, sleep, form, recent hard days) and a workout that fits your time and sport. Until last night's data has synced, the verdict is capped at amber.
- **Plan**: set your sports (e.g. cycling + gym, with a rough share each), hours and sessions a week, your long-session day and up to three goals: an event on a date, a weekly volume, a performance target (FTP or threshold pace) or general fitness. The app builds a plan four weeks ahead in detail, with phases up to an event: build weeks, an easier week every fourth, a two-week taper. It places long, hard and gym sessions so they don't clash. Edit, move, lock or delete any session, add your own, or set fewer hours for a busy week. "Regenerate" keeps everything you edited or locked.
- **Plan adjustments**: when you do much more than planned (a 4-hour ride instead of 2), miss a key session or have several poor recovery days, the Plan page suggests changes. Examples: the next hard day becomes easy, a missed session moves to a free day, the coming sessions are trimmed. Nothing changes until you press Apply. Home still downgrades *today's* session on an amber or red day.
- **Send to watch**: uploads the workout to Garmin Connect and schedules it for today; sync the watch to get it. Rides get watt targets, runs pace targets (with a threshold pace set), swims become pool-length distances (set your pool length and swim threshold pace in Settings), and gym sessions become Garmin strength workouts with named exercises, sets, reps and rests. Sending again the same day replaces the earlier copy.
- **FTP**: set it in Settings; Home offers to raise it when your best 20-minute power in the last 90 days is at least 3% higher.
- **Ask coach**: paste an [OpenRouter](https://openrouter.ai) API key in the coach panel (it stays in your browser's localStorage, never on the server) and pick a model. The coach receives only numbers: no names, places, GPS or account details. On the Plan page, "Review my plan" asks the coach to look over this week and next. Tell it things like "I can't train Thursday", in the Plan page or in the chat. Its edits arrive as a suggested change you apply or dismiss. Goal names are never sent.
- **Delete my data** (Settings): removes your database and remembered sign-ins from this server. Your Garmin account is untouched.

## Hosting
Serve over https (session cookies are `Secure` on https). `data/` holds one SQLite file per user plus saved sign-in sessions; put it on a persistent disk and back it up. Garmin may block datacenter IPs, so test a real sign-in from the server first.

## Environment variables
- `GARMIN_DASHBOARDS_LOCAL=1`: single-user mode; also lets `OPENROUTER_API_KEY` act as the coach key.
- `OPENROUTER_API_KEY`: coach key, used only with `GARMIN_DASHBOARDS_LOCAL=1`.
- `OPENROUTER_MODEL`: initial coach model.
- `GARMIN_DASHBOARDS_DATA`: where per-user databases and sessions live (default `data/`).

In hosted mode each user enters their own OpenRouter key in the app; it is never stored on the server.

See `docs/PRELAUNCH.md` for the launch checklist.
