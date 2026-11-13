import sqlite3

import pytest

from garmin_dashboards import db


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(db, "LEGACY_DB", tmp_path / "garmin_data.db")
    return tmp_path


def test_each_user_gets_a_separate_database(data_dir):
    assert db.user_db_path("A@x.com") == db.user_db_path(" a@x.com ")
    assert db.user_db_path("a@x.com") != db.user_db_path("b@x.com")
    db.set_setting("ftp_w", 300, db.ensure_user_db("a@x.com"))
    assert db.get_setting("ftp_w", db.ensure_user_db("b@x.com")) == db.SETTING_DEFAULTS["ftp_w"]


def test_no_path_means_no_database(data_dir):
    with pytest.raises(RuntimeError):
        db.get_setting("ftp_w")


def test_legacy_database_goes_only_to_its_owner(data_dir):
    with sqlite3.connect(db.LEGACY_DB) as conn:
        conn.execute("CREATE TABLE activities (activity_id INTEGER, user_email TEXT)")
        conn.execute("INSERT INTO activities VALUES (1, 'me@x.com')")
    other = db.ensure_user_db("other@x.com")
    assert sqlite3.connect(other).execute("SELECT name FROM sqlite_master").fetchall() == []
    with sqlite3.connect(db.ensure_user_db("me@x.com")) as conn:
        assert conn.execute("SELECT COUNT(*) FROM activities").fetchone()[0] == 1


def test_deleting_the_owner_also_removes_the_legacy_file(data_dir, monkeypatch):
    from garmin_dashboards import auth
    monkeypatch.setattr(auth, "SESSIONS_DIR", data_dir / "sessions")
    with sqlite3.connect(db.LEGACY_DB) as conn:
        conn.execute("CREATE TABLE activities (activity_id INTEGER, user_email TEXT)")
        conn.execute("INSERT INTO activities VALUES (1, 'me@x.com')")
    auth.delete_user_data("other@x.com")
    assert db.LEGACY_DB.exists()
    path = db.ensure_user_db("me@x.com")
    auth.delete_user_data("me@x.com")
    # Gone, and not re-adopted at the next sign-in.
    assert not db.LEGACY_DB.exists()
    assert not sqlite3.connect(db.ensure_user_db("me@x.com")).execute("SELECT name FROM sqlite_master").fetchall()
    assert path == db.user_db_path("me@x.com")
