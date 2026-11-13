import json
from datetime import datetime, timedelta, timezone

import pytest

from garmin_dashboards import auth


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
        self.store = store
        if self.email.startswith("mfa"):
            return "needs_mfa", {"cookies": "secret"}
        return None, None

    def logout(self, store=None):
        pass


@pytest.fixture(autouse=True)
def sessions(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setattr(auth, "Garmin", FakeGarmin)
    auth.PENDING.clear()


def test_session_round_trip_and_sign_out():
    token = auth.new_session_token()
    auth.start_login("a@x.com", "pw", token, remember=True)
    api, email = auth.restore(token)
    assert email == "a@x.com"
    auth.forget(api, token)
    assert auth.restore(token) is None


def test_unknown_or_hostile_tokens_restore_nothing():
    assert auth.restore(None) is None
    assert auth.restore("../../etc") is None


def test_expired_session_is_deleted():
    token = auth.new_session_token()
    auth.start_login("a@x.com", "pw", token, remember=True)
    account = auth._session_dir(token) / "account.json"
    old = datetime.now(timezone.utc) - timedelta(days=auth.SESSION_DAYS + 1)
    account.write_text(json.dumps({"email": "a@x.com", "created": old.isoformat()}))
    assert auth.restore(token) is None and not account.exists()


def test_no_token_means_nothing_is_saved(tmp_path):
    auth.start_login("a@x.com", "pw", auth.new_session_token(), remember=False)
    assert not (tmp_path / "sessions").exists()


def test_clean_login_loads_the_profile_and_forgets_the_password():
    status, api = auth.start_login("a@x.com", "pw")
    assert status == "ok" and api.profile_loaded and api.password is None


def test_mfa_login_waits_in_memory_then_finishes_with_the_code(tmp_path):
    token = auth.new_session_token()
    status, pending = auth.start_login("mfa@x.com", "pw", token, remember=True)
    assert status == "needs_mfa" and not (tmp_path / "sessions").exists()  # nothing on disk yet
    with pytest.raises(RuntimeError):
        auth.finish_login(pending, "000000")
    done = auth.finish_login(pending, " 123456 ")
    assert done.email == "mfa@x.com" and done.token == token and done.api.password is None
    assert auth.restore(token)[1] == "mfa@x.com"
    with pytest.raises(auth.LoginExpired):  # a pending login is used once
        auth.finish_login(pending, "123456")


def test_mfa_gives_up_after_too_many_wrong_codes():
    _, pending = auth.start_login("mfa@x.com", "pw")
    for _ in range(auth.MFA_ATTEMPTS - 1):
        with pytest.raises(RuntimeError):
            auth.finish_login(pending, "000000")
    with pytest.raises(auth.LoginExpired):
        auth.finish_login(pending, "000000")
    assert pending not in auth.PENDING


def test_pending_mfa_login_expires(monkeypatch):
    _, pending = auth.start_login("mfa@x.com", "pw")
    auth.PENDING[pending].created -= auth.PENDING_TTL_S + 1
    with pytest.raises(auth.LoginExpired):
        auth.finish_login(pending, "123456")
