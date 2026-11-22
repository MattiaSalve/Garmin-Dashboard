from datetime import date, timedelta

from garmin_dashboards import gym
from garmin_dashboards import plan as P
from garmin_dashboards.readiness import BAD, Readiness, Signal

TODAY = date(2026, 11, 13)  # a Friday
MON = date(2026, 11, 16)
RATES = P.load_rates([])
LEVELS = {"cycling": "intermediate", "running": "intermediate", "swimming": "intermediate", "gym": "intermediate"}


def profile(sports=None, hours=7, sessions=5, long_day=5):
    return P.Profile(sports or {"cycling": 1.0}, hours, sessions, long_day, MON.isoformat())


def week(phase="build", hours=7, sessions=5, cap=10_000, start=MON):
    return P.WeekTarget(start.isoformat(), phase, hours, sessions, cap)


def ready(level="green", score=90, form_bad=False):
    signals = [Signal("Form", BAD, "", "")] if form_bad else []
    return Readiness(level, score, signals, f"Readiness {level}.")


def by_date(days):
    return {date.fromisoformat(p.day): p for p in days if p.slot == 0}


# ------------------------------------------------------------------ load rates


def test_load_rates_use_own_history_and_fall_back_when_thin():
    acts = [{"sport_type": "road_biking", "minutes": 120, "load": 116, "hard_min": 2} for _ in range(4)]
    acts += [{"sport_type": "indoor_cycling", "minutes": 60, "load": 105, "hard_min": 20} for _ in range(3)]
    acts += [{"sport_type": "running", "minutes": 40, "load": 70, "hard_min": 0}]  # one run: too few
    acts += [{"sport_type": "strength_training", "minutes": 45, "load": 11, "hard_min": 0} for _ in range(3)]
    acts += [{"sport_type": "hiking", "minutes": 200, "load": 90, "hard_min": 0}]  # not a plan sport
    r = P.load_rates(acts)
    assert r["cycling"]["easy"] == 58 and r["cycling"]["hard"] == 105
    assert r["cycling"]["easy"] <= r["cycling"]["moderate"] <= r["cycling"]["hard"]
    assert r["running"] == P.DEFAULT_RATES["running"]
    assert r["gym"]["easy"] == r["gym"]["hard"] == 14.7


def test_missing_class_is_scaled_from_the_measured_one():
    acts = [{"sport_type": "road_biking", "minutes": 60, "load": 50, "hard_min": 0} for _ in range(5)]
    r = P.load_rates(acts)
    assert r["cycling"]["easy"] == 50
    assert r["cycling"]["hard"] == round(50 * 105 / 60, 1)


# ------------------------------------------------------------------ phases and weekly targets


def test_event_gets_3_to_1_blocks_a_two_week_taper_and_race_week():
    event = MON + timedelta(weeks=10, days=6)  # a Sunday, 11 weeks from this week
    goal = P.Goal("event", "cycling", event.isoformat(), 4)
    weeks, days = P.build_plan(TODAY, 45, profile(), [goal], LEVELS, RATES, 6)
    phases = [w.phase for w in weeks]
    assert phases[-3:] == ["taper", "taper", "race"]
    assert phases[-4] != "recovery" or phases[-5] == "build"
    builds_in_a_row = max(len(run) for run in "".join("b" if p == "build" else " " for p in phases).split())
    assert builds_in_a_row <= 3 and "recovery" in phases
    assert [w.detailed for w in weeks].count(True) == P.HORIZON_WEEKS and len(weeks) == 12
    assert weeks[-3].hours > weeks[-2].hours > 0
    assert not any(p.day > (TODAY + timedelta(weeks=4)).isoformat() for p in days)


def test_event_day_is_placed_when_the_race_is_within_the_horizon():
    event = MON + timedelta(days=13)  # Sunday next week
    goal = P.Goal("event", "cycling", event.isoformat(), 3, "Club race")
    _, days = P.build_plan(TODAY, 45, profile(), [goal], LEVELS, RATES, 6)
    race = [p for p in days if p.kind == "event"]
    assert len(race) == 1 and race[0].day == event.isoformat() and race[0].minutes == 180
    assert not any(p.day == (event - timedelta(days=1)).isoformat() for p in days)
    hard_near = [p for p in days if p.kind in P.HARD_KINDS and 0 < (event - date.fromisoformat(p.day)).days < 3]
    assert not hard_near


def test_fitness_ramp_stays_under_the_warning():
    weeks, _ = P.build_plan(TODAY, 40, profile(hours=15, sessions=6), [], {"cycling": "advanced"}, RATES, 14)
    ctl = 40
    for w in weeks:
        new = P.advance_fitness(ctl, w.planned_load)
        assert new - ctl <= P.RAMP_WARNING + 0.01
        ctl = new


