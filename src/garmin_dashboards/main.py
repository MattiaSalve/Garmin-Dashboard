"""Streamlit dashboard: sign in to Garmin, sync activities, and explore them.

Run from the project root:
    .venv/bin/streamlit run src/garmin_dashboards/main.py
"""

import sqlite3
from datetime import date, timedelta

import altair as alt
import pandas as pd
import streamlit as st
from garminconnect.activity_details import parse_activity_detail_metrics

from garmin_dashboards import auth
from garmin_dashboards.db import DB_NAME, init_db, save_activities
from garmin_dashboards.metrics import by_sport, summary, weekly_totals


PAGE_SIZE = 100
DEFAULT_RANGE_DAYS = 365

# (title, unit, candidate Garmin keys in priority order, factor to convert to the unit)
RIDE_CHANNELS = [
    ("Heart rate", "bpm", ["directHeartRate"], 1.0),
    ("Power", "W", ["directPower"], 1.0),
    ("Speed", "km/h", ["directSpeed"], 3.6),
    ("Elevation", "m", ["directElevation"], 1.0),
    ("Cadence", "rpm", ["directBikeCadence", "directRunCadence", "directDoubleCadence"], 1.0),
]


CHANNEL_COLORS = {
    "Heart rate": "#e53935",  # red
    "Power": "#fb8c00",       # orange (not specified; change if you prefer another)
    "Speed": "#1e88e5",       # blue
    "Elevation": "#9e9e9e",   # grey
    "Cadence": "#8e24aa",     # purple
}
ELEVATION_BACKGROUND_FOR = ("Heart rate", "Power", "Speed")
TRAILING_SPEED_WINDOW = None  # None = running average from the start of the ride; or e.g. "30s"
CHART_ORDER = ("Heart rate", "Power", "Speed", "Elevation", "Cadence")


def fmt_seconds_hm(seconds: float | None) -> str | None:
    """Format seconds as h:mm, e.g. 4500 -> '1:15'."""
    if seconds is None or pd.isna(seconds):
        return None
    minutes = round(seconds / 60)
    return f"{minutes // 60}:{minutes % 60:02d}"


def fetch_all_activities(api) -> list[dict]:
    """Page through the Garmin activity list until it runs out."""
    activities: list[dict] = []
    start = 0
    while True:
        batch = api.get_activities(start, PAGE_SIZE)
        if not batch:
            break
        activities.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        start += PAGE_SIZE
    return activities


def sign_in_form() -> None:
    with st.form("sign_in"):
        email = st.text_input("Garmin email")
        password = st.text_input("Garmin password", type="password")
        remember = st.checkbox("Remember me on this computer")
        submitted = st.form_submit_button("Sign in")

    if not submitted:
        return
    if not email or not password:
        st.error("Enter your email and password.")
        return
    try:
        with st.spinner("Signing in to Garmin..."):
            api = auth.login(email, password, remember=remember)
    except Exception as exc:  # the garminconnect library raises several error types
        st.error(f"Sign-in failed: {exc}")
        return
    st.session_state.api = api
    st.session_state.email = email
    st.rerun()


def try_restore_session() -> None:
    """Sign in with a remembered session, once per browser session."""
    if st.session_state.get("restore_tried"):
        return
    st.session_state.restore_tried = True
    try:
        restored = auth.restore()
    except Exception:
        auth.forget()
        st.warning("Your remembered Garmin sign-in has expired. Please sign in again.")
        return
    if restored is not None:
        st.session_state.api, st.session_state.email = restored
        st.rerun()


def display_sport(key: str | None) -> str | None:
    """Turn a Garmin sport key like 'road_biking' into 'Road biking'."""
    return key.replace("_", " ").capitalize() if key else key


def sport_options() -> list[str]:
    with sqlite3.connect(DB_NAME) as conn:
        rows = conn.execute(
            "SELECT DISTINCT sport_type FROM activities "
            "WHERE sport_type IS NOT NULL ORDER BY sport_type"
        ).fetchall()
    return [r[0] for r in rows]


