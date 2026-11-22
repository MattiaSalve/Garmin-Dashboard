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


def test_follow_up_keeps_context(monkeypatch):
    seen = {}

    def post(body, key):
        seen["messages"] = body["messages"]
        return reply(json.dumps({"summary": "Shorter.", "agree_with_suggestion": False,
                                 "adjusted_steps": None, "cautions": []}))

    history = [coach.ChatMessage(role="assistant", content='{"summary": "Easy day."}'),
               coach.ChatMessage(role="user", content="Make it 45 minutes")]
    coach.ask_coach(payload(), post=post, api_key="k", history=history)
    roles = [m["role"] for m in seen["messages"]]
    assert roles == ["system", "user", "assistant", "user"]
    assert seen["messages"][-1]["content"] == "Make it 45 minutes"


def test_custom_first_message_joins_the_opening_turn():
    seen = {}

    def post(body, key):
        seen["messages"] = body["messages"]
        return reply(json.dumps({"summary": "Ok.", "agree_with_suggestion": True, "adjusted_steps": None, "cautions": []}))

    history = [coach.ChatMessage(role="user", content="I only have 40 minutes")]
    coach.ask_coach(payload(), post=post, api_key="k", history=history)
    assert [m["role"] for m in seen["messages"]] == ["system", "user"]  # no two user turns in a row
    assert seen["messages"][1]["content"].endswith("The athlete's message: I only have 40 minutes")


def test_goals_and_targets_reach_the_payload():
    r = Readiness("amber", 70, [], "h", (96, 124), True)
    p = coach.build_payload(r, suggest(r, 60, INTENSITY, date(2026, 11, 8)), WELLNESS, INTENSITY, -5.0, 252,
                            goals="Ride 200 km in June", targets={"weekly_hours": 6, "yearly_km": None})
    assert p["athlete_goals"] == "Ride 200 km in June" and p["targets"] == {"weekly_hours": 6}
    assert payload()["athlete_goals"] is None


def test_gym_reply_with_loose_formatting_is_accepted():
    messy = '''Here you go:
```json
{"summary": "Legs today.", "agree_with_suggestion": false,
 "adjusted_exercises": [
   {"name": "Squat", "group": "Quads and glutes", "sets": "3", "reps": 8, "rpe": "7-8", "rest_s": "90 s", "note": null},
   {"name": "Plank", "group": "abs", "sets": 3.0, "reps": "45 s", "rpe": 12}
 ]}
```'''
    advice = coach.ask_coach(payload(), post=lambda b, k: reply(messy), api_key="k")
    first, second = advice.adjusted_exercises
    assert (first.group, first.sets, first.reps, first.rpe, first.rest_s, first.note) == ("legs", 3, "8", 7, 90, "")
    assert second.group == "core" and second.rpe == 10
    assert advice.adjusted_steps is None and advice.cautions == []


def test_workout_sport_is_normalised_and_optional():
    def ask(extra):
        body = {"summary": "s", "agree_with_suggestion": False, **extra}
        return coach.ask_coach(payload(), post=lambda b, k: reply(json.dumps(body)), api_key="k")

    assert ask({}).workout_sport is None
    assert ask({"workout_sport": "Bodyweight session"}).workout_sport == "gym"
    assert ask({"workout_sport": "running"}).workout_sport == "running"
    assert ask({"workout_sport": "yoga"}).workout_sport is None


def test_loose_cycling_steps_and_clear_errors():
    steps = '{"summary": "s", "agree_with_suggestion": true, "adjusted_steps": [{"minutes": "10", "pct_ftp_low": 55.0, "pct_ftp_high": "72.5%", "label": null}], "cautions": null}'
    advice = coach.ask_coach(payload(), post=lambda b, k: reply(steps), api_key="k")  # cautions: null is fine
    assert advice.cautions == []
    step = advice.adjusted_steps[0]
    assert (step.minutes, step.pct_ftp_low, step.pct_ftp_high, step.label) == (10.0, 55, 72, "Step")
    with pytest.raises(coach.CoachError, match="summary"):  # names the field that failed
        coach.ask_coach(payload(), post=lambda b, k: reply('{"agree_with_suggestion": true}'), api_key="k")
    with pytest.raises(coach.CoachError, match="cut off"):
        coach.ask_coach(payload(), post=lambda b, k: reply('{"summary": "s", "agr', "length"), api_key="k")
    with pytest.raises(coach.CoachError, match="empty reply"):
        coach.ask_coach(payload(), post=lambda b, k: reply("   "), api_key="k")


GOOD = json.dumps({"summary": "Easy day.", "agree_with_suggestion": True})


