"""Workout libraries (cycling and running) and the picker that matches a workout to today's readiness.

Workouts are data: a warm-up, a repeated interval block and a cool-down. Intensities are fractions of
a threshold: FTP for cycling, threshold running speed for running. `build` fits a workout into the time
available by choosing the number of repetitions and filling any leftover time with steady easy work.
The athlete's level in the chosen sport (beginner / intermediate / advanced) scales reps and session
length and decides which hard sessions are on offer.
"""

from dataclasses import dataclass, field, replace
from datetime import date, timedelta

from garmin_dashboards.readiness import BAD, Readiness, is_hard_day

REST_MAX_SCORE = 30  # red and this low means several signals are bad at once (e.g. two "bad" signals)
AEROBIC_FITNESS = (25.0, 65.0)  # peak fitness (CTL) below which overall aerobic fitness is beginner / intermediate
REST_HARD_DAYS = 2   # hard days in the last 3 that, with very low form, call for a full rest


@dataclass(frozen=True)
class Step:
    seconds: int
    low: float   # fraction of threshold (FTP for cycling, threshold running speed for running)
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

REST = Workout(
    "rest", "Rest day", "Let the accumulated fatigue clear before training again.",
    note="Off the bike or the road. Walking, stretching, food and an early night all help.",
)

