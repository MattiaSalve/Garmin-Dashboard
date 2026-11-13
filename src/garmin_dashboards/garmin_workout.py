"""Turn a suggested workout into a Garmin Connect workout, so it can be scheduled on the watch.

Pure: builds the JSON with garminconnect's typed models and does no network calls.
- Cycling and running steps are time-based. Cycling gets watt ranges from FTP; running gets pace ranges
  from threshold pace (when set).
- Swim steps become distances, rounded to whole there-and-back pool lengths, using threshold swim pace;
  rests stay timed. The target pace goes in each step's note: Garmin's own swim targets are relative to
  its Critical Swim Speed, which many watches (the Forerunner 255 included) don't have. Without a
  threshold pace the swim steps stay time-based.
- Gym exercises map to Garmin's exercise catalog (`garminconnect.exercises`) by name, else by muscle
  group, so the watch shows the right exercise and counts sets, reps and rests.
"""

import re
from dataclasses import dataclass

from garminconnect.workout import (
    ConditionType, CustomPowerTarget, CyclingWorkout, ExecutableStep, PaceTarget, RunningWorkout, SportType, StepType,
    StrengthWorkout, SwimmingWorkout, TargetType, WorkoutSegment, create_strength_set, create_strength_timed_set,
)

from garmin_dashboards.gym import CATEGORY_GROUPS

PUSHABLE_SPORTS = ("cycling", "running", "swimming", "gym")
MIN_PACE_FRACTION = 0.55  # below this (walking) a pace target is more annoying than useful
NAME_MAX = 80
NOTE_MAX = 200
SWIM_REST_FRACTION = 0.3  # swim steps at or below this fraction of threshold speed are rests at the wall
SET_SECONDS = 45  # rough length of one strength set, for the estimated duration
MIN_REST_S = 5
RECOVERY_LABEL = re.compile(r"\b(easy|rest|walk|jog|spin|recover)", re.IGNORECASE)

_SPORT = {
    "cycling": (CyclingWorkout, SportType.CYCLING, "cycling"),
    "running": (RunningWorkout, SportType.RUNNING, "running"),
    "swimming": (SwimmingWorkout, SportType.SWIMMING, "swimming"),
}
_STEP_KEYS = {
    StepType.WARMUP: "warmup", StepType.COOLDOWN: "cooldown",
    StepType.INTERVAL: "interval", StepType.RECOVERY: "recovery", StepType.REST: "rest",
}
_TIME = {"conditionTypeId": ConditionType.TIME, "conditionTypeKey": "time", "displayOrder": 2, "displayable": True}
_DISTANCE = {"conditionTypeId": ConditionType.DISTANCE, "conditionTypeKey": "distance", "displayOrder": 3, "displayable": True}
_NO_TARGET = {"targetType": {"workoutTargetTypeId": TargetType.NO_TARGET, "workoutTargetTypeKey": "no.target", "displayOrder": 1}}


@dataclass(frozen=True)
class PlannedStep:
    minutes: float
    frac_low: float
    frac_high: float
    label: str


def step_types(steps: list[PlannedStep]) -> list[int]:
    """Garmin step type per step: leading warm-ups, a final cool-down, and in between "recovery" for the
    easy half of an interval pair (an easy step that is easier than the hardest main step) or "interval"."""
    types = [StepType.INTERVAL] * len(steps)
    i = 0
    while i < len(steps) - 1 and "warm" in steps[i].label.lower():
        types[i] = StepType.WARMUP
        i += 1
    end = len(steps)
    if len(steps) > 1 and "cool" in steps[-1].label.lower():
        types[-1] = StepType.COOLDOWN
        end -= 1
    main = steps[i:end]
    hardest = max((s.frac_high for s in main), default=0)
    for j in range(i, end):
        s = steps[j]
        if RECOVERY_LABEL.search(s.label) and s.frac_high < hardest:
            types[j] = StepType.RECOVERY
    return types


