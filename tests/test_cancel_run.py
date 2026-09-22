"""
Offline self-check for stopping a run: DELETE /api/runs/{id} on a run that
is already going.

progress.publish() is the checkpoint. Its UPDATE only matches a running
row, so a stopped run raises progress.Cancelled at its next stage write and
adapter._execute unwinds. These tests therefore need the real _execute on a
real daemon thread: the same harness test_skip_pause.py uses, with the
pipeline edges mocked but every progress call genuine. A fake pipeline that
never publishes could not reach the checkpoint at all.

Run: python tests/test_cancel_run.py
"""

import os
import pathlib
import sys
import tempfile
import threading
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
_ORIG_DB_PATH = db._DB_PATH
db._DB_PATH = pathlib.Path(tempfile.mkdtemp()) / "app.db"

import storage
_ORIG_STORAGE_ROOT = storage._ROOT
storage._ROOT = tempfile.mkdtemp()

from starlette.testclient import TestClient

from auth_helper import login, wait_until

import adapter
import progress
import server

# The pipeline stand-ins, shared with tests/test_skip_pause.py. They touch
# no module seams, so importing them here is safe.
from pipeline_fakes import (
    patched_pipeline as _patched_pipeline,
    run_reconcile_of as _run_reconcile_of,
    wait_for_run_thread as _wait_for_run_thread,
)

db.init()
client = login(TestClient(server.app))


def _clear_runs():
    conn = db.get_conn()
    try:
        conn.execute("DELETE FROM runs")
        conn.commit()
    finally:
        conn.close()


def _terminate_stray_runs():
    """Runs share one global queue, so a test that fails before its run
    reaches a terminal state would leave every later test waiting behind
    it. Flipping strays to failed keeps one failure to one test."""
    conn = db.get_conn()
    try:
        conn.execute(
            "UPDATE runs SET state = 'failed' WHERE state IN ('queued', 'running')")
        conn.commit()
    finally:
        conn.close()


def _new_session_with_video():
    sid = client.post("/api/sessions", json={"name": "S"}).json()["id"]
    cid = client.post(f"/api/sessions/{sid}/campaigns", json={"name": "C"}).json()["id"]
    client.post(f"/api/campaigns/{cid}/videos",
                json={"url": "https://youtu.be/abcdefghijk"})
    return sid, cid


def _stage(run_id):
    return client.get(f"/api/runs/{run_id}").json()["stage"]


def _run(run_id):
    return client.get(f"/api/runs/{run_id}").json()


def _start():
    return client.post(
        f"/api/sessions/{_new_session_with_video()[0]}/runs").json()["id"]


def test_stopping_a_paused_run_reports_the_stop_not_a_pipeline_error():
    """The likely case: the user parks at the Key Message review, excludes
    everything, and bails. Without the Cancelled raise in await_review the
    run walks on and dies at the "all points excluded" guard, recording a
    ValueError and logging a traceback for a deliberate stop."""
    _clear_runs()
    patches = _patched_pipeline(_run_reconcile_of([("Idea", "d")], included=False))
    for p in patches:
        p.start()
    run_id = None
    try:
        run_id = _start()
        assert wait_until(lambda: _stage(run_id) == "brief_pause"), "never reached brief_pause"

        assert client.delete(f"/api/runs/{run_id}").status_code == 204
        assert wait_until(lambda: _run(run_id)["status"] == "failed"), _run(run_id)
        snap = _run(run_id)
        assert snap["error"] == "Stopped at your request.", snap
        assert snap["stage"] == "error", snap
        assert _wait_for_run_thread(run_id), "the stopped run's thread never exited"
        assert client.post(f"/api/runs/{run_id}/review_activity").status_code == 409, \
            "the stopped review left its activity entry behind"
    finally:
        for p in patches:
            p.stop()
        _terminate_stray_runs()
    print("  ok  stopping a paused run records the stop, not a pipeline error")


def test_stopping_mid_pipeline_unwinds_at_the_next_checkpoint():
    """The run is held inside analyze.build, which sits between two
    publishes. The stop lands while it is in there, and the thread must
    unwind at the publish that follows rather than finishing the run."""
    _clear_runs()
    entered, release = threading.Event(), threading.Event()

    def _blocking_build(df, summary, cfg):
        entered.set()
        release.wait(5)
        return []

    patches = _patched_pipeline(_run_reconcile_of([("Idea", "d")])) + (
        patch.object(adapter.analyze, "build", side_effect=_blocking_build),)
    for p in patches:
        p.start()
    run_id = None
    try:
        run_id = _start()
        assert wait_until(lambda: _stage(run_id) == "brief_pause"), "never reached brief_pause"
        assert client.post(f"/api/runs/{run_id}/proceed").status_code == 200
        assert entered.wait(5), "the run never reached the blocking stage"

        assert client.delete(f"/api/runs/{run_id}").status_code == 204
        assert _run(run_id)["status"] == "failed", "the stop was not recorded at once"
        release.set()

        assert _wait_for_run_thread(run_id), "the stopped run's thread never exited"
        snap = _run(run_id)
        assert snap["status"] == "failed" and snap["error"] == "Stopped at your request.", snap
    finally:
        release.set()
        for p in patches:
            p.stop()
        _terminate_stray_runs()
    print("  ok  a stopped run unwinds at its next checkpoint")


