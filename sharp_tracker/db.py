from __future__ import annotations

import os
from datetime import datetime
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


def get_engine() -> Engine:
    engine = create_engine(database_url(), pool_pre_ping=True)
    metadata.create_all(engine)
    return engine


def insert_position(position: dict[str, Any], engine: Engine | None = None) -> None:
    engine = engine or get_engine()
    payload = dict(position)
    payload["received_at"] = _parse_dt(payload["received_at"])
    payload["fix_time"] = _parse_dt(payload.get("fix_time"))

    with engine.begin() as conn:
        conn.execute(insert(positions).values(**payload))


def fetch_positions_df(engine: Engine | None = None) -> pd.DataFrame:
    engine = engine or get_engine()
    stmt = select(positions).order_by(positions.c.fix_time, positions.c.received_at)
    with engine.connect() as conn:
        return pd.read_sql(stmt, conn, parse_dates=["received_at", "fix_time"])


def _parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    raise TypeError(f"Unsupported datetime value: {value!r}")