def test_volume_goal_ramps_at_most_10_percent_a_week():
    goal = P.Goal("volume", None, None, 10)
    weeks, _ = P.build_plan(TODAY, 30, profile(hours=12), [goal], LEVELS, RATES, 4, horizon=8)
    builds = [w.hours for w in weeks if w.phase == "build"]
    assert builds[0] <= 5.0
    for a, b in zip(builds, builds[1:]):
        assert b <= (a + 0.5) * 1.1  # shown rounded down to half hours
    assert max(builds) <= 10


def test_a_busy_week_override_caps_its_hours():
    overrides = {MON.isoformat(): 3}
    weeks, days = P.build_plan(TODAY, 45, profile(), [], LEVELS, RATES, 7, overrides)
    w = next(w for w in weeks if w.week_start == MON.isoformat())
    assert w.hours == 3
    in_week = [p for p in days if MON.isoformat() <= p.day <= (MON + timedelta(days=6)).isoformat()]
    assert sum(p.minutes for p in in_week) <= 3 * 60 + 30


def test_phase_cycle_without_a_goal_is_three_build_one_recovery():
    phases = [P.phase_for(MON + timedelta(weeks=i), None, MON)[0] for i in range(8)]
    assert phases == ["build", "build", "build", "recovery"] * 2


# ------------------------------------------------------------------ distributing a week


def test_week_matches_sessions_and_hours_with_spacing_rules():
    days = P.distribute_week(week(), profile(), None, LEVELS, RATES, MON)
    assert len(days) == 5
    assert abs(sum(p.minutes for p in days) - 420) <= 30
    d = by_date(days)
    long = [x for x, p in d.items() if p.kind == "long"]
    assert long == [MON + timedelta(days=5)]
    hard = sorted(x for x, p in d.items() if p.kind in P.HARD_KINDS)
    assert len(hard) == 2
    assert all((b - a).days > 1 for a, b in zip(hard, hard[1:]))
    assert all(abs((h - long[0]).days) > 1 for h in hard)


def test_only_the_chosen_sports_appear_and_gym_avoids_the_day_before_key_sessions():
    prof = profile({"cycling": 0.6, "gym": 0.4}, hours=7, sessions=6)
    days = P.distribute_week(week(sessions=6), prof, None, LEVELS, RATES, MON)
    assert {p.sport for p in days} == {"cycling", "gym"}
    assert sum(p.sport == "gym" for p in days) == 2
    key = {date.fromisoformat(p.day) for p in days if p.kind in P.HARD_KINDS or p.kind == "long"}
    for p in days:
        if p.sport == "gym" and p.kind in ("lower", "full"):
            assert date.fromisoformat(p.day) + timedelta(days=1) not in key


def test_three_sports_split_by_share():
    prof = profile({"cycling": 0.5, "running": 0.3, "swimming": 0.2}, hours=8, sessions=6)
    days = P.distribute_week(week(hours=8, sessions=6), prof, None, LEVELS, RATES, MON)
    counts = {s: sum(p.sport == s for p in days) for s in ("cycling", "running", "swimming")}
    assert counts == {"cycling": 3, "running": 2, "swimming": 1}
    assert all(p.kind in P.KIND_CLASS for p in days)


def test_fixed_sessions_are_kept_and_filled_around():
    locked = P.PlanDay((MON + timedelta(days=1)).isoformat(), 0, "cycling", "endurance", 90, 90, "user", True)
    rest = P.PlanDay((MON + timedelta(days=3)).isoformat(), 0, "rest", "rest", 0, 0, "user")
    days = P.distribute_week(week(), profile(), None, LEVELS, RATES, MON, [locked, rest])
    assert not any(p.day in (locked.day, rest.day) for p in days)
    assert len(days) == 4
    assert abs(sum(p.minutes for p in days) + 90 - 420) <= 30


def test_beginners_get_one_hard_session_and_beginner_kinds():
    levels = {"running": "beginner"}
    days = P.distribute_week(week(hours=3, sessions=4), profile({"running": 1}, 3, 4), None, levels, RATES, MON)
    hard = [p for p in days if p.kind in P.HARD_KINDS]
    assert len(hard) == 1 and hard[0].kind == "fartlek"
    assert {p.kind for p in days} <= {"fartlek", "run_walk", "long"}


def test_recovery_week_has_no_hard_sessions_and_taper_one_short_sharpener():
    rec = P.distribute_week(week("recovery", hours=4), profile(), None, LEVELS, RATES, MON)
    assert not any(p.kind in P.HARD_KINDS for p in rec)
    taper = P.distribute_week(week("taper", hours=5), profile(), None, LEVELS, RATES, MON)
    sharp = [p for p in taper if p.kind in P.HARD_KINDS]
    assert len(sharp) == 1 and sharp[0].minutes <= P.SHARPENER_CAP["cycling"]


