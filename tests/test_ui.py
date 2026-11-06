import importlib


def test_activities_table_handles_all_null_columns():
    main = importlib.import_module("garmin_dashboards.main")
    row = {
        "activity_id": 1, "start_time_local": "2026-01-05 08:00:00", "activity_name": "Lift",
        "sport_type": "strength_training", "distance_m": None, "moving_duration_s": None,
        "elevation_gain_m": None, "avg_hr": None, "max_hr": None, "avg_power_w": None,
        "calories": None, "training_stress_score": None,
    }
    table = main.activities_table([row])
    assert list(table.index) == [1] and table.loc[1, "Name"] == "Lift"
    assert main.activities_table([]).empty
