# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

SHARP Tracker reads NMEA position sentences from a ship's TCP or serial NMEA source, ships them to an ingest API over HTTPS, stores them in SQLite (local) or Postgres (Railway), and renders a live MapLibre map fed over Server-Sent Events. No frontend build step — `sharp_tracker/static/index.html` is a static page with a vendored MapLibre GL bundle.

## Commands

```bash
# Install
uv sync                      # or: python -m venv .venv && pip install -e .

# Run collector + live map together (dashboard reuses one already running on host/port)
uv run sharp-tracker [--host 0.0.0.0] [--port 8090]

# Run pieces individually
uv run sharp-tracker-live                              # live map only, http://127.0.0.1:8090
python -m sharp_tracker.collector                       # collector only
uvicorn sharp_tracker.api:app --reload --port 8000       # ingest API only

curl http://127.0.0.1:8000/health                       # ingest API health check
```

```bash
uv run pytest                                   # SQLite only; Postgres variants skip
docker compose up -d --wait postgres            # local Postgres (compose.yaml)
TEST_DATABASE_URL=postgresql://sharp:sharp@127.0.0.1:5432/sharp_tracker_test uv run pytest   # SQLite + Postgres
```

Tests drop all tables in `TEST_DATABASE_URL`; `tests/conftest.py` refuses any URL that isn't localhost with `test` in the database name. There is no linter or type checker configured in this repo; don't invent commands for them.

### Full local end-to-end run (4 terminals)

1. Fake NMEA source: `python scripts/simulate_nmea_source.py --host 127.0.0.1 --port 4000`
2. Ingest API: `uvicorn sharp_tracker.api:app --reload --port 8000`
3. Collector in POST mode (env: `NMEA_SOURCE=tcp NMEA_HOST=127.0.0.1 NMEA_PORT=4000 TRACKER_API_URL=http://127.0.0.1:8000`): `python -m sharp_tracker.collector`
4. Live map: `uv run sharp-tracker-live`

The collector always logs fixes to its local DB; if `TRACKER_API_URL` is set it also forwards them.

## Architecture

```
Ship network (NMEA TCP 192.168.20.215:4000 or serial GPS/AIS)
        v
  collector (sharp_tracker/collector.py)  -- parses NMEA, logs to local SQLite, forwards unsent rows
        v HTTPS POST /positions/batch
  ingest API (sharp_tracker/api.py)       -- FastAPI, writes to DB
        v
  DB (sharp_tracker/db.py: SQLAlchemy, SQLite or Postgres)
        v
  live map (sharp_tracker/live.py)        -- FastAPI, SSE + static MapLibre page
```

Three independent FastAPI/process entry points, each mapped to a Railway `Procfile` process type — they are NOT three instances of the same app:

- `web` = `sharp_tracker.live:app` — the dashboard (SSE stream + static page). Deploy this as the main Railway service.
- `ingest` = `sharp_tracker.api:app` — receives posted positions, auth-gated by `TRACKER_INGEST_TOKEN`.
- `worker` = `python -m sharp_tracker.collector` — only needed if Railway itself can reach the ship's NMEA source, which normally it can't (private LAN IP). In practice the collector runs on a machine on the ship network and pushes out to the `ingest` service.

Railway does not split a multi-process `Procfile` into services automatically — `web` and `ingest` must be created as two separate Railway services from this same repo, each with `DATABASE_URL` pointed at the same Postgres instance so they share data. See README.md "Railway deployment notes" for the full checklist (health check path for `ingest` must be set to `/health`, since it has no `/` route).

### Module responsibilities

- `sharp_tracker/nmea.py` — pure parsing: one `parse_line()` call turns a raw NMEA sentence into `(navigation, position)` dicts via `pynmea2`. No I/O. `navigation` carries speed/course from sentences like VTG that lack a position (e.g. GGA); `collector.py` merges the last-seen nav values onto position-bearing fixes so heading survives even when a GGA fix has no COG of its own.
- `sharp_tracker/collector.py` — owns the read loop (TCP or serial, chosen by `NMEA_SOURCE`/`NMEA_SERIAL_PORT`), keeps at most one fix per `LOG_INTERVAL_SECONDS` (default 5; this also collapses GGA/RMC pairs), and always inserts kept fixes into the local DB. When `TRACKER_API_URL` is set, a forwarder thread reads rows past the `forward_state` cursor and POSTs them in batches, advancing the cursor only on a 201 and backing off (up to 60 s) while the API is unreachable. A fresh `TRACKER_API_URL` on an existing local DB backfills its whole history.
- `sharp_tracker/db.py` — single source of truth for the `positions` table schema and DB URL resolution. `database_url()` normalizes Railway's `postgres://`/`postgresql://` to `postgresql+psycopg://` since SQLAlchemy's psycopg3 driver requires that scheme. `get_engine()` is process-wide (`lru_cache`) and calls `metadata.create_all` so schema issues fail loudly at startup rather than on first write. It also creates a unique index on `(received_at, raw)` (with raw SQL, so existing tables get it too); inserts use `ON CONFLICT DO NOTHING`, so resending a fix is always harmless. Stored times are UTC; reads tag SQLite's naive values as UTC. `forward_state` holds the collector's forwarding cursor (`get_forward_cursor`/`unsent_positions`/`mark_forwarded`).
- `sharp_tracker/api.py` — thin ingest surface (`POST /positions`, `POST /positions/batch`), Bearer-token gated via `TRACKER_INGEST_TOKEN` (if unset, ingest is unauthenticated — fine for local dev, not for a public Railway URL).
- `sharp_tracker/live.py` — dashboard backend: `GET /` serves the static page, `GET /track` is the SSE stream (polls the DB for the latest fix every `LIVE_REFRESH_SECONDS`, computes heading from COG or, if absent, the bearing between the last two fixes), `GET /api/positions` returns a decimated track window for initial page load, `GET /api/waypoints` proxies+caches current NES-LTER station coordinates from the public NES-LTER API for map display.
- `sharp_tracker/run.py` — `sharp-tracker` entry point: starts the live dashboard (unless one is already answering on host/port) and the collector as subprocesses, and shuts both down together on Ctrl-C.
- `scripts/simulate_nmea_source.py` — fake TCP NMEA source for local testing without real ship hardware.
- `firmware/pico_nmea_tracker/` — CircuitPython firmware for a Raspberry Pi Pico W as a hardware alternative to `collector.py`: reads NMEA over UART and POSTs to the ingest API. It is a separate runtime (MicroPython/CircuitPython, not CPython) and cannot import from `sharp_tracker/` — `firmware/pico_nmea_tracker/nmea_min.py` is a deliberately independent, dependency-free reimplementation of `nmea.py`'s parsing, kept schema-compatible with `PositionIn` by hand. It never touches Postgres directly; it authenticates to `sharp_tracker.api` with `TRACKER_INGEST_TOKEN` exactly like the desktop collector, so DB credentials never live on the field device. See README.md "Pico W firmware" for install/wiring steps.

### Data flow details worth knowing before touching them

- Every position row carries both `received_at` (when the collector/server saw it) and `fix_time` (parsed from the NMEA sentence's own date/time, may be null). Display code should prefer `fix_time` with `received_at` as fallback — this is exactly what `db.latest_positions()`'s `display_time` field and `live.py`'s `_compact_position` do; keep new consumers consistent with that convention rather than reading `received_at` directly.
- The collector's local DB is the source of truth on the ship: every kept fix is inserted there first, and the server only ever receives copies via the forwarder. Never make the forwarder delete or modify local rows.
