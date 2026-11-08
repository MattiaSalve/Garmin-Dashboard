# Pre-launch checklist

Tick items off as they are done. Stages are in order: don't open a stage until the one before it is finished.

## Stage 1: before anyone else uses it

- [ ] Run the app and look at every page in a real browser (Home, Activities, Find, Trends, ride detail).
- [ ] Home page charts: workout steps, HRV band over load bars, intensity split bar.
- [ ] Ride chart: hover tooltip lists every selected channel to 2 decimals, zoom and reset work.
- [ ] "Ask coach" works end to end with a real OpenRouter key; a bad key shows a clear error.
- [ ] "Keep me signed in": sign in with it ticked, reload, you stay signed in. Sign out, reload, you don't.
- [ ] Two different Garmin accounts in two browsers never see each other's data.
- [ ] A real Garmin sign-in works **from the server** (Garmin may block datacenter IPs).
- [ ] README covers setup, hosting, environment variables and the OpenRouter key.
- [ ] All work committed; tests pass (`uv run pytest`).

## Stage 2: private beta (3–5 people)

- [ ] Deployed behind https (cookies are marked `Secure` only on https).
- [ ] `data/` is on a persistent disk and backed up automatically; a restore has been tested once.
- [ ] Users can delete their account and all stored data (database file and saved sign-in).
- [ ] Privacy note: what is stored, that the coach sends only numbers (no names, places, GPS or account details), how to delete data.
- [ ] Clear messages for wrong password, two-factor prompts, Garmin rate limits and sync failures.
- [ ] First-run experience: sync progress, and sensible empty states (no HRV data, no power meter, no rides).
- [ ] Logs and uptime monitoring in place.
- [ ] Testers include different devices: with and without HRV, with and without power, non-cyclists.

## Stage 3: before sharing publicly

- [ ] Rate limiting on sign-in attempts and coach calls.
- [ ] Readiness thresholds, FTP and HRV range adapt per user, not tuned to one athlete.
- [ ] Phone layout checked on a real phone.
- [ ] Issues from the beta fixed or written down.
- [ ] A plan for when Garmin's unofficial API breaks (who gets told, how fast it is fixed).
- [ ] Server is patched and secrets (OpenRouter key if used server-side, any admin access) are not in the repo.

## Later

- [ ] Push suggested workouts to the watch (`upload_workout` / `schedule_workout`).
- [ ] Weekly summary, goal and event tracking, more sports, light/dark theme.
- [ ] Other data sources (Strava, Apple Health) so the app doesn't depend on one unofficial API.
