"""Average 1 Hz GPS fixes into UTC-aligned windows (default 60 s).

Shared by the desktop collector and the Pico firmware: this file is copied
as-is to the Pico, so it must stay CircuitPython-compatible. It imports only
`math` (tests/test_pico.py enforces this) and avoids typing, dataclasses, and
datetime.

Windows are aligned to the UTC clock (12:00:00-12:00:59, 12:01:00-...). Each
averaged fix is stamped with the window midpoint as `fix_time`; the caller
stamps `received_at` when it stores or queues the fix.

- latitude/longitude/altitude: arithmetic mean (longitude unwrapped across
  +/-180 relative to the window's first fix)
- speed_knots: mean SOG
- course_deg: direction of the mean velocity vector (SOG-weighted COG), so
  359 and 1 average to 0, not 180; None when stationary or no COG
- quality, satellites: worst (minimum) seen in the window
- raw: a summary like "AVG 60s n=58 2026-11-15T12:00:00Z/2026-11-15T12:01:00Z"
  (not NMEA: there is no single source sentence). With received_at it keeps
  rows unique for the database's (received_at, raw) index.
"""

import math


class Averager:
    def __init__(self, window_seconds):
        self.window = int(window_seconds)
        if self.window < 1:
            raise ValueError("window_seconds must be >= 1")
        self._reset()

    def add(self, t, position):
        """Add a fix taken at `t` (UTC epoch seconds).

        Returns the previous window's averaged position once `t` falls in a
        new window, else None. Only the first fix of each second counts, so a
        GGA/RMC pair for the same second is one sample.
        """
        second = int(t)
        start = second - second % self.window
        done = None
        if self._start is not None and start != self._start:
            done = self._result()
            self._reset()
        if self._start is None:
            self._start = start
        if second == self._last_second:
            return done
        self._last_second = second
        self._accumulate(position)
        return done

    def _reset(self):
        self._start = None
        self._last_second = None
        self._n = 0
        self._lat = 0.0
        self._lon = 0.0
        self._lon0 = None
        self._alt = 0.0
        self._alt_n = 0
        self._sog = 0.0
        self._sog_n = 0
        self._ve = 0.0
        self._vn = 0.0
        self._cog_n = 0
        self._quality = None
        self._satellites = None
        self._talker = None

    def _accumulate(self, p):
        self._n += 1
        self._lat += p["latitude"]
        lon = p["longitude"]
        if self._lon0 is None:
            self._lon0 = lon
        # Unwrap so a window straddling the antimeridian doesn't average to ~0.
        self._lon += lon + 360.0 * round((self._lon0 - lon) / 360.0)
        if p.get("altitude_m") is not None:
            self._alt += p["altitude_m"]
            self._alt_n += 1
        sog = p.get("speed_knots")
        if sog is not None:
            self._sog += sog
            self._sog_n += 1
        cog = p.get("course_deg")
        if cog is not None:
            weight = sog if sog is not None else 1.0
            self._ve += weight * math.sin(math.radians(cog))
            self._vn += weight * math.cos(math.radians(cog))
            self._cog_n += 1
        self._quality = _min(self._quality, p.get("quality"))
        self._satellites = _min(self._satellites, p.get("satellites"))
        if self._talker is None:
            self._talker = p.get("talker")

    def _result(self):
        n = self._n
        lon = self._lon / n
        lon = (lon + 180.0) % 360.0 - 180.0
        course = None
        if self._cog_n and math.hypot(self._ve, self._vn) > 1e-9:
            course = math.degrees(math.atan2(self._ve, self._vn)) % 360.0
        start = self._start
        return {
            "fix_time": iso_utc_from_epoch(start + self.window // 2),
            "talker": self._talker,
            "sentence_type": "AVG",
            "latitude": self._lat / n,
            "longitude": lon,
            "altitude_m": self._alt / self._alt_n if self._alt_n else None,
            "speed_knots": self._sog / self._sog_n if self._sog_n else None,
            "course_deg": course,
            "quality": self._quality,
            "satellites": self._satellites,
            "raw": "AVG {}s n={} {}/{}".format(
                self.window,
                n,
                iso_utc_from_epoch(start)[:19] + "Z",
                iso_utc_from_epoch(start + self.window)[:19] + "Z",
            ),
        }


def _min(current, value):
    if value is None:
        return current
    return value if current is None else min(current, value)


def epoch_from_parts(year, month, day, hour, minute, second):
    """UTC civil time -> epoch seconds (Howard Hinnant's days_from_civil)."""
    y = year - 1 if month <= 2 else year
    era = (y if y >= 0 else y - 399) // 400
    yoe = y - era * 400
    doy = (153 * (month + (-3 if month > 2 else 9)) + 2) // 5 + day - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    days = era * 146097 + doe - 719468
    return days * 86400 + hour * 3600 + minute * 60 + second


def iso_utc_from_epoch(seconds):
    """Epoch seconds -> '2026-11-15T12:00:30+00:00' (Hinnant's civil_from_days)."""
    seconds = int(seconds)
    days, rem = divmod(seconds, 86400)
    z = days + 719468
    era = (z if z >= 0 else z - 146096) // 146097
    doe = z - era * 146097
    yoe = (doe - doe // 1460 + doe // 36524 - doe // 146096) // 365
    doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
    mp = (5 * doy + 2) // 153
    day = doy - (153 * mp + 2) // 5 + 1
    month = mp + 3 if mp < 10 else mp - 9
    year = yoe + era * 400 + (1 if month <= 2 else 0)
    return "{:04d}-{:02d}-{:02d}T{:02d}:{:02d}:{:02d}+00:00".format(
        year, month, day, rem // 3600, rem % 3600 // 60, rem % 60
    )
