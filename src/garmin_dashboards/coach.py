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

SYSTEM = """You are an experienced, conservative endurance coach (cycling, running, swimming and strength).
You receive a short JSON summary: today's recovery and readiness verdict, the athlete's levels, goals, gym
setup and the workout their rules engine picked for today in "sport_today".

How to answer:
- Answer the athlete's actual question first, in the shape it needs. A request to change a workout gets the
  new workout and a one-line reason; "why am I tired?" gets a diagnosis from the data; "how is my FTP going?"
  gets a trend. Do not open with a recitation of HRV, resting HR and sleep: mention a recovery number only
  when it changes your answer, and then once.
- You have read-only tools (get_recovery, get_training, get_strength_history, get_fitness, get_power_curve,
  get_plan, find_activities). Call them for anything the summary does not already settle (history, trends, when something was
  last trained, how past hard days went, "my longest / hardest / highest-power ride" via find_activities) instead of guessing. Ask for only what the question needs, and cite
  only numbers you have seen. Never invent data.
- When they ask for a harder, longer or different workout, give it within safety limits (recovery signals and
  the rules below) and say what you changed and why. Do not refuse just because the rules engine suggested less.
- "summary" is your reply to the athlete: plain prose, as long as the question deserves (usually 2-6
  sentences), and markdown lists only when listing helps. "cautions" is optional: only real warnings, empty
  otherwise. Leave "agree_with_suggestion" null unless they asked about today's suggestion.
- Attach adjusted_steps / adjusted_exercises / plan_changes only when the athlete wants a workout or plan change.

- "athlete_levels" gives the athlete's level in each sport. Fit the workout to the level in the sport
  being trained today: a strong cyclist can still be a beginner runner who needs gentler running, with
  fewer intervals and more walk breaks. Aerobic fitness from one sport carries over only partly to another, and running adds impact
  stress that cycling and swimming do not.

- "athlete_goals" is the athlete's own description of their long-term goals (e.g. an event, a distance,
  a sustained speed) and "targets" holds their weekly hours and yearly km targets. Shape the plan so it
  builds towards them: choose session types and durations that serve the goal, and say in one sentence
  how today's ride fits. Recovery signals still win over goals on a day when they conflict.
- Say whether you agree with the suggested workout. Only propose adjusted steps if you have a
  clear reason; keep the same total duration and express targets as % of FTP for cycling, or % of threshold speed for running and swimming
  (the pct_ftp fields hold that percentage).
- When signals conflict, lean towards recovery.
- A rest day is a valid answer. If the suggested workout is "Rest day", or the data show the athlete is
  overworked (poor recovery plus heavy recent load), say so plainly and agree or recommend rest, with
  adjusted_steps as an empty list. Never invent a ride to fill the day.
- Do not make medical claims or diagnoses. If something looks unusual (e.g. a sharp resting HR jump),
  suggest the athlete pays attention to how they feel and seeks professional advice if it persists.
- Gym sessions: when "sport_today" is "gym" the suggested workout lists exercises (sets, reps, rpe 1-10, rest in
  seconds, muscle group). "gym.days_since_trained" says how many days ago each muscle group was last trained
  (null = nothing in the recent data) and "gym.goal_label" is why the athlete lifts: for "Support my endurance
  training" protect their rides and runs (no heavy leg work within 48 h of hard training or before a key session,
  stop sets short of failure, favour legs, hips and core). "gym.equipment" is what the athlete trains with:
  only use exercises that can be done with it (bodyweight always works). "gym.effort" says how hard the sets go:
  with "To failure" every working set ends at failure (rpe 10) unless recovery is poor. Propose changes with the
  complete list in adjusted_exercises and leave adjusted_steps null. In every other sport leave adjusted_exercises null.
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
- The summary says where the athlete is chatting from (Home or Plan). The plan comes from get_plan (a
  "training_plan" block in the summary is the same data): their multi-week plan, built from their goals, sports
  and weekly time. A request to change the plan ("make the gym sessions longer", "no training Thursday") is
  answered with plan_changes, never only in prose and never as adjusted_exercises for today.
  "weeks" lists this week and next day by day (planned sessions, status, what was done),
  "last_weeks_planned_vs_done" shows how well they followed it, and "open_proposals" are changes the rules have
  already suggested. Only when the athlete asks you to review or change the plan (e.g. "I can't train on
  Thursday", "make next week lighter", "review my plan"), return plan_changes: each one sets a day's session
  (day as YYYY-MM-DD, slot 0 for the main session or 1 for a second one, sport, kind, minutes, and a one-line
  note saying why). Use kind "rest" to clear a session. Otherwise leave plan_changes null.
  For a gym session, "exercises" is the complete exercise list for that session (name, group, sets, reps, rpe,
  rest_s): use it for any change to what is done in the session (harder, more variety, different muscles, failure
  sets = rpe 10), keeping minutes and kind as they are when only the content changes. The planned gym sessions in
  the plan summary list their current exercises.
- Plan changes: use only kinds listed in "kinds_by_sport" and the athlete's chosen sports; stay within their
  hours_per_week and sessions_per_week; keep hard sessions (tempo, sweet_spot, fartlek, threshold, vo2max) on
  non-consecutive days and not the day after the long session; never change locked sessions or days before
  today; and when recovery is poor or they recently did much more than planned, make the coming days easier,
  not harder. Never add sessions to catch up on missed ones. Fewer, well-reasoned changes beat many. The athlete
  approves each change set before it is applied, so explain it in the summary."""


