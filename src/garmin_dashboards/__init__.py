import sys
from pathlib import Path


def main() -> None:
    """Launch the Streamlit dashboard."""
    from streamlit.web import cli as stcli

    app = Path(__file__).parent / "main.py"
    sys.argv = ["streamlit", "run", str(app)]
    sys.exit(stcli.main())