def activities_table(start: date, end: date, sport: str | None) -> pd.DataFrame:
    with sqlite3.connect(DB_NAME) as conn:
        df = pd.read_sql_query(
            """
            SELECT activity_id, start_time_local, activity_name, sport_type, distance_m,
                   moving_duration_s, elevation_gain_m, avg_hr, max_hr, avg_power_w,
                   calories, training_stress_score
            FROM activities
            WHERE date(start_time_local) BETWEEN ? AND ?
              AND (? IS NULL OR sport_type = ?)
            ORDER BY start_time_local DESC
            """,
            conn,
            params=(start.isoformat(), end.isoformat(), sport, sport),
        )
    moving_h = df["moving_duration_s"] / 3600
    df["sport_type"] = df["sport_type"].map(display_sport)
    df["distance_km"] = (df["distance_m"] / 1000).round(2)
    df["moving_time"] = df["moving_duration_s"].map(fmt_seconds_hm)
    df["avg_speed_kmh"] = (df["distance_m"] / 1000 / moving_h).round(1)
    df = df.drop(columns=["distance_m", "moving_duration_s"])
    df = df.rename(columns={
        "start_time_local": "Start",
        "activity_name": "Name",
        "sport_type": "Sport",
        "distance_km": "Distance (km)",
        "moving_time": "Moving time (h:mm)",
        "avg_speed_kmh": "Avg speed (km/h)",
        "elevation_gain_m": "Elevation gain (m)",
        "avg_hr": "Avg HR (bpm)",
        "max_hr": "Max HR (bpm)",
        "avg_power_w": "Avg power (W)",
        "calories": "Calories (kcal)",
        "training_stress_score": "Training stress",
    })
    return df.set_index("activity_id")


def ride_details(activity_id: int) -> dict:
    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM activities WHERE activity_id = ?", (activity_id,)
        ).fetchone()
    return dict(row) if row else {}


def selected_week(event) -> str | None:
    """Return the week_start picked on the weekly chart, or None.

    Streamlit may return the Vega-Lite selection as a dict of field -> values,
    or as a list of selected points; handle both.
    """
    sel = event.selection.get("week") if event is not None else None
    if not sel:
        return None
    if isinstance(sel, dict):
        value = sel.get("week_start")
        if value:
            return value[0] if isinstance(value, list) else value
        sel = sel.get("points") or []
    if isinstance(sel, list) and sel and isinstance(sel[0], dict):
        return sel[0].get("week_start")
    return None


def _fmt(value, template: str) -> str:
    return "–" if value is None else template.format(value)


def show_ride_details(activity_id: int) -> None:
    r = ride_details(activity_id)
    if not r:
        return
    moving_s = r["moving_duration_s"] or 0
    speed = (r["distance_m"] / 1000 / (moving_s / 3600)) if moving_s else None

    st.subheader(f"{r['activity_name']} · {display_sport(r['sport_type'])}")
    place = f" · {r['location_name']}" if r["location_name"] else ""
    st.caption(f"Started {r['start_time_local']}{place}")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Distance", _fmt(r["distance_m"] and r["distance_m"] / 1000, "{:,.2f} km"))
    c2.metric("Moving time", fmt_seconds_hm(r["moving_duration_s"]) or "–")
    c3.metric("Avg speed", _fmt(speed, "{:,.1f} km/h"))
    c4.metric("Max speed", _fmt(r["max_speed_mps"] and r["max_speed_mps"] * 3.6, "{:,.1f} km/h"))

    c5, c6, c7, c8 = st.columns(4)
    c5.metric("Elevation gain", _fmt(r["elevation_gain_m"], "{:,.0f} m"))
    c6.metric("Elevation loss", _fmt(r["elevation_loss_m"], "{:,.0f} m"))
    c7.metric("Avg heart rate", _fmt(r["avg_hr"], "{:,.0f} bpm"))
    c8.metric("Max heart rate", _fmt(r["max_hr"], "{:,.0f} bpm"))

    c9, c10, c11, c12 = st.columns(4)
    c9.metric("Avg power", _fmt(r["avg_power_w"], "{:,.0f} W"))
    c10.metric("Normalized power", _fmt(r["norm_power_w"], "{:,.0f} W"))
    c11.metric("Max power", _fmt(r["max_power_w"], "{:,.0f} W"))
    c12.metric("Calories", _fmt(r["calories"], "{:,.0f} kcal"))

    c13, c14, c15, c16 = st.columns(4)
    c13.metric("Training stress", _fmt(r["training_stress_score"], "{:,.0f}"))
    c14.metric("Aerobic effect", _fmt(r["aerobic_te"], "{:,.1f}"))
    c15.metric("Anaerobic effect", _fmt(r["anaerobic_te"], "{:,.1f}"))
    c16.metric("VO2max", _fmt(r["vo2max"], "{:,.0f}"))

    show_ride_charts(activity_id)


