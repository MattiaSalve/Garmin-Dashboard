"""HTTP API for the dashboard (FastAPI). Every data route works on the signed-in user's own database.

Sign-in sets an HttpOnly session cookie. "Keep me signed in" makes it last SESSION_DAYS and saves the
Garmin tokens on disk (see auth.py); otherwise the session lives in server memory until sign-out.
"""

import sqlite3
import threading
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from garminconnect.activity_details import parse_activity_detail_metrics
from pydantic import BaseModel, Field

from garmin_dashboards import auth, coach
from garmin_dashboards.db import SETTING_DEFAULTS, all_settings, get_text_setting, set_text_setting, init_db, ensure_user_db, resolve, set_setting
from garmin_dashboards.fitness import fitness_series
from garmin_dashboards.metrics import (
    SEARCH_ORDERS,
    SPORT_GROUPS,
    by_sport,
    daily_intensity,
    daily_totals,
    daily_training_load,
    daily_wellness,
    distance_by_group,
    estimated_ftp_trend,
    filter_bounds,
    longest_activity,
    avg_weekly_hours,
    power_curve,
    power_prs,
    search_activities,
    sport_evidence,
    summary,
    week_progress,
    weekly_hr_zones,
    weekly_totals,
)
from garmin_dashboards.readiness import assess
from garmin_dashboards.workouts import SPORTS, assess_level, suggest
from garmin_dashboards.ride import samples_payload
from garmin_dashboards.sync import sync
from garmin_dashboards.wellness import sync_wellness

GOALS_KEY = "athlete_goals"
RAMP_WARNING = 7  # fitness points per week, see the Home page
DEFAULT_FTP = 250
HIDDEN_ACTIVITY_COLUMNS = {"raw_json", "user_email", "last_synced"}
WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"


@dataclass
class Session:
    api: object
    email: str
    db_path: str
    persistent: bool
    lock: threading.Lock = field(default_factory=threading.Lock)  # one sync at a time per user
    samples: dict = field(default_factory=dict)  # activity_id -> payload, small per-session cache


SESSIONS: dict[str, Session] = {}


