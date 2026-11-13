import pytest
from garminconnect.exercises import CATEGORIES, EXERCISES
from garminconnect.workout import CyclingWorkout, RunningWorkout, StepType, StrengthWorkout, SwimmingWorkout

from garmin_dashboards import gym
from garmin_dashboards.garmin_workout import (
    EXERCISE_RULES, GROUP_CATEGORY, PlannedExercise, PlannedStep, garmin_exercise, parse_reps, step_types,
    swim_distance_m, to_garmin, to_garmin_strength,
)
from garmin_dashboards.workouts import CYCLING, RUNNING, SWIMMING, build

VO2 = [
    PlannedStep(8, 0.45, 0.60, "Warm-up, building"), PlannedStep(2, 0.60, 0.70, "Warm-up, steady"),
    PlannedStep(3, 1.10, 1.20, "VO₂max"), PlannedStep(3, 0.45, 0.55, "Easy"),
    PlannedStep(3, 1.10, 1.20, "VO₂max"), PlannedStep(3, 0.45, 0.55, "Easy"),
    PlannedStep(5, 0.40, 0.55, "Cool-down"),
]


def steps_of(payload):
    return payload["workoutSegments"][0]["workoutSteps"]


def test_step_types_follow_position_and_label():
    W, C, I, R = StepType.WARMUP, StepType.COOLDOWN, StepType.INTERVAL, StepType.RECOVERY
    assert step_types(VO2) == [W, W, I, R, I, R, C]
    # A steady endurance block is the main work even though its label says "easy".
    endurance = [PlannedStep(5, 0.5, 0.6, "Warm-up"), PlannedStep(40, 0.72, 0.82, "Easy run"), PlannedStep(5, 0.5, 0.65, "Cool-down jog or walk")]
    assert step_types(endurance) == [W, I, C]


def test_cycling_targets_are_watts_and_durations_add_up():
    payload = to_garmin("VO₂max 5×3", "cycling", VO2, ftp=252)
    CyclingWorkout.model_validate(payload)
    steps = steps_of(payload)
    assert (steps[2]["targetValueOne"], steps[2]["targetValueTwo"]) == (277, 302)
    assert steps[0]["targetValueOne"] == 113  # warm-ups keep their targets
    assert payload["estimatedDurationInSecs"] == 27 * 60
    assert [s["stepOrder"] for s in steps] == list(range(1, 8))


def test_running_pace_targets_from_threshold_pace():
    steps = [PlannedStep(10, 0.40, 0.50, "Walk"), PlannedStep(20, 0.80, 1.00, "Tempo")]
    payload = to_garmin("Run", "running", steps, ftp=252, threshold_pace_s=300)
    RunningWorkout.model_validate(payload)
    walk, tempo = steps_of(payload)
    assert walk["targetType"]["workoutTargetTypeKey"] == "no.target"
    # 300 s/km threshold: 80% of threshold speed = 375 s/km = 2.667 m/s; 100% = 3.333 m/s.
    assert tempo["targetType"]["workoutTargetTypeKey"] == "pace.zone"
    assert (tempo["targetValueOne"], tempo["targetValueTwo"]) == (2.667, 3.333)


def test_running_without_threshold_pace_has_no_targets():
    payload = to_garmin("Run", "running", VO2, ftp=252)
    assert {s["targetType"]["workoutTargetTypeKey"] for s in steps_of(payload)} == {"no.target"}


def test_gym_needs_its_own_converter():
    with pytest.raises(ValueError):
        to_garmin("Lift", "gym", VO2, ftp=252)


@pytest.mark.parametrize("profile", [CYCLING, RUNNING, SWIMMING])
def test_every_library_workout_converts(profile):
    for workout in profile.library.values():
        built = build(workout, 75, profile)
        steps = [PlannedStep(s.seconds / 60, s.low, s.high, s.label) for s in built]
        pace = 110 if profile is SWIMMING else 300
        payload = to_garmin(workout.name, profile.key, steps, ftp=252, threshold_pace_s=pace)
        kinds = step_types(steps)
        assert kinds[0] == StepType.WARMUP and kinds[-1] == StepType.COOLDOWN
        assert payload["estimatedDurationInSecs"] == pytest.approx(75 * 60, abs=len(steps))


# ------------------------------------------------------------------ swimming

SWIM = [
    PlannedStep(5, 0.65, 0.75, "Warm-up, easy swim"),
    PlannedStep(4, 0.97, 1.03, "Threshold"), PlannedStep(0.75, 0.0, 0.3, "Rest"),
    PlannedStep(3, 0.60, 0.70, "Easy swim cool-down"),
]


