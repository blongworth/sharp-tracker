"""SHARP Tracker Pico W firmware: NMEA serial -> ingest API bridge.

Reads NMEA from a GPS/AIS source on UART0, averages fixes into UTC-aligned
windows (AVERAGE_SECONDS, default 60), and POSTs them in batches to the same
ingest endpoint the desktop collector uses. Fixes that can't be sent
wait in RAM (bounded; oldest dropped first) and go out when the link returns.
Configure via settings.toml (see settings.toml.example).

Hardware glue only; the testable logic lives in fixes.py, nmea_min.py, and
averaging.py (copied from sharp_tracker/; shared with the collector).
"""

import gc
import os
import time
import traceback

import board
import busio
import digitalio
import microcontroller
import rtc
import socketpool
import ssl
import watchdog
import wifi

import adafruit_ntp
import adafruit_requests

from averaging import Averager, epoch_from_parts
from fixes import FixBuffer, FixFilter, ascii_text, fix_time_parts, iso_utc, weekday
from status import LED_FLICKER, Health, led_on

UART_TX = board.GP0
UART_RX = board.GP1

BAUD = int(os.getenv("NMEA_SERIAL_BAUD", "4800"))
# Average 1 Hz fixes into UTC-aligned windows of this many seconds; 0 sends
# every fix (both halves of each GGA/RMC pair; RAM fills ~120x faster).
AVERAGE_SECONDS = int(os.getenv("AVERAGE_SECONDS", "60"))
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "20"))
FLUSH_SECONDS = float(os.getenv("FLUSH_SECONDS", "5"))
# Unsent fixes kept in RAM (500 at 60 s is ~8 h); oldest dropped beyond this,
# or sooner if free memory falls below MIN_FREE_BYTES.
MAX_BUFFER = int(os.getenv("MAX_BUFFER", "500"))
MIN_FREE_BYTES = int(os.getenv("MIN_FREE_BYTES", "30000"))
# Batches sent per flush; small so the UART is read between sends while catching up.
SEND_BATCHES_PER_FLUSH = int(os.getenv("SEND_BATCHES_PER_FLUSH", "3"))
# RP2040 maximum is about 8.3 s. 0 disables (handy while debugging over USB).
WATCHDOG_SECONDS = float(os.getenv("WATCHDOG_SECONDS", "8"))
# Console status line interval (seconds); 0 disables.
STATUS_SECONDS = float(os.getenv("STATUS_SECONDS", "60"))
HTTP_TIMEOUT_SECONDS = 5
# Longest a UART read waits for data, so the loop (and the LED) runs at ~20 Hz.
UART_POLL_SECONDS = 0.05
WIFI_CONNECT_TIMEOUT_SECONDS = 5  # below the watchdog timeout
WIFI_RETRY_SECONDS = 30
MAX_RETRY_SECONDS = 60
CLOCK_RESYNC_SECONDS = 3600

WIFI_SSID = os.getenv("CIRCUITPY_WIFI_SSID")
WIFI_PASSWORD = os.getenv("CIRCUITPY_WIFI_PASSWORD")
INGEST_URL = os.getenv("TRACKER_API_URL")
INGEST_TOKEN = os.getenv("TRACKER_INGEST_TOKEN")


health = Health()

_led_pin = getattr(board, "LED", None)
if _led_pin is not None:
    led = digitalio.DigitalInOut(_led_pin)
    led.direction = digitalio.Direction.OUTPUT
else:
    led = None
_led_state = None


def set_led(on):
    """Write the LED only on change: on a Pico W each write goes to the WiFi chip."""
    global _led_state
    if led is not None and on != _led_state:
        led.value = on
        _led_state = on


def feed_watchdog():
    if WATCHDOG_SECONDS:
        microcontroller.watchdog.feed()


def start_watchdog():
    """Reboot if the main loop hangs (e.g. a stuck network call)."""
    if WATCHDOG_SECONDS:
        microcontroller.watchdog.timeout = WATCHDOG_SECONDS
        microcontroller.watchdog.mode = watchdog.WatchDogMode.RESET
        microcontroller.watchdog.feed()


def clock_is_set():
    return time.localtime().tm_year >= 2024


