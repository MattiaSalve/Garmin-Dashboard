"""Gym sessions: a library of strength sessions and the picker that matches one to today's readiness.

A gym session is a list of exercises (sets, reps, effort as RPE, rest), not timed steps. What gets picked
depends on three things:
- the athlete's gym goal from Settings (general strength, support for endurance training, build muscle),
  which decides the rep ranges and which sessions are favoured;
- which muscle groups were trained last, read from the exercise sets Garmin records for strength activities,
  so the session goes to what has rested longest;
- fatigue: readiness, and hard rides or runs in the last two days (heavy leg work is skipped then).
"""

from collections import Counter
from dataclasses import dataclass, field
from datetime import date

from garmin_dashboards.readiness import Readiness
from garmin_dashboards.workouts import DEFAULT_LEVEL, LEVELS, days_since_hard, needs_rest

KEY = "gym"
GROUP_NAME = "Strength"  # the metrics.SPORT_GROUPS entry for gym activities

GROUPS = ("legs", "push", "pull", "core")
GROUP_LABEL = {"legs": "Legs", "push": "Push", "pull": "Pull", "core": "Core"}

# Garmin's exercise categories (as in summarizedExerciseSets) mapped to the muscle groups they load.
CATEGORY_GROUPS: dict[str, tuple[str, ...]] = {
    "SQUAT": ("legs",), "DEADLIFT": ("legs",), "LUNGE": ("legs",), "LEG_CURL": ("legs",), "CALF_RAISE": ("legs",),
    "HIP_RAISE": ("legs",), "HIP_SWING": ("legs",), "PLYO": ("legs",),
    "OLYMPIC_LIFT": ("legs", "pull"),
    "BENCH_PRESS": ("push",), "SHOULDER_PRESS": ("push",), "PUSH_UP": ("push",), "FLYE": ("push",),
    "LATERAL_RAISE": ("push",), "TRICEPS_EXTENSION": ("push",), "SHOULDER_STABILITY": ("push",),
    "ROW": ("pull",), "PULL_UP": ("pull",), "CURL": ("pull",), "SHRUG": ("pull",),
    "PLANK": ("core",), "CORE": ("core",), "CRUNCH": ("core",), "SIT_UP": ("core",), "LEG_RAISE": ("core",),
    "HYPEREXTENSION": ("core",), "CHOP": ("core",), "CARRY": ("core",),
    # Low-load hip and trunk stability drills (Garmin files the dead bug here): core, so they never block leg day.
    "HIP_STABILITY": ("core",),
    "TOTAL_BODY": GROUPS,
}
MIN_SETS = 2  # sets of a group in one session before it counts as trained (one set is often a warm-up)
MIN_REST_DAYS = 2  # a muscle group is left alone for this many days after a session

GOALS = {1: "strength", 2: "endurance", 3: "muscle"}
GOAL_LABEL = {
    "strength": "General strength",
    "endurance": "Support my endurance training",
    "muscle": "Build muscle",
}
DEFAULT_GOAL = "strength"
LEVEL_MAX_MINUTES = {"beginner": 45, "intermediate": 60, "advanced": 90}
LEVEL_MAX_EXERCISES = {"beginner": 5, "intermediate": 7, "advanced": 8}
RED_CAP_MINUTES = 25
WARMUP_MIN = 6


@dataclass(frozen=True)
class Exercise:
    name: str
    group: str           # one of GROUPS, or "mobility"
    kind: str            # "compound" | "accessory" | "core" | "mobility": picks the rep scheme
    sets: int
    note: str = ""


@dataclass(frozen=True)
class Session:
    key: str
    name: str
    purpose: str
    groups: tuple[str, ...]
    exercises: tuple[Exercise, ...] = ()
    note: str = ""


# Reps, effort (RPE 1-10, where 10 = nothing left) and rest in seconds per exercise kind and goal.
SCHEMES: dict[str, dict[str, tuple[str, int, int]]] = {
    "strength": {"compound": ("4–6", 8, 150), "accessory": ("8–10", 7, 90), "core": ("30–45 s", 7, 45), "mobility": ("30–60 s", 2, 15)},
    "endurance": {"compound": ("5–8", 7, 120), "accessory": ("8–12", 6, 75), "core": ("30–45 s", 7, 45), "mobility": ("30–60 s", 2, 15)},
    "muscle": {"compound": ("6–10", 8, 120), "accessory": ("10–15", 8, 75), "core": ("45–60 s", 7, 45), "mobility": ("30–60 s", 2, 15)},
}
# How strongly each goal favours each session when several are equally rested.
BIAS: dict[str, dict[str, float]] = {
    "strength": {"lower": 1.0, "upper": 1.0, "full": 1.0, "core": 0.6},
    "endurance": {"lower": 1.4, "upper": 0.6, "full": 1.0, "core": 1.1},
    "muscle": {"lower": 1.0, "upper": 1.0, "full": 0.8, "core": 0.5},
}

