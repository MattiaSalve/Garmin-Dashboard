"""Cycling workout library and the picker that matches a workout to today's readiness.

Workouts are data: a warm-up, a repeated interval block and a cool-down, all as
fractions of FTP. `build` fits a workout into the time available by choosing the
number of repetitions and filling any leftover time with steady endurance riding.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta

from garmin_dashboards.readiness import Readiness, is_hard_day


@dataclass(frozen=True)
class Step:
    seconds: int
    low: float   # fraction of FTP
    high: float
    label: str


@dataclass(frozen=True)
class Workout:
    key: str
    name: str
    purpose: str
    block: tuple[Step, ...] = ()   # one repetition of the main set (empty = continuous ride)
    min_reps: int = 0
    max_reps: int = 0
    filler: Step = Step(60, 0.60, 0.72, "Endurance")
    note: str = ""


WARMUP = (Step(480, 0.45, 0.60, "Warm-up, building"), Step(120, 0.60, 0.70, "Warm-up, steady"))
COOLDOWN = Step(300, 0.40, 0.55, "Cool-down")

LIBRARY = {
    w.key: w
    for w in [
        Workout(
            "recovery", "Recovery spin", "Promote blood flow without adding fatigue.",
            filler=Step(60, 0.45, 0.55, "Very easy spin"),
            note="Keep it truly easy; stop early if you feel worse.",
        ),
        Workout(
            "endurance", "Endurance (zone 2)", "Aerobic volume with little recovery cost.",
            note="Optional, only if legs feel good after 25 min: 3 × 3 min at 85–90% FTP, 3 min easy between.",
        ),
        Workout(
            "tempo", "Tempo 2 × 15", "Muscular endurance at a sustainable effort.",
            block=(Step(900, 0.76, 0.87, "Tempo"), Step(300, 0.50, 0.60, "Easy")), min_reps=1, max_reps=3,
        ),
        Workout(
            "sweet_spot", "Sweet spot 3 × 10", "Raise FTP with manageable fatigue.",
            block=(Step(600, 0.88, 0.93, "Sweet spot"), Step(300, 0.50, 0.60, "Easy")), min_reps=2, max_reps=4,
        ),
        Workout(
            "threshold", "Threshold 4 × 8", "Push FTP up directly.",
            block=(Step(480, 0.95, 1.02, "Threshold"), Step(240, 0.50, 0.60, "Easy")), min_reps=3, max_reps=5,
        ),
        Workout(
            "vo2max", "VO₂max 5 × 3", "Raise the aerobic ceiling.",
            block=(Step(180, 1.10, 1.20, "VO₂max"), Step(180, 0.45, 0.55, "Easy")), min_reps=4, max_reps=6,
            note="If you can't hold the low end of the target by rep 4, stop the intervals.",
        ),
    ]
}


@dataclass
class Suggestion:
    workout: Workout
    steps: list[Step]
    rationale: list[str] = field(default_factory=list)

    @property
    def minutes(self) -> int:
        return round(sum(s.seconds for s in self.steps) / 60)

    def in_watts(self, ftp: float) -> list[dict]:
        return [
            {
                "minutes": s.seconds / 60,
                "label": s.label,
                "watts_low": round(s.low * ftp),
                "watts_high": round(s.high * ftp),
                "pct_ftp": f"{s.low * 100:.0f}–{s.high * 100:.0f}%",
            }
            for s in self.steps
        ]


def build(workout: Workout, minutes: int) -> list[Step]:
    """Fit a workout into `minutes`: as many reps as fit (within limits), the rest as filler."""
    total = minutes * 60
    warmup = list(WARMUP) if minutes >= 40 else [Step(300, 0.45, 0.60, "Warm-up")]
    fixed = sum(s.seconds for s in warmup) + COOLDOWN.seconds
    available = max(total - fixed, 0)

    main: list[Step] = []
    if workout.block:
        rep = sum(s.seconds for s in workout.block)
        reps = min(workout.max_reps, available // rep)
        if reps >= workout.min_reps:
            main = [s for _ in range(reps) for s in workout.block]
    leftover = available - sum(s.seconds for s in main)
    if leftover >= 60:
        filler = Step(leftover, workout.filler.low, workout.filler.high, workout.filler.label)
        # Endurance before the intervals would delay them; put it after the main set.
        main = main + [filler]
    return warmup + main + [COOLDOWN]


def days_since_hard(intensity: list[dict], today: date) -> int | None:
    hard_days = [date.fromisoformat(d["day"]) for d in intensity if is_hard_day(d) and d["day"] < today.isoformat()]
    return (today - max(hard_days)).days if hard_days else None


def recent_high_intensity_minutes(intensity: list[dict], today: date, days: int = 10) -> float:
    since = (today - timedelta(days=days)).isoformat()
    return sum(d.get("hard_min") or 0 for d in intensity if since <= d["day"] < today.isoformat())


def suggest(readiness: Readiness, minutes: int, intensity: list[dict], today: date) -> Suggestion:
    """Pick and size today's workout. `intensity` is daily rows from metrics.daily_intensity."""
    why = [readiness.headline]
    if readiness.level == "red":
        key = "recovery"
        minutes = min(minutes, 45)
        why.append("Recovery comes first; a rest day is equally fine.")
    elif readiness.level == "amber":
        key = "endurance"
        why.append("Aerobic riding builds fitness without digging a deeper hole.")
    else:
        hard_min = recent_high_intensity_minutes(intensity, today)
        since = days_since_hard(intensity, today)
        if hard_min < 15 and minutes >= 45:
            key = "vo2max"
            why.append(f"Only {hard_min:.0f} min above threshold in the last 10 days — time for some top-end work.")
        elif since is not None and since <= 2:
            key = "tempo"
            why.append("You went hard recently; tempo adds work without stacking intensity.")
        elif minutes >= 60:
            key = "threshold" if hard_min < 30 else "sweet_spot"
            why.append("Recovered and enough time for a full interval session.")
        else:
            key = "sweet_spot"
            why.append("Sweet spot gives the most training per minute when time is short.")
    workout = LIBRARY[key]
    return Suggestion(workout, build(workout, minutes), why)
