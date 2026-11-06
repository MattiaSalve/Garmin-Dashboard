"""SQLite storage for Garmin activities. All stored values are metric."""

import sqlite3

DB_NAME = "garmin_data.db"

# All units are metric (meters, seconds, m/s, meters above sea level, watts, bpm, kcal).
SCHEMA = """
CREATE TABLE IF NOT EXISTS activities (
    activity_id           INTEGER PRIMARY KEY,
    activity_uuid         TEXT,
    activity_name         TEXT,
    sport_type            TEXT,
    start_time_local      TEXT,
    start_time_gmt        TEXT,
    distance_m            REAL,
    duration_s            REAL,
    moving_duration_s     REAL,
    elevation_gain_m      REAL,
    elevation_loss_m      REAL,
    avg_speed_mps         REAL,
    max_speed_mps         REAL,
    avg_hr                REAL,
    max_hr                REAL,
    calories              REAL,
    avg_power_w           REAL,
    norm_power_w          REAL,
    max_power_w           REAL,
    training_stress_score REAL,
    aerobic_te            REAL,
    anaerobic_te          REAL,
    vo2max                REAL,
    start_lat             REAL,
    start_lon             REAL,
    end_lat               REAL,
    end_lon               REAL,
    location_name         TEXT,
    user_email            TEXT
)
"""


def to_row(a: dict, user_email: str) -> dict:
    return {
        "activity_id": a["activityId"],
        "activity_uuid": a.get("activityUUID"),
        "activity_name": a.get("activityName"),
        "sport_type": (a.get("activityType") or {}).get("typeKey"),
        "start_time_local": a.get("startTimeLocal"),
        "start_time_gmt": a.get("startTimeGMT"),
        "distance_m": a.get("distance"),
        "duration_s": a.get("duration"),
        "moving_duration_s": a.get("movingDuration"),
        "elevation_gain_m": a.get("elevationGain"),
        "elevation_loss_m": a.get("elevationLoss"),
        "avg_speed_mps": a.get("averageSpeed"),
        "max_speed_mps": a.get("maxSpeed"),
        "avg_hr": a.get("averageHR"),
        "max_hr": a.get("maxHR"),
        "calories": a.get("calories"),
        "avg_power_w": a.get("avgPower"),
        "norm_power_w": a.get("normPower"),
        "max_power_w": a.get("maxPower"),
        "training_stress_score": a.get("trainingStressScore"),
        "aerobic_te": a.get("aerobicTrainingEffect"),
        "anaerobic_te": a.get("anaerobicTrainingEffect"),
        "vo2max": a.get("vO2MaxValue"),
        "start_lat": a.get("startLatitude"),
        "start_lon": a.get("startLongitude"),
        "end_lat": a.get("endLatitude"),
        "end_lon": a.get("endLongitude"),
        "location_name": a.get("locationName"),
        "user_email": user_email,
    }


def init_db(db_path: str = DB_NAME) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(SCHEMA)


def save_activities(activities: list[dict], user_email: str, db_path: str = DB_NAME) -> None:
    if not activities:
        return
    rows = [to_row(a, user_email) for a in activities]
    cols = list(rows[0].keys())
    sql = (
        f"INSERT OR REPLACE INTO activities ({', '.join(cols)}) "
        f"VALUES ({', '.join(':' + c for c in cols)})"
    )
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.executemany(sql, rows)