def test_a_stopped_run_is_never_flipped_to_complete():
    """The last publish is several steps before finish(), and nothing in
    between is a checkpoint. A stop landing in that window must survive: the
    pipeline runs to the end and finish() must leave the row alone."""
    _clear_runs()
    entered, release = threading.Event(), threading.Event()

    def _blocking_prose(*args, **kwargs):
        entered.set()
        release.wait(5)
        return {"title": "t", "interpretation": "i",
                "quote": {"text": "q", "attr": "a"}, "caveat": "c"}

    patches = _patched_pipeline(_run_reconcile_of([("Idea", "d")])) + (
        patch.object(adapter, "_build_prose", side_effect=_blocking_prose),)
    for p in patches:
        p.start()
    run_id = None
    try:
        run_id = _start()
        assert wait_until(lambda: _stage(run_id) == "brief_pause"), "never reached brief_pause"
        assert client.post(f"/api/runs/{run_id}/proceed").status_code == 200
        # Past the last publish, so nothing ahead of finish() can notice.
        assert entered.wait(5), "the run never reached the last stage"

        assert client.delete(f"/api/runs/{run_id}").status_code == 204
        release.set()
        assert _wait_for_run_thread(run_id), "the run's thread never exited"

        snap = _run(run_id)
        assert snap["status"] == "failed", f"finish() overwrote a stopped run: {snap}"
        assert snap["error"] == "Stopped at your request.", snap
    finally:
        release.set()
        for p in patches:
            p.stop()
        _terminate_stray_runs()
    print("  ok  a run stopped past its last checkpoint is not flipped to complete")


def test_stopping_a_run_frees_the_slot_for_the_queued_one():
    _clear_runs()
    patches = _patched_pipeline(_run_reconcile_of([("Idea", "d")]))
    for p in patches:
        p.start()
    waiting = None
    try:
        first = _start()
        assert wait_until(lambda: _stage(first) == "brief_pause"), "never reached brief_pause"
        waiting = _start()
        assert _run(waiting)["status"] == "queued", _run(waiting)

        assert client.delete(f"/api/runs/{first}").status_code == 204
        assert wait_until(lambda: _stage(waiting) == "brief_pause"), \
            "the queued run did not start after the running one was stopped"
    finally:
        for p in patches:
            p.stop()
        _terminate_stray_runs()
    print("  ok  stopping a run frees the slot for the one behind it")


def test_stopping_before_the_first_checkpoint_keeps_the_prior_result():
    """_clear_prior_runs is the run's first destructive act, so the first
    publish sits in front of it. A run stopped between the claim and that
    point must leave the Session's previous result alone."""
    _clear_runs()
    entered, release = threading.Event(), threading.Event()
    real_publish = progress.publish

    def _hold_first_publish(run_id, stage, message, pct, **counts):
        if pct == 2:
            entered.set()
            release.wait(5)
        return real_publish(run_id, stage, message, pct, **counts)

    patches = _patched_pipeline(_run_reconcile_of([("Idea", "d")])) + (
        patch.object(adapter.progress, "publish", side_effect=_hold_first_publish),)
    for p in patches:
        p.start()
    run_id = None
    try:
        run_id = _start()
        assert entered.wait(5), "the run never reached its first checkpoint"

        assert client.delete(f"/api/runs/{run_id}").status_code == 204
        release.set()
        assert _wait_for_run_thread(run_id), "the stopped run's thread never exited"
        assert _run(run_id)["error"] == "Stopped at your request.", _run(run_id)
    finally:
        release.set()
        for p in patches:
            p.stop()
        _terminate_stray_runs()
    print("  ok  a run stopped at its first checkpoint stops before overwriting anything")


def test_delete_is_idempotent_on_a_finished_run():
    _clear_runs()
    patches = _patched_pipeline(_run_reconcile_of([("Idea", "d")]))
    for p in patches:
        p.start()
    run_id = None
    try:
        run_id = _start()
        assert wait_until(lambda: _stage(run_id) == "brief_pause"), "never reached brief_pause"
        assert client.post(f"/api/runs/{run_id}/proceed").status_code == 200
        assert wait_until(lambda: _run(run_id)["status"] == "complete"), _run(run_id)

        assert client.delete(f"/api/runs/{run_id}").status_code == 204
        assert _run(run_id)["status"] == "complete", "DELETE stopped a finished run"
        assert client.delete("/api/runs/does-not-exist").status_code == 204
    finally:
        for p in patches:
            p.stop()
        _terminate_stray_runs()
    print("  ok  DELETE on a finished run changes nothing and stays 204")


if __name__ == "__main__":
    tests = [
        test_stopping_a_paused_run_reports_the_stop_not_a_pipeline_error,
        test_stopping_mid_pipeline_unwinds_at_the_next_checkpoint,
        test_a_stopped_run_is_never_flipped_to_complete,
        test_stopping_a_run_frees_the_slot_for_the_queued_one,
        test_stopping_before_the_first_checkpoint_keeps_the_prior_result,
        test_delete_is_idempotent_on_a_finished_run,
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
        finally:
            _terminate_stray_runs()

    db._DB_PATH = _ORIG_DB_PATH
    storage._ROOT = _ORIG_STORAGE_ROOT

    if failed:
        print(f"\nFAIL ({failed}/{len(tests)} failed)")
        sys.exit(1)
    print(f"\nPASS ({len(tests)}/{len(tests)})")
