"""HTTP API for the dashboard (FastAPI). Every data route works on the signed-in user's own database.

Sign-in sets an HttpOnly session cookie. "Keep me signed in" makes it last SESSION_DAYS and saves the
Garmin tokens on disk (see auth.py); otherwise the session lives in server memory until sign-out.
"""

import json
import sqlite3
import threading
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from garminconnect.activity_details import parse_activity_detail_metrics
from pydantic import BaseModel, Field

from garmin_dashboards import auth, coach, garmin_workout, gym, plan
from garmin_dashboards.db import (
    add_proposal, all_settings, clear_plan, delete_plan_day, delete_setting, ensure_user_db, get_plan_days, get_proposal,
    get_proposals, get_text_setting, init_db, resolve, save_plan_days, set_proposal_status, set_setting, set_text_setting,
    settings_errors, text_settings_with_prefix,
)
from garmin_dashboards.fitness import fitness_series, form_today
from garmin_dashboards.metrics import (
    SEARCH_ORDERS,
    SPORT_GROUPS,
    activity_loads,
    by_sport,
    daily_intensity,
    daily_totals,
    daily_training_load,
    daily_wellness,
    distance_by_group,
    estimated_ftp_trend,
    ftp_estimate,
    filter_bounds,
    longest_activity,
    avg_weekly_hours,
    power_curve,
    power_prs,
    search_activities,
    sport_evidence,
    strength_sessions,
    summary,
    week_progress,
    weekly_hr_zones,
    weekly_totals,
)
from garmin_dashboards.plan import RAMP_WARNING
from garmin_dashboards.readiness import Readiness, assess
from garmin_dashboards.workouts import SPORTS, assess_level, suggest
from garmin_dashboards.ride import samples_payload
from garmin_dashboards.sync import sync
from garmin_dashboards.wellness import sync_wellness

GOALS_KEY = "athlete_goals"
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


LOCAL_DATE_HEADER = "x-local-date"


def local_today(request: Request) -> date:
    """The browser's calendar date (sent by the web app), so "today" is the athlete's day and not the
    server's. Anything more than a day away from UTC today is ignored as a bad clock or a forged header."""
    utc = datetime.now(UTC).date()
    try:
        local = date.fromisoformat(request.headers.get(LOCAL_DATE_HEADER, ""))
    except ValueError:
        return utc
    return local if abs((local - utc).days) <= 1 else utc


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


def _signed_in(api, email: str, token: str, remember: bool, request: Request, response: Response) -> dict:
    SESSIONS[token] = _new_session(api, email, persistent=remember)
    response.set_cookie(
        auth.COOKIE_NAME, token, httponly=True, samesite="lax", secure=_secure(request),
        max_age=auth.SESSION_DAYS * 86400 if remember else None,
    )
    return {"email": email}


@router.post("/login")
def login(body: LoginBody, request: Request, response: Response) -> dict:
    email = body.email.strip()
    if not email or not body.password:
        raise HTTPException(400, "Enter your email and password.")
    token = auth.new_session_token()
    try:
        status, result = auth.start_login(email, body.password, token, body.remember)
    except Exception as exc:  # garminconnect raises several error types
        raise HTTPException(401, f"Sign-in failed: {exc}") from exc
    if status == "needs_mfa":  # the cookie token waits server-side with the pending login until the code arrives
        return {"mfa_required": True, "pending": result}
    return _signed_in(result, email, token, body.remember, request, response)


class MfaBody(BaseModel):
    pending: str = Field(max_length=100)
    code: str = Field(min_length=4, max_length=12)


