"""Run the real firmware code.py under CPython against simulated hardware.

Stubs stand in for the CircuitPython modules (board, busio, wifi, rtc, ...),
with a fake clock that advances one second per UART read, so long outages run
in milliseconds. This exercises code.py's control flow (WiFi loss, server
outages, the bounded RAM buffer, low memory, GPS clock); it does not prove the
real CircuitPython APIs behave as stubbed, so a board smoke test is still needed.
"""

from __future__ import annotations

import calendar
import runpy
import sys
import time as real_time
import types
from datetime import datetime, timezone
from pathlib import Path

import nmea_gen
import pytest

from sharp_tracker import averaging

CODE_PY = (
    Path(__file__).resolve().parents[1] / "firmware" / "pico_nmea_tracker" / "code.py"
)
START = datetime(2026, 11, 15, 12, 0, 0, tzinfo=timezone.utc)


class SimEnd(BaseException):
    """UART script exhausted. BaseException so code.py's `except Exception` can't swallow it."""


class Rebooted(BaseException):
    """microcontroller.reset() was called."""


class World:
    """Simulated time, network, and server state; scenario hooks run per tick."""

    def __init__(self, seconds: int, rmc: bool = True):
        self.t = 0.0
        self.rtc_base = None  # (epoch seconds, monotonic t) once the RTC is set
        self.seconds = seconds
        self.rmc = rmc
        self.wifi_up = True
        self.server_up = True
        self.server_status = 201  # HTTP status the reachable server answers with
        self.ntp_up = False
        self.mem_free = 100_000
        # "fix", "no_fix" (talking, quality 0/status V), "garbled" (wrong baud), or "silent"
        self.gps = "fix"
        self.led: list[bool] = []  # every LED write
        self.posts: list[list[dict]] = []
        self.attempts = 0
        self.hooks: dict[int, callable] = {}

    # time module
    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.t += s

    def localtime(self):
        if self.rtc_base is None:
            return real_time.gmtime(
                946684800 + self.t
            )  # board RTC starts at 2000-01-01
        epoch, at = self.rtc_base
        return real_time.gmtime(epoch + self.t - at)

    def set_rtc(self, st):
        self.rtc_base = (calendar.timegm(tuple(st)[:6] + (0, 0, 0)), self.t)

    def uart_read(self, n):
        second = int(self.t)
        if second >= self.seconds:
            raise SimEnd
        if second in self.hooks:
            self.hooks.pop(second)(self)
        self.t += 1
        if self.gps == "silent":
            return b""
        if self.gps == "garbled":
            # Invalid UTF-8 with line ends, like a wrong baud rate or power-up noise.
            return bytes(range(0x80, 0xC0)) + b"\r\n"
        return nmea_gen.sentences(second, self.rmc, valid=self.gps == "fix")


def install_stubs(monkeypatch, world: World):
    def module(name, **attrs):
        mod = types.ModuleType(name)
        mod.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, mod)
        return mod

    module(
        "time",
        monotonic=world.monotonic,
        sleep=world.sleep,
        localtime=world.localtime,
        struct_time=real_time.struct_time,
    )
    module("board", GP0="GP0", GP1="GP1", LED="LED")

    class Led:
        direction = None

        @property
        def value(self):
            return world.led[-1] if world.led else False

        @value.setter
        def value(self, on):
            world.led.append(on)

    module(
        "digitalio",
        DigitalInOut=lambda pin: Led(),
        Direction=types.SimpleNamespace(OUTPUT="OUTPUT"),
    )
    module("gc", collect=lambda: None, mem_free=lambda: world.mem_free)
    # code.py imports the shared module copied to the board from sharp_tracker/.
    monkeypatch.setitem(sys.modules, "averaging", averaging)

    class Uart:
        in_waiting = 256  # the fake clock advances per read, so always read

        def __init__(self, *a, **k):
            pass

        def read(self, n):
            return world.uart_read(n)

    module("busio", UART=Uart)

    class Watchdog:
        timeout = 0
        mode = None

        def feed(self):
            pass

    def reset():
        raise Rebooted

    module("microcontroller", watchdog=Watchdog(), reset=reset)
    module("watchdog", WatchDogMode=types.SimpleNamespace(RESET="RESET"))

    class Rtc:
        @property
        def datetime(self):
            return world.localtime()

        @datetime.setter
        def datetime(self, st):
            world.set_rtc(st)

    module("rtc", RTC=Rtc)

    class Radio:
        ipv4_address = "10.0.0.2"

        @property
        def connected(self):
            return world.wifi_up

        def connect(self, ssid, password, timeout=None):
            world.t += 1  # connecting takes time
            if not world.wifi_up:
                raise ConnectionError("no AP")

    module("wifi", radio=Radio())
    module("socketpool", SocketPool=lambda radio: object())
    module("ssl", create_default_context=lambda: object())

    class Ntp:
        def __init__(self, pool, tz_offset=0, socket_timeout=10):
            if not world.ntp_up:
                raise OSError("NTP timeout")

        @property
        def datetime(self):
            return real_time.gmtime(calendar.timegm(START.timetuple()))

    module("adafruit_ntp", NTP=Ntp)

    class Response:
        def __init__(self, status):
            self.status_code = status

        def close(self):
            pass

    class Session:
        def __init__(self, pool, ctx):
            pass

        def post(self, url, json, headers, timeout):
            world.attempts += 1
            world.t += 0.5
            if not (world.wifi_up and world.server_up):
                raise OSError("unreachable")
            if world.server_status != 201:
                return Response(world.server_status)
            world.posts.append(json["positions"])
            return Response(201)

    module("adafruit_requests", Session=Session)


