from datetime import date

from garmin_dashboards.readiness import Readiness
from garmin_dashboards.workouts import LIBRARY, build, suggest

TODAY = date(2026, 11, 8)


def ready(level):
    return Readiness(level=level, score={"green": 90, "amber": 60, "red": 20}[level], headline="h")


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
