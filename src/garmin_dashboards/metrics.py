"""Dashboard metrics computed from the activities table. All values are metric."""

import sqlite3
from datetime import date

DB_NAME = "garmin_data.db"

# Shared WHERE clause: inclusive date range on the local start date, optional sport filter.
_FILTER = """
    WHERE date(start_time_local) BETWEEN :start AND :end
      AND (:sport IS NULL OR sport_type = :sport)
"""


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def summary(start: date, end: date, sport: str | None = None, db_path: str = DB_NAME) -> dict:
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


def by_sport(start: date, end: date, db_path: str = DB_NAME) -> list[dict]:
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


def weekly_totals(start: date, end: date, sport: str | None = None, db_path: str = DB_NAME) -> list[dict]:
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


def daily_training_load(db_path: str = DB_NAME) -> list[dict]:
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