@pytest.fixture
def run_firmware(monkeypatch):
    def run(world: World, **env):
        settings = {
            "CIRCUITPY_WIFI_SSID": "ship",
            "CIRCUITPY_WIFI_PASSWORD": "pw",
            "TRACKER_API_URL": "https://ingest.example",
            # 5 s windows keep fix counts high enough to exercise the RAM buffer;
            # test_default_one_minute_averaging covers the 60 s default.
            "AVERAGE_SECONDS": "5",
            **env,
        }
        for key, value in settings.items():
            monkeypatch.setenv(key, value)
        install_stubs(monkeypatch, world)
        with pytest.raises(SimEnd):
            runpy.run_path(str(CODE_PY))

    return run


def delivered(world: World) -> list[str]:
    """fix_time of every delivered fix (unique per averaging window)."""
    return [p["fix_time"] for batch in world.posts for p in batch]


def fix_second(fix_time: str) -> int:
    """Seconds after START for an ISO fix_time (window midpoint)."""
    return int((datetime.fromisoformat(fix_time) - START).total_seconds())


def test_normal_operation_sets_clock_from_gps(run_firmware):
    world = World(seconds=60)
    run_firmware(world)
    fixes = [p for batch in world.posts for p in batch]
    assert 10 <= len(fixes) <= 13  # one per 5 s
    assert len(set(delivered(world))) == len(fixes)
    # NTP was down: received_at comes from the GPS-set clock, not 2000-01-01.
    assert fixes[0]["received_at"].startswith("2026-11-15T12:00")


def test_server_outage_held_in_ram_then_caught_up(run_firmware):
    world = World(seconds=1500)
    world.hooks[30] = lambda w: setattr(w, "server_up", False)
    world.hooks[800] = lambda w: setattr(w, "server_up", True)
    run_firmware(world)

    raws = delivered(world)
    assert len(raws) == len(set(raws))  # nothing sent twice
    assert 280 <= len(raws) <= 300  # ~one fix per 5 s over 1500 s: nothing lost
    # Backoff: retries during the 770 s outage are a few dozen, not one per loop pass.
    assert world.attempts < len(world.posts) + 40
    # No POST larger than BATCH_SIZE, even with ~150 fixes waiting.
    assert max(len(b) for b in world.posts) <= 20


def test_first_post_after_outage_is_newest_fixes(run_firmware):
    world = World(seconds=900)
    world.hooks[30] = lambda w: setattr(w, "server_up", False)
    world.hooks[600] = lambda w: setattr(w, "server_up", True)
    run_firmware(world)
    sent_before = sum(
        1 for b in world.posts if max(fix_second(p["fix_time"]) for p in b) < 30
    )
    first_after = world.posts[sent_before]
    assert (
        min(fix_second(p["fix_time"]) for p in first_after) > 400
    )  # the map catches up first


def test_long_outage_keeps_newest_fixes(run_firmware):
    world = World(seconds=900)
    world.hooks[10] = lambda w: setattr(w, "server_up", False)
    world.hooks[700] = lambda w: setattr(w, "server_up", True)
    run_firmware(world, MAX_BUFFER="50")
    raws = delivered(world)
    assert len(raws) == len(set(raws))
    outage = sorted(fix_second(r) for r in raws if 10 <= fix_second(r) < 700)
    # Only MAX_BUFFER of ~138 outage fixes survive. Fixes logged after the server
    # returns, while the backoff timer (up to 60 s) runs, share the 50 slots too.
    assert 35 <= len(outage) <= 50
    assert outage[0] > 400  # the survivors are the newest ones


def test_wifi_loss_keeps_logging_without_reboot(run_firmware):
    world = World(seconds=1200)
    world.hooks[20] = lambda w: setattr(w, "wifi_up", False)
    world.hooks[700] = lambda w: setattr(w, "wifi_up", True)
    run_firmware(world)  # a reboot would raise Rebooted, not SimEnd
    raws = delivered(world)
    assert len(raws) == len(set(raws))
    assert len(raws) >= 225  # fixes from the WiFi outage arrived after reconnect


def test_low_memory_bounds_buffer_without_crashing(run_firmware):
    world = World(seconds=900)
    world.hooks[10] = lambda w: setattr(w, "server_up", False)
    world.hooks[300] = lambda w: setattr(w, "mem_free", 10_000)  # below MIN_FREE_BYTES
    world.hooks[700] = lambda w: setattr(w, "server_up", True)
    run_firmware(world)
    outage = [r for r in delivered(world) if 10 <= fix_second(r) < 700]
    # Grew until memory ran low at 300 s (~58 fixes), then held steady; as above,
    # fixes logged during the post-outage backoff take some of those slots.
    assert 40 <= len(outage) <= 62


