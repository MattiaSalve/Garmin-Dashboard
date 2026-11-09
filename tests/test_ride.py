import pandas as pd

from garmin_dashboards.ride import route_points, samples_payload


def frame(n, lat0=46.0, lon0=8.0):
    return pd.DataFrame({
        "sumDuration": [float(i) for i in range(n)],
        "directLatitude": [lat0 + i * 1e-4 for i in range(n)],
        "directLongitude": [lon0 + i * 1e-4 for i in range(n)],
    })


def test_route_is_thinned_but_keeps_both_ends():
    route = route_points(frame(5000), max_points=100)
    assert len(route) == 100 and route[0] == [46.0, 8.0, 0] and route[-1] == [46.4999, 8.4999, 4999]


def test_route_drops_missing_and_zero_fixes():
    df = frame(4)
    df.loc[1, "directLatitude"] = None
    df.loc[2, ["directLatitude", "directLongitude"]] = 0.0
    assert route_points(df) == [[46.0, 8.0, 0], [46.0003, 8.0003, 3]]


def test_indoor_activity_has_no_route():
    indoor = frame(10).drop(columns=["directLatitude", "directLongitude"])
    assert route_points(indoor) is None and samples_payload(indoor)["route"] is None
    assert len(samples_payload(frame(10))["route"]) == 10
