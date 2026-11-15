"""Minimal NMEA 0183 parser for CircuitPython (no external dependencies).

Mirrors the field set produced by sharp_tracker/nmea.py on the desktop side,
so code.py can POST positions straight to the ingest API's PositionIn schema.
"""


def parse_line(line):
    """Parse one NMEA sentence.

    Returns (navigation, position); either may be None. navigation carries
    speed/course from sentences with no position of their own (VTG).
    position is a dict with everything the ingest API needs except
    received_at, which the caller stamps with the current wall-clock time.
    """
    raw = line.strip()
    if not raw.startswith("$") or "*" not in raw or not _checksum_ok(raw):
        return None, None

    fields = raw[1:].split("*")[0].split(",")
    header = fields[0]
    if len(header) < 5:  # e.g. "GPGGA" ("$" already stripped)
        return None, None
    talker = header[0:2]
    sentence_type = header[2:5]

    try:
        if sentence_type == "VTG":
            return _parse_vtg(fields), None
        if sentence_type == "GGA":
            return None, _parse_gga(fields, talker, raw)
        if sentence_type == "RMC":
            # RMC speed/course also feed navigation, like nmea.py, so they
            # carry forward onto later GGA fixes.
            speed = _float_or_none(fields[7]) if len(fields) > 7 else None
            course = _float_or_none(fields[8]) if len(fields) > 8 else None
            nav = {"speed_knots": speed, "course_deg": course} if speed is not None or course is not None else None
            return nav, _parse_rmc(fields, talker, raw)
    except (IndexError, ValueError):
        return None, None
    return None, None


def _checksum_ok(sentence):
    body, _, cksum = sentence[1:].partition("*")
    calc = 0
    for ch in body:
        calc ^= ord(ch)
    try:
        return calc == int(cksum.strip()[:2], 16)
    except ValueError:
        return False


def _to_decimal(value, hemisphere):
    if not value or not hemisphere:
        return None
    dot = value.find(".")
    if dot < 2:
        return None
    degrees = float(value[: dot - 2])
    minutes = float(value[dot - 2 :])
    decimal = degrees + minutes / 60.0
    return -decimal if hemisphere in ("S", "W") else decimal


def _float_or_none(value):
    try:
        return float(value) if value else None
    except ValueError:
        return None


def _int_or_none(value):
    try:
        return int(value) if value else None
    except ValueError:
        return None


def _fix_datetime(time_str, date_str):
    if not time_str or not date_str or len(time_str) < 6 or len(date_str) != 6:
        return None
    hh, mm, ss = time_str[0:2], time_str[2:4], time_str[4:6]
    dd, mo, yy = date_str[0:2], date_str[2:4], date_str[4:6]
    return "{:04d}-{:02d}-{:02d}T{:02d}:{:02d}:{:02d}+00:00".format(
        2000 + int(yy), int(mo), int(dd), int(hh), int(mm), int(ss)
    )


def _parse_gga(f, talker, raw):
    # $--GGA,time,lat,N,lon,E,quality,numSat,hdop,alt,M,geoid,M,age,stationId
    lat = _to_decimal(f[2], f[3])
    lon = _to_decimal(f[4], f[5])
    if lat is None or lon is None or (lat == 0 and lon == 0):
        return None
    if _int_or_none(f[6]) == 0:
        return None  # quality 0 = no fix
    return {
        "fix_time": None,
        "talker": talker,
        "sentence_type": "GGA",
        "latitude": lat,
        "longitude": lon,
        "altitude_m": _float_or_none(f[9]) if len(f) > 9 else None,
        "speed_knots": None,
        "course_deg": None,
        "quality": _int_or_none(f[6]) if len(f) > 6 else None,
        "satellites": _int_or_none(f[7]) if len(f) > 7 else None,
        "raw": raw,
    }


def _parse_rmc(f, talker, raw):
    # $--RMC,time,status,lat,N,lon,E,speed,course,date,magvar,E/W,mode
    if len(f) < 10 or f[2] != "A":
        return None
    lat = _to_decimal(f[3], f[4])
    lon = _to_decimal(f[5], f[6])
    if lat is None or lon is None or (lat == 0 and lon == 0):
        return None
    return {
        "fix_time": _fix_datetime(f[1], f[9]),
        "talker": talker,
        "sentence_type": "RMC",
        "latitude": lat,
        "longitude": lon,
        "altitude_m": None,
        "speed_knots": _float_or_none(f[7]),
        "course_deg": _float_or_none(f[8]),
        "quality": None,
        "satellites": None,
        "raw": raw,
    }


def _parse_vtg(f):
    # $--VTG,track,T,magtrack,M,speedKnots,N,speedKmh,K,mode
    course = _float_or_none(f[1]) if len(f) > 1 else None
    if course is None and len(f) > 3:
        course = _float_or_none(f[3])  # magnetic track fallback, as in nmea.py
    speed = _float_or_none(f[5]) if len(f) > 5 else None
    if course is None and speed is None:
        return None
    return {"speed_knots": speed, "course_deg": course}
