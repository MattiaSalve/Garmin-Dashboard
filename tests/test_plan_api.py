import json
from datetime import UTC, date, datetime, timedelta

from test_api import client, sign_in  # noqa: F401  (pytest fixture)

from garmin_dashboards import api, db, plan

TODAY = datetime.now(UTC).date()
MON = plan.monday(TODAY)
H = {"X-Local-Date": TODAY.isoformat()}
SETUP = {
    "profile": {"sports": {"cycling": 0.8, "gym": 0.2}, "hours_per_week": 7, "sessions_per_week": 5, "long_day": 5},
    "goals": [{"kind": "event", "sport": "cycling", "date": (TODAY + timedelta(weeks=10)).isoformat(), "target": 4,
               "name": "Gran fondo"}],
}


def setup_plan(client):  # noqa: F811
    sign_in(client)
    r = client.put("/api/plan/setup", json=SETUP, headers=H)
    assert r.status_code == 200, r.text
    return api.SESSIONS[next(iter(api.SESSIONS))].db_path


def all_sessions(view):
    return [s for w in view["weeks"] for d in w["days"] for s in d["sessions"]]


def future_session(view, pred=lambda s: True):
    return next(s for s in all_sessions(view) if s["day"] > TODAY.isoformat() and s["kind"] != "rest" and pred(s))


def test_setup_is_suggested_then_saved_and_builds_a_plan(client):  # noqa: F811
    sign_in(client)
    first = client.get("/api/plan", headers=H).json()
    assert first["setup"]["configured"] is False and first["weeks"] == []
    assert first["setup"]["profile"]["hours_per_week"] > 0
    setup_plan(client)
    view = client.get("/api/plan", headers=H).json()
    assert view["setup"]["configured"] and view["setup"]["goals"][0]["name"] == "Gran fondo"
    assert view["weeks"][0]["past"] and view["weeks"][1]["week_start"] == MON.isoformat()
    phases = [w["phase"] for w in view["weeks"] if w["phase"]]
    assert phases[-1] == "race" and "taper" in phases
    sessions = all_sessions(view)
    assert sessions and {s["sport"] for s in sessions} <= {"cycling", "gym", "rest"}
    assert all(s["day"] >= TODAY.isoformat() for s in sessions)
    assert not any(w["days"] for w in view["weeks"] if not w["detailed"])


def test_setup_is_validated(client):  # noqa: F811
    sign_in(client)
    bad = {"profile": {**SETUP["profile"], "sports": {"rowing": 1}, "hours_per_week": 0}, "goals": []}
    r = client.put("/api/plan/setup", json=bad, headers=H)
    assert r.status_code == 400 and "rowing" in r.json()["detail"] and "Hours per week" in r.json()["detail"]
    assert client.post("/api/plan/generate", headers=H).status_code == 400  # nothing set up yet


def test_edits_survive_regenerate_and_rest_days_stay_free(client):  # noqa: F811
    setup_plan(client)
    view = client.get("/api/plan", headers=H).json()
    s = future_session(view, lambda s: s["sport"] == "cycling")
    r = client.patch(f"/api/plan/day/{s['day']}/{s['slot']}", json={"kind": "tempo", "minutes": 70}, headers=H)
    assert r.status_code == 200 and r.json()["session"]["source"] == "user"
    gone = future_session(view, lambda x: x["day"] != s["day"] and x["slot"] == 0)
    assert client.delete(f"/api/plan/day/{gone['day']}/0", headers=H).status_code == 200
    assert client.post("/api/plan/generate", headers=H).status_code == 200
    after = all_sessions(client.get("/api/plan", headers=H).json())
    kept = next(x for x in after if x["day"] == s["day"] and x["slot"] == s["slot"])
    assert (kept["kind"], kept["minutes"], kept["source"]) == ("tempo", 70, "user")
    rest = [x for x in after if x["day"] == gone["day"] and x["slot"] == 0]
    assert rest[0]["kind"] == "rest"


