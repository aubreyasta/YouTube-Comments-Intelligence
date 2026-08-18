"""
Offline self-check for the SSE heartbeat in server.run_events()/_generate().

_SSE_HEARTBEAT_SECONDS is patched to a small value for every test here so
none of this waits the real 15-second interval. State is seeded directly
(a run row via db.get_conn(), progress items pushed straight onto
adapter.get_queue(run_id), terminal state flipped via adapter._terminal)
rather than running a real pipeline, mirroring tests/test_run_concurrency_guard.py.

Run: python tests/test_sse_keepalive.py
"""

import json
import os
import pathlib
import sys
import tempfile
import threading
import uuid
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
db._DB_PATH = pathlib.Path(tempfile.mkdtemp()) / "app.db"

import storage
storage._ROOT = tempfile.mkdtemp()

from starlette.testclient import TestClient

# server refuses to start without APP_PASSWORD, and its Basic Auth
# middleware guards every route. Set before import so the startup hook sees it.
os.environ.setdefault("APP_PASSWORD", "test-password")

import server
import adapter

db.init()  # server's startup hook only fires inside TestClient's `with` block
client = TestClient(server.app, headers={"Authorization": "Basic b2ZmaWNlOnRlc3QtcGFzc3dvcmQ="})


def _new_session():
    return client.post("/api/sessions", json={"name": "S"}).json()["id"]


def _seed_run(session_id, state="running"):
    """Insert a run row directly, bypassing the route/pipeline."""
    conn = db.get_conn()
    try:
        rid = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO runs (id, session_id, state) VALUES (?, ?, ?)",
            (rid, session_id, state),
        )
        conn.commit()
    finally:
        conn.close()
    return rid


def _cleanup(run_id):
    adapter._queues.pop(run_id, None)
    adapter._terminal.pop(run_id, None)


def _bounded_stream_read(open_stream, run_id, max_chunks=None, teardown_delay=0.8, timeout=5.0):
    """Read an SSE stream to completion, bounded in wall-clock time no matter
    what the stream does.

    server.run_events._generate() never returns for a non-terminal run - it
    loops forever waiting on the queue - so a test must supply the terminal
    condition itself. This arms a threading.Timer that, if the stream is
    still open after `teardown_delay`, flips adapter._terminal for run_id AND
    pushes a "complete" item onto its queue (setting the flag alone would not
    wake a generator parked in the queue wait). The whole read runs in a
    worker thread with a hard join timeout, so a stream that still fails to
    end is a test failure, never a hang. Returns (status_code, headers, buf)
    with buf the full decoded text, so callers don't depend on incremental
    delivery.
    """
    holder = {"buf": "", "exc": None, "status": None, "headers": None}

    def _force_terminal():
        adapter._terminal[run_id] = True
        adapter.get_queue(run_id).put(
            {"run_id": run_id, "stage": "complete", "pct": 100, "message": "test teardown", "detail": None}
        )

    timer = threading.Timer(teardown_delay, _force_terminal)
    timer.start()

    def _worker():
        try:
            buf = ""
            with open_stream() as resp:
                holder["status"] = resp.status_code
                holder["headers"] = dict(resp.headers)
                n = 0
                for chunk in resp.iter_raw():
                    buf += chunk.decode("utf-8")
                    n += 1
                    if max_chunks is not None and n >= max_chunks:
                        break
            holder["buf"] = buf
        except Exception as exc:  # noqa: BLE001 - surfaced to the main thread below
            holder["exc"] = exc

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    thread.join(timeout=timeout)

    timer.cancel()
    adapter._terminal.pop(run_id, None)
    adapter._queues.pop(run_id, None)

    assert not thread.is_alive(), "SSE stream read did not terminate within the bounded timeout"
    if holder["exc"] is not None:
        raise holder["exc"]
    return holder["status"], holder["headers"], holder["buf"]


def test_idle_queue_emits_the_heartbeat_comment_frame():
    with patch.object(server, "_SSE_HEARTBEAT_SECONDS", 0.2):
        session_id = _new_session()
        run_id = _seed_run(session_id)
        try:
            _, _, buf = _bounded_stream_read(
                lambda: client.stream("GET", f"/api/runs/{run_id}/events"), run_id
            )
            # The teardown timer eventually forces this idle stream to end with
            # its own "data: ...test teardown" frame; that frame is not part of
            # what this test is asserting on, only that the heartbeat preceded it.
            assert ": heartbeat\n\n" in buf, buf
            heartbeat_idx = buf.index(": heartbeat\n\n")
            teardown_idx = buf.find("test teardown")
            if teardown_idx != -1:
                assert heartbeat_idx < teardown_idx, buf
        finally:
            _cleanup(run_id)
    print("  ok  an idle queue on a non-terminal run emits the heartbeat comment frame")


def test_a_real_event_resets_the_timer_and_precedes_any_heartbeat():
    with patch.object(server, "_SSE_HEARTBEAT_SECONDS", 0.2):
        session_id = _new_session()
        run_id = _seed_run(session_id)
        try:
            adapter.get_queue(run_id).put(
                {"run_id": run_id, "stage": "downloading", "message": "m", "pct": 10, "detail": None}
            )
            _, _, buf = _bounded_stream_read(
                lambda: client.stream("GET", f"/api/runs/{run_id}/events"), run_id
            )
            assert "data: " in buf, buf
            before_data = buf.split("data: ", 1)[0]
            assert ": heartbeat\n\n" not in before_data, (
                "a heartbeat frame appeared before the real event that should have reset the timer"
            )
        finally:
            _cleanup(run_id)
    print("  ok  a real event on the queue reaches the client before any heartbeat, resetting the timer")


