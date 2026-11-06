"""Streamlit dashboard: sign in to Garmin, sync activities, and explore them.

Run from the project root:
    .venv/bin/streamlit run src/garmin_dashboards/main.py
"""

import math
import sqlite3
from datetime import date, timedelta

import altair as alt
import pandas as pd
import streamlit as st
from garminconnect.activity_details import parse_activity_detail_metrics

from garmin_dashboards import auth
from garmin_dashboards.db import DB_NAME, init_db
from garmin_dashboards.fitness import fitness_series
from garmin_dashboards.metrics import (
    SEARCH_ORDERS,
    SPORT_GROUPS,
    by_sport,
    daily_totals,
    daily_training_load,
    distance_by_group,
    filter_bounds,
    latest_vo2max,
    power_curve,
    search_activities,
    summary,
    weekly_hr_zones,
    weekly_totals,
)
from garmin_dashboards.sync import sync


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


COMPACT_COLUMNS = ["Date", "Name", "Sport", "Distance (km)", "Time (h:mm)", "Elevation (m)", "Avg HR"]


def activities_table(rows: list[dict], compact: bool = True) -> pd.DataFrame:
    """Activities as a display table indexed by activity_id."""
    df = pd.DataFrame(rows, columns=[
        "activity_id", "start_time_local", "activity_name", "sport_type", "distance_m",
        "moving_duration_s", "elevation_gain_m", "avg_hr", "max_hr", "avg_power_w",
        "calories", "training_stress_score",
    ])
    # A column that is entirely NULL arrives as object dtype and breaks round(); force numbers.
    numeric = ["distance_m", "moving_duration_s", "elevation_gain_m", "avg_hr", "max_hr",
               "avg_power_w", "calories", "training_stress_score"]
    df[numeric] = df[numeric].apply(pd.to_numeric, errors="coerce")
    out = pd.DataFrame({
        "Date": df["start_time_local"].str[:10],
        "Name": df["activity_name"],
        "Sport": df["sport_type"].map(display_sport),
        "Distance (km)": (df["distance_m"] / 1000).round(1),
        "Time (h:mm)": df["moving_duration_s"].map(fmt_seconds_hm),
        "Elevation (m)": df["elevation_gain_m"].round(0),
        "Avg speed (km/h)": (df["distance_m"] / 1000 / (df["moving_duration_s"] / 3600)).round(1),
        "Avg HR": df["avg_hr"].round(0),
        "Avg power (W)": df["avg_power_w"].round(0),
        "Calories": df["calories"].round(0),
    })
    out.index = df["activity_id"]
    return out[COMPACT_COLUMNS] if compact else out


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
    c1.metric("Distance", _fmt(r["distance_m"] and r["distance_m"] / 1000, "{:,.1f} km"))
    c2.metric("Moving time", fmt_seconds_hm(r["moving_duration_s"]) or "–")
    c3.metric("Avg speed", _fmt(speed, "{:,.1f} km/h"))
    c4.metric("Elevation gain", _fmt(r["elevation_gain_m"], "{:,.0f} m"))

    with st.expander("More stats"):
        rows = [
            ("Max speed", _fmt(r["max_speed_mps"] and r["max_speed_mps"] * 3.6, "{:,.1f} km/h")),
            ("Elevation loss", _fmt(r["elevation_loss_m"], "{:,.0f} m")),
            ("Avg / max heart rate", f"{_fmt(r['avg_hr'], '{:,.0f}')} / {_fmt(r['max_hr'], '{:,.0f}')} bpm"),
            ("Avg / norm / max power",
             f"{_fmt(r['avg_power_w'], '{:,.0f}')} / {_fmt(r['norm_power_w'], '{:,.0f}')} / "
             f"{_fmt(r['max_power_w'], '{:,.0f}')} W"),
            ("Calories", _fmt(r["calories"], "{:,.0f} kcal")),
            ("Training load", _fmt(r["training_load"], "{:,.0f}")),
            ("Training stress", _fmt(r["training_stress_score"], "{:,.0f}")),
            ("Aerobic / anaerobic effect", f"{_fmt(r['aerobic_te'], '{:,.1f}')} / {_fmt(r['anaerobic_te'], '{:,.1f}')}"),
            ("VO2max", _fmt(r["vo2max"], "{:,.0f}")),
        ]
        st.table(pd.DataFrame(rows, columns=["", ""]).set_index(""))

    show_ride_charts(activity_id)


