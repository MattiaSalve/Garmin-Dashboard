"""Read-only data tools the AI coach can call to look things up instead of guessing.

Every tool returns small, identifier-free JSON (no activity names, locations, email or raw_json).
`Toolbox` binds them to one user's database and "today"; `coach.ask_coach` runs the tool loop.
"""

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable

from garmin_dashboards import fitness, gym, metrics

MAX_DAYS = 120
MAX_ROWS = 200
MAX_ACTIVITY_DAYS = 3650
MAX_ACTIVITIES = 25

WELLNESS_KEYS = ("day", "hrv_avg", "hrv_status", "rhr", "sleep_score", "sleep_s", "bb_high", "bb_low")


def _days(args: dict, default: int) -> int:
    try:
        n = int(args.get("days", default))
    except (TypeError, ValueError):
        n = default
    return min(MAX_DAYS, max(1, n))


def _spec(name: str, description: str, properties: dict | None = None) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties or {}, "additionalProperties": False}}}


_DAYS = {"days": {"type": "integer", "description": f"How many days back from today (1-{MAX_DAYS})."}}

SPECS = [
    _spec("get_recovery", "Per-day HRV, HRV status, resting HR, sleep score and hours, Body Battery. Oldest first.", _DAYS),
    _spec("get_training", "Every activity in the period: day, sport, minutes, distance_km, Garmin training load and "
          "minutes in heart-rate zones 4-5. No names.", _DAYS),
    _spec("get_strength_history", "Gym sessions in the period with the exercise categories, sets and reps Garmin "
          "recorded, plus days since each muscle group (legs, push, pull, core) was last trained.", _DAYS),
    _spec("get_fitness", "Daily fitness (CTL), fatigue (ATL) and form (TSB) from training load, last N days.", _DAYS),
    _spec("get_power_curve", "Best average cycling power (watts) for key durations in the period, with the "
          "previous best before it, and the monthly FTP estimate trend.", _DAYS),
    _spec("find_activities", "Look up individual activities, ranked by one measure: e.g. the highest average power "
          "ride, the longest run, the most climbing, the hardest session by load. Returns day, sport, minutes, "
          "distance_km, elevation_m, avg/max HR, avg/normalized/max power, load, training effect. No names.", {
              "sport": {"type": "string", "description": "Optional: Cycling, Running, Swimming, Strength, or a Garmin sport key."},
              "sort_by": {"type": "string", "enum": list(metrics.ACTIVITY_SORTS), "description": "Measure to rank by (default date)."},
              "order": {"type": "string", "enum": ["highest_first", "lowest_first"], "description": "Default highest_first (newest first for date)."},
              "days": {"type": "integer", "description": f"How many days back (1-{MAX_ACTIVITY_DAYS}, default 365)."},
              "limit": {"type": "integer", "description": f"How many activities (1-{MAX_ACTIVITIES}, default 5)."}}),
    _spec("get_plan", "The athlete's training plan: planned sessions this week and next, how well recent weeks were "
          "followed, and open proposed changes. Null when there is no plan."),
]


