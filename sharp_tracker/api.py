from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Header, HTTPException, status
from pydantic import BaseModel, Field

from .db import db_label, get_engine, insert_position, insert_positions


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Create tables early so startup fails loudly if DB config is wrong.
    get_engine()
    yield


app = FastAPI(title="SHARP Tracker ingest API", lifespan=lifespan)


class PositionIn(BaseModel):
    received_at: str
    fix_time: str | None = None
    talker: str | None = None
    sentence_type: str | None = None
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    altitude_m: float | None = None
    speed_knots: float | None = None
    course_deg: float | None = None
    quality: int | None = None
    satellites: int | None = None
    raw: str


class PositionsIn(BaseModel):
    positions: list[PositionIn]


def _check_token(authorization: str | None) -> None:
    expected_token = os.getenv("TRACKER_INGEST_TOKEN")
    if expected_token and authorization != f"Bearer {expected_token}":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid ingest token")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "database": db_label()}


@app.post("/positions", status_code=status.HTTP_201_CREATED)
def create_position(
    position: PositionIn,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _check_token(authorization)
    insert_position(position.model_dump())
    return {"status": "created"}


@app.post("/positions/batch", status_code=status.HTTP_201_CREATED)
def create_positions(
    body: PositionsIn,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _check_token(authorization)
    insert_positions([p.model_dump() for p in body.positions])
    return {"status": "created", "count": len(body.positions)}
