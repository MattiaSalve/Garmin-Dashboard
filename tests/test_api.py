from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from garmin_dashboards import api, auth, db


class FakeClient:
    def dump(self, path):
        import pathlib
        pathlib.Path(path).mkdir(parents=True, exist_ok=True)


class FakeGarmin:
    def __init__(self, email, password, return_on_mfa=False):
        self.email = email
        self.password = password
        self.client = FakeClient()

    def _load_profile_and_settings(self):
        self.profile_loaded = True

    def resume_login(self, state, code):
        if code != "123456":
            raise RuntimeError("Invalid MFA code")
        self.profile_loaded = True

    def login(self, store=None):
        if self.password == "bad":
            raise RuntimeError("401 Unauthorized")
        if self.email.startswith("mfa"):
            return "needs_mfa", {"cookies": "secret"}
        return None, None

    def logout(self, store=None):
        pass

    uploads: list = []  # class-level: shared so tests can inspect what was sent to Garmin
    deleted: list = []
    scheduled: list = []

    def upload_workout(self, payload):
        if payload["workoutName"] == "garmin down":
            raise RuntimeError("503 Service Unavailable")
        FakeGarmin.uploads.append(payload)
        return {"workoutId": 1000 + len(FakeGarmin.uploads)}

    def delete_workout(self, workout_id):
        FakeGarmin.deleted.append(workout_id)

    def schedule_workout(self, workout_id, day):
        FakeGarmin.scheduled.append((workout_id, day))

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
    auth.PENDING.clear()
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
    run = client.get("/api/home?sport=running").json()
    assert run["sport"] == "running" and run["power_prs"] == [] and set(run["levels"]) == {"cycling", "running", "swimming", "gym"}
    assert client.get("/api/home?sport=swimming").json()["pace_unit"] == "/100 m"
    assert client.get("/api/home?sport=curling").status_code == 400


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


def test_settings_out_of_range_are_rejected(client):
    sign_in(client)
    assert client.put("/api/settings", json={"ftp_w": 0}).status_code == 400
    assert client.put("/api/settings", json={"level_running": 7}).status_code == 400
    assert client.put("/api/settings", json={"ftp_w": 260, "level_running": 1}).status_code == 200
    assert client.put("/api/settings", json={"ftp_w": None}).status_code == 200  # clearing is always allowed


def test_hrv_range_must_be_ordered_including_stored_values(client):
    sign_in(client)
    r = client.put("/api/settings", json={"hrv_low": 130, "hrv_high": 100})
    assert r.status_code == 400 and "HRV" in r.json()["detail"]
    assert client.put("/api/settings", json={"hrv_low": 95, "hrv_high": 125}).status_code == 200
    assert client.put("/api/settings", json={"hrv_low": 130}).status_code == 400  # above the stored high


def test_local_date_header_sets_today_within_a_day(client):
    from datetime import UTC, datetime, timedelta
    sign_in(client)
    utc = datetime.now(UTC).date()
    ahead = (utc + timedelta(days=1)).isoformat()
    assert client.get("/api/home", headers={"X-Local-Date": ahead}).json()["today"] == ahead
    for bad in ["2001-01-01", "not-a-date"]:
        assert client.get("/api/home", headers={"X-Local-Date": bad}).json()["today"] == utc.isoformat()


def test_delete_account_removes_data_and_signs_out_everywhere(client, tmp_path):
    sign_in(client, remember=True)
    other_browser = TestClient(api.app)
    sign_in(other_browser, email="A@x.com ", remember=True)  # same account, different spelling
    bystander = TestClient(api.app)
    sign_in(bystander, email="b@x.com", remember=True)
    mine = Path(db.user_db_path("a@x.com"))
    assert mine.exists() and len(list((tmp_path / "sessions").iterdir())) == 3

    assert client.delete("/api/account").status_code == 200
    assert not mine.exists()
    assert len(list((tmp_path / "sessions").iterdir())) == 1  # only b@x.com's remembered sign-in is left
    api.SESSIONS.clear()  # as after a server restart: nothing to restore from either
    assert client.get("/api/me").status_code == 401
    assert other_browser.get("/api/me").status_code == 401
    assert bystander.get("/api/me").json()["email"] == "b@x.com"
    assert Path(db.user_db_path("b@x.com")).exists()


def test_mfa_sign_in_takes_two_steps(client):
    r = client.post("/api/login", json={"email": "mfa@x.com", "password": "pw", "remember": True}).json()
    assert r["mfa_required"] and "email" not in r
    assert client.get("/api/me").status_code == 401  # no cookie until the code is accepted
    bad = client.post("/api/login/mfa", json={"pending": r["pending"], "code": "000000"})
    assert bad.status_code == 401 and "code" in bad.json()["detail"]
    ok = client.post("/api/login/mfa", json={"pending": r["pending"], "code": "123456"})
    assert ok.json() == {"email": "mfa@x.com"}
    assert client.get("/api/me").json()["email"] == "mfa@x.com"
    api.SESSIONS.clear()  # remembered: survives a server restart
    assert client.get("/api/me").json()["email"] == "mfa@x.com"