def test_mid_week_only_fills_the_days_left_and_counts_what_was_done():
    wed = MON + timedelta(days=2)
    done = {MON.isoformat(): {"minutes": 90, "load": 90, "sports": {"cycling": 90}},
            (MON + timedelta(days=1)).isoformat(): {"minutes": 60, "load": 100, "sports": {"cycling": 60}}}
    days = P.distribute_week(week(), profile(), None, LEVELS, RATES, wed, done=done)
    assert all(p.day >= wed.isoformat() for p in days)
    assert len(days) == 3
    assert sum(p.minutes for p in days) <= 420 - 150 + 15


def test_load_cap_shortens_the_week():
    roomy = P.distribute_week(week(), profile(), None, LEVELS, RATES, MON)
    capped = P.distribute_week(week(cap=300), profile(), None, LEVELS, RATES, MON)
    assert sum(p.planned_load for p in capped) <= 300 + 15 < sum(p.planned_load for p in roomy)


def test_regenerate_keeps_user_sessions_and_replaces_auto_ones():
    _, first = P.build_plan(TODAY, 45, profile(), [], LEVELS, RATES, 6)
    edited = P.PlanDay((MON + timedelta(days=2)).isoformat(), 0, "cycling", "tempo", 70, 0, "user")
    existing = [p for p in first if p.day != edited.day] + [edited]
    _, again = P.build_plan(TODAY, 45, profile(), [], LEVELS, RATES, 6, existing=existing)
    assert not any(p.day == edited.day and p.slot == 0 for p in again)


def test_top_up_leaves_weeks_that_already_have_sessions():
    _, first = P.build_plan(TODAY, 45, profile(), [], LEVELS, RATES, 6, horizon=2)
    _, more = P.build_plan(TODAY, 45, profile(), [], LEVELS, RATES, 6, existing=first, only_empty_weeks=True)
    assert more and min(p.day for p in more) >= (MON + timedelta(weeks=1)).isoformat()


# ------------------------------------------------------------------ setup checks


def test_setup_errors():
    assert P.setup_errors(profile(), [P.Goal("general")], TODAY) == []
    bad = P.Profile({"cycling": 1, "rowing": 1}, 0.5, 12, 9)
    errors = P.setup_errors(bad, [P.Goal("event"), P.Goal("performance", "running", None, 5000)], TODAY)
    text = " ".join(errors)
    for part in ("rowing", "Hours per week", "Sessions per week", "long-session day", "needs a date",
                 "running isn't one of your chosen sports", "between 120 and 900"):
        assert part in text
    past = P.Goal("event", "cycling", (TODAY - timedelta(days=1)).isoformat())
    assert P.setup_errors(profile(), [past], TODAY)


# ------------------------------------------------------------------ reconcile


def two_weeks_plan():
    """A plan for this week and next made by the planner, starting on Monday."""
    _, days = P.build_plan(MON, 45, profile(), [], LEVELS, RATES, 7, horizon=2)
    return days


def test_four_hours_instead_of_two_softens_the_next_hard_session_and_trims():
    days = two_weeks_plan()
    sat = MON + timedelta(days=5)
    long = next(p for p in days if p.day == sat.isoformat())
    long.minutes, long.planned_load = 120, 120
    actual = {sat.isoformat(): {"minutes": 240, "load": 290, "sports": {"cycling": 240}}}
    proposals = P.reconcile(days, actual, sat + timedelta(days=1), RATES, LEVELS)
    assert [p.kind for p in proposals] == ["overshoot"]
    prop = proposals[0]
    assert prop.key == f"overshoot:{sat.isoformat()}"
    assert "4 h" in prop.reason and "2 h" in prop.reason
    first = prop.changes[0]
    assert first["before"]["kind"] in P.HARD_KINDS and first["after"]["kind"] == "endurance"
    assert any(c["after"]["minutes"] < c["before"]["minutes"] for c in prop.changes[1:])
    for c in prop.changes:
        assert c["after"]["minutes"] >= c["before"]["minutes"] * (1 - P.TRIM_MAX) - 5


def test_small_deviations_give_no_proposal():
    days = two_weeks_plan()
    actual = {p.day: {"minutes": p.minutes + 10, "load": p.planned_load + 15} for p in days if p.day < "2026-11-20"}
    assert P.reconcile(days, actual, date(2026, 11, 20), RATES, LEVELS) == []


