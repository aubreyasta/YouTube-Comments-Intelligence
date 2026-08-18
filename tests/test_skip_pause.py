"""
Offline self-check for T2.3: the skipPause request contract, the
RunSnapshot boolean, adapter._execute()'s skip branch, and the
runs.skip_pause migration, all covered offline via server.py + adapter.py.

Points db._DB_PATH and storage._ROOT at temp locations before importing
server (server.app's startup hook calls db.init() against whatever path
db._DB_PATH holds at that time; storage._ROOT gates every data/runs and
data/artifacts write adapter.py makes), then drives the routes through
FastAPI's TestClient. adapter._execute()'s pipeline edges (collect,
brief.reconcile, analyze, affect, report, and every Ollama-touching call)
are mocked - no network, no Ollama, no HuggingFace, no real PDF render -
but the run still executes on adapter.start_run()'s real daemon thread,
so the skip_pause branch and DB writes are exercised for real.

Run: python tests/test_skip_pause.py
"""

import os
import pathlib
import sys
import tempfile
import threading
import time
import uuid
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
_ORIG_DB_PATH = db._DB_PATH
db._DB_PATH = pathlib.Path(tempfile.mkdtemp()) / "app.db"

import storage
_ORIG_STORAGE_ROOT = storage._ROOT
storage._ROOT = tempfile.mkdtemp()

from starlette.testclient import TestClient

# server refuses to start without APP_PASSWORD, and its Basic Auth
# middleware guards every route. Set before import so the startup hook sees it.
os.environ.setdefault("APP_PASSWORD", "test-password")

import server
import adapter

db.init()  # server's startup hook only fires inside TestClient's `with` block
client = TestClient(server.app, headers={"Authorization": "Basic b2ZmaWNlOnRlc3QtcGFzc3dvcmQ="})

_RUN_SNAPSHOT_KEYS = {
    "id", "sessionId", "status", "stage", "pct", "message",
    "error", "briefPoints", "artifacts", "skipPause",
}


def _wait_until(pred, timeout=5.0, interval=0.02):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(interval)
    return False


def _new_session_with_video():
    sid = client.post("/api/sessions", json={"name": "S"}).json()["id"]
    cid = client.post(f"/api/sessions/{sid}/campaigns", json={"name": "C"}).json()["id"]
    client.post(f"/api/campaigns/{cid}/videos",
                json={"url": "https://youtu.be/abcdefghijk"})
    return sid, cid


def _fake_comments_df():
    return pd.DataFrame([
        {"video_id": "abcdefghijk", "group": "C", "comment": "Great value",
         "likes": 3, "reply_count": 0, "in_base": True, "theme": "Other",
         "emotion": "neutral", "sentiment": "positive"},
    ])


def _fake_meta_df():
    return pd.DataFrame([
        {"video_id": "abcdefghijk", "group": "C", "kind": "auto",
         "title": "t", "channel": "ch", "description": "d",
         "transcript": "We cut the price.", "has_transcript": True},
    ])


def _fake_affect_result():
    empty_table = pd.DataFrame()
    return {
        "emotion": {"table": empty_table, "caveat": ""},
        "sentiment": {"table": empty_table, "caveat": ""},
    }


def _fake_render(markdown, out_dir, cfg, debug_dir, _df=None, _transfer=None):
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "report.pdf"), "wb") as f:
        f.write(b"%PDF-1.4 fake report\n")