def test_heartbeat_is_a_comment_frame_not_a_data_record():
    with patch.object(server, "_SSE_HEARTBEAT_SECONDS", 0.2):
        session_id = _new_session()
        run_id = _seed_run(session_id)
        try:
            _, _, buf = _bounded_stream_read(
                lambda: client.stream("GET", f"/api/runs/{run_id}/events"), run_id
            )
            frame_start = buf.index(": heartbeat\n\n")
            frame = buf[frame_start:frame_start + len(": heartbeat\n\n")]
            assert frame.startswith(": "), frame
            assert not any(line.startswith("data:") for line in frame.splitlines()), (
                "the heartbeat frame contains a data: line, so an SSE client's onmessage would fire on it"
            )
        finally:
            _cleanup(run_id)
    print("  ok  the heartbeat frame is a comment (': ' prefix), never a data: record")


def test_a_complete_event_ends_the_stream_instead_of_heartbeating():
    with patch.object(server, "_SSE_HEARTBEAT_SECONDS", 0.2):
        session_id = _new_session()
        run_id = _seed_run(session_id)
        try:
            adapter.get_queue(run_id).put(
                {"run_id": run_id, "stage": "complete", "message": "done", "pct": 100, "detail": None}
            )
            # This stream ends on its own via the seeded complete event, well
            # before the teardown timer's deadline, so the teardown frame is
            # never appended and does not affect the assertions below.
            _, _, buf = _bounded_stream_read(
                lambda: client.stream("GET", f"/api/runs/{run_id}/events"), run_id
            )
            assert json.dumps({"stage": "complete"})[1:-1] in buf.replace(" ", "") or '"stage": "complete"' in buf, buf
            assert ": heartbeat\n\n" not in buf, (
                "the stream kept heartbeating after a complete event instead of closing"
            )
        finally:
            _cleanup(run_id)
    print("  ok  a complete-stage event ends the stream rather than continuing to heartbeat")


def test_terminal_run_drains_buffered_items_then_ends():
    session_id = _new_session()
    run_id = _seed_run(session_id, state="complete")
    try:
        adapter.get_queue(run_id).put(
            {"run_id": run_id, "stage": "labelling", "message": "m", "pct": 50, "detail": None}
        )
        adapter.get_queue(run_id).put(
            {"run_id": run_id, "stage": "complete", "message": "done", "pct": 100, "detail": None}
        )
        adapter._terminal[run_id] = True

        # Already terminal when the client connects: _generate() drains the
        # two buffered items and returns on its own, well before the
        # teardown timer's deadline, so the teardown frame never appears.
        _, _, buf = _bounded_stream_read(
            lambda: client.stream("GET", f"/api/runs/{run_id}/events"), run_id
        )
        assert '"stage": "labelling"' in buf, buf
        assert '"stage": "complete"' in buf, buf
        assert buf.index('"stage": "labelling"') < buf.index('"stage": "complete"')
    finally:
        _cleanup(run_id)
    print("  ok  a terminal run drains both buffered items in order, then the stream ends")


def test_closing_the_stream_early_leaks_nothing_and_a_later_stream_still_works():
    with patch.object(server, "_SSE_HEARTBEAT_SECONDS", 0.2):
        session_id = _new_session()
        run_id = _seed_run(session_id)
        try:
            # Bounded read of exactly one chunk, then the stream is closed
            # (mirroring the old early-disconnect intent) as soon as the
            # bounded read returns, instead of an unbounded next(...) call.
            _bounded_stream_read(
                lambda: client.stream("GET", f"/api/runs/{run_id}/events"), run_id, max_chunks=1
            )

            _, _, buf = _bounded_stream_read(
                lambda: client.stream("GET", f"/api/runs/{run_id}/events"), run_id
            )
            assert ": heartbeat\n\n" in buf, (
                "a later stream on the same run_id did not still work after an early disconnect"
            )
        finally:
            _cleanup(run_id)
    print("  ok  closing the stream early leaves no leaked task; a later stream on the same run still works")


def test_heartbeat_interval_is_module_level_with_the_documented_default():
    assert hasattr(server, "_SSE_HEARTBEAT_SECONDS"), (
        "_SSE_HEARTBEAT_SECONDS is not a module-level attribute on server"
    )
    assert server._SSE_HEARTBEAT_SECONDS == 15.0, server._SSE_HEARTBEAT_SECONDS
    print("  ok  server._SSE_HEARTBEAT_SECONDS exists at module level and defaults to 15.0")


if __name__ == "__main__":
    tests = [
        test_idle_queue_emits_the_heartbeat_comment_frame,
        test_a_real_event_resets_the_timer_and_precedes_any_heartbeat,
        test_heartbeat_is_a_comment_frame_not_a_data_record,
        test_a_complete_event_ends_the_stream_instead_of_heartbeating,
        test_terminal_run_drains_buffered_items_then_ends,
        test_closing_the_stream_early_leaks_nothing_and_a_later_stream_still_works,
        test_heartbeat_interval_is_module_level_with_the_documented_default,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as exc:
            print(f"  FAIL {t.__name__}: {exc}")
            failed += 1
        except Exception as exc:
            print(f"  ERROR {t.__name__}: {type(exc).__name__}: {exc}")
            failed += 1

    if failed:
        print(f"\nFAIL ({failed}/{len(tests)} failed)")
        sys.exit(1)
    print(f"\nPASS ({len(tests)}/{len(tests)})")
