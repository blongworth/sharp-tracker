"""Fix handling for the Pico firmware, kept free of hardware imports.

Pure Python that runs unchanged on CircuitPython and CPython, so the logic is
unit-tested on a laptop (tests/test_pico.py). Mirrors what
sharp_tracker/collector.py does per line: merge the latest VTG/RMC speed and
course onto position fixes. Averaging happens next, in averaging.py (shared
with the collector).

Stick to syntax CircuitPython supports: no typing, dataclasses, or pathlib.
"""

from nmea_min import is_sentence, parse_line


class FixFilter:
    """Turns NMEA lines into position fixes with speed/course filled in.

    process() returns a position dict (without received_at, which the caller
    stamps) or None. After each call, `gps_time` is that line's UTC fix time
    (valid RMC) or None, for setting the clock, and `sentence_ok` says whether
    the line was checksum-valid NMEA (with or without a fix), so the caller
    can tell a GPS with no fix from garbled input.
    """

    def __init__(self):
        self.latest_nav = {}
        self.gps_time = None
        self.sentence_ok = False

    def process(self, text):
        self.gps_time = None
        self.sentence_ok = is_sentence(text)
        if not self.sentence_ok:
            return None
        nav, position = parse_line(text)
        if nav:
            for key, value in nav.items():
                if value is not None:
                    self.latest_nav[key] = value
        if position is None:
            return None
        self.gps_time = position.get("fix_time")

        # GGA has no speed/course; carry the last VTG/RMC values onto it.
        for key in ("speed_knots", "course_deg"):
            if position.get(key) is None and self.latest_nav.get(key) is not None:
                position[key] = self.latest_nav[key]
        return position


def fix_time_parts(fix_time):
    """'2026-11-15T12:35:19+00:00' -> (2026, 11, 15, 12, 35, 19), or None."""
    if not fix_time or len(fix_time) < 19:
        return None
    try:
        return (
            int(fix_time[0:4]),
            int(fix_time[5:7]),
            int(fix_time[8:10]),
            int(fix_time[11:13]),
            int(fix_time[14:16]),
            int(fix_time[17:19]),
        )
    except ValueError:
        return None


def iso_utc(parts):
    """(y, mo, d, h, mi, s) -> '2026-11-15T12:35:19+00:00'."""
    return "{:04d}-{:02d}-{:02d}T{:02d}:{:02d}:{:02d}+00:00".format(*parts[:6])


def weekday(year, month, day):
    """Monday=0 ... Sunday=6, like datetime.weekday(); needed to set the RTC."""
    # Sakamoto's method gives Sunday=0; shift to Monday=0.
    offsets = (0, 3, 2, 5, 0, 3, 5, 1, 4, 6, 2, 4)
    if month < 3:
        year -= 1
    sunday_first = (year + year // 4 - year // 100 + year // 400 + offsets[month - 1] + day) % 7
    return (sunday_first + 6) % 7


class FixBuffer:
    """Unsent fixes in RAM, oldest first, bounded by count and free memory.

    When full, the oldest fix is dropped: the newest positions matter most for
    a live tracker. Batches are taken newest first so the map catches up as
    soon as the link returns.
    """

    def __init__(self, max_fixes, min_free_bytes):
        self.max_fixes = max_fixes
        self.min_free_bytes = min_free_bytes
        self.fixes = []
        self.dropped = 0

    def __len__(self):
        return len(self.fixes)

    def add(self, fix, free_bytes=None):
        """Append a fix; drop the oldest if over max_fixes or memory is low.

        `free_bytes` is gc.mem_free() on the board (None to skip the check).
        Dropping one per add keeps the size steady while memory stays low.
        """
        self.fixes.append(fix)
        low_memory = free_bytes is not None and free_bytes < self.min_free_bytes
        if len(self.fixes) > 1 and (len(self.fixes) > self.max_fixes or low_memory):
            self.fixes.pop(0)
            self.dropped += 1
            return True
        return False

    def newest(self, n):
        """Up to n newest fixes, oldest first (a batch to send)."""
        return self.fixes[-n:] if n else []

    def remove_newest(self, n):
        """Forget the n newest fixes after they were sent."""
        if n:
            del self.fixes[-n:]
