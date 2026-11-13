"""Training plan: goals and weekly totals in, a multi-week plan of sessions out, adapted to what really happened.

Pure functions on plain data (no web framework, no database), so every rule can be unit-tested.

- The plan's currency is Garmin's per-activity training load, the same number behind fitness / fatigue / form.
  Load per hour differs a lot by sport and intensity (an easy road ride ~60 /h, an indoor interval session
  ~105 /h), so `load_rates` calibrates it from the athlete's own history.
- `build_plan` picks each week's phase (build / recovery / taper / race), its hours (the athlete's available
  time, ramped up gently) and a load cap that keeps the fitness ramp under RAMP_WARNING points a week, then
  `distribute_week` places the sessions: long session on the long day, hard days apart, gym on easy days.
- `reconcile` compares the plan with what was done and proposes changes (it never applies them): ease off
  after an overshoot, move a missed key session, lighten the week after a run of poor recovery days.
- `today_from_plan` is the one automatic adjustment: today's session is downgraded on an amber or red day,
  the same safety rule the Home page has always used.
"""

import math
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field, replace
from datetime import date, timedelta
from statistics import median

from garmin_dashboards import gym
from garmin_dashboards.fitness import CTL_DAYS
from garmin_dashboards.metrics import SPORT_GROUPS
from garmin_dashboards.readiness import Readiness
from garmin_dashboards.workouts import (
    BEGINNER_EASY, BEGINNER_QUALITY, REST, SPORTS, Suggestion, Workout, _scaled, build, needs_rest,
)

RAMP_WARNING = 7  # fitness (CTL) points per week; a faster rise is a warning sign on Home and a hard cap here
HORIZON_WEEKS = 4  # weeks planned day by day; further out (up to an event) only weekly targets
MAX_WEEKS = 52
EXTEND_WHEN_DAYS_LEFT = 14  # the plan is topped up when fewer days than this are planned ahead

ENDURANCE = tuple(SPORTS)  # cycling, running, swimming
PLAN_SPORTS = (*ENDURANCE, gym.KEY)
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
GOAL_KINDS = ("event", "volume", "performance", "general")
MAX_GOALS = 3
CLASSES = ("easy", "moderate", "hard")

KIND_CLASS = {
    "recovery": "easy", "endurance": "easy", "long": "easy", "technique": "easy", "run_walk": "easy",
    "mobility": "easy", "core": "easy", "event": "easy",
    "tempo": "moderate", "sweet_spot": "moderate", "fartlek": "moderate",
    "lower": "moderate", "upper": "moderate", "full": "moderate",
    "threshold": "hard", "vo2max": "hard",
}
HARD_KINDS = frozenset({"tempo", "sweet_spot", "fartlek", "threshold", "vo2max"})  # quality sessions, kept apart
EASY_KINDS = frozenset({"recovery", "endurance", "technique", "run_walk"})
GYM_KINDS = ("lower", "upper", "full", "core", "mobility")
KIND_LABEL = {
    "recovery": "Recovery", "endurance": "Endurance", "long": "Long", "tempo": "Tempo", "sweet_spot": "Sweet spot",
    "threshold": "Threshold", "vo2max": "VO₂max", "run_walk": "Run/walk", "fartlek": "Fartlek",
    "technique": "Technique", "lower": "Legs", "upper": "Upper body", "full": "Full body", "core": "Core",
    "mobility": "Mobility", "event": "Event", "rest": "Rest",
}
NOUN = {"cycling": "ride", "running": "run", "swimming": "swim", "gym": "gym session"}

# Load per hour when the athlete's history is too thin to measure it (Garmin training load).
DEFAULT_RATES = {
    "cycling": {"easy": 60, "moderate": 85, "hard": 105},
    "running": {"easy": 85, "moderate": 110, "hard": 130},
    "swimming": {"easy": 75, "moderate": 105, "hard": 125},
    "gym": {"easy": 15, "moderate": 20, "hard": 25},
}
MIN_SAMPLES = 3  # activities of one sport and class before their median replaces the default
EASY_SHARE, MODERATE_SHARE = 0.08, 0.18  # share of time in HR zones 4-5 that makes an activity moderate / hard

# Phases: the share of the athlete's build-week hours each one keeps.
RECOVERY_FACTOR = 0.65
TAPER = {2: 0.7, 1: 0.5}  # weeks before the event -> hours factor
RACE_FACTOR = 0.45
BASE_BEFORE_WEEKS = 16  # further than this from an event, build weeks are base weeks (less intensity)
FIRST_WEEK_RAMP, VOLUME_RAMP = 1.2, 1.1  # hours may rise 20 % (or +1 h) on the first week, then 10 % a week
NEW_START_SHARE, NEW_START_MAX = 0.5, 3.0  # starting hours after a break: half the available time, at most 3 h
RAMP_POINTS = {"beginner": 3, "intermediate": 5, "advanced": 6}  # target fitness gain per build week
MIN_WEEK_LOAD = 100
_WEEK_DECAY = (1 - 1 / CTL_DAYS) ** 7
LOAD_PER_RAMP_POINT = 1 / (1 - _WEEK_DECAY)  # extra daily load above fitness that adds one point in a week (~6.4)
MIN_AVG_SESSION = 40  # minutes; fewer sessions than asked when the hours can't fill them
LONG_MIN_BUDGET = 180  # a long session only when the week has at least this many minutes left
CRAM_LIMIT = 1.25  # a part-week left gets at most its share of the week's hours and sessions, plus 25 %

HARD_PER_WEEK = {"build": 2, "base": 1, "recovery": 0, "taper": 1, "race": 1}
LONG_CAP = {
    "cycling": {"beginner": 120, "intermediate": 240, "advanced": 300},
    "running": {"beginner": 60, "intermediate": 120, "advanced": 150},
    "swimming": {"beginner": 45, "intermediate": 75, "advanced": 90},
}
HARD_CAP = {"cycling": 90, "running": 60, "swimming": 60}
SHARPENER_CAP = {"cycling": 60, "running": 45, "swimming": 40}  # the one quality session of a taper or race week
MIN_MINUTES = {"cycling": 30, "running": 20, "swimming": 20, "gym": 20}
HARD_MIN = {"cycling": 45, "running": 35, "swimming": 30}
GYM_MINUTES = {"beginner": 40, "intermediate": 50, "advanced": 60}
GYM_SHORT = {"core": 30, "mobility": 20}
WEIGHT = {"long": 2.2, "hard": 1.0, "easy": 0.9}
LONG_WEIGHT = {"recovery": 1.8, "taper": 1.4, "race": 1.2}

# Reconcile thresholds.
OVERSHOOT_FACTOR, OVERSHOOT_MIN_LOAD = 1.5, 80  # a day at 1.5x plan and 80+ load above it
WEEK_OVERSHOOT_FACTOR = 1.2
EASE_SHARE, EASE_WEEK_CAP = 0.5, 0.25  # take off half the surplus, at most a quarter of the coming week
EASE_HARD_WITHIN = 3  # days after the big day in which a hard session is turned easy first
TRIM_MAX = 0.3  # no session is cut by more than 30 %
MISSED_FRACTION = 0.5  # under half the planned minutes = missed
STREAK_DAYS, STREAK_MIN = 3, 2  # amber or red on 2 of the last 3 days
STREAK_WINDOW = 4
STREAK_LONG_TRIM = 0.8