def test_empty_reply_is_retried_once_with_less_reasoning_after_a_length_stop():
    bodies = []

    def post(body, key):
        bodies.append(body)
        return reply(None, "length") if len(bodies) == 1 else reply(GOOD)

    assert coach.ask_coach(payload(), post=post, api_key="k").summary == "Easy day."
    assert "reasoning" not in bodies[0] and bodies[1]["reasoning"] == {"effort": "low"}

    bodies.clear()
    with pytest.raises(coach.CoachError, match="thinking"):
        coach.ask_coach(payload(), post=lambda b, k: bodies.append(b) or reply("", "length"), api_key="k")
    assert len(bodies) == 2


def test_errors_inside_a_200_response_are_reported():
    with pytest.raises(coach.CoachError, match="provider failed .Upstream timeout"):
        coach.ask_coach(payload(), post=lambda b, k: {"error": {"code": 502, "message": "Upstream timeout"}}, api_key="k")
    mid = {"choices": [{"finish_reason": "error", "error": {"message": "overloaded"}, "message": {"content": ""}}]}
    with pytest.raises(coach.CoachError, match="overloaded"):
        coach.ask_coach(payload(), post=lambda b, k: mid, api_key="k")


def test_plan_block_is_sent_and_plan_changes_are_parsed_forgivingly():
    r = Readiness("green", 90, [], "h", (96, 124), True)
    p = coach.build_payload(r, suggest(r, 60, INTENSITY, date(2026, 11, 8)), WELLNESS, INTENSITY, 0.0, 252,
                            plan={"today": "2026-11-08", "weeks": []})
    assert p["training_plan"]["today"] == "2026-11-08"
    assert "training_plan" not in payload()
    advice = coach.CoachAdvice.model_validate({
        "summary": "Lighter Thursday.", "agree_with_suggestion": True,
        "plan_changes": [
            {"day": "2026-11-12", "slot": "1", "sport": "Bike", "kind": "Sweet spot", "minutes": "60 min", "note": None},
            {"day": "2026-11-13", "sport": None, "kind": "Rest day"},
            {"day": "2026-11-14", "sport": "gym", "kind": "Full body", "minutes": 45},
        ],
    })
    a, b, c = advice.plan_changes
    assert (a.slot, a.sport, a.kind, a.minutes, a.note) == (1, "cycling", "sweet_spot", 60, "")
    assert (b.slot, b.sport, b.kind) == (0, "rest", "rest")
    assert (c.sport, c.kind) == ("gym", "full")
    assert coach.CoachAdvice.model_validate({"summary": "x", "agree_with_suggestion": True}).plan_changes is None


def test_ask_plan_sends_the_brief_and_reads_a_loose_plan():
    seen = {}
    messy = '```json\n{"summary": "Build up.", "weeks": [{"week_start": "2026-11-16", "phase": "Build", "hours": "6.5 h", "note": null}],' \
            ' "sessions": [{"day": "2026-11-17", "slot": "0", "sport": "Bike", "kind": "Sweet spot", "minutes": "75 min"}]}\n```'

    def post(body, key):
        seen["body"] = body
        return reply(messy)

    plan = coach.ask_plan({"today": "2026-11-13"}, post=post, api_key="k")
    assert json.loads(seen["body"]["messages"][1]["content"]) == {"today": "2026-11-13"}
    assert seen["body"]["max_tokens"] == coach.PLAN_MAX_TOKENS
    (w,), (s,) = plan.weeks, plan.sessions
    assert (w.phase, w.hours, w.note) == ("build", 6.5, "") and (s.sport, s.kind, s.minutes) == ("cycling", "sweet_spot", 75)
    assert coach.AIPlan.model_validate({"summary": "x", "weeks": None, "sessions": None}).sessions == []


def test_ask_plan_reads_gym_exercises_loosely():
    body = {"summary": "s", "weeks": [], "sessions": [
        {"day": "2026-11-18", "sport": "gym", "kind": "Lower body", "minutes": 50,
         "exercises": [{"name": "Pistol squat", "group": "quads", "sets": "4", "reps": 6, "rpe": "10 (failure)", "rest_s": "150 s"},
                       None, {"name": ""}]},
        {"day": "2026-11-19", "sport": "cycling", "kind": "endurance", "minutes": 60, "exercises": "none"}]}
    plan = coach.ask_plan({}, post=lambda b, k: reply(json.dumps(body)), api_key="k")
    g, ride = plan.sessions
    (e,) = g.exercises
    assert (g.kind, e.group, e.sets, e.reps, e.rpe, e.rest_s) == ("lower", "legs", 4, "6", 10, 150)
    assert ride.exercises is None
    assert "gym_setup" in coach.PLAN_SYSTEM


