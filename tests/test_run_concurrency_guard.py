"""
Offline self-check for the run queue: server.start_run() admits, and
adapter.start_next() claims through progress.claim_next().

One GPU serves one model at a time, so at most one run is `running`
across every Session. Every other start waits as `queued` and runs, in
order, when the slot frees. A Session's own running run is never
overwritten (409); its own queued run is replaced by the new request,
which keeps its place in line.
The Session's finished runs survive until its new run starts, so leaving
the queue keeps the old result.

Every test clears the runs table first, so no test depends on another
test's leftover rows. adapter._execute (the pipeline) is replaced by a
fake that holds the slot until a test releases it, then ends the run the
way the real one does: finish, then start_next.

Run: python tests/test_run_concurrency_guard.py
"""

import os
import pathlib
import sqlite3
import sys
import threading
import tempfile
import time
import uuid
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
db._DB_PATH = pathlib.Path(tempfile.mkdtemp()) / "app.db"

import storage
storage._ROOT = tempfile.mkdtemp()

from starlette.testclient import TestClient

from auth_helper import login, wait_until  # sets the sign-in env the startup hook requires

import server
import adapter
import progress

db.init()  # server's startup hook only fires inside TestClient's `with` block
client = login(TestClient(server.app))


class FakePipeline:
    """Stands in for adapter._execute. Each run blocks until release()."""

    def __init__(self):
        self.started = []
        self.gates = {}

    def __call__(self, run_id):
        self.started.append(run_id)
        gate = self.gates.setdefault(run_id, threading.Event())
        gate.wait(10)
        progress.finish(run_id)
        adapter.start_next()

    def release(self, run_id):
        self.gates.setdefault(run_id, threading.Event()).set()


def _new_session():
    return client.post("/api/sessions", json={"name": "S"}).json()["id"]


def _seed_run(session_id, state, started_at=None):
    """Insert a run row directly, bypassing the route, so 'running' can be
    tested without a real pipeline thread reaching that state."""
    conn = db.get_conn()
    try:
        rid = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO runs (id, session_id, state, started_at) VALUES (?, ?, ?, ?)",
            (rid, session_id, state, started_at or server._now()),
        )
        conn.commit()
    finally:
        conn.close()
    return rid


def _seed_sentinel_file(run_id):
    """Drop a marker file under this run's storage dir. If a path wrongly
    overwrote the run, storage.clear_run(run_id) would delete it."""
    path = pathlib.Path(storage.run_dir(run_id)) / "sentinel.txt"
    path.write_text("sentinel")
    return path


def _states():
    conn = db.get_conn()
    try:
        return {r["id"]: r["state"] for r in conn.execute("SELECT id, state FROM runs")}
    finally:
        conn.close()


def _clear_runs():
    conn = db.get_conn()
    try:
        conn.execute("DELETE FROM runs")
        conn.commit()
    finally:
        conn.close()


def test_second_session_queues_then_runs_on_its_own():
    _clear_runs()
    fake = FakePipeline()
    with patch.object(adapter, "_execute", fake):
        first = client.post(f"/api/sessions/{_new_session()}/runs")
        second = client.post(f"/api/sessions/{_new_session()}/runs")
        assert (first.status_code, second.status_code) == (202, 202), second.text
        a, b = first.json()["id"], second.json()["id"]
        assert (first.json()["status"], second.json()["status"]) == ("running", "queued")

        assert wait_until(lambda: fake.started == [a]), fake.started
        assert _states() == {a: "running", b: "queued"}, _states()
        snap = client.get(f"/api/runs/{b}").json()
        assert (snap["stage"], snap["queuePosition"]) == ("queued", 1), snap
        assert client.get(f"/api/runs/{a}").json()["queuePosition"] is None

        fake.release(a)
        assert wait_until(lambda: fake.started == [a, b]), fake.started
        assert _states() == {a: "complete", b: "running"}, _states()
        fake.release(b)
        assert wait_until(lambda: _states()[b] == "complete"), _states()
    print("  ok  a second Session's run queues, shows its position, and starts on its own")


def test_queue_positions_follow_start_order():
    _clear_runs()
    running = _seed_run(_new_session(), "running")
    with patch.object(adapter, "start_next", return_value=None):
        ids = [client.post(f"/api/sessions/{_new_session()}/runs").json()["id"]
               for _ in range(3)]
    positions = [client.get(f"/api/runs/{rid}").json()["queuePosition"] for rid in ids]
    assert positions == [1, 2, 3], positions
    assert _states()[running] == "running"
    print("  ok  queued runs are numbered 1, 2, 3 in start order")


