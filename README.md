# SHARP Tracker

Python app to read NMEA position sentences from a TCP or serial source, ingest positions over HTTP, store them in SQLite/Postgres, and display them in a Streamlit map.

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
Streamlit tracker map
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

This starts the NMEA collector and serves the Streamlit dashboard at `http://127.0.0.1:8501`. If a dashboard is already running on that host/port, it reuses it instead of starting another one.

Optional host/port overrides:

```bash
uv run sharp-tracker --host 0.0.0.0 --port 8501
```

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

### 4. Start the Streamlit tracker

```bash
streamlit run streamlit_app.py
```

The app shows an interactive Folium map with an Esri Ocean bathymetric basemap, zoom controls, a track line, point popups, and a timestamp range filter.

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

For Railway, attach Postgres. Railway will provide `DATABASE_URL`; the app automatically uses it. Both the ingest API and Streamlit app should point at the same Postgres database.

## Railway deployment notes

`Procfile` includes three process types:

- `web`: Streamlit map app
- `ingest`: FastAPI ingest API
- `worker`: NMEA collector

Recommended Railway setup:

1. Create a Railway Postgres database.
2. Deploy this repo as a Streamlit service using the `web` process.
3. Deploy this repo as an ingest API service using the `ingest` process.
4. Set `TRACKER_INGEST_TOKEN` on the ingest service.
5. Run the collector on the ship-network machine, not on Railway, because `192.168.20.215` is a private LAN address.

You generally do **not** need the Railway `worker` process unless Railway can reach your NMEA source.

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
LOG_LEVEL=INFO
```
