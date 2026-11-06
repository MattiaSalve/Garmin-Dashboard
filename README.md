# garmin-dashboards

Personal Streamlit dashboard for Garmin Connect activities.

## Setup
```
uv sync
uv run garmin-dashboards
```
Sign in with your Garmin account in the app, then press Sync. Activities are stored in a local SQLite file (`garmin_data.db`). Login tokens are stored in `~/.garmin_dashboards/`.
