from datetime import date, timedelta

from garmin_dashboards.readiness import Readiness
from garmin_dashboards.workouts import LIBRARY, build, suggest

TODAY = date(2026, 11, 8)


def ready(level):
    return Readiness(level=level, score={"green": 90, "amber": 60, "red": 40}[level], headline="h")


def test_build_fills_exact_time():
    for key in LIBRARY:
        for minutes in (30, 45, 60, 90):
            steps = build(LIBRARY[key], minutes)
            assert sum(s.seconds for s in steps) == minutes * 60, (key, minutes)


def test_build_respects_rep_limits():
    steps = build(LIBRARY["threshold"], 180)
    assert sum(s.label == "Threshold" for s in steps) == 5
    assert sum(s.label == "Threshold" for s in build(LIBRARY["threshold"], 40)) == 0


def test_watts_scale_with_ftp():
    s = suggest(ready("amber"), 60, [], TODAY)
    endurance = [w for w in s.in_watts(252) if w["label"] == "Endurance"][0]
    assert (endurance["watts_low"], endurance["watts_high"]) == (151, 181)


def test_level_picks_workout():
    assert suggest(ready("amber"), 60, [], TODAY).workout.key == "endurance"
    red = suggest(ready("red"), 120, [], TODAY)
    assert red.workout.key == "recovery" and red.minutes == 45
    assert suggest(ready("green"), 60, [], TODAY).workout.key == "vo2max"  # no recent intensity


def test_green_after_recent_intensity():
    hard_yesterday = [{"day": "2026-11-07", "hard_min": 30, "load": 180}]
    assert suggest(ready("green"), 60, hard_yesterday, TODAY).workout.key == "tempo"
    hard_last_week = [{"day": "2026-11-03", "hard_min": 20}]
    assert suggest(ready("green"), 60, hard_last_week, TODAY).workout.key == "threshold"
    assert suggest(ready("green"), 45, hard_last_week, TODAY).workout.key == "sweet_spot"


def test_overworked_red_day_suggests_rest():
    from garmin_dashboards.readiness import BAD, Signal
    r = Readiness(level="red", score=30, headline="h", signals=[Signal("HRV", BAD, "x", ""), Signal("Sleep", BAD, "y", "")])
    s = suggest(r, 60, [], TODAY)
    assert s.workout.key == "rest" and s.steps == [] and s.minutes == 0


def test_red_after_repeated_hard_days_with_bad_form_is_rest_but_plain_red_is_not():
    from garmin_dashboards.readiness import BAD, CAUTION, Signal
    hard = [{"day": (TODAY - timedelta(days=n)).isoformat(), "load": 200} for n in (1, 2)]
    r = Readiness(level="red", score=40, headline="h", signals=[Signal("Form", BAD, "-30", ""), Signal("Sleep", CAUTION, "", "")])
    assert suggest(r, 60, hard, TODAY).workout.key == "rest"
    assert suggest(r, 60, [], TODAY).workout.key == "recovery"


# ---------------------------------------------------------------- running and levels

from garmin_dashboards.workouts import RUN_LIBRARY, RUNNING, SWIMMING, SWIM_LIBRARY, assess_level


def test_running_workouts_fill_exact_time():
    for key in RUN_LIBRARY:
        for minutes in (20, 30, 45, 60, 90):
            steps = build(RUN_LIBRARY[key], minutes, RUNNING)
            assert sum(s.seconds for s in steps) == minutes * 60, (key, minutes)


def test_running_suggestion_uses_running_workouts():
    s = suggest(ready("amber"), 45, [], TODAY, "running", "intermediate")
    assert s.sport == "running" and s.workout.key == "endurance" and s.workout.name == "Easy run"
    assert all("pedal" not in x.label.lower() for x in s.steps)
    green = suggest(ready("green"), 60, [], TODAY, "running", "intermediate")
    assert green.workout.key == "vo2max" and green.workout.name.startswith("VO₂max")


