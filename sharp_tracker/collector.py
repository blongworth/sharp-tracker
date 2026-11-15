from __future__ import annotations

import logging
import os
import socket
import threading
import time
from datetime import datetime, timezone
from typing import Any

import requests
import serial
from dotenv import load_dotenv

load_dotenv()

from .db import get_forward_cursor, insert_position, mark_forwarded, unsent_positions
from .nmea import parse_line

LOG = logging.getLogger(__name__)

DEFAULT_HOST = "192.168.20.215"
DEFAULT_PORT = 4000
DEFAULT_SERIAL_BAUD = 4800
# Keep at most one fix per interval. Also collapses GGA/RMC pairs that report
# the same fix. 0 keeps every fix, including both halves of a GGA/RMC pair.
LOG_INTERVAL_SECONDS = float(os.getenv("LOG_INTERVAL_SECONDS", "5"))
BATCH_SIZE = 50
IDLE_SECONDS = 1.0
MAX_BACKOFF_SECONDS = 60.0

LATEST_NAV: dict[str, Any] = {}
_forwarder_thread: threading.Thread | None = None
_last_logged: datetime | None = None


class ForwardError(Exception):
    """The ingest API answered, but did not accept the batch."""


def collect_forever(
    host: str | None = None,
    port: int | None = None,
    reconnect_seconds: float = 5.0,
) -> None:
    """Read NMEA forever, log fixes locally, and forward them if TRACKER_API_URL is set.

    Set NMEA_SOURCE=serial or NMEA_SERIAL_PORT=/dev/... to read from a serial
    device. Otherwise the collector reads TCP from NMEA_HOST:NMEA_PORT.
    """
    source = os.getenv("NMEA_SOURCE", "serial" if os.getenv("NMEA_SERIAL_PORT") else "tcp").lower()
    api_url = os.getenv("TRACKER_API_URL")
    sink = f"local database + {api_url}" if api_url else "local database"
    if api_url:
        _ensure_forwarder(api_url)

    while True:
        try:
            if source == "serial":
                _collect_serial_once()
            elif source == "tcp":
                host = host or os.getenv("NMEA_HOST", DEFAULT_HOST)
                port = port or int(os.getenv("NMEA_PORT", str(DEFAULT_PORT)))
                LOG.info("Starting TCP NMEA collector for %s:%s -> %s", host, port, sink)
                _collect_tcp_once(host, port)
            else:
                raise ValueError("NMEA_SOURCE must be 'tcp' or 'serial'")
        except KeyboardInterrupt:
            raise
        except Exception:
            LOG.exception("NMEA collector disconnected; reconnecting in %.1fs", reconnect_seconds)
            time.sleep(reconnect_seconds)


def _collect_tcp_once(host: str, port: int) -> None:
    with socket.create_connection((host, port), timeout=15) as sock:
        LOG.info("Connected to TCP NMEA source %s:%s", host, port)
        sock.settimeout(None)
        file = sock.makefile("r", encoding="ascii", errors="ignore", newline="\n")
        for line in file:
            process_nmea_line(line)


def _collect_serial_once() -> None:
    serial_port = os.environ["NMEA_SERIAL_PORT"]
    baud = int(os.getenv("NMEA_SERIAL_BAUD", str(DEFAULT_SERIAL_BAUD)))
    timeout = float(os.getenv("NMEA_SERIAL_TIMEOUT", "5"))

    LOG.info("Starting serial NMEA collector for %s at %s baud", serial_port, baud)
    with serial.Serial(serial_port, baudrate=baud, timeout=timeout) as ser:
        while True:
            raw = ser.readline()
            if not raw:
                LOG.debug("No serial data received within %.1fs", timeout)
                continue
            process_nmea_line(raw.decode("ascii", errors="ignore"))


def process_nmea_line(line: str, now: datetime | None = None) -> dict[str, Any] | None:
    """Parse one NMEA line and log the fix locally; returns the stored position.

    Every kept fix goes to the local database first. When TRACKER_API_URL is
    set, the forwarder thread sends it on from there, so nothing is lost if the
    link or the server is down.
    """
    global _last_logged

    now = now or datetime.now(timezone.utc)
    nav, position = parse_line(line, received_at=now)
    if nav:
        LATEST_NAV.update({k: v for k, v in nav.items() if v is not None})
    if position is None:
        return None

    # GGA has position but no SOG/COG; VTG often arrives nearby. Carry the
    # latest navigation values forward so the dashboard can show heading.
    for key in ("speed_knots", "course_deg"):
        if position.get(key) is None and LATEST_NAV.get(key) is not None:
            position[key] = LATEST_NAV[key]

    if _last_logged is not None and (now - _last_logged).total_seconds() < LOG_INTERVAL_SECONDS:
        return None

    _last_logged = now
    insert_position(position)
    LOG.debug(
        "Position %.6f, %.6f from %s",
        position["latitude"],
        position["longitude"],
        position.get("sentence_type"),
    )
    return position


def _ensure_forwarder(api_url: str) -> None:
    """Start the background thread that forwards unsent fixes to the API."""
    global _forwarder_thread
    if _forwarder_thread is None or not _forwarder_thread.is_alive():
        _forwarder_thread = threading.Thread(target=_forward_loop, args=(api_url,), daemon=True)
        _forwarder_thread.start()


def _forward_loop(api_url: str) -> None:
    """Forward unsent fixes forever, backing off while the API is unreachable."""
    session = requests.Session()
    backoff = 0.0
    while True:
        try:
            sent = _forward_once(session, api_url)
        except (requests.RequestException, ForwardError) as exc:
            backoff = min(max(backoff * 2, 2.0), MAX_BACKOFF_SECONDS)
            LOG.warning("Forwarding to %s failed (%s); retrying in %.0fs", api_url, exc, backoff)
            time.sleep(backoff)
            continue
        except Exception:
            # e.g. local DB locked; keep the thread alive.
            LOG.exception("Forwarder error; retrying in %.0fs", MAX_BACKOFF_SECONDS)
            time.sleep(MAX_BACKOFF_SECONDS)
            continue
        if backoff:
            LOG.info("Forwarding to %s recovered", api_url)
            backoff = 0.0
        if sent < BATCH_SIZE:
            time.sleep(IDLE_SECONDS)  # caught up; full batches loop immediately


def _forward_once(session: Any, api_url: str) -> int:
    """POST the next batch of unsent fixes; returns how many were accepted.

    `session` is anything with a requests-style ``post`` (a requests.Session,
    or a FastAPI TestClient in tests). The cursor only advances after the API
    accepts the batch; a resend after a lost response is harmless because
    inserts are idempotent.
    """
    batch = unsent_positions(get_forward_cursor(), limit=BATCH_SIZE)
    if not batch:
        return 0

    headers: dict[str, str] = {}
    token = os.getenv("TRACKER_INGEST_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    response = session.post(
        api_url.rstrip("/") + "/positions/batch",
        json={"positions": batch},
        headers=headers,
        timeout=10,
    )
    if response.status_code != 201:
        raise ForwardError(f"HTTP {response.status_code}: {response.text[:200]}")

    mark_forwarded(batch[-1]["id"])
    return len(batch)


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    collect_forever()


if __name__ == "__main__":
    main()