LONG_NAME = {"cycling": "Long ride", "running": "Long run", "swimming": "Long swim"}

# What the athlete can pick for each sport in the day editor.
KINDS_BY_SPORT = {
    **{s: ["recovery", "endurance", "long", *(k for k in SPORTS[s].library if k not in ("rest", "recovery", "endurance"))]
       for s in ENDURANCE},
    gym.KEY: list(GYM_KINDS),
}


# ------------------------------------------------------------------ data


@dataclass
class Goal:
    kind: str                     # event | volume | performance | general
    sport: str | None = None
    date: str | None = None       # ISO date: the event, or the date to reach a performance target
    target: float | None = None   # volume: hours a week; performance: FTP W or pace s/km, s/100 m; event: hours
    name: str = ""                # the athlete's own label; kept on this server, never sent to the coach


@dataclass
class Profile:
    sports: dict[str, float]      # sport -> share of sessions, e.g. {"cycling": 0.8, "gym": 0.2}
    hours_per_week: float
    sessions_per_week: int | None = None  # None = picked from the hours
    long_day: int = 5             # 0 = Monday ... 5 = Saturday
    start: str | None = None      # anchors the 3:1 build / recovery cycle

    def shares(self) -> dict[str, float]:
        total = sum(v for v in self.sports.values() if v > 0)
        return {s: v / total for s, v in self.sports.items() if v > 0 and s in PLAN_SPORTS} if total else {"cycling": 1.0}


@dataclass
class PlanDay:
    day: str
    slot: int
    sport: str          # a PLAN_SPORTS key, or "rest" for a day the athlete keeps free
    kind: str
    minutes: int
    planned_load: float = 0.0
    source: str = "auto"  # auto (rebuilt on regenerate) | user | coach | adjusted (an applied proposal)
    locked: bool = False
    note: str = ""


@dataclass
class WeekTarget:
    week_start: str
    phase: str
    hours: float
    sessions: int
    load_cap: float
    detailed: bool = True
    planned_load: float = 0.0
    note: str = ""


@dataclass
class Proposal:
    key: str            # unique: the same situation never produces a second proposal
    kind: str           # overshoot | missed | recovery | coach | week
    reason: str
    changes: list[dict] = field(default_factory=list)  # {day, slot, before, after}; None = empty slot


PHASE_NOTE = {
    "base": "Base: aerobic volume and steady tempo work.",
    "build": "Build: the main training weeks.",
    "recovery": "Recovery: less volume so the work of the last weeks turns into fitness.",
    "taper": "Taper: less volume, a little intensity, so you arrive fresh.",
    "race": "Event week: short and easy, with one sharpener.",
}


def profile_from_dict(raw: dict) -> Profile:
    return Profile(
        sports={k: float(v) for k, v in (raw.get("sports") or {}).items()},
        hours_per_week=float(raw.get("hours_per_week") or 0),
        sessions_per_week=int(raw["sessions_per_week"]) if raw.get("sessions_per_week") else None,
        long_day=int(raw.get("long_day", 5)),
        start=raw.get("start"),
    )


def goal_from_dict(raw: dict) -> Goal:
    target = raw.get("target")
    return Goal(raw.get("kind", "general"), raw.get("sport") or None, raw.get("date") or None,
                None if target in (None, "") else float(target), (raw.get("name") or "").strip())


TARGET_LIMITS = {"volume": (1, 30), "event": (0.5, 24)}
PERFORMANCE_LIMITS = {"cycling": (50, 600), "running": (120, 900), "swimming": (50, 300)}
HOURS_LIMITS, SESSIONS_LIMITS = (1, 30), (1, 10)


def setup_errors(profile: Profile, goals: list[Goal], today: date) -> list[str]:
    """Problems with a plan setup (empty = fine)."""
    errors = []
    unknown = sorted(set(profile.sports) - set(PLAN_SPORTS))
    if unknown:
        errors.append(f"Unknown sport: {', '.join(unknown)}.")
    if not any(v > 0 for v in profile.sports.values()):
        errors.append("Pick at least one sport.")
    if any(v < 0 for v in profile.sports.values()):
        errors.append("Sport shares can't be negative.")
    if not HOURS_LIMITS[0] <= profile.hours_per_week <= HOURS_LIMITS[1]:
        errors.append(f"Hours per week must be between {HOURS_LIMITS[0]} and {HOURS_LIMITS[1]}.")
    if profile.sessions_per_week is not None:
        if not SESSIONS_LIMITS[0] <= profile.sessions_per_week <= SESSIONS_LIMITS[1]:
            errors.append(f"Sessions per week must be between {SESSIONS_LIMITS[0]} and {SESSIONS_LIMITS[1]}.")
        elif profile.sessions_per_week * MIN_MINUTES["gym"] > profile.hours_per_week * 60:
            errors.append(f"{profile.sessions_per_week} sessions don't fit in {profile.hours_per_week:g} hours.")
    if not 0 <= profile.long_day <= 6:
        errors.append("The long-session day must be a weekday from 0 (Monday) to 6 (Sunday).")
    if len(goals) > MAX_GOALS:
        errors.append(f"At most {MAX_GOALS} goals.")
    chosen = {s for s, v in profile.sports.items() if v > 0}
    for i, g in enumerate(goals, 1):
        where = f"Goal {i}:"
        if g.kind not in GOAL_KINDS:
            errors.append(f"{where} unknown type {g.kind}.")
            continue
        if g.sport is not None and g.sport not in ENDURANCE:
            errors.append(f"{where} the sport must be cycling, running or swimming.")
        elif g.sport is not None and g.sport not in chosen:
            errors.append(f"{where} {g.sport} isn't one of your chosen sports.")
        if g.date:
            try:
                d = date.fromisoformat(g.date)
            except ValueError:
                errors.append(f"{where} the date isn't valid.")
                d = None
            if d and not today <= d <= today + timedelta(days=730):
                errors.append(f"{where} the date must be between today and two years from now.")
        if g.kind == "event" and not g.date:
            errors.append(f"{where} an event needs a date.")
        if g.kind == "performance":
            if g.sport not in PERFORMANCE_LIMITS:
                errors.append(f"{where} a performance target needs a sport.")
            elif g.target is None or not PERFORMANCE_LIMITS[g.sport][0] <= g.target <= PERFORMANCE_LIMITS[g.sport][1]:
                low, high = PERFORMANCE_LIMITS[g.sport]
                errors.append(f"{where} the target must be between {low} and {high}.")
        if g.kind == "volume" and (g.target is None or not TARGET_LIMITS["volume"][0] <= g.target <= TARGET_LIMITS["volume"][1]):
            errors.append(f"{where} the weekly hours target must be between 1 and 30.")
        if g.kind == "event" and g.target is not None and not TARGET_LIMITS["event"][0] <= g.target <= TARGET_LIMITS["event"][1]:
            errors.append(f"{where} the event duration must be between 0.5 and 24 hours.")
        if len(g.name) > 80:
            errors.append(f"{where} the name is too long.")
    return errors


def default_profile(weekly_hours_by_sport: dict[str, float], today: date) -> Profile:
    """A starting point for the setup form: the last 12 weeks' sport mix and weekly hours."""
    total = sum(weekly_hours_by_sport.values())
    if total <= 0:
        return Profile({"cycling": 1.0}, 5.0, None, 5, today.isoformat())
    shares = {s: round(h / total, 2) for s, h in weekly_hours_by_sport.items() if h / total >= 0.05}
    return Profile(shares, max(3.0, round(total * 2) / 2), None, 5, today.isoformat())


