from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(__file__).parent / "data"

# Pico firmware modules are pure Python and importable on CPython for tests.
# Appended, not prepended: the firmware's code.py would otherwise shadow the
# stdlib `code` module (pdb imports it).
sys.path.append(str(ROOT / "firmware" / "pico_nmea_tracker"))


@pytest.fixture
def sample_lines() -> list[str]:
    return (DATA / "sample.nmea").read_text().splitlines()


@pytest.fixture
def db(tmp_path, monkeypatch):
    """Fresh SQLite DB per test; returns the engine."""
    from sharp_tracker import db as db_module

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "positions.sqlite3"))
    db_module.get_engine.cache_clear()
    yield db_module.get_engine()
    db_module.get_engine.cache_clear()