def _target(sport: str, step: PlannedStep, ftp: float, threshold_pace_s: float | None) -> dict:
    if sport == "cycling" and ftp > 0:
        target = CustomPowerTarget(lower_limit=round(step.frac_low * ftp), upper_limit=round(step.frac_high * ftp))
    elif sport == "running" and threshold_pace_s and step.frac_low >= MIN_PACE_FRACTION:
        # A fraction of threshold *speed*: pace = threshold pace / fraction; speed in m/s, slower = lower.
        speed = lambda frac: round(1000 / (threshold_pace_s / frac), 3)  # noqa: E731
        target = PaceTarget(lower_limit=speed(step.frac_low), upper_limit=speed(step.frac_high))
    else:
        return _NO_TARGET
    return {
        "targetType": {"workoutTargetTypeId": target.target_type, "workoutTargetTypeKey": target.target_type_key, "displayOrder": 1},
        "targetValueOne": target.lower_limit,
        "targetValueTwo": target.upper_limit,
    }


def _pace_text(seconds: float) -> str:
    m, sec = divmod(round(seconds), 60)
    return f"{m}:{sec:02d}"


def swim_distance_m(step: PlannedStep, threshold_pace_s: float, pool_length_m: float) -> float:
    """How far the step's time takes you at the middle of its target, in whole there-and-back pool lengths
    (so every rep ends at the wall you started from), at least one there-and-back."""
    speed = 100 / (threshold_pace_s / ((step.frac_low + step.frac_high) / 2))  # m/s
    unit = 2 * pool_length_m
    return max(1, round(step.minutes * 60 * speed / unit)) * unit


def _swim_step(n: int, step: PlannedStep, kind: int, threshold_pace_s: float | None, pool_length_m: float) -> ExecutableStep:
    if step.frac_high <= SWIM_REST_FRACTION:  # rest at the wall: always timed
        kind, end, value, note = StepType.REST, _TIME, round(step.minutes * 60), step.label
    elif threshold_pace_s:
        fast, slow = threshold_pace_s / step.frac_high, threshold_pace_s / max(step.frac_low, 0.01)
        kind, end, value = kind, _DISTANCE, swim_distance_m(step, threshold_pace_s, pool_length_m)
        note = f"{step.label} · {_pace_text(fast)}–{_pace_text(slow)} /100 m"
    else:
        end, value, note = _TIME, round(step.minutes * 60), step.label
    return ExecutableStep(
        stepOrder=n, stepType={"stepTypeId": kind, "stepTypeKey": _STEP_KEYS[kind], "displayOrder": kind},
        endCondition=end, endConditionValue=value, description=note[:NOTE_MAX], **_NO_TARGET,
    )


def _duration_s(step: ExecutableStep, planned: PlannedStep) -> int:
    return round(planned.minutes * 60) if step.endCondition == _DISTANCE else int(step.endConditionValue)


