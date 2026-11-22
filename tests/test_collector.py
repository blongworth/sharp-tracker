from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import nmea_gen
import pytest
import requests
from fastapi.testclient import TestClient

from sharp_tracker import collector, live
from sharp_tracker.db import get_forward_cursor, latest_positions, unsent_positions

T0 = datetime(2026, 11, 15, 12, 35, 19, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def fresh_collector(monkeypatch):
    monkeypatch.setattr(collector, "_averager", None)
    monkeypatch.setattr(collector, "LATEST_NAV", {})
    # Most tests want one stored row per fix; averaging tests turn it on.
    monkeypatch.setattr(collector, "AVERAGE_SECONDS", 0)
    monkeypatch.delenv("TRACKER_INGEST_TOKEN", raising=False)


def feed(lines: list[str], seconds: list[float]) -> list[dict | None]:
    return [
        collector.process_nmea_line(line, now=T0 + timedelta(seconds=s))
        for line, s in zip(lines, seconds)
    ]


def stored(engine) -> list[dict]:
    return list(reversed(latest_positions(1000)))


def test_without_averaging_every_valid_fix_is_logged(engine, sample_lines):
    seconds = [0, 0, 0, 1, 7, 7, 11, 11, 11, 16, 16, 16]
    feed(sample_lines, seconds)
    rows = stored(engine)
    # Invalid fixes at 11 s, empty GGA at 16 s and junk lines never count.
    assert [r["sentence_type"] for r in rows] == ["GGA", "RMC", "GGA", "GGA", "RMC"]
    # The 1 s GGA has no COG of its own; it carries the RMC/VTG nav seen earlier.
    assert rows[2]["course_deg"] == 46.0
    assert rows[2]["speed_knots"] == 8.6


def test_averaging_stores_one_row_per_utc_window(engine, monkeypatch):
    monkeypatch.setattr(collector, "AVERAGE_SECONDS", 60)
    start = datetime(2026, 11, 15, 12, 0, 20, tzinfo=timezone.utc)
    for s in range(0, 160):  # 12:00:20 .. 12:02:59 at 1 Hz, GGA + RMC each second
        lines = nmea_gen.sentences(s, start=start).decode().splitlines()
        for line in lines:
            collector.process_nmea_line(
                line, now=start + timedelta(seconds=s, milliseconds=100)
            )
    rows = stored(engine)
    # 12:00 (partial, from :20) and 12:01 closed; 12:02 still open.
    assert [r["fix_time"].isoformat() for r in rows] == [
        "2026-11-15T12:00:30+00:00",
        "2026-11-15T12:01:30+00:00",
    ]
    assert rows[0]["raw"].startswith("AVG 60s n=40 ")
    assert rows[1]["raw"].startswith("AVG 60s n=60 ")
    assert rows[1]["sentence_type"] == "AVG"
    assert rows[1]["course_deg"] == pytest.approx(45.0)
    assert rows[1]["speed_knots"] == pytest.approx(8.5)
    # received_at is when the window closed (first fix of the next window).
    assert rows[1]["received_at"] == datetime(
        2026, 11, 15, 12, 2, 0, 100000, tzinfo=timezone.utc
    )


def test_close_due_window_stores_partial_window(engine, monkeypatch):
    monkeypatch.setattr(collector, "AVERAGE_SECONDS", 60)
    start = datetime(2026, 11, 15, 12, 0, 0, tzinfo=timezone.utc)
    for s in range(20):  # GPS goes quiet after 12:00:19
        for line in nmea_gen.sentences(s, start=start).decode().splitlines():
            collector.process_nmea_line(line, now=start + timedelta(seconds=s))
    assert collector.close_due_window(now=start + timedelta(seconds=59)) is None
    row = collector.close_due_window(
        now=start + timedelta(seconds=60, milliseconds=500)
    )
    assert row["raw"].startswith("AVG 60s n=20 ")
    (stored_row,) = stored(engine)
    assert stored_row["received_at"] == start + timedelta(seconds=60, milliseconds=500)


def test_close_due_window_without_averaging_is_noop(engine):
    assert collector.close_due_window() is None


def test_tcp_loop_closes_window_while_fix_is_lost(engine, monkeypatch):
    """Real socket: one good fix, then a receiver with no fix that keeps talking.

    add() never sees another valid fix, so only close_due_window() can store
    the window, and it must do so before the connection ends.
    """
    import socket
    import time

    monkeypatch.setattr(collector, "AVERAGE_SECONDS", 1)
    monkeypatch.setattr(collector, "SOURCE_POLL_SECONDS", 0.1)
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    rows_before_close = []

    def source():
        conn, _ = server.accept()
        with conn:
            conn.sendall(nmea_gen.sentences(0))  # one valid fix
            for s in range(1, 13):  # ~2.4 s of no-fix chatter
                time.sleep(0.2)
                conn.sendall(nmea_gen.sentences(s, valid=False))
            rows_before_close.extend(latest_positions(10))

    thread = threading.Thread(target=source, daemon=True)
    thread.start()
    with pytest.raises(ConnectionError, match="closed"):
        collector._collect_tcp_once("127.0.0.1", server.getsockname()[1])
    thread.join(5)
    server.close()
    assert [r["sentence_type"] for r in rows_before_close] == ["AVG"]
    assert "n=1 " in rows_before_close[0]["raw"]


def test_averaged_rows_forward_through_real_api(engine, monkeypatch):
    monkeypatch.setattr(collector, "AVERAGE_SECONDS", 10)
    start = datetime(2026, 11, 15, 12, 0, 0, tzinfo=timezone.utc)
    for s in range(35):
        for line in nmea_gen.sentences(s, start=start).decode().splitlines():
            collector.process_nmea_line(line, now=start + timedelta(seconds=s))
    with TestClient(live.app) as client:
        assert collector._forward_once(client, "http://testserver") == 3
    assert len(stored(engine)) == 3  # AVG rows validate and resend idempotently


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


def run_loop(monkeypatch, outcomes, source="tcp"):
    """Run collect_forever over canned per-attempt outcomes; stops when they run out."""
    attempts = iter(outcomes)

    def attempt(*args):
        outcome = next(attempts, KeyboardInterrupt())
        if isinstance(outcome, BaseException):
            raise outcome
        outcome()  # e.g. a successful connection that later drops

    monkeypatch.setenv("NMEA_SOURCE", source)
    monkeypatch.setenv("NMEA_SERIAL_PORT", "/dev/fake")
    monkeypatch.delenv("TRACKER_API_URL", raising=False)
    monkeypatch.setattr(collector, "_collect_tcp_once", attempt)
    monkeypatch.setattr(collector, "_collect_serial_once", attempt)
    monkeypatch.setattr(collector.time, "sleep", lambda s: None)
    with pytest.raises(KeyboardInterrupt):
        collector.collect_forever(host="127.0.0.1", port=4000)


def warnings_logged(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]


def test_unreachable_source_warns_once_then_periodically(monkeypatch, caplog):
    caplog.set_level("DEBUG", logger="sharp_tracker.collector")
    run_loop(monkeypatch, [ConnectionRefusedError("refused")] * 130)
    msgs = warnings_logged(caplog)
    # First failure, then every SOURCE_WARNING_EVERY (60): attempts 1, 60, 120.
    assert len(msgs) == 3
    assert "(1 failed attempts)" in msgs[0] and "(120 failed attempts)" in msgs[2]
    assert not any(r.exc_info for r in caplog.records)  # no tracebacks
    assert sum("Starting TCP" in r.getMessage() for r in caplog.records) == 1


def test_reconnect_resets_failure_count(monkeypatch, caplog):
    caplog.set_level("INFO", logger="sharp_tracker.collector")

    def connect():
        collector._source_connected("TCP NMEA source test")

    lost = ConnectionError("source closed the connection")
    run_loop(
        monkeypatch, [ConnectionRefusedError("refused")] * 3 + [connect, lost, lost]
    )
    infos = [r.getMessage() for r in caplog.records if r.levelname == "INFO"]
    assert "Connected to TCP NMEA source test after 3 failed attempts" in infos
    # After the reconnect, the next outage warns again from attempt 1.
    assert warnings_logged(caplog)[-1].endswith("(1 failed attempts)")


def test_unexpected_error_keeps_traceback(monkeypatch, caplog):
    run_loop(monkeypatch, [RuntimeError("bug")])
    assert any(r.exc_info for r in caplog.records if r.levelname == "ERROR")


def test_bad_config_fails_once(monkeypatch):
    monkeypatch.setenv("NMEA_SOURCE", "carrier-pigeon")
    with pytest.raises(ValueError, match="NMEA_SOURCE"):
        collector.collect_forever()
    monkeypatch.setenv("NMEA_SOURCE", "serial")
    monkeypatch.delenv("NMEA_SERIAL_PORT", raising=False)
    with pytest.raises(ValueError, match="NMEA_SERIAL_PORT"):
        collector.collect_forever()


def test_tcp_eof_is_a_disconnect(monkeypatch):
    """A source that accepts then closes must go through the retry delay."""
    import socket

    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    threading.Thread(target=lambda: server.accept()[0].close(), daemon=True).start()
    with pytest.raises(ConnectionError, match="closed"):
        collector._collect_tcp_once("127.0.0.1", port)
    server.close()


def test_window_closes_while_source_is_down(engine, monkeypatch):
    """Source vanishes mid-window: the retry loop must still store it."""
    monkeypatch.setattr(collector, "AVERAGE_SECONDS", 60)
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(seconds=now.timestamp() % 60) - timedelta(seconds=60)
    for line in (
        nmea_gen.sentences(0).decode().splitlines()
    ):  # a fix in the previous minute
        collector.process_nmea_line(line, now=window_start + timedelta(seconds=5))
    run_loop(monkeypatch, [ConnectionRefusedError("refused")])
    (row,) = stored(engine)
    assert row["raw"].startswith("AVG 60s n=1 ")