def test_unplanned_big_day_counts_as_overshoot():
    days = two_weeks_plan()
    rest_day = next(MON + timedelta(days=i) for i in range(7)
                    if not any(p.day == (MON + timedelta(days=i)).isoformat() for p in days))
    actual = {rest_day.isoformat(): {"minutes": 180, "load": 200}}
    props = P.reconcile(days, actual, rest_day + timedelta(days=1), RATES, LEVELS)
    assert props and "rest day" in props[0].reason


def test_missed_threshold_is_moved_when_spacing_allows():
    d = lambda i: (MON + timedelta(days=i)).isoformat()  # noqa: E731
    days = [P.PlanDay(d(1), 0, "cycling", "threshold", 60, 105), P.PlanDay(d(2), 0, "rest", "rest", 0, 0, "user"),
            P.PlanDay(d(3), 0, "cycling", "endurance", 60, 60), P.PlanDay(d(4), 0, "cycling", "endurance", 60, 60),
            P.PlanDay(d(5), 0, "cycling", "long", 180, 180)]
    props = P.reconcile(days, {}, MON + timedelta(days=2), RATES, LEVELS)
    assert [p.kind for p in props] == ["missed"] and props[0].key == f"missed:{d(1)}:0"
    change = props[0].changes[0]
    assert change["day"] == d(3)  # Friday would be the day before the long ride
    assert change["before"]["kind"] == "endurance" and change["after"]["kind"] == "threshold"


def test_missed_session_is_dropped_when_nothing_fits():
    tue = MON + timedelta(days=1)
    days = [P.PlanDay(tue.isoformat(), 0, "cycling", "threshold", 60, 105)]
    days += [P.PlanDay((MON + timedelta(days=i)).isoformat(), 0, "rest", "rest", 0, 0, "user") for i in range(2, 7)]
    assert P.reconcile(days, {}, tue + timedelta(days=1), RATES, LEVELS) == []


def test_poor_recovery_streak_lightens_the_coming_days():
    days = two_weeks_plan()
    today = MON + timedelta(days=1)
    levels = {today.isoformat(): "amber", MON.isoformat(): "red", (MON - timedelta(days=1)).isoformat(): "green"}
    props = P.reconcile(days, {MON.isoformat(): {"minutes": 0, "load": 0}}, today, RATES, LEVELS, levels)
    rec = [p for p in props if p.kind == "recovery"]
    assert rec and rec[0].key.startswith("recovery:")
    assert all(c["after"]["kind"] not in P.HARD_KINDS for c in rec[0].changes)
    recovered = {**levels, today.isoformat(): "green", (MON - timedelta(days=1)).isoformat(): "amber"}
    assert not P.reconcile(days, {MON.isoformat(): {"minutes": 0, "load": 0}}, today, RATES, LEVELS, recovered)


def test_same_situation_gives_the_same_key():
    days = two_weeks_plan()
    sat = MON + timedelta(days=5)
    actual = {sat.isoformat(): {"minutes": 300, "load": 350}}
    a = P.reconcile(days, actual, sat + timedelta(days=1), RATES, LEVELS)
    b = P.reconcile(days, actual, sat + timedelta(days=2), RATES, LEVELS)
    assert a and b and a[0].key == b[0].key


def test_stale_change_is_detected():
    p = P.PlanDay("2026-11-18", 0, "cycling", "threshold", 60, 105)
    change = P._change(p.day, 0, p, None)
    assert not P.is_stale(change, p)
    assert P.is_stale(change, P.PlanDay("2026-11-18", 0, "cycling", "threshold", 75, 120))
    assert P.is_stale(P._change(p.day, 0, None, p), p)


def test_day_status():
    p = [P.PlanDay("2026-11-16", 0, "cycling", "endurance", 60)]
    assert P.day_status(p, {"minutes": 55}, MON, TODAY + timedelta(days=7)) == "done"
    assert P.day_status(p, {"minutes": 25}, MON, TODAY + timedelta(days=7)) == "partial"
    assert P.day_status(p, None, MON, TODAY + timedelta(days=7)) == "missed"
    assert P.day_status(p, None, MON, MON) == "today"
    assert P.day_status([], {"minutes": 50}, MON, MON) == "extra"
    assert P.day_status(p, None, MON + timedelta(days=1), MON) == "planned"


# ------------------------------------------------------------------ today


def test_today_from_plan_follows_readiness():
    p = [P.PlanDay(TODAY.isoformat(), 0, "cycling", "threshold", 75, 130)]
    green = P.today_from_plan(p, ready("green"), [], TODAY)
    assert (green["kind"], green["changed"]) == ("threshold", False)
    amber = P.today_from_plan(p, ready("amber", 60), [], TODAY)
    assert (amber["kind"], amber["minutes"], amber["changed"]) == ("endurance", 75, True)
    assert "amber" in amber["reason"]
    red = P.today_from_plan(p, ready("red", 40), [], TODAY)
    assert (red["kind"], red["minutes"]) == ("recovery", 45)
    hard_days = [{"day": (TODAY - timedelta(days=i)).isoformat(), "load": 200, "hard_min": 30} for i in (1, 2)]
    rest = P.today_from_plan(p, ready("red", 40, form_bad=True), hard_days, TODAY)
    assert rest["kind"] == "rest"
    assert P.today_from_plan([], ready(), [], TODAY) is None


