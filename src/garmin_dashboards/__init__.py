import os


def main() -> None:
    """Serve the API (and the built web app, if present)."""
    import uvicorn

    uvicorn.run(
        "garmin_dashboards.api:app",
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8000")),
    )
