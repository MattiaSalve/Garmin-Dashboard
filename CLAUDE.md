# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

A Garmin Connect dashboard (FastAPI backend, React frontend in `web/`) for one cyclist's Garmin Connect data. It does four things:

- Syncs activities and daily recovery data (HRV, resting HR, sleep, Body Battery) into a local SQLite file.
- Charts training and recovery.
- Builds a multi-week training plan from the athlete's goals, sports and weekly time, and proposes changes when recovery or real training differs from it.
- Recommends a workout for today from recovery signals (and today's planned session). An optional "Ask coach" button sends a summary to an LLM via OpenRouter.

The athlete uses a Forerunner 255 (it has no Garmin Training Readiness), with FTP 252 W and a usual HRV of 100–120 ms. Garmin's own baseline for them is 96–124 ms.

## Commands

```bash
uv sync                                   # install (Python >= 3.15, managed by uv)
uv run garmin-dashboards                  # FastAPI on :8000, serves web/dist
(cd web && npm run build)                 # build the frontend; `npm run dev` for hot reload; `npx vitest run` for frontend tests
uv run pytest                             # all tests
uv run pytest tests/test_readiness.py::test_todays_real_case_is_amber   # a single test
sqlite3 data/<hash>.db "select day,hrv_avg,hrv_status,rhr,sleep_score from wellness order by day desc limit 7"   # one file per user
```

There is no linter or formatter configured.

## Architecture

The data flows in four stages: Garmin API → sync modules → SQLite (`data/<hash>.db`, one per user, git-ignored) → pure query and logic modules → FastAPI → React UI.

- **Auth** (`auth.py`, routes in `api.py`):
  - `POST /api/login` calls `auth.start_login`. It returns `("ok", api)`, or `("needs_mfa", pending_id)` for accounts with two-step verification; then `POST /api/login/mfa {pending, code}` calls `auth.finish_login`.
  - Pending sign-ins live only in memory (`auth.PENDING`), with a 5-minute TTL and 3 code attempts. Garmin's `client_state` holds cookies, so it is never written to disk.
  - The clean path calls the library's private `_load_profile_and_settings()`, because `return_on_mfa` mode skips it. That call stays in `start_login`.
  - The session cookie is `gd_session`, httponly, set by the API. "Keep me signed in" makes it last 30 days and saves Garmin OAuth tokens plus `account.json` (email, created) under `data/sessions/<hash of token>/`. `api.current_session` restores a session from there after a server restart.
  - Sign-out deletes the folder and the cookie.
  - `DELETE /api/account` → `auth.delete_user_data(email)`: deletes the user's DB, every session folder with their email, and the legacy `garmin_data.db` if it is theirs (otherwise it would be re-adopted).
- **Sync**:
  - `sync.py` fetches activities incrementally. It pages newest-first and stops at the first page containing a known ID.
  - `wellness.py` uses the Garmin range endpoints in 28-day chunks. The first sync covers 120 days; after that it re-fetches from the last stored day minus 2 days, because Garmin revises last night's values.
  - In `wellness.py`, each endpoint is a `(name, call, parser)` entry in `ENDPOINTS`. A failing endpoint is logged and skipped, never fatal.
  - Parsers are pure functions, tested against `tests/fixtures/wellness.json`: real payloads with identifiers stripped.
  - `POST /api/sync` runs both, one at a time per user (409 if one is already running). The web app syncs automatically once per day per tab (`web/src/sync.tsx`, `sessionStorage["synced:<email>"]`), and from the top-bar button.
- **Users and dates** (`db.py`, `api.py`):
  - Every Garmin account gets its own SQLite file in `data/` (name = hash of the lower-cased email). The API keeps the path on the session and passes `db_path` explicitly everywhere; `db.resolve(None)` raises, so a forgotten path never falls back to someone's data.
  - The old `garmin_data.db` is copied to its owner on first sign-in.
  - "Today" is the browser's date: `api.ts` sends `X-Local-Date`, and the `local_today` dependency uses it if it is within ±1 day of UTC.
  - The OpenRouter key comes from a text box in the coach panel and is remembered in the browser's localStorage (never stored on the server); `GARMIN_DASHBOARDS_LOCAL=1` also allows the `OPENROUTER_API_KEY` env var.
- **Storage** (`db.py`), with five tables:
  - `activities`, `wellness` (one row per day) and `settings` (key/value: FTP, HRV range override, weekly hours target, yearly km goal, levels, threshold paces, pool length; defaults in `SETTING_DEFAULTS`, allowed ranges in `SETTING_LIMITS`, checked by `settings_errors`). Text values share the table (`get_text_setting`): coach goals, and `pushed_workout:<day>:<sport>` (the Garmin workout id last sent that day).
  - `plan_days` (PK day + slot; sport, kind, minutes, planned_load, source auto/user/coach/adjusted, locked, note; a `rest`/`rest` row keeps a day free) and `plan_proposals` (unique `dedupe_key`, `changes_json`, status open/applied/dismissed/stale/expired, source rules/coach). Plan setup lives in text settings: `training_profile`, `training_goals` (JSON) and `week_hours:<monday>` overrides.
  - Every row keeps the full Garmin payload in `raw_json`. Add new activity columns through `MIGRATION_COLUMNS` plus `BACKFILL_FROM_RAW` in `init_db`, which backfills old rows with `json_extract`.
  - All stored units are metric SI (m, s, m/s, W, bpm).
- **Queries** (`metrics.py`, `fitness.py`):
  - Plain SQL returns lists of dicts. Every function takes `db_path` so tests can use `tmp_path`.
  - Power-curve data (`maxAvgPower_<seconds>`) is read straight from `raw_json`.
  - `fitness.fitness_series` computes fitness / fatigue / form (CTL/ATL/TSB, 42/7-day) from Garmin's per-activity `training_load`. Each row's form is computed before that day's load, so `fitness.form_today(series)` (the last row) is start-of-today form.
  - `metrics.activity_loads`: per-activity day, sport, minutes, distance, load and Z4+Z5 minutes, used by the plan.
  - `metrics.ftp_estimate`: 0.95 × best 20-minute power from rides in the last 90 days. `/home` returns `ftp_hint` when it is ≥3% above the set FTP (never suggests lowering).
- **Athlete intelligence**, all pure functions with no web framework:
  - `readiness.assess()` scores signals as good / caution / bad: HRV against range, HRV trend, resting HR against its 30-day mean, sleep, form, hard sessions in the last 48 h, and Garmin readiness if present.
  - The HRV trend uses Garmin's `hrv_status` when it is LOW or POOR, otherwise 3 of the last 5 nights below range.
  - The score starts at 100. A caution costs 15 and a bad signal costs 35. 75+ is green, 45–74 amber, below 45 red. With no HRV, resting HR or sleep for last night, green is capped at amber (`MISSING_DATA_LEVEL`). Thresholds are module constants.
  - The HRV range comes from the settings override, then Garmin's baseline, then the 60-day mean ± SD.
  - `workouts.py`: workout libraries stored as data, one per sport (cycling, running, swimming; steps as fractions of FTP or of threshold running speed). `suggest()` takes a sport and the athlete's level in it (settings `level_<sport>`, else `assess_level()`: the best of weekly volume, longest recent session, and, for running, overall fitness from all sports capped at intermediate); level caps session length, scales reps and swaps hard sessions for gentler ones. `build()` fits a workout into the available minutes. `suggest()` maps red to recovery, amber to endurance, and green to VO₂max, tempo, threshold or sweet spot, based on recent hard minutes.
  - `gym.py`: gym sessions (lower, upper, full body, core, mobility) as data. They list exercises (sets, reps, RPE, rest) instead of timed steps. The `gym_goal` setting (general strength / support endurance training / build muscle) picks rep ranges and which sessions are favoured. `last_trained()` reads the exercise categories in each strength activity's `summarizedExerciseSets` (in `raw_json`) to see which muscle groups rested longest; hard rides or runs in the last 48 h block heavy leg work. Sport `gym` goes through `/home` and `/coach` like the other sports, with `workout.exercises` instead of `steps`, and the coach can return `adjusted_exercises`.
  - `garmin_workout.py`: builds Garmin workout dicts with garminconnect's typed models, for all four sports.
    - `to_garmin()` (cycling, running, swimming): step types come from position and label (warm-up steps need "warm" in the label, the cool-down needs "cool"). Cycling gets watt targets from FTP, running gets pace targets from threshold pace.
    - Swim steps become distances in whole there-and-back lengths of the `pool_length_m` setting, from threshold swim pace. Rests (≤ 30 % of threshold speed) stay timed. The pace goes in the step note, not a CSS target (the Forerunner 255 has no CSS). Without a swim pace, steps stay time-based.
    - `to_garmin_strength()` (gym): a timed warm-up (none for mobility), then one sets × reps (or × seconds for holds) block per exercise. `EXERCISE_RULES` maps exercise names to Garmin catalog entries (`garminconnect.exercises`), with a muscle-group fallback for names the coach invents. Mapped categories must count for the same group in `gym.CATEGORY_GROUPS`, and mobility work never lands in a counted category; a test checks both.
    - `POST /api/workout/push` uploads the workout, schedules it for today and deletes that day's earlier copy.
  - `workouts._rpe` and `rpeFor` in `web/src/lib.ts` must stay in step (the coach's workouts use the TS one).
  - `plan.py`: the training plan. Its currency is Garmin's training load, calibrated per sport and intensity class from the athlete's own history (`load_rates`, 180 days).
    - `build_plan()`: week phases (with a dated goal: build, recovery every 4th week, two taper weeks, race week; base when more than 16 weeks out; otherwise a 3:1 cycle from `profile.start`), hours ramped from recent volume towards `hours_per_week` (≤ +10 %/week after the first), and a load cap that keeps the CTL ramp ≤ `RAMP_WARNING` (moved here; `api.py` imports it). `HORIZON_WEEKS` (4) are filled with sessions; later weeks are targets only.
    - `distribute_week()`: sports by share, long session on `long_day`, hard days never adjacent or after the long day, leg gym never the day before a key session. It keeps fixed sessions (non-auto, locked, past). Regenerate (`replace_auto_from`) only replaces unlocked `auto` rows.
    - `reconcile()` only proposes, never applies: overshoot (a day ≥ 1.5× plan and +80 load, or the week ≥ 1.2×) → ease the next hard session, then trim ≤ 30 %. Recovery streak (today and 2 of the last 3 days amber/red) → hard days easy for 4 days. Missed key session → moved this week if spacing allows, else dropped. Proposals are de-duplicated by key; `is_stale` refuses to apply a change when the plan no longer matches its `before`.
    - `today_from_plan()` is the only automatic change: red/needs-rest/amber downgrade today's session on Home (`plan_today` in `/home`; `used` says whether the requested sport/focus followed it).
    - `coach_summary()` / `coach_changes()`: the identifier-free plan block for the coach (no goal names or activity names), and the coach's `plan_changes` turned into proposal changes (today to the end of next week, never locked rows, valid kinds only).
  - `coach.py`: `build_payload()` makes an identifier-free numeric summary (a test enforces this). With a plan it adds `training_plan`; `CoachAdvice.plan_changes` comes back as a `source=coach` proposal (`/coach` and `/plan/review`). `ask_coach()` POSTs to OpenRouter's chat completions endpoint with stdlib `urllib` (JSON mode, validated into a Pydantic `CoachAdvice`). The Home page has a model menu (`coach.MODELS`, plus "Other…" for any id); `OPENROUTER_MODEL` sets the initial choice. The rules engine stays the source of truth.
- **API** (`api.py`): FastAPI app. Cookie sessions (`current_session`), per-user DB path passed explicitly, JSON endpoints under `/api` (home, activities, trends, ride, settings, sync, coach, plan: `GET /plan` tops up empty weeks and stores new rule proposals; `PUT /plan/setup`, `POST /plan/generate`, `PUT /plan/week/{monday}`, day add/edit/delete/move/preview, `proposals/{id}/apply|dismiss`, `POST /plan/review`), and static serving of `web/dist`. `ride.py` shapes ride samples for the chart.
- **UI** (`web/`, React + TS + Vite + Recharts): pages Home (today only; follows today's plan once a day via `sessionStorage["plan_applied:<day>"]`), Plan (setup, suggested changes, coach review, week rows with a session editor), Week overview (`Activities.tsx`), Find, Trends, Progress, Ride and Settings in `src/pages/`; shared helpers in `src/lib.ts` (vitest) and `src/components.tsx`. Keep the existing colors: `ZONE_COLORS` (HR/power zones) and the channel colors (red HR, orange power, blue speed, grey elevation).

## Conventions

- Keep logic in pure, testable modules. API and UI code only read from them.
- Never send the user's email, names, locations, GPS or `raw_json` to external services (the coach payload included).
- The route map (`RouteMap.tsx`) draws the GPS path locally. Street-map tiles from openstreetmap.org load only when the user ticks the box, because tile requests reveal the area.
- The coach only runs when the user has entered an OpenRouter key (or, in local mode, `OPENROUTER_API_KEY` is set); see `coach.is_available()`.

## Status and next steps

Done, uncommitted on `main` as of 2026-11-13 (on top of the earlier wellness / readiness / coach work):

- Bug fixes: form no longer lags a day; the coach uses the target sport's pace and the backend RPE scale; settings are range-checked.
- Missing last-night data caps readiness at amber; Garmin's HRV status feeds the trend; daily auto-sync and a "not synced yet" banner on Home.
- Beta basics: browser local date, Settings → Delete my data.
- Garmin two-step verification sign-in, FTP update prompt, Send to watch for all four sports (swims as pool-length distances, gym sessions as Garmin exercises). `HIP_STABILITY` now counts as core, not legs (Garmin files the dead bug there).
- Cleanup: removed the Streamlit-era active-DB fallback, `latest_vo2max`, env-var credentials, and `stress_avg` from the wellness query (the column stays; nothing fills it yet).
- Training plan: goals (event / volume / performance / general), sports with shares, hours and sessions a week, a multi-week plan with phases, editing (move, lock, add, delete, busy-week hours), rule-based proposals after overshoots, misses and poor-recovery streaks, Home following today's plan, and coach plan review (`plan.py`, Plan page). Checked against a copy of the real DB (sensible 4 → 7 h ramp, recovery and taper weeks).
- `uv run pytest` and `npx vitest run` pass; the web app type-checks and builds.

Next:
1. Visually check every page in a browser, including the new banners, the 2FA step, Send to watch and the Plan page (setup, editor, proposals). See `docs/PRELAUNCH.md`.
2. Real-world checks that tests can't do: a 2FA-enabled Garmin account; a pushed workout in Garmin Connect's calendar with watt targets (and only one copy after re-sending); a pushed swim (pool length and distances right on the watch) and gym session (right exercise names); "Ask coach" and "Review my plan" with a real OpenRouter key.
3. Commit the work. Commit only when asked; end messages with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
4. Later: fill `stress_avg` from `get_daily_stress_stats` (needs a real payload fixture first).
5. Plan tuning after some real weeks: the overshoot thresholds (`OVERSHOOT_*`, `WEEK_OVERSHOOT_FACTOR`), the starting hours for new users (`NEW_START_*`) and the ramp points per level (`RAMP_POINTS`).
6. Possible tuning: whether a long, easy activity such as a hike should count as a "hard day" in `readiness.is_hard_day`. It currently checks only Z4+Z5 minutes ≥ 10, aerobic TE ≥ 4, or load ≥ 150.
