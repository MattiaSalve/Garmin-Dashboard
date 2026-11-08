"""Dashboard metrics computed from the activities table. All values are metric."""

import sqlite3
from datetime import date, timedelta

from garmin_dashboards.db import resolve


# Shared WHERE clause: inclusive date range on the local start date, optional sport filter.
_FILTER = """
    WHERE date(start_time_local) BETWEEN :start AND :end
      AND (:sport IS NULL OR sport_type = :sport)
"""


def _connect(db_path: str | None) -> sqlite3.Connection:
    conn = sqlite3.connect(resolve(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def summary(start: date, end: date, sport: str | None = None, db_path: str | None = None) -> dict:
    """Totals and averages for activities between start and end (inclusive)."""
    params = {"start": start.isoformat(), "end": end.isoformat(), "sport": sport}
    with _connect(db_path) as conn:
        row = conn.execute(
            f"""
            SELECT
                COUNT(*)                                   AS activity_count,
                COALESCE(SUM(distance_m), 0)               AS total_distance_m,
                COALESCE(SUM(moving_duration_s), 0)        AS total_moving_duration_s,
                COALESCE(SUM(elevation_gain_m), 0)         AS total_elevation_gain_m,
                COALESCE(SUM(calories), 0)                 AS total_calories,
                COALESCE(SUM(training_stress_score), 0)    AS total_training_stress,
                COALESCE(SUM(training_load), 0)            AS total_training_load,
                AVG(avg_hr)                                AS avg_hr,
                MAX(max_hr)                                AS max_hr,
                AVG(avg_power_w)                           AS avg_power_w,
                MAX(max_power_w)                           AS max_power_w,
                AVG(vo2max)                                AS latest_vo2max_avg
            FROM activities
            {_FILTER}
            """,
            params,
        ).fetchone()

    count = row["activity_count"]
    moving = row["total_moving_duration_s"]
    distance = row["total_distance_m"]
    return {
        "activity_count": count,
        "total_distance_km": distance / 1000,
        "total_moving_duration_h": moving / 3600,
        "total_elevation_gain_m": row["total_elevation_gain_m"],
        "total_calories": row["total_calories"],
        "total_training_stress": row["total_training_stress"],
        "total_training_load": row["total_training_load"],
        "avg_distance_km": (distance / 1000 / count) if count else None,
        "avg_moving_duration_min": (moving / 60 / count) if count else None,
        # Average speed over moving time, in km/h.
        "avg_speed_kmh": (distance / moving * 3.6) if moving else None,
        "avg_hr": row["avg_hr"],
        "max_hr": row["max_hr"],
        "avg_power_w": row["avg_power_w"],
        "max_power_w": row["max_power_w"],
        "avg_vo2max": row["latest_vo2max_avg"],
    }


def by_sport(start: date, end: date, db_path: str | None = None) -> list[dict]:
    """Per-sport breakdown (count, distance, moving time) for the date range."""
    params = {"start": start.isoformat(), "end": end.isoformat(), "sport": None}
    with _connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT sport_type,
                   COUNT(*)                          AS activity_count,
                   COALESCE(SUM(distance_m), 0)      AS total_distance_m,
                   COALESCE(SUM(moving_duration_s), 0) AS total_moving_duration_s
            FROM activities
            {_FILTER}
            GROUP BY sport_type
            ORDER BY total_moving_duration_s DESC
            """,
            params,
        ).fetchall()
    return [
        {
            "sport_type": r["sport_type"],
            "activity_count": r["activity_count"],
            "total_distance_km": r["total_distance_m"] / 1000,
            "total_moving_duration_h": r["total_moving_duration_s"] / 3600,
        }
        for r in rows
    ]


def weekly_totals(start: date, end: date, sport: str | None = None, db_path: str | None = None) -> list[dict]:
    """Per-week totals (weeks start on Monday), for trend charts."""
    params = {"start": start.isoformat(), "end": end.isoformat(), "sport": sport}
    with _connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT date(start_time_local, 'weekday 0', '-6 days') AS week_start,
                   COUNT(*)                                       AS activity_count,
                   COALESCE(SUM(distance_m), 0)                   AS total_distance_m,
                   COALESCE(SUM(moving_duration_s), 0)            AS total_moving_duration_s,
                   COALESCE(SUM(training_stress_score), 0)        AS total_training_stress,
                   COALESCE(SUM(training_load), 0)                AS total_training_load
            FROM activities
            {_FILTER}
            GROUP BY week_start
            ORDER BY week_start
            """,
            params,
        ).fetchall()
    return [
        {
            "week_start": r["week_start"],
            "activity_count": r["activity_count"],
            "total_distance_km": r["total_distance_m"] / 1000,
            "total_moving_duration_h": r["total_moving_duration_s"] / 3600,
            "total_training_stress": r["total_training_stress"],
            "total_training_load": r["total_training_load"],
        }
        for r in rows
    ]


def daily_training_load(db_path: str | None = None) -> list[dict]:
    """Total training load per local calendar day, across all sports and all time."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT date(start_time_local) AS day, SUM(training_load) AS load
            FROM activities
            WHERE training_load IS NOT NULL
            GROUP BY day
            ORDER BY day
            """
        ).fetchall()
    return [{"day": r["day"], "load": r["load"]} for r in rows]


