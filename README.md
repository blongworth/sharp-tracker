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

## Averaging

GPS receivers send fixes at 1 Hz. Both the desktop collector and the Pico
average them into windows of `AVERAGE_SECONDS` (default 60) aligned to the UTC
clock (12:00:00–12:00:59, 12:01:00–…), and store or send one fix per window:

- `fix_time` is the window midpoint (12:00:30); `received_at` is when the
  window closed: at the first fix of the next window or, if the GPS goes quiet
  or loses its fix, when the window's end passes (within about 1 s for TCP and
  the Pico, `NMEA_SERIAL_TIMEOUT` for serial). A partial window shows its
  sample count in `raw` (`n=30`). Windows with no valid fixes produce no row.
- Position and altitude are means; speed is the mean SOG; course is the
  direction of the mean velocity vector, so 359° and 1° average to 0°.
- Quality and satellite count are the worst seen in the window.
- `sentence_type` is `AVG` and `raw` summarizes the window instead of holding an
  NMEA sentence, e.g. `AVG 60s n=58 2026-11-15T12:00:00Z/2026-11-15T12:01:00Z`.
- A GGA/RMC pair for the same second counts once.

`AVERAGE_SECONDS=0` stores every fix as received. The averaging code is
`sharp_tracker/averaging.py`; the Pico runs the same file.

Because fixes now arrive once a minute, the map flags the feed stale only when
the newest fix is older than `STALE_AFTER_SECONDS` (default 180, about three
windows). Raise it if you use a longer averaging window.

## Run without Railway

Everything runs on laptops with SQLite; Railway is only "the shore laptop in
the cloud". For quick tests, set `AVERAGE_SECONDS=10` so a fix appears every
10 s instead of every minute.

**One laptop, fake GPS** (the simulator sends 1 Hz GGA + RMC at 10 kn, 045°):

```bash
uv run python scripts/simulate_nmea_source.py --port 4000                          # terminal 1
NMEA_SOURCE=tcp NMEA_HOST=127.0.0.1 NMEA_PORT=4000 AVERAGE_SECONDS=10 \
  uv run sharp-tracker --collect                                                    # terminal 2
open http://127.0.0.1:8090
```

**One laptop, USB GPS:** `NMEA_SOURCE=serial NMEA_SERIAL_PORT=/dev/tty.usbserial-XXXX
NMEA_SERIAL_BAUD=9600 uv run sharp-tracker --collect` (many modern GPS modules
default to 9600 baud; marine NMEA 0183 is usually 4800).

**Ship and shore on two laptops** (same LAN or WiFi):

```bash
# shore laptop: find its address with `ipconfig getifaddr en0` (macOS)
TRACKER_INGEST_TOKEN=test-token uv run sharp-tracker --host 0.0.0.0
# ship laptop
NMEA_SOURCE=tcp NMEA_HOST=127.0.0.1 NMEA_PORT=4000 AVERAGE_SECONDS=10 \
  TRACKER_API_URL=http://<shore-ip>:8090 TRACKER_INGEST_TOKEN=test-token \
  uv run sharp-tracker --collect
```

macOS may ask the shore laptop to allow incoming connections. Each laptop has
its own map; the ship's works with no network at all. On one laptop, give the
two instances different `DATABASE_PATH`s and ports. Stop the shore instance for
a while and restart it: the ship keeps logging and catches the shore up.

### Getting the log off the ship

The collector's SQLite database runs in WAL mode, so recent rows may live in
`positions.sqlite3-wal` rather than the main file. Copying `positions.sqlite3`
alone can miss them. Use SQLite's online backup instead; it is safe while the
collector is running:

```bash
sqlite3 data/positions.sqlite3 ".backup 'cruise-2026-11.sqlite3'"
# without the sqlite3 CLI:
uv run python -c "import sqlite3; sqlite3.connect('data/positions.sqlite3').backup(sqlite3.connect('cruise-2026-11.sqlite3'))"
```

For a CSV: `sqlite3 -header -csv data/positions.sqlite3 "select fix_time,
latitude, longitude, speed_knots, course_deg, raw from positions order by
fix_time" > track.csv`.

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

## Time to go / ETA

The app computes distance, time to go (TTG) and ETA from the latest fix to a
target, which is one of:
- a NES-LTER station;
- a point you pick on the map;
- **auto**: the nearest station within ±30° of the ship's course, more than
  0.5 nm away, so the station you're on is skipped.

Speed is the mean SOG over the last 30 min unless you give a planned speed.
There's no TTG when the ship is stopped (under 0.5 kn). The ETA counts from the
fix time, so clients count TTG down between updates.

