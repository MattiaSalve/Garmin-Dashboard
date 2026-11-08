import pytest
from fastapi.testclient import TestClient

from garmin_dashboards import api, auth, db


class FakeGarmin:
    def __init__(self, email, password):
        self.email = email
        self.password = password

    def login(self, store=None):
        if self.password == "bad":
            raise RuntimeError("401 Unauthorized")

    def logout(self, store=None):
        pass

    def get_activities(self, start, limit):
        return [] if start else [{"activityId": 1, "activityName": "Ride", "activityType": {"typeKey": "road_biking"},
                                  "startTimeLocal": "2026-11-01 08:00:00", "distance": 40000.0, "duration": 5400.0}]

    def get_activity_details(self, activity_id):
        raise RuntimeError("no details in tests")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(db, "LEGACY_DB", tmp_path / "none.db")
    monkeypatch.setattr(auth, "SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setattr(auth, "Garmin", FakeGarmin)
    api.SESSIONS.clear()
    monkeypatch.setattr(api, "sync_wellness", lambda *a, **k: (0, []))
    return TestClient(api.app)


def sign_in(client, email="a@x.com", remember=False):
    return client.post("/api/login", json={"email": email, "password": "pw", "remember": remember})


def test_requires_sign_in(client):
    assert client.get("/api/home").status_code == 401
    assert client.get("/api/me").status_code == 401


def test_bad_password_is_rejected(client):
    r = client.post("/api/login", json={"email": "a@x.com", "password": "bad"})
    assert r.status_code == 401 and "Sign-in failed" in r.json()["detail"]


def test_cookie_is_httponly_and_session_cookie_unless_remembered(client):
    header = sign_in(client).headers["set-cookie"].lower()
    assert "httponly" in header and "max-age" not in header
    assert "max-age" in sign_in(client, remember=True).headers["set-cookie"].lower()


def test_sync_then_read_back(client):
    sign_in(client)
    assert client.post("/api/sync", json={}).json()["new_activities"] == 1
    rows = client.get("/api/activities").json()
    assert [r["activity_name"] for r in rows] == ["Ride"]
    detail = client.get("/api/activities/1").json()
    assert "raw_json" not in detail and "user_email" not in detail
    assert client.get("/api/home").status_code == 200


def test_users_cannot_see_each_others_data(client, tmp_path):
    sign_in(client, "a@x.com")
    client.post("/api/sync", json={})
    client.put("/api/settings", json={"ftp_w": 300})
    other = TestClient(api.app)
    sign_in(other, "b@x.com")
    assert other.get("/api/activities").json() == []
    assert other.get("/api/settings").json()["ftp_w"] != 300
    assert other.get("/api/activities/1").status_code == 404
    assert other.get("/api/activities/1/samples").status_code == 404


def test_remembered_session_survives_server_restart(client):
    sign_in(client, remember=True)
    api.SESSIONS.clear()  # as if the server restarted
    assert client.get("/api/me").json()["email"] == "a@x.com"


def test_sign_out_ends_the_session(client):
    sign_in(client, remember=True)
    client.post("/api/logout")
    api.SESSIONS.clear()
    assert client.get("/api/me").status_code == 401


def test_unknown_settings_are_rejected(client):
    sign_in(client)
    assert client.put("/api/settings", json={"nope": 1}).status_code == 400
