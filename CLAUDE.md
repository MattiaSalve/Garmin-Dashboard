# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

A personal Streamlit dashboard for one cyclist's Garmin Connect data. It does three things:

- Syncs activities and daily recovery data (HRV, resting HR, sleep, Body Battery) into a local SQLite file.
- Charts training and recovery.
- Recommends a workout for today from recovery signals. An optional "Ask coach" button sends a summary to an LLM via OpenRouter.

The athlete uses a Forerunner 255 (it has no Garmin Training Readiness), with FTP 252 W and a usual HRV of 100–120 ms. Garmin's own baseline for them is 96–124 ms.

## Commands

```bash
uv sync                                   # install (Python >= 3.15, managed by uv)
uv run garmin-dashboards                  # launch the app (wraps `streamlit run src/garmin_dashboards/main.py`)
uv run pytest                             # all tests
uv run pytest tests/test_readiness.py::test_todays_real_case_is_amber   # a single test
sqlite3 garmin_data.db "select day,hrv_avg,rhr,sleep_score from wellness order by day desc limit 7"
```

There is no linter or formatter configured.

## Architecture

The data flows in four stages: Garmin API → sync modules → SQLite (`garmin_data.db`, git-ignored personal data) → pure query and logic modules → Streamlit UI.

- **Auth** (`auth.py`): garminconnect login. With "remember me", tokens are saved in `~/.garmin_dashboards/`. `main.try_restore_session()` re-signs in once per browser session.
- **Sync**:
  - `sync.py` fetches activities incrementally. It pages newest-first and stops at the first page containing a known ID.
  - `wellness.py` uses the Garmin range endpoints in 28-day chunks. The first sync covers 120 days; after that it re-fetches from the last stored day minus 2 days, because Garmin revises last night's values.
  - In `wellness.py`, each endpoint is a `(name, call, parser)` entry in `ENDPOINTS`. A failing endpoint is logged and skipped, never fatal.
  - Parsers are pure functions, tested against `tests/fixtures/wellness.json`: real payloads with identifiers stripped.
  - Both syncs run automatically once per session (`main.auto_sync`) and from the sidebar button.
- **Storage** (`db.py`), with three tables:
  - `activities`, `wellness` (one row per day) and `settings` (key/value: FTP, HRV range override, weekly hours target, yearly km goal; defaults in `SETTING_DEFAULTS`).
  - Every row keeps the full Garmin payload in `raw_json`. Add new activity columns through `MIGRATION_COLUMNS` plus `BACKFILL_FROM_RAW` in `init_db`, which backfills old rows with `json_extract`.
  - All stored units are metric SI (m, s, m/s, W, bpm).
- **Queries** (`metrics.py`, `fitness.py`):
  - Plain SQL returns lists of dicts. Every function takes `db_path` so tests can use `tmp_path`.
  - Power-curve data (`maxAvgPower_<seconds>`) is read straight from `raw_json`.
  - `fitness.fitness_series` computes fitness / fatigue / form (CTL/ATL/TSB, 42/7-day) from Garmin's per-activity `training_load`.
- **Athlete intelligence**, all pure functions with no Streamlit:
  - `readiness.assess()` scores signals as good / caution / bad: HRV against range, HRV trend, resting HR against its 30-day mean, sleep, form, hard sessions in the last 48 h, and Garmin readiness if present.
  - The score starts at 100. A caution costs 15 and a bad signal costs 35. 75+ is green, 45–74 amber, below 45 red. Thresholds are module constants.
  - The HRV range comes from the settings override, then Garmin's baseline, then the 60-day mean ± SD.
  - `workouts.py`: a workout library stored as data (steps as fractions of FTP). `build()` fits a workout into the available minutes. `suggest()` maps red to recovery, amber to endurance, and green to VO₂max, tempo, threshold or sweet spot, based on recent hard minutes.
  - `coach.py`: `build_payload()` makes an identifier-free numeric summary (a test enforces this). `ask_coach()` POSTs to OpenRouter's chat completions endpoint with stdlib `urllib` (JSON mode, validated into a Pydantic `CoachAdvice`). The Home page has a model menu (`coach.MODELS`, plus "Other…" for any id); `OPENROUTER_MODEL` sets the initial choice. The rules engine stays the source of truth.
- **UI**:
  - `main.py` holds the pages `Home | Activities | Find | Trends`, the sidebar (sync, settings form) and the ride-detail charts.
  - `ui_home.py` renders Home: today's readiness and workout, recovery trends, week at a glance, and records.
  - Streamlit runs `main.py` as `__main__`, so **other modules must not import `garmin_dashboards.main`**. That's why `main` passes `pick_ride` into `show_home()` as a callback, and why `ui_home` duplicates `ZONE_COLORS`.
  - Charts use Altair. Keep the existing colors for consistency: `ZONE_COLORS` (HR/power zones), and `CHANNEL_COLORS` (red HR, orange power, blue speed, grey elevation).

## Conventions

- Keep logic in pure, testable modules. Streamlit code only reads from them.
- Never send the user's email, names, locations, GPS or `raw_json` to external services (the coach payload included).
- The coach only runs when `OPENROUTER_API_KEY` is set (`coach.is_available()`).

## Status and next steps

Done, uncommitted on `main` as of 2026-11-08:

- Wellness sync and tables, settings, the readiness engine, the workout library, the Claude coach, the Home page, the Settings expander, and the HRV/resting HR chart on Trends.
- All 30 tests pass.
- Checked against real data: Nov 8 came out amber (70), with HRV 90 below the 96–124 range on 4 of the last 5 nights, and the suggestion was a zone 2 endurance ride at 151–181 W.

Next:
1. Visually check the Home page in a browser (`uv run garmin-dashboards`). It hasn't been looked at yet; only imports and the logic were checked. In particular, look at the Altair layers in `ui_home.py`: the workout step chart, the HRV band over load bars, and the intensity split bar.
2. Try "Ask coach" end to end with a real `OPENROUTER_API_KEY`.
3. Update `README.md`: wellness sync, Settings, and coach setup (`OPENROUTER_API_KEY`, optional `OPENROUTER_MODEL`).
4. Commit the work. Commit only when asked; end messages with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
5. Later, push suggested workouts to the watch with garminconnect's `upload_workout` / `schedule_workout`. This was deliberately left out of v1.
6. Possible tuning: whether a long, easy activity such as a hike should count as a "hard day" in `readiness.is_hard_day`. It currently checks only Z4+Z5 minutes ≥ 10, aerobic TE ≥ 4, or load ≥ 150.