def iso_utc_now():
    t = time.localtime()
    return iso_utc((t.tm_year, t.tm_mon, t.tm_mday, t.tm_hour, t.tm_min, t.tm_sec))


def epoch_now():
    """UTC epoch seconds from the RTC (computed, so no port's time.time() epoch matters)."""
    t = time.localtime()
    return epoch_from_parts(t.tm_year, t.tm_mon, t.tm_mday, t.tm_hour, t.tm_min, t.tm_sec)


def set_clock(parts):
    year, month, day, hour, minute, second = parts
    rtc.RTC().datetime = time.struct_time(
        (year, month, day, hour, minute, second, weekday(year, month, day), -1, -1)
    )


def sync_clock_ntp(pool):
    """Best effort: a ship often has no NTP; GPS time (RMC) takes over then."""
    try:
        rtc.RTC().datetime = adafruit_ntp.NTP(pool, tz_offset=0).datetime
        health.clock_source = "ntp"
        print("Clock set from NTP:", iso_utc_now())
        return True
    except Exception as exc:
        print("NTP unavailable; will set clock from GPS:", exc)
        return False


_last_wifi_attempt = None


def ensure_wifi():
    """True if connected. Otherwise tries to reconnect, at most every
    WIFI_RETRY_SECONDS and with a timeout below the watchdog's, so logging
    carries on through WiFi outages."""
    global _last_wifi_attempt
    if wifi.radio.connected:
        return True
    now = time.monotonic()
    if _last_wifi_attempt is not None and now - _last_wifi_attempt < WIFI_RETRY_SECONDS:
        return False
    _last_wifi_attempt = now
    print("Connecting to WiFi:", WIFI_SSID)
    feed_watchdog()
    try:
        wifi.radio.connect(WIFI_SSID, WIFI_PASSWORD, timeout=WIFI_CONNECT_TIMEOUT_SECONDS)
    except Exception as exc:
        print("WiFi unavailable:", exc)
        return False
    finally:
        feed_watchdog()
    print("Connected, IP:", wifi.radio.ipv4_address)
    return True


def send_batch(session, batch):
    """Deliver one batch; True when the server accepted it.

    The only transport-specific code in the firmware: an MQTT publish would
    replace this function, with everything else unchanged.
    """
    headers = {"Content-Type": "application/json"}
    if INGEST_TOKEN:
        headers["Authorization"] = "Bearer " + INGEST_TOKEN
    endpoint = INGEST_URL.rstrip("/") + "/positions/batch"
    response = session.post(
        endpoint, json={"positions": batch}, headers=headers, timeout=HTTP_TIMEOUT_SECONDS
    )
    try:
        return response.status_code == 201
    finally:
        response.close()


def free_memory():
    """Free heap in bytes on the board; None where gc.mem_free doesn't exist."""
    gc.collect()
    mem_free = getattr(gc, "mem_free", None)
    return mem_free() if mem_free else None


def flush(session, pending):
    """Send up to SEND_BATCHES_PER_FLUSH batches from RAM, newest first.

    Returns True unless a send failed (or WiFi is down); unsent fixes stay
    in `pending` either way.
    """
    if not ensure_wifi():
        return False
    for _ in range(SEND_BATCHES_PER_FLUSH):
        batch = pending.newest(BATCH_SIZE)
        if not batch:
            break
        feed_watchdog()
        try:
            ok = send_batch(session, batch)
        except Exception as exc:
            print("Send failed:", exc)
            ok = False
        feed_watchdog()
        if not ok:
            return False
        pending.remove_newest(len(batch))
    return True


def queue_fix(pending, position):
    """Stamp received_at and add to the RAM buffer for sending."""
    position["received_at"] = iso_utc_now()
    if pending.add(position, free_memory()) and pending.dropped % 60 == 1:
        print("RAM buffer full; dropped", pending.dropped, "oldest fixes so far")
    print("Fix: {:.5f}, {:.5f} {}".format(position["latitude"], position["longitude"], position["raw"][:24]))


