import json

from garmin_dashboards import db, metrics
from datetime import date


def activity(i, zones=(60, 120, 0, 0, 0)):
    a = {"activityId": i, "startTimeLocal": "2026-01-05 08:00:00", "activityTrainingLoad": 50.0}
    a.update({f"hrTimeInZone_{n}": z for n, z in enumerate(zones, 1)})
    return a


def test_to_row_maps_zones_and_load():
    row = db.to_row(activity(1), "me")
    assert row["hr_zone2_s"] == 120 and row["training_load"] == 50.0
    assert json.loads(row["raw_json"])["activityId"] == 1


def test_weekly_hr_zones_skips_rows_without_zones(tmp_path):
    path = str(tmp_path / "t.db")
    db.save_activities([activity(1), {"activityId": 2, "startTimeLocal": "2026-01-06 08:00:00"}], "me", path)
    weeks = metrics.weekly_hr_zones(date(2026, 1, 1), date(2026, 1, 31), db_path=path)
    assert weeks == [{"week_start": "2026-01-05", "Zone 1": 60 / 3600, "Zone 2": 120 / 3600,
                      "Zone 3": 0, "Zone 4": 0, "Zone 5": 0}]
