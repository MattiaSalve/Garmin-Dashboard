"""Home page: today's readiness and workout, recovery trends, the current week, and records."""

from collections.abc import Callable
from datetime import date, timedelta

import altair as alt
import pandas as pd
import streamlit as st

from garmin_dashboards import coach
from garmin_dashboards.db import all_settings
from garmin_dashboards.fitness import fitness_series
from garmin_dashboards.metrics import (
    SPORT_GROUPS,
    daily_intensity,
    daily_training_load,
    daily_wellness,
    distance_by_group,
    estimated_ftp_trend,
    longest_activity,
    power_prs,
    search_activities,
    week_progress,
)
from garmin_dashboards.readiness import BAD, CAUTION, GOOD, Readiness, assess
from garmin_dashboards.workouts import Suggestion, suggest

# Same five colours as the heart-rate zone chart in main.py, so intensity reads the same everywhere.
ZONE_COLORS = ["#90caf9", "#66bb6a", "#fdd835", "#fb8c00", "#e53935"]
POWER_ZONE_UPPER = [0.55, 0.75, 0.90, 1.05]  # %FTP upper bounds of zones 1-4 (Coggan, merged 5-7)
HRV_COLOR, RHR_COLOR, SLEEP_COLOR, LOAD_COLOR = "#1e88e5", "#e53935", "#8e24aa", "#bdbdbd"
LEVEL_BOX = {"green": st.success, "amber": st.warning, "red": st.error}
STATUS_DOT = {GOOD: ":green[●]", CAUTION: ":orange[●]", BAD: ":red[●]"}
RAMP_WARNING = 7  # fitness points gained in 7 days
TIME_OPTIONS = [30, 45, 60, 75, 90, 120, 150, 180]


def _power_zone(fraction: float) -> int:
    return next((i for i, upper in enumerate(POWER_ZONE_UPPER) if fraction <= upper), 4)


def _load_by_day() -> dict[date, float]:
    return {date.fromisoformat(r["day"]): r["load"] for r in daily_training_load()}


def show_home(pick_ride: Callable[[list[dict], str], None]) -> None:
    today = date.today()
    settings = all_settings()
    wellness = daily_wellness(today - timedelta(days=90), today)
    intensity = daily_intensity(today - timedelta(days=30), today)
    series = fitness_series(_load_by_day(), end=today)
    # Form at the start of today (yesterday's value), so a ride done today doesn't change the verdict.
    form = series[-2]["form"] if len(series) >= 2 else None

    readiness = assess(wellness, intensity, form, settings, today)
    show_today(readiness, wellness, intensity, form, settings, today)
    st.divider()
    show_recovery_trends(wellness, today)
    st.divider()
    show_week(series, today, settings, pick_ride)
    st.divider()
    show_records(settings, today)


# ---------------------------------------------------------------- today


def show_today(readiness: Readiness, wellness, intensity, form, settings, today: date) -> None:
    st.subheader(f"Today · {today:%A %d %B}")
    if not wellness:
        st.info("No recovery data yet. Press **Sync from Garmin** in the sidebar to download HRV, sleep and resting HR.")
    LEVEL_BOX[readiness.level](f"**Readiness {readiness.score}/100** · {readiness.headline}")

    today_row = next((r for r in wellness if r["day"] == today.isoformat()), {})
    rng = readiness.hrv_range
    t1, t2, t3, t4, t5 = st.columns(5)
    t1.metric(
        "HRV", f"{today_row['hrv_avg']:.0f} ms" if today_row.get("hrv_avg") else "–",
        f"range {rng[0]:.0f}–{rng[1]:.0f}" if rng else None, delta_color="off",
    )
    t2.metric("Resting HR", f"{today_row['rhr']:.0f} bpm" if today_row.get("rhr") else "–")
    t3.metric(
        "Sleep", f"{today_row['sleep_score']:.0f}" if today_row.get("sleep_score") else "–",
        f"{today_row['sleep_s'] / 3600:.1f} h" if today_row.get("sleep_s") else None, delta_color="off",
    )
    t4.metric("Body Battery", f"{today_row['bb_high']:.0f}" if today_row.get("bb_high") else "–",
              help="Highest Body Battery level today.")
    t5.metric("Form", f"{form:+.0f}" if form is not None else "–", help="Fitness minus fatigue. Negative means tired.")

    with st.expander("Why this verdict"):
        for s in readiness.signals:
            dot = STATUS_DOT.get(s.status, ":gray[●]")
            st.markdown(f"{dot} **{s.name}** · {s.value} · {s.explanation}")
        st.caption(
            "Each caution costs 15 points and each bad signal 35. "
            "75+ is green, 45–74 amber, below 45 red. Set your own HRV range in the sidebar settings."
        )

    minutes = st.select_slider("Time available (min)", TIME_OPTIONS, value=60)
    suggestion = suggest(readiness, minutes, intensity, today)
    ftp = settings.get("ftp_w") or 250
    show_workout(suggestion, ftp)
    show_coach(readiness, suggestion, wellness, intensity, form, ftp, today, minutes)