LIBRARY = {  # cycling
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
LIBRARY["rest"] = REST

RUN_EASY = Step(60, 0.72, 0.82, "Easy run")
RUN_RECOVERY = Step(60, 0.62, 0.72, "Very easy jog")
RUN_JOG = Step(60, 0.65, 0.75, "Easy jog")
RUN_WALK = Step(60, 0.40, 0.50, "Walk")

RUN_LIBRARY = {
    w.key: w
    for w in [
        Workout(
            "recovery", "Recovery jog", "Promote blood flow without adding impact fatigue.",
            filler=RUN_RECOVERY, note="Walk breaks are fine. Stop early if anything hurts.",
        ),
        Workout(
            "endurance", "Easy run", "Aerobic volume at a conversational effort.", filler=RUN_EASY,
            note="Optional, only if legs feel good: finish with 4–6 × 20 s relaxed strides, walking back between.",
        ),
        Workout(
            "run_walk", "Run/walk", "Build running volume with built-in rests so impact stays manageable.",
            block=(Step(240, 0.72, 0.80, "Run"), Step(60, 0.40, 0.50, "Walk")), min_reps=3, max_reps=10,
            filler=RUN_WALK, note="Stay conversational on the run segments. Lengthen them as it gets easier over the weeks.",
        ),
        Workout(
            "fartlek", "Fartlek pick-ups", "A little speed without a structured session.",
            block=(Step(60, 0.95, 1.05, "Pick-up"), Step(120, 0.72, 0.80, "Easy")), min_reps=4, max_reps=8,
            filler=RUN_EASY, note="Pick-ups are comfortably hard, not a sprint.",
        ),
        Workout(
            "tempo", "Tempo 2 × 10", "Comfortably hard running that raises the pace you can sustain.",
            block=(Step(600, 0.88, 0.94, "Tempo"), Step(180, 0.65, 0.75, "Easy jog")), min_reps=1, max_reps=3,
            filler=RUN_EASY,
        ),
        Workout(
            "threshold", "Threshold 4 × 5", "Push threshold pace up directly.",
            block=(Step(300, 0.97, 1.03, "Threshold"), Step(120, 0.65, 0.75, "Easy jog")), min_reps=3, max_reps=5,
            filler=RUN_EASY,
        ),
        Workout(
            "vo2max", "VO₂max 5 × 3", "Raise the aerobic ceiling.",
            block=(Step(180, 1.07, 1.15, "VO₂max"), Step(180, 0.60, 0.70, "Easy jog")), min_reps=4, max_reps=6,
            filler=RUN_EASY, note="If you can't hold the low end of the target by rep 4, stop the intervals.",
        ),
    ]
}
RUN_LIBRARY["rest"] = REST

SWIM_EASY = Step(60, 0.65, 0.75, "Easy swim")
SWIM_REST = Step(30, 0.0, 0.3, "Rest")

SWIM_LIBRARY = {
    w.key: w
    for w in [
        Workout(
            "recovery", "Easy swim", "Loosen up with no impact and little strain.", filler=SWIM_EASY,
            note="Mix strokes and focus on relaxed technique.",
        ),
        Workout(
            "endurance", "Aerobic swim", "Steady aerobic volume.", filler=Step(60, 0.78, 0.88, "Steady swim"),
            note="Swim continuously or in 200–400 m blocks with about 15 s rest between.",
        ),
        Workout(
            "technique", "Technique + easy", "Build efficiency with short easy swims and drill rests.",
            block=(Step(120, 0.65, 0.75, "Easy swim"), Step(30, 0.0, 0.3, "Rest / drill")), min_reps=3, max_reps=10,
            filler=SWIM_EASY, note="Use the rests for a drill or a stroke-count check.",
        ),
        Workout(
            "tempo", "Tempo 3 × 6", "Comfortably hard swimming to sustain a faster pace.",
            block=(Step(360, 0.88, 0.94, "Tempo"), Step(60, 0.0, 0.3, "Rest")), min_reps=1, max_reps=3,
            filler=Step(60, 0.78, 0.88, "Steady swim"),
        ),
        Workout(
            "threshold", "Threshold 5 × 4", "Raise the pace you can hold for a long time.",
            block=(Step(240, 0.97, 1.03, "Threshold"), Step(45, 0.0, 0.3, "Rest")), min_reps=3, max_reps=6,
            filler=Step(60, 0.78, 0.88, "Steady swim"),
        ),
        Workout(
            "vo2max", "Fast 8 × 2", "Raise the aerobic ceiling.",
            block=(Step(120, 1.05, 1.12, "Fast"), Step(60, 0.0, 0.3, "Rest")), min_reps=4, max_reps=8,
            filler=Step(60, 0.78, 0.88, "Steady swim"),
            note="If you can't hold the low end of the target by rep 4, stop the fast swims.",
        ),
    ]
}
SWIM_LIBRARY["rest"] = REST

LEVELS = ("beginner", "intermediate", "advanced")
DEFAULT_LEVEL = "intermediate"


@dataclass(frozen=True)
class SportProfile:
    key: str
    name: str
    library: dict
    warmup_long: tuple      # used when the session is 40+ minutes
    warmup_short: Step
    cooldown: Step
    max_minutes: dict       # session length cap per level
    red_cap_minutes: int    # longest suggested session on a red day
    vo2_min_minutes: int    # shortest session that gets VO₂max work
    full_min_minutes: int   # shortest session that gets a full interval session
    # Hours per week (last 12 weeks) below which an athlete is estimated beginner / intermediate.
    level_hours: tuple[float, float]
    # Longest session (minutes, last 180 days) below which the athlete shows beginner / intermediate capacity.
    capacity_minutes: tuple[float, float]
    # Does general aerobic fitness from other sports carry over (running yes, technique-limited swimming barely)?
    aerobic_transfer: bool
    pace_unit: str = ""      # "" = no pace targets (cycling uses watts)
    pace_setting: str = ""   # settings key holding the threshold pace in seconds per pace unit


CYCLING = SportProfile(
    "cycling", "Cycling", LIBRARY, WARMUP, Step(300, 0.45, 0.60, "Warm-up"), COOLDOWN,
    {"beginner": 75, "intermediate": 180, "advanced": 300}, 45, 45, 60, (3.0, 7.0), (60, 150), False,
)
RUNNING = SportProfile(
    "running", "Running", RUN_LIBRARY,
    (Step(300, 0.55, 0.70, "Easy jog"), Step(180, 0.70, 0.80, "Steady jog")),
    Step(300, 0.55, 0.70, "Warm-up jog"), Step(300, 0.50, 0.65, "Cool-down jog or walk"),
    {"beginner": 40, "intermediate": 90, "advanced": 150}, 30, 35, 45, (1.5, 4.0), (30, 75), True,
    "/km", "run_threshold_pace_s",
)
SWIMMING = SportProfile(
    "swimming", "Swimming", SWIM_LIBRARY,
    (Step(300, 0.65, 0.75, "Easy swim"), Step(180, 0.75, 0.85, "Drills and build")),
    Step(240, 0.65, 0.75, "Easy swim"), Step(180, 0.60, 0.70, "Easy swim cool-down"),
    {"beginner": 30, "intermediate": 60, "advanced": 90}, 20, 30, 40, (1.0, 3.0), (30, 60), False,
    "/100 m", "swim_threshold_pace_s",
)
SPORTS = {CYCLING.key: CYCLING, RUNNING.key: RUNNING, SWIMMING.key: SWIMMING}


@dataclass
class Suggestion:
    workout: Workout
    steps: list[Step]
    rationale: list[str] = field(default_factory=list)
    sport: str = "cycling"
    level: str = DEFAULT_LEVEL

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
                "frac_low": s.low,
                "frac_high": s.high,
            }
            for s in self.steps
        ]


    def in_targets(self, ftp: float, threshold_pace_s: float | None = None) -> list[dict]:
        """Steps with concrete targets: watts for cycling; otherwise an effort (RPE) and, when the
        threshold pace (seconds per km or per 100 m) is known, a pace range."""
        if self.sport == "cycling":
            return self.in_watts(ftp)
        unit = SPORTS[self.sport].pace_unit
        out = []
        for s in self.steps:
            row = {"minutes": s.seconds / 60, "label": s.label, "pct_ftp": f"{s.low * 100:.0f}–{s.high * 100:.0f}%",
                   "frac_low": s.low, "frac_high": s.high, "rpe": _rpe(s.high), "pace_unit": unit}
            if threshold_pace_s and s.low >= 0.55:  # rests and walking have no pace target
                row["pace_fast"], row["pace_slow"] = _pace(threshold_pace_s / s.high), _pace(threshold_pace_s / s.low)
            out.append(row)
        return out


