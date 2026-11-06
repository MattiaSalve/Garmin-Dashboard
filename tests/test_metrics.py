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


def test_search_filters_and_groups(tmp_path):
    path = str(tmp_path / "t.db")
    def act(i, sport, km, elev, hours, name="x", vo2=None):
        return {"activityId": i, "activityName": name, "activityType": {"typeKey": sport},
                "startTimeLocal": f"2026-01-0{i} 08:00:00", "distance": km * 1000,
                "elevationGain": elev, "movingDuration": hours * 3600, "vO2MaxValue": vo2}
    db.save_activities([
        act(1, "road_biking", 100, 1500, 4, "Alps", 55),
        act(2, "road_biking", 30, 200, 1, "Local"),
        act(3, "running", 10, 50, 1, "Park", 48),
    ], "me", path)
    ids = lambda **kw: [r["activity_id"] for r in metrics.search_activities(db_path=path, **kw)]
    assert ids(min_km=50) == [1]
    assert ids(max_elevation_m=100) == [3]
    assert ids(min_hours=1, max_hours=1, sports=["road_biking"]) == [2]
    assert ids(text="alp") == [1]
    assert ids(order="Longest distance") == [1, 2, 3]
    rng = (date(2026, 1, 1), date(2026, 1, 31))
    assert metrics.distance_by_group(*rng, db_path=path) == {"Cycling": 130.0, "Running": 10.0}
    assert metrics.latest_vo2max("Cycling", db_path=path) == {"value": 55, "day": "2026-01-01"}
    assert metrics.filter_bounds(db_path=path)["km"] == 100