def test_plan_suggestion_builds_the_workout():
    trained = gym.last_trained([], TODAY)
    long = {"sport": "cycling", "kind": "long", "minutes": 150, "planned": {"label": "Long ride, 2 h 30"}}
    s = P.plan_suggestion(long, ready(), [], trained, TODAY, "intermediate")
    assert s.workout.name == "Long ride" and abs(s.minutes - 150) <= 5
    g = {"sport": "gym", "kind": "mobility", "minutes": 20, "planned": {"label": "Gym: mobility, 20 min"}}
    s = P.plan_suggestion(g, ready(), [], trained, TODAY, "intermediate")
    assert s.session.key == "mobility" and s.exercises


def test_describe():
    assert P.describe("cycling", "threshold", 75) == "Threshold ride, 1 h 15"
    assert P.describe("gym", "lower", 50) == "Gym: legs, 50 min"
    assert P.describe("rest", "rest") == "Rest day"


# ------------------------------------------------------------------ AI coach


def test_coach_summary_is_identifier_free_and_covers_two_weeks():
    import json
    goal = P.Goal("event", "cycling", (MON + timedelta(weeks=8)).isoformat(), 4, name="Gran Fondo Secret Name")
    weeks, days = P.build_plan(MON, 45, profile({"cycling": 0.8, "gym": 0.2}), [goal], LEVELS, RATES, 7)
    actual = {MON.isoformat(): {"minutes": 95, "load": 120, "activities": [{"name": "Morning ride in Milan"}]}}
    s = P.coach_summary(profile({"cycling": 0.8, "gym": 0.2}), [goal], weeks, days, actual, MON + timedelta(days=1),
                        [{"kind": "overshoot", "reason": "Big day.", "id": 3, "dedupe_key": "x"}])
    text = json.dumps(s)
    assert "Secret" not in text and "Milan" not in text and "dedupe" not in text
    assert s["goals"][0]["kind"] == "event" and s["goals"][0]["weeks_to_go"] == 7.9
    assert [w["week_start"] for w in s["weeks"]] == [MON.isoformat(), (MON + timedelta(weeks=1)).isoformat()]
    monday_ = s["weeks"][0]["days"][0]
    assert monday_["done_minutes"] == 95 and monday_["weekday"] == "Monday"
    assert s["profile"]["long_day"] == "Saturday" and s["open_proposals"] == [{"kind": "overshoot", "reason": "Big day."}]
    assert len(s["last_weeks_planned_vs_done"]) == 2 and "gym" in s["kinds_by_sport"]


def test_coach_changes_become_proposal_changes_and_bad_ones_are_skipped():
    tue, wed, thu = ((TODAY + timedelta(days=i)).isoformat() for i in (4, 5, 6))  # within next week
    rows = [
        P.PlanDay(tue, 0, "cycling", "threshold", 75, 100),
        P.PlanDay(wed, 0, "cycling", "endurance", 60, 60, locked=True),
        P.PlanDay(thu, 0, "rest", "rest", 0, 0),
    ]
    changes, skipped = P.coach_changes([
        {"day": tue, "sport": "cycling", "kind": "endurance", "minutes": 60, "note": "Too soon after the long ride."},
        {"day": wed, "sport": "rest", "kind": "rest"},                              # locked
        {"day": thu, "slot": 0, "sport": "gym", "kind": "core", "minutes": 30},      # fills a rest day
        {"day": (TODAY - timedelta(days=1)).isoformat(), "sport": "cycling", "kind": "endurance", "minutes": 60},
        {"day": thu, "slot": 1, "sport": "cycling", "kind": "lower", "minutes": 60},  # not a cycling kind
        {"day": (TODAY + timedelta(days=30)).isoformat(), "sport": "cycling", "kind": "endurance", "minutes": 60},
        {"day": "soon", "sport": "cycling", "kind": "endurance", "minutes": 60},
    ], rows, TODAY, RATES)
    assert [(c["day"], c["slot"]) for c in changes] == [(tue, 0), (thu, 0)]
    first, second = changes
    assert first["before"]["kind"] == "threshold" and first["after"]["kind"] == "endurance"
    assert first["after"]["note"] == "Too soon after the long ride." and first["after"]["planned_load"] > 0
    assert second["before"] is None and second["after"]["sport"] == "gym"
    assert len(skipped) == 5 and any("locked" in s for s in skipped)
    # Applying them would be judged against the same rows: nothing is stale.
    current = {(p.day, p.slot): p for p in rows}
    assert not any(P.is_stale(c, current.get((c["day"], c["slot"]))) for c in changes)


