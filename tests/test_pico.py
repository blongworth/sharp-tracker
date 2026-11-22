"""Tests for the Pico firmware's pure-Python modules (run under CPython).

code.py needs CircuitPython hardware modules and is not imported here;
it is exercised under stubs in test_pico_sim.py and still needs a board smoke test.
"""

from __future__ import annotations

import ast
from datetime import date
from pathlib import Path

import pytest
from fixes import FixBuffer, FixFilter, ascii_text, fix_time_parts, iso_utc, weekday
from status import LED_FLICKER, LED_NETWORK, LED_NO_FIX, LED_NO_GPS, LED_OK, Health, led_code, led_on

FIRMWARE = Path(__file__).resolve().parents[1] / "firmware" / "pico_nmea_tracker"

# --- fixes.py -----------------------------------------------------------------


def test_fix_filter_merges_nav_like_collector(sample_lines):
    ff = FixFilter()
    kept = [p for p in (ff.process(line) for line in sample_lines) if p]
    # Same valid fixes as the desktop parser; invalid/empty/junk lines dropped.
    assert [p["sentence_type"] for p in kept] == ["GGA", "RMC", "GGA", "GGA", "RMC"]
    # The second GGA has no COG of its own; it carries the RMC/VTG nav seen earlier.
    assert kept[2]["course_deg"] == 46.0
    assert kept[2]["speed_knots"] == 8.6
    assert "received_at" not in kept[0]  # caller stamps it


def test_gps_time_reported_per_line(sample_lines):
    ff = FixFilter()
    ff.process(sample_lines[0])  # GGA: no date
    assert ff.gps_time is None
    ff.process(sample_lines[1])  # RMC
    assert ff.gps_time == "2026-11-15T12:35:19+00:00"
    ff.process(sample_lines[2])  # VTG
    assert ff.gps_time is None  # never stale: only the current line counts


def test_fix_time_parts_and_iso_round_trip():
    parts = fix_time_parts("2026-11-15T12:35:19+00:00")
    assert parts == (2026, 11, 15, 12, 35, 19)
    assert iso_utc(parts) == "2026-11-15T12:35:19+00:00"
    assert fix_time_parts(None) is None
    assert fix_time_parts("") is None
    assert fix_time_parts("2026-11-15Txx:35:19+00:00") is None


def test_weekday_matches_datetime():
    d = date(1999, 12, 25)
    for _ in range(12000):  # ~33 years, covering leap years and 2000
        assert weekday(d.year, d.month, d.day) == d.weekday(), d
        d = date.fromordinal(d.toordinal() + 1)


# --- FixBuffer (RAM) -------------------------------------------------------------


def fix(i: int) -> dict:
    return {"raw": f"$X{i}"}


def raws(fixes: list[dict]) -> list[str]:
    return [f["raw"] for f in fixes]


def test_buffer_batches_newest_first():
    buf = FixBuffer(max_fixes=100, min_free_bytes=0)
    for i in range(5):
        buf.add(fix(i))
    assert raws(buf.newest(2)) == ["$X3", "$X4"]
    buf.remove_newest(2)
    assert raws(buf.newest(10)) == ["$X0", "$X1", "$X2"]
    buf.remove_newest(3)
    assert len(buf) == 0 and buf.newest(10) == []


def test_buffer_drops_oldest_when_full():
    buf = FixBuffer(max_fixes=3, min_free_bytes=0)
    dropped = [buf.add(fix(i)) for i in range(5)]
    assert dropped == [False, False, False, True, True]
    assert raws(buf.fixes) == ["$X2", "$X3", "$X4"]
    assert buf.dropped == 2


def test_buffer_holds_size_steady_when_memory_low():
    buf = FixBuffer(max_fixes=100, min_free_bytes=10_000)
    for i in range(3):
        buf.add(fix(i), free_bytes=50_000)
    for i in range(3, 6):
        assert buf.add(fix(i), free_bytes=5_000)  # low: one in, one out
    assert raws(buf.fixes) == ["$X3", "$X4", "$X5"]


