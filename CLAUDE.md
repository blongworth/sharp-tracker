# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

SHARP Tracker reads NMEA position sentences from a ship's TCP or serial NMEA source, logs them locally and forwards them to an ingest endpoint over HTTPS, stores them in SQLite (local) or Postgres (Railway), and renders a live MapLibre map fed over Server-Sent Events. No frontend build step — `sharp_tracker/static/index.html` is a static page with a vendored MapLibre GL bundle.

## Commands

```bash
# Install
uv sync                      # or: python -m venv .venv && pip install -e .

# One process: web app (map + ingest), optionally with the collector thread
uv run sharp-tracker --collect [--host 0.0.0.0] [--port 8090]   # ship: read NMEA, log, forward, serve map
uv run sharp-tracker                                             # shore/server: map + ingest only
uv run sharp-tracker-collector                                   # collector alone, no web app

curl http://127.0.0.1:8090/health
```

```bash
uv run pytest                                   # SQLite only; Postgres variants skip
docker compose up -d --wait postgres            # local Postgres (compose.yaml)
TEST_DATABASE_URL=postgresql://sharp:sharp@127.0.0.1:5432/sharp_tracker_test uv run pytest   # SQLite + Postgres
```

Tests drop all tables in `TEST_DATABASE_URL`; `tests/conftest.py` refuses any URL that isn't localhost with `test` in the database name. There is no linter or type checker configured in this repo; don't invent commands for them.

### Full local end-to-end run

1. Fake NMEA source: `uv run python scripts/simulate_nmea_source.py --port 4000`
2. `NMEA_SOURCE=tcp NMEA_HOST=127.0.0.1 NMEA_PORT=4000 AVERAGE_SECONDS=10 uv run sharp-tracker --collect` (10 s windows so fixes show up quickly)

For ship-to-shore forwarding, run a second `sharp-tracker` with its own `DATABASE_PATH` and port, and point the collector's `TRACKER_API_URL` at it (README "Test the whole system locally"). Set `NMEA_SOURCE=tcp` explicitly when testing: the local `.env` may select a serial port.

The collector always logs fixes to its local DB; if `TRACKER_API_URL` is set it also forwards them.

## Architecture

```
Ship network (NMEA TCP 192.168.20.215:4000 or serial GPS/AIS)
        v
  collector (sharp_tracker/collector.py)  -- parses NMEA, logs to local SQLite, forwards unsent rows
        v HTTPS POST /positions/batch
  web app (sharp_tracker/live.py + api.router) -- FastAPI: ingest, SSE, static MapLibre page
        v
  DB (sharp_tracker/db.py: SQLAlchemy, SQLite or Postgres)
```

One web app, `sharp_tracker.live:app`, serves both the map and ingest (`api.router` is mounted on it). The same app runs everywhere:

- Railway: the `Procfile` `web` line, one service, `DATABASE_URL` -> Railway Postgres. The collector never runs on Railway (it can't reach the ship's private LAN).
- Ship: `sharp-tracker --collect` starts the collector as a daemon thread in the app's lifespan (`COLLECT=1`), with a local SQLite DB, forwarding to Railway via `TRACKER_API_URL`.

### Module responsibilities

