"""Optional AI coach: ask an LLM (via OpenRouter) to explain today's readiness and refine the suggested workout.

Only a compact numeric summary is sent (no names, places, GPS, email or raw Garmin JSON).
The rules engine stays the source of truth; the LLM explains it and may propose tweaks.
"""

import json
import os
import urllib.error
import urllib.request

from pydantic import BaseModel, ValidationError

from garmin_dashboards.readiness import Readiness
from garmin_dashboards.workouts import Suggestion

URL = "https://openrouter.ai/api/v1/chat/completions"
# Models offered in the menu (OpenRouter ids; the UI also accepts any other id). Edit freely.
MODELS = [
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

SYSTEM = """You are an experienced, conservative endurance cycling coach.
You receive a JSON summary of an athlete's recent recovery data (HRV, resting heart rate, sleep),
training load, a rules-based readiness verdict and the workout those rules picked for today.

- Explain the verdict in plain language, citing only numbers present in the data.
- Say whether you agree with the suggested workout. Only propose adjusted steps if you have a
  clear reason; keep the same total duration and express targets as % of FTP.
- When signals conflict, lean towards recovery.
- Do not make medical claims or diagnoses. If something looks unusual (e.g. a sharp resting HR jump),
  suggest the athlete pays attention to how they feel and seeks professional advice if it persists.
- Be brief: the summary is 2-4 sentences."""


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


class CoachError(RuntimeError):
    pass


def is_available() -> bool:
    return bool(os.environ.get("OPENROUTER_API_KEY"))


def build_payload(
    readiness: Readiness,
    suggestion: Suggestion,
    wellness: list[dict],
    intensity: list[dict],
    form: float | None,
    ftp: float,
) -> dict:
    """Compact, identifier-free summary of the last 7 days plus today's verdict."""
    keep = ("day", "hrv_avg", "rhr", "sleep_score", "sleep_s", "bb_high")
    return {
        "ftp_w": ftp,
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
            raise CoachError("OpenRouter rejected the API key. Check OPENROUTER_API_KEY.") from exc
        if exc.code == 402:
            raise CoachError("OpenRouter account is out of credits.") from exc
        if exc.code == 429:
            raise CoachError("Rate limited by OpenRouter. Try again in a minute.") from exc
        raise CoachError(f"OpenRouter error ({exc.code}).") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise CoachError("Couldn't reach OpenRouter. Check your connection.") from exc


def ask_coach(payload: dict, model: str | None = None, post=_post) -> CoachAdvice:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise CoachError("OPENROUTER_API_KEY is not set.")
    schema = json.dumps(CoachAdvice.model_json_schema())
    body = {
        "model": model or DEFAULT_MODEL,
        "max_tokens": 2000,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": f"{SYSTEM}\n\nReply with a single JSON object matching this schema:\n{schema}"},
            {"role": "user", "content": json.dumps(payload)},
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
