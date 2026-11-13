from datetime import UTC, datetime, timedelta

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