def to_garmin(
    name: str, sport: str, steps: list[PlannedStep], ftp: float, threshold_pace_s: float | None = None,
    pool_length_m: float = 25,
) -> dict:
    """The upload payload for garminconnect's `upload_workout` (cycling, running or swimming)."""
    if sport not in _SPORT:
        raise ValueError(f"Only {', '.join(PUSHABLE_SPORTS)} workouts can be sent to the watch.")
    if not steps:
        raise ValueError("The workout has no steps.")
    model, sport_id, sport_key = _SPORT[sport]
    sport_type = {"sportTypeId": sport_id, "sportTypeKey": sport_key, "displayOrder": 1}
    executable = []
    for n, (step, kind) in enumerate(zip(steps, step_types(steps)), start=1):
        if sport == "swimming":
            executable.append(_swim_step(n, step, kind, threshold_pace_s, pool_length_m))
            continue
        executable.append(ExecutableStep(
            stepOrder=n,
            stepType={"stepTypeId": kind, "stepTypeKey": _STEP_KEYS[kind], "displayOrder": kind},
            endCondition=_TIME,
            endConditionValue=round(step.minutes * 60),
            description=step.label[:NAME_MAX],
            **_target(sport, step, ftp, threshold_pace_s),
        ))
    extra = {}
    if sport == "swimming":  # Garmin needs the pool length to count lengths for distance steps
        extra = {"poolLength": float(pool_length_m), "poolLengthUnit": {"unitId": 1, "unitKey": "meter", "factor": 100.0}}
    workout = model(
        workoutName=name.strip()[:NAME_MAX] or "Workout",
        sportType=sport_type,
        estimatedDurationInSecs=sum(_duration_s(e, p) for e, p in zip(executable, steps)),
        workoutSegments=[WorkoutSegment(segmentOrder=1, sportType=sport_type, workoutSteps=executable)],
        description="From Garmin dashboard",
        **extra,
    )
    return workout.to_dict()


# ------------------------------------------------------------------ gym


@dataclass(frozen=True)
class PlannedExercise:
    name: str
    group: str  # legs / push / pull / core / mobility
    sets: int
    reps: str   # "4–6", "10", "30–45 s"
    rpe: float | None = None
    rest_s: float = 60
    note: str = ""


# Garmin catalog entry (category, exercise) for the exercise names the app (and the coach) use. First match
# wins, so the more specific patterns come first ("side plank" before "plank", "split squat" before "squat").
EXERCISE_RULES: tuple[tuple[re.Pattern, str, str], ...] = tuple(
    (re.compile(pattern, re.IGNORECASE), category, exercise) for pattern, category, exercise in (
        (r"split squat|step-?up|lunge", "LUNGE", "DUMBBELL_BULGARIAN_SPLIT_SQUAT"),
        (r"squat", "SQUAT", "GOBLET_SQUAT"),
        (r"romanian|\brdl\b|deadlift", "DEADLIFT", "ROMANIAN_DEADLIFT"),
        (r"hip thrust", "HIP_RAISE", "BARBELL_HIP_THRUST_WITH_BENCH"),
        (r"glute bridge|hip raise|bridge", "HIP_RAISE", "HIP_RAISE"),
        (r"calf", "CALF_RAISE", "CALF_RAISE"),
        (r"bench", "BENCH_PRESS", "BENCH_PRESS"),
        (r"push-?\s?up", "PUSH_UP", "PUSH_UP"),
        (r"face pull|rear[- ]delt", "ROW", "FACE_PULL"),
        (r"pull-?\s?down|pull-?\s?up|chin-?\s?up", "PULL_UP", "LAT_PULLDOWN"),
        (r"\brow", "ROW", "DUMBBELL_ROW"),
        (r"pallof|anti-rotation", "CORE", "CABLE_CORE_PRESS"),
        (r"overhead press|shoulder press|military press", "SHOULDER_PRESS", "DUMBBELL_SHOULDER_PRESS"),
        (r"triceps", "TRICEPS_EXTENSION", "DUMBBELL_LYING_TRICEPS_EXTENSION"),
        (r"biceps|\bcurl", "CURL", "DUMBBELL_BICEPS_CURL"),
        (r"side plank", "PLANK", "SIDE_PLANK"),
        (r"plank", "PLANK", "PLANK"),
        (r"dead bug", "HIP_STABILITY", "DEAD_BUG"),
        (r"bird dog", "WARM_UP", "OPPOSITE_ARM_AND_LEG_BALANCE"),
        (r"back extension|hyperextension|superman", "HYPEREXTENSION", "STATIC_BACK_EXTENSION"),
        (r"hip flexor", "WARM_UP", "STRETCH_LUNGING_HIP_FLEXOR"),
        (r"greatest stretch", "WARM_UP", "STRETCH_LUNGE_WITH_SPINAL_TWIST"),
        (r"thoracic", "WARM_UP", "THORACIC_ROTATION"),
        (r"cat-?\s?cow|cat camel", "WARM_UP", "STRETCH_CAT_COW"),
        (r"hamstring", "WARM_UP", "STRETCH_HAMSTRING"),
    )
)
# For names nothing above matches (the coach can invent exercises): the category alone, by muscle group.
GROUP_CATEGORY = {"legs": "SQUAT", "push": "PUSH_UP", "pull": "ROW", "core": "CORE", "mobility": "WARM_UP"}


