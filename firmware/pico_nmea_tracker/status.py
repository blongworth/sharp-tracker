"""Health tracking for the Pico firmware: console status line and LED blink codes.

Pure Python with no imports, so it runs unchanged on CircuitPython and is
unit-tested on a laptop (tests/test_pico.py). code.py feeds it events and
drives the LED from led_on().

Stick to syntax CircuitPython supports: no typing, dataclasses, or pathlib.
"""

# GPS input counts as current for this long after the last byte/sentence/fix.
GPS_STALE_SECONDS = 10

# LED codes: blinks per frame. Higher GPS codes outrank the network code.
LED_OK = 1  # short heartbeat blink
LED_NETWORK = 2  # WiFi down or sends failing; fixes queue in RAM
LED_NO_FIX = 3  # GPS talking but no usable fix (or no date to set the clock)
LED_NO_GPS = 4  # no GPS data, or garbled (wiring/baud)
LED_FLICKER = -1  # fatal error, about to reboot

LED_FRAME_SECONDS = 3.0
LED_HEARTBEAT_SECONDS = 0.1
LED_PULSE_ON_SECONDS = 0.2
LED_PULSE_PERIOD_SECONDS = 0.5
LED_FLICKER_SECONDS = 0.05

LAST_LINE_CHARS = 60


class Health:
    """GPS, network, and clock state from events reported by code.py."""

    def __init__(self):
        self.last_bytes_at = None
        self.last_sentence_at = None
        self.last_fix_at = None
        self.last_line = ""
        self.send_ok = None  # None until the first send attempt
        self.send_error = None  # reason the last send failed
        self.clock_source = "unset"  # "ntp" or "gps" once set
        self._reset_counts()

    def _reset_counts(self):
        self.bytes = 0
        self.lines = 0
        self.fixes = 0

    def on_bytes(self, now, n):
        if n:
            self.bytes += n
            self.last_bytes_at = now

    def on_line(self, now, text, sentence_ok, position):
        """One line from the UART; sentence_ok = checksum-valid NMEA."""
        self.lines += 1
        self.last_line = text
        if sentence_ok:
            self.last_sentence_at = now
        if position is not None:
            self.fixes += 1
            self.last_fix_at = now

    def on_send(self, error):
        """Result of a send attempt: None on success, else a short reason."""
        self.send_ok = error is None
        self.send_error = error

    def gps_state(self, now):
        """'ok', 'no_fix' (valid NMEA, no fix), 'garbled' (bytes, no valid NMEA), or 'no_data'."""
        if _recent(self.last_fix_at, now):
            return "ok"
        if _recent(self.last_sentence_at, now):
            return "no_fix"
        if _recent(self.last_bytes_at, now):
            return "garbled"
        return "no_data"

    def led_code(self, now, wifi_up):
        return led_code(self.gps_state(now), wifi_up, self.send_ok, self.clock_source != "unset")

    def status_line(self, now, wifi_up, queued, mem_free):
        """One console line summarizing the interval since the last call; resets the counts."""
        gps = self.gps_state(now)
        gps_part = "gps={} lines={} fixes={}".format(gps, self.lines, self.fixes)
        if gps != "ok":
            gps_part += " bytes={}".format(self.bytes)
            last = _printable(self.last_line)
            if last:
                gps_part += " last={}".format(last)
        if self.send_ok is None:
            send = "-"
        elif self.send_ok:
            send = "ok"
        else:
            send = "failing ({})".format(self.send_error)
        self._reset_counts()
        return "Status: {} | wifi={} send={} queued={} | clock={} | mem={}".format(
            gps_part,
            "up" if wifi_up else "down",
            send,
            queued,
            self.clock_source,
            "-" if mem_free is None else mem_free,
        )


def led_code(gps, wifi_up, send_ok, clock_set):
    """The most severe problem wins: no GPS > no usable fix > network > OK."""
    if gps in ("no_data", "garbled"):
        return LED_NO_GPS
    if gps == "no_fix" or not clock_set:
        return LED_NO_FIX
    if not wifi_up or send_ok is False:
        return LED_NETWORK
    return LED_OK


def led_on(code, t):
    """LED state for blink `code` at monotonic time t (seconds)."""
    if code == LED_FLICKER:
        return int(t / LED_FLICKER_SECONDS) % 2 == 0
    phase = t % LED_FRAME_SECONDS
    if code <= LED_OK:
        return phase < LED_HEARTBEAT_SECONDS
    if phase >= code * LED_PULSE_PERIOD_SECONDS:
        return False
    return phase % LED_PULSE_PERIOD_SECONDS < LED_PULSE_ON_SECONDS


def _recent(at, now):
    return at is not None and now - at <= GPS_STALE_SECONDS


def _printable(text):
    text = text.strip()[:LAST_LINE_CHARS]
    return "".join(c if " " <= c <= "~" else "?" for c in text)