@router.post("/login/mfa")
def login_mfa(body: MfaBody, request: Request, response: Response) -> dict:
    try:
        done = auth.finish_login(body.pending, body.code)
    except auth.LoginExpired as exc:
        raise HTTPException(401, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(401, f"That code didn't work: {exc}") from exc
    return _signed_in(done.api, done.email, done.token, done.remember, request, response)


@router.post("/logout")
def logout(request: Request, response: Response) -> dict:
    token = request.cookies.get(auth.COOKIE_NAME)
    session = SESSIONS.pop(token, None) if token else None
    auth.forget(session.api if session else None, token)
    response.delete_cookie(auth.COOKIE_NAME)
    return {"ok": True}


@router.delete("/account")
def delete_account(response: Response, session: Session = Depends(current_session)) -> dict:
    """Delete everything this app stores about the signed-in user, sign out every browser, and drop the cookie."""
    for token, other in list(SESSIONS.items()):
        if auth._same_email(other.email, session.email):
            SESSIONS.pop(token, None)
            auth.forget(other.api, token)
    auth.delete_user_data(session.email)
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
    errors = settings_errors(values, all_settings(session.db_path))
    if errors:
        raise HTTPException(400, " ".join(errors))
    for key, value in values.items():
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


def _check_focus(focus: str | None) -> str | None:
    if focus in (None, "", "auto"):
        return None
    if focus not in gym.FOCUS:
        raise HTTPException(400, f"Unknown gym focus: {focus}. Choose one of {', '.join(gym.FOCUS)}.")
    return focus


def _check_sport(sport: str) -> str:
    if sport != gym.KEY and sport not in SPORTS:
        raise HTTPException(400, f"Unknown sport: {sport}. Choose one of {', '.join([*SPORTS, gym.KEY])}.")
    return sport


def _levels(db: str, settings: dict, today: date, aerobic_fitness: float | None) -> dict[str, dict]:
    """Level per sport: the one the athlete set, else judged from their training in the sport and overall fitness."""
    levels = {
        key: assess_level(key, settings.get(f"level_{key}"), sport_evidence(key.title(), today, db), aerobic_fitness)
        for key in SPORTS
    }
    sessions = summary(today - timedelta(days=83), today, gym.GROUP_NAME, db)["activity_count"]
    levels[gym.KEY] = gym.assess_level(settings.get("level_gym"), sessions)
    return levels


def _home_context(db: str, minutes: int, today: date, sport: str = "cycling", focus: str | None = None) -> dict:
    settings = all_settings(db)
    wellness = daily_wellness(today - timedelta(days=90), today, db)
    intensity = daily_intensity(today - timedelta(days=30), today, db)
    load = {date.fromisoformat(r["day"]): r["load"] for r in daily_training_load(db)}
    series = fitness_series(load, end=today)
    # Form at the start of today: includes yesterday's training, so a ride done today doesn't change the verdict.
    form = form_today(series)
    readiness = assess(wellness, intensity, form, settings, today)
    # Peak fitness over the last 90 days, from every sport: a general aerobic base that carries over somewhat.
    recent_fitness = [p["fitness"] for p in series if p["day"] >= today - timedelta(days=90)]
    levels = _levels(db, settings, today, max(recent_fitness) if recent_fitness else None)
    trained = gym.last_trained(strength_sessions(today - timedelta(days=60), today, db), today)
    gym_goal = gym.goal_from_setting(settings.get("gym_goal"))
    # Today's planned session, after today's readiness check. It drives the workout when the athlete is
    # looking at the planned sport (and, for gym, hasn't picked a different focus).
    plan_today = plan.today_from_plan(_plan_rows(db, today, today), readiness, intensity, today)
    use_plan = bool(plan_today) and plan_today["sport"] == sport and (sport != gym.KEY or focus in (None, plan_today["kind"]))
    if plan_today:
        plan_today["used"] = use_plan
    if use_plan:
        suggestion = plan.plan_suggestion(plan_today, readiness, intensity, trained, today, levels[sport]["level"], gym_goal, minutes)
    elif sport == gym.KEY:
        suggestion = gym.suggest(readiness, minutes, intensity, trained, today, gym_goal, levels[sport]["level"], focus)
    else:
        suggestion = suggest(readiness, minutes, intensity, today, sport, levels[sport]["level"])
    return {
        "sport": sport, "levels": levels, "trained": trained, "plan_today": plan_today,
        "today": today, "settings": settings, "wellness": wellness, "intensity": intensity,
        "series": series, "form": form, "readiness": readiness, "suggestion": suggestion,
        "ftp": settings.get("ftp_w") or DEFAULT_FTP,
    }


def _gym_workout(s: gym.GymSuggestion) -> dict:
    """A gym session in the same shape as an endurance workout, with `exercises` instead of timed steps."""
    return {
        "key": s.session.key, "name": s.session.name, "purpose": s.session.purpose, "note": s.session.note,
        "minutes": s.minutes, "rationale": s.rationale, "steps": [], "exercises": s.exercises,
    }


def _workout_out(s, sport: str, settings: dict) -> dict:
    """A suggestion as the web app shows it: steps with targets for endurance sports, exercises for gym."""
    if sport == gym.KEY:
        return _gym_workout(s)
    profile = SPORTS[sport]
    pace = settings.get(profile.pace_setting) if profile.pace_setting else None
    return {
        "key": s.workout.key, "name": s.workout.name, "purpose": s.workout.purpose, "note": s.workout.note,
        "minutes": s.minutes, "rationale": s.rationale,
        "steps": s.in_targets(settings.get("ftp_w") or DEFAULT_FTP, pace),
    }


def _gym_block(s: gym.GymSuggestion, trained: dict) -> dict:
    return {
        "goal": s.goal, "goal_label": gym.GOAL_LABEL[s.goal],
        "days_since": trained["days"], "unlabelled": trained["unlabelled"],
        "labels": gym.GROUP_LABEL, "focus_options": gym.FOCUS_LABEL,
    }


FTP_HINT_MARGIN = 1.03  # only suggest a change worth making


def _ftp_hint(estimate: float | None, current: float) -> dict | None:
    """Suggest raising FTP when recent 20-minute power says it's clearly higher. Never suggests lowering
    it: not having done a hard 20 minutes lately isn't evidence of lost fitness."""
    if estimate is None or estimate < current * FTP_HINT_MARGIN:
        return None
    return {"estimate": round(estimate), "current": round(current)}


@router.get("/home")
def home(
    minutes: int = Query(60, ge=20, le=600), sport: str = "cycling", focus: str | None = None,
    session: Session = Depends(current_session), today: date = Depends(local_today),
) -> dict:
    db = session.db_path
    sport = _check_sport(sport)
    focus = _check_focus(focus)
    is_gym = sport == gym.KEY
    group = gym.GROUP_NAME if is_gym else sport.title()  # "cycling" -> the "Cycling" entry in SPORT_GROUPS
    c = _home_context(db, minutes, today, sport, focus)
    today, settings, series = c["today"], c["settings"], c["series"]
    suggestion, ftp = c["suggestion"], c["ftp"]
    profile = None if is_gym else SPORTS[sport]
    pace = settings.get(profile.pace_setting) if profile and profile.pace_setting else None

    week = week_progress(today, db, group)
    year_start = date(today.year, 1, 1)
    longest = longest_activity(year_start, today, SPORT_GROUPS[group], db)
    ramp = series[-1]["fitness"] - series[-8]["fitness"] if len(series) > 7 else None
    ftp_trend = estimated_ftp_trend(db) if sport == "cycling" else []
    ftp_hint = _ftp_hint(ftp_estimate(today, db_path=db), ftp) if sport == "cycling" else None
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
        "pace_unit": profile.pace_unit if profile else "",
        "settings": settings,
        "workout": _workout_out(suggestion, sport, {**settings, "ftp_w": ftp}),
        "plan_today": c["plan_today"],
        "plan_proposals": len(get_proposals("open", db)),
        "gym": _gym_block(suggestion, c["trained"]) if is_gym else None,
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
            "sessions": summary(year_start, today, group, db)["activity_count"],
        },
        "ftp_trend": ftp_trend[-18:],
        "ftp_hint": ftp_hint,
    }