# ---------------------------------------------------------------- tool loop

from garmin_dashboards import coach_tools, db  # noqa: E402


def tool_reply(*calls):
    return {"choices": [{"finish_reason": "tool_calls", "message": {"content": None, "tool_calls": [
        {"id": f"c{i}", "type": "function", "function": {"name": n, "arguments": a}} for i, (n, a) in enumerate(calls)]}}]}


class FakeBox:
    specs = [{"type": "function", "function": {"name": "get_recovery"}}]

    def __init__(self):
        self.used, self.calls = [], []

    def call(self, name, arguments):
        self.calls.append((name, arguments))
        self.used.append(name)
        return '[{"day": "2026-11-20", "hrv_avg": 82}]'


def test_tool_loop_runs_tools_then_answers():
    box, bodies = FakeBox(), []
    answers = iter([tool_reply(("get_recovery", '{"days": 7}')),
                    reply(json.dumps({"summary": "HRV dipped on the 20th."}))])

    def post(body, key):
        bodies.append(body)
        return next(answers)

    advice = coach.ask_coach(payload(), post=post, api_key="k", toolbox=box)
    assert advice.summary.startswith("HRV dipped") and advice.tools_used == ["get_recovery"]
    assert box.calls == [("get_recovery", '{"days": 7}')]
    assert "tools" in bodies[0] and "response_format" not in bodies[0]
    last = bodies[1]["messages"]
    assert last[-1]["role"] == "tool" and last[-2]["tool_calls"][0]["id"] == "c0"


def test_tool_loop_is_capped_and_forces_an_answer():
    box, bodies = FakeBox(), []

    def post(body, key):
        bodies.append(body)
        if "tools" in body:
            return tool_reply(("get_recovery", "{}"))
        return reply(json.dumps({"summary": "Done."}))

    advice = coach.ask_coach(payload(), post=post, api_key="k", toolbox=box)
    assert advice.summary == "Done." and len(box.calls) == coach.MAX_TOOL_ROUNDS
    assert "tools" not in bodies[-1] and "Answer now" in bodies[-1]["messages"][-1]["content"]


def test_toolbox_tools_are_identifier_free_and_robust(tmp_path):
    path = str(tmp_path / "t.db")
    db.init_db(path)
    box = coach_tools.Toolbox(path, date(2026, 11, 22), plan_block=lambda: {"weeks": []})
    for spec in coach_tools.SPECS:
        out = box.call(spec["function"]["name"], "{}")
        assert "error" not in out or '"error"' not in out[:10], out
        assert "raw_json" not in out and "@" not in out
    assert json.loads(box.call("get_plan", None)) == {"weeks": []}
    assert "unknown tool" in box.call("rm_rf", "{}")
    assert "not valid JSON" in box.call("get_recovery", "{oops")
    assert set(box.used) == {s["function"]["name"] for s in coach_tools.SPECS}


def test_find_activities_ranks_by_the_requested_measure(tmp_path):
    import sqlite3
    path = str(tmp_path / "a.db")
    db.init_db(path)
    with sqlite3.connect(path) as conn:
        for i, (day, sport, power) in enumerate([("2026-11-01", "cycling", 180), ("2026-11-10", "cycling", 240),
                                                 ("2026-11-15", "running", None), ("2026-11-18", "cycling", 210)]):
            conn.execute("INSERT INTO activities (activity_id, activity_name, sport_type, start_time_local, "
                         "moving_duration_s, avg_power_w) VALUES (?, 'Secret name', ?, ?, 3600, ?)",
                         (i, sport, f"{day} 08:00:00", power))
    box = coach_tools.Toolbox(path, date(2026, 11, 22))
    out = json.loads(box.call("find_activities", '{"sport": "cycling", "sort_by": "avg_power", "limit": 2}'))
    assert [r["avg_power_w"] for r in out] == [240, 210] and out[0]["day"] == "2026-11-10"
    assert "Secret" not in json.dumps(out)
    low = json.loads(box.call("find_activities", '{"sort_by": "avg_power", "order": "lowest_first", "limit": 1}'))
    assert low[0]["avg_power_w"] == 180  # rows without power are left out
    assert "error" in json.loads(box.call("find_activities", '{"sort_by": "secret_column"}'))


def test_screen_tells_the_coach_where_the_athlete_is():
    bodies = []

    def post(body, key):
        bodies.append(body)
        return reply(json.dumps({"summary": "ok"}))

    coach.ask_coach(payload(), post=post, api_key="k", screen="plan")
    assert "Plan page" in bodies[0]["messages"][0]["content"]
    coach.ask_coach(payload(), post=post, api_key="k")
    assert "Plan page" not in bodies[1]["messages"][0]["content"]