def test_bad_edits_are_rejected(client):  # noqa: F811
    setup_plan(client)
    s = future_session(client.get("/api/plan", headers=H).json(), lambda s: s["sport"] == "cycling")
    url = f"/api/plan/day/{s['day']}/{s['slot']}"
    assert client.patch(url, json={"kind": "lower"}, headers=H).status_code == 400
    assert client.patch(url, json={"minutes": 5}, headers=H).status_code == 422
    assert client.patch("/api/plan/day/2001-01-01/0", json={"minutes": 50}, headers=H).status_code == 404


def test_move_add_and_lock(client):  # noqa: F811
    setup_plan(client)
    view = client.get("/api/plan", headers=H).json()
    s = future_session(view, lambda s: s["sport"] == "cycling" and s["slot"] == 0)
    target = (TODAY + timedelta(days=30)).isoformat()
    r = client.post(f"/api/plan/day/{s['day']}/0/move", json={"to": target}, headers=H)
    assert r.status_code == 200 and r.json()["session"]["day"] == target
    rows = {(x["day"], x["slot"]): x for x in db.get_plan_days(TODAY.isoformat(), "9999-12-31", api.SESSIONS[next(iter(api.SESSIONS))].db_path)}
    assert rows[(s["day"], 0)]["kind"] == "rest"
    added = client.post(f"/api/plan/day/{target}", json={"sport": "gym", "kind": "core", "minutes": 30}, headers=H).json()
    assert added["session"]["slot"] == 1
    assert client.post(f"/api/plan/day/{target}", json={"sport": "gym", "kind": "core", "minutes": 30}, headers=H).status_code == 409
    assert client.patch(f"/api/plan/day/{target}/1", json={"locked": True}, headers=H).json()["session"]["locked"]


def test_week_hours_override_resizes_the_week(client):  # noqa: F811
    setup_plan(client)
    nxt = MON + timedelta(weeks=1)
    r = client.put(f"/api/plan/week/{nxt}", json={"hours": 2}, headers=H)
    assert r.status_code == 200
    week = next(w for w in client.get("/api/plan", headers=H).json()["weeks"] if w["week_start"] == nxt.isoformat())
    assert week["override_hours"] == 2 and week["target_hours"] <= 2 and week["planned_hours"] <= 2.5
    assert client.put(f"/api/plan/week/{nxt + timedelta(days=1)}", json={"hours": 2}, headers=H).status_code == 400


def seed_ride(path, day, minutes, load, aid=1):
    db.save_activities([{
        "activityId": aid, "activityName": "Big day", "startTimeLocal": f"{day} 08:00:00",
        "activityType": {"typeKey": "road_biking"}, "duration": minutes * 60, "movingDuration": minutes * 60,
        "distance": minutes * 450.0, "activityTrainingLoad": load,
    }], "a@x.com", path)


def test_overshoot_gives_a_proposal_that_applies_once(client):  # noqa: F811
    path = setup_plan(client)
    yesterday = (TODAY - timedelta(days=1)).isoformat()
    # Pretend yesterday was planned as an easy hour and the athlete rode five hours.
    db.save_plan_days([{"day": yesterday, "slot": 0, "sport": "cycling", "kind": "endurance", "minutes": 60,
                        "planned_load": 60, "source": "user", "locked": False, "note": ""}], path)
    seed_ride(path, yesterday, 300, 380)
    view = client.get("/api/plan", headers=H).json()
    props = [p for p in view["proposals"] if p["kind"] == "overshoot"]
    assert len(props) == 1 and props[0]["changes"]
    p = props[0]
    assert "5 h" in p["reason"] and p["changes"][0]["after_label"]
    assert client.post(f"/api/plan/proposals/{p['id']}/apply", headers=H).status_code == 200
    c = p["changes"][0]
    row = next(x for x in all_sessions(client.get("/api/plan", headers=H).json()) if x["day"] == c["day"] and x["slot"] == c["slot"])
    assert (row["kind"], row["minutes"], row["source"]) == (c["after"]["kind"], c["after"]["minutes"], "adjusted")
    # The same situation doesn't come back, and applying twice fails.
    assert not [x for x in client.get("/api/plan", headers=H).json()["proposals"] if x["kind"] == "overshoot"]
    assert client.post(f"/api/plan/proposals/{p['id']}/apply", headers=H).status_code == 409