class CoachBody(BaseModel):
    minutes: int = 60
    sport: str = "cycling"
    focus: str | None = None  # gym only: the session the athlete asked to train
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


def _coach_payload(db: str, today: date, minutes: int, sport: str, focus: str | None) -> dict:
    c = _home_context(db, minutes, today, _check_sport(sport), _check_focus(focus))
    s = c["settings"]
    return coach.build_payload(
        c["readiness"], c["suggestion"], c["wellness"], c["intensity"], c["form"], c["ftp"],
        goals=get_text_setting(GOALS_KEY, db),
        targets={"weekly_hours": s.get("weekly_hours_target"), "yearly_km": s.get("yearly_km_goal")},
        sport=sport, levels={k: v["level"] for k, v in c["levels"].items()},
        threshold_pace_s=s.get(SPORTS[sport].pace_setting) if sport in SPORTS and SPORTS[sport].pace_setting else None,
        pace_unit=SPORTS[sport].pace_unit if sport in SPORTS else "",
        # Always sent, so the coach can propose a gym workout when the athlete asks for one from another sport.
        gym={"goal_label": gym.GOAL_LABEL[gym.goal_from_setting(s.get("gym_goal"))], "days_since": c["trained"]["days"]},
        plan=_coach_plan_block(db, today),
    )


def _ask(db: str, today: date, payload: dict, body) -> dict:
    """Ask the coach; any plan_changes in the reply become a proposal the athlete can apply or dismiss."""
    try:
        advice = coach.ask_coach(payload, model=body.model, api_key=body.api_key, history=body.history)
    except coach.CoachError as exc:
        raise HTTPException(400, str(exc)) from exc
    out = advice.model_dump()
    out["plan_proposal"], out["plan_skipped"] = _coach_proposal(db, today, advice)
    return out


@router.post("/coach")
def ask_coach(
    body: CoachBody, session: Session = Depends(current_session), today: date = Depends(local_today),
) -> dict:
    db = session.db_path
    return _ask(db, today, _coach_payload(db, today, body.minutes, body.sport, body.focus), body)


# ------------------------------------------------------------------ training plan

PROFILE_KEY, PLAN_GOALS_KEY, WEEK_HOURS_PREFIX = "training_profile", "training_goals", "week_hours:"
PLAN_PAST_WEEKS = 1  # weeks before this one shown on the Plan page, to compare plan and reality
RATE_HISTORY_DAYS = 180


def _json_setting(db: str, key: str, default):
    raw = get_text_setting(key, db)
    try:
        return json.loads(raw) if raw else default
    except ValueError:
        return default


def _plan_setup(db: str) -> tuple[plan.Profile | None, list[plan.Goal]]:
    raw = _json_setting(db, PROFILE_KEY, None)
    profile = plan.profile_from_dict(raw) if isinstance(raw, dict) else None
    goals = [plan.goal_from_dict(g) for g in _json_setting(db, PLAN_GOALS_KEY, []) if isinstance(g, dict)]
    return profile, goals


def _week_overrides(db: str) -> dict[str, float]:
    out = {}
    for week, value in text_settings_with_prefix(WEEK_HOURS_PREFIX, db).items():
        try:
            out[week] = float(value)
        except ValueError:
            pass
    return out


def _actual_days(db: str, start: date, end: date) -> dict[str, dict]:
    """What was really done per day: minutes, load, minutes per plan sport, and the activities."""
    out: dict[str, dict] = {}
    for a in activity_loads(start, end, db):
        d = out.setdefault(a["day"], {"minutes": 0.0, "load": 0.0, "sports": {}, "activities": []})
        sport = plan.plan_sport(a["sport_type"]) or "other"
        d["minutes"] += a["minutes"]
        d["load"] += a["load"] or 0
        d["sports"][sport] = d["sports"].get(sport, 0) + a["minutes"]
        d["activities"].append({
            "activity_id": a["activity_id"], "name": a["activity_name"], "sport_type": a["sport_type"], "sport": sport,
            "minutes": round(a["minutes"]), "km": round((a["distance_m"] or 0) / 1000, 1), "load": round(a["load"] or 0),
        })
    return out


