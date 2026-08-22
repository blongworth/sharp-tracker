from __future__ import annotations

import os
from datetime import datetime
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
    insert,
    select,
)
from sqlalchemy.engine import Engine

load_dotenv()

DEFAULT_DB_PATH = "data/positions.sqlite3"

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
    metadata.create_all(engine)
    return engine


def insert_position(position: dict[str, Any], engine: Engine | None = None) -> None:
    insert_positions([position], engine=engine)


def insert_positions(rows: list[dict[str, Any]], engine: Engine | None = None) -> None:
    """Insert one or more position dicts in a single transaction."""
    engine = engine or get_engine()
    payload = []
    for row in rows:
        data = dict(row)
        data["received_at"] = _parse_dt(data["received_at"])
        data["fix_time"] = _parse_dt(data.get("fix_time"))
        payload.append(data)

    with engine.begin() as conn:
        conn.execute(insert(positions), payload)


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
        return pd.read_sql(stmt, conn, parse_dates=["received_at", "fix_time"])


def latest_position(engine: Engine | None = None) -> dict | None:
    """Return the most recent position as a dict, or None if the table is empty.

    Adds `display_time` = fix_time with received_at as fallback.
    """
    engine = engine or get_engine()
    stmt = select(positions).order_by(positions.c.received_at.desc(), positions.c.id.desc()).limit(1)
    with engine.connect() as conn:
        row = conn.execute(stmt).mappings().first()
    if row is None:
        return None
    data = dict(row)
    data["display_time"] = data.get("fix_time") or data.get("received_at")
    return data


def _parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    raise TypeError(f"Unsupported datetime value: {value!r}")