LOWER = Session(
    "lower", "Lower body", "Leg strength and the glutes and hamstrings that drive the pedal stroke and stride.", ("legs", "core"),
    (
        Exercise("Goblet or back squat", "legs", "compound", 3),
        Exercise("Romanian deadlift", "legs", "compound", 3, "Hinge at the hips, flat back."),
        Exercise("Split squat or step-up (each leg)", "legs", "compound", 3, "One leg at a time: balance and the pedal stroke's single-leg work."),
        Exercise("Hip thrust or glute bridge", "legs", "accessory", 3),
        Exercise("Calf raise", "legs", "accessory", 2),
        Exercise("Plank", "core", "core", 3),
    ),
)
UPPER = Session(
    "upper", "Upper body", "Push and pull strength for posture and balanced shoulders.", ("push", "pull"),
    (
        Exercise("Bench press or push-up", "push", "compound", 3),
        Exercise("Row (dumbbell, cable or bar)", "pull", "compound", 3),
        Exercise("Overhead press", "push", "compound", 3),
        Exercise("Lat pulldown or pull-up", "pull", "compound", 3),
        Exercise("Face pull or rear-delt raise", "pull", "accessory", 2),
        Exercise("Triceps extension", "push", "accessory", 2),
    ),
)
FULL = Session(
    "full", "Full body", "One session that covers every main movement when time is short.", GROUPS,
    (
        Exercise("Goblet or back squat", "legs", "compound", 3),
        Exercise("Bench press or push-up", "push", "compound", 3),
        Exercise("Row (dumbbell, cable or bar)", "pull", "compound", 3),
        Exercise("Romanian deadlift", "legs", "compound", 3),
        Exercise("Plank", "core", "core", 3),
        Exercise("Overhead press", "push", "accessory", 2),
        Exercise("Side plank (each side)", "core", "core", 2),
    ),
)
CORE = Session(
    "core", "Core and stability", "Trunk strength that keeps you stable on the bike and in running form, with little soreness.", ("core",),
    (
        Exercise("Plank", "core", "core", 3),
        Exercise("Side plank (each side)", "core", "core", 3),
        Exercise("Dead bug", "core", "core", 3),
        Exercise("Pallof press", "core", "core", 3),
        Exercise("Back extension", "core", "core", 3),
        Exercise("Bird dog", "core", "core", 2),
    ),
)
MOBILITY = Session(
    "mobility", "Mobility and easy strength", "Keep moving without adding fatigue.", (),
    (
        Exercise("Hip flexor stretch (each side)", "mobility", "mobility", 2),
        Exercise("World's greatest stretch", "mobility", "mobility", 2),
        Exercise("Glute bridge", "mobility", "mobility", 2),
        Exercise("Thoracic rotation", "mobility", "mobility", 2),
        Exercise("Cat-cow", "mobility", "mobility", 2),
        Exercise("Hamstring stretch (each side)", "mobility", "mobility", 2),
    ),
    "Keep it gentle: nothing should feel hard.",
)
REST_SESSION = Session(
    "rest", "Rest day", "Let the accumulated fatigue clear before training again.", (),
    note="No lifting today. Walking, stretching, food and an early night all help.",
)
CANDIDATES = (LOWER, UPPER, FULL, CORE)
FOCUS = {s.key: s for s in CANDIDATES}  # what the athlete can ask to train today
FOCUS_LABEL = {"lower": "Legs", "upper": "Upper body", "full": "Full body", "core": "Core"}


@dataclass
class GymSuggestion:
    session: Session
    exercises: list[dict] = field(default_factory=list)
    rationale: list[str] = field(default_factory=list)
    minutes: int = 0
    level: str = DEFAULT_LEVEL
    goal: str = DEFAULT_GOAL
    last_trained: dict = field(default_factory=dict)

    @property
    def workout(self) -> Session:  # same name the endurance suggestions use
        return self.session


