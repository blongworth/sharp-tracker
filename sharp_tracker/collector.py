from __future__ import annotations

import logging
import os
import queue
import socket
import threading
import time
from datetime import datetime, timezone
from typing import Any

import requests
import serial
from dotenv import load_dotenv

load_dotenv()

from .db import insert_position, insert_positions
from .nmea import parse_line

LOG = logging.getLogger(__name__)

DEFAULT_HOST = "192.168.20.215"
DEFAULT_PORT = 4000
DEFAULT_SERIAL_BAUD = 4800
BATCH_SIZE = 50
FLUSH_SECONDS = 1.0
MAX_QUEUE = 2000

LATEST_NAV: dict[str, Any] = {}
_position_queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=MAX_QUEUE)
_sender_thread: threading.Thread | None = None
_last_fix: tuple[datetime, dict[str, Any]] | None = None


def collect_forever(
    host: str | None = None,
    port: int | None = None,
    reconnect_seconds: float = 5.0,
) -> None:
    """Read NMEA forever and persist or post parsed positions.

    Set NMEA_SOURCE=serial or NMEA_SERIAL_PORT=/dev/... to read from a serial
    device. Otherwise the collector reads TCP from NMEA_HOST:NMEA_PORT.
    """
    source = os.getenv("NMEA_SOURCE", "serial" if os.getenv("NMEA_SERIAL_PORT") else "tcp").lower()
    sink = "API" if os.getenv("TRACKER_API_URL") else "database"
    if sink == "API":
        _ensure_sender()

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


def process_nmea_line(line: str) -> None:
    """Parse one NMEA line (single parse) and queue or store the position."""
    global _last_fix

    now = datetime.now(timezone.utc)
    nav, position = parse_line(line, received_at=now)
    if nav:
        LATEST_NAV.update({k: v for k, v in nav.items() if v is not None})
    if position is None:
        return

    # GGA has position but no SOG/COG; VTG often arrives nearby. Carry the
    # latest navigation values forward so the dashboard can show heading.
    for key in ("speed_knots", "course_deg"):
        if position.get(key) is None and LATEST_NAV.get(key) is not None:
            position[key] = LATEST_NAV[key]

    if _is_duplicate(position, now):
        return

    _last_fix = (now, position)
    write_position(position)
    LOG.debug(
        "Position %.6f, %.6f from %s",
        position["latitude"],
        position["longitude"],
        position.get("sentence_type"),
    )


def _is_duplicate(position: dict[str, Any], now: datetime) -> bool:
    """Skip GGA/RMC pairs that report the same fix within a second."""
    if _last_fix is None:
        return False
    last_time, last_position = _last_fix
    if (now - last_time).total_seconds() > 1.0:
        return False
    return (
        abs(position["latitude"] - last_position["latitude"]) < 1e-5
        and abs(position["longitude"] - last_position["longitude"]) < 1e-5
    )


def write_position(position: dict[str, Any]) -> None:
    """Queue position for the API when configured, otherwise write to local DB."""
    api_url = os.getenv("TRACKER_API_URL")
    if not api_url:
        insert_position(position)
        return

    try:
        _position_queue.put_nowait(position)
    except queue.Full:
        LOG.warning("Position queue full; dropping position to avoid backpressure")


def _ensure_sender() -> None:
    """Start the background thread that flushes queued positions in batches."""
    global _sender_thread
    if _sender_thread is None or not _sender_thread.is_alive():
        _sender_thread = threading.Thread(target=_send_loop, daemon=True)
        _sender_thread.start()


def _send_loop() -> None:
    api_url = os.getenv("TRACKER_API_URL")
    session = requests.Session()
    while True:
        batch = _drain_batch()
        if batch:
            _flush(session, batch, api_url)


def _drain_batch() -> list[dict[str, Any]]:
    """Wait for the first queued position, then drain up to BATCH_SIZE."""
    try:
        batch = [_position_queue.get(timeout=FLUSH_SECONDS)]
    except queue.Empty:
        return []
    while len(batch) < BATCH_SIZE:
        try:
            batch.append(_position_queue.get_nowait())
        except queue.Empty:
            break
    return batch


def _flush(session: requests.Session, batch: list[dict[str, Any]], api_url: str) -> None:
    endpoint = api_url.rstrip("/") + "/positions/batch"
    headers: dict[str, str] = {}
    token = os.getenv("TRACKER_INGEST_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    payload = {"positions": batch}

    for attempt in (1, 2):
        try:
            response = session.post(endpoint, json=payload, headers=headers, timeout=10)
            response.raise_for_status()
            return
        except requests.RequestException as exc:
            if attempt == 1:
                LOG.warning("Batch POST of %d positions failed (%s); retrying", len(batch), exc)
                time.sleep(2)

    LOG.error("Batch POST failed twice; writing %d positions to local DB as fallback", len(batch))
    for position in batch:
        try:
            insert_position(position)
        except Exception:
            LOG.exception("Local fallback insert failed")


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %s: %s",
    )
    collect_forever()


if __name__ == "__main__":
    main()