def test_no_fixes_logged_until_clock_is_known(run_firmware):
    world = World(seconds=60, rmc=False)  # GGA only, no NTP: no source of the date
    run_firmware(world)
    assert world.posts == []


def test_ntp_sets_clock_when_available(run_firmware):
    world = World(seconds=30, rmc=False)
    world.ntp_up = True
    run_firmware(world)
    assert world.posts and world.posts[0][0]["received_at"].startswith("2026-11-15")


def test_default_one_minute_averaging(run_firmware):
    world = World(seconds=200)
    run_firmware(world, AVERAGE_SECONDS="60")
    fixes = [p for batch in world.posts for p in batch]
    # Windows 12:00, 12:01, 12:02 closed (12:03 still open at 200 s).
    assert [p["fix_time"] for p in fixes] == [
        "2026-11-15T12:00:30+00:00",
        "2026-11-15T12:01:30+00:00",
        "2026-11-15T12:02:30+00:00",
    ]
    assert fixes[1]["raw"].startswith("AVG 60s n=60 ")
    assert fixes[0]["sentence_type"] == "AVG"
    assert fixes[1]["course_deg"] == pytest.approx(45.0)


@pytest.mark.parametrize("gps", ["silent", "no_fix"])
def test_window_closes_on_time_when_gps_stops(run_firmware, gps):
    world = World(seconds=200)
    world.hooks[90] = lambda w: setattr(
        w, "gps", gps
    )  # mid-way through the 12:01 window
    run_firmware(world, AVERAGE_SECONDS="60")
    fixes = [p for batch in world.posts for p in batch]
    assert [p["fix_time"] for p in fixes] == [
        "2026-11-15T12:00:30+00:00",
        "2026-11-15T12:01:30+00:00",  # partial window still sent...
    ]
    assert "n=30 " in fixes[1]["raw"]
    # ...at the window's end, not whenever a valid fix next appears.
    assert fixes[1]["received_at"] == "2026-11-15T12:02:00+00:00"


def status_lines(capsys) -> list[str]:
    return [line for line in capsys.readouterr().out.splitlines() if line.startswith("Status:")]


@pytest.mark.parametrize(
    ("gps", "expected"),
    [
        ("fix", "gps=ok "),
        ("no_fix", "gps=no_fix "),
        ("garbled", "gps=garbled "),
        ("silent", "gps=no_data "),
    ],
)
def test_status_line_reports_gps_state(run_firmware, capsys, gps, expected):
    world = World(seconds=125)
    world.ntp_up = True  # clock set without a GPS fix, so only the GPS state varies
    world.gps = gps
    run_firmware(world, STATUS_SECONDS="60")
    lines = status_lines(capsys)
    assert len(lines) == 2  # at 60 s and 120 s
    assert all(expected in line for line in lines), lines
    assert "clock=ntp" in lines[-1]
    if gps == "no_fix":
        assert "last=$GPRMC," in lines[-1]
    # Healthy: the LED blinks a heartbeat; otherwise it still changes state.
    assert True in world.led and False in world.led


def test_status_line_reports_failing_sends(run_firmware, capsys):
    world = World(seconds=125)
    world.hooks[30] = lambda w: setattr(w, "server_up", False)
    run_firmware(world, STATUS_SECONDS="60")
    last = status_lines(capsys)[-1]
    assert "gps=ok " in last and "send=failing" in last and "clock=gps" in last
    assert "queued=0" not in last


def test_status_line_disabled(run_firmware, capsys):
    run_firmware(World(seconds=125), STATUS_SECONDS="0")
    assert status_lines(capsys) == []


def test_fatal_error_flickers_then_reboots(run_firmware):
    world = World(seconds=60)
    with pytest.raises(Rebooted):
        run_firmware(world, TRACKER_API_URL="")
    assert world.led[0] is True  # solid on while booting
    assert len(world.led) > 20  # flickered for the 5 s before reset


def test_rejected_sends_report_the_http_status(run_firmware, capsys):
    world = World(seconds=125)
    world.server_status = 401
    run_firmware(world, STATUS_SECONDS="60")
    out = capsys.readouterr().out
    assert "Send failed: HTTP 401 (check TRACKER_INGEST_TOKEN)" in out
    last = [line for line in out.splitlines() if line.startswith("Status:")][-1]
    assert "send=failing (HTTP 401 (check TRACKER_INGEST_TOKEN))" in last
    assert world.posts == []


def test_network_errors_report_the_exception(run_firmware, capsys):
    world = World(seconds=125)
    world.hooks[30] = lambda w: setattr(w, "server_up", False)
    run_firmware(world, STATUS_SECONDS="60")
    out = capsys.readouterr().out
    assert "Send failed: OSError('unreachable')" in out