def test_same_readiness_different_level_per_sport():
    """A strong cyclist who is a beginner runner gets hard intervals on the bike but run/walk on foot."""
    bike = suggest(ready("amber"), 60, [], TODAY, "cycling", "advanced")
    run = suggest(ready("amber"), 60, [], TODAY, "running", "beginner")
    assert bike.workout.key == "endurance" and run.workout.key == "run_walk"
    assert any(s.label == "Walk" for s in run.steps)
    green_run = suggest(ready("green"), 60, [], TODAY, "running", "beginner")
    green_bike = suggest(ready("green"), 60, [], TODAY, "cycling", "advanced")
    assert green_run.workout.key == "fartlek" and green_bike.workout.key == "vo2max"


def test_level_caps_length_and_scales_reps():
    assert suggest(ready("amber"), 120, [], TODAY, "running", "beginner").minutes == 40
    assert suggest(ready("amber"), 120, [], TODAY, "cycling", "beginner").minutes == 75
    assert suggest(ready("red"), 90, [], TODAY, "running", "advanced").minutes == 30
    hard_last_week = [{"day": "2026-11-03", "hard_min": 20}]

    def reps(level):
        steps = suggest(ready("green"), 180, hard_last_week, TODAY, "cycling", level).steps
        return sum(s.label == "Threshold" for s in steps)

    assert reps("beginner") < reps("intermediate") < reps("advanced") or reps("beginner") <= 2


def test_beginner_cyclist_is_not_given_vo2max_or_threshold():
    s = suggest(ready("green"), 60, [], TODAY, "cycling", "beginner")
    assert s.workout.key == "tempo"


def test_running_targets_have_effort_and_optional_pace():
    s = suggest(ready("amber"), 60, [], TODAY, "running", "intermediate")
    easy = [t for t in s.in_targets(252, 300) if t["label"] == "Easy run"][0]
    assert (easy["pace_fast"], easy["pace_slow"]) == ("6:06", "6:57") and easy["rpe"] == "3–4"
    assert all("pace_fast" not in t for t in s.in_targets(252, None))


def evidence(hours=0.0, longest=0.0, sessions=0):
    return {"weekly_hours": hours, "longest_min_180d": longest, "sessions_180d": sessions}


def test_level_is_set_or_judged_from_several_signals():
    assert assess_level("running", 3, evidence(), 10)["level"] == "advanced"
    assert assess_level("running", 3, evidence(), 10)["source"] == "set"
    # Rarely runs, so volume alone says beginner...
    assert assess_level("running", None, evidence(0.1), 10)["level"] == "beginner"
    # ...but a 72-minute run in the last six months shows intermediate capacity.
    assert assess_level("running", None, evidence(0.1, 72, 2), 10)["level"] == "intermediate"
    # Strong overall fitness plus recent running lifts a low-volume runner to intermediate, never higher.
    got = assess_level("running", None, evidence(0.2, 20, 2), 80)
    assert got["level"] == "intermediate" and any("Overall fitness" in r for r in got["reasons"])
    # No recent running: aerobic fitness alone does not count.
    assert assess_level("running", None, evidence(), 80)["level"] == "beginner"
    # Swimming is technique-limited, so a fit non-swimmer stays a beginner.
    assert assess_level("swimming", None, evidence(0.3, 23, 3), 80)["level"] == "beginner"
    assert assess_level("cycling", None, evidence(9, 300, 20), 80)["level"] == "advanced"
    assert assess_level("cycling", None, evidence(5), 10)["level"] == "intermediate"


def test_swimming_suggestions_and_pace_per_100m():
    for key in SWIM_LIBRARY:
        for minutes in (20, 30, 45, 60):
            assert sum(s.seconds for s in build(SWIM_LIBRARY[key], minutes, SWIMMING)) == minutes * 60
    s = suggest(ready("amber"), 90, [], TODAY, "swimming", "intermediate")
    assert s.workout.key == "endurance" and s.minutes == 60  # capped at the intermediate swim length
    steady = [t for t in s.in_targets(252, 100) if t["label"] == "Steady swim"][0]
    assert steady["pace_unit"] == "/100 m" and (steady["pace_fast"], steady["pace_slow"]) == ("1:54", "2:08")
    beginner = suggest(ready("green"), 40, [], TODAY, "swimming", "beginner")
    assert beginner.workout.key == "technique"
    rest = [t for t in beginner.in_targets(252, 100) if t["label"].startswith("Rest")]
    assert rest and all("pace_fast" not in t for t in rest)