def test_buffer_never_drops_the_only_fix():
    buf = FixBuffer(max_fixes=0, min_free_bytes=10**9)
    assert not buf.add(fix(0), free_bytes=0)
    assert len(buf) == 1


def test_ascii_text_drops_line_noise(sample_lines):
    good = sample_lines[0]
    assert ascii_text(good.encode()) == good
    # 0xFF is invalid UTF-8: CircuitPython's decode() would raise on it.
    assert ascii_text(b"\xff\x80" + good.encode() + b"\xfe") == good


def test_fix_filter_flags_valid_sentences(sample_lines):
    ff = FixFilter()
    ff.process(sample_lines[0])
    assert ff.sentence_ok
    ff.process("$GPRMC,120000.00,V,,,,,,,151126,,,N*00")  # bad checksum
    assert not ff.sentence_ok
    ff.process("\x8f\xffjunk")
    assert not ff.sentence_ok


# --- status.py --------------------------------------------------------------------

GOOD = "$GPRMC,...,A,..."  # Health only sees the flags code.py passes


def test_gps_state_from_newest_evidence():
    h = Health()
    assert h.gps_state(0) == "no_data"
    h.on_bytes(0, 10)
    assert h.gps_state(0) == "garbled"
    h.on_line(0, "$GPRMC,...,V", True, None)
    assert h.gps_state(0) == "no_fix"
    h.on_line(0, GOOD, True, {"latitude": 41.5})
    assert h.gps_state(0) == "ok"
    # Each kind of evidence goes stale after GPS_STALE_SECONDS.
    h.on_line(8, "$GPRMC,...,V", True, None)
    h.on_bytes(15, 5)
    assert h.gps_state(11) == "no_fix"
    assert h.gps_state(19) == "garbled"
    assert h.gps_state(26) == "no_data"


def test_status_line_reports_and_resets_counts():
    h = Health()
    h.clock_source = "gps"
    h.on_bytes(0, 200)
    h.on_line(0, GOOD, True, {"latitude": 41.5})
    h.on_line(1, GOOD, True, {"latitude": 41.5})
    h.on_send(None)
    line = h.status_line(1, True, 3, 81234)
    assert line == (
        "Status: gps=ok lines=2 fixes=2 | wifi=up send=ok queued=3 | clock=gps | mem=81234"
    )
    h.on_bytes(20, 70)  # the last fix (t=1) is stale by now
    h.on_line(20, "$GPRMC,120000.00,V,,,,,,,151126,,,N*4E\r", True, None)
    h.on_send("HTTP 401 (check TRACKER_INGEST_TOKEN)")
    line = h.status_line(20, False, 4, None)
    assert line.startswith("Status: gps=no_fix lines=1 fixes=0 bytes=70 last=$GPRMC,120000.00,V,")
    assert line.endswith(
        "| wifi=down send=failing (HTTP 401 (check TRACKER_INGEST_TOKEN)) queued=4 | clock=gps | mem=-"
    )
    assert "\r" not in line


def test_status_line_shows_garbled_bytes_printably():
    h = Health()
    h.on_bytes(0, 64)
    h.on_line(0, "\x01\x7f" + "x" * 100, False, None)
    line = h.status_line(0, True, 0, 1000)
    assert "gps=garbled" in line and "send=-" in line and "clock=unset" in line
    assert "last=??" + "x" * 58 + " |" in line


def test_led_code_priority():
    assert led_code("ok", True, True, True) == LED_OK
    assert led_code("ok", True, None, True) == LED_OK  # nothing sent yet
    assert led_code("ok", False, True, True) == LED_NETWORK
    assert led_code("ok", True, False, True) == LED_NETWORK
    assert led_code("ok", True, True, False) == LED_NO_FIX  # fix but no date for the clock
    assert led_code("no_fix", False, False, True) == LED_NO_FIX
    assert led_code("garbled", False, False, False) == LED_NO_GPS
    assert led_code("no_data", True, True, True) == LED_NO_GPS


