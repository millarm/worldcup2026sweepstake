"""Shared pytest fixtures."""
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
XLSX = ROOT / "data" / "World_Cup_sweepstake.xlsx"


@pytest.fixture(autouse=True)
def _restore_group_fixtures():
    """Tests that install synthetic fixtures must not leak into other tests."""
    from wcsweepstake import DATA, engine
    saved = list(engine._GROUP_FIXTURES)
    yield
    engine.set_group_fixtures(saved if saved else DATA["fixtures"])


@pytest.fixture
def store(tmp_path):
    """A throwaway SQLite store backed by a temp file."""
    from wcsweepstake.store import Store
    s = Store(tmp_path / "test.db")
    yield s
    s.close()


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Flask test client with an isolated in-memory database."""
    monkeypatch.setenv("WC_DB_PATH", str(tmp_path / "api.db"))
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    # Default feed source is the live ESPN API; pin to the offline sample so
    # tests are deterministic and never touch the network.
    monkeypatch.setenv("WC_FEED_SOURCE", "sample")
    monkeypatch.delenv("WC_FEED_AUTO", raising=False)
    monkeypatch.delenv("FOOTBALL_DATA_API_KEY", raising=False)
    monkeypatch.delenv("WC_RESULTS_URL", raising=False)
    import importlib
    import app as app_module
    importlib.reload(app_module)
    app_module.app.config.update(TESTING=True)
    return app_module.app.test_client()


@pytest.fixture
def sample_results():
    """The bundled sample feed (a full, deterministic group stage)."""
    import json
    return json.loads((ROOT / "data" / "sample_results.json").read_text("utf-8"))


def has_workbook() -> bool:
    return XLSX.exists()
