"""Generate valid 1 Hz GGA/RMC sentences for tests (no strftime: see sentences())."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

START = datetime(2026, 11, 15, 12, 0, 0, tzinfo=timezone.utc)


def nmea(body: str) -> str:
    check = 0
    for ch in body:
        check ^= ord(ch)
    return f"${body}*{check:02X}\r\n"


def sentences(second: int, rmc: bool = True, start: datetime = START) -> bytes:
    """GGA (+ RMC) for `second` seconds after `start`, latitude creeping north.

    Formats fields by hand: datetime formatting calls time.strftime, and the
    Pico simulator stubs out the time module while the firmware runs.
    """
    t = start + timedelta(seconds=second)
    hms = "%02d%02d%02d" % (t.hour, t.minute, t.second)
    dmy = "%02d%02d%02d" % (t.day, t.month, t.year % 100)
    lat = "41%07.4f" % (31.56 + second * 0.001)
    lines = [nmea(f"GPGGA,{hms}.00,{lat},N,07040.3800,W,1,08,0.9,10.5,M,-34.0,M,,")]
    if rmc:
        lines.append(nmea(f"GPRMC,{hms}.00,A,{lat},N,07040.3800,W,8.5,045.0,{dmy},,,A"))
    return "".join(lines).encode()
