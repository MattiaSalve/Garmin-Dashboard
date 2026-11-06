"""SQLite storage for Garmin activities. All stored values are metric."""

import json
import sqlite3
from datetime import datetime, timezone

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
    training_load         REAL,
    hr_zone1_s           REAL,
    hr_zone2_s           REAL,
    hr_zone3_s           REAL,
    hr_zone4_s           REAL,
    hr_zone5_s           REAL,
    aerobic_te            REAL,
    anaerobic_te          REAL,
    vo2max                REAL,
    start_lat             REAL,
    start_lon             REAL,
    end_lat               REAL,
    end_lon               REAL,
    location_name         TEXT,
    user_email            TEXT,
    last_synced           TEXT,
    raw_json              TEXT
)
"""

# Columns added after the first release; applied to existing databases by init_db.
MIGRATION_COLUMNS = {
    "last_synced": "TEXT",
    "raw_json": "TEXT",
    "training_load": "REAL",
    **{f"hr_zone{i}_s": "REAL" for i in range(1, 6)},
}
# Columns that can be rebuilt from raw_json for rows stored before the column existed.
BACKFILL_FROM_RAW = {
    "training_load": "activityTrainingLoad",
    **{f"hr_zone{i}_s": f"hrTimeInZone_{i}" for i in range(1, 6)},
}



def to_row(a: dict, user_email: str, synced_at: str | None = None) -> dict:
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
        "training_load": a.get("activityTrainingLoad"),
        **{f"hr_zone{i}_s": a.get(f"hrTimeInZone_{i}") for i in range(1, 6)},
        "aerobic_te": a.get("aerobicTrainingEffect"),
        "anaerobic_te": a.get("anaerobicTrainingEffect"),
        "vo2max": a.get("vO2MaxValue"),
        "start_lat": a.get("startLatitude"),
        "start_lon": a.get("startLongitude"),
        "end_lat": a.get("endLatitude"),
        "end_lon": a.get("endLongitude"),
        "location_name": a.get("locationName"),
        "user_email": user_email,
        "last_synced": synced_at,
        "raw_json": json.dumps(a),
    }


def init_db(db_path: str = DB_NAME) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(SCHEMA)
        existing = {r[1] for r in conn.execute("PRAGMA table_info(activities)")}
        for col, typ in MIGRATION_COLUMNS.items():
            if col not in existing:
                conn.execute(f"ALTER TABLE activities ADD COLUMN {col} {typ}")
        # Backfill columns that were added after rows were stored, from the raw JSON.
        for col, key in BACKFILL_FROM_RAW.items():
            conn.execute(
                f"UPDATE activities SET {col} = json_extract(raw_json, '$.{key}') "
                f"WHERE {col} IS NULL AND raw_json IS NOT NULL"
            )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_activities_start ON activities (start_time_local)"
        )


def known_activity_ids(user_email: str, db_path: str = DB_NAME) -> set[int]:
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT activity_id FROM activities WHERE user_email = ?", (user_email,)
        ).fetchall()
    return {r[0] for r in rows}


def save_activities(activities: list[dict], user_email: str, db_path: str = DB_NAME) -> None:
    if not activities:
        return
    synced_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = [to_row(a, user_email, synced_at) for a in activities]
    cols = list(rows[0].keys())
    updates = ", ".join(f"{c} = excluded.{c}" for c in cols if c != "activity_id")
    sql = (
        f"INSERT INTO activities ({', '.join(cols)}) "
        f"VALUES ({', '.join(':' + c for c in cols)}) "
        f"ON CONFLICT(activity_id) DO UPDATE SET {updates}"
    )
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.executemany(sql, rows)