@pytest.mark.parametrize("code", [LED_OK, LED_NETWORK, LED_NO_FIX, LED_NO_GPS])
def test_led_blinks_code_times_per_frame(code):
    frame = [led_on(code, 6 + i / 100) for i in range(300)]  # one 3 s frame, 10 ms steps
    rising = sum(1 for prev, cur in zip([False] + frame, frame) if cur and not prev)
    assert rising == code
    assert not frame[-1]  # dark gap before the next frame


def test_led_flicker_is_fast():
    states = [led_on(LED_FLICKER, i / 100) for i in range(100)]
    assert sum(1 for a, b in zip(states, states[1:]) if a != b) >= 15


# --- CircuitPython compatibility guard ------------------------------------------

PURE_MODULES = ["nmea_min.py", "fixes.py", "status.py"]
CIRCUITPYTHON_OK_IMPORTS = {"nmea_min"}


@pytest.mark.parametrize("name", PURE_MODULES)
def test_pure_modules_only_import_circuitpython_modules(name):
    """CPython would happily run `typing`, `pathlib`, `dataclasses`...; the board won't."""
    tree = ast.parse((FIRMWARE / name).read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert imported <= CIRCUITPYTHON_OK_IMPORTS, imported - CIRCUITPYTHON_OK_IMPORTS


@pytest.mark.parametrize("name", ["code.py"])
def test_hardware_modules_parse(name):
    ast.parse((FIRMWARE / name).read_text())


# --- Deploy script --------------------------------------------------------------


def _load_deploy():
    import importlib.util

    path = FIRMWARE.parents[1] / "scripts" / "deploy_pico.py"
    spec = importlib.util.spec_from_file_location("deploy_pico", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _imports(path):
    names = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            names.add((node.module or "").split(".")[0])
    return names


def test_deploy_ships_every_local_module_and_library():
    deploy = _load_deploy()
    shipped = {name.removesuffix(".py") for _, name in deploy.FILES}
    assert deploy.FILES[-1][1] == "code.py"  # written last (auto-reload)
    imported = set().union(*(_imports(src) for src, _ in deploy.FILES))
    local = {p.stem for p in FIRMWARE.glob("*.py")} | {"averaging"}
    assert imported & local <= shipped
    libs = {
        line.strip()
        for line in (FIRMWARE / "requirements.txt").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    }
    assert {name for name in imported if name.startswith("adafruit_")} <= libs


def test_deploy_copies_files_and_keeps_settings(tmp_path):
    deploy = _load_deploy()
    (tmp_path / "boot_out.txt").write_text("Adafruit CircuitPython 9.2.1")
    deploy.deploy(tmp_path, skip_libs=True)
    for src, name in deploy.FILES:
        assert (tmp_path / name).read_bytes() == src.read_bytes()
    settings = tmp_path / "settings.toml"
    assert settings.read_text() == deploy.SETTINGS_EXAMPLE.read_text()

    settings.write_text('TRACKER_INGEST_TOKEN = "real"\n')
    deploy.deploy(tmp_path, skip_libs=True)
    assert settings.read_text() == 'TRACKER_INGEST_TOKEN = "real"\n'


def test_deploy_fails_when_a_file_is_not_listed(tmp_path, monkeypatch):
    deploy = _load_deploy()
    real_listdir = deploy.os.listdir
    monkeypatch.setattr(
        deploy.os, "listdir", lambda p: [n for n in real_listdir(p) if n != "status.py"]
    )
    with pytest.raises(SystemExit, match="status.py"):
        deploy.deploy(tmp_path, skip_libs=True)


def test_code_py_decodes_uart_bytes_via_ascii_text():
    """CircuitPython's bytes.decode() raises on invalid UTF-8 whatever `errors` says,
    and CPython (the simulator) can't show that: decode only through ascii_text()."""
    tree = ast.parse((FIRMWARE / "code.py").read_text())
    calls = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "decode"
    ]
    assert calls == [], f"code.py calls .decode() directly on lines {calls}"