def test_stale_proposal_is_not_applied_and_dismiss_sticks(client):  # noqa: F811
    path = setup_plan(client)
    s = future_session(client.get("/api/plan", headers=H).json(), lambda s: s["sport"] == "cycling")
    change = {"day": s["day"], "slot": s["slot"], "before": {"sport": "cycling", "kind": "vo2max", "minutes": 999},
              "after": {"sport": "cycling", "kind": "endurance", "minutes": 60}}
    pid = db.add_proposal("test:1", "coach", "Because.", [change], "coach", path)
    assert client.post(f"/api/plan/proposals/{pid}/apply", headers=H).status_code == 409
    assert db.get_proposal(pid, path)["status"] == "stale"
    pid2 = db.add_proposal("test:2", "coach", "Because.", [{**change, "before": None, "slot": 1}], "coach", path)
    assert client.post(f"/api/plan/proposals/{pid2}/dismiss", headers=H).status_code == 200
    assert db.add_proposal("test:2", "coach", "Again.", [], "coach", path) is None


def test_home_follows_todays_plan(client):  # noqa: F811
    path = setup_plan(client)
    db.save_plan_days([{"day": TODAY.isoformat(), "slot": 0, "sport": "running", "kind": "tempo", "minutes": 45,
                        "planned_load": 80, "source": "user", "locked": False, "note": ""}], path)
    home = client.get("/api/home?sport=running&minutes=45", headers=H).json()
    pt = home["plan_today"]
    assert pt["planned"]["kind"] == "tempo" and pt["used"]
    # No recovery data yet: readiness is capped at amber, so the tempo work waits.
    assert pt["kind"] == "endurance" and pt["reason"]
    assert any("Today's plan" in line for line in home["workout"]["rationale"])
    other = client.get("/api/home?sport=cycling&minutes=60", headers=H).json()
    assert other["plan_today"]["used"] is False


def test_planned_workout_preview(client):  # noqa: F811
    setup_plan(client)
    view = client.get("/api/plan", headers=H).json()
    ride = future_session(view, lambda s: s["sport"] == "cycling")
    w = client.get(f"/api/plan/day/{ride['day']}/{ride['slot']}/workout", headers=H).json()["workout"]
    assert w["steps"] and abs(w["minutes"] - ride["minutes"]) <= 5


def test_start_over_clears_everything(client):  # noqa: F811
    setup_plan(client)
    client.put(f"/api/plan/week/{MON + timedelta(weeks=1)}", json={"hours": 3}, headers=H)
    assert client.delete("/api/plan", headers=H).status_code == 200
    view = client.get("/api/plan", headers=H).json()
    assert view["setup"]["configured"] is False and view["weeks"] == []


def fake_coach(monkeypatch, reply: dict):
    """Swap OpenRouter for a canned reply; returns what the coach was sent."""
    import json

    from garmin_dashboards import coach
    seen = {}
    real = coach.ask_coach

    def post(body, key):
        seen["body"] = body
        return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(reply)}}]}

    monkeypatch.setattr(coach, "ask_coach", lambda payload, **kw: real(payload, post=post, **kw))
    return seen


