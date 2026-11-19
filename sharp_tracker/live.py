"""SHARP Tracker web app: live MapLibre map fed by SSE, plus position ingest.

Serves:
  GET  /                -> static MapLibre page
  GET  /track           -> Server-Sent Events stream of the latest position
  GET  /api/positions   -> initial position window for the track line
  GET  /api/waypoints   -> current NES-LTER stations
  GET  /api/eta         -> distance / time to go / ETA to a station, a point, or the predicted next station
  GET  /api/eta/stations -> the same for every station, nearest first
  GET  /eta             -> static page listing time to go
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
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .api import router as ingest_router
from .db import get_engine, latest_positions, track_positions
from .eta import bearing_deg, distance_nm, mean_sog, predict_next, time_to_go

LOG = logging.getLogger(__name__)

LIVE_DIR = Path(__file__).parent / "static"
LIVE_REFRESH_SECONDS = float(os.getenv("LIVE_REFRESH_SECONDS", "10"))
# The map flags the feed stale when the newest fix is older than this. Fixes
# arrive once per averaging window (AVERAGE_SECONDS, default 60) and are
# stamped at its midpoint, so ~3 windows avoids false alarms from one gap.
STALE_AFTER_SECONDS = float(os.getenv("STALE_AFTER_SECONDS", "180"))
WAYPOINTS_URL = "https://nes-lter-api.whoi.edu/api/stations/file.csv"
# /api/eta defaults when a client names no target or speed.
ETA_DEFAULT_TARGET = os.getenv("ETA_DEFAULT_TARGET", "auto")  # "auto" or a station id
ETA_PLANNED_SPEED_KN = os.getenv("ETA_PLANNED_SPEED_KN")  # unset: use recent mean SOG
ETA_SOG_MINUTES = float(os.getenv("ETA_SOG_MINUTES", "30"))


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


def _heading(row: dict, prev: dict | None) -> float | None:
    """COG when present, otherwise the direction of travel between the last two fixes."""
    if row.get("course_deg") is not None:
        return float(row["course_deg"])
    if prev is None:
        return None
    return bearing_deg(prev["latitude"], prev["longitude"], row["latitude"], row["longitude"])


async def _position_stream() -> AsyncIterator[str]:
    """Yield the latest position (and calculated heading) every LIVE_REFRESH_SECONDS."""
    while True:
        rows = await asyncio.to_thread(latest_positions, 2)
        row = rows[0] if rows else None
        if row:
            payload = {
                **_compact_position(row),
                "heading": _heading(row, rows[1] if len(rows) > 1 else None),
                "stale_after": STALE_AFTER_SECONDS,
            }
            yield f"data: {json.dumps(payload)}\n\n"
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
                # Strip: the source has IDs with trailing spaces ("MVCO  "),
                # which break ?station= lookups.
                "station": row["station"].strip(),
                "name": row["stationfullname"].strip(),
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


@app.get("/eta")
def eta_page() -> FileResponse:
    return FileResponse(LIVE_DIR / "eta.html")


@app.get("/eta.js")
def eta_script() -> FileResponse:
    """Target storage and formatting shared by the map and the /eta page."""
    return FileResponse(LIVE_DIR / "eta.js", media_type="text/javascript")


def _stations() -> list[dict[str, Any]]:
    """Station list for ETA lookups; 503 when the NES-LTER API can't be reached."""
    try:
        stations = load_waypoints()
    except Exception:
        stations = []
    if not stations:
        raise HTTPException(status_code=503, detail="Station list unavailable")
    return stations


def _speed(speed_kn: float | None, now: datetime) -> tuple[float | None, str]:
    """Planned speed if given (query, then ETA_PLANNED_SPEED_KN), else recent mean SOG."""
    if speed_kn is not None:
        return speed_kn, "planned"
    if ETA_PLANNED_SPEED_KN:
        return float(ETA_PLANNED_SPEED_KN), "planned"
    recent = track_positions(since=now - timedelta(minutes=ETA_SOG_MINUTES), max_points=10000)
    return mean_sog(recent), f"sog_{ETA_SOG_MINUTES:g}min"


def _epoch(t: datetime | None) -> int | None:
    return int(t.timestamp()) if t is not None else None