def run():
    if not INGEST_URL:
        raise RuntimeError("TRACKER_API_URL must be set in settings.toml")

    pool = socketpool.SocketPool(wifi.radio)
    session = adafruit_requests.Session(pool, ssl.create_default_context())
    clock_synced_at = None
    if ensure_wifi() and sync_clock_ntp(pool):
        clock_synced_at = time.monotonic()

    # A larger receive buffer (several seconds of NMEA) rides out slow sends.
    uart = busio.UART(UART_TX, UART_RX, baudrate=BAUD, timeout=UART_POLL_SECONDS, receiver_buffer_size=2048)
    fixes = FixFilter()
    averager = Averager(AVERAGE_SECONDS) if AVERAGE_SECONDS > 0 else None
    pending = FixBuffer(MAX_BUFFER, MIN_FREE_BYTES)
    next_flush = time.monotonic() + FLUSH_SECONDS
    retry_seconds = FLUSH_SECONDS
    line_buf = b""
    next_status = time.monotonic() + STATUS_SECONDS
    wifi_up = wifi.radio.connected
    next_wifi_check = 0

    start_watchdog()
    while True:
        feed_watchdog()
        # Take what's waiting without blocking; with nothing waiting, read(1)
        # waits at most UART_POLL_SECONDS, which paces the loop.
        data = uart.read(min(uart.in_waiting, 256) or 1)
        if data:
            health.on_bytes(time.monotonic(), len(data))
            line_buf += data
            if len(line_buf) > 1024 and b"\n" not in line_buf:
                line_buf = line_buf[-256:]  # garbled input with no line ends
            while b"\n" in line_buf:
                raw_line, _, line_buf = line_buf.partition(b"\n")
                text = ascii_text(raw_line)
                try:
                    position = fixes.process(text)
                except Exception:
                    position = None
                health.on_line(time.monotonic(), text, fixes.sentence_ok, position)

                # GPS time is authoritative UTC: set (and hourly re-set) the RTC from
                # any valid RMC.
                if fixes.gps_time and (
                    clock_synced_at is None or time.monotonic() - clock_synced_at > CLOCK_RESYNC_SECONDS
                ):
                    parts = fix_time_parts(fixes.gps_time)
                    if parts:
                        set_clock(parts)
                        clock_synced_at = time.monotonic()
                        health.clock_source = "gps"
                        print("Clock set from GPS:", iso_utc_now())
                if position is None or not clock_is_set():
                    continue  # no clock yet: received_at would be 2000-01-01

                if averager:
                    position = averager.add(epoch_now(), position)
                    if position is None:
                        continue  # window still filling
                queue_fix(pending, position)

        # Close a due window even when the GPS is quiet or has no valid fix
        # (data can keep flowing with RMC status V, so add() alone won't).
        if averager and clock_is_set():
            position = averager.close_due(epoch_now())
            if position:
                queue_fix(pending, position)

        now = time.monotonic()
        link_ok = retry_seconds == FLUSH_SECONDS
        # A full batch flushes early only while sends succeed; otherwise honor the backoff.
        if len(pending) and (now >= next_flush or (link_ok and len(pending) >= BATCH_SIZE)):
            ok = flush(session, pending)
            health.on_send(ok)
            # Back off while sends fail so a dead link doesn't stall UART reads every flush.
            retry_seconds = FLUSH_SECONDS if ok else min(retry_seconds * 2, MAX_RETRY_SECONDS)
            next_flush = now + retry_seconds

        if now >= next_wifi_check:  # a query to the WiFi chip; once a second is plenty
            wifi_up = wifi.radio.connected
            next_wifi_check = now + 1
        if STATUS_SECONDS and now >= next_status:
            print(health.status_line(now, wifi_up, len(pending), free_memory()))
            next_status = now + STATUS_SECONDS
        set_led(led_on(health.led_code(now, wifi_up), now))


set_led(True)  # solid on while booting (WiFi, NTP), until the main loop runs
try:
    run()
except Exception as exc:
    # Unexpected error (WiFi outages are handled in the loop). Reboot cleanly
    # rather than retry in place. Unsent fixes in RAM are lost.
    # repr and traceback: some errors (e.g. MemoryError) have an empty message.
    print("Fatal error, rebooting in 5 s:", repr(exc))
    traceback.print_exception(exc)
    flicker_until = time.monotonic() + 5  # under the 8 s watchdog
    while time.monotonic() < flicker_until:
        set_led(led_on(LED_FLICKER, time.monotonic()))
        time.sleep(0.02)
    microcontroller.reset()