def goal_from_setting(value: float | None) -> str:
    return GOALS.get(round(value), DEFAULT_GOAL) if value else DEFAULT_GOAL


# ------------------------------------------------------------------ what was trained when


def groups_hit(sets: list[dict]) -> set[str]:
    """Muscle groups with at least MIN_SETS sets in one session's exercise summary."""
    count: Counter[str] = Counter()
    for s in sets:
        for g in CATEGORY_GROUPS.get(str(s.get("category") or "").upper(), ()):
            count[g] += s.get("sets") or 0
    return {g for g, n in count.items() if n >= MIN_SETS}


def last_trained(sessions: list[dict], today: date) -> dict:
    """Days since each muscle group was last trained (None = not in the data).

    `sessions` are strength activities, each {"day": "YYYY-MM-DD", "sets": [{"category", "sets", ...}]}.
    A session whose exercises Garmin didn't label is treated as full body, the cautious assumption.
    """
    days: dict[str, int | None] = {g: None for g in GROUPS}
    unlabelled = False
    for s in sessions:
        day = date.fromisoformat(s["day"])
        if day > today:
            continue
        hit = groups_hit(s.get("sets") or [])
        if not hit:
            hit, unlabelled = set(GROUPS), True
        ago = (today - day).days
        for g in hit:
            if days[g] is None or ago < days[g]:
                days[g] = ago
    return {"days": days, "unlabelled": unlabelled}


def assess_level(setting: float | None, sessions_12w: int) -> dict:
    """Gym level: the one set in Settings, else from how many strength sessions the last 12 weeks hold."""
    if setting and 1 <= round(setting) <= len(LEVELS):
        return {"level": LEVELS[round(setting) - 1], "source": "set", "reasons": ["Set in Settings."]}
    per_week = sessions_12w / 12
    level = 0 if per_week < 1 else 1 if per_week < 2.5 else 2
    return {
        "level": LEVELS[level], "source": "estimated",
        "reasons": [f"{sessions_12w} strength sessions in the last 12 weeks ({per_week:.1f} per week) → {LEVELS[level]}."],
    }


# ------------------------------------------------------------------ picking and sizing a session


def _row(e: Exercise, goal: str, level: str, trim: bool) -> dict:
    reps, rpe, rest = SCHEMES[goal][e.kind]
    sets = e.sets
    if level == "beginner":
        sets, rpe = max(2, sets - 1), rpe - 1 if e.kind != "mobility" else rpe
    elif level == "advanced" and e.kind == "compound":
        sets = min(5, sets + 1)
    if trim and e.kind != "mobility":  # amber: a little less volume and effort
        sets, rpe = max(2, sets - 1), max(5, rpe - 1)
    note = e.note
    if goal == "endurance" and e.kind in ("compound", "accessory"):
        note = (note + " " if note else "") + "Stop about 2 reps before failure so you aren't sore for your next ride or run."
    return {"name": e.name, "group": e.group, "sets": sets, "reps": reps, "rpe": rpe, "rest_s": rest, "note": note}


def _minutes(row: dict) -> float:
    return row["sets"] * (45 + row["rest_s"]) / 60 + 1  # a set takes about 45 s; a minute to set up


def _fit(session: Session, goal: str, level: str, minutes: int, trim: bool) -> list[dict]:
    """The session's exercises in priority order, as many as fit the time (at least 2, within the level's limit)."""
    warmup = 0 if session.key == "mobility" else WARMUP_MIN
    budget = minutes - warmup
    rows: list[dict] = []
    used = 0.0
    for e in session.exercises:
        row = _row(e, goal, level, trim)
        if len(rows) >= LEVEL_MAX_EXERCISES[level] or (len(rows) >= 2 and used + _minutes(row) > budget):
            break
        rows.append(row)
        used += _minutes(row)
    return rows