# Where the athlete is chatting from: what they can see there, and what their messages are most likely about.
SCREENS = {
    "home": "The athlete is on the Home page, looking at today's recovery verdict and the workout recommended for "
            "today (sport_today, suggested_workout in the summary). Read their messages as being about today's "
            "workout unless they clearly ask about something else (history, trends, the plan).",
    "plan": "The athlete is on the Plan page, looking at their multi-week training plan (training_plan in the "
            "summary: this week and next, sessions per day). Read their messages as being about the plan. A "
            "request to change it (longer, shorter, move, add, remove a session) is answered with plan_changes "
            "for every affected session, not as today's workout (leave adjusted_steps and adjusted_exercises "
            "null). Gym sessions in the plan can run up to 90 minutes. A change to what is done in a gym "
            "session (harder, more variety, failure sets) is a plan_change with the full \"exercises\" list, even "
            "when the minutes stay the same. If you cannot make a change, say why in the summary.",
}


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
    exercises: list[CoachExercise] | None = None  # gym only: the full exercise list for that session

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
    agree_with_suggestion: bool | None = None
    # The sport of the proposed workout when it isn't the one the athlete selected (null = the selected sport).
    workout_sport: Literal["cycling", "running", "swimming", "gym"] | None = None
    adjusted_steps: list[CoachStep] | None = None
    cautions: list[str] = Field(default_factory=list)
    adjusted_exercises: list[CoachExercise] | None = None  # gym sessions only
    plan_changes: list[CoachPlanChange] | None = None  # edits to the training plan, proposed to the athlete
    tools_used: list[str] = Field(default_factory=list)  # set by the server, not the model

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
    slim: bool = False,
    today: str | None = None,
) -> dict:
    """Compact, identifier-free summary of the last 7 days plus today's verdict.
    `slim` (used with the lookup tools) keeps only today's row; the coach fetches history itself (and the plan, unless one is passed)."""
    keep = ("day", "hrv_avg", "rhr", "sleep_score", "sleep_s", "bb_high")
    if slim:
        wellness, intensity = wellness[-1:], intensity[-1:]
    return {
        **({"today": today} if today else {}),
        "athlete_goals": goals.strip()[:GOALS_MAX] or None,
        "targets": {k: v for k, v in (targets or {}).items() if v is not None},
        "sport_today": sport,
        # Level in each sport, e.g. a strong cyclist who is a beginner runner.
        "athlete_levels": levels or {},
        "ftp_w": ftp,
        "threshold_pace_s": threshold_pace_s, "threshold_pace_unit": pace_unit or None,
        "hrv_range_ms": list(readiness.hrv_range) if readiness.hrv_range else None,
        "form_tsb": None if form is None else round(form, 1),
        ("recovery_latest" if slim else "recovery_last_7_days"): [
            {k: (round(r[k] / 3600, 1) if k == "sleep_s" and r.get(k) else r.get(k)) for k in keep}
            for r in wellness[-7:]
        ],
        ("training_latest" if slim else "training_last_7_days"): [
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
        **({"gym": {"goal_label": gym["goal_label"], "days_since_trained": gym["days_since"],
                    **({"equipment": gym["equipment"], "effort": gym["effort"]} if "equipment" in gym else {})}} if gym else {}),
        **({"training_plan": plan} if plan else {}),
    }


def _post(body: dict, key: str, timeout: float = TIMEOUT_S) -> dict:
    request = urllib.request.Request(
        URL,
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
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


def _text(content) -> str:
    if isinstance(content, list):  # some providers return a list of parts
        return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return content if isinstance(content, str) else ""


def _reply(data: dict, model: str) -> tuple[str, dict]:
    """The reply text and its choice. Raises CoachError for errors OpenRouter reports inside a 200 response."""
    error = data.get("error")
    choice = (data.get("choices") or [{}])[0]
    error = error or choice.get("error")
    if error or choice.get("finish_reason") == "error":
        message = error.get("message") if isinstance(error, dict) else error
        log.warning("OpenRouter error from %s: %s", model, error)
        raise CoachError(f"The model's provider failed ({message or 'unknown error'}). Try again, or pick another model.")
    if choice.get("finish_reason") == "content_filter":
        raise CoachError("The coach declined to answer this request.")
    text = _text((choice.get("message") or {}).get("content"))
    if not text.strip():
        log.warning("Empty coach reply from %s (provider=%s, finish_reason=%s, usage=%s)", model,
                    data.get("provider"), choice.get("finish_reason"), data.get("usage"))
    return text, choice


MAX_TOOL_ROUNDS = 4


def ask_coach(
    payload: dict, model: str | None = None, post=_post, api_key: str | None = None,
    history: list[ChatMessage] | None = None, toolbox=None, screen: str | None = None,
) -> CoachAdvice:
    """Ask for advice. `history` holds the turns after the opening summary, ending with the athlete's new message.
    With a `toolbox` (coach_tools.Toolbox) the model may look data up first: up to MAX_TOOL_ROUNDS rounds of
    tool calls, then it must answer."""
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
            {"role": "system", "content": f"{SYSTEM}\n\n{SCREENS.get(screen or '', '')}\n\nReply with a single JSON object matching this schema:\n{schema}"},
            {"role": "user", "content": opening},
            *turns,
        ],
    }
    first = None
    if toolbox is not None:
        body, first = _run_tools(body, key, post, toolbox)
    advice = _ask_json(body, key, post, CoachAdvice, first)
    if toolbox is not None:
        advice.tools_used = list(toolbox.used)
    return advice


