"""SQLite storage for Garmin activities. All stored values are metric."""

import hashlib
import json
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

# Each signed-in user gets their own database file, so nobody ever sees another user's data.
DATA_DIR = Path(os.environ.get("GARMIN_DASHBOARDS_DATA") or "data")
LEGACY_DB = Path("garmin_data.db")  # the old single-user file, adopted by its owner on first sign-in



def user_db_path(email: str) -> str:
    digest = hashlib.sha256(email.strip().lower().encode()).hexdigest()[:20]
    return str(DATA_DIR / f"{digest}.db")


def ensure_user_db(email: str) -> str:
    """Path of `email`'s database, adopting the old single-user file if it is theirs."""
    path = Path(user_db_path(email))
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() and _legacy_belongs_to(email):
        shutil.copy2(LEGACY_DB, path)
    return str(path)


def _legacy_belongs_to(email: str) -> bool:
    if not LEGACY_DB.exists():
        return False
    try:
        with sqlite3.connect(LEGACY_DB) as conn:
            owners = {r[0] for r in conn.execute("SELECT DISTINCT user_email FROM activities")}
    except sqlite3.Error:
        return False
    return owners == {email}


def resolve(db_path: str | None) -> str:
    """The database path as a string. Every caller passes the signed-in user's file explicitly (the API
    keeps it on the session); None means a caller forgot, which must never fall back to someone's data."""
    if db_path is None:
        raise RuntimeError("No database given: pass the signed-in user's db_path.")
    return str(db_path)

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

# The training plan: one row per planned session (a day can hold two, e.g. a ride and a gym session),
# and the changes proposed by the rules or the coach, applied only when the athlete says so.
PLAN_SCHEMA = """
CREATE TABLE IF NOT EXISTS plan_days (
    day          TEXT NOT NULL,
    slot         INTEGER NOT NULL DEFAULT 0,
    sport        TEXT NOT NULL,
    kind         TEXT NOT NULL,
    minutes      INTEGER NOT NULL,
    planned_load REAL NOT NULL DEFAULT 0,
    source       TEXT NOT NULL DEFAULT 'auto',
    locked       INTEGER NOT NULL DEFAULT 0,
    note         TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (day, slot)
)
"""
PROPOSALS_SCHEMA = """
CREATE TABLE IF NOT EXISTS plan_proposals (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    created      TEXT NOT NULL,
    dedupe_key   TEXT NOT NULL UNIQUE,
    kind         TEXT NOT NULL,
    reason       TEXT NOT NULL,
    changes_json TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'open',
    source       TEXT NOT NULL DEFAULT 'rules'
)
"""
PLAN_COLUMNS = ("day", "slot", "sport", "kind", "minutes", "planned_load", "source", "locked", "note")

# Defaults for user settings (stored as text, converted on read).
SETTING_DEFAULTS: dict[str, float | None] = {
    "ftp_w": 252.0,
    "hrv_low": None,   # personal HRV range; None = use Garmin's baseline
    "hrv_high": None,
    "weekly_hours_target": None,
    "yearly_km_goal": None,
    "level_cycling": None,   # 1 beginner, 2 intermediate, 3 advanced; None = estimate from recent hours
    "level_running": None,
    "level_swimming": None,
    "level_gym": None,
    "gym_goal": None,  # 1 general strength, 2 support endurance training, 3 build muscle; None = general strength
    "run_threshold_pace_s": None,  # seconds per km you can hold for about an hour; None = effort-only targets
    "swim_threshold_pace_s": None,  # seconds per 100 m you could hold for ~20 min of continuous swimming
    "pool_length_m": 25.0,  # for swim workouts sent to the watch (distance steps count pool lengths)
}

# Sensible range (inclusive) for each setting; values outside are rejected by the API.
SETTING_LIMITS: dict[str, tuple[float, float]] = {
    "ftp_w": (50, 600),
    "hrv_low": (10, 250),
    "hrv_high": (10, 250),
    "weekly_hours_target": (0, 40),
    "yearly_km_goal": (0, 100_000),
    "level_cycling": (1, 3),
    "level_running": (1, 3),
    "level_swimming": (1, 3),
    "level_gym": (1, 3),
    "gym_goal": (1, 3),
    "run_threshold_pace_s": (120, 900),
    "swim_threshold_pace_s": (50, 300),
    "pool_length_m": (10, 100),
}


