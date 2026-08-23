"""Run the NMEA collector and serve the live MapLibre dashboard."""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen

from dotenv import load_dotenv

load_dotenv()


def dashboard_is_running(host: str, port: int) -> bool:
    url = f"http://{host}:{port}/"
    try:
        with urlopen(url, timeout=1.5) as response:
            return response.status == 200
    except URLError:
        return False
    except TimeoutError:
        return False


def start_dashboard(host: str, port: int) -> subprocess.Popen:
    print(f"Starting live map at http://{host}:{port}")
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "sharp_tracker.live:app",
            "--host",
            host,
            "--port",
            str(port),
        ]
    )


def start_collector() -> subprocess.Popen:
    print("Starting NMEA collector")
    return subprocess.Popen([sys.executable, "-m", "sharp_tracker.collector"])


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the NMEA collector and serve the live map dashboard."
    )
    parser.add_argument("--host", default=os.getenv("LIVE_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("LIVE_PORT", "8090")))
    args = parser.parse_args()

    dashboard_process: subprocess.Popen | None = None
    collector_process: subprocess.Popen | None = None
    started_dashboard = False

    if dashboard_is_running(args.host, args.port):
        print(f"Live map already running at http://{args.host}:{args.port}")
    else:
        dashboard_process = start_dashboard(args.host, args.port)
        started_dashboard = True
        time.sleep(2)

    collector_process = start_collector()

    try:
        while True:
            if collector_process.poll() is not None:
                raise SystemExit(collector_process.returncode)
            if dashboard_process and dashboard_process.poll() is not None:
                raise SystemExit(dashboard_process.returncode)
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping sharp tracker")
    finally:
        if collector_process and collector_process.poll() is None:
            collector_process.send_signal(signal.SIGINT)
            try:
                collector_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                collector_process.terminate()

        if started_dashboard and dashboard_process and dashboard_process.poll() is None:
            dashboard_process.send_signal(signal.SIGINT)
            try:
                dashboard_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                dashboard_process.terminate()


if __name__ == "__main__":
    main()