def test_plan_review_turns_coach_changes_into_a_proposal(client, monkeypatch):  # noqa: F811
    setup_plan(client)
    view = client.get("/api/plan", headers=H).json()
    s = future_session(view, lambda s: s["day"] <= (MON + timedelta(days=13)).isoformat() and s["sport"] == "cycling")
    seen = fake_coach(monkeypatch, {
        "summary": "Shorter ride: you have a busy day.", "agree_with_suggestion": True,
        "plan_changes": [{"day": s["day"], "slot": s["slot"], "sport": "cycling", "kind": "recovery", "minutes": 40,
                          "note": "Busy day."},
                         {"day": "2001-01-01", "sport": "cycling", "kind": "endurance", "minutes": 60}],
    })
    r = client.post("/api/plan/review", json={"api_key": "k"}, headers=H)
    assert r.status_code == 200, r.text
    out = r.json()
    sent = seen["body"]["messages"][1]["content"]
    assert "training_plan" in sent and "Gran fondo" not in sent and "a@x.com" not in sent
    assert "Review my training plan" in sent
    prop = out["plan_proposal"]
    assert prop["source"] == "coach" and prop["reason"].startswith("Shorter ride") and len(out["plan_skipped"]) == 1
    assert prop["changes"][0]["after_label"].startswith("Recovery")
    assert client.post(f"/api/plan/proposals/{prop['id']}/apply", headers=H).status_code == 200
    row = next(x for x in all_sessions(client.get("/api/plan", headers=H).json())
               if x["day"] == s["day"] and x["slot"] == s["slot"])
    assert (row["kind"], row["minutes"], row["source"], row["note"]) == ("recovery", 40, "coach", "Busy day.")


def test_home_chat_can_change_the_plan_but_not_without_one(client, monkeypatch):  # noqa: F811
    sign_in(client)
    tomorrow = (TODAY + timedelta(days=1)).isoformat()
    fake_coach(monkeypatch, {"summary": "Rest tomorrow.", "agree_with_suggestion": True,
                             "plan_changes": [{"day": tomorrow, "sport": "rest", "kind": "rest"}]})
    out = client.post("/api/coach", json={"api_key": "k", "sport": "cycling"}, headers=H).json()
    assert out["plan_proposal"] is None and "no training plan" in out["plan_skipped"][0]
    assert client.post("/api/plan/review", json={"api_key": "k"}, headers=H).status_code == 400


NEXT = MON + timedelta(weeks=1)
REAL_ASK_PLAN = __import__("garmin_dashboards.coach", fromlist=["ask_plan"]).ask_plan


def fake_planner(monkeypatch, reply):
    """Swap OpenRouter for a canned plan; returns the requests the AI was sent."""
    import json

    from garmin_dashboards import coach
    seen = []
    real = REAL_ASK_PLAN

    def post(body, key):
        seen.append(body)
        if isinstance(reply, Exception):
            raise reply
        return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(reply)}}]}

    monkeypatch.setattr(coach, "ask_plan", lambda payload, **kw: real(payload, post=post, **kw))
    return seen


AI_REPLY = {
    "summary": "Two quality rides and a long one.",
    "weeks": [{"week_start": NEXT.isoformat(), "phase": "build", "hours": 6, "note": "Build the base."}],
    "sessions": [
        {"day": (NEXT + timedelta(days=1)).isoformat(), "sport": "cycling", "kind": "sweet_spot", "minutes": 75, "note": "3x12"},
        {"day": (NEXT + timedelta(days=3)).isoformat(), "sport": "gym", "kind": "full", "minutes": 45},
        {"day": (NEXT + timedelta(days=5)).isoformat(), "sport": "cycling", "kind": "long", "minutes": 600},
    ],
}


def week_of(view, ws):
    return next(w for w in view["weeks"] if w["week_start"] == ws.isoformat())


