"""Fake NMEA GPS for testing without ship hardware.

Sends a GGA + RMC pair per tick (1 Hz by default, like a real receiver) for a
ship steaming from near Woods Hole at --speed-kn on --course-deg.

    # TCP, for the desktop collector (NMEA_SOURCE=tcp)
    uv run python scripts/simulate_nmea_source.py --port 4000

    # Serial, e.g. a USB-serial adapter wired to a Pico's GP1 (3.3 V logic!)
    uv run python scripts/simulate_nmea_source.py --serial /dev/tty.usbserial-XXXX --baud 4800
"""

from __future__ import annotations

import argparse
import math
import socket
import time
from datetime import datetime, timezone

METERS_PER_DEG_LAT = 111_320.0
METERS_PER_KNOT_SECOND = 1852.0 / 3600.0


def checksum(sentence_without_dollar: str) -> str:
    value = 0
    for char in sentence_without_dollar:
        value ^= ord(char)
    return f"{value:02X}"


def nmea_lat_lon(lat: float, lon: float) -> tuple[str, str, str, str]:
    lat_dir = "N" if lat >= 0 else "S"
    lon_dir = "E" if lon >= 0 else "W"
    lat_abs = abs(lat)
    lon_abs = abs(lon)
    lat_deg = int(lat_abs)
    lon_deg = int(lon_abs)
    lat_min = (lat_abs - lat_deg) * 60
    lon_min = (lon_abs - lon_deg) * 60
    return f"{lat_deg:02d}{lat_min:07.4f}", lat_dir, f"{lon_deg:03d}{lon_min:07.4f}", lon_dir


def sentence(body: str) -> str:
    return f"${body}*{checksum(body)}\r\n"


def fix_sentences(lat: float, lon: float, sog: float, cog: float) -> str:
    now = datetime.now(timezone.utc)
    lat_s, lat_dir, lon_s, lon_dir = nmea_lat_lon(lat, lon)
    gga = f"GPGGA,{now:%H%M%S}.00,{lat_s},{lat_dir},{lon_s},{lon_dir},1,08,0.9,0.0,M,0.0,M,,"
    rmc = f"GPRMC,{now:%H%M%S}.00,A,{lat_s},{lat_dir},{lon_s},{lon_dir},{sog:.1f},{cog:.1f},{now:%d%m%y},,,A"
    return sentence(gga) + sentence(rmc)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fake NMEA GPS (TCP server or serial port)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4000)
    parser.add_argument("--serial", help="write to this serial port instead of serving TCP")
    parser.add_argument("--baud", type=int, default=4800)
    parser.add_argument("--interval", type=float, default=1.0, help="seconds between fixes")
    parser.add_argument("--speed-kn", type=float, default=10.0)
    parser.add_argument("--course-deg", type=float, default=45.0)
    args = parser.parse_args()

    lat, lon = 41.526, -70.673  # near Woods Hole

    def ticks():
        nonlocal lat, lon
        step_m = args.speed_kn * METERS_PER_KNOT_SECOND * args.interval
        course = math.radians(args.course_deg)
        while True:
            yield fix_sentences(lat, lon, args.speed_kn, args.course_deg)
            lat += step_m * math.cos(course) / METERS_PER_DEG_LAT
            lon += step_m * math.sin(course) / (METERS_PER_DEG_LAT * math.cos(math.radians(lat)))
            time.sleep(args.interval)

    if args.serial:
        import serial

        with serial.Serial(args.serial, baudrate=args.baud) as port:
            print(f"NMEA simulator writing to {args.serial} at {args.baud} baud")
            for text in ticks():
                port.write(text.encode("ascii"))
                print(text.strip())
        return

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((args.host, args.port))
        server.listen(1)
        print(f"NMEA simulator listening on {args.host}:{args.port}")
        conn, addr = server.accept()
        with conn:
            print(f"collector connected from {addr}")
            for text in ticks():
                conn.sendall(text.encode("ascii"))
                print(text.strip())


if __name__ == "__main__":
    main()
