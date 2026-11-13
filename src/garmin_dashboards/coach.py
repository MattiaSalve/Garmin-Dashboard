"""Optional AI coach: ask an LLM (via OpenRouter) to explain today's readiness and refine the suggested workout.

Only a compact numeric summary is sent (no names, places, GPS, email or raw Garmin JSON).
The rules engine stays the source of truth; the LLM explains it and may propose tweaks.
"""

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Literal

import re

from pydantic import BaseModel, Field, ValidationError, field_validator

from garmin_dashboards.mode import is_local
from garmin_dashboards.readiness import Readiness
from garmin_dashboards.gym import GymSuggestion
from garmin_dashboards.workouts import Suggestion

log = logging.getLogger(__name__)

URL = "https://openrouter.ai/api/v1/chat/completions"
# Models offered in the menu (OpenRouter ids; the UI also accepts any other id). Edit freely.
MODELS = [
    "z-ai/glm-5.3-flash",
    "anthropic/claude-opus-5-5",
    "anthropic/claude-sonnet-5-5",
    "anthropic/claude-haiku-5-5",
    "google/gemini-2.5-pro",
    "deepseek/deepseek-chat",
    "meta-llama/llama-3.3-70b-instruct",
]
# The OPENROUTER_MODEL environment variable sets which model the menu starts on.
DEFAULT_MODEL = os.environ.get("OPENROUTER_MODEL") or MODELS[0]
TIMEOUT_S = 60
GOALS_MAX = 1000  # characters of free text the athlete can attach

SYSTEM = """You are an experienced, conservative endurance coach (cycling, running and swimming).
You receive a JSON summary of an athlete's recent recovery data (HRV, resting heart rate, sleep),
training load across all sports, a rules-based readiness verdict and the workout those rules picked
for today in "sport_today".

- "athlete_levels" gives the athlete's level in each sport. Fit the workout to the level in the sport
  being trained today: a strong cyclist can still be a beginner runner who needs gentler running, with
  fewer intervals and more walk breaks. Aerobic fitness from one sport carries over only partly to another, and running adds impact
  stress that cycling and swimming do not.

- "athlete_goals" is the athlete's own description of their long-term goals (e.g. an event, a distance,
  a sustained speed) and "targets" holds their weekly hours and yearly km targets. Shape the plan so it
  builds towards them: choose session types and durations that serve the goal, and say in one sentence
  how today's ride fits. Recovery signals still win over goals on a day when they conflict.
- Explain the verdict in plain language, citing only numbers present in the data.
- Say whether you agree with the suggested workout. Only propose adjusted steps if you have a
  clear reason; keep the same total duration and express targets as % of FTP for cycling, or % of threshold speed for running and swimming
  (the pct_ftp fields hold that percentage).
- When signals conflict, lean towards recovery.
- A rest day is a valid answer. If the suggested workout is "Rest day", or the data show the athlete is
  overworked (poor recovery plus heavy recent load), say so plainly and agree or recommend rest, with
  adjusted_steps as an empty list. Never invent a ride to fill the day.
- Do not make medical claims or diagnoses. If something looks unusual (e.g. a sharp resting HR jump),
  suggest the athlete pays attention to how they feel and seeks professional advice if it persists.
- Be brief: the summary is 2-4 sentences.
- Gym sessions: when "sport_today" is "gym" the suggested workout lists exercises (sets, reps, rpe 1-10, rest in
  seconds, muscle group). "gym.days_since_trained" says how many days ago each muscle group was last trained
  (null = nothing in the recent data) and "gym.goal_label" is why the athlete lifts: for "Support my endurance
  training" protect their rides and runs (no heavy leg work within 48 h of hard training or before a key session,
  stop sets short of failure, favour legs, hips and core). Propose changes with the complete list in
  adjusted_exercises and leave adjusted_steps null. In every other sport leave adjusted_exercises null.
- The workout you propose can be in a different sport from "sport_today" when the athlete's message points
  that way (e.g. they ask for a bodyweight or home workout, or a run instead of a ride). Set "workout_sport" to
  "cycling", "running", "swimming" or "gym" for the sport of the workout in adjusted_steps / adjusted_exercises.
  Leave it null when the workout is for sport_today. A bodyweight, home, "no gym" or strength request is a
  "gym" workout built from adjusted_exercises using bodyweight or minimal-equipment exercises. Use the "gym"
  block (goal and days since each muscle group was trained) and the levels to fit it.
- The athlete may follow up with a message, e.g. to change today's workout (shorter, different focus, a hill
  they have to ride). Answer it directly, keep the earlier context, and when the workout changes return
  the complete updated adjusted_steps (not just the changed part). The athlete's request overrides
  "keep the same total duration", but still push back in the summary if a change looks unwise for their recovery.
- "training_plan" (when present) is the athlete's multi-week plan, built by rules from their goals, sports and
  weekly time. "weeks" lists this week and next day by day (planned sessions, status, what was done),
  "last_weeks_planned_vs_done" shows how well they followed it, and "open_proposals" are changes the rules have
  already suggested. Only when the athlete asks you to review or change the plan (e.g. "I can't train on
  Thursday", "make next week lighter", "review my plan"), return plan_changes: each one sets a day's session
  (day as YYYY-MM-DD, slot 0 for the main session or 1 for a second one, sport, kind, minutes, and a one-line
  note saying why). Use kind "rest" to clear a session. Otherwise leave plan_changes null.
- Plan changes: use only kinds listed in "kinds_by_sport" and the athlete's chosen sports; stay within their
  hours_per_week and sessions_per_week; keep hard sessions (tempo, sweet_spot, fartlek, threshold, vo2max) on
  non-consecutive days and not the day after the long session; never change locked sessions or days before
  today; and when recovery is poor or they recently did much more than planned, make the coming days easier,
  not harder. Never add sessions to catch up on missed ones. Fewer, well-reasoned changes beat many. The athlete
  approves each change set before it is applied, so explain it in the summary."""


