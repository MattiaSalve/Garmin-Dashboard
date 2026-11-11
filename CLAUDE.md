# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

A Garmin Connect dashboard (FastAPI backend, React frontend in `web/`) for one cyclist's Garmin Connect data. It does three things:

- Syncs activities and daily recovery data (HRV, resting HR, sleep, Body Battery) into a local SQLite file.
- Charts training and recovery.
- Recommends a workout for today from recovery signals. An optional "Ask coach" button sends a summary to an LLM via OpenRouter.

The athlete uses a Forerunner 255 (it has no Garmin Training Readiness), with FTP 252 W and a usual HRV of 100–120 ms. Garmin's own baseline for them is 96–124 ms.

## Commands

```bash
uv sync                                   # install (Python >= 3.15, managed by uv)
uv run garmin-dashboards                  # FastAPI on :8000, serves web/dist
(cd web && npm run build)                 # build the frontend; `npm run dev` for hot reload; `npx vitest run` for frontend tests
uv run pytest                             # all tests
uv run pytest tests/test_readiness.py::test_todays_real_case_is_amber   # a single test
sqlite3 garmin_data.db "select day,hrv_avg,rhr,sleep_score from wellness order by day desc limit 7"
```

There is no linter or formatter configured.

## Architecture

The data flows in four stages: Garmin API → sync modules → SQLite (`data/<hash>.db`, one per user, git-ignored) → pure query and logic modules → FastAPI → React UI.

- **Auth** (`auth.py`): garminconnect login. "Keep me signed in" stores a random token in a browser cookie (`gd_session`, 30 days, set with a bit of JS in `main.apply_cookie_action`). On the server, `data/sessions/<hash of token>/` holds that user's Garmin OAuth tokens and email. `main.try_restore_session()` reads the cookie once per browser session. Sign-out deletes the folder and the cookie.
- **Sync**:
  - `sync.py` fetches activities incrementally. It pages newest-first and stops at the first page containing a known ID.
  - `wellness.py` uses the Garmin range endpoints in 28-day chunks. The first sync covers 120 days; after that it re-fetches from the last stored day minus 2 days, because Garmin revises last night's values.
  - In `wellness.py`, each endpoint is a `(name, call, parser)` entry in `ENDPOINTS`. A failing endpoint is logged and skipped, never fatal.
  - Parsers are pure functions, tested against `tests/fixtures/wellness.json`: real payloads with identifiers stripped.
  - Both syncs run automatically once per session (`main.auto_sync`) and from the sidebar button.
- **Users** (`db.py`, `mode.py`): every Garmin account gets its own SQLite file in `data/` (name = hash of the email). `main()` calls `db.use_user(email)` once per script run; query functions take `db_path=None` and fall back to that active file, and raise if no user is signed in. The old `garmin_data.db` is copied to its owner on first sign-in. The OpenRouter key comes from a text box in the coach panel and is remembered in the browser's localStorage (never stored on the server); `GARMIN_DASHBOARDS_LOCAL=1` also allows the `OPENROUTER_API_KEY` env var.
- **Storage** (`db.py`), with three tables:
  - `activities`, `wellness` (one row per day) and `settings` (key/value: FTP, HRV range override, weekly hours target, yearly km goal; defaults in `SETTING_DEFAULTS`).
  - Every row keeps the full Garmin payload in `raw_json`. Add new activity columns through `MIGRATION_COLUMNS` plus `BACKFILL_FROM_RAW` in `init_db`, which backfills old rows with `json_extract`.
  - All stored units are metric SI (m, s, m/s, W, bpm).
- **Queries** (`metrics.py`, `fitness.py`):
  - Plain SQL returns lists of dicts. Every function takes `db_path` so tests can use `tmp_path`.
  - Power-curve data (`maxAvgPower_<seconds>`) is read straight from `raw_json`.
  - `fitness.fitness_series` computes fitness / fatigue / form (CTL/ATL/TSB, 42/7-day) from Garmin's per-activity `training_load`.