def weekly_hr_zones(start: date, end: date, sport: str | None = None, db_path: str | None = None) -> list[dict]:
    """Hours spent in each heart-rate zone per week (activities without zone data are skipped)."""
    params = {"start": start.isoformat(), "end": end.isoformat(), "sport": sport}
    sums = ", ".join(f"COALESCE(SUM(hr_zone{i}_s), 0) AS z{i}" for i in range(1, 6))
    with _connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT date(start_time_local, 'weekday 0', '-6 days') AS week_start, {sums}
            FROM activities
            {_FILTER} AND hr_zone1_s IS NOT NULL
            GROUP BY week_start
            ORDER BY week_start
            """,
            params,
        ).fetchall()
    return [
        {"week_start": r["week_start"], **{f"Zone {i}": r[f"z{i}"] / 3600 for i in range(1, 6)}}
        for r in rows
    ]


# Durations (seconds) Garmin reports best average power for, in raw_json as maxAvgPower_<seconds>.
POWER_CURVE_SECONDS = [1, 2, 5, 10, 20, 30, 60, 120, 300, 600, 1200, 1800, 3600, 7200]


def power_curve(start: date, end: date, sport: str | None = None, db_path: str | None = None) -> list[dict]:
    """Best average power per duration over the range, from Garmin's per-activity bests."""
    params = {"start": start.isoformat(), "end": end.isoformat(), "sport": sport}
    # Column names come from the constant list above, never from user input.
    bests = ", ".join(
        f"MAX(json_extract(raw_json, '$.maxAvgPower_{d}')) AS p{d}" for d in POWER_CURVE_SECONDS
    )
    with _connect(db_path) as conn:
        row = conn.execute(
            f"SELECT {bests} FROM activities {_FILTER} AND raw_json IS NOT NULL", params
        ).fetchone()
    return [
        {"seconds": d, "watts": row[f"p{d}"]} for d in POWER_CURVE_SECONDS if row[f"p{d}"] is not None
    ]


