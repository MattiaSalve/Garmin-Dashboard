"""Optional AI coach: ask an LLM (via OpenRouter) to explain today's readiness and refine the suggested workout.

Only a compact numeric summary is sent (no names, places, GPS, email or raw Garmin JSON).
The rules engine stays the source of truth; the LLM explains it and may propose tweaks.
"""

import json
import os
import urllib.error
import urllib.request
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from garmin_dashboards.mode import is_local
from garmin_dashboards.readiness import Readiness
from garmin_dashboards.workouts import Suggestion

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
- The athlete may follow up with a message, e.g. to change the plan (shorter, different focus, a hill
  they have to ride). Answer it directly, keep the earlier context, and when the workout changes return
  the complete updated adjusted_steps (not just the changed part). The athlete's request overrides
  "keep the same total duration", but still push back in the summary if a change looks unwise for their recovery."""


class CoachStep(BaseModel):
    minutes: float
    pct_ftp_low: int
    pct_ftp_high: int
    label: str


class CoachAdvice(BaseModel):
    summary: str
    agree_with_suggestion: bool
    adjusted_steps: list[CoachStep] | None
    cautions: list[str]


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
    suggestion: Suggestion,
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
        "suggested_workout": {
            "name": suggestion.workout.name,
            "steps": [
                {"minutes": s.seconds / 60, "pct_ftp_low": round(s.low * 100), "pct_ftp_high": round(s.high * 100),
                 "label": s.label}
                for s in suggestion.steps
            ],
        },
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
    body = {
        "model": model or DEFAULT_MODEL,
        "max_tokens": 2000,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": f"{SYSTEM}\n\nReply with a single JSON object matching this schema:\n{schema}"},
            {"role": "user", "content": json.dumps(payload)},
            *({"role": m.role, "content": m.content} for m in history or []),
        ],
    }
    data = post(body, key)
    choice = (data.get("choices") or [{}])[0]
    if choice.get("finish_reason") == "content_filter":
        raise CoachError("The coach declined to answer this request.")
    text = (choice.get("message") or {}).get("content") or ""
    try:
        return CoachAdvice.model_validate_json(text)
    except ValidationError as exc:
        raise CoachError("The coach returned an unexpected response.") from exc
