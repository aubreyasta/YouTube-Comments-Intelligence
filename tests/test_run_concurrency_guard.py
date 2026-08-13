"""
Offline self-check for the start_run() concurrency guard in server.py:
a Session with a queued or running run must reject a second POST with
409 before any existing run row is touched; a Session whose only prior
run is terminal (complete/failed) must still allow the normal overwrite.

adapter.start_run() (the real pipeline thread) is mocked to a no-op so
this only exercises the guard/overwrite logic in the route handler - no
network, no model calls.

Run: python tests/test_run_concurrency_guard.py
"""

import os
import pathlib
import sys
import tempfile
import uuid
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
db._DB_PATH = pathlib.Path(tempfile.mkdtemp()) / "app.db"

from starlette.testclient import TestClient
import server
import adapter

db.init()  # server's startup hook only fires inside TestClient's `with` block
client = TestClient(server.app)


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


def _run_exists(run_id):
    conn = db.get_conn()
    try:
        return conn.execute(
            "SELECT id FROM runs WHERE id = ?", (run_id,)
        ).fetchone() is not None
    finally:
        conn.close()


def test_queued_run_blocks_second_start_with_409():
    session_id = _new_session()
    with patch.object(adapter, "start_run", return_value=None):
        first_id = client.post(f"/api/sessions/{session_id}/runs").json()["id"]

        resp = client.post(f"/api/sessions/{session_id}/runs")
        assert resp.status_code == 409, resp.text
        body = resp.json()["detail"]
        assert body["error"] == "conflict"
        assert body["message"] == "This session already has a run in progress."

        assert _run_exists(first_id), "409 path deleted the queued run"
    print("  ok  queued run blocks a second start with 409, existing row untouched")


def test_running_run_blocks_second_start_with_409():
    session_id = _new_session()
    run_id = _seed_run(session_id, "running")

    with patch.object(adapter, "start_run", return_value=None):
        resp = client.post(f"/api/sessions/{session_id}/runs")
        assert resp.status_code == 409, resp.text
        assert resp.json()["detail"]["message"] == "This session already has a run in progress."

    assert _run_exists(run_id), "409 path deleted the running run"
    print("  ok  running run blocks a second start with 409, existing row untouched")


def test_terminal_prior_run_allows_replacement():
    for state in ("complete", "failed"):
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
    print("  ok  a terminal (complete/failed) prior run is replaced, not blocked")


if __name__ == "__main__":
    tests = [
        test_queued_run_blocks_second_start_with_409,
        test_running_run_blocks_second_start_with_409,
        test_terminal_prior_run_allows_replacement,
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
