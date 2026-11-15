"""SHARP Tracker web app: live MapLibre map fed by SSE, plus position ingest.

Serves:
  GET  /                -> static MapLibre page
  GET  /track           -> Server-Sent Events stream of the latest position
  GET  /api/positions   -> initial position window for the track line
  GET  /api/waypoints   -> current NES-LTER stations
  GET  /health          -> health check (from api.router)
  POST /positions/batch -> token-gated ingest (from api.router)

With COLLECT=1 (`sharp-tracker --collect`) it also runs the NMEA collector in
a background thread, so one process reads, logs, forwards, and serves the map.
"""

from __future__ import annotations

import asyncio
import csv
import json
import logging
import math
import os
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from io import StringIO
from pathlib import Path
from typing import Any, AsyncIterator

import requests
from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .api import router as ingest_router
from .db import get_engine, latest_positions, track_positions

LOG = logging.getLogger(__name__)

LIVE_DIR = Path(__file__).parent / "static"
LIVE_REFRESH_SECONDS = float(os.getenv("LIVE_REFRESH_SECONDS", "10"))
WAYPOINTS_URL = "https://nes-lter-api.whoi.edu/api/stations/file.csv"


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Fail loudly at startup if DB config is wrong.
    get_engine()
    if os.getenv("COLLECT", "").lower() in ("1", "true", "yes"):
        # Imported lazily: a server-only deployment never needs the collector.
        from .collector import collect_forever

        LOG.info("COLLECT is set; starting NMEA collector thread")
        # Daemon thread: it blocks on socket/serial reads and simply ends with the process.
        threading.Thread(target=collect_forever, name="collector", daemon=True).start()
    yield


app = FastAPI(title="SHARP Tracker", lifespan=lifespan)
app.include_router(ingest_router)


def _compact_position(row: dict) -> dict:
    t = row.get("display_time")
    return {
        "t": t.isoformat() if t is not None else None,
        "lat": row.get("latitude"),
        "lon": row.get("longitude"),
        "sog": row.get("speed_knots"),
        "cog": row.get("course_deg"),
    }


def _bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Compass bearing (0-360) from point 1 to point 2, planar approximation."""
    y = math.sin(math.radians(lon2 - lon1)) * math.cos(math.radians(lat2))
    x = (math.cos(math.radians(lat1)) * math.sin(math.radians(lat2))
         - math.sin(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.cos(math.radians(lon2 - lon1)))
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def _heading(row: dict, prev: dict | None) -> float | None:
    """COG when present, otherwise the direction of travel between the last two fixes."""
    if row.get("course_deg") is not None:
        return float(row["course_deg"])
    if prev is None:
        return None
    return _bearing_deg(prev["latitude"], prev["longitude"], row["latitude"], row["longitude"])


async def _position_stream() -> AsyncIterator[str]:
    """Yield the latest position (and calculated heading) every LIVE_REFRESH_SECONDS."""
    while True:
        rows = await asyncio.to_thread(latest_positions, 2)
        row = rows[0] if rows else None
        if row:
            yield f"data: {json.dumps({**_compact_position(row), 'heading': _heading(row, rows[1] if len(rows) > 1 else None)})}\n\n"
        else:
            # SSE comment: keeps the connection alive while the table is empty.
            yield ": waiting for first position\n\n"
        await asyncio.sleep(LIVE_REFRESH_SECONDS)


def _float_or_none(value: str | None) -> float | None:
    """CSV cell to float; empty and "NaN" cells become None."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) else number


@lru_cache(maxsize=1)
def load_waypoints() -> list[dict[str, Any]]:
    """Current NES-LTER station positions, cached for the process lifetime."""
    response = requests.get(WAYPOINTS_URL, timeout=15)
    response.raise_for_status()
    stations = []
    for row in csv.DictReader(StringIO(response.text)):
        lat = _float_or_none(row.get("decimalLatitude"))
        lon = _float_or_none(row.get("decimalLongitude"))
        if str(row.get("endDate")).lower() != "current" or lat is None or lon is None:
            continue
        stations.append(
            {
                "station": row["station"],
                "name": row["stationfullname"],
                "lat": lat,
                "lon": lon,
                "depth_m": _float_or_none(row.get("depth_m")),
            }
        )
    return sorted(stations, key=lambda w: (w["name"], w["station"]))


@app.get("/")
def index() -> FileResponse:
    return FileResponse(LIVE_DIR / "index.html")


app.mount("/vendor", StaticFiles(directory=LIVE_DIR / "vendor"), name="vendor")


@app.get("/api/waypoints")
def waypoints() -> list[dict[str, Any]]:
    """Current NES-LTER stations; [] when the remote API is unreachable."""
    try:
        return load_waypoints()
    except Exception:
        return []


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
    """Positions within the last `window_hours`, thinned to at most `max_points`."""
    since = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    return [_compact_position(row) for row in track_positions(since=since, max_points=max_points)]