@dataclass
class Toolbox:
    db: str
    today: date
    plan_block: Callable[[], dict | None] = lambda: None
    used: list[str] = field(default_factory=list)

    @property
    def specs(self) -> list[dict]:
        return SPECS

    def call(self, name: str, arguments: str | dict | None) -> str:
        """Run a tool and return its JSON result. Errors come back as {"error": ...} so the model can recover."""
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments) if arguments.strip() else {}
            except ValueError:
                return json.dumps({"error": "arguments were not valid JSON"})
        args = arguments if isinstance(arguments, dict) else {}
        handler = getattr(self, f"_{name}", None) if name in {s["function"]["name"] for s in SPECS} else None
        if handler is None:
            return json.dumps({"error": f"unknown tool {name!r}", "tools": [s["function"]["name"] for s in SPECS]})
        if name not in self.used:
            self.used.append(name)
        try:
            return json.dumps(handler(args), default=str)
        except Exception as exc:  # a broken tool must not end the conversation
            return json.dumps({"error": f"{name} failed: {type(exc).__name__}"})

    def _start(self, days: int) -> date:
        return self.today - timedelta(days=days - 1)

    def _get_recovery(self, args: dict) -> list[dict]:
        rows = metrics.daily_wellness(self._start(_days(args, 14)), self.today, self.db)
        return [{k: (round(r[k] / 3600, 1) if k == "sleep_s" and r.get(k) else r.get(k)) for k in WELLNESS_KEYS}
                for r in rows][-MAX_ROWS:]

    def _get_training(self, args: dict) -> list[dict]:
        rows = metrics.activity_loads(self._start(_days(args, 14)), self.today, self.db)
        return [{"day": r["day"], "sport": r["sport_type"], "minutes": round(r["minutes"]),
                 "distance_km": round((r["distance_m"] or 0) / 1000, 1),
                 "load": round(r["load"] or 0), "hard_min": round(r["hard_min"] or 0)} for r in rows][-MAX_ROWS:]

    def _get_strength_history(self, args: dict) -> dict:
        sessions = metrics.strength_sessions(self._start(_days(args, 60)), self.today, self.db)
        # Trained-since is always measured over a window long enough to be meaningful.
        recent = metrics.strength_sessions(self._start(MAX_DAYS), self.today, self.db)
        return {"sessions": sessions[-MAX_ROWS:], "days_since_trained": gym.last_trained(recent, self.today)["days"]}

    def _get_fitness(self, args: dict) -> list[dict]:
        days = _days(args, 42)
        loads = metrics.daily_training_load(self.db)
        series = fitness.fitness_series({date.fromisoformat(r["day"]): r["load"] or 0 for r in loads}, self.today)
        keep = [r for r in series if r["day"] >= self._start(days)]
        return [{k: (v.isoformat() if isinstance(v, date) else round(v, 1) if isinstance(v, float) else v)
                 for k, v in r.items()} for r in keep]

    def _get_power_curve(self, args: dict) -> dict:
        days = _days(args, 90)
        return {"best_power_w": metrics.power_curve(self._start(days), self.today, "Cycling", self.db),
                "monthly_ftp_estimate": metrics.estimated_ftp_trend(self.db)[-12:]}

    def _find_activities(self, args: dict) -> list[dict]:
        sort_by = args.get("sort_by") or "date"
        if sort_by not in metrics.ACTIVITY_SORTS:
            return {"error": f"sort_by must be one of {', '.join(metrics.ACTIVITY_SORTS)}"}
        try:
            days = min(MAX_ACTIVITY_DAYS, max(1, int(args.get("days", 365))))
            limit = min(MAX_ACTIVITIES, max(1, int(args.get("limit", 5))))
        except (TypeError, ValueError):
            return {"error": "days and limit must be whole numbers"}
        sport = args.get("sport")
        sport = str(sport).strip() if sport else None
        if sport and sport not in metrics.SPORT_GROUPS and sport.lower() in {k.lower() for k in metrics.SPORT_GROUPS}:
            sport = next(k for k in metrics.SPORT_GROUPS if k.lower() == sport.lower())
        rows = metrics.top_activities(self.today - timedelta(days=days - 1), self.today, sport, sort_by,
                                      args.get("order") != "lowest_first", limit, self.db)
        return [{"day": r["day"], "sport": r["sport_type"], "minutes": round(r["minutes"]),
                 "distance_km": round((r["distance_m"] or 0) / 1000, 1),
                 "elevation_m": None if r["elevation_gain_m"] is None else round(r["elevation_gain_m"]),
                 "avg_hr": r["avg_hr"] and round(r["avg_hr"]), "max_hr": r["max_hr"] and round(r["max_hr"]),
                 "avg_power_w": r["avg_power_w"] and round(r["avg_power_w"]),
                 "norm_power_w": r["norm_power_w"] and round(r["norm_power_w"]),
                 "max_power_w": r["max_power_w"] and round(r["max_power_w"]),
                 "load": r["load"] and round(r["load"]), "aerobic_te": r["aerobic_te"], "anaerobic_te": r["anaerobic_te"]}
                for r in rows]

    def _get_plan(self, args: dict) -> dict | None:
        return self.plan_block()
