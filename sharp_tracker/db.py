from __future__ import annotations

import os
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import (
    Column,
    DateTime,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    event,
    select,
    text,
)
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

load_dotenv()

DEFAULT_DB_PATH = "data/positions.sqlite3"
DEFAULT_FORWARD_TARGET = "server"

metadata = MetaData()

positions = Table(
    "positions",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("received_at", DateTime(timezone=True), nullable=False, index=True),
    Column("fix_time", DateTime(timezone=True), nullable=True, index=True),
    Column("talker", String(16)),
    Column("sentence_type", String(16)),
    Column("latitude", Float, nullable=False),
    Column("longitude", Float, nullable=False),
    Column("altitude_m", Float),
    Column("speed_knots", Float),
    Column("course_deg", Float),
    Column("quality", Integer),
    Column("satellites", Integer),
    Column("raw", Text, nullable=False),
)

# Collector-side cursor: highest positions.id already accepted by the server.
forward_state = Table(
    "forward_state",
    metadata,
    Column("name", String(32), primary_key=True),
    Column("last_id", Integer, nullable=False),
)

# Created with raw SQL rather than in the Table definition: create_all() skips
# indexes on tables that already exist, and existing deployments predate it.
# A resent fix (HTTP retry after a lost response, MQTT QoS 1 redelivery) has
# the same received_at and raw sentence, so this makes inserts idempotent.
UNIQUE_FIX_INDEX = "uq_positions_received_at_raw"
_CREATE_UNIQUE_FIX_INDEX = text(f"CREATE UNIQUE INDEX IF NOT EXISTS {UNIQUE_FIX_INDEX} ON positions (received_at, raw)")


def database_url() -> str:
    """Return SQLAlchemy database URL.

    Local default is SQLite. Railway Postgres supplies DATABASE_URL, often using
    postgres://, which SQLAlchemy expects as postgresql+psycopg://.
    """
    url = os.getenv("DATABASE_URL")
    if url:
        if url.startswith("postgres://"):
            return url.replace("postgres://", "postgresql+psycopg://", 1)
        if url.startswith("postgresql://"):
            return url.replace("postgresql://", "postgresql+psycopg://", 1)
        return url

    path = Path(os.getenv("DATABASE_PATH", DEFAULT_DB_PATH))
    path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{path}"


