from datetime import date

from garmin_dashboards import db, metrics


def test_power_curve_takes_best_per_duration_and_daily_totals(tmp_path):
    path = str(tmp_path / "t.db")
    base = {"startTimeLocal": "2026-01-05 08:00:00", "distance": 20000.0, "movingDuration": 3600.0}
    db.save_activities(
        [
            {"activityId": 1, **base, "maxAvgPower_5": 700, "maxAvgPower_300": 300},
            {"activityId": 2, **base, "maxAvgPower_5": 800},
            {"activityId": 3, **base},
        ],
        "me",
        path,
    )
    rng = (date(2026, 1, 1), date(2026, 1, 31))
    assert metrics.power_curve(*rng, db_path=path) == [
        {"seconds": 5, "watts": 800},
        {"seconds": 300, "watts": 300},
    ]
    assert metrics.daily_totals(*rng, db_path=path) == [
        {"day": "2026-01-05", "activity_count": 3, "distance_km": 60.0, "hours": 3.0}
    ]
