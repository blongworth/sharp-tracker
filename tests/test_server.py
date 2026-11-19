from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from sharp_tracker import api, live
from sharp_tracker.db import latest_positions

FIX = {
    "received_at": "2026-11-15T12:35:19+00:00",
    "fix_time": "2026-11-15T12:35:19+00:00",
    "sentence_type": "RMC",
    "latitude": 41.526,
    "longitude": -70.673,
    "raw": "$GPRMC,123519",
}


@pytest.fixture
def client(engine, monkeypatch):
    monkeypatch.delenv("TRACKER_INGEST_TOKEN", raising=False)
    monkeypatch.delenv("COLLECT", raising=False)
    with TestClient(live.app) as c:
        yield c


def test_web_app_serves_map_and_health(client):
    assert client.get("/").status_code == 200
    health = client.get("/health").json()
    assert health["status"] == "ok"
    assert health["last_fix_received_at"] is None
    assert health["last_fix_age_seconds"] is None


def test_health_reports_last_fix(client):
    client.post("/positions/batch", json={"positions": [FIX]})
    health = client.get("/health").json()
    assert health["last_fix_received_at"] == "2026-11-15T12:35:19+00:00"
    assert health["last_fix_age_seconds"] >= 0


def test_api_positions_window_and_format(client):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    recent = {
        **FIX,
        "received_at": (now - timedelta(minutes=10)).isoformat(),
        "fix_time": None,
        "raw": "$recent",
    }
    old = {
        **FIX,
        "received_at": (now - timedelta(hours=3)).isoformat(),
        "fix_time": None,
        "raw": "$old",
    }
    client.post("/positions/batch", json={"positions": [old, recent]})

    assert client.get("/api/positions?window_hours=1").json() == [
        {
            "t": recent["received_at"],
            "lat": 41.526,
            "lon": -70.673,
            "sog": None,
            "cog": None,
        }
    ]
    assert len(client.get("/api/positions?window_hours=6").json()) == 2


def test_waypoints_empty_when_remote_unreachable(client, monkeypatch):
    import requests

    def boom(*args, **kwargs):
        raise requests.ConnectionError("offline")

    live.load_waypoints.cache_clear()
    monkeypatch.setattr(live.requests, "get", boom)
    assert client.get("/api/waypoints").json() == []
    live.load_waypoints.cache_clear()


def test_waypoints_parsed_from_csv(client, monkeypatch):
    csv_text = (
        "stationfullname,station,startDate,endDate,decimalLatitude,decimalLongitude,depth_m\n"
        "LTER2 ,L2  ,2018-01-01,current,41.03,-70.88,40.0\n"
        "LTER1,L1,2018-01-01,current,41.1967,-70.8833,NaN\n"
        "LTER1,L1old,2010-01-01,2017-12-31,41.0,-70.0,10\n"
        "LTER9,L9,2018-01-01,current,NaN,-70.0,10\n"
    )

    class Resp:
        text = csv_text

        def raise_for_status(self):
            pass

    live.load_waypoints.cache_clear()
    monkeypatch.setattr(live.requests, "get", lambda *a, **k: Resp())
    assert client.get("/api/waypoints").json() == [
        {
            "station": "L1",
            "name": "LTER1",
            "lat": 41.1967,
            "lon": -70.8833,
            "depth_m": None,
        },
        {
            "station": "L2",
            "name": "LTER2",
            "lat": 41.03,
            "lon": -70.88,
            "depth_m": 40.0,
        },
    ]
    live.load_waypoints.cache_clear()


def test_batch_ingest_on_web_app(client):
    r = client.post("/positions/batch", json={"positions": [FIX]})
    assert r.status_code == 201
    assert r.json() == {"status": "created", "count": 1}
    assert latest_positions(1)[0]["latitude"] == pytest.approx(41.526)


def test_batch_resend_stored_once(client):
    for _ in range(2):
        assert (
            client.post("/positions/batch", json={"positions": [FIX]}).status_code
            == 201
        )
    assert len(latest_positions(10)) == 1


def test_token_required_when_set(client, monkeypatch):
    monkeypatch.setenv("TRACKER_INGEST_TOKEN", "secret")
    body = {"positions": [FIX]}
    assert client.post("/positions/batch", json=body).status_code == 401
    assert (
        client.post(
            "/positions/batch", json=body, headers={"Authorization": "Bearer nope"}
        ).status_code
        == 401
    )
    ok = client.post(
        "/positions/batch", json=body, headers={"Authorization": "Bearer secret"}
    )
    assert ok.status_code == 201


