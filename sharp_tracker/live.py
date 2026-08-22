"""Live map dashboard: a single MapLibre page fed by SSE, no full-page refreshes.

Serves:
  GET /               -> static MapLibre page
  GET /track          -> Server-Sent Events stream of the latest position
  GET /api/positions  -> initial position window for the track line
"""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import AsyncIterator

import pandas as pd
from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, StreamingResponse

from .db import fetch_positions_df, get_engine, latest_position

LIVE_DIR = Path(__file__).parent / "static"
LIVE_REFRESH_SECONDS = float(os.getenv("LIVE_REFRESH_SECONDS", "10"))


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Fail loudly at startup if DB config is wrong.
    get_engine()
    yield


app = FastAPI(title="SHARP Tracker live map", lifespan=lifespan)


def _compact_position(row: dict) -> dict:
    t = row.get("display_time")
    return {
        "t": t.isoformat() if t is not None else None,
        "lat": row.get("latitude"),
        "lon": row.get("longitude"),
        "sog": row.get("speed_knots"),
        "cog": row.get("course_deg"),
    }


async def _position_stream() -> AsyncIterator[str]:
    """Yield the latest position every LIVE_REFRESH_SECONDS."""
    while True:
        row = await asyncio.to_thread(latest_position)
        if row:
            yield f"data: {json.dumps(_compact_position(row))}\n\n"
        else:
            # SSE comment: keeps the connection alive while the table is empty.
            yield ": waiting for first position\n\n"
        await asyncio.sleep(LIVE_REFRESH_SECONDS)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(LIVE_DIR / "index.html")


@app.get("/track")
def track() -> StreamingResponse:
    return StreamingResponse(
        _position_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/positions")
def positions(
    window_hours: float = Query(6.0, ge=0.01, le=720.0),
    max_points: int = Query(1000, ge=10, le=10000),
) -> list[dict]:
    """Positions within the last `window_hours`, decimated to at most `max_points`."""
    since = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    df = fetch_positions_df(since=since)
    df["display_time"] = df["fix_time"].fillna(df["received_at"])
    df = df.dropna(subset=["display_time", "latitude", "longitude"])
    if len(df) > max_points:
        step = len(df) // max_points
        df = df.iloc[::step]

    records = []
    for row in df.itertuples(index=False):
        records.append(
            {
                "t": row.display_time.isoformat(),
                "lat": float(row.latitude),
                "lon": float(row.longitude),
                "sog": None if pd.isna(row.speed_knots) else float(row.speed_knots),
                "cog": None if pd.isna(row.course_deg) else float(row.course_deg),
            }
        )
    return records


def main() -> None:
    import uvicorn

    uvicorn.run(
        "sharp_tracker.live:app",
        host=os.getenv("LIVE_HOST", "127.0.0.1"),
        port=int(os.getenv("LIVE_PORT", "8090")),
    )


if __name__ == "__main__":
    main()