def test_coach_rest_clears_a_session_and_no_ops_are_dropped():
    tue = (TODAY + timedelta(days=4)).isoformat()
    rows = [P.PlanDay(tue, 0, "cycling", "threshold", 75, 100), P.PlanDay(tue, 1, "gym", "core", 30, 10)]
    changes, skipped = P.coach_changes([
        {"day": tue, "slot": 1, "sport": "rest", "kind": "rest"},
        {"day": tue, "slot": 0, "sport": "cycling", "kind": "threshold", "minutes": 75},
    ], rows, TODAY, RATES)
    assert changes == [{"day": tue, "slot": 1, "before": P.session_view(rows[1]), "after": None}] and skipped == []


# ------------------------------------------------------------------ AI-built plan

W0 = P.monday(TODAY)  # this week (TODAY is its Friday); MON is next week


def ai(day, sport="cycling", kind="endurance", minutes=60, slot=0, note=""):
    return {"day": day.isoformat(), "slot": slot, "sport": sport, "kind": kind, "minutes": minutes, "note": note}


def check(sessions, weeks=(), fill=frozenset({MON}), existing=(), done=None, prof=None, goals=(), ctl=60, overrides=None):
    return P.check_ai_plan(list(sessions), list(weeks), TODAY, set(fill), list(existing), done or {}, prof or profile(),
                           list(goals), LEVELS, RATES, ctl, overrides or {})


def test_ai_sessions_are_kept_as_written_within_the_limits():
    plan = [ai(MON + timedelta(days=1), kind="threshold", minutes=75, note="2x20"), ai(MON + timedelta(days=5), kind="long", minutes=180),
            ai(MON + timedelta(days=2), sport="gym", kind="lower", minutes=45, slot=1)]
    r = check(plan, [{"week_start": MON.isoformat(), "phase": "Build", "hours": 5, "note": "First build week."}],
              prof=profile({"cycling": 0.8, "gym": 0.2}))
    assert [(p.kind, p.minutes, p.source) for p in r.rows] == [("threshold", 75, "ai"), ("lower", 45, "ai"), ("long", 180, "ai")]
    assert r.rows[0].note == "2x20" and r.rows[1].slot == 0  # the free main slot is used
    assert r.weeks[MON.isoformat()] == {"phase": "build", "hours": 5.0, "note": "First build week."}
    assert not r.adjusted and not r.skipped


def test_ai_sessions_are_capped_by_level_and_bad_ones_skipped():
    plan = [ai(MON, kind="vo2max", minutes=150), ai(MON + timedelta(days=5), kind="long", minutes=400),
            ai(MON + timedelta(days=1), sport="running"), ai(MON + timedelta(days=2), kind="swim_drills"),
            ai(MON + timedelta(weeks=3)), ai(TODAY - timedelta(days=1)), ai(MON + timedelta(days=3), kind="rest")]
    r = check(plan)
    assert [(p.kind, p.minutes) for p in r.rows] == [("vo2max", P.HARD_CAP["cycling"]), ("long", P.LONG_CAP["cycling"]["intermediate"])]
    assert len(r.adjusted) == 2 and len(r.skipped) == 4  # running isn't a sport here, unknown kind, two outside the weeks


def test_ai_plan_respects_fixed_sessions_free_days_and_the_event():
    event = MON + timedelta(days=6)
    fixed = [P.PlanDay((MON + timedelta(days=1)).isoformat(), 0, "rest", "rest", 0, 0, "user"),
             P.PlanDay((MON + timedelta(days=2)).isoformat(), 0, "cycling", "tempo", 60, 60, "user"),
             P.PlanDay((MON + timedelta(days=3)).isoformat(), 0, "cycling", "endurance", 60, 60, "auto")]  # replaced
    goal = P.Goal("event", "cycling", event.isoformat(), 3, "Secret race")
    plan = [ai(MON + timedelta(days=1)), ai(MON + timedelta(days=2), minutes=40), ai(MON + timedelta(days=3), kind="recovery", minutes=40),
            ai(event - timedelta(days=1)), ai(event)]
    r = check(plan, existing=fixed, goals=[goal])
    rows = {(p.day, p.slot): p for p in r.rows}
    assert ((MON + timedelta(days=2)).isoformat(), 1) in rows  # next to the athlete's tempo, in slot 1
    assert rows[((MON + timedelta(days=3)).isoformat(), 0)].kind == "recovery"
    assert rows[(event.isoformat(), 0)].kind == "event" and rows[(event.isoformat(), 0)].source == "ai"
    assert len(r.skipped) == 3  # the free day, the day before the event, the event day