def daily_totals(start: date, end: date, sport: str | None = None, db_path: str | None = None) -> list[dict]:
    """Per-day distance and moving time, for the calendar heatmap."""
    params = {"start": start.isoformat(), "end": end.isoformat(), "sport": sport}
    with _connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT date(start_time_local) AS day,
                   COUNT(*) AS activity_count,
                   COALESCE(SUM(distance_m), 0) AS total_distance_m,
                   COALESCE(SUM(moving_duration_s), 0) AS total_moving_duration_s
            FROM activities
            {_FILTER}
            GROUP BY day
            ORDER BY day
            """,
            params,
        ).fetchall()
    return [
        {
            "day": r["day"],
            "activity_count": r["activity_count"],
            "distance_km": r["total_distance_m"] / 1000,
            "hours": r["total_moving_duration_s"] / 3600,
        }
        for r in rows
    ]


# Garmin sport keys grouped for "km cycled / km run" style totals.
SPORT_GROUPS = {
    "Cycling": ("road_biking", "indoor_cycling", "cycling", "virtual_ride", "mountain_biking", "gravel_cycling"),
    "Running": ("running", "trail_running", "treadmill_running", "track_running"),
}

# Whitelisted sort orders for search_activities (never interpolate user input).
SEARCH_ORDERS = {
    "Newest first": "start_time_local DESC",
    "Oldest first": "start_time_local ASC",
    "Longest distance": "distance_m DESC",
    "Most elevation": "elevation_gain_m DESC",
    "Longest time": "moving_duration_s DESC",
}


def search_activities(
    start: date | None = None,
    end: date | None = None,
    sports: list[str] | None = None,
    min_km: float | None = None,
    max_km: float | None = None,
    min_elevation_m: float | None = None,
    max_elevation_m: float | None = None,
    min_hours: float | None = None,
    max_hours: float | None = None,
    text: str | None = None,
    order: str = "Newest first",
    db_path: str | None = None,
) -> list[dict]:
    """Activities matching every given filter (None = no limit)."""
    clauses, params = [], {}

    def add(clause: str, name: str, value) -> None:
        if value is not None:
            clauses.append(clause)
            params[name] = value

    add("date(start_time_local) >= :start", "start", start and start.isoformat())
    add("date(start_time_local) <= :end", "end", end and end.isoformat())
    add("distance_m >= :min_m", "min_m", None if min_km is None else min_km * 1000)
    add("distance_m <= :max_m", "max_m", None if max_km is None else max_km * 1000)
    add("elevation_gain_m >= :min_e", "min_e", min_elevation_m)
    add("elevation_gain_m <= :max_e", "max_e", max_elevation_m)
    add("moving_duration_s >= :min_s", "min_s", None if min_hours is None else min_hours * 3600)
    add("moving_duration_s <= :max_s", "max_s", None if max_hours is None else max_hours * 3600)
    if text:
        add("(activity_name LIKE :text OR location_name LIKE :text)", "text", f"%{text}%")
    if sports:
        names = {f"sport{i}": s for i, s in enumerate(sports)}
        clauses.append(f"sport_type IN ({', '.join(':' + n for n in names)})")
        params.update(names)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with _connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT activity_id, start_time_local, activity_name, sport_type, distance_m,
                   moving_duration_s, elevation_gain_m, avg_hr, max_hr, avg_power_w,
                   calories, training_stress_score
            FROM activities {where}
            ORDER BY {SEARCH_ORDERS[order]}
            """,
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def distance_by_group(start: date, end: date, db_path: str | None = None) -> dict[str, float]:
    """Kilometres per sport group (see SPORT_GROUPS) in the inclusive date range."""
    params = {"start": start.isoformat(), "end": end.isoformat(), "sport": None}
    with _connect(db_path) as conn:
        rows = conn.execute(
            f"SELECT sport_type, COALESCE(SUM(distance_m), 0) AS m FROM activities {_FILTER} GROUP BY sport_type",
            params,
        ).fetchall()
    by_type = {r["sport_type"]: r["m"] for r in rows}
    return {g: sum(by_type.get(s, 0) for s in sports) / 1000 for g, sports in SPORT_GROUPS.items()}


def latest_vo2max(group: str, db_path: str | None = None) -> dict | None:
    """Most recent VO2max reading for a sport group, or None."""
    sports = SPORT_GROUPS[group]
    marks = ", ".join("?" * len(sports))
    with _connect(db_path) as conn:
        row = conn.execute(
            f"SELECT vo2max, date(start_time_local) AS day FROM activities "
            f"WHERE vo2max IS NOT NULL AND sport_type IN ({marks}) "
            f"ORDER BY start_time_local DESC LIMIT 1",
            sports,
        ).fetchone()
    return {"value": row["vo2max"], "day": row["day"]} if row else None


def filter_bounds(db_path: str | None = None) -> dict:
    """Largest distance (km), elevation gain (m) and moving time (h), for filter sliders."""
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT MAX(distance_m) AS d, MAX(elevation_gain_m) AS e, MAX(moving_duration_s) AS s FROM activities"
        ).fetchone()
    return {
        "km": (row["d"] or 0) / 1000,
        "elevation_m": row["e"] or 0,
        "hours": (row["s"] or 0) / 3600,
    }


# ---------------------------------------------------------------- wellness and home page

WELLNESS_COLUMNS = [
    "day", "hrv_avg", "hrv_weekly_avg", "hrv_baseline_low", "hrv_baseline_high", "hrv_status",
    "rhr", "sleep_score", "sleep_s", "deep_s", "rem_s", "bb_charged", "bb_high", "bb_low",
    "stress_avg", "training_readiness",
]


def daily_wellness(start: date, end: date, db_path: str | None = None) -> list[dict]:
    """Recovery data per day (oldest first) in the inclusive range."""
    with _connect(db_path) as conn:
        try:
            rows = conn.execute(
                f"SELECT {', '.join(WELLNESS_COLUMNS)} FROM wellness "
                "WHERE day BETWEEN ? AND ? ORDER BY day",
                (start.isoformat(), end.isoformat()),
            ).fetchall()
        except sqlite3.OperationalError:  # table not created yet
            return []
    return [dict(r) for r in rows]