def garmin_exercise(name: str, group: str) -> tuple[str, str]:
    """(category, exercise) for an exercise; the exercise is "" when only the category is known.
    Mobility work never lands in a category gym.last_trained counts (a gentle glute bridge would otherwise
    mark the legs as trained and hold back the next leg session)."""
    for pattern, category, exercise in EXERCISE_RULES:
        if pattern.search(name):
            break
    else:
        category, exercise = GROUP_CATEGORY.get(group, "TOTAL_BODY"), ""
    if group == "mobility" and category in CATEGORY_GROUPS:
        return GROUP_CATEGORY["mobility"], ""
    return category, exercise


def parse_reps(reps: str) -> tuple[int, bool]:
    """(count, timed) from "4–6" (top of the range: 6), "10 each side" (10) or "30–45 s" (45 seconds).
    Falls back to 10 reps when there is no number; the original text stays in the step note."""
    numbers = [int(n) for n in re.findall(r"\d+", reps)]
    timed = bool(re.search(r"\d\s*(s|sec|secs|seconds)\b", reps, re.IGNORECASE))
    return (max(numbers) if numbers else 10), timed


def _exercise_note(e: PlannedExercise) -> str:
    parts = [e.name, f"{e.reps}" + ("" if parse_reps(e.reps)[1] else " reps")]
    if e.rpe:
        parts.append(f"RPE {e.rpe:g}")
    if e.note:
        parts.append(e.note)
    return " · ".join(parts)[:NOTE_MAX]


def to_garmin_strength(name: str, exercises: list[PlannedExercise], warmup_min: float = 0) -> dict:
    """A Garmin strength workout: an optional timed warm-up, then one "N sets" block per exercise
    (each set ends after the reps, or after the time for holds, followed by its rest)."""
    if not exercises:
        raise ValueError("The session has no exercises.")
    steps: list = []
    order, seconds = 1, 0
    if warmup_min > 0:
        steps.append(ExecutableStep(
            stepOrder=order, stepType={"stepTypeId": StepType.WARMUP, "stepTypeKey": "warmup", "displayOrder": 1},
            endCondition=_TIME, endConditionValue=round(warmup_min * 60), description="Warm-up: easy cardio and a few light sets",
            **_NO_TARGET,
        ))
        order, seconds = order + 1, round(warmup_min * 60)
    for e in exercises:
        category, exercise = garmin_exercise(e.name, e.group)
        count, timed = parse_reps(e.reps)
        sets, rest = max(1, e.sets), max(MIN_REST_S, e.rest_s)
        make = create_strength_timed_set if timed else create_strength_set
        steps.append(make(category, order, sets, count, rest, exercise_name=exercise, description=_exercise_note(e)))
        order += 3  # the block's own order, plus its exercise and rest steps
        seconds += sets * ((count if timed else SET_SECONDS) + rest)
    sport_type = {"sportTypeId": SportType.STRENGTH_TRAINING, "sportTypeKey": "strength_training", "displayOrder": 5}
    workout = StrengthWorkout(
        workoutName=name.strip()[:NAME_MAX] or "Gym session",
        estimatedDurationInSecs=round(seconds),
        workoutSegments=[WorkoutSegment(segmentOrder=1, sportType=sport_type, workoutSteps=steps)],
        description="From Garmin dashboard",
    )
    return workout.to_dict()
