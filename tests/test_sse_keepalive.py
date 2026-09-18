"""
Offline self-check for the SSE stream in server.run_events()/_generate().

The stream reads the persisted Run progress snapshot, so state is driven
through progress.publish()/finish() against a seeded run row instead of a
real pipeline. _SSE_HEARTBEAT_SECONDS and _SSE_POLL_SECONDS are patched small
so nothing waits the real intervals.

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

from auth_helper import login  # sets the sign-in env the startup hook requires

import progress
import server

db.init()  # server's startup hook only fires inside TestClient's `with` block
client = login(TestClient(server.app))


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


def _fast():
    return patch.multiple(server, _SSE_HEARTBEAT_SECONDS=0.2, _SSE_POLL_SECONDS=0.05)


def _data_frames(buf):
    return [json.loads(line[len("data: "):]) for line in buf.splitlines()
            if line.startswith("data: ")]


def _read_stream(run_id, finish_after=0.8, timeout=5.0):
    """Read one SSE stream to its end, bounded in wall-clock time.

    A non-terminal run streams forever, so a timer finishes the run after
    `finish_after` seconds; the stream then sends the terminal snapshot and
    closes. The read runs in a worker thread with a hard join timeout, so a
    stream that fails to end is a test failure, never a hang. Returns the
    decoded text."""
    holder = {"buf": "", "exc": None}
    timer = threading.Timer(finish_after, progress.finish, args=(run_id,)) if finish_after else None
    if timer:
        timer.start()

    def _worker():
        try:
            with client.stream("GET", f"/api/runs/{run_id}/events") as resp:
                assert resp.status_code == 200, resp.status_code
                assert resp.headers["content-type"].startswith("text/event-stream")
                for chunk in resp.iter_raw():
                    holder["buf"] += chunk.decode("utf-8")
        except Exception as exc:  # noqa: BLE001 - surfaced to the main thread below
            holder["exc"] = exc

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    thread.join(timeout=timeout)
    if timer:
        timer.cancel()

    assert not thread.is_alive(), "SSE stream read did not terminate within the bounded timeout"
    if holder["exc"] is not None:
        raise holder["exc"]
    return holder["buf"]


def test_idle_run_sends_its_snapshot_then_heartbeats():
    with _fast():
        run_id = _seed_run(_new_session())
        progress.publish(run_id, "collect", "Collecting", 5, total=3)
        buf = _read_stream(run_id)
    frames = _data_frames(buf)
    assert frames[0] == {"stage": "collect", "pct": 5, "message": "Collecting",
                         "counts": {"total": 3}, "error": None, "queuePosition": None}, frames[0]
    assert ": heartbeat\n\n" in buf, buf
    assert frames[-1]["stage"] == "complete", frames
    # Unchanged snapshots are not resent: only the first and the terminal one.
    assert len(frames) == 2, frames
    print("  ok  an idle run sends its snapshot once, then heartbeats until it ends")


def test_heartbeat_is_a_comment_frame_not_a_data_record():
    with _fast():
        run_id = _seed_run(_new_session())
        buf = _read_stream(run_id)
    frame_start = buf.index(": heartbeat\n\n")
    frame = buf[frame_start:frame_start + len(": heartbeat\n\n")]
    assert not any(line.startswith("data:") for line in frame.splitlines()), frame
    print("  ok  the heartbeat frame is a comment (': ' prefix), never a data: record")


def test_a_terminal_run_sends_one_snapshot_and_ends():
    with _fast():
        run_id = _seed_run(_new_session())
        progress.finish(run_id, error="RuntimeError: boom")
        buf = _read_stream(run_id, finish_after=None)
    frames = _data_frames(buf)
    assert len(frames) == 1 and frames[0]["stage"] == "error", frames
    assert frames[0]["error"] == "RuntimeError: boom", frames
    assert ": heartbeat" not in buf, buf
    print("  ok  a terminal run sends its terminal snapshot once and the stream ends")


def test_progress_reaches_every_open_stream():
    """Two tabs on one run both see every change, which a consume-once queue
    could not do."""
    with _fast():
        run_id = _seed_run(_new_session())
        bufs = [None, None]

        def _reader(i):
            bufs[i] = _read_stream(run_id, finish_after=None, timeout=6.0)

        readers = [threading.Thread(target=_reader, args=(i,)) for i in range(2)]
        for r in readers:
            r.start()
        threading.Timer(0.4, progress.publish,
                        args=(run_id, "classify", "Batch 1", 55), kwargs={"labelled": 2}).start()
        threading.Timer(1.0, progress.finish, args=(run_id,)).start()
        for r in readers:
            r.join(8.0)
    for buf in bufs:
        stages = [f["stage"] for f in _data_frames(buf)]
        assert "classify" in stages and stages[-1] == "complete", stages
    print("  ok  two concurrent streams on one run both receive every snapshot and the end")


def test_intervals_are_module_level_with_the_documented_defaults():
    assert server._SSE_HEARTBEAT_SECONDS == 15.0, server._SSE_HEARTBEAT_SECONDS
    assert server._SSE_POLL_SECONDS == 1.0, server._SSE_POLL_SECONDS
    print("  ok  the heartbeat and poll intervals are module level with their defaults")


if __name__ == "__main__":
    tests = [
        test_idle_run_sends_its_snapshot_then_heartbeats,
        test_heartbeat_is_a_comment_frame_not_a_data_record,
        test_a_terminal_run_sends_one_snapshot_and_ends,
        test_progress_reaches_every_open_stream,
        test_intervals_are_module_level_with_the_documented_defaults,
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