- `sharp_tracker/nmea.py` — pure parsing: one `parse_line()` call turns a raw NMEA sentence into `(navigation, position)` dicts via `pynmea2`. No I/O. `navigation` carries speed/course from sentences like VTG that lack a position (e.g. GGA); `collector.py` merges the last-seen nav values onto position-bearing fixes so heading survives even when a GGA fix has no COG of its own.
- `sharp_tracker/collector.py` — owns the read loop (TCP or serial, chosen by `NMEA_SOURCE`/`NMEA_SERIAL_PORT`), averages fixes into UTC-aligned `AVERAGE_SECONDS` windows (default 60; 0 stores every fix) via `averaging.Averager`, and always inserts the resulting rows into the local DB. The read loops call `close_due_window()` between reads (TCP uses a 1 s `SOURCE_POLL_SECONDS` recv timeout; serial relies on `NMEA_SERIAL_TIMEOUT`), so a window closes on time even when the GPS is silent or sending no-fix sentences, which never reach `add()`. When `TRACKER_API_URL` is set, a forwarder thread reads rows past the `forward_state` cursor and POSTs them in batches, advancing the cursor only on a 201 and backing off (up to 60 s) while the API is unreachable. A fresh `TRACKER_API_URL` on an existing local DB backfills its whole history. Source outages (`OSError`: refused/closed TCP, missing serial device) retry every 5 s, logging one warning and then a reminder every `SOURCE_WARNING_EVERY` attempts (~5 min), no tracebacks; config errors (bad `NMEA_SOURCE`, serial without `NMEA_SERIAL_PORT`) raise once before the loop.
- `sharp_tracker/averaging.py` — `Averager`: 1 Hz fixes -> one row per UTC-aligned window (midpoint `fix_time`, `sentence_type="AVG"`, summary in `raw`, SOG-weighted vector-mean course, one sample per second so GGA/RMC pairs count once), plus epoch/ISO helpers. **The Pico runs this exact file** (copied to the board), so it may only import `math` and must avoid CPython-only syntax/modules; `tests/test_averaging.py` enforces the imports.
- `sharp_tracker/db.py` — single source of truth for the `positions` table schema and DB URL resolution. `database_url()` normalizes Railway's `postgres://`/`postgresql://` to `postgresql+psycopg://` since SQLAlchemy's psycopg3 driver requires that scheme. `get_engine()` is process-wide (`lru_cache`) and calls `metadata.create_all` so schema issues fail loudly at startup rather than on first write. It also creates a unique index on `(received_at, raw)` (with raw SQL, so existing tables get it too); inserts use `ON CONFLICT DO NOTHING`, so resending a fix is always harmless. Stored times are UTC; reads tag SQLite's naive values as UTC. `forward_state` holds the collector's forwarding cursor (`get_forward_cursor`/`unsent_positions`/`mark_forwarded`).
- `sharp_tracker/api.py` — ingest: `PositionIn`/`PositionsIn` models, `ingest_batch()` (transport-independent validate + store; the seam for adding another transport such as an MQTT subscriber later), and `router` with `GET /health` (always 200 while up; reports `last_fix_received_at`/`last_fix_age_seconds` for monitoring, deliberately not failing when the ship is out of contact) and `POST /positions/batch`, Bearer-token gated via `TRACKER_INGEST_TOKEN` (if unset, ingest is unauthenticated — fine for local dev, not for a public Railway URL). There is no single-position endpoint.
- `sharp_tracker/live.py` — the web app: mounts `api.router`, starts the collector thread when `COLLECT` is set, `GET /` serves the static page, `GET /track` is the SSE stream (polls the DB for the latest fix every `LIVE_REFRESH_SECONDS`, computes heading from COG or, if absent, the bearing between the last two fixes), `GET /api/positions` returns a track window for initial page load, thinned in SQL by `db.track_positions()` (every Nth row via `row_number()`, newest always kept), `GET /api/waypoints` proxies+caches current NES-LTER station coordinates from the public NES-LTER API for map display (parsed with stdlib `csv`; the project has no pandas dependency). The page (`static/index.html`) marks the feed stale when the newest fix is older than ``stale_after`, sent in each SSE message from `STALE_AFTER_SECONDS` (default 180 s, ~3 averaging windows), because the SSE stream re-sends the last stored fix even when no new data arrives.
- `sharp_tracker/run.py` — `sharp-tracker` entry point: sets `COLLECT=1` for `--collect`, configures root logging (uvicorn only configures its own loggers), and runs uvicorn on `live:app` in-process.
- `scripts/simulate_nmea_source.py` — fake 1 Hz GPS (GGA + RMC with SOG/COG and date, `--speed-kn`/`--course-deg`) served over TCP, or written to a serial port with `--serial` (e.g. a USB-serial adapter wired to a Pico for bench tests).
- `firmware/pico_nmea_tracker/` — CircuitPython firmware for a Raspberry Pi Pico W as a hardware alternative to `collector.py`: reads NMEA over UART and POSTs to the ingest endpoint; unsent fixes wait in a bounded RAM buffer (no on-board storage). It is a separate runtime (MicroPython/CircuitPython, not CPython) and cannot import from `sharp_tracker/` — `firmware/pico_nmea_tracker/nmea_min.py` is a deliberately independent, dependency-free reimplementation of `nmea.py`'s parsing, kept schema-compatible with `PositionIn` by hand. It never touches Postgres directly; it authenticates to the ingest endpoint with `TRACKER_INGEST_TOKEN` exactly like the desktop collector, so DB credentials never live on the field device. Logic lives in pure-Python modules (`nmea_min.py`; `fixes.py` for the per-line pipeline, GPS-time helpers, and `FixBuffer`, the RAM buffer bounded by count and `gc.mem_free()`, dropping oldest first and sending newest first) that must only import each other: `tests/test_pico.py` enforces this, since CPython would accept imports the board can't. `code.py` is hardware glue around `send_batch()` (the only transport-specific function, the seam for MQTT later); `tests/test_pico_sim.py` runs it under stubbed CircuitPython modules. A flash outbox was prototyped and dropped (internal flash has no wear leveling and only held ~2 h of fixes); if on-board storage comes back, prefer an SD card. See README.md "Pico W firmware" for install/wiring steps.

### Data flow details worth knowing before touching them

- Every position row carries both `received_at` (when the collector/server saw it) and `fix_time` (parsed from the NMEA sentence's own date/time, may be null). Display code should prefer `fix_time` with `received_at` as fallback — this is exactly what `db.latest_positions()`'s `display_time` field and `live.py`'s `_compact_position` do; keep new consumers consistent with that convention rather than reading `received_at` directly.
- The collector's local DB is the source of truth on the ship: every kept fix is inserted there first, and the server only ever receives copies via the forwarder. Never make the forwarder delete or modify local rows.
