from datetime import date, timedelta

from garmin_dashboards.readiness import BAD, CAUTION, GOOD, assess, hrv_range

TODAY = date(2026, 11, 8)


def history(hrv_recent=90, hrv_before=105, rhr=48, rhr_today=48, sleep=84, sleep_s=30645, days=30):
    rows = []
    for i in range(days, -1, -1):
        day = TODAY - timedelta(days=i)
        rows.append({
            "day": day.isoformat(), "hrv_avg": hrv_recent if i < 6 else hrv_before,
            "hrv_baseline_low": 96, "hrv_baseline_high": 124,
            "rhr": rhr_today if i == 0 else rhr, "sleep_score": sleep, "sleep_s": sleep_s,
        })
    return rows


EASY_WEEK = [{"day": (TODAY - timedelta(days=i)).isoformat(), "load": 40, "hard_min": 2, "max_aerobic_te": 2.5}
             for i in range(1, 8)]


def statuses(r):
    return {s.name: s.status for s in r.signals}


def test_todays_real_case_is_amber():
    """HRV 90 vs Garmin's 96-124 baseline and low all week; sleep and resting HR fine."""
    r = assess(history(), EASY_WEEK, form=-5, settings={}, today=TODAY)
    assert statuses(r)["HRV"] == CAUTION and statuses(r)["HRV trend"] == CAUTION
    assert statuses(r)["Sleep"] == GOOD and statuses(r)["Resting HR"] == GOOD
    assert (r.level, r.score) == ("amber", 70)


def test_personal_range_overrides_garmin_baseline():
    r = assess(history(), EASY_WEEK, form=-5, settings={"hrv_low": 80, "hrv_high": 100}, today=TODAY)
    assert r.hrv_range == (80, 100) and r.level == "green"


def test_stacked_bad_signals_are_red():
    rows = history(hrv_recent=70, rhr_today=56, sleep=50)
    r = assess(rows, EASY_WEEK, form=-30, settings={}, today=TODAY)
    s = statuses(r)
    assert s["HRV"] == BAD and s["Resting HR"] == BAD and s["Sleep"] == BAD and s["Form"] == BAD
    assert r.level == "red" and r.score == 0


def test_recent_hard_day_is_a_caution():
    hard = EASY_WEEK + [{"day": (TODAY - timedelta(days=1)).isoformat(), "load": 200, "hard_min": 25}]
    r = assess(history(hrv_recent=110), hard, form=0, settings={}, today=TODAY)
    assert statuses(r)["Last 48 h"] == CAUTION and r.level == "green"  # one caution only


def test_missing_today_is_reported_not_penalised():
    rows = history()[:-1]
    r = assess(rows, [], form=None, settings={}, today=TODAY)
    assert not r.has_today_data and "No recovery data" in r.headline
    assert statuses(r)["HRV"] == "no data" and statuses(r)["Sleep"] == "no data"


def test_hrv_range_falls_back_to_rolling_stats():
    rows = [{"day": f"d{i}", "hrv_avg": 100 + (i % 2) * 10} for i in range(20)]
    low, high = hrv_range(rows, {})
    assert low < 105 < high
    assert hrv_range(rows[:5], {}) is None
