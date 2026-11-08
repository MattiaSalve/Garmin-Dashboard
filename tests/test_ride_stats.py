import pandas as pd

from garmin_dashboards.main import range_stats


def _frame(values):
    return pd.DataFrame({"x": [0, 1, 2, 3], "minutes": [0, 10, 20, 30], "value": values})


def test_range_stats_means_and_elevation_gain():
    stats = range_stats(
        {"Elevation": _frame([100, 110, 105, 120]), "Power": _frame([200, 220, 0, 240])}, "km"
    )
    assert stats["Elevation gain"] == "25 m"
    assert stats["Avg power"] == "165 W"
    assert stats["Distance"] == "3.0 km"
    assert stats["Duration"] == "30 min"