def _eta_fields(fix: dict | None, lat: float, lon: float, speed: float | None) -> dict:
    """distance/bearing/ttg/eta from the fix to (lat, lon); nulls without a fix.

    ETA counts from the fix time, not now, so clients can count down from it.
    """
    if fix is None:
        return {"distance_nm": None, "bearing_deg": None, "ttg_s": None, "eta": None, "eta_epoch": None}
    d = distance_nm(fix["latitude"], fix["longitude"], lat, lon)
    ttg = time_to_go(d, speed)
    eta = fix["display_time"] + timedelta(seconds=ttg) if ttg is not None else None
    return {
        "distance_nm": round(d, 2),
        "bearing_deg": round(bearing_deg(fix["latitude"], fix["longitude"], lat, lon), 1),
        "ttg_s": ttg,
        "eta": eta.isoformat() if eta else None,
        "eta_epoch": _epoch(eta),
    }


def _fix_fields(fix: dict | None, now: datetime) -> dict:
    t = fix["display_time"] if fix else None
    age = round((now - t).total_seconds()) if t else None
    return {
        "fix_time": t.isoformat() if t else None,
        "fix_epoch": _epoch(t),
        "fix_age_s": age,
        "stale": age is not None and age > STALE_AFTER_SECONDS,
    }


def _target(station: dict) -> dict:
    return {
        "station": station["station"],
        "name": station["name"],
        "lat": station["lat"],
        "lon": station["lon"],
    }


@app.get("/api/eta")
def eta(
    station: str | None = None,
    lat: float | None = Query(None, ge=-90, le=90),
    lon: float | None = Query(None, ge=-180, le=180),
    name: str | None = None,
    speed_kn: float | None = Query(None, gt=0, le=50),
) -> dict[str, Any]:
    """Distance, time to go, and ETA from the latest fix to a target.

    Target: `station`, or a point (`lat` + `lon`, optional `name`), or neither
    for ETA_DEFAULT_TARGET ("auto" predicts the nearest station ahead).
    Every key is always present (nulls when unknown) so small clients can parse it.
    """
    if (lat is None) != (lon is None):
        raise HTTPException(status_code=422, detail="lat and lon go together")
    now = datetime.now(timezone.utc)
    rows = latest_positions(2)
    fix = rows[0] if rows else None
    reason = None

    if lat is not None:
        target = {"mode": "point", "station": None, "name": name or "Point", "lat": lat, "lon": lon}
    else:
        wanted = station or (None if ETA_DEFAULT_TARGET == "auto" else ETA_DEFAULT_TARGET)
        stations = _stations()
        if wanted is not None:
            match = next((s for s in stations if s["station"] == wanted), None)
            if match is None:
                raise HTTPException(status_code=404, detail=f"Unknown station {wanted!r}")
            target = {"mode": "station", **_target(match)}
        elif fix is None:
            target, reason = None, "no position yet"
        else:
            heading = _heading(fix, rows[1] if len(rows) > 1 else None)
            match = predict_next(fix["latitude"], fix["longitude"], heading, stations)
            target = {"mode": "auto", **_target(match)} if match else None
            if target is None:
                reason = "no course yet" if heading is None else "no station ahead"

    speed, source = _speed(speed_kn, now)
    if target:
        fields = _eta_fields(fix, target["lat"], target["lon"], speed)
    else:
        fields = _eta_fields(None, 0.0, 0.0, speed)
    return {
        "target": target,
        "reason": reason,
        **fields,
        "speed_kn": round(speed, 2) if speed is not None else None,
        "speed_source": source,
        **_fix_fields(fix, now),
    }


@app.get("/api/eta/stations")
def eta_stations(speed_kn: float | None = Query(None, gt=0, le=50)) -> dict[str, Any]:
    """Every station with distance / time to go / ETA from the latest fix, nearest first.

    Wrapped in {"stations": [...]}: Connect IQ's JSON parsing expects an object at the top.
    """
    now = datetime.now(timezone.utc)
    stations = _stations()
    fix = latest_positions(1)
    fix = fix[0] if fix else None
    speed, _ = _speed(speed_kn, now)
    result = []
    for s in stations:
        fields = _eta_fields(fix, s["lat"], s["lon"], speed)
        result.append(
            {
                "station": s["station"],
                "name": s["name"],
                "distance_nm": fields["distance_nm"],
                "ttg_s": fields["ttg_s"],
                "eta": fields["eta"],
                "eta_epoch": fields["eta_epoch"],
            }
        )
    if fix is not None:
        result.sort(key=lambda r: r["distance_nm"])
    return {"stations": result}
