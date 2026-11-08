"""Download daily recovery data (HRV, resting HR, sleep, Body Battery) into the local database.

Each Garmin endpoint is parsed by a pure function into {day: {column: value}}; the
results are merged per day. One endpoint failing (not every device supports every
metric) never blocks the others.
"""

import json
import logging
from datetime import date, timedelta

from garmin_dashboards.db import last_wellness_day, save_wellness

log = logging.getLogger(__name__)

FIRST_SYNC_DAYS = 120
# Garmin revises last night's HRV/sleep after the morning; re-fetch a couple of days.
OVERLAP_DAYS = 2
# Several range endpoints cap the span per request; stay under it.
CHUNK_DAYS = 28


def parse_hrv(payload: dict | None) -> dict[str, dict]:
    out = {}
    for s in (payload or {}).get("hrvSummaries") or []:
        baseline = s.get("baseline") or {}
        out[s["calendarDate"]] = {
            "hrv_avg": s.get("lastNightAvg"),
            "hrv_weekly_avg": s.get("weeklyAvg"),
            "hrv_baseline_low": baseline.get("balancedLow"),
            "hrv_baseline_high": baseline.get("balancedUpper"),
            "hrv_status": s.get("status"),
        }
    return out


def parse_sleep(payload: list | None) -> dict[str, dict]:
    out = {}
    for d in payload or []:
        v = d.get("values") or {}
        row = {
            "sleep_score": v.get("sleepScore"),
            "sleep_s": v.get("totalSleepTimeInSeconds"),
            "deep_s": v.get("deepTime"),
            "rem_s": v.get("remTime"),
        }
        # Sleep also carries overnight HRV / resting HR; used only as a fallback below.
        row["_hrv"] = v.get("avgOvernightHrv")
        row["_rhr"] = v.get("restingHeartRate")
        out[d["calendarDate"]] = row
    return out


def parse_rhr(payload: list | None) -> dict[str, dict]:
    return {d["calendarDate"]: {"rhr": d.get("value")} for d in payload or [] if d.get("value") is not None}


def parse_body_battery(payload: list | None) -> dict[str, dict]:
    out = {}
    for d in payload or []:
        levels = [p[1] for p in d.get("bodyBatteryValuesArray") or [] if len(p) > 1 and p[1] is not None]
        out[d["date"]] = {
            "bb_charged": d.get("charged"),
            "bb_high": max(levels) if levels else None,
            "bb_low": min(levels) if levels else None,
        }
    return out


def parse_readiness(payload: list | None) -> dict[str, dict]:
    out = {}
    for d in payload or []:
        day, score = d.get("calendarDate"), d.get("score")
        if day and score is not None:
            # Several readings per day are possible; keep the first (morning) one.
            out.setdefault(day, {"training_readiness": score})
    return out


def merge_days(*parts: dict[str, dict]) -> dict[str, dict]:
    """Merge per-day dicts; fill hrv/rhr from sleep when the dedicated endpoint had nothing."""
    merged: dict[str, dict] = {}
    for part in parts:
        for day, values in part.items():
            merged.setdefault(day, {}).update(values)
    for values in merged.values():
        hrv, rhr = values.pop("_hrv", None), values.pop("_rhr", None)
        if values.get("hrv_avg") is None and hrv is not None:
            values["hrv_avg"] = hrv
        if values.get("rhr") is None and rhr is not None:
            values["rhr"] = rhr
    return merged


ENDPOINTS = [
    # (name, api call taking (start, end) strings, parser)
    ("hrv", lambda api, s, e: api.get_hrv_data_range(s, e), parse_hrv),
    ("sleep", lambda api, s, e: api.get_sleep_daily(s, e), parse_sleep),
    ("rhr", lambda api, s, e: api.get_rhr_daily(s, e), parse_rhr),
    ("body_battery", lambda api, s, e: api.get_body_battery(s, e), parse_body_battery),
    ("readiness", lambda api, s, e: api.get_training_readiness_range(s, e), parse_readiness),
]


def _chunks(start: date, end: date):
    while start <= end:
        stop = min(start + timedelta(days=CHUNK_DAYS - 1), end)
        yield start, stop
        start = stop + timedelta(days=1)


def fetch_wellness(api, start: date, end: date) -> tuple[dict[str, dict], list[str]]:
    """Fetch and merge all endpoints. Returns (rows by day, names of endpoints that failed)."""
    parsed, raw, failed = [], {}, []
    for name, call, parser in ENDPOINTS:
        try:
            for s, e in _chunks(start, end):
                payload = call(api, s.isoformat(), e.isoformat())
                rows = parser(payload)
                parsed.append(rows)
                for day in rows:
                    raw.setdefault(day, {})[name] = _raw_for_day(payload, day)
        except Exception as exc:  # the garminconnect library raises several error types
            log.warning("Wellness endpoint %s failed: %s", name, exc)
            failed.append(name)
    merged = merge_days(*parsed)
    for day, values in merged.items():
        values["raw_json"] = json.dumps(raw.get(day, {}), default=str)
    return merged, failed


def _raw_for_day(payload, day: str):
    """The slice of an endpoint payload that belongs to one day (kept for later backfills)."""
    items = payload.get("hrvSummaries") if isinstance(payload, dict) else payload
    for item in items or []:
        if isinstance(item, dict) and day in (item.get("calendarDate"), item.get("date")):
            return item
    return None


def sync_wellness(api, full: bool = False, today: date | None = None) -> tuple[int, list[str]]:
    """Fetch recent days into the wellness table. Returns (days stored, failed endpoints)."""
    today = today or date.today()
    last = None if full else last_wellness_day()
    start = (
        date.fromisoformat(last) - timedelta(days=OVERLAP_DAYS)
        if last
        else today - timedelta(days=FIRST_SYNC_DAYS - 1)
    )
    rows, failed = fetch_wellness(api, start, today)
    save_wellness(rows)
    return len(rows), failed
