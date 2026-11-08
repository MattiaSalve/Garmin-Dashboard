import json
from datetime import datetime, timedelta, timezone

import pytest

from garmin_dashboards import auth


class FakeGarmin:
    def __init__(self, email, password):
        self.email = email

    def login(self, store=None):
        self.store = store

    def logout(self, store=None):
        pass


@pytest.fixture(autouse=True)
def sessions(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setattr(auth, "Garmin", FakeGarmin)


def test_session_round_trip_and_sign_out():
    token = auth.new_session_token()
    auth.login("a@x.com", "pw", session_token=token)
    api, email = auth.restore(token)
    assert email == "a@x.com"
    auth.forget(api, token)
    assert auth.restore(token) is None


def test_unknown_or_hostile_tokens_restore_nothing():
    assert auth.restore(None) is None
    assert auth.restore("../../etc") is None


def test_expired_session_is_deleted():
    token = auth.new_session_token()
    auth.login("a@x.com", "pw", session_token=token)
    account = auth._session_dir(token) / "account.json"
    old = datetime.now(timezone.utc) - timedelta(days=auth.SESSION_DAYS + 1)
    account.write_text(json.dumps({"email": "a@x.com", "created": old.isoformat()}))
    assert auth.restore(token) is None and not account.exists()


def test_no_token_means_nothing_is_saved(tmp_path):
    auth.login("a@x.com", "pw")
    assert not (tmp_path / "sessions").exists()