def settings_errors(new: dict[str, float | None], current: dict[str, float | None]) -> list[str]:
    """Problems with a settings update (empty = fine). `current` is what is stored now."""
    errors = [f"Unknown setting: {k}." for k in sorted(set(new) - set(SETTING_DEFAULTS))]
    for key, value in new.items():
        if key in SETTING_LIMITS and value is not None:
            low, high = SETTING_LIMITS[key]
            if not low <= value <= high:
                errors.append(f"{key} must be between {low:g} and {high:g}.")
    merged = {**current, **new}
    if merged.get("hrv_low") is not None and merged.get("hrv_high") is not None and merged["hrv_low"] >= merged["hrv_high"]:
        errors.append("The HRV range low value must be below the high value.")
    return errors


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


def init_db(db_path: str | None = None) -> None:
    with sqlite3.connect(resolve(db_path)) as conn:
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
        conn.execute(PLAN_SCHEMA)
        conn.execute(PROPOSALS_SCHEMA)


def save_wellness(days: dict[str, dict], db_path: str | None = None) -> None:
    """Upsert wellness rows keyed by day. Only the given columns are overwritten."""
    if not days:
        return
    synced_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    init_db(db_path)
    with sqlite3.connect(resolve(db_path)) as conn:
        for day, values in days.items():
            row = {**values, "day": day, "last_synced": synced_at}
            cols = list(row)
            updates = ", ".join(f"{c} = excluded.{c}" for c in cols if c != "day")
            conn.execute(
                f"INSERT INTO wellness ({', '.join(cols)}) VALUES ({', '.join(':' + c for c in cols)}) "
                f"ON CONFLICT(day) DO UPDATE SET {updates}",
                row,
            )


def last_wellness_day(db_path: str | None = None) -> str | None:
    init_db(db_path)
    with sqlite3.connect(resolve(db_path)) as conn:
        return conn.execute("SELECT MAX(day) FROM wellness").fetchone()[0]


def get_setting(key: str, db_path: str | None = None) -> float | None:
    init_db(db_path)
    with sqlite3.connect(resolve(db_path)) as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row is None or row[0] in (None, ""):
        return SETTING_DEFAULTS.get(key)
    return float(row[0])


def set_setting(key: str, value: float | None, db_path: str | None = None) -> None:
    init_db(db_path)
    with sqlite3.connect(resolve(db_path)) as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, None if value is None else str(value)),
        )


def get_text_setting(key: str, db_path: str | None = None) -> str:
    """Free-text settings (e.g. the athlete's goals) share the settings table but skip the numeric handling."""
    init_db(db_path)
    with sqlite3.connect(resolve(db_path)) as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return (row[0] or "") if row else ""


def set_text_setting(key: str, value: str, db_path: str | None = None) -> None:
    init_db(db_path)
    with sqlite3.connect(resolve(db_path)) as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value.strip()),
        )


def text_settings_with_prefix(prefix: str, db_path: str | None = None) -> dict[str, str]:
    """Text settings whose key starts with `prefix`, keyed by the rest of the key."""
    init_db(db_path)
    with sqlite3.connect(resolve(db_path)) as conn:
        rows = conn.execute("SELECT key, value FROM settings WHERE substr(key, 1, ?) = ?", (len(prefix), prefix)).fetchall()
    return {k[len(prefix):]: v for k, v in rows if v not in (None, "")}


def delete_setting(key: str, db_path: str | None = None) -> None:
    init_db(db_path)
    with sqlite3.connect(resolve(db_path)) as conn:
        conn.execute("DELETE FROM settings WHERE key = ?", (key,))


def all_settings(db_path: str | None = None) -> dict[str, float | None]:
    return {k: get_setting(k, db_path) for k in SETTING_DEFAULTS}


def known_activity_ids(user_email: str, db_path: str | None = None) -> set[int]:
    init_db(db_path)
    with sqlite3.connect(resolve(db_path)) as conn:
        rows = conn.execute(
            "SELECT activity_id FROM activities WHERE user_email = ?", (user_email,)
        ).fetchall()
    return {r[0] for r in rows}