class CoachStep(BaseModel):
    minutes: float
    pct_ftp_low: int
    pct_ftp_high: int
    label: str = "Step"

    @field_validator("minutes", mode="before")
    @classmethod
    def _minutes(cls, v):
        return float(_first_number(v, 1))

    @field_validator("pct_ftp_low", "pct_ftp_high", mode="before")
    @classmethod
    def _pct(cls, v):  # 72.5 or "72%" are fine; the UI shows whole percentages
        return _first_number(v, 0)

    @field_validator("label", mode="before")
    @classmethod
    def _label(cls, v):
        return "Step" if v is None else str(v)


def _first_number(value, default: int) -> int:
    """Models write numbers as 7, 7.5, "7", "7-8" or "90 s": take the first one."""
    if isinstance(value, bool) or value is None:
        return default
    if isinstance(value, (int, float)):
        return round(value)
    match = re.search(r"\d+(?:\.\d+)?", str(value))
    return round(float(match.group())) if match else default


_GROUP_WORDS = (
    ("mobility", ("mobil", "stretch", "warm")),
    ("core", ("core", "abs", "trunk")),
    ("push", ("push", "chest", "shoulder", "tricep")),
    ("pull", ("pull", "back", "bicep", "lat")),
    ("legs", ("leg", "quad", "glute", "ham", "calf", "lower")),
)


class CoachExercise(BaseModel):
    """One gym exercise. Parsing is forgiving about how a model words numbers and muscle groups."""
    name: str
    group: Literal["legs", "push", "pull", "core", "mobility"] = "legs"
    sets: int = 3
    reps: str = ""
    rpe: int = 7
    rest_s: int = 60
    note: str = ""

    @field_validator("group", mode="before")
    @classmethod
    def _group(cls, v):
        text = str(v or "").lower()
        return next((g for g, words in _GROUP_WORDS if any(w in text for w in words)), "legs")

    @field_validator("sets", mode="before")
    @classmethod
    def _sets(cls, v):
        return min(10, max(1, _first_number(v, 3)))

    @field_validator("rpe", mode="before")
    @classmethod
    def _rpe(cls, v):
        return min(10, max(1, _first_number(v, 7)))

    @field_validator("rest_s", mode="before")
    @classmethod
    def _rest(cls, v):
        return min(600, max(0, _first_number(v, 60)))

    @field_validator("reps", mode="before")
    @classmethod
    def _reps(cls, v):
        return "" if v is None else str(v)

    @field_validator("note", mode="before")
    @classmethod
    def _note(cls, v):
        return "" if v is None else str(v)