def _fake_export(base_df, theme_table, transfer_table, affect_result, meta_df, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    for name in ("comments.csv", "key-messages.csv", "themes.csv",
                 "sentiment.csv", "emotions.csv"):
        with open(os.path.join(out_dir, name), "w", encoding="utf-8") as f:
            f.write("placeholder\n")


def _patched_pipeline(reconcile_side_effect):
    """Context managers covering every pipeline edge _execute() touches,
    including every stage after brief/brief_pause, so the run reaches
    complete deterministically with no network, Ollama, or HuggingFace
    call."""
    fake_df = _fake_comments_df()
    return (
        patch.object(adapter.pipeline_llm, "preflight", return_value=None),
        patch.object(adapter.collect, "fetch",
                    return_value=(fake_df, _fake_meta_df())),
        patch.object(adapter.collect, "clean", side_effect=lambda df, cfg: df),
        patch.object(adapter.brief, "reconcile", side_effect=reconcile_side_effect),
        patch.object(adapter.analyze, "build", return_value=[]),
        patch.object(adapter.analyze, "classify",
                    side_effect=lambda df, themes, points, cfg, on_progress=None: (df, {})),
        patch.object(adapter.analyze, "extend",
                    side_effect=lambda df, themes, points, summary, cfg, on_progress=None: (df, themes, 0.0)),
        patch.object(adapter.analyze, "affect",
                    side_effect=lambda df, cfg: (df, _fake_affect_result())),
        patch.object(adapter.pipeline_llm, "unload", return_value=None),
        patch.object(adapter.pipeline_report, "write", return_value="# report"),
        patch.object(adapter.pipeline_report, "render", side_effect=_fake_render),
        patch.object(adapter.pipeline_report, "export", side_effect=_fake_export),
        patch.object(adapter, "_build_prose",
                    return_value={"title": "t", "interpretation": "i",
                                  "quote": {"text": "q", "attr": "a"},
                                  "caveat": "c"}),
    )


def _run_reconcile_of(proposals, included=True):
    """A drop-in for brief.reconcile(..., include_grounded=True) that
    ignores its transcript/context arguments and returns fixed points.
    `included` is parametrized so an all-excluded list can be produced
    (skip_pause must still pause with zero included points)."""
    def fn(existing, meta_df, cfg, context_map=None, images_map=None,
           id_factory=uuid.uuid4, include_grounded=False):
        reconciled = [
            {"id": str(id_factory()), "label": lbl, "description": desc,
             "included": included, "order": i, "edited": False}
            for i, (lbl, desc) in enumerate(proposals)
        ]
        return ("GROUNDED", reconciled) if include_grounded else reconciled
    return fn


def _wait_for_run_thread(run_id, timeout=5.0):
    """adapter._execute()'s daemon thread flips the DB row to
    complete/failed BEFORE its own `finally` block finishes calling
    pipeline_llm.unload() twice more. Waiting on the DB status alone is
    not enough to know the thread is done touching mocked pipeline
    functions; this waits for the actual thread object to exit."""
    name = f"adapter-run-{run_id[:8]}"
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not any(t.name == name for t in threading.enumerate()):
            return True
        time.sleep(0.02)
    return False


def _stop(patches, run_id=None):
    if run_id is not None:
        adapter.get_proceed_event(run_id).set()
        _wait_for_run_thread(run_id)
    for p in patches:
        p.stop()
    _terminate_stray_runs()


def _terminate_stray_runs():
    """The one-run guard in server.start_run() is global. A test that
    fails before its run reaches a terminal state leaves that run in
    `running` forever, and every later test then gets a 409 body with
    no `id` key. Flipping strays to `failed` in teardown keeps one
    failure to one test."""
    conn = db.get_conn()
    try:
        conn.execute(
            "UPDATE runs SET state = 'failed' "
            "WHERE state IN ('queued', 'running')"
        )
        conn.commit()
    finally:
        conn.close()


def _clear_runs():
    conn = db.get_conn()
    try:
        conn.execute("DELETE FROM runs")
        conn.commit()
    finally:
        conn.close()


def test_start_run_request_shapes_persist_skip_pause_correctly():
    values = {}

    def _read_skip_pause(rid):
        conn = db.get_conn()
        try:
            row = conn.execute("SELECT skip_pause FROM runs WHERE id = ?", (rid,)).fetchone()
            return row["skip_pause"]
        finally:
            conn.close()

    _clear_runs()
    session_id, _ = _new_session_with_video()
    with patch.object(adapter, "start_run", return_value=None):
        r1 = client.post(f"/api/sessions/{session_id}/runs")
        assert r1.status_code == 202, r1.text
        run_id_1 = r1.json()["id"]
    values["no body"] = _read_skip_pause(run_id_1)

    _clear_runs()
    session_id, _ = _new_session_with_video()
    with patch.object(adapter, "start_run", return_value=None):
        r2 = client.post(f"/api/sessions/{session_id}/runs", json={})
        assert r2.status_code == 202, r2.text
        run_id_2 = r2.json()["id"]
    values["empty json"] = _read_skip_pause(run_id_2)

    _clear_runs()
    session_id, _ = _new_session_with_video()
    with patch.object(adapter, "start_run", return_value=None):
        r3 = client.post(f"/api/sessions/{session_id}/runs", json={"skipPause": False})
        assert r3.status_code == 202, r3.text
        run_id_3 = r3.json()["id"]
    values["skipPause false"] = _read_skip_pause(run_id_3)

    _clear_runs()
    session_id, _ = _new_session_with_video()
    with patch.object(adapter, "start_run", return_value=None):
        r4 = client.post(f"/api/sessions/{session_id}/runs", json={"skipPause": True})
        assert r4.status_code == 202, r4.text
        run_id_4 = r4.json()["id"]
    values["skipPause true"] = _read_skip_pause(run_id_4)

    assert values["no body"] == 0, values
    assert values["empty json"] == 0, values
    assert values["skipPause false"] == 0, values
    assert values["skipPause true"] == 1, values
    print("  ok  no body / {} / skipPause:false all persist skip_pause=0, "
          "skipPause:true persists skip_pause=1")


def test_non_boolean_skip_pause_rejected_422_no_row_created():
    _clear_runs()
    session_id, _ = _new_session_with_video()

    conn = db.get_conn()
    try:
        before = conn.execute("SELECT COUNT(*) AS n FROM runs").fetchone()["n"]
    finally:
        conn.close()

    with patch.object(adapter, "start_run", return_value=None):
        resp = client.post(f"/api/sessions/{session_id}/runs",
                           json={"skipPause": "yes-please"})
    assert resp.status_code == 422, resp.text
    assert set(resp.json().keys()) == {"error", "message", "field"}, resp.json()

    conn = db.get_conn()
    try:
        after = conn.execute("SELECT COUNT(*) AS n FROM runs").fetchone()["n"]
    finally:
        conn.close()
    assert after == before, "422 skipPause validation failure created a run row"
    print("  ok  non-boolean skipPause -> 422 with exact {error,message,field} "
          "keys, no run row created")


def _assert_snapshot_skip_pause_bool(snapshot, expected):
    assert set(snapshot.keys()) == _RUN_SNAPSHOT_KEYS, snapshot.keys()
    assert type(snapshot["skipPause"]) is bool, type(snapshot["skipPause"])
    assert snapshot["skipPause"] is expected, snapshot["skipPause"]


def test_snapshot_skip_pause_is_real_bool_on_start_get_and_proceed():
    _clear_runs()

    # skipPause: True, zero included -> pauses, so proceed is reachable.
    session_id, _ = _new_session_with_video()
    patches = _patched_pipeline(_run_reconcile_of([("Idea", "d")], included=False))
    for p in patches:
        p.start()
    run_id = None
    try:
        start_resp = client.post(f"/api/sessions/{session_id}/runs",
                                 json={"skipPause": True})
        assert start_resp.status_code == 202, start_resp.text
        run_id = start_resp.json()["id"]
        _assert_snapshot_skip_pause_bool(start_resp.json(), True)

        assert _wait_until(
            lambda: client.get(f"/api/runs/{run_id}").json()["stage"] == "brief_pause"
        ), "run never reached brief_pause"
        get_resp = client.get(f"/api/runs/{run_id}")
        _assert_snapshot_skip_pause_bool(get_resp.json(), True)

        point = get_resp.json()["briefPoints"][0]
        patch_resp = client.patch(f"/api/runs/{run_id}/brief_points", json={"messages": [
            {"id": point["id"], "label": point["label"],
             "description": point["description"], "included": True,
             "order": point["order"]}
        ]})
        assert patch_resp.status_code == 200, patch_resp.text

        proceed_resp = client.post(f"/api/runs/{run_id}/proceed")
        assert proceed_resp.status_code == 200, proceed_resp.text
        _assert_snapshot_skip_pause_bool(proceed_resp.json(), True)

        assert _wait_until(
            lambda: client.get(f"/api/runs/{run_id}").json()["status"] in ("complete", "failed"))
    finally:
        _stop(patches, run_id)
    _terminate_stray_runs()

    # skipPause omitted -> False, distinguishable from int 0/1.
    session_id, _ = _new_session_with_video()
    with patch.object(adapter, "start_run", return_value=None):
        omitted_resp = client.post(f"/api/sessions/{session_id}/runs")
        assert omitted_resp.status_code == 202, omitted_resp.text
        _assert_snapshot_skip_pause_bool(omitted_resp.json(), False)
        get_resp = client.get(f"/api/runs/{omitted_resp.json()['id']}")
        _assert_snapshot_skip_pause_bool(get_resp.json(), False)

    print("  ok  RunSnapshot skipPause is type(...) is bool (not int 0/1) on "
          "start, GET, and proceed responses; exact ten-key snapshot shape")


def test_skip_true_with_included_message_skips_brief_pause():
    _clear_runs()
    session_id, _ = _new_session_with_video()

    patches = _patched_pipeline(_run_reconcile_of([("Idea", "d")], included=True))
    for p in patches:
        p.start()
    run_id = None
    try:
        resp = client.post(f"/api/sessions/{session_id}/runs", json={"skipPause": True})
        assert resp.status_code == 202, resp.text
        run_id = resp.json()["id"]

        assert _wait_until(
            lambda: client.get(f"/api/runs/{run_id}").json()["stage"]
                    in ("classify", "emotion", "report", "complete")
        ), "run with skipPause and an included message never advanced past brief"

        current = client.get(f"/api/runs/{run_id}").json()["stage"]
        assert current != "brief_pause", current

        assert adapter.get_proceed_event(run_id).is_set() is False, (
            "skip_pause branch must never set the proceed event - nothing calls it")

        assert _wait_until(
            lambda: client.get(f"/api/runs/{run_id}").json()["status"] in ("complete", "failed"))
    finally:
        _stop(patches, run_id)
    print("  ok  skipPause:true with >=1 included brief point never enters "
          "brief_pause, advances past brief with no /proceed call, and never "
          "sets the proceed event")


def test_skip_true_with_zero_included_still_pauses():
    _clear_runs()
    session_id, _ = _new_session_with_video()

    patches = _patched_pipeline(_run_reconcile_of([("Idea", "d")], included=False))
    for p in patches:
        p.start()
    run_id = None
    try:
        resp = client.post(f"/api/sessions/{session_id}/runs", json={"skipPause": True})
        assert resp.status_code == 202, resp.text
        run_id = resp.json()["id"]

        assert _wait_until(
            lambda: client.get(f"/api/runs/{run_id}").json()["stage"] == "brief_pause"
        ), "skipPause:true with zero included messages must still pause"

        # Stays paused: no progress without proceed.
        time.sleep(0.1)
        assert client.get(f"/api/runs/{run_id}").json()["stage"] == "brief_pause"

        blocked = client.post(f"/api/runs/{run_id}/proceed")
        assert blocked.status_code == 422, blocked.text
        assert blocked.json()["message"] == "Include at least one Key Message before continuing."
        assert blocked.json()["field"] == "messages"

        point = client.get(f"/api/runs/{run_id}").json()["briefPoints"][0]
        patch_resp = client.patch(f"/api/runs/{run_id}/brief_points", json={"messages": [
            {"id": point["id"], "label": point["label"],
             "description": point["description"], "included": True,
             "order": point["order"]}
        ]})
        assert patch_resp.status_code == 200, patch_resp.text

        proceed_resp = client.post(f"/api/runs/{run_id}/proceed")
        assert proceed_resp.status_code == 200, proceed_resp.text

        assert _wait_until(
            lambda: client.get(f"/api/runs/{run_id}").json()["status"] in ("complete", "failed"))
    finally:
        _stop(patches, run_id)
    print("  ok  skipPause:true with zero included brief points still enters "
          "and stays in brief_pause, refuses /proceed with 422 until a point "
          "is included")


def test_skip_false_behaves_as_before():
    _clear_runs()
    session_id, _ = _new_session_with_video()

    patches = _patched_pipeline(_run_reconcile_of([("Idea", "d")], included=True))
    for p in patches:
        p.start()
    run_id = None
    try:
        resp = client.post(f"/api/sessions/{session_id}/runs")
        assert resp.status_code == 202, resp.text
        run_id = resp.json()["id"]

        assert _wait_until(
            lambda: client.get(f"/api/runs/{run_id}").json()["stage"] == "brief_pause"
        ), "skipPause omitted must still pause as before"

        client.post(f"/api/runs/{run_id}/proceed")
        assert _wait_until(
            lambda: client.get(f"/api/runs/{run_id}").json()["status"] in ("complete", "failed"))
    finally:
        _stop(patches, run_id)
    print("  ok  skipPause omitted (default False) still enters brief_pause "
          "as it always has")


def test_restart_reopen_persists_skip_pause_true():
    _clear_runs()
    session_id, _ = _new_session_with_video()

    with patch.object(adapter, "start_run", return_value=None):
        resp = client.post(f"/api/sessions/{session_id}/runs", json={"skipPause": True})
        assert resp.status_code == 202, resp.text
        run_id = resp.json()["id"]

    # Simulate a fresh GET as if reopened after a restart - reads straight
    # from the persisted row, no in-memory state.
    reopened = client.get(f"/api/runs/{run_id}")
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["skipPause"] is True
    assert type(reopened.json()["skipPause"]) is bool
    print("  ok  a fresh GET /api/runs/{id} after 'restart' returns the "
          "persisted skipPause:true")


if __name__ == "__main__":
    tests = [
        test_start_run_request_shapes_persist_skip_pause_correctly,
        test_non_boolean_skip_pause_rejected_422_no_row_created,
        test_snapshot_skip_pause_is_real_bool_on_start_get_and_proceed,
        test_skip_true_with_included_message_skips_brief_pause,
        test_skip_true_with_zero_included_still_pauses,
        test_skip_false_behaves_as_before,
        test_restart_reopen_persists_skip_pause_true,
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