def _plan_inputs(db: str, today: date) -> dict:
    settings = all_settings(db)
    load = {date.fromisoformat(r["day"]): r["load"] for r in daily_training_load(db)}
    series = fitness_series(load, end=today)
    recent = [p["fitness"] for p in series if p["day"] >= today - timedelta(days=90)]
    levels = {k: v["level"] for k, v in _levels(db, settings, today, max(recent) if recent else None).items()}
    history = activity_loads(today - timedelta(days=RATE_HISTORY_DAYS), today, db)
    four_weeks = (today - timedelta(days=28)).isoformat()
    recent_minutes = sum(a["minutes"] for a in history if a["day"] >= four_weeks and a["day"] < today.isoformat()
                         and plan.plan_sport(a["sport_type"]))
    # Fitness at the start of today (the row for today already includes anything done today).
    ctl = series[-2]["fitness"] if len(series) > 1 else (series[-1]["fitness"] if series else 0.0)
    return {
        "settings": settings, "series": series, "levels": levels, "rates": plan.load_rates(history), "ctl": ctl,
        "recent_hours": recent_minutes / 60 / 4, "gym_goal": gym.goal_from_setting(settings.get("gym_goal")),
    }


def _plan_rows(db: str, start: date | str, end: date | str) -> list[plan.PlanDay]:
    s, e = (x.isoformat() if isinstance(x, date) else x for x in (start, end))
    return [plan.PlanDay(**r) for r in get_plan_days(s, e, db)]


FAR = "9999-12-31"


def _build(db: str, today: date, only_empty_weeks: bool, profile=None, goals=None, inputs=None):
    """Week targets and the new sessions build_plan makes for this user, saved to the database."""
    if profile is None:
        profile, goals = _plan_setup(db)
    if profile is None:
        raise HTTPException(400, "Set up your plan first.")
    inp = inputs or _plan_inputs(db, today)
    week0 = plan.monday(today)
    existing = _plan_rows(db, week0, FAR)
    done = _actual_days(db, week0, today - timedelta(days=1))
    weeks, new = plan.build_plan(
        today, inp["ctl"], profile, goals, inp["levels"], inp["rates"], inp["recent_hours"], _week_overrides(db),
        existing, done, inp["gym_goal"], only_empty_weeks=only_empty_weeks,
    )
    rows = [asdict(p) for p in new]
    if only_empty_weeks:
        if rows:
            save_plan_days(rows, db)
    else:
        save_plan_days(rows, db, replace_auto_from=today.isoformat())
    return weeks, inp


def _readiness_levels(db: str, today: date, settings: dict, series: list[dict]) -> dict[str, str]:
    """Readiness on today and the two days before, only for days that have their own recovery data."""
    wellness = daily_wellness(today - timedelta(days=90), today, db)
    intensity = daily_intensity(today - timedelta(days=33), today, db)
    form = {p["day"]: p["form"] for p in series}
    out = {}
    for back in range(plan.STREAK_DAYS):
        d = today - timedelta(days=back)
        iso = d.isoformat()
        r = assess([w for w in wellness if w["day"] <= iso], [x for x in intensity if x["day"] <= iso],
                   form.get(d), settings, d)
        if r.has_today_data:
            out[iso] = r.level
    return out


def _refresh_proposals(db: str, today: date, inp: dict, actual: dict[str, dict]) -> None:
    """Expire proposals the plan has moved past, then store any new ones the rules come up with."""
    rows = _plan_rows(db, today - timedelta(days=14), FAR)
    current = {(p.day, p.slot): p for p in rows}
    for p in get_proposals("open", db):
        if p["source"] == "coach" and p["changes"] and all(c["day"] < today.isoformat() for c in p["changes"]):
            set_proposal_status(p["id"], "expired", db)
        elif p["source"] != "coach" and (
            all(c["day"] < today.isoformat() for c in p["changes"])
            or any(plan.is_stale(c, current.get((c["day"], c["slot"]))) for c in p["changes"])
        ):
            set_proposal_status(p["id"], "expired", db)
    levels = _readiness_levels(db, today, inp["settings"], inp["series"])
    for prop in plan.reconcile(rows, actual, today, inp["rates"], inp["levels"], levels):
        add_proposal(prop.key, prop.kind, prop.reason, prop.changes, "rules", db)


def _session_out(p: plan.PlanDay) -> dict:
    return {**asdict(p), "label": plan.describe(p.sport, p.kind, p.minutes)}


def _change_out(c: dict) -> dict:
    label = lambda s: plan.describe(s["sport"], s["kind"], s["minutes"]) if s else "Nothing"  # noqa: E731
    return {**c, "before_label": label(c.get("before")), "after_label": label(c.get("after"))}


def _proposals_out(db: str) -> list[dict]:
    return [{**p, "changes": [_change_out(c) for c in p["changes"]]} for p in get_proposals("open", db)]