def _run_tools(body: dict, key: str, post, toolbox) -> tuple[dict, tuple[str, dict] | None]:
    """Let the model call the toolbox until it answers. Returns the body to finish with and, when the model
    already wrote its answer, that (text, choice) so it isn't requested twice."""
    # JSON mode and tool calls don't mix on every provider: the schema in the prompt and the tolerant parser do the job.
    body = {k: v for k, v in body.items() if k != "response_format"}
    messages = list(body["messages"])
    for _ in range(MAX_TOOL_ROUNDS):
        data = post({**body, "messages": messages, "tools": toolbox.specs}, key)
        text, choice = _reply(data, body["model"])
        calls = (choice.get("message") or {}).get("tool_calls") or []
        if not calls:
            return {**body, "messages": messages}, (text, choice)
        messages.append({"role": "assistant", "content": text or None, "tool_calls": calls})
        for call in calls:
            fn = call.get("function") or {}
            messages.append({"role": "tool", "tool_call_id": call.get("id", ""),
                             "content": toolbox.call(fn.get("name", ""), fn.get("arguments"))})
    messages.append({"role": "user", "content": "Answer now with what you have, as the JSON object. No more tool calls."})
    return {**body, "messages": messages}, None


def _ask_json(body: dict, key: str, post, cls: type[BaseModel], first: tuple[str, dict] | None = None):
    """Send the request and validate the JSON reply into `cls`. One retry when the reply comes back empty.
    `first` is a reply already received for this body."""
    text, choice = first if first is not None else _reply(post(body, key), body["model"])
    if not text.strip():
        # Usually a one-off provider hiccup, or a reasoning model that spent the whole budget thinking:
        # retry once, asking for less reasoning in the second case.
        if choice.get("finish_reason") == "length":
            body = {**body, "reasoning": {"effort": "low"}}
        text, choice = _reply(post(body, key), body["model"])
    if not text.strip():
        if choice.get("finish_reason") == "length":
            raise CoachError("The coach spent its whole reply thinking and wrote no answer. Try again, or pick a faster model.")
        raise CoachError("The coach sent an empty reply. Try again, or pick another model.")
    truncated = choice.get("finish_reason") == "length"
    # Some models wrap the JSON in a code fence or add a sentence around it.
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        text = text[start:end + 1]
    try:
        return cls.model_validate_json(text)
    except ValidationError as exc:
        log.warning("Unusable coach reply from %s (finish_reason=%s): %s | %.1500s", body["model"], choice.get("finish_reason"), exc, text)
        if truncated:
            raise CoachError("The coach's reply was cut off. Try again, or pick a model that writes longer replies.") from exc
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(p) for p in first.get("loc", ())) or "reply"
        raise CoachError(f"The coach returned an unexpected response ({where}: {first.get('msg', 'invalid')}). Try again or pick another model.") from exc


