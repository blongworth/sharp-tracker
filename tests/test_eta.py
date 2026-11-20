from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from sharp_tracker.eta import (
    angle_between,
    bearing_deg,
    distance_nm,
    mean_sog,
    next_fix_time,
    predict_next,
    time_to_go,
    vmg,
)

# A station grid around 41 N, 70.88 W (the NES-LTER L-line runs due south).
SHIP = (41.0, -70.88)


def station(name, lat, lon):
    return {"station": name, "lat": lat, "lon": lon}


def test_distance_one_minute_of_latitude_is_one_nm():
    assert distance_nm(41.0, -70.88, 41.0 + 1 / 60, -70.88) == pytest.approx(1.0, rel=1e-3)
    assert distance_nm(41.0, -70.88, 41.0, -70.88) == 0


def test_bearing_cardinal_directions():
    assert bearing_deg(*SHIP, 40.9, -70.88) == pytest.approx(180)
    assert bearing_deg(*SHIP, 41.1, -70.88) == pytest.approx(0, abs=1e-9)
    assert bearing_deg(*SHIP, 41.0, -70.8) == pytest.approx(90, abs=0.1)
    assert bearing_deg(*SHIP, 41.0, -70.96) == pytest.approx(270, abs=0.1)


def test_angle_between_wraps():
    assert angle_between(350, 10) == 20
    assert angle_between(10, 350) == 20
    assert angle_between(0, 180) == 180


def test_predict_next_picks_nearest_station_ahead():
    stations = [
        station("behind", 41.2, -70.88),
        station("ahead_far", 40.7, -70.88),
        station("ahead_near", 40.9, -70.88),
        station("abeam", 41.0, -70.7),
    ]
    assert predict_next(*SHIP, 180, stations)["station"] == "ahead_near"
    assert predict_next(*SHIP, 0, stations)["station"] == "behind"
    assert predict_next(*SHIP, None, stations) is None


def test_predict_next_cone_edges():
    # 25 deg off the course is inside a 30 deg cone; 40 deg is not.
    inside = station("inside", 41.0 - 0.1 * 0.906, -70.88 + 0.1 * 0.423 / 0.755)
    outside = station("outside", 41.0 - 0.1 * 0.766, -70.88 + 0.1 * 0.643 / 0.755)
    assert predict_next(*SHIP, 180, [outside]) is None
    assert predict_next(*SHIP, 155, [inside])["station"] == "inside"
    assert predict_next(*SHIP, 180, [inside], cone_deg=10) is None


def test_predict_next_skips_station_we_are_on():
    on_station = station("here", 41.0 - 0.2 / 60, -70.88)  # 0.2 nm south
    next_one = station("next", 40.9, -70.88)
    assert predict_next(*SHIP, 180, [on_station, next_one])["station"] == "next"


def test_predict_next_wraps_around_north():
    north_east = station("ne", 41.1, -70.87)  # bearing ~4 deg
    assert predict_next(*SHIP, 350, [north_east])["station"] == "ne"


def test_mean_sog_ignores_nulls():
    assert mean_sog([{"speed_knots": 8.0}, {"speed_knots": None}, {"speed_knots": 10.0}]) == 9.0
    assert mean_sog([{"speed_knots": None}, {}]) is None
    assert mean_sog([]) is None


def test_time_to_go():
    assert time_to_go(10.0, 10.0) == 3600
    assert time_to_go(12.4, 9.1) == round(12.4 / 9.1 * 3600)
    assert time_to_go(10.0, 0.3) is None  # stopped
    assert time_to_go(10.0, None) is None
    assert time_to_go(None, 10.0) is None


def test_vmg():
    assert vmg(10.0, 180.0, 180.0) == pytest.approx(10.0)
    assert vmg(10.0, 240.0, 180.0) == pytest.approx(5.0)  # 60 deg off
    assert vmg(10.0, 90.0, 180.0) == pytest.approx(0.0, abs=1e-9)  # abeam
    assert vmg(10.0, 0.0, 180.0) == pytest.approx(-10.0)  # moving away
    assert vmg(10.0, 350.0, 10.0) == pytest.approx(10.0 * 0.9396926)  # wraps through north
    assert vmg(None, 180.0, 180.0) is None
    assert vmg(10.0, None, 180.0) is None


def test_next_fix_time():
    t0 = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
    every_10s = [t0 - timedelta(seconds=10 * i) for i in range(4)]
    assert next_fix_time(every_10s) == t0 + timedelta(seconds=10)
    # One outage among the gaps: the median still says 10 s.
    with_outage = [t0, t0 - timedelta(seconds=10), t0 - timedelta(hours=3), t0 - timedelta(hours=3, seconds=10)]
    assert next_fix_time(with_outage) == t0 + timedelta(seconds=10)
    assert next_fix_time([t0]) is None
    assert next_fix_time([]) is None
