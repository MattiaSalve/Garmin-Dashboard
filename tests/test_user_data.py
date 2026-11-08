import sqlite3

import pytest

from garmin_dashboards import db


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(db, "LEGACY_DB", tmp_path / "garmin_data.db")
    token = db._active_db.set(None)
    yield tmp_path
    db._active_db.reset(token)


def test_each_user_gets_a_separate_database(data_dir):
    assert db.user_db_path("A@x.com") == db.user_db_path(" a@x.com ")
    assert db.user_db_path("a@x.com") != db.user_db_path("b@x.com")
    db.use_user("a@x.com")
    db.set_setting("ftp_w", 300)
    db.use_user("b@x.com")
    assert db.get_setting("ftp_w") == db.SETTING_DEFAULTS["ftp_w"]


def test_no_user_means_no_database(data_dir):
    with pytest.raises(RuntimeError):
        db.get_setting("ftp_w")


def test_legacy_database_goes_only_to_its_owner(data_dir):
    with sqlite3.connect(db.LEGACY_DB) as conn:
        conn.execute("CREATE TABLE activities (activity_id INTEGER, user_email TEXT)")
        conn.execute("INSERT INTO activities VALUES (1, 'me@x.com')")
    db.use_user("other@x.com")
    assert not (data_dir / "data").exists() or sqlite3.connect(db.resolve()).execute(
        "SELECT name FROM sqlite_master"
    ).fetchall() == []
    db.use_user("me@x.com")
    with sqlite3.connect(db.resolve()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM activities").fetchone()[0] == 1
