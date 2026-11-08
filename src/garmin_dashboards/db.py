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


# One row per local calendar day of recovery data (HRV, resting HR, sleep, Body Battery).
WELLNESS_SCHEMA = """
CREATE TABLE IF NOT EXISTS wellness (
    day                 TEXT PRIMARY KEY,
    hrv_avg             REAL,
    hrv_weekly_avg      REAL,
    hrv_baseline_low    REAL,
    hrv_baseline_high   REAL,
    hrv_status          TEXT,
    rhr                 REAL,
    sleep_score         REAL,
    sleep_s             REAL,
    deep_s              REAL,
    rem_s               REAL,
    bb_charged          REAL,
    bb_high             REAL,
    bb_low              REAL,
    stress_avg          REAL,
    training_readiness  REAL,
    last_synced         TEXT,
    raw_json            TEXT
)
"""

SETTINGS_SCHEMA = "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)"

# Defaults for user settings (stored as text, converted on read).
SETTING_DEFAULTS: dict[str, float | None] = {
    "ftp_w": 252.0,
    "hrv_low": None,   # personal HRV range; None = use Garmin's baseline
    "hrv_high": None,
    "weekly_hours_target": None,
    "yearly_km_goal": None,
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
        conn.execute(WELLNESS_SCHEMA)
        conn.execute(SETTINGS_SCHEMA)


def save_wellness(days: dict[str, dict], db_path: str = DB_NAME) -> None:
    """Upsert wellness rows keyed by day. Only the given columns are overwritten."""
    if not days:
        return
    synced_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        for day, values in days.items():
            row = {**values, "day": day, "last_synced": synced_at}
            cols = list(row)
            updates = ", ".join(f"{c} = excluded.{c}" for c in cols if c != "day")
            conn.execute(
                f"INSERT INTO wellness ({', '.join(cols)}) VALUES ({', '.join(':' + c for c in cols)}) "
                f"ON CONFLICT(day) DO UPDATE SET {updates}",
                row,
            )


def last_wellness_day(db_path: str = DB_NAME) -> str | None:
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        return conn.execute("SELECT MAX(day) FROM wellness").fetchone()[0]


def get_setting(key: str, db_path: str = DB_NAME) -> float | None:
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row is None or row[0] in (None, ""):
        return SETTING_DEFAULTS.get(key)
    return float(row[0])


def set_setting(key: str, value: float | None, db_path: str = DB_NAME) -> None:
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, None if value is None else str(value)),
        )


def all_settings(db_path: str = DB_NAME) -> dict[str, float | None]:
    return {k: get_setting(k, db_path) for k in SETTING_DEFAULTS}


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
