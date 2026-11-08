import json
import sqlite3
from datetime import date
from pathlib import Path

from garmin_dashboards import db, wellness

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "wellness.json").read_text())


def test_parsers_on_real_payloads():
    hrv = wellness.parse_hrv(FIXTURE["hrv"])
    assert hrv["2026-11-08"] == {"hrv_avg": 90, "hrv_weekly_avg": 88, "hrv_baseline_low": 96,
                                 "hrv_baseline_high": 124, "hrv_status": "LOW"}
    sleep = wellness.parse_sleep(FIXTURE["sleep"])
    assert sleep["2026-11-08"]["sleep_score"] == 84 and sleep["2026-11-08"]["sleep_s"] == 30645
    assert wellness.parse_rhr(FIXTURE["rhr"])["2026-11-08"] == {"rhr": 48}
    bb = wellness.parse_body_battery(FIXTURE["bb"])["2026-11-07"]
    assert bb["bb_charged"] == 53 and bb["bb_high"] >= bb["bb_low"]


def test_merge_uses_sleep_as_fallback_only():
    merged = wellness.merge_days(
        {"d1": {"hrv_avg": 80}, "d2": {"hrv_avg": None}},
        {"d1": {"sleep_score": 70, "_hrv": 99, "_rhr": 50}, "d2": {"_hrv": 77, "_rhr": None}},
    )
    assert merged["d1"] == {"hrv_avg": 80, "sleep_score": 70, "rhr": 50}
    assert merged["d2"] == {"hrv_avg": 77}


class FakeApi:
    def get_hrv_data_range(self, s, e):
        return FIXTURE["hrv"]

    def get_sleep_daily(self, s, e):
        return FIXTURE["sleep"]

    def get_rhr_daily(self, s, e):
        return FIXTURE["rhr"]

    def get_body_battery(self, s, e):
        return FIXTURE["bb"]

    def get_training_readiness_range(self, s, e):
        raise RuntimeError("not supported on this device")


def test_fetch_survives_failing_endpoint_and_upserts(tmp_path):
    rows, failed = wellness.fetch_wellness(FakeApi(), date(2026, 11, 7), date(2026, 11, 8))
    assert failed == ["readiness"]
    assert rows["2026-11-08"]["hrv_avg"] == 90 and rows["2026-11-08"]["rhr"] == 48
    assert set(json.loads(rows["2026-11-08"]["raw_json"])) >= {"hrv", "sleep", "rhr"}

    path = str(tmp_path / "t.db")
    db.save_wellness(rows, path)
    db.save_wellness({"2026-11-08": {"sleep_score": 60}}, path)  # partial update keeps other columns
    with sqlite3.connect(path) as conn:
        row = conn.execute("SELECT hrv_avg, rhr, sleep_score FROM wellness WHERE day = '2026-11-08'").fetchone()
    assert row == (90, 48, 60)
    assert db.last_wellness_day(path) == "2026-11-08"


def test_chunks_cover_range_without_gaps():
    chunks = list(wellness._chunks(date(2026, 1, 1), date(2026, 3, 1)))
    assert chunks[0][0] == date(2026, 1, 1) and chunks[-1][1] == date(2026, 3, 1)
    assert all((b - a).days < wellness.CHUNK_DAYS for a, b in chunks)
    assert all((n[0] - p[1]).days == 1 for p, n in zip(chunks, chunks[1:]))


def test_settings_defaults_and_overrides(tmp_path):
    path = str(tmp_path / "t.db")
    assert db.get_setting("ftp_w", path) == 252.0 and db.get_setting("hrv_low", path) is None
    db.set_setting("hrv_low", 100, path)
    db.set_setting("ftp_w", None, path)
    assert db.all_settings(path)["hrv_low"] == 100.0 and db.get_setting("ftp_w", path) == 252.0