@st.cache_data(show_spinner="Loading second-by-second data...")
def load_activity_samples(_api, activity_id: int) -> pd.DataFrame:
    """Per-second samples for one ride. The API object is not hashed (leading underscore)."""
    details = _api.get_activity_details(str(activity_id))
    return pd.DataFrame(parse_activity_detail_metrics(details))


def _x_encoding(x_title: str, domain: tuple[float, float] | None) -> alt.X:
    scale = alt.Scale(domain=list(domain), nice=False) if domain else alt.Scale(nice=False)
    return alt.X("x:Q", title=x_title, scale=scale)


def _area(data: pd.DataFrame, color: str, y_title: str, x_title: str, domain) -> alt.Chart:
    return (
        alt.Chart(data)
        .mark_area(opacity=0.7, color=color, clip=True)
        .encode(x=_x_encoding(x_title, domain), y=alt.Y("value:Q", title=y_title))
    )


def trailing_average(data: pd.DataFrame, window: str | None) -> pd.DataFrame:
    """Running mean of `value` from the start (window=None) or over a time window.

    `data` needs `minutes` and `value` columns; any other columns (e.g. `x`) are kept.
    """
    data = data.sort_values("minutes")
    if window is None:
        averaged = data["value"].expanding().mean()
    else:
        timed = pd.Series(
            data["value"].to_numpy(), index=pd.to_timedelta(data["minutes"].to_numpy() * 60, unit="s")
        )
        averaged = pd.Series(timed.rolling(window).mean().to_numpy(), index=data.index)
    return data.assign(value=averaged).dropna(subset=["value"])


def zoom_from_event(event) -> tuple[float, float] | None:
    """The x-range dragged on a chart (interval selection named 'zoom'), or None."""
    sel = event.selection.get("zoom") if event is not None else None
    values = sel.get("x") if isinstance(sel, dict) else None
    if values and len(values) == 2 and values[0] != values[1]:
        return (float(min(values)), float(max(values)))
    return None


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
    has_distance = "sumDistance" in samples and samples["sumDistance"].notna().any()

    st.subheader("Ride charts")
    by_distance = has_distance and st.radio(
        "X axis", ["Distance", "Time"], horizontal=True, key=f"xaxis_{activity_id}"
    ) == "Distance"
    if by_distance:
        x_values, x_title = samples["sumDistance"] / 1000, "Distance (km)"
    else:
        x_values, x_title = minutes, "Moving time (min)"

    # Dragging on any chart sets the zoom; it is kept per ride and axis, and cleared by the button.
    zoom_key = f"zoom_{activity_id}_{'km' if by_distance else 'min'}"
    zoom = st.session_state.get(zoom_key)
    if zoom and st.button("Reset zoom", key=f"reset_{zoom_key}"):
        st.session_state.pop(zoom_key)
        st.rerun()
    if not zoom:
        st.caption("Drag across a chart to zoom in on that part of the ride.")

    # Collect each channel that has data, as (unit, values) keyed by its title.
    series = {}
    for title, unit, keys, factor in RIDE_CHANNELS:
        key = next((k for k in keys if k in samples and samples[k].notna().any()), None)
        if key is not None:
            series[title] = (unit, samples[key] * factor)
    if not series:
        st.caption("This ride has no heart rate, power, speed, elevation, or cadence data.")
        return

    def frame(title: str, zoomed: bool = True) -> pd.DataFrame:
        df = pd.DataFrame({"x": x_values, "minutes": minutes, "value": series[title][1]}).dropna()
        if zoomed and zoom:
            df = df[df["x"].between(*zoom)]
        return df

    elevation_frame = frame("Elevation") if "Elevation" in series else None
    brush = alt.selection_interval(name="zoom", encodings=["x"])

    charts = []
    for title in CHART_ORDER:
        if title not in series:
            continue
        unit = series[title][0]
        base = _area(frame(title), CHANNEL_COLORS[title], unit, x_title, zoom).add_params(brush)
        if title == "Speed":
            # Average from the start of the ride, then cut to the zoomed range.
            trailing = trailing_average(frame(title, zoomed=False), TRAILING_SPEED_WINDOW)
            if zoom:
                trailing = trailing[trailing["x"].between(*zoom)]
            line = (
                alt.Chart(trailing)
                .mark_line(color=CHANNEL_COLORS[title], opacity=1, clip=True)
                .encode(x=_x_encoding(x_title, zoom), y=alt.Y("value:Q", title=unit))
            )
            base = alt.layer(base, line)
        if title == "Elevation":
            chart = base
        elif title in ELEVATION_BACKGROUND_FOR and elevation_frame is not None:
            # Strava-style: elevation as a grey filled background on its own (right) axis.
            background = (
                alt.Chart(elevation_frame)
                .mark_area(opacity=0.3, color=CHANNEL_COLORS["Elevation"], clip=True)
                .encode(
                    x=_x_encoding(x_title, zoom),
                    y=alt.Y("value:Q", title="Elevation (m)", axis=alt.Axis(orient="right")),
                )
            )
            chart = alt.layer(background, base).resolve_scale(y="independent")
        else:
            chart = base
        charts.append((title, chart.properties(title=f"{title} ({unit})", height=340)))

    picked = None
    for title, chart in charts:
        # The zoom is part of the key so each zoom level starts with a fresh (empty) brush.
        event = st.altair_chart(
            chart, width="stretch", on_select="rerun", selection_mode="zoom",
            key=f"ride_{activity_id}_{title}_{zoom}_{by_distance}",
        )
        picked = picked or zoom_from_event(event)
    if picked:
        st.session_state[zoom_key] = picked
        st.rerun()


