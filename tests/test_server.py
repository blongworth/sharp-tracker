from __future__ import annotations

import json
import threading

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
    assert client.get("/health").json()["status"] == "ok"


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