def test_ai_builds_the_weeks_it_plans_and_rules_fill_the_rest(client, monkeypatch):  # noqa: F811
    path = setup_plan(client)
    seen = fake_planner(monkeypatch, AI_REPLY)
    r = client.post("/api/plan/build", json={"mode": "all", "api_key": "k", "model": "some/model"}, headers=H)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["builder"] == "ai" and out["summary"].startswith("Two quality")
    assert any("long" in a.lower() or "capped" in a.lower() or "min" in a for a in out["adjusted"])  # 600 min is too long
    assert NEXT.isoformat() not in out["rules_weeks"] and MON.isoformat() in out["rules_weeks"]

    sent = json.loads(seen[0]["messages"][1]["content"])
    text = json.dumps(sent)
    assert "Gran fondo" not in text and "@" not in text and "raw_json" not in text
    assert seen[0]["model"] == "some/model" and NEXT.isoformat() in sent["weeks_to_plan"]

    view = client.get("/api/plan", headers=H).json()
    nxt = week_of(view, NEXT)
    assert nxt["builder"] == "ai" and nxt["note"] == "Build the base." and nxt["target_hours"] == 6
    rides = [s for d in nxt["days"] for s in d["sessions"]]
    assert [(s["kind"], s["source"]) for s in rides] == [("sweet_spot", "ai"), ("full", "ai"), ("long", "ai")]
    assert rides[2]["minutes"] < 600 and rides[0]["note"] == "3x12"
    assert week_of(view, MON)["builder"] == "rules"
    assert view["ai"]["model"] == "some/model" and view["ai"]["summary"].startswith("Two quality")
    assert NEXT.isoformat() not in view["ai"]["topup_weeks"]
    assert {r["source"] for r in db.get_plan_days(NEXT.isoformat(), (NEXT + timedelta(days=6)).isoformat(), path)} == {"ai"}


def test_rules_build_the_plan_without_a_key_or_when_the_ai_fails(client, monkeypatch):  # noqa: F811
    from garmin_dashboards import coach
    setup_plan(client)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    r = client.post("/api/plan/build", json={"mode": "all"}, headers=H).json()
    assert r == {"builder": "rules", "fallback": None}

    fake_planner(monkeypatch, coach.CoachError("OpenRouter is down."))
    r = client.post("/api/plan/build", json={"mode": "all", "api_key": "k"}, headers=H).json()
    assert r["builder"] == "rules" and r["fallback"] == "OpenRouter is down."

    fake_planner(monkeypatch, {"summary": "Ride lots.", "sessions": [{"day": "2001-01-01", "sport": "cycling", "kind": "endurance", "minutes": 60}]})
    r = client.post("/api/plan/build", json={"mode": "all", "api_key": "k"}, headers=H).json()
    assert r["builder"] == "rules" and "no usable sessions" in r["fallback"]
    view = client.get("/api/plan", headers=H).json()
    assert all(s["source"] != "ai" for s in all_sessions(view)) and future_session(view)
    assert all(w["builder"] == "rules" for w in view["weeks"] if w["detailed"])


def test_ai_top_up_and_single_week_keep_the_other_weeks(client, monkeypatch):  # noqa: F811
    setup_plan(client)
    fake_planner(monkeypatch, AI_REPLY)
    view = client.get("/api/plan", headers=H).json()
    assert NEXT.isoformat() in view["ai"]["topup_weeks"] and MON.isoformat() not in view["ai"]["topup_weeks"]
    before = [s for s in all_sessions(view) if s["day"] < NEXT.isoformat()]

    r = client.post("/api/plan/build", json={"mode": "topup", "api_key": "k"}, headers=H).json()
    assert r["builder"] == "ai"
    view = client.get("/api/plan", headers=H).json()
    assert [s for s in all_sessions(view) if s["day"] < NEXT.isoformat()] == before  # this week is left alone
    assert week_of(view, NEXT)["builder"] == "ai" and NEXT.isoformat() not in view["ai"]["topup_weeks"]

    later = NEXT + timedelta(weeks=1)
    reply = {**AI_REPLY, "weeks": [], "sessions": [{**s, "day": (date.fromisoformat(s["day"]) + timedelta(weeks=1)).isoformat()}
                                                   for s in AI_REPLY["sessions"]]}
    fake_planner(monkeypatch, reply)
    r = client.post("/api/plan/build", json={"mode": "week", "week_start": later.isoformat(), "api_key": "k"}, headers=H).json()
    assert r["builder"] == "ai"
    view = client.get("/api/plan", headers=H).json()
    assert week_of(view, NEXT)["builder"] == "ai" and week_of(view, later)["builder"] == "ai"
    assert [s["kind"] for d in week_of(view, NEXT)["days"] for s in d["sessions"]] == ["sweet_spot", "full", "long"]

    assert client.post("/api/plan/build", json={"mode": "week", "week_start": (MON + timedelta(days=1)).isoformat()},
                       headers=H).status_code == 400