def test_ai_week_is_trimmed_to_the_hours_available():
    plan = [ai(MON + timedelta(days=i), minutes=120) for i in range(5)]  # 10 h
    r = check(plan, overrides={MON.isoformat(): 4}, weeks=[{"week_start": MON.isoformat(), "phase": "build", "hours": 10}])
    assert sum(p.minutes for p in r.rows) <= 4 * 60 * P.AI_HOURS_TOLERANCE
    assert r.weeks[MON.isoformat()]["hours"] == 4 and "trimmed" in r.adjusted[0]
    tiny = check(plan, overrides={MON.isoformat(): 1})  # even the shortest rides don't fit: some are left out
    assert sum(p.minutes for p in tiny.rows) <= 60 * P.AI_HOURS_TOLERANCE and tiny.skipped


def test_ai_week_is_shortened_when_fitness_would_rise_too_fast():
    plan = [ai(MON + timedelta(days=i), kind="threshold", minutes=90) for i in range(7)]
    r = check(plan, prof=profile(hours=20), ctl=20)
    assert sum(p.planned_load for p in r.rows) <= P.ramp_cap(20) + 10
    assert any("fitness rises" in a for a in r.adjusted)


def test_ai_week_counts_what_was_done_this_week():
    done = {(W0 + timedelta(days=i)).isoformat(): {"minutes": 120, "load": 100} for i in range(4)}  # Mon-Thu, 8 h
    r = check([ai(TODAY, minutes=60), ai(TODAY + timedelta(days=1), minutes=60)], fill={W0}, done=done)
    assert sum(p.minutes for p in r.rows) <= 70 and r.adjusted  # 7 h a week, 8 already done: only the minimum is left


def test_ai_request_is_identifier_free_and_lists_what_to_keep():
    goal = P.Goal("event", "cycling", (MON + timedelta(weeks=8)).isoformat(), 4, "Gran Fondo Lugano")
    existing = [P.PlanDay((MON + timedelta(days=1)).isoformat(), 0, "rest", "rest", 0, 0, "user"),
                P.PlanDay((MON + timedelta(days=2)).isoformat(), 0, "cycling", "endurance", 60, 60, "auto"),
                P.PlanDay((MON + timedelta(weeks=1)).isoformat(), 0, "cycling", "tempo", 60, 60, "ai")]
    acts = [{"day": (W0 - timedelta(days=3)).isoformat(), "sport_type": "road_biking", "minutes": 90, "load": 80, "hard_min": 2}]
    req = P.ai_request(TODAY, profile(), [goal], LEVELS, "Support my endurance training", 50, -5, RATES,
                       P.recent_weeks(acts, TODAY), [{"day": "2026-11-12", "hrv_avg": 100, "raw_json": "{}"}], {"2026-11-13": "green"},
                       existing, {}, {MON.isoformat(): 4}, {MON}, goals_text="Ride far")
    text = str(req)
    assert "Lugano" not in text and "raw_json" not in text
    assert req["weeks_to_plan"] == [MON.isoformat()] and req["targets_until"] == (MON + timedelta(weeks=8)).isoformat()
    assert req["fixed_sessions"] == [{"day": (MON + timedelta(days=1)).isoformat(), "weekday": "Tuesday", "slot": 0, "sport": "rest",
                                      "kind": "rest", "minutes": 0, "why": "kept free by the athlete"}]
    assert [r["kind"] for r in req["already_planned_other_weeks"]] == ["tempo"]
    assert req["recent_weeks"][-1]["hours"] == 1.5 and req["hours_available"] == {MON.isoformat(): 4}
    assert req["limits"]["max_load_this_week"] == P.ramp_cap(50) and set(req["kinds_by_sport"]) == {"cycling"}


def test_rules_fill_only_the_asked_weeks_and_replace_ai_sessions_there():
    _, first = P.build_plan(TODAY, 45, profile(), [], LEVELS, RATES, 6)
    existing = [P.PlanDay(p.day, p.slot, p.sport, p.kind, p.minutes, p.planned_load, "ai") for p in first]
    _, again = P.build_plan(TODAY, 45, profile(), [], LEVELS, RATES, 6, existing=existing, fill_weeks={MON})
    assert again and {P.monday(date.fromisoformat(p.day)) for p in again} == {MON}