# ------------------------------------------------------------------ AI plan builder

PLAN_TIMEOUT_S = 150  # a multi-week plan is a long answer, and reasoning models think first
PLAN_MAX_TOKENS = 12000

PLAN_SYSTEM = """You are an experienced, conservative endurance coach (cycling, running, swimming and strength
for endurance athletes). You design the athlete's multi-week training plan. You receive a JSON brief:

- "goals" (the first one matters most; "weeks_to_go" counts down to its date), "athlete_goals" (their own words),
  "profile" (sports with the share of sessions each should get, hours_per_week, sessions_per_week (null = your
  choice), preferred_long_day) and "hours_available" (weeks where the athlete has less time than usual).
- "athlete_levels" per sport, "fitness" (ctl = 42-day fitness and form_tsb from Garmin training load),
  "load_per_hour" (the athlete's own Garmin training load per hour by sport and intensity class) and
  "kind_intensity" (the class of each session kind).
- "gym_goal" and "gym_setup" (when strength is one of the sports): the equipment the athlete has and how hard they
  want their sets ("To failure" means hard sessions with working sets taken to failure).
- "recent_weeks" (what they really did, week by week), "done_this_week", "recovery_last_7_days" and
  "readiness_last_days" (green / amber / red).
- "fixed_sessions": sessions in the weeks you plan that the athlete set, locked or keeps free, plus the goal event.
  Keep them and plan around them; never put a session on a day "kept free by the athlete".
- "already_planned_other_weeks" and "previous_week_targets": the plan around the weeks you write, for continuity.
- "limits": hard limits enforced after you answer. Sessions longer than max_session_minutes are cut, weeks over
  the athlete's hours are trimmed, and weeks whose load would raise fitness by more than max_fitness_rise_per_week
  are shortened ("max_load_this_week" is that cap for the current week). Plan within them.

Write the plan:
- "weeks": one entry for every week from the current one up to "targets_until": week_start (a Monday), phase
  ("base", "build", "recovery", "taper" or "race"), hours (planned training hours, within the time available) and a
  one-sentence note on the week's purpose. Use periodisation that fits the goal and the athlete: progressive build
  weeks, regular easier weeks, a taper and an easy event week before a dated event. Start from what they have
  really been doing lately (recent_weeks), not from their maximum time, and ramp up gradually.
- "sessions": every session of the weeks in "weeks_to_plan", from today on (never before today): day (YYYY-MM-DD),
  slot (0 = main session, 1 = a second session that day, e.g. gym after an easy ride), sport, kind (only kinds
  listed in "kinds_by_sport" for that sport), minutes and a short note saying what the session is for.
- Split the sessions between the sports by their shares. Put the long session on the preferred long day unless
  fixed sessions or the week's purpose say otherwise. Keep quality sessions (tempo, sweet_spot, fartlek,
  threshold, vo2max) apart, with easy days around them, and avoid leg-heavy gym (lower, full) the day before a
  quality or long session. Fit intensity to the level in each sport: beginners need fewer and gentler quality
  sessions. When readiness has been amber or red lately or form is very negative, make the first days easy.
- Every gym session also gets "exercises": the full workout, in order, each with name, group (legs, push, pull,
  core or mobility), sets, reps (e.g. "6–8" or "30 s"), rpe (1-10, 10 = failure), rest_s and a short note. Use
  only exercises that can be done with gym_setup's equipment (bodyweight always works), match the session kind
  (lower = legs, upper = push and pull, full, core, mobility), the gym goal and the effort: with "To failure", working
  sets at rpe 10 with progressions hard enough to fail in the rep range, except in recovery and taper weeks. Fit the
  exercises (sets × (45 s + rest) plus a 6-minute warm-up) into the session's minutes.
- Write the session totals so each week's minutes match its "hours".
- "summary": 2-4 sentences on the approach: the phases, how it leads to the goal, and anything you changed
  because of recovery or recent training. No medical claims."""


