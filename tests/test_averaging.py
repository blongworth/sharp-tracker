from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from sharp_tracker.averaging import Averager, epoch_from_parts, iso_utc_from_epoch

T0 = datetime(2026, 11, 15, 12, 0, 0, tzinfo=timezone.utc)


def fix(lat=41.5, lon=-70.6, sog=None, cog=None, **extra):
    return {
        "latitude": lat,
        "longitude": lon,
        "speed_knots": sog,
        "course_deg": cog,
        "talker": "GP",
        **extra,
    }


def run(av: Averager, samples) -> list[dict]:
    """samples: (seconds after T0, fix). Returns every completed window."""
    out = []
    for s, p in samples:
        done = av.add((T0 + timedelta(seconds=s)).timestamp(), p)
        if done:
            out.append(done)
    return out


def test_windows_align_to_utc_minutes_and_emit_on_next_window():
    av = Averager(60)
    # 1 Hz from 12:00:30 to 12:02:05: partial first window, one full, one open.
    out = run(av, [(s, fix(lat=41.5 + s / 1e5)) for s in range(30, 126)])
    assert [o["fix_time"] for o in out] == [
        "2026-11-15T12:00:30+00:00",
        "2026-11-15T12:01:30+00:00",
    ]
    assert out[0]["raw"] == "AVG 60s n=30 2026-11-15T12:00:00Z/2026-11-15T12:01:00Z"
    assert out[1]["raw"] == "AVG 60s n=60 2026-11-15T12:01:00Z/2026-11-15T12:02:00Z"
    assert out[1]["latitude"] == pytest.approx(
        41.5 + 89.5 / 1e5
    )  # mean of seconds 60..119
    assert out[0]["sentence_type"] == "AVG"


def test_gga_rmc_pair_in_same_second_counts_once():
    av = Averager(60)
    samples = []
    for s in range(60):
        samples += [(s, fix(lat=41.0)), (s + 0.4, fix(lat=42.0))]  # GGA then RMC
    samples.append((60, fix()))
    (out,) = run(av, samples)
    assert "n=60" in out["raw"]
    assert out["latitude"] == pytest.approx(41.0)  # first sentence of each second


def test_course_is_circular_and_speed_is_scalar_mean():
    av = Averager(10)
    samples = [(s, fix(sog=10.0, cog=359.0 if s % 2 else 1.0)) for s in range(10)] + [
        (10, fix())
    ]
    (out,) = run(av, samples)
    assert out["course_deg"] == pytest.approx(0.0, abs=1e-6) or out[
        "course_deg"
    ] == pytest.approx(360.0)
    assert out["speed_knots"] == pytest.approx(10.0)


def test_course_weighted_by_speed_and_none_when_stationary():
    av = Averager(10)
    samples = [(0, fix(sog=9.0, cog=90.0)), (1, fix(sog=1.0, cog=270.0)), (10, fix())]
    (out,) = run(av, samples)
    assert out["course_deg"] == pytest.approx(90.0)
    assert out["speed_knots"] == pytest.approx(5.0)

    av = Averager(10)
    (still,) = run(av, [(0, fix(sog=0.0, cog=123.0)), (10, fix())])
    assert still["course_deg"] is None
    (no_nav,) = run(Averager(10), [(0, fix()), (10, fix())])
    assert no_nav["course_deg"] is None and no_nav["speed_knots"] is None


def test_course_without_speed_uses_unit_vectors():
    (out,) = run(Averager(10), [(0, fix(cog=350.0)), (1, fix(cog=20.0)), (10, fix())])
    assert out["course_deg"] == pytest.approx(5.0)


def test_longitude_across_antimeridian():
    (out,) = run(Averager(10), [(0, fix(lon=179.9)), (1, fix(lon=-179.9)), (10, fix())])
    assert abs(out["longitude"]) == pytest.approx(180.0)


def test_altitude_quality_satellites():
    samples = [
        (0, fix(altitude_m=10.0, quality=2, satellites=9)),
        (1, fix(altitude_m=12.0, quality=1, satellites=7)),
        (2, fix(quality=None, satellites=None)),
        (10, fix()),
    ]
    (out,) = run(Averager(10), samples)
    assert out["altitude_m"] == pytest.approx(11.0)
    assert out["quality"] == 1 and out["satellites"] == 7  # worst seen
    assert out["talker"] == "GP"


def test_gap_skips_empty_windows():
    out = run(Averager(60), [(5, fix()), (200, fix()), (260, fix())])
    assert [o["fix_time"][11:19] for o in out] == ["12:00:30", "12:03:30"]


def test_invalid_window():
    with pytest.raises(ValueError):
        Averager(0)


def test_epoch_helpers_match_datetime():
    d = datetime(1999, 12, 31, 23, 59, 58, tzinfo=timezone.utc)
    for _ in range(4000):  # ~11 years in 1-day + 1-s steps, across 2000 and leap days
        epoch = int(d.timestamp())
        assert (
            epoch_from_parts(d.year, d.month, d.day, d.hour, d.minute, d.second)
            == epoch
        )
        assert iso_utc_from_epoch(epoch) == d.isoformat()
        d += timedelta(days=1, seconds=1)


def test_averaging_module_is_circuitpython_safe():
    """The Pico runs this exact file: only `math` may be imported."""
    src = Path(__file__).resolve().parents[1] / "sharp_tracker" / "averaging.py"
    imported = set()
    for node in ast.walk(ast.parse(src.read_text())):
        if isinstance(node, ast.Import):
            imported |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    assert imported == {"math"}
