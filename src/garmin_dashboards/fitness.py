"""Fitness / fatigue / form model (performance-management chart).

Fitness (CTL) and fatigue (ATL) are exponentially weighted averages of daily
training load over 42 and 7 days. Form (TSB) is fitness minus fatigue, using
yesterday's values so it reflects readiness at the start of each day.
"""

from datetime import date, timedelta

CTL_DAYS = 42
ATL_DAYS = 7


def fitness_series(daily_load: dict[date, float], end: date | None = None) -> list[dict]:
    """Daily CTL/ATL/TSB from the first load day through `end` (default: last load day).

    Days without activities count as zero load.
    """
    if not daily_load:
        return []
    day = min(daily_load)
    last = end or max(daily_load)
    ctl = atl = 0.0
    out = []
    while day <= last:
        tsb = ctl - atl
        load = daily_load.get(day, 0.0)
        ctl += (load - ctl) / CTL_DAYS
        atl += (load - atl) / ATL_DAYS
        out.append({"day": day, "load": load, "fitness": ctl, "fatigue": atl, "form": tsb})
        day += timedelta(days=1)
    return out