def test_unknown_pending_mfa_login_is_a_clear_401(client):
    r = client.post("/api/login/mfa", json={"pending": "nope", "code": "123456"})
    assert r.status_code == 401 and "expired" in r.json()["detail"]


def test_ftp_hint_only_suggests_a_clear_rise():
    assert api._ftp_hint(None, 252) is None
    assert api._ftp_hint(258, 252) is None  # under 3%
    assert api._ftp_hint(240, 252) is None  # never suggests lowering
    assert api._ftp_hint(266.4, 252) == {"estimate": 266, "current": 252}


def test_home_returns_ftp_hint_for_cycling_only(client):
    sign_in(client)
    me = api.SESSIONS[next(iter(api.SESSIONS))]
    from datetime import date
    db.save_activities([{"activityId": 1, "startTimeLocal": f"{date.today()} 08:00:00",
                         "activityType": {"typeKey": "road_biking"}, "maxAvgPower_1200": 300}], "a@x.com", me.db_path)
    assert client.get("/api/home?sport=cycling").json()["ftp_hint"] == {"estimate": 285, "current": 252}
    assert client.get("/api/home?sport=running").json()["ftp_hint"] is None


PUSH = {"sport": "cycling", "name": "Endurance", "steps": [
    {"minutes": 10, "frac_low": 0.45, "frac_high": 0.6, "label": "Warm-up"},
    {"minutes": 40, "frac_low": 0.6, "frac_high": 0.72, "label": "Endurance"},
]}


def test_push_workout_uploads_schedules_and_replaces_same_day(client):
    from datetime import UTC, datetime
    for log in (FakeGarmin.uploads, FakeGarmin.deleted, FakeGarmin.scheduled):
        log.clear()
    sign_in(client)
    client.put("/api/settings", json={"ftp_w": 300})
    day = datetime.now(UTC).date().isoformat()
    r = client.post("/api/workout/push", json=PUSH, headers={"X-Local-Date": day}).json()
    assert r == {"workout_id": 1001, "date": day, "replaced": False}
    step = FakeGarmin.uploads[0]["workoutSegments"][0]["workoutSteps"][1]
    assert (step["targetValueOne"], step["targetValueTwo"]) == (180, 216)  # the server's FTP, not the client's
    assert FakeGarmin.scheduled == [(1001, day)]
    again = client.post("/api/workout/push", json=PUSH, headers={"X-Local-Date": day}).json()
    assert again["replaced"] and FakeGarmin.deleted == [1001]


def test_push_workout_rejects_bad_input_and_reports_garmin_errors(client):
    sign_in(client)
    assert client.post("/api/workout/push", json={**PUSH, "sport": "yoga"}).status_code == 400
    assert client.post("/api/workout/push", json={**PUSH, "steps": []}).status_code == 400
    assert client.post("/api/workout/push", json={**PUSH, "sport": "gym"}).status_code == 400  # no exercises
    too_long = {**PUSH, "steps": [{**PUSH["steps"][0], "minutes": 999}]}
    assert client.post("/api/workout/push", json=too_long).status_code == 422
    r = client.post("/api/workout/push", json={**PUSH, "name": "garmin down"})
    assert r.status_code == 502 and "503" in r.json()["detail"]


def test_push_swim_uses_pool_length_and_swim_pace(client):
    FakeGarmin.uploads.clear()
    sign_in(client)
    client.put("/api/settings", json={"swim_threshold_pace_s": 100, "pool_length_m": 50})
    swim = {"sport": "swimming", "name": "Aerobic swim", "steps": [{"minutes": 10, "frac_low": 0.95, "frac_high": 1.05, "label": "Steady swim"}]}
    assert client.post("/api/workout/push", json=swim).status_code == 200
    payload = FakeGarmin.uploads[-1]
    step = payload["workoutSegments"][0]["workoutSteps"][0]
    assert payload["poolLength"] == 50 and step["endConditionValue"] == 600  # 10 min at 1:40/100 m


def test_push_gym_session(client):
    FakeGarmin.uploads.clear()
    sign_in(client)
    gym_body = {"sport": "gym", "name": "Lower body", "exercises": [
        {"name": "Goblet or back squat", "group": "legs", "sets": 3, "reps": "4–6", "rpe": 8, "rest_s": 150, "note": ""},
        {"name": "Plank", "group": "core", "sets": 3, "reps": "30–45 s", "rpe": 7, "rest_s": 45},
    ]}
    assert client.post("/api/workout/push", json=gym_body).status_code == 200
    payload = FakeGarmin.uploads[-1]
    warmup, squat, plank = payload["workoutSegments"][0]["workoutSteps"]
    assert payload["sportType"]["sportTypeKey"] == "strength_training" and warmup["stepType"]["stepTypeKey"] == "warmup"
    assert squat["workoutSteps"][0]["exerciseName"] == "GOBLET_SQUAT" and plank["workoutSteps"][0]["category"] == "PLANK"