def _pace(seconds_per_km: float) -> str:
    total = round(seconds_per_km)
    return f"{total // 60}:{total % 60:02d}"


def _rpe(fraction: float) -> str:
    """Rough 1-10 perceived effort for an intensity given as a fraction of threshold speed."""
    for limit, label in ((0.5, "rest"), (0.72, "2–3"), (0.83, "3–4"), (0.95, "5–6"), (1.05, "7"), (1.15, "8")):
        if fraction <= limit:
            return label
    return "9"


def build(workout: Workout, minutes: int, profile: SportProfile = CYCLING) -> list[Step]:
    """Fit a workout into `minutes`: as many reps as fit (within limits), the rest as filler."""
    total = minutes * 60
    warmup = list(profile.warmup_long) if minutes >= 40 else [profile.warmup_short]
    cooldown = profile.cooldown
    fixed = sum(s.seconds for s in warmup) + cooldown.seconds
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
    elif leftover > 0:
        # Under a minute left over (e.g. 45 s swim rests): absorb it into the last step instead of dropping it.
        target = main if main else warmup
        target[-1] = replace(target[-1], seconds=target[-1].seconds + leftover)
    return warmup + main + [cooldown]


def days_since_hard(intensity: list[dict], today: date) -> int | None:
    hard_days = [date.fromisoformat(d["day"]) for d in intensity if is_hard_day(d) and d["day"] < today.isoformat()]
    return (today - max(hard_days)).days if hard_days else None


def recent_high_intensity_minutes(intensity: list[dict], today: date, days: int = 10) -> float:
    since = (today - timedelta(days=days)).isoformat()
    return sum(d.get("hard_min") or 0 for d in intensity if since <= d["day"] < today.isoformat())


