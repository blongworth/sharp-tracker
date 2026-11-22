from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from sharp_tracker import db
from sharp_tracker.db import (
    get_forward_cursor,
    insert_positions,
    latest_position,
    latest_positions,
    mark_forwarded,
    track_positions,
    unsent_positions,
)

UTC = timezone.utc


def fix(second: int, *, fix_time: bool = True, raw: str | None = None) -> dict:
    return {
        "received_at": f"2026-11-15T12:35:{second:02d}+00:00",
        "fix_time": f"2026-11-15T12:35:{second:02d}+00:00" if fix_time else None,
        "sentence_type": "RMC",
        "latitude": 41.5 + second / 1000,
        "longitude": -70.6,
        "raw": raw or f"$GPRMC,1235{second:02d}",
    }


def count(engine) -> int:
    with engine.connect() as conn:
        return conn.execute(select(func.count()).select_from(db.positions)).scalar_one()


def test_duplicate_insert_is_skipped(engine):
    batch = [fix(1), fix(2)]
    insert_positions(batch)
    insert_positions(batch)  # e.g. HTTP retry after a lost 201
    insert_positions([fix(2), fix(3)])  # overlapping batch
    assert count(engine) == 3


def test_same_raw_at_different_time_is_kept(engine):

    insert_positions([fix(1, raw="$SAME"), fix(2, raw="$SAME")])
    assert count(engine) == 2


def test_empty_insert_is_noop(engine):

    insert_positions([])
    assert count(engine) == 0


def test_non_utc_offset_is_normalized(engine):

    row = fix(1, fix_time=False)
    row["received_at"] = "2026-11-15T07:35:01-05:00"
    insert_positions([row])
    assert latest_position()["received_at"] == datetime(
        2026, 11, 15, 12, 35, 1, tzinfo=UTC
    )


def test_latest_positions_newest_first_utc_and_display_time(engine):

    assert latest_positions(2) == []
    assert latest_position() is None

    insert_positions([fix(1), fix(2, fix_time=False), fix(3)])
    rows = latest_positions(2)
    assert [r["received_at"].second for r in rows] == [3, 2]
    assert all(r["received_at"].tzinfo is not None for r in rows)
    assert rows[0]["display_time"] == rows[0]["fix_time"]
    assert rows[1]["display_time"] == rows[1]["received_at"]  # fix_time fallback
    # The live page does `new Date(t)`; without an offset browsers assume local time.
    assert rows[0]["display_time"].isoformat().endswith("+00:00")


def many(n: int) -> list[dict]:
    """n fixes one second apart."""
    base = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)
    return [
        {
            "received_at": (base + timedelta(seconds=i)).isoformat(),
            "latitude": 41.0 + i / 10000,
            "longitude": -70.6,
            "raw": f"$X{i}",
        }
        for i in range(n)
    ]


def test_track_positions_unthinned_when_small(engine):
    insert_positions(many(10))
    rows = track_positions(max_points=10)
    assert [r["raw"] for r in rows] == [f"$X{i}" for i in range(10)]
    assert all(r["display_time"].tzinfo is not None for r in rows)


@pytest.mark.parametrize(
    "n, max_points", [(11, 10), (1000, 10), (1001, 100), (2500, 1000), (57, 13)]
)
def test_track_positions_thinned_keeps_bounds(engine, n, max_points):
    insert_positions(many(n))
    rows = track_positions(max_points=max_points)
    assert len(rows) <= max_points
    assert len(rows) >= max_points // 2  # thinned, not gutted
    assert rows[0]["raw"] == "$X0"  # oldest kept
    assert rows[-1]["raw"] == f"$X{n - 1}"  # newest kept: track ends at the ship
    times = [r["received_at"] for r in rows]
    assert times == sorted(times)


def test_track_positions_since(engine):
    insert_positions(many(10))
    rows = track_positions(since="2026-11-15T12:00:07+00:00")
    assert [r["raw"] for r in rows] == ["$X7", "$X8", "$X9"]
    assert track_positions(since="2027-01-01T00:00:00+00:00") == []


def test_forward_cursor_round_trip(engine):

    insert_positions([fix(1), fix(2), fix(3)])
    assert get_forward_cursor() == 0

    batch = unsent_positions(get_forward_cursor(), limit=2)
    assert [p["id"] for p in batch] == [1, 2]
    assert batch[0]["received_at"] == "2026-11-15T12:35:01+00:00"
    assert batch[0]["fix_time"] == "2026-11-15T12:35:01+00:00"

    mark_forwarded(batch[-1]["id"])
    assert get_forward_cursor() == 2
    assert [p["id"] for p in unsent_positions(get_forward_cursor())] == [3]

    mark_forwarded(3)  # upsert, not a second row
    assert get_forward_cursor() == 3
    assert unsent_positions(3) == []
    assert get_forward_cursor("other-target") == 0


def test_forwarded_rows_round_trip_through_api_model(engine):
    """unsent_positions() output must validate as the ingest API's PositionIn."""
    from sharp_tracker.api import PositionsIn

    insert_positions([fix(1), fix(2, fix_time=False)])
    body = PositionsIn(positions=unsent_positions(0))
    assert len(body.positions) == 2


LEGACY_SCHEMA = """
CREATE TABLE positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, received_at DATETIME NOT NULL, fix_time DATETIME,
    talker VARCHAR(16), sentence_type VARCHAR(16), latitude FLOAT NOT NULL, longitude FLOAT NOT NULL,
    altitude_m FLOAT, speed_knots FLOAT, course_deg FLOAT, quality INTEGER, satellites INTEGER,
    raw TEXT NOT NULL)
"""
LEGACY_ROW = "INSERT INTO positions (received_at, latitude, longitude, raw) VALUES ('2026-11-15 12:35:01.000000', 41.5, -70.6, '$X')"


@pytest.fixture
def legacy_db(tmp_path, monkeypatch):
    """A DB file created by the pre-index schema; returns a function to open it."""
    path = tmp_path / "legacy.sqlite3"
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_PATH", str(path))
    db.get_engine.cache_clear()
    yield path
    db.get_engine.cache_clear()


def test_sqlite_uses_wal(legacy_db):
    db.get_engine()
    with sqlite3.connect(legacy_db) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_existing_table_gets_unique_index(legacy_db):
    with sqlite3.connect(legacy_db) as conn:
        conn.execute(LEGACY_SCHEMA)
        conn.execute(LEGACY_ROW)

    db.get_engine()
    with sqlite3.connect(legacy_db) as conn:
        indexes = {row[1] for row in conn.execute("PRAGMA index_list(positions)")}
    assert db.UNIQUE_FIX_INDEX in indexes

    db.insert_positions(
        [
            {
                "received_at": "2026-11-15T12:35:01+00:00",
                "latitude": 41.5,
                "longitude": -70.6,
                "raw": "$X",
            }
        ]
    )
    assert count(db.get_engine()) == 1  # legacy row matched, not duplicated


def test_existing_duplicates_fail_loudly(legacy_db):
    with sqlite3.connect(legacy_db) as conn:
        conn.execute(LEGACY_SCHEMA)
        conn.execute(LEGACY_ROW)
        conn.execute(LEGACY_ROW)

    with pytest.raises(RuntimeError, match="duplicate"):
        db.get_engine()
