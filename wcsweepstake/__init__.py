"""World Cup 2026 sweepstake package.

Loads the canonical tournament data (generated from the Excel workbook) and
exposes the high-level :func:`compute_state` used by the web API.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import engine

DATA_PATH = Path(__file__).resolve().parent / "tournament_data.json"


def load_data(path: Path | None = None) -> dict:
    """Load and cache the canonical tournament data."""
    data = json.loads((path or DATA_PATH).read_text(encoding="utf-8"))
    return data


# Loaded once at import; the group fixtures power engine.group_standings().
DATA = load_data()
engine.set_group_fixtures(DATA["fixtures"])

from .core import compute_state, fixtures_view  # noqa: E402  (needs DATA ready)

__all__ = ["DATA", "load_data", "engine", "compute_state", "fixtures_view"]