def _plan_view(db: str, today: date, weeks: list[plan.WeekTarget], actual: dict[str, dict], past_weeks: int) -> dict:
    week0 = plan.monday(today)
    first = week0 - timedelta(weeks=past_weeks)
    rows = _plan_rows(db, first, FAR)
    overrides = _week_overrides(db)
    targets = {w.week_start: w for w in weeks}
    starts = [first + timedelta(weeks=i) for i in range(past_weeks)] + [date.fromisoformat(w.week_start) for w in weeks]
    out_weeks = []
    for ws in starts:
        we = ws + timedelta(days=6)
        week_rows = [p for p in rows if ws.isoformat() <= p.day <= we.isoformat()]
        target = targets.get(ws.isoformat())
        summary_ = plan.week_summary(ws, week_rows, actual, today)
        days = []
        if target is None or target.detailed:
            for i in range(7):
                d = (ws + timedelta(days=i)).isoformat()
                a = actual.get(d) or {}
                days.append({
                    "day": d, "status": summary_["status"][d],
                    "sessions": [_session_out(p) for p in week_rows if p.day == d],
                    "activities": a.get("activities", []),
                })
        out_weeks.append({
            "week_start": ws.isoformat(), "past": we < today,
            "phase": target.phase if target else None, "note": target.note if target else "",
            "target_hours": target.hours if target else None, "sessions": target.sessions if target else None,
            "load_cap": target.load_cap if target else None,
            "projected_load": target.planned_load if target else None,
            "detailed": target.detailed if target else True,
            "override_hours": overrides.get(ws.isoformat()),
            **{k: v for k, v in summary_.items() if k != "status"},
            "days": days,
        })
    return {"weeks": out_weeks, "proposals": _proposals_out(db)}


def _setup_out(db: str, today: date) -> dict:
    profile, goals = _plan_setup(db)
    suggested = None
    if profile is None:
        hours = {s: 0.0 for s in plan.PLAN_SPORTS}
        for a in activity_loads(today - timedelta(days=84), today - timedelta(days=1), db):
            sport = plan.plan_sport(a["sport_type"])
            if sport:
                hours[sport] += a["minutes"] / 60 / 12
        suggested = asdict(plan.default_profile(hours, today))
    return {
        "configured": profile is not None,
        "profile": asdict(profile) if profile else suggested,
        "goals": [asdict(g) for g in goals],
        "options": {
            "sports": list(plan.PLAN_SPORTS), "goal_kinds": list(plan.GOAL_KINDS), "weekdays": list(plan.WEEKDAYS),
            "kinds": plan.KINDS_BY_SPORT, "kind_labels": plan.KIND_LABEL, "max_goals": plan.MAX_GOALS,
        },
    }


@router.get("/plan")
def get_plan(
    past_weeks: int = Query(PLAN_PAST_WEEKS, ge=0, le=12),
    session: Session = Depends(current_session), today: date = Depends(local_today),
) -> dict:
    """The plan from `past_weeks` before this week up to the goal, with what was really done and open proposals.
    Weeks without sessions inside the 4-week horizon are filled in (the plan keeps rolling forward)."""
    db = session.db_path
    setup = _setup_out(db, today)
    if not setup["configured"]:
        return {"setup": setup, "weeks": [], "proposals": [], "today": today}
    weeks, inp = _build(db, today, only_empty_weeks=True)
    actual = _actual_days(db, plan.monday(today) - timedelta(weeks=max(past_weeks, 2)), today)
    _refresh_proposals(db, today, inp, actual)
    return {"setup": setup, "today": today, **_plan_view(db, today, weeks, actual, past_weeks)}


class ProfileBody(BaseModel):
    sports: dict[str, float]
    hours_per_week: float
    sessions_per_week: int | None = None
    long_day: int = 5


class GoalBody(BaseModel):
    kind: str
    sport: str | None = None
    date: str | None = None
    target: float | None = None
    name: str = Field(default="", max_length=200)


class SetupBody(BaseModel):
    profile: ProfileBody
    goals: list[GoalBody] = Field(default_factory=list, max_length=10)


@router.put("/plan/setup")
def put_plan_setup(body: SetupBody, session: Session = Depends(current_session), today: date = Depends(local_today)) -> dict:
    """Save the athlete's sports, weekly time and goals, and (re)build the automatic part of the plan."""
    db = session.db_path
    old, _ = _plan_setup(db)
    profile = plan.Profile(**body.profile.model_dump(), start=old.start if old and old.start else today.isoformat())
    goals = [plan.goal_from_dict(g.model_dump()) for g in body.goals]
    errors = plan.setup_errors(profile, goals, today)
    if errors:
        raise HTTPException(400, " ".join(errors))
    set_text_setting(PROFILE_KEY, json.dumps(asdict(profile)), db)
    set_text_setting(PLAN_GOALS_KEY, json.dumps([asdict(g) for g in goals]), db)
    _build(db, today, only_empty_weeks=False, profile=profile, goals=goals)
    return _setup_out(db, today)


@router.delete("/plan")
def delete_plan(session: Session = Depends(current_session)) -> dict:
    """Start over: forget the plan, its proposals, the setup and the per-week hours."""
    db = session.db_path
    clear_plan(db)
    for key in (PROFILE_KEY, PLAN_GOALS_KEY, *(WEEK_HOURS_PREFIX + w for w in _week_overrides(db))):
        delete_setting(key, db)
    return {"ok": True}


