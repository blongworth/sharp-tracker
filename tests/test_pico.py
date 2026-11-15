"""Tests for the Pico firmware's pure-Python modules (run under CPython).

code.py needs CircuitPython hardware modules and is not imported here;
it is exercised under stubs in test_pico_sim.py and still needs a board smoke test.
"""

from __future__ import annotations

import ast
from datetime import date
from pathlib import Path

import pytest
from fixes import FixBuffer, FixFilter, fix_time_parts, iso_utc, weekday

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


# --- CircuitPython compatibility guard ------------------------------------------

PURE_MODULES = ["nmea_min.py", "fixes.py"]
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