@st.cache_data(show_spinner="Loading second-by-second data...")
def load_activity_samples(_api, activity_id: int) -> pd.DataFrame:
    """Per-second samples for one ride. The API object is not hashed (leading underscore)."""
    details = _api.get_activity_details(str(activity_id))
    return pd.DataFrame(parse_activity_detail_metrics(details))


def _area(data: pd.DataFrame, color: str, y_title: str) -> alt.Chart:
    return (
        alt.Chart(data)
        .mark_area(opacity=0.7, color=color)
        .encode(
            x=alt.X("minutes:Q", title="Moving time (min)"),
            y=alt.Y("value:Q", title=y_title),
        )
    )


def trailing_average(data: pd.DataFrame, window: str | None) -> pd.DataFrame:
    """Running mean of `value` from the start (window=None) or over a time window, keeping `minutes`."""
    series = pd.Series(
        data["value"].to_numpy(),
        index=pd.to_timedelta(data["minutes"].to_numpy() * 60, unit="s"),
    ).sort_index()
    averaged = series.expanding().mean() if window is None else series.rolling(window).mean()
    return pd.DataFrame({
        "minutes": averaged.index.total_seconds() / 60,
        "value": averaged.to_numpy(),
    }).dropna()


def show_ride_charts(activity_id: int) -> None:
    try:
        samples = load_activity_samples(st.session_state.api, activity_id)
    except Exception as exc:
        st.warning(f"Couldn't load second-by-second data: {exc}")
        return
    if samples.empty:
        st.caption("No second-by-second data for this ride.")
        return

    time_col = next(
        (c for c in ("sumMovingDuration", "sumDuration") if c in samples and samples[c].notna().any()),
        None,
    )
    if time_col is None:
        st.caption("This ride has no timing data to plot against.")
        return
    minutes = samples[time_col] / 60

    # Collect each channel that has data, as (unit, values) keyed by its title.
    series = {}
    for title, unit, keys, factor in RIDE_CHANNELS:
        key = next((k for k in keys if k in samples and samples[k].notna().any()), None)
        if key is not None:
            series[title] = (unit, samples[key] * factor)
    if not series:
        st.caption("This ride has no heart rate, power, speed, elevation, or cadence data.")
        return

    def frame(title: str) -> pd.DataFrame:
        return pd.DataFrame({"minutes": minutes, "value": series[title][1]}).dropna()

    elevation_frame = frame("Elevation") if "Elevation" in series else None

    charts = []
    for title in CHART_ORDER:
        if title not in series:
            continue
        unit = series[title][0]
        base = _area(frame(title), CHANNEL_COLORS[title], unit)
        if title == "Speed":
            trailing = trailing_average(frame(title), TRAILING_SPEED_WINDOW)
            line = (
                alt.Chart(trailing)
                .mark_line(color=CHANNEL_COLORS[title], opacity=1)
                .encode(
                    x=alt.X("minutes:Q", title="Moving time (min)"),
                    y=alt.Y("value:Q", title=unit),
                )
            )
            base = alt.layer(base, line)
        if title == "Elevation":
            chart = base
        elif title in ELEVATION_BACKGROUND_FOR and elevation_frame is not None:
            # Strava-style: elevation as a grey filled background on its own (right) axis.
            background = (
                alt.Chart(elevation_frame)
                .mark_area(opacity=0.3, color=CHANNEL_COLORS["Elevation"])
                .encode(
                    x=alt.X("minutes:Q", title="Moving time (min)"),
                    y=alt.Y("value:Q", title="Elevation (m)", axis=alt.Axis(orient="right")),
                )
            )
            chart = alt.layer(background, base).resolve_scale(y="independent")
        else:
            chart = base
        charts.append(chart.properties(title=f"{title} ({unit})", height=340))

    st.subheader("Ride charts")
    for chart in charts:
        st.altair_chart(chart, width="stretch")


def sidebar(email: str) -> None:
    with st.sidebar:
        st.write(f"Signed in as **{email}**")

        if st.button("Sync from Garmin"):
            try:
                with st.spinner("Downloading activities..."):
                    activities = fetch_all_activities(st.session_state.api)
                    save_activities(activities, email)
                st.success(f"Saved {len(activities)} activities.")
            except Exception as exc:
                st.error(f"Sync failed: {exc}")

        st.caption("Signing out also forgets this computer's saved sign-in.")
        if st.button("Sign out"):
            auth.forget(st.session_state.api)
            st.session_state.pop("api", None)
            st.session_state.pop("email", None)
            st.rerun()