def save_activities(activities: list[dict], user_email: str, db_path: str | None = None) -> None:
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
    with sqlite3.connect(resolve(db_path)) as conn:
        conn.executemany(sql, rows)


# ------------------------------------------------------------------ training plan


def _plan_row(r: sqlite3.Row) -> dict:
    return {**dict(r), "locked": bool(r["locked"])}


def get_plan_days(start: str, end: str, db_path: str | None = None) -> list[dict]:
    """Planned sessions from `start` to `end` (ISO dates, inclusive), in day and slot order."""
    init_db(db_path)
    with sqlite3.connect(resolve(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM plan_days WHERE day BETWEEN ? AND ? ORDER BY day, slot", (start, end)
        ).fetchall()
    return [_plan_row(r) for r in rows]


def last_plan_day(db_path: str | None = None) -> str | None:
    init_db(db_path)
    with sqlite3.connect(resolve(db_path)) as conn:
        return conn.execute("SELECT MAX(day) FROM plan_days").fetchone()[0]


def save_plan_days(rows: list[dict], db_path: str | None = None, replace_auto_from: str | None = None) -> None:
    """Upsert planned sessions. With `replace_auto_from`, unlocked auto sessions from that day on are deleted
    first (a regenerate), so only the athlete's own and applied changes survive."""
    init_db(db_path)
    with sqlite3.connect(resolve(db_path)) as conn:
        if replace_auto_from:
            conn.execute("DELETE FROM plan_days WHERE day >= ? AND source = 'auto' AND locked = 0", (replace_auto_from,))
        updates = ", ".join(f"{c} = excluded.{c}" for c in PLAN_COLUMNS if c not in ("day", "slot"))
        conn.executemany(
            f"INSERT INTO plan_days ({', '.join(PLAN_COLUMNS)}) VALUES ({', '.join(':' + c for c in PLAN_COLUMNS)}) "
            f"ON CONFLICT(day, slot) DO UPDATE SET {updates}",
            [{**{c: r.get(c) for c in PLAN_COLUMNS}, "locked": int(bool(r.get("locked")))} for r in rows],
        )


def delete_plan_day(day: str, slot: int, db_path: str | None = None) -> None:
    init_db(db_path)
    with sqlite3.connect(resolve(db_path)) as conn:
        conn.execute("DELETE FROM plan_days WHERE day = ? AND slot = ?", (day, slot))


def clear_plan(db_path: str | None = None) -> None:
    init_db(db_path)
    with sqlite3.connect(resolve(db_path)) as conn:
        conn.execute("DELETE FROM plan_days")
        conn.execute("DELETE FROM plan_proposals")


def _proposal_row(r: sqlite3.Row) -> dict:
    out = dict(r)
    out["changes"] = json.loads(out.pop("changes_json"))
    return out


def add_proposal(key: str, kind: str, reason: str, changes: list[dict], source: str = "rules",
                 db_path: str | None = None) -> int | None:
    """Store a proposal; None when one with the same key was already made (open, applied or dismissed)."""
    init_db(db_path)
    created = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with sqlite3.connect(resolve(db_path)) as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO plan_proposals (created, dedupe_key, kind, reason, changes_json, source) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (created, key, kind, reason, json.dumps(changes), source),
        )
        return cur.lastrowid if cur.rowcount else None


def get_proposals(status: str | None = "open", db_path: str | None = None) -> list[dict]:
    init_db(db_path)
    with sqlite3.connect(resolve(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        sql, args = "SELECT * FROM plan_proposals", ()
        if status:
            sql, args = sql + " WHERE status = ?", (status,)
        rows = conn.execute(sql + " ORDER BY id", args).fetchall()
    return [_proposal_row(r) for r in rows]


def get_proposal(pid: int, db_path: str | None = None) -> dict | None:
    init_db(db_path)
    with sqlite3.connect(resolve(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM plan_proposals WHERE id = ?", (pid,)).fetchone()
    return _proposal_row(row) if row else None


def set_proposal_status(pid: int, status: str, db_path: str | None = None) -> None:
    init_db(db_path)
    with sqlite3.connect(resolve(db_path)) as conn:
        conn.execute("UPDATE plan_proposals SET status = ? WHERE id = ?", (status, pid))
