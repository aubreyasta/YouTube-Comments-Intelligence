"""
Offline self-check for the start_run() concurrency guard in server.py.

The guard is global, not per-Session: one RTX GPU serves one model at a
time, so only one queued or running run may exist across every Session.
A conflict inside the requesting Session keeps the original same-Session
message; a conflict in any other Session gets the cross-Session message.
A rejected request must touch no existing row and delete no existing
file, which is what the BEGIN IMMEDIATE transaction in start_run()
guarantees.

Every test clears the runs table first, so no test depends on another
test's leftover rows.

adapter.start_run() (the real pipeline thread) is mocked to a no-op so
this only exercises the guard/overwrite logic in the route handler - no
network, no model calls.

Run: python tests/test_run_concurrency_guard.py
"""

import os
import pathlib
import sys
import threading
import tempfile
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


def _seed_run(session_id, state):
    """Insert a run row directly, bypassing the route, so 'running' can be
    tested without a real pipeline thread reaching that state."""
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


def _seed_sentinel_file(run_id):
    """Drop a marker file under this run's storage dir. If a 409 ever let the
    overwrite path run, storage.clear_run(run_id) would delete it."""
    path = pathlib.Path(storage.run_dir(run_id)) / "sentinel.txt"
    path.write_text("sentinel")
    return path


def _run_exists(run_id):
    conn = db.get_conn()
    try:
        return conn.execute(
            "SELECT id FROM runs WHERE id = ?", (run_id,)
        ).fetchone() is not None
    finally:
        conn.close()


def _clear_runs():
    """Every test owns a clean runs table. The guard is global, so one
    leaked queued row would make every later test see a cross-Session
    conflict from a Session it never created."""
    conn = db.get_conn()
    try:
        conn.execute("DELETE FROM runs")
        conn.commit()
    finally:
        conn.close()


def test_queued_run_blocks_second_start_in_the_same_session():
    _clear_runs()
    session_id = _new_session()
    with patch.object(adapter, "start_run", return_value=None):
        first_id = client.post(f"/api/sessions/{session_id}/runs").json()["id"]
        sentinel = _seed_sentinel_file(first_id)

        resp = client.post(f"/api/sessions/{session_id}/runs")
        assert resp.status_code == 409, resp.text
        assert resp.json() == {
            "error": "RUN_IN_PROGRESS",
            "message": "This session already has a run in progress.",
            "field": None,
        }

        assert _run_exists(first_id), "409 path deleted the queued run"
        assert sentinel.exists(), "409 path deleted the queued run's files"
    print("  ok  a queued run blocks a second start in the same Session with the same-Session 409")


def test_running_run_blocks_second_start_in_the_same_session():
    _clear_runs()
    session_id = _new_session()
    run_id = _seed_run(session_id, "running")
    sentinel = _seed_sentinel_file(run_id)

    with patch.object(adapter, "start_run", return_value=None):
        resp = client.post(f"/api/sessions/{session_id}/runs")
        assert resp.status_code == 409, resp.text
        assert resp.json() == {
            "error": "RUN_IN_PROGRESS",
            "message": "This session already has a run in progress.",
            "field": None,
        }

    assert _run_exists(run_id), "409 path deleted the running run"
    assert sentinel.exists(), "409 path deleted the running run's files"
    print("  ok  a running run blocks a second start in the same Session with the same-Session 409")


def test_active_run_in_another_session_blocks_with_the_cross_session_message():
    for state in ("queued", "running"):
        _clear_runs()
        busy_session = _new_session()
        other_session = _new_session()
        busy_run = _seed_run(busy_session, state)
        sentinel = _seed_sentinel_file(busy_run)

        with patch.object(adapter, "start_run", return_value=None):
            resp = client.post(f"/api/sessions/{other_session}/runs")
            assert resp.status_code == 409, resp.text
            assert resp.json() == {
                "error": "RUN_IN_PROGRESS",
                "message": "Another analysis is already running. Wait for it to finish.",
                "field": None,
            }, f"wrong body for a {state} run in another Session"

        assert _run_exists(busy_run), (
            f"the rejected request deleted the other Session's {state} run")
        assert sentinel.exists(), (
            f"the rejected request deleted the other Session's {state} run's files")
    print("  ok  a queued/running run in another Session blocks with the cross-Session 409, rows and files intact")


def test_two_concurrent_starts_leave_exactly_one_active_run():
    """The guard runs inside an explicit BEGIN IMMEDIATE, which takes
    SQLite's write lock before the conflict check and holds it through
    the INSERT. A check-then-insert gap would let both requests through
    and put two runs on one GPU."""
    _clear_runs()
    session_a = _new_session()
    session_b = _new_session()

    barrier = threading.Barrier(2)
    results = {}

    def start(session_id):
        barrier.wait()
        resp = client.post(f"/api/sessions/{session_id}/runs")
        results[session_id] = (resp.status_code, resp.json())

    with patch.object(adapter, "start_run", return_value=None):
        threads = [threading.Thread(target=start, args=(sid,))
                   for sid in (session_a, session_b)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        assert not any(t.is_alive() for t in threads), "a start request hung"

    codes = sorted(code for code, _ in results.values())
    assert codes == [202, 409], f"expected one success and one conflict, got {codes}"

    rejected = [body for code, body in results.values() if code == 409][0]
    assert rejected["error"] == "RUN_IN_PROGRESS", rejected

    conn = db.get_conn()
    try:
        active = conn.execute(
            "SELECT id FROM runs WHERE state IN ('queued', 'running')"
        ).fetchall()
    finally:
        conn.close()
    assert len(active) == 1, f"expected exactly one active run, found {len(active)}"
    print("  ok  two concurrent starts leave exactly one success and exactly one active run")


def test_terminal_prior_run_allows_replacement():
    for state in ("complete", "failed"):
        _clear_runs()
        session_id = _new_session()
        old_id = _seed_run(session_id, state)

        with patch.object(adapter, "start_run", return_value=None):
            resp = client.post(f"/api/sessions/{session_id}/runs")
            assert resp.status_code == 202, resp.text
            new_id = resp.json()["id"]

        assert not _run_exists(old_id), (
            f"a terminal ({state}) prior run was not replaced"
        )
        assert _run_exists(new_id)
    print("  ok  a terminal (complete/failed) prior run in the same Session is replaced, not blocked")


def test_terminal_run_in_another_session_does_not_block():
    for state in ("complete", "failed"):
        _clear_runs()
        done_session = _new_session()
        other_session = _new_session()
        done_id = _seed_run(done_session, state)

        with patch.object(adapter, "start_run", return_value=None):
            resp = client.post(f"/api/sessions/{other_session}/runs")
            assert resp.status_code == 202, resp.text

        assert _run_exists(done_id), (
            f"a new run in another Session deleted a {state} run it does not own")
    print("  ok  a terminal run in another Session neither blocks a new start nor is deleted by it")


if __name__ == "__main__":
    tests = [
        test_queued_run_blocks_second_start_in_the_same_session,
        test_running_run_blocks_second_start_in_the_same_session,
        test_active_run_in_another_session_blocks_with_the_cross_session_message,
        test_two_concurrent_starts_leave_exactly_one_active_run,
        test_terminal_prior_run_allows_replacement,
        test_terminal_run_in_another_session_does_not_block,
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