def test_single_position_endpoint_removed(client):
    assert client.post("/positions", json=FIX).status_code in (404, 405)


def test_out_of_range_latitude_rejected(client):
    bad = {**FIX, "latitude": 91}
    assert client.post("/positions/batch", json={"positions": [bad]}).status_code == 422


def test_ingest_batch_accepts_dict_and_raw_json(engine):
    """The transport-independent entry point another transport (MQTT) would call."""
    assert api.ingest_batch({"positions": [FIX]}) == 1
    assert (
        api.ingest_batch(json.dumps({"positions": [FIX]}).encode()) == 1
    )  # redelivery
    assert len(latest_positions(10)) == 1
    with pytest.raises(ValidationError):
        api.ingest_batch(b'{"positions": [{"latitude": 1}]}')


def test_collect_starts_collector_thread(engine, monkeypatch):
    started = threading.Event()
    monkeypatch.setattr("sharp_tracker.collector.collect_forever", started.set)
    monkeypatch.setenv("COLLECT", "1")
    with TestClient(live.app):
        assert started.wait(2)


def test_no_collector_without_collect(engine, monkeypatch):
    started = threading.Event()
    monkeypatch.setattr("sharp_tracker.collector.collect_forever", started.set)
    monkeypatch.delenv("COLLECT", raising=False)
    with TestClient(live.app):
        assert not started.wait(0.2)


# --- ETA ----------------------------------------------------------------------

ETA_STATIONS = [
    {"station": "L1", "name": "LTER1", "lat": 41.2, "lon": -70.88, "depth_m": 30.0},  # behind
    {"station": "L5", "name": "LTER5", "lat": 40.9, "lon": -70.88, "depth_m": 60.0},  # 6 nm ahead
    {"station": "L9", "name": "LTER9", "lat": 40.5, "lon": -70.88, "depth_m": 90.0},  # 30 nm ahead
]
ETA_KEYS = {
    "target", "reason", "distance_nm", "bearing_deg", "ttg_s", "eta", "eta_epoch",
    "speed_kn", "speed_source", "fix_time", "fix_epoch", "fix_age_s", "stale",
}


@pytest.fixture
def stations(monkeypatch):
    monkeypatch.setattr(live, "load_waypoints", lambda: ETA_STATIONS)


def steaming_south(client, minutes=10, sog=10.0, cog=180.0):
    """Fixes every minute for `minutes`, the newest 30 s ago at 41.0 N, 70.88 W."""
    now = datetime.now(timezone.utc).replace(microsecond=0)
    fixes = []
    for i in range(minutes):
        t = (now - timedelta(seconds=30 + 60 * i)).isoformat()
        fixes.append({
            **FIX, "received_at": t, "fix_time": t, "latitude": 41.0 + i * sog / 3600,
            "longitude": -70.88, "speed_knots": sog, "course_deg": cog, "raw": f"$eta{i}",
        })
    assert client.post("/positions/batch", json={"positions": fixes}).status_code == 201
    return now - timedelta(seconds=30)


def test_eta_to_station_from_mean_sog(client, stations):
    fix_time = steaming_south(client)
    r = client.get("/api/eta?station=L5").json()
    assert set(r) == ETA_KEYS
    assert r["target"] == {"mode": "station", "station": "L5", "name": "LTER5", "lat": 40.9, "lon": -70.88}
    assert r["distance_nm"] == pytest.approx(6.0, abs=0.01)
    assert r["bearing_deg"] == 180.0
    assert (r["speed_kn"], r["speed_source"]) == (10.0, "sog_30min")
    assert r["ttg_s"] == pytest.approx(2160, abs=5)
    assert r["fix_epoch"] == int(fix_time.timestamp())
    assert r["eta_epoch"] == r["fix_epoch"] + r["ttg_s"]
    assert r["eta"] == datetime.fromtimestamp(r["eta_epoch"], timezone.utc).isoformat()
    assert r["stale"] is False and 25 <= r["fix_age_s"] <= 60


