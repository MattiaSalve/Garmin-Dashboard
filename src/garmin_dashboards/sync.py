"""Download activities from Garmin Connect into the local database."""

from garmin_dashboards.db import known_activity_ids, save_activities

PAGE_SIZE = 100


def fetch_activities(api, known_ids: frozenset[int] | set[int] = frozenset()) -> list[dict]:
    """Page through the activity list (newest first).

    Stops after the first page that contains an already-known activity, so a
    repeat sync only downloads what is new. Pass an empty set for a full sync.
    """
    activities: list[dict] = []
    start = 0
    while True:
        batch = api.get_activities(start, PAGE_SIZE)
        if not batch:
            break
        activities.extend(batch)
        if len(batch) < PAGE_SIZE or any(a["activityId"] in known_ids for a in batch):
            break
        start += PAGE_SIZE
    return activities


def sync(api, user_email: str, full: bool = False, db_path: str | None = None) -> tuple[int, int]:
    """Fetch and store activities. Returns (new_count, fetched_count)."""
    known = known_activity_ids(user_email, db_path)
    activities = fetch_activities(api, set() if full else known)
    save_activities(activities, user_email, db_path)
    new = sum(1 for a in activities if a["activityId"] not in known)
    return new, len(activities)
