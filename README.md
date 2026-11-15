# SHARP Tracker

Python app to read NMEA position sentences from a TCP or serial source, ingest positions over HTTP, store them in SQLite/Postgres, and display them in a live MapLibre map.

## Architecture

```text
Ship network
  NMEA source 192.168.20.215:4000 or serial GPS/AIS receiver
        ↓ TCP or serial
  local collector  (logs every fix to local SQLite first)
        ↓ HTTPS POST /positions/batch  (forwards unsent fixes; catches up after outages)
Railway web app  (map + ingest in one service)
        ↓
Railway Postgres
```

Railway does not need inbound access to the ship LAN. The collector only needs outbound HTTPS access to the Railway web app.

## Local setup

```bash
# If using uv
uv sync

# Or with pip
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Run it

One command runs everything in one process:

```bash
uv run sharp-tracker --collect     # read NMEA, log locally, serve the map at http://127.0.0.1:8090
uv run sharp-tracker               # map + ingest only (a shore/server machine)
uv run sharp-tracker --host 0.0.0.0 --port 8090   # reachable from other machines
```

`--collect` (or `COLLECT=1`) runs the NMEA collector in a background thread. It
logs every fix to the local database; if `TRACKER_API_URL` is set it also
forwards them to another instance's ingest endpoint. `sharp-tracker-collector`
runs the collector alone, without the map.

Endpoints: `GET /` (map), `GET /track` (SSE stream, `LIVE_REFRESH_SECONDS`,
default 10s), `GET /api/positions?window_hours=6` (initial track window),
`GET /api/waypoints`, `GET /health`, and `POST /positions/batch` (ingest,
gated by `TRACKER_INGEST_TOKEN` when set).

`/health` always answers 200 while the app is up and reports the newest fix's
`last_fix_received_at` and `last_fix_age_seconds`, so an uptime monitor can
alert when data stops arriving.

The map shows an Esri Ocean bathymetric basemap, seamarks, a 1-degree graticule, the ship track, the vessel position updated over SSE, and current NES-LTER waypoints with a line to the nearest one.

## Test the whole system locally

With a fake NMEA source, everything on one laptop:

```bash
python scripts/simulate_nmea_source.py --host 127.0.0.1 --port 4000     # terminal 1
NMEA_SOURCE=tcp NMEA_HOST=127.0.0.1 NMEA_PORT=4000 uv run sharp-tracker --collect   # terminal 2
```

To try ship-to-shore forwarding, add a second instance with its own database
(on another laptop, use its IP and `--host 0.0.0.0` on the shore side):

```bash
DATABASE_PATH=data/shore.sqlite3 uv run sharp-tracker --port 8090                     # "shore"
DATABASE_PATH=data/ship.sqlite3 NMEA_SOURCE=tcp NMEA_HOST=127.0.0.1 NMEA_PORT=4000 \
  TRACKER_API_URL=http://127.0.0.1:8090 uv run sharp-tracker --collect --port 8091      # "ship"