def suggest(
    readiness: Readiness, minutes: int, intensity: list[dict], trained: dict, today: date,
    goal: str = DEFAULT_GOAL, level: str = DEFAULT_LEVEL, focus: str | None = None,
) -> GymSuggestion:
    """Pick and size today's gym session for an athlete with this goal and level.

    `trained` comes from last_trained; `intensity` is daily rows from metrics.daily_intensity for every sport,
    so a hard ride yesterday keeps today's session away from heavy leg work.
    `focus` is a session the athlete asked for (a FOCUS key). It is honoured unless readiness calls for rest
    or mobility, and the rationale warns when the muscles aren't recovered; the volume is then trimmed.
    """
    days = trained["days"]
    why = [readiness.headline]
    if needs_rest(readiness, intensity, today):
        why.append("Several recovery signals are poor or fatigue has piled up after hard days, so a day off is better than lifting.")
        return GymSuggestion(REST_SESSION, [], why, 0, level, goal, trained)

    cap = LEVEL_MAX_MINUTES[level]
    if minutes > cap:
        why.append(f"Capped at {cap} min, a sensible session length for {level} lifters.")
        minutes = cap

    if readiness.level == "red":
        why.append("Recovery comes first: mobility and easy movement only.")
        minutes = min(minutes, RED_CAP_MINUTES)
        session = MOBILITY
        return GymSuggestion(session, _fit(session, goal, level, minutes, False), why, _total(session, goal, level, minutes, False), level, goal, trained)

    if focus == MOBILITY.key:
        why.append("Mobility, as planned: easy movement to keep you supple without adding fatigue.")
        return GymSuggestion(MOBILITY, _fit(MOBILITY, goal, level, minutes, False), why, _total(MOBILITY, goal, level, minutes, False), level, goal, trained)

    amber = readiness.level == "amber"
    since_hard = days_since_hard(intensity, today)
    legs_ago = days["legs"]
    block_legs = False
    if since_hard is not None and since_hard <= 1:
        block_legs = True
        why.append("You trained hard yesterday, so heavy leg work waits.")
    elif legs_ago is not None and legs_ago < (3 if amber else MIN_REST_DAYS):
        block_legs = True
        why.append(f"Legs were last trained {_ago(legs_ago)}; they need more recovery.")

    if focus in FOCUS:
        best = FOCUS[focus]
        why.append(f"{best.name}, as you chose. {_last_trained_text(best, days)}")
        trim = amber
        if "legs" in best.groups and block_legs:
            trim = True
            why.append("Your legs aren't fully recovered, so the volume and effort are trimmed. Consider another focus if they feel heavy.")
        elif min((days[g] for g in best.groups if days[g] is not None), default=9) < MIN_REST_DAYS:
            trim = True
            why.append("Part of this was trained very recently, so the volume and effort are trimmed.")
        if amber:
            why.append("Amber readiness: a little less volume and effort.")
        return GymSuggestion(best, _fit(best, goal, level, minutes, trim), why, _total(best, goal, level, minutes, trim), level, goal, trained)

    best, best_score = None, 0.0
    for s in CANDIDATES:
        if block_legs and "legs" in s.groups:
            continue
        staleness = sum(min(days[g] if days[g] is not None else 7, 7) for g in s.groups) / len(s.groups)
        if staleness < 1:  # every group in it was trained today
            continue
        score = staleness * BIAS[goal][s.key]
        if score > best_score:
            best, best_score = s, score
    if best is None:
        why.append("Everything useful was trained very recently, so mobility is today's better choice.")
        best = MOBILITY
        minutes = min(minutes, RED_CAP_MINUTES)
    else:
        why.append(_why_session(best, days, goal))
    if amber and best is not MOBILITY:
        why.append("Amber readiness: same exercises with a little less volume and effort.")
    return GymSuggestion(best, _fit(best, goal, level, minutes, amber), why, _total(best, goal, level, minutes, amber), level, goal, trained)


def _total(session: Session, goal: str, level: str, minutes: int, trim: bool) -> int:
    rows = _fit(session, goal, level, minutes, trim)
    return round((0 if session.key == "mobility" else WARMUP_MIN) + sum(_minutes(r) for r in rows))


def _ago(days: int) -> str:
    return "today" if days == 0 else "yesterday" if days == 1 else f"{days} days ago"


def _last_trained_text(session: Session, days: dict) -> str:
    parts = [
        f"{GROUP_LABEL[g].lower()} {'not in your recent data' if days[g] is None else _ago(days[g])}"
        for g in session.groups
    ]
    return f"Last trained: {', '.join(parts)}."


def _why_session(session: Session, days: dict, goal: str) -> str:
    text = f"{session.name} today. {_last_trained_text(session, days)}"
    if goal == "endurance":
        text += " Your goal is to support your endurance training, so legs and core come first and soreness is kept low."
    return text

