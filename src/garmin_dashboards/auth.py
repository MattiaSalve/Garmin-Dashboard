"""Garmin Connect authentication.

"Keep me signed in" works with a random session token kept in a browser cookie. On the server,
the token's hash names a private folder holding that user's Garmin OAuth tokens (never the
password), so a returning browser is signed back in without asking. Sessions expire after
SESSION_DAYS and are deleted on sign-out.
"""

import hashlib
import json
import os
import secrets
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from garminconnect import Garmin

from garmin_dashboards.db import DATA_DIR

EMAIL_VAR = "GARMIN_EMAIL"
PASSWORD_VAR = "GARMIN_PASSWORD"

COOKIE_NAME = "gd_session"
SESSION_DAYS = 30
SESSIONS_DIR = DATA_DIR / "sessions"


def credentials() -> tuple[str, str]:
    email = os.environ.get(EMAIL_VAR)
    password = os.environ.get(PASSWORD_VAR)
    if not email or not password:
        raise RuntimeError(f"Set {EMAIL_VAR} and {PASSWORD_VAR} in the environment first.")
    return email, password


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def _session_dir(token: str) -> Path:
    # Hashing means a hostile cookie value can never point outside the sessions folder.
    return SESSIONS_DIR / hashlib.sha256(token.encode()).hexdigest()[:40]


def login(
    email: str | None = None, password: str | None = None, session_token: str | None = None
) -> Garmin:
    """Sign in to Garmin. With a session token, also remember the sign-in for that browser."""
    if email is None or password is None:
        email, password = credentials()

    folder = None
    if session_token:
        folder = _session_dir(session_token)
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)

    api = Garmin(email, password)
    api.login(str(folder / "tokens") if folder else None)

    if folder:
        account = folder / "account.json"
        account.write_text(json.dumps({"email": email, "created": datetime.now(timezone.utc).isoformat()}))
        account.chmod(0o600)
    return api


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
