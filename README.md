# SHARP Tracker

Python app to read NMEA position sentences from a TCP or serial source, ingest positions over HTTP, store them in SQLite/Postgres, and display them in a live MapLibre map.

## Architecture

```text
Ship network
  NMEA source 192.168.20.215:4000 or serial GPS/AIS receiver
        ↓ TCP or serial
  local collector
        ↓ HTTPS POST /positions
Railway ingest API
        ↓
Railway Postgres
        ↓
Live tracker map (MapLibre + SSE)
```

Railway does not need inbound access to the ship LAN. The collector only needs outbound HTTPS access to the Railway ingest API.

## Local setup

```bash
# If using uv
uv sync

# Or with pip
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Run collector and dashboard together

```bash
uv run sharp-tracker
```

This starts the NMEA collector and serves the live map dashboard at `http://127.0.0.1:8090`. If a dashboard is already running on that host/port, it reuses it instead of starting another one.

Optional host/port overrides:

```bash
uv run sharp-tracker --host 0.0.0.0 --port 8090
```

## Live map (push-based, no page refreshes)

The live map is the main dashboard: a single MapLibre page served by FastAPI and updated over
Server-Sent Events — no full page reloads, so it stays smooth indefinitely:

```bash
uv run sharp-tracker-live          # http://127.0.0.1:8090
```

Endpoints: `GET /` (map), `GET /track` (SSE stream, `LIVE_REFRESH_SECONDS`,
default 10s), `GET /api/positions?window_hours=6` (initial track window).
Deploy as the `web` process on Railway alongside `ingest`.

## Test the whole system locally

Use four terminals.

### 1. Start a fake local NMEA TCP source

```bash
python scripts/simulate_nmea_source.py --host 127.0.0.1 --port 4000
```

### 2. Start the ingest API

```bash
uvicorn sharp_tracker.api:app --reload --port 8000
```

Check it:

```bash
curl http://127.0.0.1:8000/health
```

### 3. Start the collector in POST mode

```bash
export NMEA_SOURCE=tcp
export NMEA_HOST=127.0.0.1
export NMEA_PORT=4000
export TRACKER_API_URL=http://127.0.0.1:8000
python -m sharp_tracker.collector
```

If `TRACKER_API_URL` is not set, the collector writes directly to the configured DB instead.

### 4. Start the live tracker

```bash
uv run sharp-tracker-live
```

The map shows an Esri Ocean bathymetric basemap, seamarks, a 1-degree graticule, the ship track, the vessel position updated over SSE, and current NES-LTER waypoints with a line to the nearest one.

## Ship-network collector

On the machine that can reach the NMEA source, use either TCP:

```bash
export NMEA_SOURCE=tcp
export NMEA_HOST=192.168.20.215
export NMEA_PORT=4000
export TRACKER_API_URL=https://your-railway-ingest-service.up.railway.app
export TRACKER_INGEST_TOKEN=change-me
python -m sharp_tracker.collector
```

or serial:

```bash
export NMEA_SOURCE=serial
export NMEA_SERIAL_PORT=/dev/ttyUSB0        # macOS often uses /dev/tty.usbserial-XXXX
export NMEA_SERIAL_BAUD=4800                # common NMEA default; some devices use 38400
export TRACKER_API_URL=https://your-railway-ingest-service.up.railway.app
export TRACKER_INGEST_TOKEN=change-me
python -m sharp_tracker.collector
```

Set the same `TRACKER_INGEST_TOKEN` on the Railway ingest API service.

## Database configuration

Local default is SQLite:

```bash
DATABASE_PATH=data/positions.sqlite3
```

For Railway, attach Postgres. Railway will provide `DATABASE_URL`; the app automatically uses it. Both the ingest API and the live map should point at the same Postgres database.

## Railway deployment notes

`Procfile` includes three process types:

- `web`: live map app (the main application)
- `ingest`: FastAPI ingest API
- `worker`: NMEA collector

Railway does not turn a multi-process `Procfile` into multiple services on its
own. Create **two separate Railway services** from this same repo — one for
`web`, one for `ingest` — and set each service's start command (or Procfile
process type, depending on the Railway UI at the time) to match its Procfile
line. Deploying the repo once does not give you both processes.

Recommended Railway setup:

1. Create a Railway Postgres database in the project.
2. Add a service for `web` (the live map) and a service for `ingest` (the
   FastAPI ingest API), both deployed from this repo.
3. Set `DATABASE_URL` on **both** the `web` and `ingest` services, pointed at
   the same Postgres database (Railway can inject this automatically via a
   reference variable). Without it, each service falls back to local SQLite
   on Railway's ephemeral filesystem — data won't persist across redeploys,
   and `web` and `ingest` won't share a database.
4. Set `TRACKER_INGEST_TOKEN` on the `ingest` service, and the same value on
   the ship-side collector's environment. If this is unset, the ingest
   endpoint accepts unauthenticated position posts from anyone who finds its
   URL — don't leave it unset once the service has a public Railway URL.
5. Set the `ingest` service's health-check path to `/health` in its Railway
   settings — it has no `/` route, so a default root health check will fail.
   `web`'s default `/` health check works as-is.
6. Run the collector on the ship-network machine, not on Railway, because
   `192.168.20.215` is a private LAN address Railway cannot reach. Point it
   at the `ingest` service's public URL via `TRACKER_API_URL`.

You generally do **not** need the Railway `worker` process unless Railway can
reach your NMEA source.

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
TRACKER_API_URL=https://your-ingest-service.up.railway.app
TRACKER_INGEST_TOKEN=change-me
LIVE_HOST=127.0.0.1
LIVE_PORT=8090
LIVE_REFRESH_SECONDS=10
LOG_LEVEL=INFO
```