def test_eta_planned_speed_overrides_sog(client, stations, monkeypatch):
    steaming_south(client)
    r = client.get("/api/eta?station=L5&speed_kn=12").json()
    assert (r["speed_kn"], r["speed_source"]) == (12.0, "planned")
    assert r["ttg_s"] == pytest.approx(1800, abs=5)
    monkeypatch.setattr(live, "ETA_PLANNED_SPEED_KN", "6")
    assert client.get("/api/eta?station=L5").json()["ttg_s"] == pytest.approx(3600, abs=5)


def test_eta_auto_predicts_nearest_station_ahead(client, stations):
    steaming_south(client)
    r = client.get("/api/eta").json()
    assert r["target"]["mode"] == "auto"
    assert r["target"]["station"] == "L5"  # not L1 (behind) or L9 (farther)


def test_eta_auto_nothing_ahead(client, stations):
    steaming_south(client, cog=90.0)
    r = client.get("/api/eta").json()
    assert set(r) == ETA_KEYS
    assert (r["target"], r["reason"]) == (None, "no station ahead")
    assert r["ttg_s"] is None and r["fix_time"] is not None


def test_eta_default_target_from_env(client, stations, monkeypatch):
    steaming_south(client)
    monkeypatch.setattr(live, "ETA_DEFAULT_TARGET", "L9")
    assert client.get("/api/eta").json()["target"]["station"] == "L9"


def test_eta_to_point(client, monkeypatch):
    steaming_south(client)
    monkeypatch.setattr(live, "load_waypoints", lambda: [])  # stations not needed
    r = client.get("/api/eta?lat=41.1&lon=-70.88&name=Buoy").json()
    assert r["target"] == {"mode": "point", "station": None, "name": "Buoy", "lat": 41.1, "lon": -70.88}
    assert r["bearing_deg"] == pytest.approx(0, abs=0.1)
    assert client.get("/api/eta?lat=41.1").status_code == 422


def test_eta_errors(client, stations, monkeypatch):
    steaming_south(client)
    assert client.get("/api/eta?station=NOPE").status_code == 404
    monkeypatch.setattr(live, "load_waypoints", lambda: [])
    assert client.get("/api/eta?station=L5").status_code == 503
    assert client.get("/api/eta/stations").status_code == 503


def test_eta_without_fixes(client, stations):
    r = client.get("/api/eta?station=L5").json()
    assert set(r) == ETA_KEYS
    assert r["target"]["station"] == "L5"
    assert r["distance_nm"] is None and r["ttg_s"] is None and r["fix_time"] is None
    assert r["stale"] is False
    auto = client.get("/api/eta").json()
    assert (auto["target"], auto["reason"]) == (None, "no position yet")


def test_eta_stations_nearest_first(client, stations):
    steaming_south(client)
    rows = client.get("/api/eta/stations").json()["stations"]
    assert [r["station"] for r in rows] == ["L5", "L1", "L9"]
    assert set(rows[0]) == {"station", "name", "distance_nm", "ttg_s", "eta", "eta_epoch"}
    assert rows[0]["ttg_s"] == pytest.approx(2160, abs=5)


def test_eta_page_and_script_served(client):
    page = client.get("/eta")
    assert page.status_code == 200 and "/eta.js" in page.text
    script = client.get("/eta.js")
    assert script.status_code == 200
    assert script.headers["content-type"].startswith("text/javascript")
    assert "/eta.js" in client.get("/").text


GARMIN_SAMPLE = (
    Path(__file__).resolve().parents[1] / "garmin" / "sharp_eta" / "resources" / "jsonData" / "eta_sample.json"
)


def _shape(value):
    """Keys and JSON types, recursively; None matches any type (nullable fields)."""
    if isinstance(value, dict):
        return {k: _shape(v) for k, v in value.items()}
    return None if value is None else type(value).__name__


def _same_shape(real, sample):
    if isinstance(sample, dict) and isinstance(real, dict):
        return set(real) == set(sample) and all(_same_shape(real[k], sample[k]) for k in sample)
    return real is None or sample is None or type(real) is type(sample)


def test_garmin_sample_matches_api(client, stations):
    """The watch's unit tests parse eta_sample.json; keep it in step with /api/eta."""
    sample = json.loads(GARMIN_SAMPLE.read_text())
    steaming_south(client)
    real = {
        "station": client.get("/api/eta?station=L5").json(),
        "auto": client.get("/api/eta").json(),
    }
    for name in ("station", "auto"):
        assert _same_shape(real[name], sample[name]), (name, _shape(real[name]), _shape(sample[name]))
    for case in sample.values():
        assert set(case) == ETA_KEYS
