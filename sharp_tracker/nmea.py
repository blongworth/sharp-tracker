from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pynmea2


def _iso_utc(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _fix_datetime(msg: Any) -> str | None:
    """Return an ISO UTC timestamp from NMEA date/time fields when available."""
    timestamp = getattr(msg, "timestamp", None)
    datestamp = getattr(msg, "datestamp", None)
    if timestamp and datestamp:
        return _iso_utc(datetime.combine(datestamp, timestamp, tzinfo=timezone.utc))
    return None


def parse_line(line: str, received_at: datetime | None = None) -> tuple[dict | None, dict | None]:
    """Parse an NMEA sentence once.

    Returns ``(navigation, position)`` where each is a normalized dict or
    None. Navigation holds speed/course (VTG etc.); position holds a
    position-bearing row (GGA/RMC etc.). Invalid lines yield ``(None, None)``.
    """
    raw = line.strip()
    if not raw:
        return None, None

    try:
        msg = pynmea2.parse(raw)
    except pynmea2.ParseError:
        return None, None

    return _extract_navigation(msg), _extract_position(msg, raw, received_at)


def _extract_navigation(msg: Any) -> dict | None:
    speed_knots = _float_or_none(getattr(msg, "spd_over_grnd_kts", None))
    if speed_knots is None:
        speed_knots = _float_or_none(getattr(msg, "spd_over_grnd", None))
    course_deg = _float_or_none(getattr(msg, "true_track", None))
    if course_deg is None:
        course_deg = _float_or_none(getattr(msg, "true_course", None))
    if course_deg is None:
        # Some feeds only populate magnetic VTG track. It is still useful for
        # orienting the dashboard vessel icon when true COG is absent.
        course_deg = _float_or_none(getattr(msg, "mag_track", None))

    if speed_knots is None and course_deg is None:
        return None
    return {"speed_knots": speed_knots, "course_deg": course_deg}


def _extract_position(msg: Any, raw: str, received_at: datetime | None) -> dict | None:
    received_at = received_at or datetime.now(timezone.utc)

    lat = getattr(msg, "latitude", None)
    lon = getattr(msg, "longitude", None)
    if lat is None or lon is None or lat == 0 or lon == 0:
        return None

    return {
        "received_at": _iso_utc(received_at),
        "fix_time": _fix_datetime(msg),
        "talker": getattr(msg, "talker", None),
        "sentence_type": getattr(msg, "sentence_type", None),
        "latitude": float(lat),
        "longitude": float(lon),
        "altitude_m": _float_or_none(getattr(msg, "altitude", None)),
        "speed_knots": _float_or_none(getattr(msg, "spd_over_grnd", None)),
        "course_deg": _float_or_none(getattr(msg, "true_course", None)),
        "quality": _int_or_none(getattr(msg, "gps_qual", None)),
        "satellites": _int_or_none(getattr(msg, "num_sats", None)),
        "raw": raw,
    }


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