def auto_sync(email: str) -> None:
    """Sync once per browser session, right after sign-in."""
    if st.session_state.get("auto_synced"):
        return
    st.session_state.auto_synced = True
    try:
        with st.spinner("Syncing new activities from Garmin..."):
            new, _ = sync(st.session_state.api, email)
        st.toast(f"{new} new activities synced." if new else "Already up to date.")
    except Exception as exc:
        st.warning(f"Automatic sync failed: {exc}")


def sidebar(email: str) -> None:
    with st.sidebar:
        st.write(f"Signed in as **{email}**")

        with st.expander("Sync options"):
            full = st.checkbox("Full re-sync", help="Re-download the entire history.")
        if st.button("Sync from Garmin"):
            try:
                with st.spinner("Downloading activities..."):
                    new, fetched = sync(st.session_state.api, email, full=full)
                st.success(f"{new} new activities ({fetched} fetched).")
            except Exception as exc:
                st.error(f"Sync failed: {exc}")

        st.caption("Signing out also forgets this computer's saved sign-in.")
        if st.button("Sign out"):
            auth.forget(st.session_state.api)
            st.session_state.pop("api", None)
            st.session_state.pop("email", None)
            st.session_state.pop("auto_synced", None)
            st.rerun()


def show_fitness_chart(start: date, end: date) -> None:
    """Fitness (CTL), fatigue (ATL) and form (TSB) from Garmin training load, all sports."""
    daily = {date.fromisoformat(r["day"]): r["load"] for r in daily_training_load()}
    series = [r for r in fitness_series(daily, end=max(end, max(daily, default=end))) if start <= r["day"] <= end]
    st.subheader("Fitness, fatigue and form")
    if not series:
        st.info("No training load data in this range.")
        return
    st.caption(
        "Built from Garmin's per-activity training load across all sports. "
        "Fitness = 42-day average, fatigue = 7-day average, form = fitness minus fatigue."
    )
    df = pd.DataFrame(series).melt(
        id_vars="day", value_vars=["fitness", "fatigue", "form"], var_name="metric", value_name="value"
    )
    colors = alt.Scale(domain=["fitness", "fatigue", "form"], range=["#1e88e5", "#e53935", "#43a047"])
    chart = (
        alt.Chart(df)
        .mark_line()
        .encode(
            x=alt.X("day:T", title=None),
            y=alt.Y("value:Q", title="Load"),
            color=alt.Color("metric:N", scale=colors, title=None),
            tooltip=[alt.Tooltip("day:T"), "metric:N", alt.Tooltip("value:Q", format=".1f")],
        )
        .properties(height=300)
    )
    st.altair_chart(chart, width="stretch")


