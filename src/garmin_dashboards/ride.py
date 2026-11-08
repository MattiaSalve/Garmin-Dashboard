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
    }