def test_waiting_session_reads_queued_not_running():
    _clear_runs()
    running_session, waiting_session = _new_session(), _new_session()
    _seed_run(running_session, "running")
    with patch.object(adapter, "start_next", return_value=None):
        client.post(f"/api/sessions/{waiting_session}/runs")
    status = {s["id"]: s["status"] for s in client.get("/api/sessions").json()}
    assert (status[running_session], status[waiting_session]) == ("running", "queued"), status
    assert client.get(f"/api/sessions/{waiting_session}").json()["status"] == "queued"
    print("  ok  a Session waiting in the queue reads queued, not running")


def test_same_session_queued_run_is_replaced():
    _clear_runs()
    _seed_run(_new_session(), "running")
    session_id = _new_session()
    with patch.object(adapter, "start_next", return_value=None):
        first = client.post(f"/api/sessions/{session_id}/runs").json()["id"]
        client.post(f"/api/sessions/{_new_session()}/runs")
        resp = client.post(f"/api/sessions/{session_id}/runs", json={"skipPause": True})
    assert resp.status_code == 202, resp.text
    second = resp.json()["id"]
    states = _states()
    assert first not in states, "the Session's earlier queued run was not replaced"
    assert states[second] == "queued" and resp.json()["skipPause"] is True
    assert resp.json()["queuePosition"] == 1, resp.json()["queuePosition"]
    print("  ok  a second start in the same Session replaces its queued run in place")


def test_running_run_blocks_second_start_in_the_same_session():
    _clear_runs()
    session_id = _new_session()
    run_id = _seed_run(session_id, "running")
    sentinel = _seed_sentinel_file(run_id)

    with patch.object(adapter, "start_next", return_value=None):
        resp = client.post(f"/api/sessions/{session_id}/runs")
    assert resp.status_code == 409, resp.text
    assert resp.json() == {
        "error": "RUN_IN_PROGRESS",
        "message": "This session already has a run in progress.",
        "field": None,
    }
    assert _states() == {run_id: "running"}, "409 path touched the runs table"
    assert sentinel.exists(), "409 path deleted the running run's files"
    print("  ok  a running run blocks a second start in the same Session with a 409, files intact")


