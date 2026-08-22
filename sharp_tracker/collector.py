from __future__ import annotations

import logging
import os
import socket
import time
from datetime import datetime, timezone
from typing import Any

import requests
import serial
from dotenv import load_dotenv

load_dotenv()

from .db import insert_position
from .nmea import parse_navigation, parse_position

LOG = logging.getLogger(__name__)

DEFAULT_HOST = "192.168.20.215"
DEFAULT_PORT = 4000
DEFAULT_SERIAL_BAUD = 4800
LATEST_NAV: dict[str, Any] = {}


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
    nav = parse_navigation(line)
    if nav:
        LATEST_NAV.update({k: v for k, v in nav.items() if v is not None})

    position = parse_position(line, received_at=datetime.now(timezone.utc))
    if position:
        # GGA has position but no SOG/COG; VTG often arrives nearby. Carry the
        # latest navigation values forward so the dashboard can show heading.
        for key in ("speed_knots", "course_deg"):
            if position.get(key) is None and LATEST_NAV.get(key) is not None:
                position[key] = LATEST_NAV[key]

        write_position(position)
        LOG.debug(
            "Position %.6f, %.6f from %s",
            position["latitude"],
            position["longitude"],
            position.get("sentence_type"),
        )


def write_position(position: dict[str, Any]) -> None:
    """Send position to TRACKER_API_URL when configured, otherwise local DB."""
    api_url = os.getenv("TRACKER_API_URL")
    if not api_url:
        insert_position(position)
        return

    post_position(position, api_url=api_url)


def post_position(position: dict[str, Any], api_url: str, attempts: int = 3) -> None:
    endpoint = api_url.rstrip("/") + "/positions"
    headers = {}
    token = os.getenv("TRACKER_INGEST_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            response = requests.post(endpoint, json=position, headers=headers, timeout=10)
            response.raise_for_status()
            return
        except requests.RequestException as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(attempt)

    raise RuntimeError(f"Failed to post position to {endpoint}: {last_error}")


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    collect_forever()


if __name__ == "__main__":
    main()
