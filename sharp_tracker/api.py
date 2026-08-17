from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, Header, HTTPException, status
from pydantic import BaseModel, Field

from .db import db_label, get_engine, insert_position

app = FastAPI(title="SHARP Tracker ingest API")


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


@app.on_event("startup")
def startup() -> None:
    # Create tables early so startup fails loudly if DB config is wrong.
    get_engine()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "database": db_label()}


@app.post("/positions", status_code=status.HTTP_201_CREATED)
def create_position(
    position: PositionIn,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    expected_token = os.getenv("TRACKER_INGEST_TOKEN")
    if expected_token:
        expected_header = f"Bearer {expected_token}"
        if authorization != expected_header:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid ingest token")

    payload = position.model_dump() if hasattr(position, "model_dump") else position.dict()
    insert_position(payload)
    return {"status": "created"}