# ------------------------------------------------------------------ load


def plan_sport(sport_type: str | None) -> str | None:
    """The plan sport of a Garmin activity type ("road_biking" -> "cycling"), None for anything else."""
    for group, types in SPORT_GROUPS.items():
        if sport_type in types:
            return gym.KEY if group == gym.GROUP_NAME else group.lower()
    return None


def activity_class(hard_min: float, minutes: float) -> str:
    share = (hard_min or 0) / minutes if minutes else 0
    return "easy" if share < EASY_SHARE else "moderate" if share < MODERATE_SHARE else "hard"


def load_rates(activities: list[dict]) -> dict[str, dict[str, float]]:
    """Training load per hour for each sport and intensity class, from the athlete's history.

    `activities` are rows of metrics.activity_loads: sport_type, minutes, load, hard_min. A class with fewer
    than MIN_SAMPLES activities is derived from the nearest measured class of the same sport, scaled by the
    default ratios; a sport with no data uses DEFAULT_RATES. Gym load hardly depends on heart rate, so gym
    gets one rate for every class.
    """
    samples: dict[tuple[str, str], list[float]] = defaultdict(list)
    for a in activities:
        sport, minutes, load = plan_sport(a.get("sport_type")), a.get("minutes") or 0, a.get("load")
        if sport is None or not load or minutes < 10:
            continue
        cls = "moderate" if sport == gym.KEY else activity_class(a.get("hard_min") or 0, minutes)
        samples[(sport, cls)].append(load / minutes * 60)
    rates = {}
    for sport, defaults in DEFAULT_RATES.items():
        measured = {c: median(samples[(sport, c)]) for c in CLASSES if len(samples[(sport, c)]) >= MIN_SAMPLES}
        if sport == gym.KEY and measured:
            rates[sport] = {c: round(measured["moderate"], 1) for c in CLASSES}
            continue
        out = {}
        for c in CLASSES:
            if c in measured:
                out[c] = measured[c]
            elif measured:
                ref = min(measured, key=lambda m: abs(CLASSES.index(m) - CLASSES.index(c)))
                out[c] = measured[ref] * defaults[c] / defaults[ref]
            else:
                out[c] = defaults[c]
        out["moderate"] = max(out["moderate"], out["easy"])
        out["hard"] = max(out["hard"], out["moderate"])
        rates[sport] = {c: round(v, 1) for c, v in out.items()}
    return rates


def planned_load(sport: str, kind: str, minutes: float, rates: dict) -> float:
    if sport not in rates or kind in ("event", "rest"):
        return 0.0
    return round(rates[sport][KIND_CLASS.get(kind, "easy")] * minutes / 60, 1)


def advance_fitness(ctl: float, week_load: float) -> float:
    """Fitness after a week with this total load, spread evenly (the same model as fitness.fitness_series)."""
    daily = week_load / 7
    return daily + (ctl - daily) * _WEEK_DECAY


def load_cap(ctl: float, phase: str, factor: float, level: str) -> float:
    """Most load a week may hold. In build weeks it is the load that raises fitness by the level's ramp
    (never more than RAMP_WARNING points); other phases stay below the current fitness."""
    if phase in ("build", "base"):
        points = min(RAMP_POINTS.get(level, 5), RAMP_WARNING) * (0.6 if phase == "base" else 1)
        return round(7 * (max(ctl, 0) + LOAD_PER_RAMP_POINT * points))
    return round(max(7 * ctl * (factor + 0.1), MIN_WEEK_LOAD))


# ------------------------------------------------------------------ calendar helpers


def monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _d(s: str) -> date:
    return date.fromisoformat(s)


def goal_date(goal: Goal | None) -> date | None:
    if goal is None or goal.kind not in ("event", "performance") or not goal.date:
        return None
    try:
        return date.fromisoformat(goal.date)
    except ValueError:
        return None


def phase_for(week_start: date, goal: Goal | None, anchor: date) -> tuple[str, float]:
    """(phase, hours factor) for a week. With a dated goal, 3 build weeks and 1 recovery week counted back
    from a two-week taper; without one, an open 3:1 cycle from `anchor`."""
    event = goal_date(goal)
    if event:
        k = (monday(event) - week_start).days // 7
        if k == 0:
            return "race", RACE_FACTOR
        if k in TAPER:
            return "taper", TAPER[k]
        if k > 0:
            if (k - 2) % 4 == 0:
                return "recovery", RECOVERY_FACTOR
            return ("base" if k > BASE_BEFORE_WEEKS else "build"), 1.0
        if k == -1:
            return "recovery", RECOVERY_FACTOR
        anchor = monday(event) + timedelta(weeks=2)
    i = (week_start - monday(anchor)).days // 7
    return ("recovery", RECOVERY_FACTOR) if i % 4 == 3 else ("build", 1.0)


def default_sessions(hours: float, level: str) -> int:
    n = 3 if hours <= 3 else 4 if hours <= 5 else 5 if hours <= 8 else 6 if hours <= 12 else 7
    return min(n, 4) if level == "beginner" else n


def _primary(profile: Profile, goal: Goal | None) -> str | None:
    shares = profile.shares()
    if goal and goal.sport in shares:
        return goal.sport
    endurance = {s: v for s, v in shares.items() if s in ENDURANCE}
    return max(endurance, key=endurance.get) if endurance else None


def _day_order(long_day: int) -> list[int]:
    """Weekdays in the order sessions are added: the long day, then two days well apart from it (the usual
    hard days), then the day after the long one, and so on. 5 sessions with a Saturday long ride train on
    Tue, Wed, Thu, Sat and Sun and rest on Mon and Fri."""
    return [(long_day + o) % 7 for o in (0, -4, -2, 1, -3, -5, -1)]


# ------------------------------------------------------------------ choosing sessions


def easy_kind(sport: str, level: str) -> str:
    if sport == gym.KEY:
        return "mobility"
    return BEGINNER_EASY.get(sport, "endurance") if level == "beginner" else "endurance"


def _fit_kind(kind: str, sport: str, level: str) -> str:
    if level == "beginner" and kind in ("vo2max", "threshold", "sweet_spot"):
        kind = BEGINNER_QUALITY[sport]
    if kind not in SPORTS[sport].library:
        kind = "tempo"  # only cycling has sweet spot
    return kind


def _hard_kinds(goal: Goal | None, phase: str, sport: str, week_start: date) -> list[str]:
    if phase in ("taper", "race"):
        return ["threshold"]
    if phase == "base":
        kinds = ["sweet_spot", "tempo"]
    elif goal and goal.kind == "performance":
        kinds = ["threshold", "sweet_spot"] if sport == "cycling" else ["threshold", "tempo"]
    elif goal and goal.kind == "volume":
        kinds = ["tempo", "sweet_spot"]
    else:
        kinds = ["threshold", "vo2max"]
    return kinds[::-1] if week_start.isocalendar().week % 2 else kinds


def _gym_focuses(n: int, gym_goal: str, phase: str) -> list[str]:
    if phase in ("taper", "race"):
        return ["mobility"] * n
    if phase == "recovery":
        return (["core", "mobility"] * n)[:n]
    if gym_goal == "endurance":
        order = ["full"] if n == 1 else ["lower", "core", "upper"]
    else:
        order = ["full"] if n == 1 else ["lower", "upper", "core"]
    return [order[i % len(order)] for i in range(n)]


