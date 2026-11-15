"""SHARP Tracker Pico W firmware: NMEA serial -> ingest API bridge.

Reads NMEA sentences from a GPS/AIS source wired to UART0, batches parsed
positions, and POSTs them to the same ingest API the desktop collector uses
(sharp_tracker.api). Configure via settings.toml (see settings.toml.example).
"""

import os
import time

import board
import busio
import rtc
import socketpool
import ssl
import wifi

import adafruit_ntp
import adafruit_requests

from nmea_min import parse_line

UART_TX = board.GP0
UART_RX = board.GP1

BAUD = int(os.getenv("NMEA_SERIAL_BAUD", "4800"))
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "20"))
FLUSH_SECONDS = float(os.getenv("FLUSH_SECONDS", "5"))
MAX_BUFFER = int(os.getenv("MAX_BUFFER", "500"))

WIFI_SSID = os.getenv("CIRCUITPY_WIFI_SSID")
WIFI_PASSWORD = os.getenv("CIRCUITPY_WIFI_PASSWORD")
INGEST_URL = os.getenv("TRACKER_API_URL")
INGEST_TOKEN = os.getenv("TRACKER_INGEST_TOKEN")


def iso_utc_now():
    t = time.localtime()
    return "{:04d}-{:02d}-{:02d}T{:02d}:{:02d}:{:02d}+00:00".format(
        t.tm_year, t.tm_mon, t.tm_mday, t.tm_hour, t.tm_min, t.tm_sec
    )


def connect_wifi():
    print("Connecting to WiFi:", WIFI_SSID)
    wifi.radio.connect(WIFI_SSID, WIFI_PASSWORD)
    print("Connected, IP:", wifi.radio.ipv4_address)


def sync_clock(pool):
    """Set the onboard RTC from NTP so received_at reflects wall-clock time."""
    try:
        ntp = adafruit_ntp.NTP(pool, tz_offset=0)
        rtc.RTC().datetime = ntp.datetime
        print("Clock synced via NTP:", iso_utc_now())
    except Exception as exc:
        print("NTP sync failed, received_at may drift:", exc)


def post_batch(session, batch):
    headers = {"Content-Type": "application/json"}
    if INGEST_TOKEN:
        headers["Authorization"] = "Bearer " + INGEST_TOKEN
    endpoint = INGEST_URL.rstrip("/") + "/positions/batch"
    response = session.post(endpoint, json={"positions": batch}, headers=headers, timeout=10)
    try:
        return response.status_code == 201
    finally:
        response.close()


def run():
    if not INGEST_URL:
        raise RuntimeError("TRACKER_API_URL must be set in settings.toml")

    connect_wifi()
    pool = socketpool.SocketPool(wifi.radio)
    session = adafruit_requests.Session(pool, ssl.create_default_context())
    sync_clock(pool)

    uart = busio.UART(UART_TX, UART_RX, baudrate=BAUD, timeout=1)

    latest_nav = {}
    last_fix = None  # (monotonic_time, lat, lon)
    buffer = []
    last_flush = time.monotonic()
    line_buf = b""

    while True:
        if not wifi.radio.connected:
            raise RuntimeError("WiFi connection dropped")

        data = uart.read(256)
        if data:
            line_buf += data
            while b"\n" in line_buf:
                raw_line, _, line_buf = line_buf.partition(b"\n")
                try:
                    text = raw_line.decode("ascii", "ignore").strip()
                except Exception:
                    continue
                if not text:
                    continue

                try:
                    nav, position = parse_line(text)
                except Exception:
                    continue

                if nav:
                    latest_nav.update({k: v for k, v in nav.items() if v is not None})
                if position is None:
                    continue

                for key in ("speed_knots", "course_deg"):
                    if position.get(key) is None and latest_nav.get(key) is not None:
                        position[key] = latest_nav[key]

                now = time.monotonic()
                if (
                    last_fix is not None
                    and now - last_fix[0] <= 1.0
                    and abs(position["latitude"] - last_fix[1]) < 1e-5
                    and abs(position["longitude"] - last_fix[2]) < 1e-5
                ):
                    continue  # duplicate GGA/RMC pair for the same fix
                last_fix = (now, position["latitude"], position["longitude"])

                position["received_at"] = iso_utc_now()
                buffer.append(position)
                print("Fix: {:.5f}, {:.5f}".format(position["latitude"], position["longitude"]))

        now = time.monotonic()
        should_flush = buffer and (len(buffer) >= BATCH_SIZE or now - last_flush >= FLUSH_SECONDS)
        if should_flush:
            try:
                if post_batch(session, buffer):
                    buffer = []
                else:
                    print("Ingest API rejected batch")
            except Exception as exc:
                print("Batch POST failed:", exc)
                if len(buffer) > MAX_BUFFER:
                    overflow = len(buffer) - MAX_BUFFER
                    del buffer[:overflow]
                    print("Buffer full, dropped", overflow, "oldest positions")
            last_flush = now


while True:
    try:
        run()
    except Exception as exc:
        print("Fatal error, restarting in 5s:", exc)
        time.sleep(5)
