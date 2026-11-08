"""Local vs hosted mode.

Hosted (default): every visitor brings their own OpenRouter key, entered in the app.
Local (GARMIN_DASHBOARDS_LOCAL=1): single-user convenience, the OpenRouter key can come from the
OPENROUTER_API_KEY environment variable.
"""

import os


def is_local() -> bool:
    return os.environ.get("GARMIN_DASHBOARDS_LOCAL", "").lower() in ("1", "true", "yes")