@router.post("/plan/generate")
def generate_plan(session: Session = Depends(current_session), today: date = Depends(local_today)) -> dict:
    """Rebuild every automatic session from today on. Edited, locked, coach and applied sessions stay."""
    _build(session.db_path, today, only_empty_weeks=False)
    return {"ok": True}


class WeekHoursBody(BaseModel):
    hours: float | None = Field(default=None, ge=0, le=plan.HOURS_LIMITS[1])


@router.put("/plan/week/{week_start}")
def put_week_hours(
    week_start: date, body: WeekHoursBody, session: Session = Depends(current_session), today: date = Depends(local_today),
) -> dict:
    """Hours available in one week (None = back to the usual). The week's automatic sessions are resized."""
    if week_start.weekday() != 0:
        raise HTTPException(400, "A week starts on a Monday.")
    if week_start < plan.monday(today):
        raise HTTPException(400, "That week is over.")
    db = session.db_path
    key = WEEK_HOURS_PREFIX + week_start.isoformat()
    if body.hours is None:
        delete_setting(key, db)
    else:
        set_text_setting(key, str(body.hours), db)
    _build(db, today, only_empty_weeks=False)
    return {"week_start": week_start, "hours": body.hours}


class DayBody(BaseModel):
    sport: str | None = None
    kind: str | None = None
    minutes: int | None = Field(default=None, ge=10, le=600)
    note: str | None = Field(default=None, max_length=300)
    locked: bool | None = None


def _rates(db: str, today: date) -> dict:
    return plan.load_rates(activity_loads(today - timedelta(days=RATE_HISTORY_DAYS), today, db))


def _rest_row(day: str) -> dict:
    return asdict(plan.PlanDay(day, 0, "rest", "rest", 0, 0.0, "user"))


def _free_slot(db: str, day: str) -> int:
    rows = {p.slot: p for p in _plan_rows(db, day, day)}
    if 0 not in rows or rows[0].kind == "rest":
        return 0
    if 1 not in rows:
        return 1
    raise HTTPException(409, "That day already has two sessions.")


def _warnings(db: str, day: str) -> list[str]:
    d = date.fromisoformat(day)
    return plan.spacing_warnings(_plan_rows(db, d - timedelta(days=1), d + timedelta(days=1)), day)


@router.post("/plan/day/{day}")
def add_plan_day(day: date, body: DayBody, session: Session = Depends(current_session), today: date = Depends(local_today)) -> dict:
    db = session.db_path
    if day < today:
        raise HTTPException(400, "That day is over.")
    if not (body.sport and body.kind and body.minutes):
        raise HTTPException(400, "Choose a sport, a session and the minutes.")
    problem = plan.valid_session(body.sport, body.kind, body.minutes)
    if problem:
        raise HTTPException(400, problem)
    slot = _free_slot(db, day.isoformat())
    row = plan.PlanDay(day.isoformat(), slot, body.sport, body.kind, body.minutes,
                       plan.planned_load(body.sport, body.kind, body.minutes, _rates(db, today)), "user",
                       bool(body.locked), body.note or "")
    save_plan_days([asdict(row)], db)
    return {"session": _session_out(row), "warnings": _warnings(db, row.day)}


def _get_row(db: str, day: date, slot: int) -> plan.PlanDay:
    rows = [p for p in _plan_rows(db, day, day) if p.slot == slot]
    if not rows:
        raise HTTPException(404, "No session there.")
    return rows[0]


@router.patch("/plan/day/{day}/{slot}")
def edit_plan_day(
    day: date, slot: int, body: DayBody, session: Session = Depends(current_session), today: date = Depends(local_today),
) -> dict:
    """Edit a session. Changing what it is marks it as the athlete's own, so regenerating keeps it."""
    db = session.db_path
    row = _get_row(db, day, slot)
    if day < today:
        raise HTTPException(400, "That day is over.")
    changes = body.model_dump(exclude_none=True)
    new = replace(row, **changes)
    if {"sport", "kind", "minutes"} & changes.keys():
        if new.sport == "rest":
            raise HTTPException(400, "Pick a sport.")
        if "sport" in changes and "kind" not in changes and new.kind not in plan.KINDS_BY_SPORT.get(new.sport, []):
            new = replace(new, kind=plan.KINDS_BY_SPORT[new.sport][1] if new.sport in plan.ENDURANCE else "full")
        problem = plan.valid_session(new.sport, new.kind, new.minutes)
        if problem:
            raise HTTPException(400, problem)
        new = replace(new, source="user", planned_load=plan.planned_load(new.sport, new.kind, new.minutes, _rates(db, today)))
    save_plan_days([asdict(new)], db)
    return {"session": _session_out(new), "warnings": _warnings(db, new.day)}


@router.delete("/plan/day/{day}/{slot}")
def delete_plan_day_route(
    day: date, slot: int, session: Session = Depends(current_session), today: date = Depends(local_today),
) -> dict:
    """Remove a session. The day's main slot becomes a kept rest day, so regenerating doesn't refill it;
    removing that rest day hands the day back to the planner."""
    db = session.db_path
    row = _get_row(db, day, slot)
    if day < today:
        raise HTTPException(400, "That day is over.")
    if row.kind == "rest" or slot != 0:
        delete_plan_day(day.isoformat(), slot, db)
    else:
        save_plan_days([_rest_row(day.isoformat())], db)
    return {"ok": True}