def _largest_remainder(shares: dict[str, float], n: int) -> Counter:
    raw = {s: v * n for s, v in shares.items()}
    out = Counter({s: int(v) for s, v in raw.items()})
    for s in sorted(raw, key=lambda s: raw[s] - int(raw[s]), reverse=True)[: n - sum(out.values())]:
        out[s] += 1
    return out


def _water_fill(total: float, items: list[tuple[float, float, float]]) -> list[float]:
    """Split `total` in proportion to each item's weight, within its (low, high) bounds."""
    alloc: list[float | None] = [None] * len(items)
    remaining = total
    for _ in range(len(items) + 1):
        free = [i for i, a in enumerate(alloc) if a is None]
        if not free:
            break
        weight = sum(items[i][0] for i in free)
        share = {i: remaining * items[i][0] / weight for i in free}
        over = [i for i in free if share[i] > items[i][2]]
        under = [i for i in free if share[i] < items[i][1]]
        fix = over or under
        if not fix:
            for i in free:
                alloc[i] = share[i]
            break
        for i in fix:
            alloc[i] = items[i][2] if i in over else items[i][1]
            remaining -= alloc[i]
    return [a if a is not None else items[i][1] for i, a in enumerate(alloc)]


def _round5(x: float, low: float) -> int:
    return int(max(low, 5 * round(x / 5)))


