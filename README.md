# garmin-dashboards

Dashboard for Garmin Connect data: activities, recovery (HRV, resting HR, sleep), training load, and a daily workout suggestion with an optional AI coach. FastAPI backend, React frontend.

## Run (development)
```
uv sync
cd web && npm install && npm run build && cd ..
GARMIN_DASHBOARDS_LOCAL=1 uv run garmin-dashboards     # http://localhost:8000
```
For frontend work run `npm run dev` in `web/` (Vite proxies `/api` to port 8000) alongside `uv run garmin-dashboards`.

Tests: `uv run pytest` and `cd web && npx vitest run`.

## Hosting
Serve over https (session cookies are `Secure` on https). `data/` holds one SQLite file per user plus saved sign-in sessions; put it on a persistent disk and back it up. Garmin may block datacenter IPs, so test a real sign-in from the server first.

## Environment variables
- `GARMIN_DASHBOARDS_LOCAL=1`: single-user mode; also lets `OPENROUTER_API_KEY` act as the coach key.
- `OPENROUTER_MODEL`: initial coach model.

In hosted mode each user enters their own OpenRouter key in the app; it is never stored.

See `docs/PRELAUNCH.md` for the launch checklist.
