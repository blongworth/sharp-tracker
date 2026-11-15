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
        self.ntp_up = False
        self.mem_free = 100_000
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
        return nmea_gen.sentences(second, self.rmc)


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
    module("board", GP0="GP0", GP1="GP1")
    module("gc", collect=lambda: None, mem_free=lambda: world.mem_free)
    # code.py imports the shared module copied to the board from sharp_tracker/.
    monkeypatch.setitem(sys.modules, "averaging", averaging)

    class Uart:
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
        def __init__(self, pool, tz_offset=0):
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