def daily_intensity(start: date, end: date, db_path: str | None = None) -> list[dict]:
    """Per-day training load, hard minutes (zone 4+5) and peak aerobic/anaerobic effect, all sports."""
    params = {"start": start.isoformat(), "end": end.isoformat(), "sport": None}
    with _connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT date(start_time_local) AS day,
                   COALESCE(SUM(training_load), 0)                                   AS load,
                   COALESCE(SUM(COALESCE(hr_zone4_s, 0) + COALESCE(hr_zone5_s, 0)), 0) / 60.0 AS hard_min,
                   MAX(aerobic_te)                                                   AS max_aerobic_te,
                   MAX(anaerobic_te)                                                 AS max_anaerobic_te,
                   COALESCE(SUM(moving_duration_s), 0) / 3600.0                      AS hours
            FROM activities
            {_FILTER}
            GROUP BY day
            ORDER BY day
            """,
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def power_prs(today: date, days: int = 28, db_path: str | None = None) -> list[dict]:
    """Durations where the best power in the last `days` beats every earlier ride."""
    since = (today - timedelta(days=days - 1)).isoformat()
    bests = ", ".join(
        f"MAX(CASE WHEN date(start_time_local) >= :since THEN json_extract(raw_json, '$.maxAvgPower_{d}') END) AS r{d}, "
        f"MAX(CASE WHEN date(start_time_local) < :since THEN json_extract(raw_json, '$.maxAvgPower_{d}') END) AS o{d}"
        for d in POWER_CURVE_SECONDS
    )
    with _connect(db_path) as conn:
        row = conn.execute(
            f"SELECT {bests} FROM activities WHERE raw_json IS NOT NULL AND date(start_time_local) <= :today",
            {"since": since, "today": today.isoformat()},
        ).fetchone()
    out = []
    for d in POWER_CURVE_SECONDS:
        recent, old = row[f"r{d}"], row[f"o{d}"]
        if recent is not None and (old is None or recent > old):
            out.append({"seconds": d, "watts": recent, "previous_watts": old})
    return out


def estimated_ftp_trend(db_path: str | None = None) -> list[dict]:
    """95% of the best 20-minute power per calendar month (a rough FTP estimate)."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT strftime('%Y-%m', start_time_local) AS month,
                   MAX(json_extract(raw_json, '$.maxAvgPower_1200')) AS best20
            FROM activities
            WHERE raw_json IS NOT NULL
            GROUP BY month
            HAVING best20 IS NOT NULL
            ORDER BY month
            """
        ).fetchall()
    return [{"month": r["month"], "ftp_estimate": r["best20"] * 0.95} for r in rows]


def week_progress(today: date, db_path: str | None = None) -> dict:
    """This week (Mon..today) vs the average of the 4 full weeks before, plus intensity split."""
    week_start = today - timedelta(days=today.weekday())
    this = summary(week_start, today, db_path=db_path)
    prev = summary(week_start - timedelta(days=28), week_start - timedelta(days=1), db_path=db_path)
    zones = weekly_hr_zones(week_start, today, db_path=db_path)
    z = {f"Zone {i}": sum(w[f"Zone {i}"] for w in zones) for i in range(1, 6)}
    return {
        "week_start": week_start,
        "hours": this["total_moving_duration_h"],
        "distance_km": this["total_distance_km"],
        "load": this["total_training_load"],
        "activity_count": this["activity_count"],
        "avg_hours": prev["total_moving_duration_h"] / 4,
        "avg_load": prev["total_training_load"] / 4,
        "easy_h": z["Zone 1"] + z["Zone 2"],
        "moderate_h": z["Zone 3"],
        "hard_h": z["Zone 4"] + z["Zone 5"],
    }


def longest_activity(start: date, end: date, sports: tuple[str, ...], db_path: str | None = None) -> dict | None:
    marks = ", ".join("?" * len(sports))
    with _connect(db_path) as conn:
        row = conn.execute(
            f"SELECT activity_name, date(start_time_local) AS day, distance_m FROM activities "
            f"WHERE date(start_time_local) BETWEEN ? AND ? AND sport_type IN ({marks}) "
            f"ORDER BY distance_m DESC LIMIT 1",
            (start.isoformat(), end.isoformat(), *sports),
        ).fetchone()
    return dict(row) if row else None