def show_dashboard(start: date, end: date, sport: str | None) -> None:
    s = summary(start, end, sport)
    if s["activity_count"] == 0:
        st.info("No activities in this range. Try another date range or sport, or sync from Garmin.")
        return

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Activities", f"{s['activity_count']:,}")
    c2.metric("Distance", f"{s['total_distance_km']:,.1f} km")
    c3.metric("Moving time", fmt_seconds_hm(s["total_moving_duration_h"] * 3600) or "–")
    c4.metric("Avg speed", f"{s['avg_speed_kmh']:,.1f} km/h" if s["avg_speed_kmh"] else "–")

    c5, c6, c7, c8 = st.columns(4)
    c5.metric("Elevation gain", f"{s['total_elevation_gain_m']:,.0f} m")
    c6.metric("Avg distance", f"{s['avg_distance_km']:,.1f} km")
    c7.metric("Avg heart rate", f"{s['avg_hr']:,.0f} bpm" if s["avg_hr"] else "–")
    c8.metric("Training stress", f"{s['total_training_stress']:,.0f}")

    weekly = pd.DataFrame(weekly_totals(start, end, sport))
    st.subheader("Weekly distance (km)")
    st.caption("Click a week to see its rides.")
    week_pick = alt.selection_point(name="week", fields=["week_start"])
    distance_chart = (
        alt.Chart(weekly)
        .mark_bar()
        .encode(
            x=alt.X("week_start:N", title="Week of", sort=None),
            y=alt.Y("total_distance_km:Q", title="Distance (km)"),
            opacity=alt.condition(week_pick, alt.value(1.0), alt.value(0.5)),
            tooltip=[
                alt.Tooltip("week_start:N", title="Week of"),
                alt.Tooltip("total_distance_km:Q", title="Distance (km)", format=".1f"),
                alt.Tooltip("activity_count:Q", title="Rides"),
            ],
        )
        .add_params(week_pick)
        .properties(height=300)
    )
    event = st.altair_chart(
        distance_chart,
        on_select="rerun",
        selection_mode="week",
        key="weekly_chart",
        width="stretch",
    )

    left, right = st.columns(2)
    with left:
        st.subheader("Weekly training stress")
        st.bar_chart(weekly.set_index("week_start")[["total_training_stress"]])
    with right:
        st.subheader("Time by sport (hours)")
        sports = pd.DataFrame(by_sport(start, end))
        sports["sport_type"] = sports["sport_type"].map(display_sport)
        st.bar_chart(sports.set_index("sport_type")[["total_moving_duration_h"]])

    week_start = selected_week(event)
    st.subheader("Rides")
    if week_start is None:
        st.info("Click a week in the chart above to list its rides.")
    else:
        ws = date.fromisoformat(week_start)
        rides = activities_table(ws, ws + timedelta(days=6), sport)
        st.markdown(f"**Week of {ws:%d %b %Y}** · {len(rides)} rides")
        ride_event = st.dataframe(
            rides,
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row",
            key="rides_table",
        )
        if ride_event.selection.rows:
            show_ride_details(int(rides.index[ride_event.selection.rows[0]]))
        else:
            st.caption("Select a ride in the table to see its details.")

    st.subheader("All activities in range")
    st.dataframe(activities_table(start, end, sport), hide_index=True)


def main() -> None:
    st.set_page_config(page_title="Garmin dashboard", layout="wide")
    st.title("Garmin dashboard")
    init_db()

    if "api" not in st.session_state:
        try_restore_session()
    if "api" not in st.session_state:
        sign_in_form()
        return

    email = st.session_state.email
    sidebar(email)

    today = date.today()
    picked = st.date_input(
        "Date range",
        value=(today - timedelta(days=DEFAULT_RANGE_DAYS), today),
    )
    if not isinstance(picked, tuple) or len(picked) != 2:
        st.info("Select an end date to show the dashboard.")
        return
    start, end = picked

    sport_choice = st.selectbox(
        "Sport",
        ["All"] + sport_options(),
        format_func=lambda s: "All" if s == "All" else display_sport(s),
    )
    sport = None if sport_choice == "All" else sport_choice

    show_dashboard(start, end, sport)


if __name__ == "__main__":
    main()