def _secure(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"


def current_session(request: Request) -> Session:
    token = request.cookies.get(auth.COOKIE_NAME)
    if not token:
        raise HTTPException(401, "Not signed in.")
    session = SESSIONS.get(token)
    if session is None:
        try:
            restored = auth.restore(token)
        except Exception:
            auth.forget(None, token)
            restored = None
        if restored is None:
            raise HTTPException(401, "Not signed in.")
        api, email = restored
        session = SESSIONS[token] = _new_session(api, email, persistent=True)
    return session


def _new_session(api, email: str, persistent: bool) -> Session:
    path = ensure_user_db(email)
    init_db(path)
    return Session(api, email, path, persistent)


router = APIRouter(prefix="/api")


# ------------------------------------------------------------------ auth


class LoginBody(BaseModel):
    email: str
    password: str
    remember: bool = False


@router.post("/login")
def login(body: LoginBody, request: Request, response: Response) -> dict:
    email = body.email.strip()
    if not email or not body.password:
        raise HTTPException(400, "Enter your email and password.")
    token = auth.new_session_token()
    try:
        api = auth.login(email, body.password, session_token=token if body.remember else None)
    except Exception as exc:  # garminconnect raises several error types
        raise HTTPException(401, f"Sign-in failed: {exc}") from exc
    SESSIONS[token] = _new_session(api, email, persistent=body.remember)
    response.set_cookie(
        auth.COOKIE_NAME, token, httponly=True, samesite="lax", secure=_secure(request),
        max_age=auth.SESSION_DAYS * 86400 if body.remember else None,
    )
    return {"email": email}


@router.post("/logout")
def logout(request: Request, response: Response) -> dict:
    token = request.cookies.get(auth.COOKIE_NAME)
    session = SESSIONS.pop(token, None) if token else None
    auth.forget(session.api if session else None, token)
    response.delete_cookie(auth.COOKIE_NAME)
    return {"ok": True}


@router.get("/me")
def me(session: Session = Depends(current_session)) -> dict:
    return {"email": session.email, "coach_key_from_server": coach.is_available()}


# ------------------------------------------------------------------ sync and settings


class SyncBody(BaseModel):
    full: bool = False


@router.post("/sync")
def run_sync(body: SyncBody, session: Session = Depends(current_session)) -> dict:
    if not session.lock.acquire(blocking=False):
        raise HTTPException(409, "A sync is already running.")
    try:
        try:
            new, fetched = sync(session.api, session.email, full=body.full, db_path=session.db_path)
        except Exception as exc:
            raise HTTPException(502, f"Activity sync failed: {exc}") from exc
        failed: list[str] = []
        wellness_days = 0
        try:
            wellness_days, failed = sync_wellness(session.api, full=body.full, db_path=session.db_path)
            failed = [f for f in failed if f != "readiness"]  # not every watch supports it
        except Exception as exc:
            failed = [f"recovery data ({exc})"]
        return {"new_activities": new, "fetched": fetched, "wellness_days": wellness_days, "failed": failed}
    finally:
        session.lock.release()


@router.get("/settings")
def get_settings(session: Session = Depends(current_session)) -> dict:
    return all_settings(session.db_path)


@router.put("/settings")
def put_settings(values: dict[str, float | None], session: Session = Depends(current_session)) -> dict:
    unknown = set(values) - set(SETTING_DEFAULTS)
    if unknown:
        raise HTTPException(400, f"Unknown settings: {', '.join(sorted(unknown))}")
    for key, value in values.items():
        if value is not None and value < 0:
            raise HTTPException(400, f"{key} can't be negative.")
        set_setting(key, value, session.db_path)
    return all_settings(session.db_path)


@router.get("/filters")
def filters(session: Session = Depends(current_session)) -> dict:
    with sqlite3.connect(resolve(session.db_path)) as conn:
        sports = [
            r[0] for r in conn.execute(
                "SELECT DISTINCT sport_type FROM activities WHERE sport_type IS NOT NULL ORDER BY sport_type"
            )
        ]
    return {"sports": sports, "groups": list(SPORT_GROUPS), "bounds": filter_bounds(session.db_path), "orders": list(SEARCH_ORDERS)}


# ------------------------------------------------------------------ home and coach


def _check_sport(sport: str) -> str:
    if sport not in SPORTS:
        raise HTTPException(400, f"Unknown sport: {sport}. Choose one of {', '.join(SPORTS)}.")
    return sport


def _levels(db: str, settings: dict, today: date, aerobic_fitness: float | None) -> dict[str, dict]:
    """Level per sport: the one the athlete set, else judged from their training in the sport and overall fitness."""
    return {
        key: assess_level(key, settings.get(f"level_{key}"), sport_evidence(key.title(), today, db), aerobic_fitness)
        for key in SPORTS
    }


def _home_context(db: str, minutes: int, sport: str = "cycling") -> dict:
    today = date.today()
    settings = all_settings(db)
    wellness = daily_wellness(today - timedelta(days=90), today, db)
    intensity = daily_intensity(today - timedelta(days=30), today, db)
    load = {date.fromisoformat(r["day"]): r["load"] for r in daily_training_load(db)}
    series = fitness_series(load, end=today)
    # Form at the start of today (yesterday's value), so a ride done today doesn't change the verdict.
    form = series[-2]["form"] if len(series) >= 2 else None
    readiness = assess(wellness, intensity, form, settings, today)
    # Peak fitness over the last 90 days, from every sport: a general aerobic base that carries over somewhat.
    recent_fitness = [p["fitness"] for p in series if p["day"] >= today - timedelta(days=90)]
    levels = _levels(db, settings, today, max(recent_fitness) if recent_fitness else None)
    suggestion = suggest(readiness, minutes, intensity, today, sport, levels[sport]["level"])
    return {
        "sport": sport, "levels": levels,
        "today": today, "settings": settings, "wellness": wellness, "intensity": intensity,
        "series": series, "form": form, "readiness": readiness, "suggestion": suggestion,
        "ftp": settings.get("ftp_w") or DEFAULT_FTP,
    }


@router.get("/home")
def home(
    minutes: int = Query(60, ge=20, le=300), sport: str = "cycling", session: Session = Depends(current_session),
) -> dict:
    db = session.db_path
    sport = _check_sport(sport)
    group = sport.title()  # "cycling" -> the "Cycling" entry in SPORT_GROUPS
    c = _home_context(db, minutes, sport)
    today, settings, series = c["today"], c["settings"], c["series"]
    suggestion, ftp = c["suggestion"], c["ftp"]
    profile = SPORTS[sport]
    pace = settings.get(profile.pace_setting) if profile.pace_setting else None

    week = week_progress(today, db, group)
    year_start = date(today.year, 1, 1)
    longest = longest_activity(year_start, today, SPORT_GROUPS[group], db)
    ramp = series[-1]["fitness"] - series[-8]["fitness"] if len(series) > 7 else None
    ftp_trend = estimated_ftp_trend(db) if sport == "cycling" else []
    return {
        "sport": sport,
        "levels": c["levels"],
        "today": today,
        "has_wellness": bool(c["wellness"]),
        "readiness": asdict(c["readiness"]),
        "today_row": next((r for r in c["wellness"] if r["day"] == today.isoformat()), None),
        "form": c["form"],
        "ftp": ftp,
        "threshold_pace_s": pace,
        "pace_unit": profile.pace_unit,
        "settings": settings,
        "workout": {
            "key": suggestion.workout.key, "name": suggestion.workout.name,
            "purpose": suggestion.workout.purpose, "note": suggestion.workout.note,
            "minutes": suggestion.minutes, "rationale": suggestion.rationale,
            "steps": suggestion.in_targets(ftp, pace),
        },
        "wellness": c["wellness"],
        "intensity": daily_intensity(today - timedelta(days=89), today, db),
        "week": week,
        "week_hours_all": summary(week["week_start"], today, db_path=db)["total_moving_duration_h"],
        "week_activities": [
            a for a in search_activities(start=week["week_start"], end=today, db_path=db)
            if a["sport_type"] in SPORT_GROUPS[group]
        ],
        "ramp": ramp,
        "ramp_warning": RAMP_WARNING,
        "power_prs": power_prs(today, db_path=db) if sport == "cycling" else [],
        "year": {
            "year": today.year,
            "km": distance_by_group(year_start, today, db)[group],
            "goal": settings.get("yearly_km_goal"),
            "days_elapsed": (today - year_start).days,
            "days_in_year": (date(today.year, 12, 31) - year_start).days + 1,
            "longest": longest,
        },
        "ftp_trend": ftp_trend[-18:],
    }


class CoachBody(BaseModel):
    minutes: int = 60
    sport: str = "cycling"
    model: str | None = None
    api_key: str | None = None
    # Turns after the opening summary (the server rebuilds that fresh), ending with the athlete's message.
    history: list[coach.ChatMessage] = Field(default_factory=list, max_length=20)


class GoalsBody(BaseModel):
    goals: str = Field(max_length=coach.GOALS_MAX)


@router.get("/coach/goals")
def get_goals(session: Session = Depends(current_session)) -> dict:
    return {"goals": get_text_setting(GOALS_KEY, session.db_path)}


@router.put("/coach/goals")
def put_goals(body: GoalsBody, session: Session = Depends(current_session)) -> dict:
    set_text_setting(GOALS_KEY, body.goals, session.db_path)
    return {"goals": get_text_setting(GOALS_KEY, session.db_path)}


@router.get("/coach/models")
def coach_models() -> dict:
    return {"models": list(dict.fromkeys([coach.DEFAULT_MODEL, *coach.MODELS])), "default": coach.DEFAULT_MODEL}


@router.post("/coach")
def ask_coach(body: CoachBody, session: Session = Depends(current_session)) -> dict:
    c = _home_context(session.db_path, body.minutes, _check_sport(body.sport))
    s = c["settings"]
    payload = coach.build_payload(
        c["readiness"], c["suggestion"], c["wellness"], c["intensity"], c["form"], c["ftp"],
        goals=get_text_setting(GOALS_KEY, session.db_path),
        targets={"weekly_hours": s.get("weekly_hours_target"), "yearly_km": s.get("yearly_km_goal")},
        sport=body.sport, levels={k: v["level"] for k, v in c["levels"].items()},
        threshold_pace_s=s.get(SPORTS[body.sport].pace_setting) if SPORTS[body.sport].pace_setting else None,
        pace_unit=SPORTS[body.sport].pace_unit,
    )
    try:
        return coach.ask_coach(payload, model=body.model, api_key=body.api_key, history=body.history).model_dump()
    except coach.CoachError as exc:
        raise HTTPException(400, str(exc)) from exc


# ------------------------------------------------------------------ activities


@router.get("/activities")
def activities(
    start: date | None = None, end: date | None = None, sport: list[str] | None = Query(None),
    text: str | None = None, order: str = "Newest first",
    min_km: float | None = None, max_km: float | None = None,
    min_elevation_m: float | None = None, max_elevation_m: float | None = None,
    min_hours: float | None = None, max_hours: float | None = None,
    session: Session = Depends(current_session),
) -> list[dict]:
    if order not in SEARCH_ORDERS:
        raise HTTPException(400, "Unknown sort order.")
    return search_activities(
        start=start, end=end, sports=sport, text=text, order=order,
        min_km=min_km, max_km=max_km, min_elevation_m=min_elevation_m, max_elevation_m=max_elevation_m,
        min_hours=min_hours, max_hours=max_hours, db_path=session.db_path,
    )


@router.get("/summary")
def activity_summary(start: date, end: date, sport: str | None = None, session: Session = Depends(current_session)) -> dict:
    return summary(start, end, sport, session.db_path)


@router.get("/activities/{activity_id}")
def activity(activity_id: int, session: Session = Depends(current_session)) -> dict:
    with sqlite3.connect(resolve(session.db_path)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM activities WHERE activity_id = ?", (activity_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Activity not found.")
    return {k: v for k, v in dict(row).items() if k not in HIDDEN_ACTIVITY_COLUMNS}


@router.get("/activities/{activity_id}/samples")
def activity_samples(activity_id: int, session: Session = Depends(current_session)) -> dict:
    # Only fetch rides that are in this user's own database.
    with sqlite3.connect(resolve(session.db_path)) as conn:
        known = conn.execute("SELECT 1 FROM activities WHERE activity_id = ?", (activity_id,)).fetchone()
    if known is None:
        raise HTTPException(404, "Activity not found.")
    if activity_id not in session.samples:
        try:
            details = session.api.get_activity_details(str(activity_id))
            frame = pd.DataFrame(parse_activity_detail_metrics(details))
        except Exception as exc:
            raise HTTPException(502, f"Couldn't load second-by-second data: {exc}") from exc
        payload = None if frame.empty else samples_payload(frame)
        if len(session.samples) >= 5:
            session.samples.pop(next(iter(session.samples)))
        session.samples[activity_id] = payload
    payload = session.samples[activity_id]
    if payload is None:
        raise HTTPException(404, "No second-by-second data for this ride.")
    return payload


# ------------------------------------------------------------------ trends


@router.get("/trends/weekly")
def trends_weekly(start: date, end: date, sport: str | None = None, session: Session = Depends(current_session)) -> list[dict]:
    return weekly_totals(start, end, sport, session.db_path)


@router.get("/trends/fitness")
def trends_fitness(start: date, end: date, session: Session = Depends(current_session)) -> list[dict]:
    load = {date.fromisoformat(r["day"]): r["load"] for r in daily_training_load(session.db_path)}
    series = fitness_series(load, end=max(end, max(load, default=end)))
    return [r for r in series if start <= r["day"] <= end]


@router.get("/trends/hr-zones")
def trends_hr_zones(start: date, end: date, sport: str | None = None, session: Session = Depends(current_session)) -> list[dict]:
    return weekly_hr_zones(start, end, sport, session.db_path)


@router.get("/trends/power-curve")
def trends_power_curve(start: date, end: date, sport: str | None = None, session: Session = Depends(current_session)) -> list[dict]:
    return power_curve(start, end, sport, session.db_path)


@router.get("/trends/calendar")
def trends_calendar(start: date, end: date, sport: str | None = None, session: Session = Depends(current_session)) -> list[dict]:
    return daily_totals(start, end, sport, session.db_path)


@router.get("/trends/by-sport")
def trends_by_sport(start: date, end: date, session: Session = Depends(current_session)) -> list[dict]:
    return by_sport(start, end, session.db_path)


@router.get("/trends/recovery")
def trends_recovery(start: date, end: date, session: Session = Depends(current_session)) -> list[dict]:
    return daily_wellness(start, end, session.db_path)


# ------------------------------------------------------------------ app


def create_app() -> FastAPI:
    app = FastAPI(title="Garmin dashboard", docs_url=None, redoc_url=None)
    app.include_router(router)

    if WEB_DIST.exists():  # the built React app, served by the same process
        app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path.startswith("api/"):
                raise HTTPException(404)
            candidate = (WEB_DIST / path).resolve()
            if path and candidate.is_file() and WEB_DIST.resolve() in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(WEB_DIST / "index.html")

    return app


app = create_app()
