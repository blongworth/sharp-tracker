from __future__ import annotations

import nmea_min
import pytest

from sharp_tracker import nmea

COMPARED = (
    "sentence_type",
    "talker",
    "latitude",
    "longitude",
    "speed_knots",
    "course_deg",
    "quality",
    "satellites",
    "fix_time",
)


def test_valid_and_invalid_fixes(sample_lines):
    positions = [nmea.parse_line(line)[1] for line in sample_lines]
    kept = [p for p in positions if p is not None]
    # 5 valid GGA/RMC; quality-0 GGA, status-V RMC, empty GGA, bad checksum and garbage dropped.
    assert [p["sentence_type"] for p in kept] == ["GGA", "RMC", "GGA", "GGA", "RMC"]
    assert kept[0]["latitude"] == pytest.approx(41.526)
    assert kept[0]["longitude"] == pytest.approx(-70.673)
    assert kept[1]["fix_time"] == "2026-11-15T12:35:19+00:00"


def test_vtg_magnetic_fallback(sample_lines):
    nav, pos = nmea.parse_line(sample_lines[8])
    assert pos is None
    assert nav == {"speed_knots": 3.0, "course_deg": 250.0}


@pytest.mark.parametrize("index", range(12))
def test_pico_parser_matches_desktop(sample_lines, index):
    line = sample_lines[index]
    desk_nav, desk_pos = nmea.parse_line(line)
    pico_nav, pico_pos = nmea_min.parse_line(line)

    assert pico_nav == desk_nav
    assert (pico_pos is None) == (desk_pos is None)
    if desk_pos is not None:
        for key in COMPARED:
            want, got = desk_pos[key], pico_pos[key]
            if isinstance(want, float):
                assert got == pytest.approx(want), key
            elif key == "fix_time" and desk_pos["sentence_type"] == "GGA":
                assert got is None  # GGA has no date; neither parser sets fix_time
            else:
                assert got == want, key
        assert pico_pos["raw"] == desk_pos["raw"]
