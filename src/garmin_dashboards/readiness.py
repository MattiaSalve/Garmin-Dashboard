"""Readiness rules: turn recovery data and recent training into a traffic-light verdict.

Everything here is pure Python on plain dicts so it can be unit-tested without
the web app or a database. Each signal is scored good / caution / bad; the overall
score starts at 100 and loses CAUTION_PENALTY or BAD_PENALTY per signal:

    score >= GREEN_MIN  -> green  (go hard if the plan says so)
    score >= AMBER_MIN  -> amber  (keep it aerobic)
    otherwise           -> red    (recover)

With no HRV, resting HR or sleep for last night, green is capped at MISSING_DATA_LEVEL (amber).
"""

from dataclasses import dataclass, field
from datetime import date, timedelta
from statistics import mean, stdev

GOOD, CAUTION, BAD, NO_DATA = "good", "caution", "bad", "no data"
CAUTION_PENALTY, BAD_PENALTY = 15, 35
GREEN_MIN, AMBER_MIN = 75, 45

HRV_BAD_FRACTION = 0.85      # below 85% of the low end of the range = bad
HRV_TREND_NIGHTS, HRV_TREND_BELOW = 5, 3  # 3 of the last 5 nights below range = suppressed
RHR_CAUTION_BPM, RHR_BAD_BPM = 3, 6
SLEEP_CAUTION_SCORE, SLEEP_BAD_SCORE, SLEEP_MIN_HOURS = 70, 55, 6
FORM_CAUTION, FORM_BAD = -10, -25
HARD_DAY_MINUTES, HARD_DAY_TE, HARD_DAY_LOAD = 10, 4.0, 150
GARMIN_HRV_LOW = ("LOW", "POOR")  # Garmin's own HRV status values that mean "suppressed"
# With no HRV, resting HR or sleep for last night the verdict can't be green: unknown recovery isn't good recovery.
MISSING_DATA_LEVEL = "amber"
RECOVERY_SIGNALS = ("HRV", "Resting HR", "Sleep")


@dataclass
class Signal:
    name: str
    status: str
    value: str
    explanation: str


@dataclass
class Readiness:
    level: str  # green | amber | red
    score: int
    signals: list[Signal] = field(default_factory=list)
    headline: str = ""
    hrv_range: tuple[float, float] | None = None
    has_today_data: bool = False


def hrv_range(wellness: list[dict], settings: dict) -> tuple[float, float] | None:
    """Personal range from settings, else Garmin's latest baseline, else 60-day mean ± 1 SD."""
    if settings.get("hrv_low") and settings.get("hrv_high"):
        return float(settings["hrv_low"]), float(settings["hrv_high"])
    for row in reversed(wellness):
        if row.get("hrv_baseline_low") and row.get("hrv_baseline_high"):
            return float(row["hrv_baseline_low"]), float(row["hrv_baseline_high"])
    values = [r["hrv_avg"] for r in wellness[-60:] if r.get("hrv_avg")]
    if len(values) >= 14:
        m, sd = mean(values), stdev(values)
        return m - sd, m + sd
    return None


def _hrv_signals(wellness: list[dict], today_row: dict | None, rng) -> list[Signal]:
    if rng is None:
        return [Signal("HRV", NO_DATA, "–", "Not enough HRV history for a baseline yet.")]
    low, high = rng
    out = []
    hrv = today_row and today_row.get("hrv_avg")
    if not hrv:
        out.append(Signal("HRV", NO_DATA, "–", "No HRV reading for last night."))
    elif hrv < low * HRV_BAD_FRACTION:
        out.append(Signal("HRV", BAD, f"{hrv:.0f} ms", f"Well below your range ({low:.0f}–{high:.0f} ms)."))
    elif hrv < low:
        out.append(Signal("HRV", CAUTION, f"{hrv:.0f} ms", f"Below your range ({low:.0f}–{high:.0f} ms)."))
    else:
        out.append(Signal("HRV", GOOD, f"{hrv:.0f} ms", f"Within or above your range ({low:.0f}–{high:.0f} ms)."))

    nights = [r["hrv_avg"] for r in wellness[-HRV_TREND_NIGHTS:] if r.get("hrv_avg")]
    below = sum(v < low for v in nights)
    garmin_status = (today_row or {}).get("hrv_status")
    if garmin_status in GARMIN_HRV_LOW:
        # Garmin compares the 7-day average with its baseline; trust it over our own count of low nights.
        out.append(Signal(
            "HRV trend", CAUTION, f"Garmin: {garmin_status.lower()} · {below}/{len(nights)} nights low",
            "Garmin rates your 7-day HRV as low against your baseline.",
        ))
    elif len(nights) >= HRV_TREND_BELOW:
        status = CAUTION if below >= HRV_TREND_BELOW else GOOD
        out.append(Signal(
            "HRV trend", status, f"{below}/{len(nights)} nights low",
            "HRV has been suppressed for several nights." if status == CAUTION
            else "No sustained HRV dip.",
        ))
    return out


