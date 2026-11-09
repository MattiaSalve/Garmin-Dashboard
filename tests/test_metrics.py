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
    assert metrics.distance_by_group(*rng, db_path=path) == {"Cycling": 130.0, "Running": 10.0, "Swimming": 0.0}
    assert metrics.latest_vo2max("Cycling", db_path=path) == {"value": 55, "day": "2026-01-01"}
    assert metrics.filter_bounds(db_path=path)["km"] == 100


def test_power_prs_and_week_progress(tmp_path):
    path = str(tmp_path / "t.db")
    def ride(i, day, p300, z5=0):
        return {"activityId": i, "startTimeLocal": f"{day} 08:00:00", "distance": 30000.0,
                "movingDuration": 3600.0, "activityTrainingLoad": 100.0, "maxAvgPower_300": p300,
                "maxAvgPower_1200": p300 - 40, "hrTimeInZone_1": 300, "hrTimeInZone_2": 3000,
                "hrTimeInZone_3": 0, "hrTimeInZone_4": 0, "hrTimeInZone_5": z5}
    db.save_activities([
        ride(1, "2026-09-01", 300),
        ride(2, "2026-10-20", 280),
        ride(3, "2026-11-03", 320, z5=600),  # Tuesday of the current week
    ], "me", path)
    today = date(2026, 11, 8)
    assert metrics.power_prs(today, db_path=path) == [
        {"seconds": 300, "watts": 320, "previous_watts": 300},
        {"seconds": 1200, "watts": 280, "previous_watts": 260},
    ]
    assert [r["month"] for r in metrics.estimated_ftp_trend(db_path=path)] == ["2026-09", "2026-10", "2026-11"]

    w = metrics.week_progress(today, db_path=path)
    assert w["week_start"] == date(2026, 11, 2) and w["activity_count"] == 1
    assert w["avg_load"] == 25.0  # one 100-load ride in the 4 weeks before, averaged per week
    assert round(w["hard_h"], 2) == round(600 / 3600, 2)


def test_sport_group_filter(tmp_path):
    path = str(tmp_path / "t.db")
    def act(i, sport, km):
        return {"activityId": i, "activityType": {"typeKey": sport}, "startTimeLocal": "2026-01-05 08:00:00",
                "distance": km * 1000, "movingDuration": 3600.0}
    db.save_activities([act(1, "road_biking", 40), act(2, "trail_running", 10), act(3, "running", 5),
                        act(4, "swimming", 2)], "me", path)
    rng = (date(2026, 1, 1), date(2026, 1, 31))
    assert metrics.summary(*rng, db_path=path)["total_distance_km"] == 57
    assert metrics.summary(*rng, sport="Running", db_path=path)["total_distance_km"] == 15
    assert metrics.summary(*rng, sport="Cycling", db_path=path)["total_distance_km"] == 40
    assert metrics.summary(*rng, sport="swimming", db_path=path)["total_distance_km"] == 2
    assert metrics.avg_weekly_hours("Running", date(2026, 1, 31), weeks=4, db_path=path) == 0.5


def test_sport_evidence(tmp_path):
    path = str(tmp_path / "t.db")
    def run(i, day, minutes):
        return {"activityId": i, "activityType": {"typeKey": "running"}, "startTimeLocal": f"{day} 08:00:00",
                "distance": 8000, "movingDuration": minutes * 60}
    db.save_activities([run(1, "2026-01-10", 47), run(2, "2025-12-20", 72), run(3, "2025-01-01", 150)], "me", path)
    e = metrics.sport_evidence("Running", date(2026, 1, 31), db_path=path)
    assert e["sessions_180d"] == 2 and e["longest_min_180d"] == 72 and round(e["weekly_hours"], 2) == 0.17