def db_label() -> str:
    if os.getenv("DATABASE_URL"):
        return "DATABASE_URL"
    return os.getenv("DATABASE_PATH", DEFAULT_DB_PATH)


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Shared engine; created (and schema-checked) once per process."""
    engine = create_engine(database_url(), pool_pre_ping=True)
    if engine.dialect.name == "sqlite":
        # WAL lets the map and forwarder read while the collector writes
        # (default journal mode blocks writers during reads). Lock waits use
        # the sqlite3 driver's default 5 s timeout.
        event.listen(engine, "connect", _sqlite_wal)
    metadata.create_all(engine)
    try:
        with engine.begin() as conn:
            conn.execute(_CREATE_UNIQUE_FIX_INDEX)
    except IntegrityError as exc:
        raise RuntimeError(
            f"Cannot create {UNIQUE_FIX_INDEX}: the positions table already contains "
            "duplicate (received_at, raw) rows. Delete the duplicates (keeping the lowest id) and restart."
        ) from exc
    return engine


def _sqlite_wal(dbapi_connection: Any, _record: Any) -> None:
    dbapi_connection.execute("PRAGMA journal_mode=WAL")


def _insert(engine: Engine, table: Table):
    """Dialect-specific INSERT, which supports ON CONFLICT."""
    if engine.dialect.name == "sqlite":
        return sqlite.insert(table)
    if engine.dialect.name == "postgresql":
        return postgresql.insert(table)
    raise NotImplementedError(f"Unsupported database dialect: {engine.dialect.name}")


def insert_position(position: dict[str, Any], engine: Engine | None = None) -> None:
    insert_positions([position], engine=engine)


def insert_positions(rows: list[dict[str, Any]], engine: Engine | None = None) -> None:
    """Insert position dicts in one transaction; already-stored fixes are skipped."""
    if not rows:
        return
    engine = engine or get_engine()
    payload = []
    for row in rows:
        data = {k: v for k, v in row.items() if k in positions.c and k != "id"}
        data["received_at"] = _parse_dt(data["received_at"])
        data["fix_time"] = _parse_dt(data.get("fix_time"))
        payload.append(data)

    with engine.begin() as conn:
        conn.execute(_insert(engine, positions).on_conflict_do_nothing(), payload)


def fetch_positions_df(
    engine: Engine | None = None,
    since: datetime | str | None = None,
) -> pd.DataFrame:
    """Fetch positions, optionally limited to `since` (by received_at)."""
    engine = engine or get_engine()
    stmt = select(positions).order_by(positions.c.received_at, positions.c.id)
    if since is not None:
        stmt = stmt.where(positions.c.received_at >= _parse_dt(since))
    with engine.connect() as conn:
        utc = {"utc": True}
        return pd.read_sql(stmt, conn, parse_dates={"received_at": utc, "fix_time": utc})


def latest_positions(n: int = 1, engine: Engine | None = None) -> list[dict]:
    """The n most recent positions, newest first.

    Each dict adds `display_time` = fix_time with received_at as fallback.
    """
    engine = engine or get_engine()
    stmt = select(positions).order_by(positions.c.received_at.desc(), positions.c.id.desc()).limit(n)
    with engine.connect() as conn:
        rows = conn.execute(stmt).mappings().all()
    result = []
    for row in rows:
        data = _row_dict(row)
        data["display_time"] = data["fix_time"] or data["received_at"]
        result.append(data)
    return result


def latest_position(engine: Engine | None = None) -> dict | None:
    """The most recent position (see latest_positions), or None if the table is empty."""
    rows = latest_positions(1, engine=engine)
    return rows[0] if rows else None


def get_forward_cursor(name: str = DEFAULT_FORWARD_TARGET, engine: Engine | None = None) -> int:
    """Highest positions.id already forwarded to `name`; 0 if nothing has been sent."""
    engine = engine or get_engine()
    stmt = select(forward_state.c.last_id).where(forward_state.c.name == name)
    with engine.connect() as conn:
        last_id = conn.execute(stmt).scalar_one_or_none()
    return last_id or 0


def unsent_positions(after_id: int, limit: int = 50, engine: Engine | None = None) -> list[dict]:
    """Positions with id > after_id, oldest first, as JSON-ready dicts.

    Times are ISO 8601 UTC strings. `id` is included so the caller can advance
    the cursor; the ingest API ignores it.
    """
    engine = engine or get_engine()
    stmt = select(positions).where(positions.c.id > after_id).order_by(positions.c.id).limit(limit)
    with engine.connect() as conn:
        rows = conn.execute(stmt).mappings().all()
    result = []
    for row in rows:
        data = _row_dict(row)
        for key in ("received_at", "fix_time"):
            if data[key] is not None:
                data[key] = data[key].isoformat()
        result.append(data)
    return result


def mark_forwarded(last_id: int, name: str = DEFAULT_FORWARD_TARGET, engine: Engine | None = None) -> None:
    """Record that every position up to and including last_id reached `name`."""
    engine = engine or get_engine()
    stmt = _insert(engine, forward_state).values(name=name, last_id=last_id)
    stmt = stmt.on_conflict_do_update(index_elements=["name"], set_={"last_id": last_id})
    with engine.begin() as conn:
        conn.execute(stmt)


def _row_dict(row: Any) -> dict:
    """Row mapping to dict with timezone-aware UTC datetimes.

    SQLite drops tzinfo on read; every stored time is UTC (see _parse_dt), so
    naive values are tagged as UTC rather than left for clients to guess.
    """
    data = dict(row)
    for key in ("received_at", "fix_time"):
        value = data.get(key)
        if value is not None and value.tzinfo is None:
            data[key] = value.replace(tzinfo=timezone.utc)
    return data


def _parse_dt(value: Any) -> datetime | None:
    """Parse to a datetime, normalized to UTC when an offset is present.

    SQLite stores wall-clock time without the offset, so a non-UTC aware value
    would otherwise be stored shifted.
    """
    if value in (None, ""):
        return None
    if isinstance(value, pd.Timestamp):
        value = value.to_pydatetime()
    elif isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    elif not isinstance(value, datetime):
        raise TypeError(f"Unsupported datetime value: {value!r}")
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc)
    return value