class MoveBody(BaseModel):
    to: date


@router.post("/plan/day/{day}/{slot}/move")
def move_plan_day(
    day: date, slot: int, body: MoveBody, session: Session = Depends(current_session), today: date = Depends(local_today),
) -> dict:
    db = session.db_path
    row = _get_row(db, day, slot)
    if row.kind == "rest":
        raise HTTPException(400, "Rest days can't be moved; add a session instead.")
    if day < today or body.to < today:
        raise HTTPException(400, "Sessions can only move between today and later days.")
    if body.to == day:
        return {"session": _session_out(row), "warnings": []}
    to_slot = _free_slot(db, body.to.isoformat())
    moved = replace(row, day=body.to.isoformat(), slot=to_slot, source="user")
    delete_plan_day(day.isoformat(), slot, db)
    if slot == 0:
        save_plan_days([_rest_row(day.isoformat())], db)  # the day it left stays free
    save_plan_days([asdict(moved)], db)
    return {"session": _session_out(moved), "warnings": _warnings(db, moved.day)}


@router.post("/plan/proposals/{pid}/apply")
def apply_proposal(pid: int, session: Session = Depends(current_session), today: date = Depends(local_today)) -> dict:
    db = session.db_path
    prop = get_proposal(pid, db)
    if prop is None:
        raise HTTPException(404, "No such proposal.")
    if prop["status"] != "open":
        raise HTTPException(409, f"This proposal is already {prop['status']}.")
    current = {(p.day, p.slot): p for p in _plan_rows(db, today - timedelta(days=7), FAR)}
    if any(plan.is_stale(c, current.get((c["day"], c["slot"]))) or c["day"] < today.isoformat() for c in prop["changes"]):
        set_proposal_status(pid, "stale", db)
        raise HTTPException(409, "The plan has changed since this was proposed, so it no longer applies.")
    source = "coach" if prop["source"] == "coach" else "adjusted"
    for c in prop["changes"]:
        after = c.get("after")
        if after is None:
            if c["slot"] == 0:
                save_plan_days([{**_rest_row(c["day"]), "source": source}], db)
            else:
                delete_plan_day(c["day"], c["slot"], db)
        else:
            old = current.get((c["day"], c["slot"]))
            save_plan_days([asdict(plan.PlanDay(
                c["day"], c["slot"], after["sport"], after["kind"], after["minutes"], after.get("planned_load", 0.0),
                source, False, after.get("note") or (old.note if old else ""),
            ))], db)
    set_proposal_status(pid, "applied", db)
    return {"ok": True}


@router.post("/plan/proposals/{pid}/dismiss")
def dismiss_proposal(pid: int, session: Session = Depends(current_session)) -> dict:
    db = session.db_path
    if get_proposal(pid, db) is None:
        raise HTTPException(404, "No such proposal.")
    set_proposal_status(pid, "dismissed", db)
    return {"ok": True}


@router.get("/plan/day/{day}/{slot}/workout")
def plan_day_workout(
    day: date, slot: int, session: Session = Depends(current_session), today: date = Depends(local_today),
) -> dict:
    """The steps or exercises of a planned session, as planned (today's readiness adjustment is on Home)."""
    db = session.db_path
    row = _get_row(db, day, slot)
    if row.kind == "rest":
        return {"workout": None}
    inp = _plan_inputs(db, today)
    neutral = Readiness("green", 100, [], "As planned.")
    tp = {"sport": row.sport, "kind": row.kind, "minutes": row.minutes,
          "planned": {"label": plan.describe(row.sport, row.kind, row.minutes)}, "reason": None}
    trained = gym.last_trained(strength_sessions(today - timedelta(days=60), today, db), today)
    s = plan.plan_suggestion(tp, neutral, [], trained, day, inp["levels"].get(row.sport, "intermediate"), inp["gym_goal"])
    return {"workout": _workout_out(s, row.sport, inp["settings"]), "ftp": inp["settings"].get("ftp_w") or DEFAULT_FTP}


# --- the AI coach and the plan

REVIEW_PROMPT = ("Review my training plan for this week and next against my goals, my recovery and what I've "
                 "actually done. Propose plan_changes only where they clearly help.")


def _coach_plan_block(db: str, today: date) -> dict | None:
    """The plan as the coach sees it (no goal names or activity names), or None without a plan."""
    profile, goals = _plan_setup(db)
    if profile is None:
        return None
    weeks, _ = _build(db, today, only_empty_weeks=True)
    week0 = plan.monday(today)
    start = week0 - timedelta(weeks=plan.COACH_COMPLIANCE_WEEKS)
    end = week0 + timedelta(weeks=plan.COACH_WEEKS) - timedelta(days=1)
    return plan.coach_summary(profile, goals, weeks, _plan_rows(db, start, end), _actual_days(db, start, today),
                              today, get_proposals("open", db))


