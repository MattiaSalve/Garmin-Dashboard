import json
import sqlite3

from garmin_dashboards import db
from garmin_dashboards.sync import PAGE_SIZE, fetch_activities, sync


class FakeApi:
    """Newest-first activity list, paged like Garmin's."""

    def __init__(self, ids):
        self.activities = [{"activityId": i, "activityName": f"a{i}"} for i in ids]
        self.calls = 0

    def get_activities(self, start, limit):
        self.calls += 1
        return self.activities[start : start + limit]


def test_full_fetch_pages_until_exhausted():
    api = FakeApi(range(PAGE_SIZE * 2 + 5, 0, -1))
    assert len(fetch_activities(api)) == PAGE_SIZE * 2 + 5
    assert api.calls == 3


def test_incremental_fetch_stops_at_known_page():
    api = FakeApi(range(PAGE_SIZE * 3, 0, -1))
    fetch_activities(api, known_ids={PAGE_SIZE * 3 - 1})
    assert api.calls == 1


def test_sync_counts_new_and_upserts(tmp_path, monkeypatch):
    path = str(tmp_path / "t.db")
    monkeypatch.setattr(db, "DB_NAME", path)
    monkeypatch.setattr(db.known_activity_ids, "__defaults__", (path,))
    monkeypatch.setattr(db.save_activities, "__defaults__", (path,))
    assert sync(FakeApi([3, 2, 1]), "me") == (3, 3)
    assert sync(FakeApi([4, 3, 2, 1]), "me") == (1, 4)
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM activities").fetchone()[0] == 4
        raw, synced = conn.execute("SELECT raw_json, last_synced FROM activities LIMIT 1").fetchone()
    assert json.loads(raw)["activityId"] and synced