def distribute_week(
    week: WeekTarget, profile: Profile, goal: Goal | None, levels: dict[str, str], rates: dict, today: date,
    fixed: list[PlanDay] = (), done: dict[str, dict] | None = None, gym_goal: str = gym.DEFAULT_GOAL,
) -> list[PlanDay]:
    """New sessions for one week, around the sessions the athlete already fixed (edited, locked, coach or
    applied changes; rest days they keep free) and, in the current week, what they have already done.

    `done` maps past days to {"minutes", "load", "sports": {sport: minutes}} of real activities.
    """
    done = done or {}
    start = _d(week.week_start)
    days = [start + timedelta(days=i) for i in range(7)]
    open_days = [d for d in days if d >= today]
    if not open_days or week.hours <= 0:
        return []
    shares = profile.shares()
    primary = _primary(profile, goal)
    fixed = [p for p in fixed if p.day >= today.isoformat()]
    real_fixed = [p for p in fixed if p.kind != "rest"]
    taken = {p.day for p in fixed}
    past = [d.isoformat() for d in days if d < today]
    done_min = sum(done.get(d, {}).get("minutes", 0) for d in past)
    done_load = sum(done.get(d, {}).get("load", 0) for d in past)
    done_sports: Counter = Counter()
    for d in past:
        for s, m in (done.get(d, {}).get("sports") or {}).items():
            if m >= 15:
                done_sports[s] += 1
    target_min = week.hours * 60
    part = len(open_days) / 7 * CRAM_LIMIT
    budget = min(target_min - done_min - sum(p.minutes for p in real_fixed), target_min * part)
    n_new = min(week.sessions - sum(done_sports.values()) - len(real_fixed), math.ceil(week.sessions * part))
    n_new = min(n_new, max(1, int(budget // MIN_AVG_SESSION)))  # a busy week gets fewer sessions, not stubs

    event = goal_date(goal) if week.phase == "race" else None
    free = [d for d in open_days if d.isoformat() not in taken]
    if event:  # the day before the event stays free
        free = [d for d in free if d != event - timedelta(days=1)]
    if n_new <= 0 or budget < MIN_MINUTES["gym"] or not free:
        return []

    # How many sessions of each sport: the shares over the whole week, minus what's already there.
    desired = _largest_remainder(shares, week.sessions)
    deficit = {s: desired[s] - done_sports[s] - sum(1 for p in real_fixed if p.sport == s) for s in shares}
    counts: Counter = Counter()
    for _ in range(n_new):
        s = max(shares, key=lambda s: (deficit[s], shares[s]))
        counts[s] += 1
        deficit[s] -= 1
    gym_n = counts.pop(gym.KEY, 0)
    end_n = sum(counts.values())
    gym_double = min(gym_n, max(0, n_new - len(free)))  # gym can share a day with a ride or run
    gym_single = gym_n - gym_double
    while end_n + gym_single > len(free):
        s = min((s for s in counts if counts[s]), key=lambda s: shares[s])
        counts[s] -= 1
        end_n -= 1
    if end_n == 0 and gym_double:
        gym_single, gym_double = min(gym_n, len(free)), 0

    order = [start + timedelta(days=w) for w in _day_order(profile.long_day)]
    picked = [d for d in order if d in free][: end_n + gym_single]

    def take(prefer: str | None) -> str | None:
        options = [s for s in counts if counts[s] > 0]
        if not options:
            return None
        s = prefer if prefer in options else max(options, key=lambda s: (counts[s], shares[s]))
        counts[s] -= 1
        return s

    level_of = lambda s: levels.get(s, "intermediate")  # noqa: E731
    fixed_hard = {_d(p.day) for p in real_fixed if p.kind in HARD_KINDS}
    longs = {_d(p.day) for p in real_fixed if p.kind == "long"}
    roles: dict[date, tuple[str, str, str]] = {}  # day -> (sport, kind, role)

    long_date = start + timedelta(days=profile.long_day)
    endurance_this_week = end_n + sum(1 for p in real_fixed if p.sport in ENDURANCE)
    if (long_date in picked and end_n and not longs and week.phase != "race" and endurance_this_week >= 2
            and budget >= LONG_MIN_BUDGET):
        roles[long_date] = (take(primary), "long", "long")
        longs.add(long_date)

    hard_n = HARD_PER_WEEK[week.phase]
    if level_of(primary) == "beginner":
        hard_n = min(hard_n, 1)
    easy_left = end_n - len(roles)
    hard_n = min(hard_n - len(fixed_hard), easy_left - (1 if end_n >= 4 else 0))
    hard_dates: list[date] = []
    for d in picked:
        if len(hard_dates) >= max(hard_n, 0):
            break
        if d in roles:
            continue
        if any(abs((d - h).days) <= 1 for h in fixed_hard | set(hard_dates)):
            continue
        if any(abs((d - l).days) == 1 for l in longs):  # not the day before or after the long session
            continue
        if event and not 3 <= (event - d).days:
            continue
        hard_dates.append(d)

    key_days = set(hard_dates) | fixed_hard | longs
    rest_days = [d for d in picked if d not in roles and d not in hard_dates]
    before_key = lambda d: (d + timedelta(days=1)) in key_days  # noqa: E731
    gym_days = sorted(rest_days, key=lambda d: (before_key(d), order.index(d)))[:gym_single]
    easy_days = [d for d in rest_days if d not in gym_days]

    hard_kinds = _hard_kinds(goal, week.phase, primary or "cycling", start)
    for i, d in enumerate(sorted(hard_dates)):
        s = take(primary)
        roles[d] = (s, _fit_kind(hard_kinds[i % len(hard_kinds)], s, level_of(s)), "hard")
    for d in easy_days:
        s = take(None)
        roles[d] = (s, easy_kind(s, level_of(s)), "easy")

    focuses = _gym_focuses(gym_n, gym_goal, week.phase)
    legs_first = sorted(focuses, key=lambda f: f not in ("lower", "full"))
    gym_slots = [(d, 0) for d in sorted(gym_days, key=before_key)]
    doubles = sorted((d for d in easy_days), key=lambda d: (before_key(d), order.index(d))) or sorted(hard_dates)
    gym_slots += [(d, 1) for d in (doubles * gym_double)[:gym_double]]
    gym_rows = []
    for (d, slot), focus in zip(gym_slots, legs_first):
        if focus in ("lower", "full") and before_key(d):
            focus = "core" if gym_goal == "endurance" else "upper"
        gym_rows.append((d, slot, focus))

    # Minutes: gym sessions take their usual length, the rest of the budget is shared by weight.
    gym_level = level_of(gym.KEY)
    gym_minutes = [GYM_SHORT.get(f, GYM_MINUTES[gym_level]) for _, _, f in gym_rows]
    endurance = sorted((d, *r) for d, r in roles.items())
    items = []
    for d, sport, kind, role in endurance:
        lvl = level_of(sport)
        long_cap = LONG_CAP[sport][lvl]
        if role == "long":
            items.append((LONG_WEIGHT.get(week.phase, WEIGHT["long"]), MIN_MINUTES[sport] * 2, long_cap))
        elif role == "hard":
            cap = SHARPENER_CAP[sport] if week.phase in ("taper", "race") else HARD_CAP[sport]
            items.append((WEIGHT["hard"], HARD_MIN[sport], min(cap, SPORTS[sport].max_minutes[lvl])))
        else:
            items.append((WEIGHT["easy"], MIN_MINUTES[sport], min(SPORTS[sport].max_minutes[lvl], long_cap * 0.75)))
    minutes = _water_fill(max(budget - sum(gym_minutes), 0), items) if items else []
    sessions = [
        PlanDay(d.isoformat(), 0, sport, kind, _round5(m, items[i][1]))
        for i, ((d, sport, kind, _), m) in enumerate(zip(endurance, minutes))
    ]
    sessions += [PlanDay(d.isoformat(), slot, gym.KEY, f, m) for (d, slot, f), m in zip(gym_rows, gym_minutes)]
    for p in sessions:
        p.planned_load = planned_load(p.sport, p.kind, p.minutes, rates)

    # Keep the week under its load cap by shortening the endurance sessions.
    other = done_load + sum(p.planned_load for p in real_fixed) + sum(p.planned_load for p in sessions if p.sport == gym.KEY)
    new_load = sum(p.planned_load for p in sessions if p.sport != gym.KEY)
    if new_load and other + new_load > week.load_cap:
        f = max(week.load_cap - other, 0) / new_load
        for p, (_, low, _) in zip(sessions, items):
            p.minutes = int(max(low, 5 * math.floor(p.minutes * f / 5)))
            p.planned_load = planned_load(p.sport, p.kind, p.minutes, rates)
    return sorted(sessions, key=lambda p: (p.day, p.slot))


def build_plan(
    today: date, ctl: float, profile: Profile, goals: list[Goal], levels: dict[str, str], rates: dict,
    recent_hours: float, overrides: dict[str, float] | None = None, existing: list[PlanDay] = (),
    done: dict[str, dict] | None = None, gym_goal: str = gym.DEFAULT_GOAL, only_empty_weeks: bool = False,
    horizon: int = HORIZON_WEEKS,
) -> tuple[list[WeekTarget], list[PlanDay]]:
    """Week targets from this week up to the event (at least `horizon` weeks), and the new auto sessions for
    the first `horizon` weeks.

    Existing sessions that aren't plain auto ones (edited, locked, coach, applied proposals, kept rest days)
    and everything before today are kept; the caller replaces the remaining auto sessions with the result.
    With `only_empty_weeks`, weeks that already hold any session are left as they are (topping up the plan).
    """
    goal = goals[0] if goals else None
    overrides = overrides or {}
    done = done or {}
    week0 = monday(today)
    event = goal_date(goal)
    n_weeks = horizon
    if event and event >= week0:
        n_weeks = max(horizon, min(MAX_WEEKS, (monday(event) - week0).days // 7 + 1))
    anchor = _d(profile.start) if profile.start else week0
    primary = _primary(profile, goal)
    level = levels.get(primary, "intermediate") if primary else levels.get(gym.KEY, "intermediate")
    kept = [p for p in existing if p.source != "auto" or p.locked or p.day < today.isoformat()]
    busy_weeks = {monday(_d(p.day)) for p in existing} if only_empty_weeks else set()
    blended = (0.75 * rates[primary]["easy"] + 0.25 * rates[primary]["moderate"]) if primary else rates[gym.KEY]["moderate"]

    weeks, new_days = [], []
    # Ramp from what the athlete has been doing; with little recent history, from a modest share of their time.
    build_hours = max(recent_hours, min(profile.hours_per_week * NEW_START_SHARE, NEW_START_MAX), 1.0)
    first_build = True
    for i in range(n_weeks):
        ws = week0 + timedelta(weeks=i)
        we = ws + timedelta(days=6)
        phase, factor = phase_for(ws, goal, anchor)
        if i == 0 and phase == "recovery":  # a plan never opens with a recovery week: nothing to recover from yet
            phase, factor = "build", 1.0
        available = min(profile.hours_per_week, overrides.get(ws.isoformat(), math.inf))
        if phase in ("build", "base"):
            goal_hours = goal.target if goal and goal.kind == "volume" and goal.target else available
            cap = max(build_hours * FIRST_WEEK_RAMP, build_hours + 1) if first_build else build_hours * VOLUME_RAMP
            exact = min(goal_hours, cap, available)
            hours = math.floor(exact * 2) / 2  # shown in half hours; the ramp carries on from the exact value
            build_hours, first_build = max(exact, 1.0), False
        else:
            hours = round(min(build_hours * factor, available) * 2) / 2
        sessions = profile.sessions_per_week or default_sessions(hours, level)
        if phase == "race":
            sessions = min(sessions, 4)
        target = WeekTarget(ws.isoformat(), phase, hours, sessions, load_cap(ctl, phase, factor, level),
                            detailed=i < horizon, note=PHASE_NOTE[phase])
        in_week = lambda p: ws.isoformat() <= p.day <= we.isoformat()  # noqa: E731
        past_load = sum(v.get("load", 0) for d, v in done.items() if ws.isoformat() <= d < today.isoformat())
        if i < horizon and ws not in busy_weeks:
            week_fixed = [p for p in kept if in_week(p)]
            if event and phase == "race" and goal.kind == "event" and event >= today and not any(p.day == event.isoformat() for p in week_fixed):
                event_row = PlanDay(event.isoformat(), 0, goal.sport or primary or "cycling", "event",
                                    round((goal.target or 0) * 60), 0.0, note=goal.name or "Event")
                week_fixed.append(event_row)
                new_days.append(event_row)
            added = distribute_week(target, profile, goal, levels, rates, today, week_fixed, done, gym_goal)
            new_days += added
            week_load = past_load + sum(p.planned_load for p in week_fixed + added if p.day >= today.isoformat())
        elif i < horizon:
            week_load = past_load + sum(p.planned_load for p in existing if in_week(p) and p.day >= today.isoformat())
        else:
            week_load = min(target.load_cap, hours * blended)
        target.planned_load = round(week_load)
        ctl = advance_fitness(ctl, week_load)
        weeks.append(target)
    return weeks, new_days


# ------------------------------------------------------------------ planned vs actual


def describe(sport: str, kind: str, minutes: float | None = None) -> str:
    """"Threshold ride, 75 min", "Gym: legs, 50 min", "Rest day"."""
    if kind == "rest" or sport == "rest":
        return "Rest day"
    if kind == "event":
        return "Event day"
    if sport == gym.KEY:
        text = f"Gym: {KIND_LABEL.get(kind, kind).lower()}"
    else:
        text = f"{KIND_LABEL.get(kind, kind)} {NOUN.get(sport, sport)}"
    return f"{text}, {fmt_minutes(minutes)}" if minutes else text


def fmt_minutes(minutes: float) -> str:
    minutes = round(minutes or 0)
    if minutes < 60:
        return f"{minutes} min"
    return f"{minutes // 60} h" + (f" {minutes % 60:02d}" if minutes % 60 else "")


def day_status(sessions: list[PlanDay], actual: dict | None, day: date, today: date) -> str:
    """done / partial / missed / extra / rest / planned / today for one day."""
    planned = sum(p.minutes for p in sessions if p.kind not in ("rest",))
    did = (actual or {}).get("minutes", 0)
    if planned <= 0:
        return "extra" if did >= 10 else "rest"
    if did >= 0.8 * planned:
        return "done"
    if day > today:
        return "planned"
    if day == today:
        return "partial" if did >= 10 else "today"
    return "partial" if did >= 0.3 * planned else "missed"


def week_summary(week_start: date, plan: list[PlanDay], actual: dict[str, dict], today: date) -> dict:
    days = [week_start + timedelta(days=i) for i in range(7)]
    by_day = defaultdict(list)
    for p in plan:
        by_day[p.day].append(p)
    rows = [p for d in days for p in by_day[d.isoformat()] if p.kind != "rest"]
    return {
        "planned_hours": round(sum(p.minutes for p in rows) / 60, 1),
        "planned_load": round(sum(p.planned_load for p in rows)),
        "actual_hours": round(sum(actual.get(d.isoformat(), {}).get("minutes", 0) for d in days) / 60, 1),
        "actual_load": round(sum(actual.get(d.isoformat(), {}).get("load", 0) for d in days)),
        "status": {d.isoformat(): day_status(by_day[d.isoformat()], actual.get(d.isoformat()), d, today) for d in days},
    }


def session_view(p: PlanDay | None) -> dict | None:
    return None if p is None else {"sport": p.sport, "kind": p.kind, "minutes": p.minutes, "planned_load": p.planned_load}


def _change(day: str, slot: int, before: PlanDay | None, after: PlanDay | None) -> dict:
    return {"day": day, "slot": slot, "before": session_view(before), "after": session_view(after)}


def is_stale(change: dict, current: PlanDay | None) -> bool:
    """True when the plan no longer looks like it did when the change was proposed."""
    before = change.get("before")
    if before is None:
        return current is not None and current.kind != "rest"
    if current is None:
        return True
    return (current.sport, current.kind, current.minutes) != (before["sport"], before["kind"], before["minutes"])


def _ease(
    upcoming: list[PlanDay], after_day: date, reduce: float, rates: dict, levels: dict[str, str],
) -> tuple[list[dict], float]:
    """Changes that take about `reduce` load out of the coming days: first the next hard session within
    EASE_HARD_WITHIN days becomes easy, then the longest sessions are trimmed (each by at most TRIM_MAX)."""
    changes, saved, touched = [], 0.0, set()
    hard = sorted((p for p in upcoming if p.kind in HARD_KINDS and 1 <= (_d(p.day) - after_day).days <= EASE_HARD_WITHIN),
                  key=lambda p: p.day)
    if hard:
        p = hard[0]
        kind = easy_kind(p.sport, levels.get(p.sport, "intermediate"))
        new = replace(p, kind=kind, planned_load=planned_load(p.sport, kind, p.minutes, rates))
        saved += p.planned_load - new.planned_load
        changes.append(_change(p.day, p.slot, p, new))
        touched.add((p.day, p.slot))
    for p in sorted(upcoming, key=lambda p: (-p.minutes, p.day)):
        if saved >= reduce:
            break
        if (p.day, p.slot) in touched or p.sport == gym.KEY:
            continue
        rate = rates[p.sport][KIND_CLASS.get(p.kind, "easy")]
        need = (reduce - saved) / rate * 60
        cut = min(p.minutes * TRIM_MAX, p.minutes - MIN_MINUTES[p.sport], 5 * math.ceil(need / 5))
        cut = 5 * math.floor(cut / 5)
        if cut < 10:
            continue
        new = replace(p, minutes=p.minutes - cut, planned_load=planned_load(p.sport, p.kind, p.minutes - cut, rates))
        saved += p.planned_load - new.planned_load
        changes.append(_change(p.day, p.slot, p, new))
    return changes, saved


def reconcile(
    plan: list[PlanDay], actual: dict[str, dict], today: date, rates: dict, levels: dict[str, str],
    readiness_levels: dict[str, str] | None = None,
) -> list[Proposal]:
    """Proposals from comparing the plan with what was done. Nothing is applied here.

    `actual` maps days to {"minutes", "load", "sports"}; `readiness_levels` maps today and the days before
    to green / amber / red.
    """
    if not plan:
        return []
    first = monday(_d(min(p.day for p in plan))).isoformat()  # the plan's first week
    by_day: dict[str, list[PlanDay]] = defaultdict(list)
    for p in plan:
        by_day[p.day].append(p)
    planned = lambda d: sum(p.planned_load for p in by_day[d] if p.kind not in ("rest", "event"))  # noqa: E731
    planned_min = lambda d: sum(p.minutes for p in by_day[d] if p.kind not in ("rest", "event"))  # noqa: E731
    got = lambda d, k: (actual.get(d) or {}).get(k, 0)  # noqa: E731
    end7 = (today + timedelta(days=6)).isoformat()
    upcoming = [p for p in plan if today.isoformat() <= p.day <= end7 and not p.locked and p.kind not in ("rest", "event")]
    week_load = sum(p.planned_load for p in upcoming)
    out: list[Proposal] = []

    # Too much: one big day, or a week running well over plan.
    big = None
    for back in (1, 2, 3):
        d = (today - timedelta(days=back)).isoformat()
        if d < first:
            continue
        a, p = got(d, "load"), planned(d)
        if a - p >= OVERSHOOT_MIN_LOAD and a >= OVERSHOOT_FACTOR * p and (big is None or a - p > big[1] - big[2]):
            big = (d, a, p)
    ws = monday(today)
    if big is None and today > ws:
        days = [(ws + timedelta(days=i)).isoformat() for i in range((today - ws).days)]
        days = [d for d in days if d >= first]
        a, p = sum(got(d, "load") for d in days), sum(planned(d) for d in days)
        if days and a - p >= OVERSHOOT_MIN_LOAD and a >= WEEK_OVERSHOOT_FACTOR * p:
            big = ("week", a, p)
    if big and week_load:
        d, a, p = big
        surplus = a - p
        after = today - timedelta(days=1) if d == "week" else _d(d)
        changes, saved = _ease(upcoming, after, min(EASE_SHARE * surplus, EASE_WEEK_CAP * week_load), rates, levels)
        if changes:
            if d == "week":
                key = f"overshoot-week:{ws.isoformat()}"
                what = f"This week so far you've done load {a:.0f} against {p:.0f} planned."
            else:
                key = f"overshoot:{d}"
                plan_text = fmt_minutes(planned_min(d)) if planned_min(d) else "a rest day"
                what = (f"On {WEEKDAYS[_d(d).weekday()]} you trained {fmt_minutes(got(d, 'minutes'))} (load {a:.0f}) "
                        f"where the plan had {plan_text} (load {p:.0f}).")
            out.append(Proposal(key, "overshoot", f"{what} Easing the next days by about {saved:.0f} load lets "
                                "that extra work turn into fitness instead of piling up fatigue.", changes))

    # Poor recovery for several days, still poor today: lighten what's ahead (today itself is handled on Home).
    levels_seen = [(readiness_levels or {}).get((today - timedelta(days=i)).isoformat()) for i in range(STREAK_DAYS)]
    if levels_seen[0] in ("amber", "red") and sum(lv in ("amber", "red") for lv in levels_seen) >= STREAK_MIN:
        window = [p for p in upcoming if 1 <= (_d(p.day) - today).days <= STREAK_WINDOW]
        changes = []
        for p in window:
            if p.kind in HARD_KINDS:
                kind = easy_kind(p.sport, levels.get(p.sport, "intermediate"))
                changes.append(_change(p.day, p.slot, p, replace(p, kind=kind, planned_load=planned_load(p.sport, kind, p.minutes, rates))))
            elif p.kind == "long":
                m = _round5(p.minutes * STREAK_LONG_TRIM, MIN_MINUTES[p.sport])
                changes.append(_change(p.day, p.slot, p, replace(p, minutes=m, planned_load=planned_load(p.sport, p.kind, m, rates))))
        if changes:
            bad = sum(lv in ("amber", "red") for lv in levels_seen)
            out.append(Proposal(
                f"recovery:{ws.isoformat()}", "recovery",
                f"Your readiness has been amber or red on {bad} of the last {STREAK_DAYS} days. Swapping the next "
                "hard sessions for easy ones (and a shorter long session) gives your body room to recover.", changes,
            ))

    # A missed key session: move it once to a day it fits, never cram it in on top.
    if not out:
        week_end = ws + timedelta(days=6)
        for back in (1, 2):
            d = (today - timedelta(days=back)).isoformat()
            if d < first:
                continue
            for p in by_day[d]:
                if p.sport not in ENDURANCE or not (p.kind in HARD_KINDS or p.kind == "long"):
                    continue
                if got(d, "minutes") >= MISSED_FRACTION * p.minutes:
                    continue
                change = _move_target(p, by_day, today, week_end)
                if change:
                    out.append(Proposal(
                        f"missed:{d}:{p.slot}", "missed",
                        f"{WEEKDAYS[_d(d).weekday()]}'s {describe(p.sport, p.kind, p.minutes).lower()} didn't happen. "
                        f"It fits on {WEEKDAYS[_d(change['day']).weekday()]} without breaking the easy-hard spacing, so "
                        "it's moved there. Nothing extra is crammed in to catch up.", [change],
                    ))
                    return out
    return out


def _move_target(missed: PlanDay, by_day: dict[str, list[PlanDay]], today: date, week_end: date) -> dict | None:
    hard_days = {_d(d) for d, ps in by_day.items() for p in ps if p.kind in HARD_KINDS and d != missed.day}
    long_days = {_d(d) for d, ps in by_day.items() for p in ps if p.kind == "long" and d != missed.day}
    t = today
    while t <= week_end:
        rows = by_day.get(t.isoformat(), [])
        ok = True
        if any(p.kind in ("rest", "event") for p in rows) or t in long_days:
            ok = False
        elif missed.kind in HARD_KINDS:
            ok = not any(abs((t - h).days) <= 1 for h in hard_days) and not any(abs((t - l).days) == 1 for l in long_days)
        else:  # long
            ok = t not in hard_days and (t + timedelta(days=1)) not in hard_days
        if ok:
            moved = replace(missed, day=t.isoformat(), slot=0, source="adjusted", locked=False)
            if not rows:
                return _change(t.isoformat(), 0, None, moved)
            main = [p for p in rows if p.slot == 0]
            if (len(main) == 1 and main[0].sport == missed.sport and main[0].kind in EASY_KINDS and not main[0].locked):
                return _change(t.isoformat(), 0, main[0], moved)
        t += timedelta(days=1)
    return None


def valid_session(sport: str, kind: str, minutes: int) -> str | None:
    """Why a hand-edited session isn't allowed (None = fine)."""
    if sport not in KINDS_BY_SPORT:
        return f"Unknown sport: {sport}."
    if kind not in KINDS_BY_SPORT[sport]:
        return f"{kind} isn't a {sport} session. Choose one of {', '.join(KINDS_BY_SPORT[sport])}."
    if not 10 <= minutes <= 600:
        return "Minutes must be between 10 and 600."
    return None


def spacing_warnings(sessions: list[PlanDay], day: str) -> list[str]:
    """Friendly warnings when a hand-placed session breaks the easy-hard spacing around `day` (never blocks)."""
    d = _d(day)
    by_day: dict[date, set[str]] = defaultdict(set)
    for p in sessions:
        by_day[_d(p.day)].add(p.kind)
    here = by_day[d]
    out = []
    near = lambda off, kinds: bool(by_day[d + timedelta(days=off)] & kinds)  # noqa: E731
    if here & HARD_KINDS and (near(-1, HARD_KINDS) or near(1, HARD_KINDS)):
        out.append("Two hard days in a row: the second one usually ends up half-hearted.")
    if here & HARD_KINDS and near(-1, {"long"}):
        out.append("A hard session the day after the long one is tough to do well.")
    if "long" in here and near(1, HARD_KINDS):
        out.append("The long session is right before a hard day.")
    if here & {"lower", "full"} and (near(1, HARD_KINDS) or near(1, {"long"})):
        out.append("Leg work the day before a hard or long session leaves heavy legs for it.")
    return out


# ------------------------------------------------------------------ AI coach

COACH_WEEKS = 2  # this week and next: what the coach sees in detail and may change
COACH_COMPLIANCE_WEEKS = 2
COACH_NOTE_MAX = 200


def coach_summary(
    profile: Profile, goals: list[Goal], weeks: list[WeekTarget], rows: list[PlanDay],
    actual: dict[str, dict], today: date, proposals: list[dict] = (),
) -> dict:
    """The plan as the AI coach sees it: identifier-free (no goal names, activity names or places).

    `rows` should cover the compliance weeks before this one and the COACH_WEEKS from this Monday on;
    `actual` the same days, as {day: {minutes, load, ...}}.
    """
    week0 = monday(today)
    targets = {w.week_start: w for w in weeks}
    by_day: dict[str, list[PlanDay]] = defaultdict(list)
    for p in rows:
        by_day[p.day].append(p)

    def week_block(ws: date) -> dict:
        t = targets.get(ws.isoformat())
        days = []
        for i in range(7):
            d = ws + timedelta(days=i)
            iso = d.isoformat()
            a = actual.get(iso) or {}
            days.append({
                "day": iso, "weekday": WEEKDAYS[d.weekday()],
                "status": day_status(by_day[iso], a, d, today),
                "planned": [
                    {"slot": p.slot, "sport": p.sport, "kind": p.kind, "minutes": p.minutes,
                     "load": round(p.planned_load), "locked": p.locked, "edited_by_athlete": p.source == "user"}
                    for p in sorted(by_day[iso], key=lambda p: p.slot) if p.kind != "rest"
                ],
                "done_minutes": round(a.get("minutes", 0)), "done_load": round(a.get("load", 0)),
            })
        return {"week_start": ws.isoformat(), "phase": t.phase if t else None,
                "target_hours": t.hours if t else None, "days": days}

    compliance = []
    for back in range(COACH_COMPLIANCE_WEEKS, 0, -1):
        ws = week0 - timedelta(weeks=back)
        s = week_summary(ws, [p for p in rows if ws.isoformat() <= p.day <= (ws + timedelta(days=6)).isoformat()],
                         actual, today)
        compliance.append({"week_start": ws.isoformat(), **{k: v for k, v in s.items() if k != "status"}})

    def goal_out(g: Goal) -> dict:
        when = goal_date(g)
        return {"kind": g.kind, "sport": g.sport, "date": g.date, "target": g.target,
                "weeks_to_go": None if when is None else round((when - today).days / 7, 1)}

    return {
        "today": today.isoformat(),
        "goals": [goal_out(g) for g in goals],  # the first one drives the phases
        "profile": {
            "sports": {s: round(v, 2) for s, v in profile.shares().items()},
            "hours_per_week": profile.hours_per_week, "sessions_per_week": profile.sessions_per_week,
            "long_day": WEEKDAYS[profile.long_day],
        },
        "upcoming_phases": [{"week_start": w.week_start, "phase": w.phase, "target_hours": w.hours} for w in weeks[:8]],
        "weeks": [week_block(week0 + timedelta(weeks=i)) for i in range(COACH_WEEKS)],
        "last_weeks_planned_vs_done": compliance,
        "open_proposals": [{"kind": p["kind"], "reason": p["reason"]} for p in proposals],
        "kinds_by_sport": KINDS_BY_SPORT,
    }


def coach_changes(
    changes: list[dict], rows: list[PlanDay], today: date, rates: dict,
) -> tuple[list[dict], list[str]]:
    """Turn the coach's plan_changes into proposal changes ({day, slot, before, after}) plus the ones skipped and why.

    Only today up to the end of next week can change; locked sessions are left alone; kind "rest" clears the slot.
    A change without a slot targets the day's main session (slot 0). The last change for a slot wins.
    """
    last = (monday(today) + timedelta(weeks=COACH_WEEKS) - timedelta(days=1)).isoformat()
    current = {(p.day, p.slot): p for p in rows}
    out: dict[tuple[str, int], dict] = {}
    skipped = []
    for c in changes:
        day, slot = str(c.get("day") or ""), c.get("slot") or 0
        sport, kind = c.get("sport") or "", c.get("kind") or ""
        try:
            _d(day)
        except ValueError:
            skipped.append(f"{day or 'A change'}: not a date.")
            continue
        if not (today.isoformat() <= day <= last):
            skipped.append(f"{day}: only today up to the end of next week can change.")
            continue
        if slot not in (0, 1):
            skipped.append(f"{day}: there are at most two sessions a day.")
            continue
        old = current.get((day, slot))
        if old and old.locked:
            skipped.append(f"{day}: that session is locked.")
            continue
        before = old if old and old.kind != "rest" else None
        if kind == "rest" or sport == "rest":
            if before is None:
                continue  # already free
            out[(day, slot)] = _change(day, slot, before, None)
            continue
        minutes = int(round(float(c.get("minutes") or 0)))
        problem = valid_session(sport, kind, minutes)
        if problem:
            skipped.append(f"{day}: {problem}")
            continue
        if before and (before.sport, before.kind, before.minutes) == (sport, kind, minutes):
            continue
        after = PlanDay(day, slot, sport, kind, minutes, planned_load(sport, kind, minutes, rates))
        change = _change(day, slot, before, after)
        note = str(c.get("note") or "").strip()[:COACH_NOTE_MAX]
        if note:
            change["after"]["note"] = note
        out[(day, slot)] = change
    return sorted(out.values(), key=lambda c: (c["day"], c["slot"])), skipped


# ------------------------------------------------------------------ today


def today_from_plan(sessions: list[PlanDay], readiness: Readiness, intensity: list[dict], today: date) -> dict | None:
    """Today's planned session and what it becomes after today's readiness check.

    Red with heavy fatigue -> rest; red -> recovery (mobility for gym); amber -> hard sessions become easy.
    """
    main = sorted((p for p in sessions if p.sport != "rest" or p.kind == "rest"), key=lambda p: (p.kind == "rest", p.slot))
    main = [p for p in main if p.kind != "rest"] or main
    if not main:
        return None
    p = main[0]
    planned = {"sport": p.sport, "kind": p.kind, "minutes": p.minutes, "label": describe(p.sport, p.kind, p.minutes)}
    out = {"planned": planned, "sport": p.sport, "kind": p.kind, "minutes": p.minutes, "changed": False, "reason": None,
           "others": [{"sport": o.sport, "kind": o.kind, "minutes": o.minutes, "label": describe(o.sport, o.kind, o.minutes)}
                      for o in main[1:]]}
    if p.kind in ("rest", "event"):
        return out
    if needs_rest(readiness, intensity, today):
        out.update(kind="rest", changed=True,
                   reason="Recovery signals are poor and fatigue has built up, so today becomes a rest day.")
    elif readiness.level == "red":
        if p.sport == gym.KEY:
            out.update(kind="mobility", minutes=min(p.minutes, gym.RED_CAP_MINUTES), changed=p.kind != "mobility")
        else:
            out.update(kind="recovery", minutes=min(p.minutes, SPORTS[p.sport].red_cap_minutes), changed=True)
        out["reason"] = "Readiness is red, so today's session is an easy recovery one." if out["changed"] else None
    elif readiness.level == "amber" and p.kind in HARD_KINDS:
        out.update(kind="endurance", changed=True,
                   reason=f"Readiness is amber, so the {KIND_LABEL[p.kind].lower()} work waits and today stays aerobic.")
    return out


def plan_suggestion(
    today_plan: dict, readiness: Readiness, intensity: list[dict], trained: dict, today: date,
    level: str, gym_goal: str = gym.DEFAULT_GOAL, minutes: int | None = None,
) -> Suggestion | gym.GymSuggestion:
    """The concrete workout (steps or exercises) for today's plan session. `minutes` overrides its length."""
    sport, kind = today_plan["sport"], today_plan["kind"]
    length = minutes or today_plan["minutes"]
    why = [readiness.headline, f"Today's plan: {today_plan['planned']['label']}."]
    if today_plan.get("reason"):
        why.append(today_plan["reason"])
    if sport == gym.KEY:
        if kind == "rest":
            return gym.GymSuggestion(gym.REST_SESSION, [], why, 0, level, gym_goal, trained)
        s = gym.suggest(readiness, length, intensity, trained, today, gym_goal, level, kind)
        s.rationale = why + s.rationale[1:]
        return s
    if kind == "rest":
        return Suggestion(REST, [], why, sport, level)
    if kind == "event":
        event = Workout("event", "Event day", "The day you've been training for.", note="Eat, drink and pace yourself. Enjoy it.")
        return Suggestion(event, [], why, sport, level)
    profile = SPORTS[sport]
    key = "endurance" if kind == "long" else kind if kind in profile.library else "tempo"
    workout = _scaled(profile.library[key], level)
    if kind == "long":
        workout = replace(workout, key="long", name=LONG_NAME[sport],
                          purpose="Endurance for long days: steady, conversational and fuelled.")
    return Suggestion(workout, build(workout, length, profile), why, sport, level)


def to_dict(obj) -> dict:
    return asdict(obj)