def test_rules_regenerate_keeps_week_targets_honest(client, monkeypatch):  # noqa: F811
    setup_plan(client)
    fake_planner(monkeypatch, AI_REPLY)
    client.post("/api/plan/build", json={"mode": "all", "api_key": "k"}, headers=H)
    assert client.post("/api/plan/generate", headers=H).status_code == 200
    view = client.get("/api/plan", headers=H).json()
    assert all(w["builder"] == "rules" for w in view["weeks"] if w["detailed"]) and not view["ai"]["summary"]


HOME_SETUP = {"athlete": {"equipment": ["pullup_bar", "rings", "box"], "effort": "failure"},
              "about": "No gym: a pull-up bar, rings and a box at home."}
LEGS = [{"name": "Pistol squat progression", "group": "legs", "sets": 4, "reps": "5–8", "rpe": 10, "rest_s": 150, "note": "To failure."},
        {"name": "Ring leg curl", "group": "legs", "sets": 3, "reps": "8–12", "rpe": 10, "rest_s": 120, "note": ""}]


def test_athlete_setup_is_saved_validated_and_sent_to_the_ai(client, monkeypatch):  # noqa: F811
    sign_in(client)
    bad = {**SETUP, "athlete": {"equipment": ["jetpack"], "effort": "gentle"}}
    r = client.put("/api/plan/setup", json=bad, headers=H)
    assert r.status_code == 400 and "jetpack" in r.json()["detail"] and "gentle" in r.json()["detail"]
    assert client.put("/api/plan/setup", json={**SETUP, **HOME_SETUP}, headers=H).status_code == 200
    setup = client.get("/api/plan", headers=H).json()["setup"]
    assert setup["athlete"] == {"equipment": ["pullup_bar", "rings", "box"], "effort": "failure"}
    assert setup["about"] == HOME_SETUP["about"] and "rings" in setup["options"]["equipment"]

    gym_day = (NEXT + timedelta(days=3)).isoformat()
    reply = {**AI_REPLY, "sessions": [{"day": gym_day, "sport": "gym", "kind": "lower", "minutes": 50, "exercises": LEGS}]}
    seen = fake_planner(monkeypatch, reply)
    assert client.post("/api/plan/build", json={"mode": "week", "week_start": NEXT.isoformat(), "api_key": "k"},
                       headers=H).status_code == 200
    sent = json.loads(seen[0]["messages"][1]["content"])
    assert sent["gym_setup"]["equipment"] == ["Pull-up bar", "Gymnastic rings", "Box or bench"]
    assert sent["athlete_goals"] == HOME_SETUP["about"] and "To failure" in sent["gym_setup"]["effort"]

    w = client.get(f"/api/plan/day/{gym_day}/0/workout", headers=H).json()["workout"]
    assert [e["name"] for e in w["exercises"]] == ["Pistol squat progression", "Ring leg curl"]
    client.patch(f"/api/plan/day/{gym_day}/0", json={"kind": "upper"}, headers=H)  # a new kind drops the AI's exercises
    w = client.get(f"/api/plan/day/{gym_day}/0/workout", headers=H).json()["workout"]
    names = [e["name"] for e in w["exercises"]]
    assert "Ring row" in names and not any("Dumbbell" in n or "Barbell" in n for n in names)


def test_home_gym_session_fits_the_equipment(client):  # noqa: F811
    sign_in(client)
    client.put("/api/plan/setup", json={**SETUP, **HOME_SETUP}, headers=H)
    home = client.get("/api/home?sport=gym&minutes=60&focus=upper", headers=H).json()
    names = [e["name"] for e in home["workout"]["exercises"]]
    assert "Ring row" in names or "Pull-up" in names
    assert not any(w in n for n in names for w in ("Dumbbell", "Barbell", "Band"))
