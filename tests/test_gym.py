from datetime import date

from garmin_dashboards import gym
from garmin_dashboards.metrics import strength_sessions
from garmin_dashboards.readiness import Readiness

TODAY = date(2026, 11, 11)
NEVER = {"days": {g: None for g in gym.GROUPS}, "unlabelled": False}


def ready(level):
    return Readiness(level=level, score={"green": 90, "amber": 60, "red": 40}[level], headline="h")


def trained(**days):
    return {"days": {g: days.get(g) for g in gym.GROUPS}, "unlabelled": False}


def sets(category, n=3):
    return {"category": category, "sets": n, "reps": n * 8}


def test_last_trained_reads_garmin_categories():
    sessions = [
        {"day": "2026-11-05", "sets": [sets("SQUAT"), sets("PLANK")]},
        {"day": "2026-11-09", "sets": [sets("BENCH_PRESS", 7), sets("ROW", 1)]},  # one row set is not enough
    ]
    got = gym.last_trained(sessions, TODAY)
    assert got["days"] == {"legs": 6, "push": 2, "pull": None, "core": 6}
    assert not got["unlabelled"]


def test_unlabelled_session_counts_as_full_body():
    got = gym.last_trained([{"day": "2026-11-10", "sets": [sets("UNKNOWN")]}], TODAY)
    assert set(got["days"].values()) == {1} and got["unlabelled"]


def test_picks_the_session_that_rested_longest():
    s = gym.suggest(ready("green"), 60, [], trained(legs=1, push=6, pull=6, core=1), TODAY)
    assert s.session.key == "upper"
    s = gym.suggest(ready("green"), 60, [], trained(legs=7, push=1, pull=1, core=4), TODAY, "strength")
    assert s.session.key == "lower"


def test_hard_ride_yesterday_blocks_heavy_legs():
    hard = [{"day": "2026-11-10", "hard_min": 30, "load": 180}]
    s = gym.suggest(ready("green"), 60, hard, trained(legs=9, push=9, pull=9, core=9), TODAY)
    assert not any(e["group"] == "legs" for e in s.exercises)
    assert any("hard yesterday" in r for r in s.rationale)


def test_endurance_goal_favours_legs_and_keeps_reps_in_reserve():
    s = gym.suggest(ready("green"), 60, [], NEVER, TODAY, "endurance", "intermediate")
    assert s.session.key == "lower"
    assert "2 reps before failure" in s.exercises[0]["note"]


def test_red_gives_mobility_and_very_low_scores_rest():
    s = gym.suggest(ready("red"), 90, [], NEVER, TODAY)
    assert s.session.key == "mobility" and s.minutes <= gym.RED_CAP_MINUTES
    rest = gym.suggest(Readiness("red", 20, [], "h"), 60, [], NEVER, TODAY)
    assert rest.session.key == "rest" and rest.exercises == []


def test_session_fits_time_and_level():
    short = gym.suggest(ready("green"), 30, [], NEVER, TODAY, "strength", "intermediate")
    long = gym.suggest(ready("green"), 75, [], NEVER, TODAY, "strength", "intermediate")
    assert 2 <= len(short.exercises) < len(long.exercises)
    assert short.minutes <= 32
    beginner = gym.suggest(ready("green"), 90, [], NEVER, TODAY, "strength", "beginner")
    assert beginner.minutes <= 45 and all(e["sets"] <= 3 for e in beginner.exercises)


def test_amber_trims_volume():
    green = gym.suggest(ready("green"), 60, [], NEVER, TODAY, "strength")
    amber = gym.suggest(ready("amber"), 60, [], NEVER, TODAY, "strength")
    assert amber.exercises[0]["sets"] < green.exercises[0]["sets"]
    assert amber.exercises[0]["rpe"] < green.exercises[0]["rpe"]


def test_goal_and_level_settings():
    assert gym.goal_from_setting(None) == "strength" and gym.goal_from_setting(2) == "endurance"
    assert gym.assess_level(3, 0)["level"] == "advanced"
    assert gym.assess_level(None, 6)["level"] == "beginner"
    assert gym.assess_level(None, 36)["level"] == "advanced"


def test_strength_sessions_are_read_from_the_stored_activity(tmp_path):
    from garmin_dashboards.db import init_db, save_activities
    path = str(tmp_path / "t.db")
    init_db(path)
    save_activities([{
        "activityId": 1, "activityName": "Strength", "activityType": {"typeKey": "strength_training"},
        "startTimeLocal": "2026-11-09 10:00:00",
        "summarizedExerciseSets": [{"category": "SQUAT", "sets": 4, "reps": 24, "maxWeight": 80000}],
    }], "me@example.com", path)
    got = strength_sessions(date(2026, 11, 1), TODAY, path)
    assert got == [{"day": "2026-11-09", "sets": [{"category": "SQUAT", "sets": 4, "reps": 24, "max_weight_kg": 80.0}]}]


def test_chosen_focus_is_honoured_even_when_another_session_is_fresher():
    rested = trained(legs=1, push=8, pull=8, core=8)  # auto would pick upper
    assert gym.suggest(ready("green"), 60, [], rested, TODAY).session.key == "upper"
    s = gym.suggest(ready("green"), 60, [], rested, TODAY, focus="core")
    assert s.session.key == "core" and any("as you chose" in r for r in s.rationale)


def test_chosen_legs_after_a_hard_ride_are_trimmed_with_a_warning():
    hard = [{"day": "2026-11-10", "hard_min": 30, "load": 180}]
    fresh = trained(legs=9, push=9, pull=9, core=9)
    normal = gym.suggest(ready("green"), 60, [], fresh, TODAY, focus="lower")
    tired = gym.suggest(ready("green"), 60, hard, fresh, TODAY, focus="lower")
    assert tired.session.key == "lower" and tired.exercises[0]["sets"] < normal.exercises[0]["sets"]
    assert any("aren't fully recovered" in r for r in tired.rationale)


def test_focus_does_not_override_a_red_day():
    s = gym.suggest(ready("red"), 60, [], NEVER, TODAY, focus="lower")
    assert s.session.key == "mobility"