def _coach_proposal(db: str, today: date, advice: coach.CoachAdvice) -> tuple[dict | None, list[str]]:
    if not advice.plan_changes:
        return None, []
    if _plan_setup(db)[0] is None:
        return None, ["There is no training plan yet, so the coach's plan changes were not kept."]
    rows = _plan_rows(db, today, today + timedelta(weeks=plan.COACH_WEEKS + 1))
    rates = plan.load_rates(activity_loads(today - timedelta(days=RATE_HISTORY_DAYS), today, db))
    changes, skipped = plan.coach_changes([c.model_dump() for c in advice.plan_changes], rows, today, rates)
    if not changes:
        return None, skipped
    reason = advice.summary.strip()[:600] or "The coach suggests these changes."
    pid = add_proposal(f"coach:{datetime.now(UTC).isoformat()}", "coach", reason, changes, "coach", db)
    prop = get_proposal(pid, db) if pid is not None else None
    return ({**prop, "changes": [_change_out(c) for c in prop["changes"]]} if prop else None), skipped


class ReviewBody(BaseModel):
    model: str | None = None
    api_key: str | None = None
    history: list[coach.ChatMessage] = Field(default_factory=list, max_length=20)


@router.post("/plan/review")
def review_plan(body: ReviewBody, session: Session = Depends(current_session), today: date = Depends(local_today)) -> dict:
    """Ask the AI coach to review the next two weeks. Its changes come back as one proposal to apply or dismiss."""
    db = session.db_path
    profile = _plan_setup(db)[0]
    if profile is None:
        raise HTTPException(400, "Set up your plan first.")
    # Today's planned session sets the sport and length of the one-day suggestion that comes with the summary.
    tp = plan.today_from_plan(_plan_rows(db, today, today), Readiness("green", 100, [], ""), [], today)
    shares = profile.shares()
    sport = tp["sport"] if tp and tp["sport"] in plan.PLAN_SPORTS else max(shares, key=shares.get)
    minutes = tp["minutes"] if tp and tp["kind"] != "rest" else 60
    history = body.history or [coach.ChatMessage(role="user", content=REVIEW_PROMPT)]
    payload = _coach_payload(db, today, min(600, max(20, minutes)), sport, None)
    return _ask(db, today, payload, body.model_copy(update={"history": history}))


# ------------------------------------------------------------------ send to watch


class PushStep(BaseModel):
    minutes: float = Field(gt=0, le=300)
    frac_low: float = Field(ge=0, le=2)
    frac_high: float = Field(ge=0, le=2)
    label: str = Field(default="", max_length=120)


class PushExercise(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    group: str = Field(default="", max_length=20)
    sets: int = Field(ge=1, le=10)
    reps: str = Field(max_length=40)
    rpe: float | None = Field(default=None, ge=0, le=10)
    rest_s: float = Field(default=60, ge=0, le=600)
    note: str = Field(default="", max_length=300)


class PushBody(BaseModel):
    sport: str
    name: str = Field(min_length=1, max_length=120)
    steps: list[PushStep] = Field(default=[], max_length=60)
    exercises: list[PushExercise] = Field(default=[], max_length=20)


def _workout_id(response) -> int | None:
    return response.get("workoutId") if isinstance(response, dict) else None


@router.post("/workout/push")
def push_workout(
    body: PushBody, session: Session = Depends(current_session), today: date = Depends(local_today),
) -> dict:
    """Upload the workout shown on Home to Garmin Connect and schedule it for today. The client sends the
    steps (so an accepted coach workout works too); targets come from this user's own FTP and pace settings.
    Pushing again the same day replaces the earlier copy, so the Garmin library doesn't fill up."""
    if body.sport not in garmin_workout.PUSHABLE_SPORTS:
        raise HTTPException(400, "This workout can't be sent to the watch.")
    db = session.db_path
    settings = all_settings(db)
    if body.sport == "gym":
        if not body.exercises:
            raise HTTPException(400, "The session has no exercises.")
        exercises = [garmin_workout.PlannedExercise(**e.model_dump()) for e in body.exercises]
        # Same warm-up the suggestion counts in its minutes; a mobility session is its own warm-up.
        warmup = 0 if all(e.group == "mobility" for e in exercises) else gym.WARMUP_MIN
        payload = garmin_workout.to_garmin_strength(body.name, exercises, warmup)
    else:
        if not body.steps:
            raise HTTPException(400, "The workout has no steps.")
        steps = [garmin_workout.PlannedStep(s.minutes, min(s.frac_low, s.frac_high), max(s.frac_low, s.frac_high), s.label)
                 for s in body.steps]
        pace = settings.get(SPORTS[body.sport].pace_setting) if SPORTS[body.sport].pace_setting else None
        payload = garmin_workout.to_garmin(body.name, body.sport, steps, settings["ftp_w"], pace,
                                           pool_length_m=settings.get("pool_length_m") or 25)
    key = f"pushed_workout:{today.isoformat()}:{body.sport}"
    previous = get_text_setting(key, db)
    try:
        if previous:
            try:
                session.api.delete_workout(int(previous))
            except Exception:
                pass  # already deleted in Garmin Connect: nothing to replace
        workout_id = _workout_id(session.api.upload_workout(payload))
        if workout_id is None:
            raise RuntimeError("Garmin didn't return a workout id.")
        set_text_setting(key, str(workout_id), db)
        session.api.schedule_workout(workout_id, today.isoformat())
    except Exception as exc:  # garminconnect raises several error types
        raise HTTPException(502, f"Garmin Connect didn't accept the workout: {exc}") from exc
    return {"workout_id": workout_id, "date": today.isoformat(), "replaced": bool(previous)}


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
