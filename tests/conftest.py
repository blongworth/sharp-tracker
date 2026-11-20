from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(__file__).parent / "data"

# Pico firmware modules are pure Python and importable on CPython for tests.
# Appended, not prepended: the firmware's code.py would otherwise shadow the
# stdlib `code` module (pdb imports it).
sys.path.append(str(ROOT / "firmware" / "pico_nmea_tracker"))


@pytest.fixture
def sample_lines() -> list[str]:
    return (DATA / "sample.nmea").read_text().splitlines()


def _test_postgres_url() -> str | None:
    """TEST_DATABASE_URL, refused unless it is clearly a local test database.

    Tests drop every table, so never let this point at real data.
    """
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        return None
    parsed = make_url(url)
    if parsed.host not in ("127.0.0.1", "localhost") or "test" not in (
        parsed.database or ""
    ):
        raise pytest.UsageError(
            "TEST_DATABASE_URL must be a localhost database whose name contains 'test'"
        )
    return url


@pytest.fixture(params=["sqlite", "postgres"])
def engine(request, tmp_path, monkeypatch):
    """Empty database per test, on SQLite and (if TEST_DATABASE_URL is set) Postgres."""
    from sharp_tracker import db as db_module

    if request.param == "sqlite":
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "positions.sqlite3"))
    else:
        url = _test_postgres_url()
        if url is None:
            pytest.skip("TEST_DATABASE_URL not set (see compose.yaml)")
        monkeypatch.setenv("DATABASE_URL", url)
        db_module.get_engine.cache_clear()
        db_module.metadata.drop_all(db_module.get_engine())

    db_module.get_engine.cache_clear()
    engine = db_module.get_engine()
    yield engine
    engine.dispose()
    db_module.get_engine.cache_clear()