AI_EXERCISES = [
    {"name": "Pistol squat progression", "group": "legs", "sets": 4, "reps": "5–8", "rpe": 10, "rest_s": 150, "note": "To failure."},
    {"name": "Ring leg curl", "group": "legs", "sets": 3, "reps": "8–12", "rpe": 10, "rest_s": 120, "note": ""},
    {"name": "Bulgarian split squat on the box", "group": "legs", "sets": 3, "reps": "8–10", "rpe": 10, "rest_s": 120, "note": ""},
    {"name": "Hanging knee raise", "group": "core", "sets": 3, "reps": "10–15", "rpe": 9, "rest_s": 60, "note": ""},
]


def test_ai_gym_sessions_keep_their_exercises_tidied():
    messy = [*AI_EXERCISES, {"name": "  "}, "junk", {"name": "x" * 200, "group": "arms", "sets": "lots", "rpe": 99}]
    plan = [{**ai(MON + timedelta(days=2), sport="gym", kind="lower", minutes=50), "exercises": messy},
            {**ai(MON + timedelta(days=3)), "exercises": AI_EXERCISES}]  # a ride never carries exercises
    r = check(plan, prof=profile({"cycling": 0.8, "gym": 0.2}))
    g, ride = r.rows
    assert g.exercises[:4] == AI_EXERCISES and len(g.exercises) == 5 and ride.exercises is None
    assert g.exercises[4] == {"name": "x" * 80, "group": "legs", "sets": 3, "reps": "8–12", "rpe": 10, "rest_s": 60, "note": ""}


def test_ai_request_describes_the_gym_setup_only_with_gym():
    args = (TODAY, profile({"cycling": 0.8, "gym": 0.2}), [], LEVELS, "Build muscle", 50, -5, RATES, [], [], {}, [], {}, {}, {MON})
    kit = gym.Kit(frozenset({"pullup_bar", "rings", "box"}), "failure")
    req = P.ai_request(*args, kit=kit)
    assert req["gym_setup"] == {"equipment": ["Pull-up bar", "Gymnastic rings", "Box or bench"], "effort": gym.EFFORTS["failure"]}
    assert P.ai_request(*args[:1], profile(), *args[2:], kit=kit)["gym_setup"] is None


def test_plan_suggestion_uses_the_ai_exercises_and_fits_them_to_the_day():
    trained = gym.last_trained([], TODAY)
    g = {"sport": "gym", "kind": "lower", "minutes": 60, "planned": {"label": "Gym: legs, 1 h"}, "exercises": AI_EXERCISES}
    s = P.plan_suggestion(g, ready(), [], trained, TODAY, "intermediate")
    assert s.exercises == AI_EXERCISES and s.session.name == gym.FOCUS["lower"].name
    assert any("AI coach" in r for r in s.rationale)
    short = P.plan_suggestion(g, ready(), [], trained, TODAY, "intermediate", minutes=25)
    assert len(short.exercises) == 2 and any("2 of its 4" in r for r in short.rationale)
    amber = P.plan_suggestion(g, ready("amber", 60), [], trained, TODAY, "intermediate")
    assert amber.exercises[0]["sets"] == 3 and amber.exercises[0]["rpe"] == 8 and "Amber" in amber.exercises[0]["note"]
    changed = P.plan_suggestion({**g, "kind": "mobility", "changed": True}, ready("red", 40), [], trained, TODAY, "intermediate")
    assert changed.session.key == "mobility" and changed.exercises != AI_EXERCISES  # readiness replaced the session


def test_today_from_plan_carries_the_exercises():
    p = [P.PlanDay(TODAY.isoformat(), 0, "gym", "lower", 50, 10, "ai", False, "", AI_EXERCISES)]
    assert P.today_from_plan(p, ready(), [], TODAY)["exercises"] == AI_EXERCISES


def test_coach_can_change_a_gym_sessions_exercises_without_changing_its_length():
    tue = (TODAY + timedelta(days=4)).isoformat()
    old = [{"name": "Squat", "group": "legs", "sets": 3, "reps": "8", "rpe": 7, "rest_s": 90, "note": ""}]
    rows = [P.PlanDay(tue, 0, "gym", "full", 45, 20, exercises=old)]
    new = [{"name": "Squat", "group": "legs", "sets": 4, "reps": "8", "rpe": 10, "rest_s": 120},
           {"name": "Plank", "group": "core", "sets": 3, "reps": "45 s", "rpe": 8, "rest_s": 45}]
    same = {"day": tue, "sport": "gym", "kind": "full", "minutes": 45}
    changes, _ = P.coach_changes([{**same, "exercises": new}], rows, TODAY, RATES)
    assert len(changes) == 1 and [e["name"] for e in changes[0]["after"]["exercises"]] == ["Squat", "Plank"]
    assert changes[0]["after"]["exercises"][0]["rpe"] == 10
    assert P.coach_changes([{**same, "exercises": old}], rows, TODAY, RATES)[0] == []  # nothing new
    assert P.coach_changes([same], rows, TODAY, RATES)[0] == []
