"""Garmin Connect authentication.

"Keep me signed in" works with a random session token kept in a browser cookie. On the server,
the token's hash names a private folder holding that user's Garmin OAuth tokens (never the
password), so a returning browser is signed back in without asking. Sessions expire after
SESSION_DAYS and are deleted on sign-out.

Accounts with two-step verification sign in in two calls: start_login() returns a pending id, and
finish_login() completes it with the emailed code. Pending sign-ins live only in memory.
"""

import hashlib
import json
import secrets
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from garminconnect import Garmin

from garmin_dashboards import db
from garmin_dashboards.db import DATA_DIR

COOKIE_NAME = "gd_session"
SESSION_DAYS = 30
SESSIONS_DIR = DATA_DIR / "sessions"


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def _session_dir(token: str) -> Path:
    # Hashing means a hostile cookie value can never point outside the sessions folder.
    return SESSIONS_DIR / hashlib.sha256(token.encode()).hexdigest()[:40]


@dataclass
class PendingLogin:
    """A sign-in waiting for the code Garmin emailed. Kept in memory only: `client_state` holds Garmin
    session cookies, so it is never written to disk."""
    api: Garmin
    client_state: Any
    email: str
    token: str | None  # the browser's cookie token, issued once the code is accepted
    remember: bool
    created: float = field(default_factory=time.monotonic)
    attempts: int = 0


PENDING: dict[str, PendingLogin] = {}
PENDING_TTL_S = 300
MFA_ATTEMPTS = 3


class LoginExpired(Exception):
    """The pending sign-in is unknown, too old, or out of attempts: start again with the password."""


def _purge_pending() -> None:
    now = time.monotonic()
    for key in [k for k, p in PENDING.items() if now - p.created > PENDING_TTL_S]:
        del PENDING[key]


def _remember(api: Garmin, email: str, token: str | None, remember: bool) -> None:
    """Save the Garmin tokens and account file for a "keep me signed in" browser."""
    if not (token and remember):
        return
    folder = _session_dir(token)
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    api.client.dump(str(folder / "tokens"))
    account = folder / "account.json"
    account.write_text(json.dumps({"email": email, "created": datetime.now(timezone.utc).isoformat()}))
    account.chmod(0o600)


def start_login(email: str, password: str, token: str | None = None, remember: bool = False) -> tuple[str, Any]:
    """Sign in to Garmin. Returns ("ok", api), or ("needs_mfa", pending_id) when Garmin wants the code it
    emailed; then call finish_login(pending_id, code). With `remember`, the sign-in is saved under `token`."""
    _purge_pending()
    api = Garmin(email, password, return_on_mfa=True)
    status, client_state = api.login()
    if status == "needs_mfa":
        pending_id = secrets.token_urlsafe(24)
        PENDING[pending_id] = PendingLogin(api, client_state, email, token, remember)
        return "needs_mfa", pending_id
    # In return_on_mfa mode the library stops before loading the profile it needs for most calls (the
    # display name goes into URLs), so do that step here. It is private, hence kept to this one place.
    api._load_profile_and_settings()
    api.password = None
    _remember(api, email, token, remember)
    return "ok", api


def finish_login(pending_id: str, code: str) -> PendingLogin:
    """Finish a sign-in with Garmin's emailed code. Returns the completed login (api, email, token, remember).

    Raises LoginExpired when the pending sign-in is gone, and re-raises Garmin's error for a wrong code
    (the pending sign-in stays for another try, up to MFA_ATTEMPTS)."""
    _purge_pending()
    pending = PENDING.get(pending_id)
    if pending is None:
        raise LoginExpired("This sign-in has expired. Enter your password again.")
    pending.attempts += 1
    if pending.attempts >= MFA_ATTEMPTS:
        del PENDING[pending_id]
    try:
        pending.api.resume_login(pending.client_state, code.strip())
    except Exception:
        if pending_id not in PENDING:
            raise LoginExpired("Too many wrong codes. Enter your password again.") from None
        raise
    PENDING.pop(pending_id, None)
    pending.api.password = None
    _remember(pending.api, pending.email, pending.token, pending.remember)
    return pending


def restore(session_token: str | None) -> tuple[Garmin, str] | None:
    """Sign in with a remembered session. Returns None if the token is unknown or expired.

    Raises if the remembered Garmin tokens no longer work.
    """
    if not session_token:
        return None
    folder = _session_dir(session_token)
    try:
        account = json.loads((folder / "account.json").read_text())
        created = datetime.fromisoformat(account["created"])
    except (OSError, ValueError, KeyError):
        return None
    if datetime.now(timezone.utc) - created > timedelta(days=SESSION_DAYS):
        forget(None, session_token)
        return None
    api = Garmin(account["email"], None)
    api.login(str(folder / "tokens"))
    return api, account["email"]


def forget(api: Garmin | None, session_token: str | None) -> None:
    """Sign out: revoke the Garmin token and delete this browser's remembered session."""
    if not session_token:
        return
    folder = _session_dir(session_token)
    if api is not None:
        try:
            api.logout(str(folder / "tokens"))
        except Exception:
            pass  # Still delete the local copy below.
    shutil.rmtree(folder, ignore_errors=True)


def _same_email(a: str, b: str) -> bool:
    return a.strip().lower() == b.strip().lower()


def delete_user_data(email: str) -> list[str]:
    """Delete everything stored for `email`: their database (and SQLite side files), every remembered
    browser session, and the old single-user file if it is theirs (or it would be re-adopted at the next
    sign-in). Returns the deleted paths. Garmin's copy of the data is untouched."""
    deleted = []
    main = Path(db.user_db_path(email))
    for path in (main, main.with_name(main.name + "-wal"), main.with_name(main.name + "-shm")):
        if path.exists():
            path.unlink()
            deleted.append(str(path))
    if db.LEGACY_DB.exists() and db._legacy_belongs_to(email):
        db.LEGACY_DB.unlink()
        deleted.append(str(db.LEGACY_DB))
    if SESSIONS_DIR.exists():
        for folder in SESSIONS_DIR.iterdir():
            try:
                owner = json.loads((folder / "account.json").read_text())["email"]
            except (OSError, ValueError, KeyError, TypeError):
                continue
            if _same_email(owner, email):
                shutil.rmtree(folder, ignore_errors=True)
                deleted.append(str(folder))
    return deleted