def _rhr_signal(wellness: list[dict], today_row: dict | None) -> Signal:
    rhr = today_row and today_row.get("rhr")
    history = [r["rhr"] for r in wellness[-31:-1] if r.get("rhr")]
    if not rhr or len(history) < 7:
        return Signal("Resting HR", NO_DATA, f"{rhr:.0f} bpm" if rhr else "–", "Not enough data to compare.")
    diff = rhr - mean(history)
    value = f"{rhr:.0f} bpm ({diff:+.0f})"
    if diff > RHR_BAD_BPM:
        return Signal("Resting HR", BAD, value, "Well above your 30-day average.")
    if diff > RHR_CAUTION_BPM:
        return Signal("Resting HR", CAUTION, value, "Above your 30-day average.")
    return Signal("Resting HR", GOOD, value, "In line with your 30-day average.")


def _sleep_signal(today_row: dict | None) -> Signal:
    score = today_row and today_row.get("sleep_score")
    hours = (today_row.get("sleep_s") or 0) / 3600 if today_row else 0
    if not score:
        return Signal("Sleep", NO_DATA, "–", "No sleep score for last night.")
    value = f"{score:.0f} · {hours:.1f} h"
    if score < SLEEP_BAD_SCORE:
        return Signal("Sleep", BAD, value, "Poor sleep.")
    if score < SLEEP_CAUTION_SCORE or (hours and hours < SLEEP_MIN_HOURS):
        return Signal("Sleep", CAUTION, value, "Short or below-par sleep.")
    return Signal("Sleep", GOOD, value, "Good sleep.")


def _form_signal(form: float | None) -> Signal:
    if form is None:
        return Signal("Form", NO_DATA, "–", "No training load history.")
    value = f"{form:+.0f}"
    if form < FORM_BAD:
        return Signal("Form", BAD, value, "Heavy accumulated fatigue (fitness minus fatigue).")
    if form < FORM_CAUTION:
        return Signal("Form", CAUTION, value, "Some accumulated fatigue.")
    return Signal("Form", GOOD, value, "Fatigue is under control.")


def is_hard_day(day: dict) -> bool:
    return (
        (day.get("hard_min") or 0) >= HARD_DAY_MINUTES
        or (day.get("max_aerobic_te") or 0) >= HARD_DAY_TE
        or (day.get("load") or 0) >= HARD_DAY_LOAD
    )


def _recent_load_signal(intensity: list[dict], today: date) -> Signal:
    recent = [d for d in intensity if date.fromisoformat(d["day"]) >= today - timedelta(days=2)
              and date.fromisoformat(d["day"]) < today]
    hard = [d for d in recent if is_hard_day(d)]
    if hard:
        return Signal("Last 48 h", CAUTION, f"{len(hard)} hard day(s)", "Recent hard session; legs may not be fresh.")
    return Signal("Last 48 h", GOOD, "easy", "No hard sessions in the last two days.")


def _garmin_readiness_signal(today_row: dict | None) -> Signal | None:
    score = today_row and today_row.get("training_readiness")
    if score is None:
        return None  # not every device reports it; skip silently
    if score < 25:
        return Signal("Garmin readiness", BAD, f"{score:.0f}", "Garmin rates readiness as poor.")
    if score < 50:
        return Signal("Garmin readiness", CAUTION, f"{score:.0f}", "Garmin rates readiness as low.")
    return Signal("Garmin readiness", GOOD, f"{score:.0f}", "Garmin rates readiness as moderate or better.")


def assess(
    wellness: list[dict],
    intensity: list[dict],
    form: float | None,
    settings: dict,
    today: date,
) -> Readiness:
    """Combine all signals into a verdict. `wellness` and `intensity` are oldest-first daily rows."""
    wellness = [r for r in wellness if r["day"] <= today.isoformat()]
    today_row = next((r for r in reversed(wellness) if r["day"] == today.isoformat()), None)
    rng = hrv_range(wellness, settings)

    signals = [
        *_hrv_signals(wellness, today_row, rng),
        _rhr_signal(wellness, today_row),
        _sleep_signal(today_row),
        _form_signal(form),
        _recent_load_signal(intensity, today),
    ]
    garmin = _garmin_readiness_signal(today_row)
    if garmin:
        signals.append(garmin)

    score = 100
    for s in signals:
        score -= {CAUTION: CAUTION_PENALTY, BAD: BAD_PENALTY}.get(s.status, 0)
    score = max(score, 0)
    level = "green" if score >= GREEN_MIN else "amber" if score >= AMBER_MIN else "red"
    by_name = {s.name: s.status for s in signals}
    missing = all(by_name.get(n) == NO_DATA for n in RECOVERY_SIGNALS)
    if missing and level == "green":
        level = MISSING_DATA_LEVEL

    flagged = [s.name for s in signals if s.status in (CAUTION, BAD)]
    headline = {
        "green": "Recovered — a quality session fits today.",
        "amber": "Partly recovered — keep it aerobic today.",
        "red": "Under-recovered — rest or spin very easy.",
    }[level]
    if missing and level != "red":
        headline = "No recovery data for last night yet: keeping it aerobic."
    if flagged:
        headline += f" Watch: {', '.join(flagged)}."
    if missing and level == "red":
        headline += " (No recovery data for last night yet.)"

    return Readiness(level, score, signals, headline, rng, today_row is not None)
