"""`sharp-tracker` entry point: serve the web app, optionally with the collector.

    sharp-tracker                     # map + ingest (shore/server laptop)
    sharp-tracker --collect           # also read NMEA, log locally, forward (ship)
    sharp-tracker --host 0.0.0.0      # reachable from other machines
"""

from __future__ import annotations

import argparse
import logging
import os

import uvicorn
from dotenv import load_dotenv

load_dotenv()


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the SHARP Tracker map and ingest API.")
    parser.add_argument("--host", default=os.getenv("LIVE_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("LIVE_PORT", "8090")))
    parser.add_argument(
        "--collect",
        action="store_true",
        help="also run the NMEA collector in this process (same as COLLECT=1)",
    )
    args = parser.parse_args()

    if args.collect:
        os.environ["COLLECT"] = "1"
    # Root logging for the collector; uvicorn configures only its own loggers.
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    uvicorn.run("sharp_tracker.live:app", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