_SPORT_WORDS = (
    ("gym", ("gym", "strength", "weight", "bodyweight", "body weight", "resistance")),
    ("running", ("run",)),
    ("swimming", ("swim",)),
    ("cycling", ("cycl", "bike", "ride")),
)


class CoachPlanChange(BaseModel):
    """One day of the plan as the coach wants it. kind "rest" clears the slot."""
    day: str
    slot: int = 0
    sport: str = "rest"
    kind: str = "rest"
    minutes: int = 0
    note: str = ""

    @field_validator("slot", mode="before")
    @classmethod
    def _slot(cls, v):
        return min(1, max(0, _first_number(v, 0)))

    @field_validator("minutes", mode="before")
    @classmethod
    def _minutes(cls, v):
        return _first_number(v, 0)

    @field_validator("sport", mode="before")
    @classmethod
    def _sport(cls, v):
        text = str(v or "").lower()
        if text in ("", "rest", "none", "off"):
            return "rest"
        return next((s for s, words in _SPORT_WORDS if any(w in text for w in words)), text)

    @field_validator("kind", mode="before")
    @classmethod
    def _kind(cls, v):  # "Sweet spot" -> "sweet_spot"
        text = re.sub(r"[^a-z0-9]+", "_", str(v or "rest").lower()).strip("_")
        return {"vo2_max": "vo2max", "off": "rest", "rest_day": "rest", "long_ride": "long", "long_run": "long",
                "long_swim": "long", "lower_body": "lower", "upper_body": "upper", "full_body": "full"}.get(text, text)

    @field_validator("note", "day", mode="before")
    @classmethod
    def _text(cls, v):
        return "" if v is None else str(v)


class CoachAdvice(BaseModel):
    summary: str
    agree_with_suggestion: bool
    # The sport of the proposed workout when it isn't the one the athlete selected (null = the selected sport).
    workout_sport: Literal["cycling", "running", "swimming", "gym"] | None = None
    adjusted_steps: list[CoachStep] | None = None
    cautions: list[str] = Field(default_factory=list)
    adjusted_exercises: list[CoachExercise] | None = None  # gym sessions only
    plan_changes: list[CoachPlanChange] | None = None  # edits to the training plan, proposed to the athlete

    @field_validator("cautions", mode="before")
    @classmethod
    def _cautions(cls, v):  # null or a single sentence instead of a list
        return [] if v is None else [v] if isinstance(v, str) else v

    @field_validator("workout_sport", mode="before")
    @classmethod
    def _workout_sport(cls, v):
        text = str(v or "").lower()
        return next((s for s, words in _SPORT_WORDS if any(w in text for w in words)), None)


class ChatMessage(BaseModel):
    """One earlier turn of the conversation (assistant turns hold the JSON advice as text)."""
    role: Literal["user", "assistant"]
    content: str = Field(max_length=8000)


class CoachError(RuntimeError):
    pass


def resolve_key(api_key: str | None = None) -> str | None:
    """The user's own key; in local mode the OPENROUTER_API_KEY environment variable is the fallback."""
    key = (api_key or "").strip()
    if not key and is_local():
        key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    return key or None


def is_available(api_key: str | None = None) -> bool:
    return resolve_key(api_key) is not None


