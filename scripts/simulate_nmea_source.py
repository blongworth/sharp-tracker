from __future__ import annotations

import argparse
import socket
import time
from datetime import datetime, timezone


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


def gga_sentence(lat: float, lon: float) -> str:
    now = datetime.now(timezone.utc)
    lat_s, lat_dir, lon_s, lon_dir = nmea_lat_lon(lat, lon)
    body = f"GPGGA,{now:%H%M%S}.00,{lat_s},{lat_dir},{lon_s},{lon_dir},1,08,0.9,0.0,M,0.0,M,,"
    return f"${body}*{checksum(body)}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Local TCP NMEA test source")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4000)
    parser.add_argument("--interval", type=float, default=1.0)
    args = parser.parse_args()

    # Starts near Woods Hole and slowly moves northeast.
    lat, lon = 41.526, -70.673

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((args.host, args.port))
        server.listen(1)
        print(f"NMEA simulator listening on {args.host}:{args.port}")
        conn, addr = server.accept()
        with conn:
            print(f"collector connected from {addr}")
            while True:
                line = gga_sentence(lat, lon) + "\r\n"
                conn.sendall(line.encode("ascii"))
                print(line.strip())
                lat += 0.00015
                lon += 0.0002
                time.sleep(args.interval)


if __name__ == "__main__":
    main()