Where to see it:
- **Map:** use the HUD's **Target** dropdown, click a station dot, or
  right-click / long-press the map to target a point. It shows Dist, TTG and
  ETA, and the line goes to the target.
- **`/eta` page:** the chosen target in large type, plus every station sorted
  by distance with its TTG and ETA. Tap a row to target it.

Each browser keeps its own target and planned speed (in `localStorage`), and
the map and `/eta` share them.

API:

| Request | Returns |
| --- | --- |
| `GET /api/eta?station=L5` | ETA to a station (404 if unknown; 503 if the station list can't be loaded) |
| `GET /api/eta?lat=41.1&lon=-70.9&name=Buoy` | ETA to a point |
| `GET /api/eta` | ETA to `ETA_DEFAULT_TARGET` (default `auto`) |
| `...&speed_kn=10` | Use a planned speed instead of mean SOG |
| `GET /api/eta/stations` | `{"stations": [...], "speed_kn", "fix_epoch"}`: every station, nearest first, each with its `vmg_kn` |

Every `/api/eta` response has the same keys, with nulls when a value is
unknown:
- `target` and `reason` (why there's no target, e.g. `no station ahead`);
- `distance_nm`, `bearing_deg`, `ttg_s`;
- `eta` (ISO) and `eta_epoch`;
- `vmg_kn`: speed made good toward the target at the fix (SOG × cos of the
  angle between COG and the bearing; negative when moving away). Between
  fixes the map, the `/eta` page and the watch dead-reckon with it: the
  distance shrinks at the VMG, and TTG is that distance at the speed basis;
- `speed_kn` and `speed_source` (`sog_30min` or `planned`);
- `fix_time`, `fix_epoch`, `fix_age_s` and `stale`.

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

For a field build (3D-printed enclosure, USB wall power, opto-isolated DB9 GPS input),
see [`hardware/pico_enclosure/`](hardware/pico_enclosure/README.md).

### Install

1. Flash the latest [CircuitPython UF2 for Raspberry Pi Pico W](https://circuitpython.org/board/raspberry_pi_pico_w/)
   by holding BOOTSEL while plugging in the Pico, then dragging the UF2 onto the
   `RPI-RP2` drive that appears. It reboots as a `CIRCUITPY` USB drive.
2. With the board mounted as `CIRCUITPY`, run:

   ```bash
   uv run python scripts/deploy_pico.py      # or --path /Volumes/CIRCUITPY
   ```

   This uses [circup](https://github.com/adafruit/circup) (a dev dependency) to
   install the libraries in `firmware/pico_nmea_tracker/requirements.txt` into
   `CIRCUITPY/lib/`, choosing the builds that match the board's CircuitPython
   version and adding their dependencies (`adafruit_connection_manager`). It then
   copies `nmea_min.py`, `fixes.py`, `sharp_tracker/averaging.py` (shared with the
   desktop collector), and `code.py` (last, because CircuitPython reloads on every
   write), skipping files that haven't changed. Re-run it after any firmware change;
   `--skip-libs` copies only the firmware files and works offline.
3. On the first deploy the script creates `CIRCUITPY/settings.toml` from
   `settings.toml.example`; fill in your WiFi credentials, `TRACKER_API_URL`, and
   `TRACKER_INGEST_TOKEN` (the same ingest URL/token used by the desktop collector
   above). Later deploys never overwrite it. `settings.toml` is gitignored — never
   commit your filled-in copy.

   Without the script: copy `adafruit_requests.mpy`, `adafruit_connection_manager.mpy`,
   and `adafruit_ntp.mpy` from the matching
   [Adafruit CircuitPython Bundle](https://circuitpython.org/libraries) into
   `CIRCUITPY/lib/`, the four files above to the root of `CIRCUITPY`, and the
   example settings to `CIRCUITPY/settings.toml`.

### Use

The Pico runs `code.py` automatically on boot. Open a serial console (e.g.
`screen /dev/tty.usbmodemXXXX 115200`, or the Mu editor's serial pane) to watch
it connect, set its clock, and log each fix.

- **Clock:** set from NTP if reachable, otherwise from the GPS (first valid RMC),
  and re-set from GPS hourly. No fixes are logged until the clock is known, so
  `received_at` is never a bogus 2000-01-01.
- **Averaging:** 1 Hz fixes are averaged into UTC-aligned windows of
  `AVERAGE_SECONDS` (default 60), same as the desktop collector (see
  "Averaging" above).
- **Sending:** fixes are POSTed in batches of `BATCH_SIZE` to `/positions/batch`.
  Sends back off (up to 60 s) while they fail; WiFi is reconnected in the
  background every 30 s, and logging continues throughout.
- **Outages:** unsent fixes wait in RAM: up to `MAX_BUFFER` (default 500, about
  8 hours of 1-minute averages), or fewer if free memory drops below `MIN_FREE_BYTES`. When
  full, the oldest fixes are dropped. After the link returns, the newest fixes
  are sent first so the map catches up, then the rest, a few batches per cycle.
  There is no on-board storage: fixes still in RAM are lost if the board
  reboots or loses power. For longer outages, use the desktop collector (which
  logs every fix to SQLite) or add storage to the Pico later.
- **Watchdog:** if the main loop hangs for `WATCHDOG_SECONDS` (default 8; 0
  disables), the board reboots. Any other unexpected error also reboots after
  5 s.

#### Status line

Every `STATUS_SECONDS` (default 60; 0 disables) the console prints one line
covering the interval since the last one:

```
Status: gps=ok lines=120 fixes=120 | wifi=up send=ok queued=0 | clock=gps | mem=81234
Status: gps=no_fix lines=118 fixes=0 bytes=8512 last=$GPRMC,120000.00,V,,,,,,,151126,,,N*4E | wifi=up send=- queued=0 | clock=ntp | mem=80112
```

`gps=` reflects the last 10 s of input. If it isn't `ok`, the line also shows the
bytes received and the last line from the GPS.

| `gps=` | Meaning | Check |
| --- | --- | --- |
| `ok` | Position fixes arriving | |
| `no_fix` | Valid NMEA, but no fix (RMC status `V`, GGA quality 0) | Antenna sky view; give a cold GPS a few minutes |
| `garbled` | Bytes arriving, but no valid NMEA sentences | `NMEA_SERIAL_BAUD`; signal inverted (RS-232 straight into GP1) |
| `no_data` | Nothing on GP1 | GPS TX → GP1, common ground, GPS power |

`send=` is `ok`, `-` (nothing sent yet), or `failing (<reason>)`: the last
send failed and fixes are queuing in RAM (see `queued=`). The reason is
`WiFi down`, a network error, or an HTTP status from the server; `HTTP 401` or
`403` means `TRACKER_INGEST_TOKEN` doesn't match the server's. `clock=` is `ntp`, `gps`, or `unset`
(nothing is logged until it's set).

#### LED

The onboard LED (only visible with the lid off) shows the most severe problem:

| LED | Meaning |
| --- | --- |
| Solid on | Booting: connecting to WiFi / NTP |
| 1 short blink every 3 s | All OK |
| 2 blinks every 3 s | Network: WiFi down or sends failing (fixes queue in RAM) |
| 3 blinks every 3 s | GPS talking but no usable fix (no fix, or no date yet to set the clock) |
| 4 blinks every 3 s | No GPS data, or garbled (wiring or baud rate) |
| Fast flicker | Fatal error; rebooting in 5 s |
| Stuck on or off | Blocked in a network call (up to ~5 s) or hung; the watchdog reboots it |

### Bench test on hardware

1. On a laptop on the same WiFi: `TRACKER_INGEST_TOKEN=test-token uv run
   sharp-tracker --host 0.0.0.0` (see "Run without Railway").
2. In `settings.toml`: `TRACKER_API_URL = "http://<laptop-ip>:8090"` (plain HTTP
   is fine on a LAN), the same token, `AVERAGE_SECONDS = "10"` for quick
   feedback, `NMEA_SERIAL_BAUD` to match the GPS, and `WATCHDOG_SECONDS = "0"`
   while you are working in the REPL (set it back to 8 afterwards).
3. GPS source: a GPS module (TX → GP1, GND → GND, power per the module), or the
   simulator through a 3.3 V USB-serial adapter (adapter TX → GP1, GND → GND):
   `uv run python scripts/simulate_nmea_source.py --serial /dev/tty.usbserial-XXXX --baud 4800`.
4. Watch the serial console (`screen /dev/tty.usbmodemXXXX 115200`): expect
   `Clock set from GPS` (or NTP), then a `Fix:` line per window, and the fix on
   the laptop's map within a few seconds.

Then break things on purpose:

| Do this | Expect |
| --- | --- |
| Stop the laptop server ~5 min, restart | `Send failed` with growing gaps (≤ 60 s); after restart the newest fixes arrive first, then the backlog; no gaps or duplicates in the laptop DB |
| Turn off the WiFi access point ~5 min | `WiFi unavailable` about every 30–60 s, fixes keep queuing, no reboot; everything arrives after WiFi returns |
| Keep WiFi up but cut its internet (unplug the router's WAN, or use an isolated AP), with `WATCHDOG_SECONDS = "8"`; also boot the board this way | `Send failed` with a network error, fixes keep queuing, **no watchdog reboots** (a reboot would lose the queue). If it reboots, DNS or TLS is blocking longer than the watchdog allows |
| Stop the laptop server 30+ min, restart | Sends resume after the restart without a reboot (if they keep failing until a reboot, the HTTP session isn't recovering) |
| Wrong `TRACKER_INGEST_TOKEN` | `Send failed: HTTP 401 (check TRACKER_INGEST_TOKEN)`, `send=failing (HTTP 401 ...)`, 2 LED blinks |
| Shield the GPS antenna / unplug the GPS mid-window | No new `Fix:` lines; the partial window is still sent at the window's end with a lower `n` |
| Boot with no NTP and the GPS indoors | Nothing logged until the first valid RMC sets the clock |
| `WATCHDOG_SECONDS = "8"`, then add `time.sleep(20)` inside the main loop | Board reboots after ~8 s (remove the sleep afterwards) |
| After a long outage, in the REPL: `import gc; gc.collect(); gc.mem_free()` | Comfortably above `MIN_FREE_BYTES`; otherwise lower `MAX_BUFFER` |

### Testing

The firmware's logic (`nmea_min.py`, `fixes.py`) is plain Python and is
unit-tested on a laptop (`tests/test_pico.py`). `tests/test_pico_sim.py` runs
the real `code.py` against simulated hardware: a fake UART, WiFi, server,
clock, and memory. It covers server and WiFi outages, the RAM limits, the GPS
clock, and NTP. It can't prove the CircuitPython APIs behave as stubbed, so do
a smoke test on a real board before deploying.

## Garmin watch app (Connect IQ, Forerunner 165)

`garmin/sharp_eta/` is a Connect IQ widget with a glance. It shows the target,
TTG, ETA (local), distance and fix age. Between fixes it re-estimates every
second: the distance shrinks at the VMG from the last fix, and TTG is that
distance at the speed basis, so off course it counts down slower than the
clock and heading away it counts up. Press SELECT to pick **Auto (ahead)**
or a station (nearest first). The choice is kept on the watch.

**How it connects:** the watch has no internet of its own.
`makeWebRequest` goes over Bluetooth to the Garmin Connect app on the paired
phone, which fetches `/api/eta`. So:
- the phone must be within Bluetooth range;
- the phone must be able to reach the server;
- Connect IQ expects **https**, which Railway provides. A plain-HTTP ship
  server may be refused, especially on iOS ("Needs https://" on the watch).

The glance never fetches. It shows the last data the widget fetched, counted
down, and dims it when the fix is more than 5 min old. Refresh happens every
60 s while the widget is open.

**Build and test.** You need the Connect IQ SDK and the `fr165` device files,
installed with Garmin's SDK Manager (Garmin account required):

```bash
cd garmin/sharp_eta
make key        # once: developer key in garmin/ (gitignored)
make test       # Monkey C unit tests in the simulator
make sim        # run in the simulator against http://localhost:8090
make release    # bin/sharp_eta_release.prg for the watch
```

`make sim SERVER=http://localhost:8091` points the debug build at another
port. If the watch shows "Needs https://", turn off **Settings → Use Device
HTTPS Requirements** in the simulator to allow a plain-HTTP local server.

**Install on the watch.**
1. Copy `bin/sharp_eta_release.prg` to the watch's `GARMIN/APPS` folder over
   USB. On macOS, use Garmin Express or OpenMTP to see the watch's files.
2. In Garmin Connect on the phone, set the app's **Server URL** (e.g.
   `https://your-app.up.railway.app`) and optionally a planned speed.

The unit tests in `source/test/` parse `resources/jsonData/eta_sample.json`.
`tests/test_server.py` checks that the real `/api/eta` output has the same
keys and types, so a format change breaks a test on one side or the other.

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
AVERAGE_SECONDS=60          # 0 = store every fix
STALE_AFTER_SECONDS=180     # map flags the feed stale past this fix age
LIVE_HOST=127.0.0.1
LIVE_PORT=8090
LIVE_REFRESH_SECONDS=10
ETA_DEFAULT_TARGET=auto     # /api/eta target when none is given: auto or a station id
ETA_PLANNED_SPEED_KN=       # unset: mean SOG; set to use a planned speed by default
ETA_SOG_MINUTES=30          # window for the mean SOG
LOG_LEVEL=INFO
```