ZONE_COLORS = ["#90caf9", "#66bb6a", "#fdd835", "#fb8c00", "#e53935"]


def show_hr_zones(start: date, end: date, sport: str | None) -> None:
    """Weekly time in heart-rate zones, plus the overall zone split for the range."""
    weekly = pd.DataFrame(weekly_hr_zones(start, end, sport))
    st.subheader("Heart-rate zones")
    if weekly.empty:
        st.info("No heart-rate zone data in this range.")
        return
    zones = [f"Zone {i}" for i in range(1, 6)]
    long = weekly.melt(id_vars="week_start", value_vars=zones, var_name="zone", value_name="hours")
    scale = alt.Scale(domain=zones, range=ZONE_COLORS)
    chart = (
        alt.Chart(long)
        .mark_bar()
        .encode(
            x=alt.X("week_start:N", title="Week of", sort=None),
            y=alt.Y("hours:Q", title="Hours"),
            color=alt.Color("zone:N", scale=scale, title=None),
            order=alt.Order("zone:N"),
            tooltip=[
                alt.Tooltip("week_start:N", title="Week of"),
                "zone:N",
                alt.Tooltip("hours:Q", format=".1f"),
            ],
        )
        .properties(height=300)
    )
    st.altair_chart(chart, width="stretch")

    totals = weekly[zones].sum()
    share = totals / totals.sum() if totals.sum() else totals
    cols = st.columns(5)
    for col, zone in zip(cols, zones):
        col.metric(zone, f"{share[zone] * 100:.0f}%", f"{totals[zone]:.1f} h", delta_color="off")
    st.caption("Zones as defined on your Garmin device. Rides without heart-rate data are skipped.")


def show_power_curve(start: date, end: date, sport: str | None) -> None:
    """Best average power for each duration over the selected range."""
    points = pd.DataFrame(power_curve(start, end, sport))
    if points.empty:
        return
    st.subheader("Power curve")
    points["label"] = points["seconds"].map(
        lambda s: f"{s}s" if s < 60 else f"{s // 60}min" if s < 3600 else f"{s // 3600}h"
    )
    chart = (
        alt.Chart(points)
        .mark_line(point=True, color="#fb8c00")
        .encode(
            x=alt.X("seconds:Q", scale=alt.Scale(type="log"), title="Duration",
                    axis=alt.Axis(values=points["seconds"].tolist(), labelExpr="datum.value < 60 ? datum.value + 's' : datum.value < 3600 ? datum.value / 60 + 'min' : datum.value / 3600 + 'h'")),
            y=alt.Y("watts:Q", title="Best average power (W)", scale=alt.Scale(zero=False)),
            tooltip=[alt.Tooltip("label:N", title="Duration"), alt.Tooltip("watts:Q", format=".0f")],
        )
        .properties(height=300)
    )
    st.altair_chart(chart, width="stretch")
    st.caption("Best average power over each duration, across rides with a power meter in this range.")