- **Athlete intelligence**, all pure functions with no web framework:
  - `readiness.assess()` scores signals as good / caution / bad: HRV against range, HRV trend, resting HR against its 30-day mean, sleep, form, hard sessions in the last 48 h, and Garmin readiness if present.
  - The score starts at 100. A caution costs 15 and a bad signal costs 35. 75+ is green, 45–74 amber, below 45 red. Thresholds are module constants.
  - The HRV range comes from the settings override, then Garmin's baseline, then the 60-day mean ± SD.
  - `workouts.py`: workout libraries stored as data, one per sport (cycling, running, swimming; steps as fractions of FTP or of threshold running speed). `suggest()` takes a sport and the athlete's level in it (settings `level_<sport>`, else `assess_level()`: the best of weekly volume, longest recent session, and, for running, overall fitness from all sports capped at intermediate); level caps session length, scales reps and swaps hard sessions for gentler ones. `build()` fits a workout into the available minutes. `suggest()` maps red to recovery, amber to endurance, and green to VO₂max, tempo, threshold or sweet spot, based on recent hard minutes.
  - `coach.py`: `build_payload()` makes an identifier-free numeric summary (a test enforces this). `ask_coach()` POSTs to OpenRouter's chat completions endpoint with stdlib `urllib` (JSON mode, validated into a Pydantic `CoachAdvice`). The Home page has a model menu (`coach.MODELS`, plus "Other…" for any id); `OPENROUTER_MODEL` sets the initial choice. The rules engine stays the source of truth.
- **API** (`api.py`): FastAPI app. Cookie sessions (`current_session`), per-user DB path passed explicitly, JSON endpoints under `/api` (home, activities, trends, ride, settings, sync, coach), and static serving of `web/dist`. `ride.py` shapes ride samples for the chart.
- **UI** (`web/`, React + TS + Vite + Recharts): pages Home (today only), Week overview (`Activities.tsx`), Find, Trends, Progress, Ride and Settings in `src/pages/`; shared helpers in `src/lib.ts` (vitest) and `src/components.tsx`. Keep the existing colors: `ZONE_COLORS` (HR/power zones) and the channel colors (red HR, orange power, blue speed, grey elevation).

## Conventions

- Keep logic in pure, testable modules. API and UI code only read from them.
- Never send the user's email, names, locations, GPS or `raw_json` to external services (the coach payload included).
- The route map (`RouteMap.tsx`) draws the GPS path locally. Street-map tiles from openstreetmap.org load only when the user ticks the box, because tile requests reveal the area.
- The coach only runs when the user has entered an OpenRouter key (or, in local mode, `OPENROUTER_API_KEY` is set); see `coach.is_available()`.

## Status and next steps

Done, uncommitted on `main` as of 2026-11-08:

- Wellness sync and tables, settings, the readiness engine, the workout library, the Claude coach, the Home page, the Settings expander, and the HRV/resting HR chart on Trends.
- All Python tests (`uv run pytest`) and vitest tests pass.
- Checked against real data: Nov 8 came out amber (70), with HRV 90 below the 96–124 range on 4 of the last 5 nights, and the suggestion was a zone 2 endurance ride at 151–181 W.

Next:
1. Visually check every page in a browser (the React app was only type-checked, built and smoke-tested over HTTP). See `docs/PRELAUNCH.md`.
2. Try "Ask coach" end to end with a real `OPENROUTER_API_KEY`.
3. Update `README.md`: wellness sync, Settings, and coach setup (`OPENROUTER_API_KEY`, optional `OPENROUTER_MODEL`).
4. Commit the work. Commit only when asked; end messages with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
5. Later, push suggested workouts to the watch with garminconnect's `upload_workout` / `schedule_workout`. This was deliberately left out of v1.
6. Possible tuning: whether a long, easy activity such as a hike should count as a "hard day" in `readiness.is_hard_day`. It currently checks only Z4+Z5 minutes ≥ 10, aerobic TE ≥ 4, or load ≥ 150.