def needs_rest(readiness: Readiness, intensity: list[dict], today: date) -> bool:
    """True when the athlete looks overworked: a red verdict that is very low, or red with heavy
    accumulated fatigue after repeated hard days. Plain red keeps the easy-spin suggestion."""
    if readiness.level != "red":
        return False
    if readiness.score <= REST_MAX_SCORE:
        return True
    form_bad = any(s.name == "Form" and s.status == BAD for s in readiness.signals)
    since = (today - timedelta(days=3)).isoformat()
    hard = sum(1 for d in intensity if since <= d["day"] < today.isoformat() and is_hard_day(d))
    return form_bad and hard >= REST_HARD_DAYS


def _volume_level(sport: str, weekly_hours: float) -> int:
    """0 beginner, 1 intermediate, 2 advanced, from average weekly hours in the sport."""
    low, high = SPORTS[sport].level_hours
    return 0 if weekly_hours < low else 1 if weekly_hours < high else 2


def _capacity_level(sport: str, longest_min: float) -> int:
    low, high = SPORTS[sport].capacity_minutes
    return 0 if longest_min < low else 1 if longest_min < high else 2


def _aerobic_level(fitness: float | None) -> int:
    """General aerobic fitness from every sport, from the peak fitness (CTL) of the last 90 days."""
    if fitness is None:
        return 0
    return 0 if fitness < AEROBIC_FITNESS[0] else 1 if fitness < AEROBIC_FITNESS[1] else 2


def assess_level(
    sport: str, setting: float | None, evidence: dict, aerobic_fitness: float | None,
) -> dict:
    """The athlete's level in a sport, with the reasons.

    A level set in Settings wins. Otherwise it is the best of three signals:
    - volume: average weekly hours in this sport over the last 12 weeks;
    - capacity: the longest session in this sport over the last 180 days, so someone who runs rarely
      but has done a 70-minute run is not treated as a beginner;
    - overall fitness: for sports where it carries over (running, not swimming), strong aerobic fitness
      from other sports lifts a beginner to intermediate, provided they have done the sport lately.
    Overall fitness never lifts anyone above intermediate: skill and impact tolerance are sport-specific.
    """
    if setting and 1 <= round(setting) <= len(LEVELS):
        return {"level": LEVELS[round(setting) - 1], "source": "set", "reasons": ["Set in Settings."], **_facts(evidence, aerobic_fitness)}
    hours, longest, sessions = evidence["weekly_hours"], evidence["longest_min_180d"], evidence["sessions_180d"]
    volume, capacity = _volume_level(sport, hours), _capacity_level(sport, longest)
    transfer = min(_aerobic_level(aerobic_fitness), 1) if SPORTS[sport].aerobic_transfer and sessions >= 2 else 0
    best = max(volume, capacity, transfer)
    reasons = [f"{hours:.1f} h/week in {sport} over the last 12 weeks → {LEVELS[volume]}."]
    if sessions:
        reasons.append(f"Longest {sport} session in 180 days: {longest:.0f} min → {LEVELS[capacity]}.")
    else:
        reasons.append(f"No {sport} sessions in the last 180 days.")
    if SPORTS[sport].aerobic_transfer and aerobic_fitness is not None:
        reasons.append(
            f"Overall fitness {aerobic_fitness:.0f} (all sports) → {LEVELS[_aerobic_level(aerobic_fitness)]} aerobic base"
            + (", counted because you've done this sport recently." if transfer else
               (", not counted: fewer than 2 sessions in 180 days." if _aerobic_level(aerobic_fitness) else "."))
        )
    return {"level": LEVELS[best], "source": "estimated", "reasons": reasons, **_facts(evidence, aerobic_fitness)}


