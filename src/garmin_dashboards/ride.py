"""Second-by-second ride data: turn Garmin's activity details into plottable channels."""

import pandas as pd

# (title, unit, candidate Garmin keys in priority order, factor to convert to the unit)
RIDE_CHANNELS = [
    ("Heart rate", "bpm", ["directHeartRate"], 1.0),
    ("Power", "W", ["directPower"], 1.0),
    ("Speed", "km/h", ["directSpeed"], 3.6),
    ("Elevation", "m", ["directElevation"], 1.0),
    ("Cadence", "rpm", ["directBikeCadence", "directRunCadence", "directDoubleCadence"], 1.0),
]


def trailing_average(data: pd.DataFrame, window: str | None) -> pd.DataFrame:
    """Running mean of `value` from the start (window=None) or over a time window.

    `data` needs `minutes` and `value` columns; any other columns (e.g. `x`) are kept.
    """
    data = data.sort_values("minutes")
    if window is None:
        averaged = data["value"].expanding().mean()
    else:
        timed = pd.Series(
            data["value"].to_numpy(), index=pd.to_timedelta(data["minutes"].to_numpy() * 60, unit="s")
        )
        averaged = pd.Series(timed.rolling(window).mean().to_numpy(), index=data.index)
    return data.assign(value=averaged).dropna(subset=["value"])


def _clean(values: pd.Series, digits: int) -> list[float | None]:
    return [None if pd.isna(v) else round(float(v), digits) for v in values]


MAX_ROUTE_POINTS = 600


def route_points(samples: pd.DataFrame, max_points: int = MAX_ROUTE_POINTS) -> list[list[float]] | None:
    """[lat, lon, sample_index] triples for drawing the path, thinned evenly to at most `max_points`.

    The sample index is the row in the chart's arrays, so a selected part of the chart can be
    highlighted on the map.

    None for indoor or GPS-less activities. Garmin reports degrees; (0, 0) means "no fix" and is dropped.
    """
    if "directLatitude" not in samples or "directLongitude" not in samples:
        return None
    pts = samples[["directLatitude", "directLongitude"]].reset_index(drop=True).dropna()
    pts = pts[(pts["directLatitude"].abs() <= 90) & (pts["directLongitude"].abs() <= 180)]
    pts = pts[(pts["directLatitude"] != 0) | (pts["directLongitude"] != 0)]
    if len(pts) < 2:
        return None
    if len(pts) > max_points:
        keep = [round(i * (len(pts) - 1) / (max_points - 1)) for i in range(max_points)]
        pts = pts.iloc[keep]
    return [[round(float(la), 6), round(float(lo), 6), int(i)] for i, (la, lo) in zip(pts.index, pts.to_numpy())]


def samples_payload(samples: pd.DataFrame) -> dict | None:
    """Aligned arrays for the chart: time, distance (if any) and one list per available channel.

    Returns None when there is no usable timing data. Missing readings are None.
    """
    time_col = next(
        (c for c in ("sumMovingDuration", "sumDuration") if c in samples and samples[c].notna().any()),
        None,
    )
    if time_col is None:
        return None
    has_distance = "sumDistance" in samples and samples["sumDistance"].notna().any()
    channels = {}
    for title, unit, keys, factor in RIDE_CHANNELS:
        key = next((k for k in keys if k in samples and samples[k].notna().any()), None)
        if key is not None:
            channels[title] = {"unit": unit, "values": _clean(samples[key] * factor, 2)}
    return {
        "minutes": _clean(samples[time_col] / 60, 3),
        "km": _clean(samples["sumDistance"] / 1000, 4) if has_distance else None,
        "channels": channels,
        "route": route_points(samples),
    }