class AIPlanWeek(BaseModel):
    week_start: str
    phase: str = "build"
    hours: float = 0
    note: str = ""

    @field_validator("week_start", "phase", "note", mode="before")
    @classmethod
    def _text(cls, v):
        return "" if v is None else str(v)

    @field_validator("phase")
    @classmethod
    def _phase(cls, v):
        return v.strip().lower() or "build"

    @field_validator("hours", mode="before")
    @classmethod
    def _hours(cls, v):
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v)
        match = re.search(r"\d+(?:\.\d+)?", str(v or ""))
        return float(match.group()) if match else 0.0


class AIPlanSession(CoachPlanChange):
    """A planned session; gym sessions carry the AI's exercises."""
    exercises: list[CoachExercise] | None = None

    @field_validator("exercises", mode="before")
    @classmethod
    def _exercises(cls, v):
        if not isinstance(v, list):
            return None
        return [e for e in v if isinstance(e, dict) and str(e.get("name") or "").strip()] or None


class AIPlan(BaseModel):
    summary: str
    weeks: list[AIPlanWeek] = Field(default_factory=list)
    sessions: list[AIPlanSession] = Field(default_factory=list)

    @field_validator("weeks", "sessions", mode="before")
    @classmethod
    def _lists(cls, v):
        return v or []


def _post_plan(body: dict, key: str) -> dict:
    return _post(body, key, PLAN_TIMEOUT_S)


def ask_plan(payload: dict, model: str | None = None, post=_post_plan, api_key: str | None = None) -> AIPlan:
    """Ask the model to design the plan described by `payload` (see plan.ai_request)."""
    key = resolve_key(api_key)
    if not key:
        raise CoachError("Enter your OpenRouter API key first.")
    schema = json.dumps(AIPlan.model_json_schema())
    body = {
        "model": model or DEFAULT_MODEL,
        "max_tokens": PLAN_MAX_TOKENS,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": f"{PLAN_SYSTEM}\n\nReply with a single JSON object matching this schema:\n{schema}"},
            {"role": "user", "content": json.dumps(payload)},
        ],
    }
    return _ask_json(body, key, post, AIPlan)