def _steps_frame(steps: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(steps)
    df["end"] = df["minutes"].cumsum()
    df["start"] = df["end"] - df["minutes"]
    df["mid"] = (df["watts_low"] + df["watts_high"]) / 2
    return df


def _workout_chart(df: pd.DataFrame, ftp: float) -> alt.Chart:
    df = df.assign(zone=[f"Zone {_power_zone(m / ftp) + 1}" for m in df["mid"]])
    zones = [f"Zone {i}" for i in range(1, 6)]
    blocks = (
        alt.Chart(df)
        .mark_rect(cornerRadiusTopLeft=2, cornerRadiusTopRight=2)
        .encode(
            x=alt.X("start:Q", title="Minutes", scale=alt.Scale(nice=False)),
            x2="end:Q",
            y=alt.Y("watts_high:Q", title="Watts", scale=alt.Scale(domainMin=0)),
            y2=alt.datum(0),
            color=alt.Color("zone:N", scale=alt.Scale(domain=zones, range=ZONE_COLORS), legend=None),
            tooltip=[
                alt.Tooltip("label:N", title="Step"),
                alt.Tooltip("minutes:Q", title="Minutes", format=".0f"),
                alt.Tooltip("watts_low:Q", title="From (W)"),
                alt.Tooltip("watts_high:Q", title="To (W)"),
                alt.Tooltip("pct_ftp:N", title="% FTP"),
            ],
        )
    )
    ftp_rule = (
        alt.Chart(pd.DataFrame({"ftp": [ftp]}))
        .mark_rule(strokeDash=[4, 3], color="#616161")
        .encode(y="ftp:Q")
    )
    ftp_label = ftp_rule.mark_text(align="left", dx=4, dy=-6, color="#616161", text=f"FTP {ftp:.0f} W").encode(
        x=alt.datum(0)
    )
    return alt.layer(blocks, ftp_rule, ftp_label).properties(height=200)


def show_workout(suggestion: Suggestion, ftp: float) -> None:
    w = suggestion.workout
    st.markdown(f"#### {w.name} · {suggestion.minutes} min")
    st.caption(" ".join([w.purpose, *suggestion.rationale[1:]]))
    df = _steps_frame(suggestion.in_watts(ftp))
    st.altair_chart(_workout_chart(df, ftp), width="stretch")
    table = pd.DataFrame({
        "Step": df["label"],
        "Min": df["minutes"].round(0).astype(int),
        "Watts": [f"{lo}–{hi}" for lo, hi in zip(df["watts_low"], df["watts_high"])],
        "% FTP": df["pct_ftp"],
    })
    with st.expander("Steps"):
        st.dataframe(table, hide_index=True, width="stretch")
    if w.note:
        st.caption(f"Note: {w.note}")


def show_coach(readiness, suggestion, wellness, intensity, form, ftp, today, minutes) -> None:
    if not coach.is_available():
        st.caption(
            "Set `OPENROUTER_API_KEY` and restart to get an AI coach's take on today's plan."
        )
        return
    options = [*dict.fromkeys([coach.DEFAULT_MODEL, *coach.MODELS]), "Other…"]
    model = st.selectbox("Coach model", options, key="coach_model", help="Any model available on OpenRouter.")
    if model == "Other…":
        model = st.text_input("OpenRouter model id", placeholder="provider/model-name", key="coach_model_custom").strip()
        if not model:
            st.caption("Enter a model id from openrouter.ai/models.")
            return
    key = f"coach_{today}_{minutes}_{suggestion.workout.key}_{model}"
    if st.button("Ask coach", help="Sends the numbers above (no names, places or account details) to OpenRouter."):
        payload = coach.build_payload(readiness, suggestion, wellness, intensity, form, ftp)
        try:
            with st.spinner("Asking the coach..."):
                st.session_state[key] = coach.ask_coach(payload, model=model)
        except coach.CoachError as exc:
            st.error(str(exc))
    advice = st.session_state.get(key)
    if advice is None:
        st.caption("Sends a summary of the last 7 days of recovery and training numbers to OpenRouter.")
        return
    with st.container(border=True):
        st.markdown(f"**Coach** · {'agrees with' if advice.agree_with_suggestion else 'suggests changing'} the plan")
        st.write(advice.summary)
        for c in advice.cautions:
            st.markdown(f"- {c}")
        if advice.adjusted_steps:
            st.dataframe(
                pd.DataFrame([
                    {"Step": s.label, "Min": round(s.minutes),
                     "Watts": f"{round(s.pct_ftp_low * ftp / 100)}–{round(s.pct_ftp_high * ftp / 100)}",
                     "% FTP": f"{s.pct_ftp_low}–{s.pct_ftp_high}%"}
                    for s in advice.adjusted_steps
                ]),
                hide_index=True, width="stretch",
            )


# ---------------------------------------------------------------- recovery trends


def show_recovery_trends(wellness: list[dict], today: date) -> None:
    st.subheader("Recovery trends")
    days = st.segmented_control("Range", [30, 90], default=30, format_func=lambda d: f"{d} days", key="trend_days")
    days = days or 30
    since = (today - timedelta(days=days - 1)).isoformat()
    df = pd.DataFrame([r for r in wellness if r["day"] >= since])
    if df.empty or df["hrv_avg"].isna().all():
        st.info("No HRV data in this range yet.")
        return
    df["day"] = pd.to_datetime(df["day"])
    loads = pd.DataFrame(daily_intensity(today - timedelta(days=days - 1), today))
    x = alt.X("day:T", title=None, scale=alt.Scale(domain=[since, today.isoformat()]))

    band = (
        alt.Chart(df.dropna(subset=["hrv_baseline_low"]))
        .mark_area(color=HRV_COLOR, opacity=0.12)
        .encode(x=x, y="hrv_baseline_low:Q", y2="hrv_baseline_high:Q")
    )
    hrv = (
        alt.Chart(df.dropna(subset=["hrv_avg"]))
        .mark_line(color=HRV_COLOR, point=alt.OverlayMarkDef(size=20, color=HRV_COLOR))
        .encode(
            x=x,
            y=alt.Y("hrv_avg:Q", title="Overnight HRV (ms)", scale=alt.Scale(zero=False)),
            tooltip=[alt.Tooltip("day:T", title="Date"), alt.Tooltip("hrv_avg:Q", title="HRV (ms)"),
                     alt.Tooltip("hrv_status:N", title="Garmin status")],
        )
    )
    top = alt.layer(band, hrv).properties(height=220, title="HRV · shaded band is your Garmin baseline")
    if loads.empty:
        st.altair_chart(top, width="stretch")
    else:
        loads["day"] = pd.to_datetime(loads["day"])
        bars = (
            alt.Chart(loads)
            .mark_bar(color=LOAD_COLOR)
            .encode(
                x=x,
                y=alt.Y("load:Q", title="Load"),
                tooltip=[alt.Tooltip("day:T", title="Date"), alt.Tooltip("load:Q", title="Training load", format=".0f")],
            )
            .properties(height=80)
        )
        st.altair_chart(alt.vconcat(top, bars).resolve_scale(x="shared"), width="stretch")

    c1, c2 = st.columns(2)
    c1.altair_chart(_small_line(df, "rhr", "Resting HR (bpm)", RHR_COLOR, x), width="stretch")
    c2.altair_chart(_small_line(df, "sleep_score", "Sleep score", SLEEP_COLOR, x), width="stretch")


def _small_line(df: pd.DataFrame, col: str, title: str, color: str, x: alt.X) -> alt.Chart:
    return (
        alt.Chart(df.dropna(subset=[col]))
        .mark_line(color=color, point=alt.OverlayMarkDef(size=15, color=color))
        .encode(
            x=x,
            y=alt.Y(f"{col}:Q", title=None, scale=alt.Scale(zero=False)),
            tooltip=[alt.Tooltip("day:T", title="Date"), alt.Tooltip(f"{col}:Q", title=title, format=".0f")],
        )
        .properties(height=160, title=title)
    )


# ---------------------------------------------------------------- week at a glance


def show_week(series: list[dict], today: date, settings: dict, pick_ride) -> None:
    w = week_progress(today)
    st.subheader(f"This week · since {w['week_start']:%a %d %b}")
    target = settings.get("weekly_hours_target")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Hours", f"{w['hours']:.1f}", f"{w['hours'] - w['avg_hours']:+.1f} vs 4-wk avg", delta_color="off")
    c2.metric("Distance", f"{w['distance_km']:,.0f} km")
    c3.metric("Load", f"{w['load']:,.0f}", f"{w['load'] - w['avg_load']:+,.0f} vs 4-wk avg", delta_color="off")
    c4.metric("Activities", w["activity_count"])
    if target:
        st.progress(min(w["hours"] / target, 1.0), text=f"{w['hours']:.1f} of {target:.0f} h weekly target")

    split = pd.DataFrame({
        "intensity": ["Easy (Z1–2)", "Moderate (Z3)", "Hard (Z4–5)"],
        "hours": [w["easy_h"], w["moderate_h"], w["hard_h"]],
    })
    if split["hours"].sum() > 0:
        split["share"] = split["hours"] / split["hours"].sum()
        chart = (
            alt.Chart(split)
            .mark_bar(height=22)
            .encode(
                x=alt.X("hours:Q", stack="zero", title="Hours by heart-rate zone"),
                color=alt.Color(
                    "intensity:N", title=None, sort=list(split["intensity"]),
                    scale=alt.Scale(domain=list(split["intensity"]),
                                    range=[ZONE_COLORS[1], ZONE_COLORS[2], ZONE_COLORS[4]]),
                    legend=alt.Legend(orient="bottom"),
                ),
                order=alt.Order("order:Q"),
                tooltip=["intensity:N", alt.Tooltip("hours:Q", format=".1f"), alt.Tooltip("share:Q", format=".0%")],
            )
            .transform_calculate(order="indexof(['Easy (Z1–2)', 'Moderate (Z3)', 'Hard (Z4–5)'], datum.intensity)")
            .properties(height=60)
        )
        st.altair_chart(chart, width="stretch")

    if len(series) > 7:
        ramp = series[-1]["fitness"] - series[-8]["fitness"]
        if ramp > RAMP_WARNING:
            st.warning(f"Fitness rose {ramp:.1f} points in 7 days. Ramping this fast raises injury and illness risk.")
        else:
            st.caption(f"Ramp rate: fitness {ramp:+.1f} in the last 7 days (keep it under {RAMP_WARNING}).")

    pick_ride(search_activities(start=w["week_start"], end=today), key="home_week")


# ---------------------------------------------------------------- records and milestones


def _fmt_duration(seconds: int) -> str:
    return f"{seconds}s" if seconds < 60 else f"{seconds // 60} min" if seconds < 3600 else f"{seconds // 3600} h"


def show_records(settings: dict, today: date) -> None:
    st.subheader("Records and milestones")

    prs = power_prs(today)
    if prs:
        st.markdown("**New power bests in the last 4 weeks**")
        cols = st.columns(min(len(prs), 4))
        for i, pr in enumerate(prs[:8]):
            delta = f"+{pr['watts'] - pr['previous_watts']:.0f} W" if pr["previous_watts"] else "first"
            cols[i % len(cols)].metric(_fmt_duration(pr["seconds"]), f"{pr['watts']:.0f} W", delta)
    else:
        st.caption("No new power bests in the last 4 weeks.")

    year_start = date(today.year, 1, 1)
    km = distance_by_group(year_start, today)["Cycling"]
    goal = settings.get("yearly_km_goal")
    longest = longest_activity(year_start, today, SPORT_GROUPS["Cycling"])
    c1, c2 = st.columns(2)
    if goal:
        expected = goal * (today - year_start).days / ((date(today.year, 12, 31) - year_start).days + 1)
        c1.metric(f"Cycling {today.year}", f"{km:,.0f} km", f"{km - expected:+,.0f} km vs pace", delta_color="normal")
        c1.progress(min(km / goal, 1.0), text=f"{km / goal:.0%} of {goal:,.0f} km")
    else:
        c1.metric(f"Cycling {today.year}", f"{km:,.0f} km", help="Set a yearly goal in the sidebar settings.")
    if longest and longest["distance_m"]:
        c2.metric("Longest ride this year", f"{longest['distance_m'] / 1000:,.0f} km",
                  f"{longest['activity_name']} · {longest['day']}", delta_color="off")

    trend = pd.DataFrame(estimated_ftp_trend())
    if len(trend) >= 2:
        trend = trend.tail(18)
        ftp = settings.get("ftp_w")
        line = (
            alt.Chart(trend)
            .mark_line(color=ZONE_COLORS[3], point=alt.OverlayMarkDef(color=ZONE_COLORS[3]))
            .encode(
                x=alt.X("month:O", title=None),
                y=alt.Y("ftp_estimate:Q", title="Watts", scale=alt.Scale(zero=False)),
                tooltip=["month:O", alt.Tooltip("ftp_estimate:Q", title="Estimate (W)", format=".0f")],
            )
        )
        layers = [line]
        if ftp:
            rule = alt.Chart(pd.DataFrame({"ftp": [ftp]})).mark_rule(strokeDash=[4, 3], color="#616161").encode(y="ftp:Q")
            layers += [rule, rule.mark_text(align="left", dx=4, dy=-6, color="#616161", text=f"Your FTP {ftp:.0f} W")
                       .encode(x=alt.datum(trend["month"].iloc[0]))]
        st.altair_chart(
            alt.layer(*layers).properties(height=200, title="Estimated FTP · 95% of best 20-min power per month"),
            width="stretch",
        )
        st.caption("Months without a hard 20-minute effort underestimate FTP. Your FTP setting is never changed automatically.")
