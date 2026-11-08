import json
from datetime import date

import pytest

from garmin_dashboards import coach
from garmin_dashboards.readiness import Readiness, Signal
from garmin_dashboards.workouts import suggest

WELLNESS = [{"day": "2026-11-08", "hrv_avg": 90, "rhr": 48, "sleep_score": 84, "sleep_s": 30645, "bb_high": 70,
             "raw_json": '{"userProfilePk": 123}', "hrv_status": "LOW"}]
INTENSITY = [{"day": "2026-11-07", "load": 120.4, "hard_min": 3.2, "hours": 3.5}]


def payload():
    r = Readiness("amber", 70, [Signal("HRV", "caution", "90 ms", "Below range")], "h", (96, 124), True)
    return coach.build_payload(r, suggest(r, 60, INTENSITY, date(2026, 11, 8)), WELLNESS, INTENSITY, -5.0, 252)


def test_payload_is_compact_and_identifier_free():
    p = payload()
    text = json.dumps(p)
    assert "raw_json" not in text and "userProfilePk" not in text and "@" not in text
    assert p["recovery_last_7_days"][0] == {"day": "2026-11-08", "hrv_avg": 90, "rhr": 48, "sleep_score": 84,
                                            "sleep_s": 8.5, "bb_high": 70}
    assert p["readiness"]["level"] == "amber" and p["suggested_workout"]["name"].startswith("Endurance")
    assert sum(s["minutes"] for s in p["suggested_workout"]["steps"]) == 60


def reply(content, finish="stop"):
    return {"choices": [{"finish_reason": finish, "message": {"content": content}}]}


def test_ask_coach_returns_parsed_advice(monkeypatch):
    seen = {}

    def post(body, key):
        seen.update(body=body, key=key)
        return reply(json.dumps({"summary": "Easy day.", "agree_with_suggestion": True,
                                 "adjusted_steps": None, "cautions": []}))

    advice = coach.ask_coach(payload(), model="some/model", post=post, api_key="k")
    assert advice.summary == "Easy day." and seen["key"] == "k"
    assert seen["body"]["model"] == "some/model"
    assert "@" not in json.dumps(seen["body"]["messages"][1])


def test_ask_coach_errors(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(coach.CoachError, match="API key"):
        coach.ask_coach(payload())
    with pytest.raises(coach.CoachError, match="declined"):
        coach.ask_coach(payload(), post=lambda b, k: reply("", "content_filter"), api_key="k")
    with pytest.raises(coach.CoachError, match="unexpected"):
        coach.ask_coach(payload(), post=lambda b, k: reply("not json"), api_key="k")


def test_env_key_is_only_used_in_local_mode(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "server-key")
    monkeypatch.delenv("GARMIN_DASHBOARDS_LOCAL", raising=False)
    assert not coach.is_available() and coach.is_available(" mine ")
    assert coach.resolve_key("mine") == "mine"
    monkeypatch.setenv("GARMIN_DASHBOARDS_LOCAL", "1")
    assert coach.resolve_key() == "server-key" and coach.resolve_key("mine") == "mine"
