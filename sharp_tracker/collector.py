from __future__ import annotations

import logging
import os
import socket
import time
from datetime import datetime, timezone
from typing import Any

import requests

from .db import insert_position
from .nmea import parse_position

LOG = logging.getLogger(__name__)

DEFAULT_HOST = "192.168.20.215"
DEFAULT_PORT = 4000


def collect_forever(
    host: str | None = None,
    port: int | None = None,
    reconnect_seconds: float = 5.0,
) -> None:
    """Read NMEA over TCP forever and persist or post parsed positions."""
    host = host or os.getenv("NMEA_HOST", DEFAULT_HOST)
    port = port or int(os.getenv("NMEA_PORT", str(DEFAULT_PORT)))

    sink = "API" if os.getenv("TRACKER_API_URL") else "database"
    LOG.info("Starting NMEA collector for %s:%s -> %s", host, port, sink)
    while True:
        try:
            _collect_once(host, port)
        except KeyboardInterrupt:
            raise
        except Exception:
            LOG.exception("NMEA collector disconnected; reconnecting in %.1fs", reconnect_seconds)
            time.sleep(reconnect_seconds)


def _collect_once(host: str, port: int) -> None:
    with socket.create_connection((host, port), timeout=15) as sock:
        LOG.info("Connected to NMEA source %s:%s", host, port)
        sock.settimeout(None)
        file = sock.makefile("r", encoding="ascii", errors="ignore", newline="\n")
        for line in file:
            position = parse_position(line, received_at=datetime.now(timezone.utc))
            if position:
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
