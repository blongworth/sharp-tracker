"""Distance, bearing, next-station prediction, and time to go. Pure functions, no I/O.

Used by live.py's /api/eta endpoints. Distances are nautical miles, speeds
knots, angles degrees true (0 = north, clockwise), times seconds.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any, Iterable

EARTH_RADIUS_NM = 3440.065
# Below this speed the ship is treated as stopped: no time to go.
MIN_SPEED_KN = 0.5


def distance_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle (haversine) distance in nautical miles."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat = p2 - p1
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_NM * math.asin(math.sqrt(a))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial great-circle bearing (0-360) from point 1 to point 2."""
    y = math.sin(math.radians(lon2 - lon1)) * math.cos(math.radians(lat2))
    x = math.cos(math.radians(lat1)) * math.sin(math.radians(lat2)) - math.sin(
        math.radians(lat1)
    ) * math.cos(math.radians(lat2)) * math.cos(math.radians(lon2 - lon1))
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def angle_between(a: float, b: float) -> float:
    """Smallest difference between two bearings, 0-180."""
    return abs((a - b + 180) % 360 - 180)


def predict_next(
    lat: float,
    lon: float,
    cog: float | None,
    stations: Iterable[dict[str, Any]],
    cone_deg: float = 30.0,
    arrive_nm: float = 0.5,
) -> dict[str, Any] | None:
    """Nearest station within +/-cone_deg of the course, more than arrive_nm away.

    Stations need "lat" and "lon". Returns None when the course is unknown or
    no station is ahead. The arrive_nm gap skips the station the ship is on.
    """
    if cog is None:
        return None
    best, best_d = None, math.inf
    for station in stations:
        d = distance_nm(lat, lon, station["lat"], station["lon"])
        if d <= arrive_nm or d >= best_d:
            continue
        if angle_between(bearing_deg(lat, lon, station["lat"], station["lon"]), cog) <= cone_deg:
            best, best_d = station, d
    return best


def mean_sog(positions: Iterable[dict[str, Any]]) -> float | None:
    """Mean of the non-null speed_knots values, or None if there are none."""
    speeds = [p["speed_knots"] for p in positions if p.get("speed_knots") is not None]
    return sum(speeds) / len(speeds) if speeds else None


def vmg(sog: float | None, cog: float | None, bearing: float) -> float | None:
    """Velocity made good toward `bearing` (knots, negative when moving away)."""
    if sog is None or cog is None:
        return None
    return sog * math.cos(math.radians(angle_between(cog, bearing)))


def next_fix_time(received: list[datetime]) -> datetime | None:
    """When the source should produce its next fix, from recent received_at times (newest first).

    received_at is stamped when each averaging window closes, so the gaps are
    regular: the next fix is the newest plus the median gap (the median skips a
    one-off outage). None with fewer than two fixes.
    """
    gaps = sorted((a - b).total_seconds() for a, b in zip(received, received[1:]) if a > b)
    if not gaps:
        return None
    return received[0] + timedelta(seconds=gaps[len(gaps) // 2])


def time_to_go(distance: float | None, speed_kn: float | None) -> int | None:
    """Seconds to cover `distance` nm at `speed_kn`; None when stopped or unknown."""
    if distance is None or speed_kn is None or speed_kn < MIN_SPEED_KN:
        return None
    return round(distance / speed_kn * 3600)