def build_payload(
    readiness: Readiness,
    suggestion: Suggestion | GymSuggestion,
    wellness: list[dict],
    intensity: list[dict],
    form: float | None,
    ftp: float,
    goals: str = "",
    targets: dict | None = None,
    sport: str = "cycling",
    levels: dict | None = None,
    threshold_pace_s: float | None = None,
    pace_unit: str = "",
    gym: dict | None = None,
    plan: dict | None = None,
) -> dict:
    """Compact, identifier-free summary of the last 7 days plus today's verdict."""
    keep = ("day", "hrv_avg", "rhr", "sleep_score", "sleep_s", "bb_high")
    return {
        "athlete_goals": goals.strip()[:GOALS_MAX] or None,
        "targets": {k: v for k, v in (targets or {}).items() if v is not None},
        "sport_today": sport,
        # Level in each sport, e.g. a strong cyclist who is a beginner runner.
        "athlete_levels": levels or {},
        "ftp_w": ftp,
        "threshold_pace_s": threshold_pace_s, "threshold_pace_unit": pace_unit or None,
        "hrv_range_ms": list(readiness.hrv_range) if readiness.hrv_range else None,
        "form_tsb": None if form is None else round(form, 1),
        "recovery_last_7_days": [
            {k: (round(r[k] / 3600, 1) if k == "sleep_s" and r.get(k) else r.get(k)) for k in keep}
            for r in wellness[-7:]
        ],
        "training_last_7_days": [
            {"day": d["day"], "load": round(d.get("load") or 0), "hard_min": round(d.get("hard_min") or 0),
             "hours": round(d.get("hours") or 0, 1)}
            for d in intensity[-7:]
        ],
        "readiness": {
            "level": readiness.level,
            "score": readiness.score,
            "signals": [{"name": s.name, "status": s.status, "value": s.value} for s in readiness.signals],
        },
        "suggested_workout": (
            {"name": suggestion.workout.name, "exercises": suggestion.exercises}
            if isinstance(suggestion, GymSuggestion) else
            {
                "name": suggestion.workout.name,
                "steps": [
                    {"minutes": s.seconds / 60, "pct_ftp_low": round(s.low * 100), "pct_ftp_high": round(s.high * 100),
                     "label": s.label}
                    for s in suggestion.steps
                ],
            }
        ),
        **({"gym": {"goal_label": gym["goal_label"], "days_since_trained": gym["days_since"]}} if gym else {}),
        **({"training_plan": plan} if plan else {}),
    }


def _post(body: dict, key: str) -> dict:
    request = urllib.request.Request(
        URL,
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise CoachError("OpenRouter rejected the API key. Check your OpenRouter API key.") from exc
        if exc.code == 402:
            raise CoachError("OpenRouter account is out of credits.") from exc
        if exc.code == 429:
            raise CoachError("Rate limited by OpenRouter. Try again in a minute.") from exc
        raise CoachError(f"OpenRouter error ({exc.code}).") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise CoachError("Couldn't reach OpenRouter. Check your connection.") from exc


def ask_coach(
    payload: dict, model: str | None = None, post=_post, api_key: str | None = None,
    history: list[ChatMessage] | None = None,
) -> CoachAdvice:
    """Ask for advice. `history` holds the turns after the opening summary, ending with the athlete's new message."""
    key = resolve_key(api_key)
    if not key:
        raise CoachError("Enter your OpenRouter API key first.")
    schema = json.dumps(CoachAdvice.model_json_schema())
    turns = [{"role": m.role, "content": m.content} for m in history or []]
    opening = json.dumps(payload)
    if turns and turns[0]["role"] == "user":
        # The athlete wrote the first message themselves: send it with the data summary, not as a second user turn.
        opening += "\n\nThe athlete's message: " + turns.pop(0)["content"]
    body = {
        "model": model or DEFAULT_MODEL,
        "max_tokens": 4000,  # some models spend part of this on reasoning before the JSON
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": f"{SYSTEM}\n\nReply with a single JSON object matching this schema:\n{schema}"},
            {"role": "user", "content": opening},
            *turns,
        ],
    }
    data = post(body, key)
    choice = (data.get("choices") or [{}])[0]
    if choice.get("finish_reason") == "content_filter":
        raise CoachError("The coach declined to answer this request.")
    content = (choice.get("message") or {}).get("content")
    if isinstance(content, list):  # some providers return a list of parts
        content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
    text = content or ""
    if not text.strip():
        raise CoachError("The coach sent an empty reply. Try again, or pick another model.")
    truncated = choice.get("finish_reason") == "length"
    # Some models wrap the JSON in a code fence or add a sentence around it.
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        text = text[start:end + 1]
    try:
        return CoachAdvice.model_validate_json(text)
    except ValidationError as exc:
        log.warning("Unusable coach reply from %s (finish_reason=%s): %s | %.1500s", body["model"], choice.get("finish_reason"), exc, text)
        if truncated:
            raise CoachError("The coach's reply was cut off. Try again, or pick a model that writes longer replies.") from exc
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(p) for p in first.get("loc", ())) or "reply"
        raise CoachError(f"The coach returned an unexpected response ({where}: {first.get('msg', 'invalid')}). Try again or pick another model.") from exc