def test_swim_steps_become_pool_lengths_and_rests_stay_timed():
    payload = to_garmin("Threshold", "swimming", SWIM, ftp=252, threshold_pace_s=100, pool_length_m=25)
    SwimmingWorkout.model_validate(payload)
    warm, main, rest, cool = steps_of(payload)
    assert payload["poolLength"] == 25
    # 4 min at threshold (1:40/100 m) = 240 m, rounded to whole 50 m there-and-backs = 250 m.
    assert main["endCondition"]["conditionTypeKey"] == "distance" and main["endConditionValue"] == 250
    assert "1:37–1:43 /100 m" in main["description"]
    assert rest["stepType"]["stepTypeKey"] == "rest"
    assert (rest["endCondition"]["conditionTypeKey"], rest["endConditionValue"]) == ("time", 45)
    assert warm["stepType"]["stepTypeKey"] == "warmup" and cool["stepType"]["stepTypeKey"] == "cooldown"
    assert all(s["endConditionValue"] % 50 == 0 for s in (warm, main, cool))
    assert payload["estimatedDurationInSecs"] == round((5 + 4 + 0.75 + 3) * 60)


def test_swim_distance_rounding():
    assert swim_distance_m(PlannedStep(0.25, 0.97, 1.03, "Sprint"), 100, 25) == 50  # at least one there-and-back
    assert swim_distance_m(PlannedStep(10, 1, 1, "Steady"), 100, 50) == 600


def test_swim_without_threshold_pace_stays_time_based():
    payload = to_garmin("Swim", "swimming", SWIM, ftp=252)
    assert {s["endCondition"]["conditionTypeKey"] for s in steps_of(payload)} == {"time"}


# ------------------------------------------------------------------ gym

CATALOG = {(e["category"], e["exercise"]) for e in EXERCISES}


def test_every_mapping_is_a_real_garmin_exercise():
    assert all((category, exercise) in CATALOG for _, category, exercise in EXERCISE_RULES)
    assert set(GROUP_CATEGORY.values()) <= set(CATEGORIES)


def test_every_app_exercise_has_a_specific_garmin_exercise():
    for e in (e for s in (*gym.CANDIDATES, gym.MOBILITY) for e in s.exercises):
        category, exercise = garmin_exercise(e.name, e.group)
        # Back in gym.last_trained, the session must count for the same muscle group, and mobility for none.
        if e.group == "mobility":
            assert category not in gym.CATEGORY_GROUPS, e.name
        else:
            assert exercise, f"{e.name} only maps to a category"
            assert e.group in gym.CATEGORY_GROUPS.get(category, ()) or category == "WARM_UP", e.name  # stretches aren't counted


def test_unknown_exercises_fall_back_to_their_group():
    assert garmin_exercise("Kettlebell swing", "legs") == ("SQUAT", "")
    assert garmin_exercise("Mystery move", "") == ("TOTAL_BODY", "")
    assert garmin_exercise("Glute bridge", "mobility") == ("WARM_UP", "")  # not counted as a leg session
    assert garmin_exercise("Glute bridge", "legs") == ("HIP_RAISE", "HIP_RAISE")


def test_parse_reps():
    assert parse_reps("4–6") == (6, False)
    assert parse_reps("30–45 s") == (45, True)
    assert parse_reps("10 each side") == (10, False)
    assert parse_reps("AMRAP") == (10, False)


def test_strength_workout_has_warmup_then_set_blocks():
    exercises = [
        PlannedExercise("Goblet or back squat", "legs", 3, "4–6", 8, 150, "Stop 2 reps short."),
        PlannedExercise("Side plank (each side)", "core", 2, "30–45 s", 7, 45),
    ]
    payload = to_garmin_strength("Lower body", exercises, warmup_min=6)
    StrengthWorkout.model_validate(payload)
    warm, squat, plank = steps_of(payload)
    assert warm["endConditionValue"] == 360
    assert squat["numberOfIterations"] == 3 and plank["numberOfIterations"] == 2
    lift, rest = squat["workoutSteps"]
    assert (lift["category"], lift["exerciseName"], lift["endConditionValue"]) == ("SQUAT", "GOBLET_SQUAT", 6)
    assert lift["description"] == "Goblet or back squat · 4–6 reps · RPE 8 · Stop 2 reps short."
    assert rest["endConditionValue"] == 150
    hold = plank["workoutSteps"][0]
    assert (hold["endCondition"]["conditionTypeKey"], hold["endConditionValue"]) == ("time", 45)
    orders = [warm["stepOrder"], squat["stepOrder"], *(s["stepOrder"] for s in squat["workoutSteps"]),
              plank["stepOrder"], *(s["stepOrder"] for s in plank["workoutSteps"])]
    assert len(orders) == len(set(orders))
    assert payload["estimatedDurationInSecs"] == 360 + 3 * (45 + 150) + 2 * (45 + 45)


def test_every_gym_suggestion_converts():
    for session in (*gym.CANDIDATES, gym.MOBILITY):
        rows = [gym._row(e, "strength", "intermediate", False) for e in session.exercises]
        payload = to_garmin_strength(session.name, [PlannedExercise(**r) for r in rows])
        StrengthWorkout.model_validate(payload)
        assert len(steps_of(payload)) == len(rows)
