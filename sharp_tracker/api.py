"""Position ingest: validation + storage, plus the HTTP routes that expose it.

`router` is mounted on the main web app (sharp_tracker.live). `ingest_batch()`
holds the transport-independent logic so another transport (e.g. an MQTT
subscriber) can reuse it without going through HTTP.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel, Field

from .db import db_label, insert_positions, latest_position


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


def ingest_batch(payload: dict[str, Any] | str | bytes) -> int:
    """Validate a ``{"positions": [...]}`` batch and store it; returns its size.

    Accepts a parsed dict or raw JSON. Raises pydantic.ValidationError on bad
    input. Storage is idempotent, so redelivered batches are harmless.
    """
    if isinstance(payload, dict):
        body = PositionsIn.model_validate(payload)
    else:
        body = PositionsIn.model_validate_json(payload)
    insert_positions([p.model_dump() for p in body.positions])
    return len(body.positions)


def _check_token(authorization: str | None) -> None:
    expected_token = os.getenv("TRACKER_INGEST_TOKEN")
    if expected_token and authorization != f"Bearer {expected_token}":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid ingest token")


router = APIRouter()


@router.get("/health")
def health() -> dict[str, Any]:
    """Liveness plus the newest fix, so monitors can alert when data stops.

    Always 200 while the app and DB are up: a ship out of contact must not make
    the platform restart the server.
    """
    latest = latest_position()
    last_fix_at = latest["received_at"] if latest else None
    age = (datetime.now(timezone.utc) - last_fix_at).total_seconds() if last_fix_at else None
    return {
        "status": "ok",
        "database": db_label(),
        "last_fix_received_at": last_fix_at.isoformat() if last_fix_at else None,
        "last_fix_age_seconds": round(age, 1) if age is not None else None,
    }


@router.post("/positions/batch", status_code=status.HTTP_201_CREATED)
def create_positions(
    body: PositionsIn,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _check_token(authorization)
    count = ingest_batch(body.model_dump())
    return {"status": "created", "count": count}