def _facts(evidence: dict, aerobic_fitness: float | None) -> dict:
    return {"weekly_hours": round(evidence["weekly_hours"], 1), "longest_min": round(evidence["longest_min_180d"]),
            "overall_fitness": None if aerobic_fitness is None else round(aerobic_fitness)}


def _scaled(workout: Workout, level: str) -> Workout:
    """Fewer intervals for beginners, one more for advanced athletes."""
    if not workout.block:
        return workout
    if level == "beginner":
        return replace(workout, max_reps=max(workout.min_reps, round(workout.max_reps * 0.6)))
    if level == "advanced":
        return replace(workout, max_reps=workout.max_reps + 1)
    return workout


BEGINNER_QUALITY = {"cycling": "tempo", "running": "fartlek", "swimming": "technique"}
BEGINNER_EASY = {"running": "run_walk", "swimming": "technique"}


def _adjust_for_level(key: str, sport: str, level: str, why: list[str]) -> str:
    """Swap sessions a beginner shouldn't start with for gentler ones."""
    if level != "beginner":
        return key
    if key in ("vo2max", "threshold", "sweet_spot"):
        new = BEGINNER_QUALITY[sport]
        why.append(f"At beginner level in {sport}, {new.replace('_', ' ')} work is a better step than {key.replace('_', ' ')} sessions.")
        return new
    if key == "endurance" and sport in BEGINNER_EASY:
        new = BEGINNER_EASY[sport]
        why.append(f"As a newer {'runner' if sport == 'running' else 'swimmer'}, {new.replace('_', '/')} intervals build volume with less strain than continuous {sport}.")
        return new
    return key


def suggest(
    readiness: Readiness, minutes: int, intensity: list[dict], today: date,
    sport: str = "cycling", level: str = DEFAULT_LEVEL,
) -> Suggestion:
    """Pick and size today's workout in `sport` for an athlete of `level`.

    `intensity` is daily rows from metrics.daily_intensity, covering every sport: a hard run
    makes tomorrow's ride harder too, so recovery and recent-intensity checks look at all of it.
    """
    profile = SPORTS[sport]
    why = [readiness.headline]
    if needs_rest(readiness, intensity, today):
        why.append("Several recovery signals are poor or fatigue has piled up after hard days, so a full day off is the better call than even an easy session.")
        return Suggestion(REST, [], why, sport, level)

    cap = profile.max_minutes[level]
    if minutes > cap:
        why.append(f"Capped at {cap} min, a sensible session length for {level} {sport}.")
        minutes = cap
    verb = {"cycling": "riding", "running": "running", "swimming": "swimming"}[sport]
    if readiness.level == "red":
        key = "recovery"
        if minutes > profile.red_cap_minutes:
            minutes = profile.red_cap_minutes
        why.append("Recovery comes first; a rest day is equally fine.")
    elif readiness.level == "amber":
        key = "endurance"
        why.append(f"Aerobic {verb} builds fitness without digging a deeper hole.")
    else:
        hard_min = recent_high_intensity_minutes(intensity, today)
        since = days_since_hard(intensity, today)
        if hard_min < 15 and minutes >= profile.vo2_min_minutes:
            key = "vo2max"
            why.append(f"Only {hard_min:.0f} min above threshold in the last 10 days (all sports) — time for some top-end work.")
        elif since is not None and since <= 2:
            key = "tempo"
            why.append("You went hard recently; tempo adds work without stacking intensity.")
        elif minutes >= profile.full_min_minutes:
            key = "threshold" if hard_min < 30 else "sweet_spot"
            why.append("Recovered and enough time for a full interval session.")
        else:
            key = "sweet_spot"
            why.append("Sweet spot gives the most training per minute when time is short.")
    key = _adjust_for_level(key, sport, level, why)
    if key == "sweet_spot" and key not in profile.library:
        key = "tempo"  # only cycling has a sweet-spot session; tempo is the equivalent elsewhere
    workout = _scaled(profile.library[key], level)
    return Suggestion(workout, build(workout, minutes, profile), why, sport, level)