```

Stop the shore instance for a while and restart it: the ship keeps logging and
catches the shore up when it returns.

## Ship-network collector

On the machine that can reach the NMEA source, use either TCP:

```bash
export NMEA_SOURCE=tcp
export NMEA_HOST=192.168.20.215
export NMEA_PORT=4000
export TRACKER_API_URL=https://your-railway-web-service.up.railway.app
export TRACKER_INGEST_TOKEN=change-me
uv run sharp-tracker --collect
```

or serial:

```bash
export NMEA_SOURCE=serial
export NMEA_SERIAL_PORT=/dev/ttyUSB0        # macOS often uses /dev/tty.usbserial-XXXX
export NMEA_SERIAL_BAUD=4800                # common NMEA default; some devices use 38400
export TRACKER_API_URL=https://your-railway-web-service.up.railway.app
export TRACKER_INGEST_TOKEN=change-me
uv run sharp-tracker --collect
```

Set the same `TRACKER_INGEST_TOKEN` on the Railway web service. The ship-side
map at http://127.0.0.1:8090 keeps working with no internet connection.

## Pico W firmware (CircuitPython NMEA bridge)

`firmware/pico_nmea_tracker/` is an alternative to running the Python collector on a
shipboard laptop/Raspberry Pi: a Raspberry Pi **Pico W** (or Pico 2 W — WiFi is
required) wired directly to the NMEA source reads sentences over UART and posts
parsed positions straight to the ingest API, same as `sharp_tracker.collector` does.
It does not talk to Postgres directly — like the desktop collector, it authenticates
to the ingest API with `TRACKER_INGEST_TOKEN` and lets that service own the database
connection, so no DB credentials ever live on the field device.

### Hardware

Wire the NMEA talker's TX output to the Pico's UART0 RX pin, and tie grounds together:

| NMEA source | Pico W |
| --- | --- |
| TX (NMEA out) | GP1 (UART0 RX) |
| GND | GND |

Most NMEA GPS/AIS talkers are 4800 baud (some AIS units use 38400) and idle high,
which is compatible with the Pico's 3.3V UART input — if your source outputs RS-232
levels instead of TTL, add a level shifter between them.

### Install

1. Flash the latest [CircuitPython UF2 for Raspberry Pi Pico W](https://circuitpython.org/board/raspberry_pi_pico_w/)
   by holding BOOTSEL while plugging in the Pico, then dragging the UF2 onto the
   `RPI-RP2` drive that appears. It reboots as a `CIRCUITPY` USB drive.
2. From the [Adafruit CircuitPython Bundle](https://circuitpython.org/libraries)
   matching your CircuitPython version, copy these into `CIRCUITPY/lib/`:
   - `adafruit_requests.mpy`
   - `adafruit_connection_manager.mpy`
   - `adafruit_ntp.mpy`
3. Copy `firmware/pico_nmea_tracker/code.py` and `firmware/pico_nmea_tracker/nmea_min.py`
   to the root of `CIRCUITPY`.
4. Copy `firmware/pico_nmea_tracker/settings.toml.example` to `CIRCUITPY/settings.toml`
   and fill in your WiFi credentials, `TRACKER_API_URL`, and `TRACKER_INGEST_TOKEN`
   (the same ingest URL/token used by the desktop collector above). `settings.toml`
   is gitignored — never commit your filled-in copy.

### Use

The Pico runs `code.py` automatically on boot and whenever `CIRCUITPY` is
reconnected. Open a serial console (e.g. `screen /dev/tty.usbmodemXXXX 115200`,
or the Mu editor's serial pane) to watch it connect to WiFi, sync its clock over
NTP, and log each parsed fix as it batches and POSTs positions to
`/positions/batch`. If the ingest API is unreachable, positions queue in RAM (up
to `MAX_BUFFER`, oldest dropped first) and resume sending once connectivity
returns; any other unhandled error restarts the whole bridge after 5 seconds
rather than requiring a manual reset. Tune `BATCH_SIZE`/`FLUSH_SECONDS` in
`settings.toml` to trade off POST frequency against position latency.

## Database configuration

Local default is SQLite:

```bash
DATABASE_PATH=data/positions.sqlite3
```

For Railway, attach Postgres. Railway will provide `DATABASE_URL`; the app automatically uses it.

## Tests and local Postgres

```bash
uv run pytest                                   # SQLite; Postgres variants are skipped
docker compose up -d --wait postgres            # throwaway local Postgres (compose.yaml)
TEST_DATABASE_URL=postgresql://sharp:sharp@127.0.0.1:5432/sharp_tracker_test uv run pytest
```

The compose database is also handy for running the app against Postgres the
way Railway does:
`DATABASE_URL=postgresql://sharp:sharp@127.0.0.1:5432/sharp_tracker uv run sharp-tracker`.
Tests drop all tables in `TEST_DATABASE_URL`, so it must be a localhost database
with `test` in its name.

## Railway deployment notes

Railway runs one service: `web` in the `Procfile` (map + ingest).

1. Create a Railway Postgres database in the project.
2. Add one service deployed from this repo (start command: the `Procfile` `web` line).
3. Set `DATABASE_URL` on the service, pointed at the Postgres database (a
   Railway reference variable works). Without it the app falls back to SQLite
   on Railway's ephemeral filesystem and data won't survive redeploys.
4. Set `TRACKER_INGEST_TOKEN`, and the same value on the ship-side collector.
   If unset, anyone who finds the URL can post positions.
5. Run the collector on the ship network, not on Railway: `192.168.20.215` is a
   private LAN address Railway cannot reach. Point `TRACKER_API_URL` at the
   web service's public URL.

## Environment variables

```bash
NMEA_SOURCE=tcp
NMEA_HOST=192.168.20.215
NMEA_PORT=4000
NMEA_SERIAL_PORT=/dev/ttyUSB0
NMEA_SERIAL_BAUD=4800
NMEA_SERIAL_TIMEOUT=5
DATABASE_PATH=data/positions.sqlite3
DATABASE_URL=postgresql+psycopg://user:password@host:5432/dbname
TRACKER_API_URL=https://your-web-service.up.railway.app
TRACKER_INGEST_TOKEN=change-me
COLLECT=1                 # same as --collect
LOG_INTERVAL_SECONDS=5
LIVE_HOST=127.0.0.1
LIVE_PORT=8090
LIVE_REFRESH_SECONDS=10
LOG_LEVEL=INFO
```
