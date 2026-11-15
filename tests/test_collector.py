from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import requests
from fastapi.testclient import TestClient

from sharp_tracker import collector, live
from sharp_tracker.db import get_forward_cursor, latest_positions, unsent_positions

T0 = datetime(2026, 11, 15, 12, 35, 19, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def fresh_collector(monkeypatch):
    monkeypatch.setattr(collector, "_last_logged", None)
    monkeypatch.setattr(collector, "LATEST_NAV", {})
    monkeypatch.setattr(collector, "LOG_INTERVAL_SECONDS", 5.0)
    monkeypatch.delenv("TRACKER_INGEST_TOKEN", raising=False)


def feed(lines: list[str], seconds: list[float]) -> list[dict | None]:
    return [
        collector.process_nmea_line(line, now=T0 + timedelta(seconds=s))
        for line, s in zip(lines, seconds)
    ]


def stored(engine) -> list[dict]:
    return list(reversed(latest_positions(100)))


def test_sample_feed_throttled_and_logged_locally(engine, sample_lines):
    # Arrival times (s) for each sample line; a real feed sends a burst per fix.
    seconds = [0, 0, 0, 1, 7, 7, 11, 11, 11, 16, 16, 16]
    feed(sample_lines, seconds)
    rows = stored(engine)
    # 0 s GGA kept; same-second RMC and 1 s GGA throttled; 7 s GGA kept.
    # Invalid fixes at 11 s, empty GGA at 16 s and junk lines never count.
    assert [(r["sentence_type"], r["received_at"]) for r in rows] == [
        ("GGA", T0),
        ("GGA", T0 + timedelta(seconds=7)),
    ]
    # The 7 s GGA has no COG of its own; it carries RMC/VTG nav seen earlier.
    assert rows[1]["course_deg"] == 46.0
    assert rows[1]["speed_knots"] == 8.6


def test_interval_zero_keeps_every_valid_fix(engine, sample_lines, monkeypatch):
    monkeypatch.setattr(collector, "LOG_INTERVAL_SECONDS", 0.0)
    feed(sample_lines, [0] * len(sample_lines))
    assert len(stored(engine)) == 5


class FakeSession:
    """requests-style session that records posts and replays canned outcomes."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.posts = []

    def post(self, url, json, headers, timeout):
        self.posts.append({"url": url, "json": json, "headers": headers})
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return type("Resp", (), {"status_code": outcome, "text": ""})()


def log_fixes(n: int) -> None:
    for i in range(n):
        collector.process_nmea_line(
            "$GPGGA,123519.00,4131.5600,N,07040.3800,W,1,08,0.9,10.5,M,-34.0,M,,*60",
            now=T0 + timedelta(seconds=10 * i),
        )


def test_forward_advances_cursor_only_on_success(engine, monkeypatch):
    monkeypatch.setattr(collector, "BATCH_SIZE", 2)
    monkeypatch.setenv("TRACKER_INGEST_TOKEN", "secret")
    log_fixes(3)
    session = FakeSession([requests.ConnectionError("link down"), 500, 201, 201])

    with pytest.raises(requests.ConnectionError):
        collector._forward_once(session, "https://ingest.example/")
    with pytest.raises(collector.ForwardError, match="HTTP 500"):
        collector._forward_once(session, "https://ingest.example/")
    assert get_forward_cursor() == 0  # failures never advance the cursor

    assert collector._forward_once(session, "https://ingest.example/") == 2
    assert collector._forward_once(session, "https://ingest.example/") == 1
    assert (
        collector._forward_once(session, "https://ingest.example/") == 0
    )  # caught up; no post
    assert get_forward_cursor() == 3

    # The two failed attempts and the first success sent the same batch.
    ids = [[p["id"] for p in post["json"]["positions"]] for post in session.posts]
    assert ids == [[1, 2], [1, 2], [1, 2], [3]]
    assert session.posts[0]["url"] == "https://ingest.example/positions/batch"
    assert session.posts[0]["headers"] == {"Authorization": "Bearer secret"}


def test_forward_against_real_ingest_api(engine, monkeypatch):
    """Payload contract with the actual API. The in-process API shares the
    collector's database, so this checks acceptance, not a second copy."""
    monkeypatch.setenv("TRACKER_INGEST_TOKEN", "secret")
    log_fixes(3)
    with TestClient(live.app) as client:
        assert collector._forward_once(client, "http://testserver") == 3
    assert get_forward_cursor() == 3
    assert unsent_positions(get_forward_cursor()) == []
    assert len(stored(engine)) == 3  # resend into the same DB was idempotent