def show_calendar(start: date, end: date, sport: str | None) -> None:
    """GitHub-style heatmap of daily distance."""
    days = pd.DataFrame(daily_totals(start, end, sport))
    if days.empty:
        return
    st.subheader("Activity calendar")
    days["day"] = pd.to_datetime(days["day"])
    days["week"] = (days["day"] - pd.to_timedelta(days["day"].dt.weekday, unit="D")).dt.strftime("%Y-%m-%d")
    days["weekday"] = days["day"].dt.strftime("%a")
    chart = (
        alt.Chart(days)
        .mark_rect(cornerRadius=2)
        .encode(
            x=alt.X("week:O", title=None, axis=alt.Axis(labels=False, ticks=False)),
            y=alt.Y("weekday:O", title=None, sort=["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]),
            color=alt.Color("distance_km:Q", title="km", scale=alt.Scale(scheme="blues")),
            tooltip=[
                alt.Tooltip("day:T", title="Date"),
                alt.Tooltip("distance_km:Q", title="km", format=".1f"),
                alt.Tooltip("hours:Q", format=".1f"),
                alt.Tooltip("activity_count:Q", title="Activities"),
            ],
        )
        .properties(height=200)
    )
    st.altair_chart(chart, width="stretch")


def pick_ride(rows: list[dict], key: str) -> None:
    """Show a selectable activity table and the details of the selected one."""
    if not rows:
        st.info("No activities match.")
        return
    table = activities_table(rows)
    event = st.dataframe(
        table, hide_index=True, on_select="rerun", selection_mode="single-row", key=key,
        width="stretch",
    )
    if event.selection.rows:
        show_ride_details(int(table.index[event.selection.rows[0]]))
    else:
        st.caption("Select an activity to see its details.")


def _delta(now: float, before: float, unit: str, digits: int = 0) -> str | None:
    diff = now - before
    return f"{diff:+,.{digits}f} {unit}".strip() if before else None


def show_overview() -> None:
    today = date.today()
    week = (today - timedelta(days=6), today)
    prev = (today - timedelta(days=13), today - timedelta(days=7))
    s, p = summary(*week), summary(*prev)

    st.subheader("Last 7 days")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Activities", f"{s['activity_count']}", _delta(s["activity_count"], p["activity_count"], ""))
    c2.metric("Distance", f"{s['total_distance_km']:,.0f} km", _delta(s["total_distance_km"], p["total_distance_km"], "km"))
    c3.metric("Time", fmt_seconds_hm(s["total_moving_duration_h"] * 3600) or "0:00",
              _delta(s["total_moving_duration_h"], p["total_moving_duration_h"], "h", 1))
    c4.metric("Elevation", f"{s['total_elevation_gain_m']:,.0f} m",
              _delta(s["total_elevation_gain_m"], p["total_elevation_gain_m"], "m"))
    st.caption("Changes compare with the 7 days before.")
    pick_ride(search_activities(start=week[0], end=week[1]), key="overview_rides")

    st.subheader("Health")
    daily = {date.fromisoformat(r["day"]): r["load"] for r in daily_training_load()}
    now = fitness_series(daily, end=today)[-1] if daily else None
    h1, h2, h3, h4 = st.columns(4)
    for col, group in zip((h1, h2), SPORT_GROUPS):
        v = latest_vo2max(group)
        col.metric(f"VO₂max · {group.lower()}", f"{v['value']:.0f}" if v else "–")
    h3.metric("Fitness", f"{now['fitness']:.0f}" if now else "–")
    h4.metric("Form", f"{now['form']:+.0f}" if now else "–", help="Fitness minus fatigue. Negative means tired.")

    periods = {
        "7 days": week,
        "30 days": (today - timedelta(days=29), today),
        "This year": (date(today.year, 1, 1), today),
        "All time": (date(1970, 1, 1), today),
    }
    totals = {name: distance_by_group(*rng) for name, rng in periods.items()}
    km = pd.DataFrame(totals).round(0).astype(int)
    km.index = [f"{g} (km)" for g in km.index]
    st.table(km)


def show_find() -> None:
    bounds = filter_bounds()
    if not bounds["km"]:
        st.info("No activities yet. Sync from Garmin first.")
        return
    max_km, max_elev, max_h = math.ceil(bounds["km"]), math.ceil(bounds["elevation_m"]), math.ceil(bounds["hours"])

    f1, f2, f3 = st.columns([2, 2, 1])
    text = f1.text_input("Search", placeholder="Name or place")
    sports = f2.multiselect("Sport", sport_options(), format_func=display_sport, placeholder="All sports")
    period = f3.selectbox("Period", ["All time", "Last 30 days", "Last 90 days", "This year", "Last year"])
    s1, s2, s3 = st.columns(3)
    km = s1.slider("Distance (km)", 0, max_km, (0, max_km))
    elev = s2.slider("Elevation gain (m)", 0, max_elev, (0, max_elev), step=10)
    hours = s3.slider("Moving time (h)", 0.0, float(max_h), (0.0, float(max_h)), step=0.25)

    today = date.today()
    start, end = {
        "All time": (None, None),
        "Last 30 days": (today - timedelta(days=29), None),
        "Last 90 days": (today - timedelta(days=89), None),
        "This year": (date(today.year, 1, 1), None),
        "Last year": (date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)),
    }[period]

    def bound(value, lo, hi, is_min):
        """A slider at its extreme means 'no limit', so rows missing that metric still match."""
        return None if value == (lo if is_min else hi) else value

    order = st.selectbox("Sort by", list(SEARCH_ORDERS), label_visibility="collapsed")
    rows = search_activities(
        start=start, end=end, sports=sports or None, text=text or None, order=order,
        min_km=bound(km[0], 0, max_km, True), max_km=bound(km[1], 0, max_km, False),
        min_elevation_m=bound(elev[0], 0, max_elev, True), max_elevation_m=bound(elev[1], 0, max_elev, False),
        min_hours=bound(hours[0], 0.0, float(max_h), True), max_hours=bound(hours[1], 0.0, float(max_h), False),
    )
    st.caption(f"{len(rows)} activities")
    pick_ride(rows, key="find_rides")


CHARTS = [
    "Weekly distance", "Fitness and form", "Heart-rate zones", "Power curve",
    "Calendar", "Weekly training load", "Time by sport",
]


def show_weekly_distance(start: date, end: date, sport: str | None) -> None:
    weekly = pd.DataFrame(weekly_totals(start, end, sport))
    st.caption("Click a week to see its rides.")
    week_pick = alt.selection_point(name="week", fields=["week_start"])
    chart = (
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
    event = st.altair_chart(chart, on_select="rerun", selection_mode="week", key="weekly_chart", width="stretch")
    week_start = selected_week(event)
    if week_start:
        ws = date.fromisoformat(week_start)
        rows = search_activities(start=ws, end=ws + timedelta(days=6), sports=[sport] if sport else None)
        st.markdown(f"**Week of {ws:%d %b %Y}** · {len(rows)} activities")
        pick_ride(rows, key="week_rides")


def show_trends() -> None:
    today = date.today()
    c1, c2, c3 = st.columns([2, 2, 1])
    chart_name = c1.selectbox("Chart", CHARTS)
    picked = c2.date_input("Date range", value=(today - timedelta(days=DEFAULT_RANGE_DAYS), today))
    sport_choice = c3.selectbox(
        "Sport", ["All"] + sport_options(), format_func=lambda x: "All" if x == "All" else display_sport(x)
    )
    if not isinstance(picked, tuple) or len(picked) != 2:
        st.info("Select an end date.")
        return
    start, end = picked
    sport = None if sport_choice == "All" else sport_choice
    if summary(start, end, sport)["activity_count"] == 0:
        st.info("No activities in this range. Try another date range or sport.")
        return

    if chart_name == "Weekly distance":
        show_weekly_distance(start, end, sport)
    elif chart_name == "Fitness and form":
        show_fitness_chart(start, end)
    elif chart_name == "Heart-rate zones":
        show_hr_zones(start, end, sport)
    elif chart_name == "Power curve":
        show_power_curve(start, end, sport)
    elif chart_name == "Calendar":
        show_calendar(start, end, sport)
    elif chart_name == "Weekly training load":
        weekly = pd.DataFrame(weekly_totals(start, end, sport))
        st.bar_chart(weekly.set_index("week_start")[["total_training_load"]])
    else:
        sports = pd.DataFrame(by_sport(start, end))
        sports["sport_type"] = sports["sport_type"].map(display_sport)
        st.bar_chart(sports.set_index("sport_type")[["total_moving_duration_h"]])


def main() -> None:
    st.set_page_config(page_title="Garmin dashboard", layout="centered")
    init_db()

    if "api" not in st.session_state:
        try_restore_session()
    if "api" not in st.session_state:
        st.title("Garmin dashboard")
        sign_in_form()
        return

    email = st.session_state.email
    auto_sync(email)
    sidebar(email)

    page = st.segmented_control(
        "Page", ["Overview", "Find", "Trends"], default="Overview", label_visibility="collapsed"
    )
    {"Find": show_find, "Trends": show_trends}.get(page, show_overview)()


if __name__ == "__main__":
    main()