def test_two_concurrent_starts_leave_exactly_one_running_run():
    """claim_next() runs inside BEGIN IMMEDIATE. A check-then-claim gap
    would let both requests start a run and put two runs on one GPU."""
    _clear_runs()
    fake = FakePipeline()
    barrier = threading.Barrier(2)
    codes = []

    def start(session_id):
        barrier.wait()
        codes.append(client.post(f"/api/sessions/{session_id}/runs").status_code)

    with patch.object(adapter, "_execute", fake):
        threads = [threading.Thread(target=start, args=(_new_session(),)) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        assert not any(t.is_alive() for t in threads), "a start request hung"
        assert codes == [202, 202], codes
        assert wait_until(lambda: len(fake.started) == 1)
        time.sleep(0.2)  # a second claim, if the guard were broken, lands here
        assert sorted(_states().values()) == ["queued", "running"], _states()
        assert len(fake.started) == 1, fake.started
        for rid in list(_states()):
            fake.release(rid)
        assert wait_until(lambda: set(_states().values()) == {"complete"}), _states()
    print("  ok  two concurrent starts leave one running run and one queued run")


def test_prior_result_survives_queueing_and_leaving_the_queue():
    _clear_runs()
    _seed_run(_new_session(), "running")
    session_id = _new_session()
    done = _seed_run(session_id, "complete", "2026-01-01T00:00:00+00:00")
    sentinel = _seed_sentinel_file(done)

    with patch.object(adapter, "start_next", return_value=None):
        queued = client.post(f"/api/sessions/{session_id}/runs").json()["id"]
    assert done in _states() and sentinel.exists(), "queueing deleted the prior result"

    resp = client.delete(f"/api/runs/{queued}")
    assert resp.status_code == 204, resp.text
    assert queued not in _states(), "the queued run is still there"
    assert _states()[done] == "complete" and sentinel.exists(), "leaving the queue lost the prior result"
    print("  ok  the prior result survives queueing and leaving the queue")


def test_prior_result_is_replaced_when_the_new_run_starts():
    """The real _execute clears prior runs first; the Session here has no
    campaign, so the run then fails and frees the slot."""
    _clear_runs()
    session_id = _new_session()
    done = _seed_run(session_id, "complete", "2026-01-01T00:00:00+00:00")
    sentinel = _seed_sentinel_file(done)
    other = _seed_run(_new_session(), "complete", "2026-01-01T00:00:00+00:00")

    new = client.post(f"/api/sessions/{session_id}/runs").json()["id"]
    assert wait_until(lambda: _states().get(new) == "failed"), _states()
    assert done not in _states() and not sentinel.exists(), "the prior result was not replaced"
    assert other in _states(), "a start deleted another Session's run"
    print("  ok  the prior result is replaced when the new run starts, other Sessions untouched")


def test_leaving_the_queue_leaves_the_active_run_alone():
    _clear_runs()
    running = _seed_run(_new_session(), "running")
    sentinel = _seed_sentinel_file(running)
    with patch.object(adapter, "start_next", return_value=None):
        queued = client.post(f"/api/sessions/{_new_session()}/runs").json()["id"]

    assert client.delete(f"/api/runs/{queued}").status_code == 204
    assert client.delete(f"/api/runs/{queued}").status_code == 204, "not idempotent"
    resp = client.delete(f"/api/runs/{running}")
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"] == "CONFLICT", resp.json()
    assert _states() == {running: "running"} and sentinel.exists()
    print("  ok  cancelling a queued run is idempotent; a running run is refused and untouched")


def test_startup_fails_running_runs_and_resumes_the_queue():
    """No run thread survives a restart, so the startup hook fails every
    running row. Queued rows have lost nothing: the queue picks them up."""
    _clear_runs()
    running_id = _seed_run(_new_session(), "running")
    queued_id = _seed_run(_new_session(), "queued")
    complete_id = _seed_run(_new_session(), "complete")

    fake = FakePipeline()
    with patch.object(adapter, "_execute", fake):
        server._startup()
        assert wait_until(lambda: fake.started == [queued_id]), fake.started

        conn = db.get_conn()
        try:
            rows = {r["id"]: r for r in conn.execute("SELECT * FROM runs").fetchall()}
        finally:
            conn.close()
        row = rows[running_id]
        assert (row["state"], row["stage"]) == ("failed", "error"), dict(row)
        assert row["error"].startswith("Interrupted"), row["error"]
        assert row["finished_at"], "orphaned run has no finished_at"
        assert rows[queued_id]["state"] == "running", dict(rows[queued_id])
        assert rows[complete_id]["state"] == "complete", "startup touched a complete run"
        fake.release(queued_id)
        assert wait_until(lambda: _states()[queued_id] == "complete")
    print("  ok  startup fails running runs, keeps and starts queued runs, leaves complete runs alone")


def test_failed_claim_is_retried_not_raised():
    _clear_runs()
    fake = FakePipeline()
    real_claim = progress.claim_next
    failures = [sqlite3.OperationalError("database is locked")]

    def flaky_claim():
        if failures:
            raise failures.pop()
        return real_claim()

    # The retry Timer runs at once instead of after 5 s.
    now = lambda _delay, fn: threading.Thread(target=fn)
    with patch.object(adapter, "_execute", fake),             patch.object(progress, "claim_next", flaky_claim),             patch.object(adapter.threading, "Timer", now):
        resp = client.post(f"/api/sessions/{_new_session()}/runs")
        assert resp.status_code == 202, resp.text
        rid = resp.json()["id"]
        assert wait_until(lambda: fake.started == [rid]), fake.started
        fake.release(rid)
        assert wait_until(lambda: _states()[rid] == "complete"), _states()
    print("  ok  a failed claim is retried instead of stalling the queue")


if __name__ == "__main__":
    tests = [
        test_second_session_queues_then_runs_on_its_own,
        test_queue_positions_follow_start_order,
        test_waiting_session_reads_queued_not_running,
        test_same_session_queued_run_is_replaced,
        test_running_run_blocks_second_start_in_the_same_session,
        test_two_concurrent_starts_leave_exactly_one_running_run,
        test_prior_result_survives_queueing_and_leaving_the_queue,
        test_prior_result_is_replaced_when_the_new_run_starts,
        test_leaving_the_queue_leaves_the_active_run_alone,
        test_startup_fails_running_runs_and_resumes_the_queue,
        test_failed_claim_is_retried_not_raised,
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
