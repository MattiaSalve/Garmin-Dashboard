"""Garmin Connect authentication. Credentials come from environment variables.

"Remember me" stores Garmin's OAuth tokens (not the password) in a private folder
under the home directory, so the dashboard can sign back in without asking.
"""

import json
import os
import shutil
from pathlib import Path

from garminconnect import Garmin

EMAIL_VAR = "GARMIN_EMAIL"
PASSWORD_VAR = "GARMIN_PASSWORD"

REMEMBER_DIR = Path.home() / ".garmin_dashboards"
TOKEN_DIR = REMEMBER_DIR / "tokens"
ACCOUNT_FILE = REMEMBER_DIR / "account.json"


def credentials() -> tuple[str, str]:
    email = os.environ.get(EMAIL_VAR)
    password = os.environ.get(PASSWORD_VAR)
    if not email or not password:
        raise RuntimeError(f"Set {EMAIL_VAR} and {PASSWORD_VAR} in the environment first.")
    return email, password


def login(email: str | None = None, password: str | None = None, remember: bool = False) -> Garmin:
    if email is None or password is None:
        email, password = credentials()

    if remember:
        # Drop any tokens from a previous account before signing in fresh.
        _clear_remembered_files()
        REMEMBER_DIR.mkdir(mode=0o700, exist_ok=True)

    api = Garmin(email, password)
    api.login(str(TOKEN_DIR) if remember else None)

    if remember:
        ACCOUNT_FILE.write_text(json.dumps({"email": email}))
        ACCOUNT_FILE.chmod(0o600)
    return api


def restore() -> tuple[Garmin, str] | None:
    """Sign in with remembered tokens. Returns None if nothing is remembered.

    Raises if the remembered tokens no longer work.
    """
    if not ACCOUNT_FILE.exists() or not TOKEN_DIR.exists():
        return None
    email = json.loads(ACCOUNT_FILE.read_text())["email"]
    api = Garmin(email, None)
    api.login(str(TOKEN_DIR))
    return api, email


def forget(api: Garmin | None = None) -> None:
    """Sign out: revoke the local token and delete anything remembered."""
    if api is not None:
        try:
            api.logout(str(TOKEN_DIR))
        except Exception:
            pass  # Still delete the local copy below.
    _clear_remembered_files()


def _clear_remembered_files() -> None:
    if TOKEN_DIR.exists():
        shutil.rmtree(TOKEN_DIR)
    if ACCOUNT_FILE.exists():
        ACCOUNT_FILE.unlink()
