"""
Offline self-check for Wave 2 run integration: Session Key Message
snapshot -> transcript reconciliation -> run brief_points, the persisted
run stage, and the associated 409/PATCH/proceed gating in server.py.

Points db._DB_PATH and storage._ROOT at temp locations before importing
server (server.app's startup hook calls db.init() against whatever path
db._DB_PATH holds at that time; storage._ROOT gates every data/runs and
data/artifacts write adapter.py makes), then drives the routes through
FastAPI's TestClient. Both globals are restored on exit so this file
never leaves anything under the repo's real data/ tree.
adapter._execute()'s pipeline edges (collect, brief.reconcile, analyze,
affect, report, and every Ollama-touching call after brief_pause) are
mocked - no network, no Ollama, no HuggingFace, no real PDF render - but
the run still executes on adapter.start_run()'s real daemon thread, so
the brief_pause block and DB writes are exercised for real.

Run: python tests/test_run_key_messages.py
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
import server
import adapter

db.init()  # server's startup hook only fires inside TestClient's `with` block
client = TestClient(server.app)

_RUN_STAGES = {"queued", "collect", "brief", "brief_pause", "classify",
              "emotion", "report", "complete", "error"}
_BRIEF_POINT_KEYS = {"id", "label", "description", "included", "order"}
_RUN_SNAPSHOT_KEYS = {
    "id", "sessionId", "status", "stage", "pct", "message",
    "error", "briefPoints", "artifacts",
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


def _seed_key_message(session_id, label, description="", included=True,
                      order=0, edited=0):
    conn = db.get_conn()
    try:
        mid = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO key_messages (id, session_id, label, description, "
            "included, sort_order, edited) VALUES (?,?,?,?,?,?,?)",
            (mid, session_id, label, description, int(included), order, int(edited)),
        )
        conn.commit()
    finally:
        conn.close()
    return mid


def _fake_comments_df():
    return pd.DataFrame([
        {"video_id": "abcdefghijk", "group": "C", "comment": "Great value",
         "likes": 3, "reply_count": 0, "in_base": True, "theme": "Other",
         "emotion": "neutral", "emotion_confidence": 0.9,
         "sentiment": "positive", "sentiment_confidence": 0.9},
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
        "emotion": {"table": empty_table, "low_confidence_pct": 0.0, "caveat": ""},
        "sentiment": {"table": empty_table, "low_confidence_pct": 0.0, "caveat": ""},
    }


def _patched_pipeline(reconcile_side_effect):
    """Context managers covering every pipeline edge _execute() touches,
    including every stage AFTER brief_pause, so the run reaches complete
    (or a controlled failure) deterministically with no network, Ollama,
    or HuggingFace call. classify()/extend()/affect()/report all normally
    call into pipeline.llm or transformers; every one of those edges is
    stubbed here rather than left to run for real post-pause."""
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
        patch.object(adapter.pipeline_report, "render", return_value=None),
        patch.object(adapter.pipeline_report, "export", return_value=None),
        patch.object(adapter, "_build_prose",
                    return_value={"title": "t", "interpretation": "i",
                                  "quote": {"text": "q", "attr": "a"},
                                  "caveat": "c"}),
    )


def _run_reconcile_of(proposals):
    """A drop-in for brief.reconcile(..., include_grounded=True) that
    ignores its transcript/context arguments and returns fixed points,
    mirroring _draft_of() in test_key_messages_draft.py."""
    def fn(existing, meta_df, cfg, context_map=None, images_map=None,
           id_factory=uuid.uuid4, include_grounded=False):
        reconciled = [
            {"id": str(id_factory()), "label": lbl, "description": desc,
             "included": True, "order": i, "edited": False}
            for i, (lbl, desc) in enumerate(proposals)
        ]
        return ("GROUNDED", reconciled) if include_grounded else reconciled
    return fn


def _run_to_brief_pause(session_id, proposals):
    """POST a run, start every pipeline patch, and wait for brief_pause.
    Returns (run_id, running_patches). Caller must call _stop(patches,
    run_id) in a `finally` once done - post-proceed stages keep running
    on the daemon thread and touching the same mocked functions, so
    _stop() waits for that thread to actually exit before unpatching."""
    patches = _patched_pipeline(_run_reconcile_of(proposals))
    for p in patches:
        p.start()
    run_id = client.post(f"/api/sessions/{session_id}/runs").json()["id"]
    assert _wait_until(
        lambda: client.get(f"/api/runs/{run_id}").json()["stage"] == "brief_pause"
    ), "run never reached brief_pause"
    return run_id, patches


def _wait_for_run_thread(run_id, timeout=5.0):
    """adapter._execute()'s daemon thread flips the DB row to
    complete/failed BEFORE its own `finally` block finishes calling
    pipeline_llm.unload() twice more. Waiting on the DB status alone is
    not enough to know the thread is done touching mocked pipeline
    functions; this waits for the actual thread object to exit so
    _stop() never removes a mock the thread is still mid-call on."""
    name = f"adapter-run-{run_id[:8]}"
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not any(t.name == name for t in threading.enumerate()):
            return True
        time.sleep(0.02)
    return False


def _stop(patches, run_id=None):
    if run_id is not None:
        _wait_for_run_thread(run_id)
    for p in patches:
        p.stop()


def test_second_concurrent_run_rejected_with_409_before_delete():
    session_id, campaign_id = _new_session_with_video()
    _seed_key_message(session_id, "Lower price", "d")

    run_id, patches = _run_to_brief_pause(session_id, [("Lower price", "d")])
    try:
        r2 = client.post(f"/api/sessions/{session_id}/runs")
        assert r2.status_code == 409, r2.text
        assert r2.json() == {
            "error": "RUN_IN_PROGRESS",
            "message": "This session already has a run in progress.",
            "field": None,
        }, r2.json()

        # The first run's row must still exist - rejecting the second
        # request must not have deleted anything.
        conn = db.get_conn()
        try:
            still_there = conn.execute(
                "SELECT id FROM runs WHERE id = ?", (run_id,)
            ).fetchone()
        finally:
            conn.close()
        assert still_there is not None, "409 path deleted the in-flight run"

        client.post(f"/api/runs/{run_id}/proceed")
        assert _wait_until(
            lambda: client.get(f"/api/runs/{run_id}").json()["status"] in ("complete", "failed"))
    finally:
        _stop(patches, run_id)
    print("  ok  second queued/running run rejected 409 with the exact "
          "RUN_IN_PROGRESS body, first run's row untouched")


def test_snapshot_and_reconcile_replace_brief_points_before_pause():
    """brief.reconcile()'s own merge semantics (edited entries survive,
    unedited unmatched ones drop, transcript-only ones append) are
    covered by tests/test_brief_key_messages.py. This test proves the
    plumbing around it: adapter._execute() (a) snapshots the Session's
    current key_messages and passes that exact snapshot as `existing` to
    brief.reconcile(), and (b) writes reconcile()'s return value - not
    the raw snapshot - into the run's brief_points before brief_pause,
    with video_id NULL on every row (Session-level Key Messages are not
    scoped to one video)."""
    session_id, campaign_id = _new_session_with_video()
    _seed_key_message(session_id, "Durability", "user wording",
                      included=False, order=0, edited=1)

    seen_existing = {}

    def recording_reconcile(existing, meta_df, cfg, context_map=None,
                            images_map=None, id_factory=uuid.uuid4,
                            include_grounded=False):
        seen_existing["value"] = existing
        reconciled = [{
            "id": str(id_factory()), "label": "Reconciled output",
            "description": "from reconcile", "included": True,
            "order": 0, "edited": False,
        }]
        return ("GROUNDED", reconciled) if include_grounded else reconciled

    patches = _patched_pipeline(recording_reconcile)
    for p in patches:
        p.start()
    try:
        run_id = client.post(f"/api/sessions/{session_id}/runs").json()["id"]
        assert _wait_until(
            lambda: client.get(f"/api/runs/{run_id}").json()["stage"] == "brief_pause"
        ), "run never reached brief_pause"

        # (a) reconcile() was called with the Session's snapshot, not [].
        assert len(seen_existing["value"]) == 1
        assert seen_existing["value"][0]["label"] == "Durability"
        assert seen_existing["value"][0]["edited"] is True

        # (b) the run's brief_points are reconcile()'s return value, not
        # the raw pre-collect snapshot (label "Durability" is gone, the
        # reconciled label is what's there; video_id is NULL/broadcast).
        run = client.get(f"/api/runs/{run_id}").json()
        labels = [p["label"] for p in run["briefPoints"]]
        assert labels == ["Reconciled output"], labels

        conn = db.get_conn()
        try:
            video_ids = [r[0] for r in conn.execute(
                "SELECT video_id FROM brief_points WHERE run_id = ?", (run_id,)
            ).fetchall()]
        finally:
            conn.close()
        assert all(v is None for v in video_ids), video_ids

        client.post(f"/api/runs/{run_id}/proceed")
        assert _wait_until(
            lambda: client.get(f"/api/runs/{run_id}").json()["status"] in ("complete", "failed"))
    finally:
        _stop(patches, run_id)
    print("  ok  adapter passes the Session snapshot into brief.reconcile() as "
          "`existing`, then replaces run brief_points with reconcile()'s "
          "return value (video_id NULL / broadcast) before brief_pause")


def test_session_key_messages_never_mutated_by_a_run():
    session_id, campaign_id = _new_session_with_video()
    _seed_key_message(session_id, "Original", "original text", included=True, order=0, edited=0)

    def before_snapshot():
        conn = db.get_conn()
        try:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM key_messages WHERE session_id = ?", (session_id,)
            ).fetchall()]
        finally:
            conn.close()

    before = before_snapshot()

    run_id, patches = _run_to_brief_pause(session_id, [("Transcript idea", "d")])
    try:
        client.post(f"/api/runs/{run_id}/proceed")
        assert _wait_until(
            lambda: client.get(f"/api/runs/{run_id}").json()["status"] == "complete",
            timeout=5.0), "run did not reach complete (every pipeline edge is mocked to succeed)"
    finally:
        _stop(patches, run_id)

    after = before_snapshot()
    assert after == before, "Session key_messages were mutated by a run"
    print("  ok  Session key_messages table is untouched by a run, start to finish")


def test_patch_and_proceed_gated_on_persisted_brief_pause_stage():
    session_id, campaign_id = _new_session_with_video()
    _seed_key_message(session_id, "Idea", "d")

    run_id, patches = _run_to_brief_pause(session_id, [("Idea", "d")])
    try:
        run = client.get(f"/api/runs/{run_id}").json()
        assert run["stage"] == "brief_pause"
        assert set(run.keys()) == _RUN_SNAPSHOT_KEYS
        assert run["briefPoints"], "expected the reconciled point to be present"

        # Open: PATCH and proceed succeed while paused.
        pid = run["briefPoints"][0]["id"]
        ok = client.patch(f"/api/runs/{run_id}/brief_points", json={"messages": [
            {"id": pid, "label": "Edited", "description": "d2", "included": True, "order": 0}
        ]})
        assert ok.status_code == 200, ok.text
        assert set(ok.json().keys()) == {"messages"}
        assert set(ok.json()["messages"][0].keys()) == _BRIEF_POINT_KEYS

        # Compact 422 checks: label/description length limits, reusing
        # this run's already-open pause window rather than a new setup.
        too_long_label = client.patch(f"/api/runs/{run_id}/brief_points", json={"messages": [
            {"id": pid, "label": "x" * 121, "description": "d2", "included": True, "order": 0}
        ]})
        assert too_long_label.status_code == 422, too_long_label.text
        assert too_long_label.json() == {
            "error": "VALIDATION_ERROR",
            "message": "Key Message labels are limited to 120 characters.",
            "field": "messages",
        }, too_long_label.json()

        too_long_desc = client.patch(f"/api/runs/{run_id}/brief_points", json={"messages": [
            {"id": pid, "label": "Edited", "description": "x" * 501, "included": True, "order": 0}
        ]})
        assert too_long_desc.status_code == 422, too_long_desc.text
        assert too_long_desc.json() == {
            "error": "VALIDATION_ERROR",
            "message": "Key Message descriptions are limited to 500 characters.",
            "field": "messages",
        }, too_long_desc.json()

        proceeded = client.post(f"/api/runs/{run_id}/proceed")
        assert proceeded.status_code == 200, proceeded.text
        snapshot = proceeded.json()
        assert set(snapshot.keys()) == _RUN_SNAPSHOT_KEYS
        assert snapshot["stage"] in _RUN_STAGES

        # Closed again: after proceed, the review window does not reopen.
        late = client.patch(f"/api/runs/{run_id}/brief_points", json={"messages": [
            {"id": pid, "label": "Too late", "description": "", "included": True, "order": 0}
        ]})
        assert late.status_code == 409, late.text

        late_proceed = client.post(f"/api/runs/{run_id}/proceed")
        assert late_proceed.status_code == 409, late_proceed.text

        assert _wait_until(
            lambda: client.get(f"/api/runs/{run_id}").json()["status"] in ("complete", "failed"))
    finally:
        _stop(patches, run_id)
    print("  ok  PATCH/proceed open at persisted brief_pause, closed again "
          "immediately after proceed; RunSnapshot always carries briefPoints "
          "and artifacts and proceed returns a snapshot with a locked stage")


def test_id_null_creates_uuid_and_full_ordered_replace():
    session_id, campaign_id = _new_session_with_video()

    run_id, patches = _run_to_brief_pause(session_id, [("Idea A", "a"), ("Idea B", "b")])
    try:
        run = client.get(f"/api/runs/{run_id}").json()
        existing_ids = {p["id"] for p in run["briefPoints"]}
        assert len(existing_ids) == 2

        resp = client.patch(f"/api/runs/{run_id}/brief_points", json={"messages": [
            {"id": None, "label": "New idea", "description": "d",
             "included": True, "order": 0},
        ]})
        assert resp.status_code == 200, resp.text
        saved = resp.json()["messages"]

        # Complete ordered replacement/deletion: the two prior rows are
        # gone, only the new one remains, with a server-minted id.
        assert len(saved) == 1, saved
        assert saved[0]["id"] not in existing_ids
        assert uuid.UUID(saved[0]["id"])  # server-generated UUID string
        assert saved[0]["label"] == "New idea"
        assert saved[0]["order"] == 0

        conn = db.get_conn()
        try:
            remaining = [r[0] for r in conn.execute(
                "SELECT id FROM brief_points WHERE run_id = ?", (run_id,)
            ).fetchall()]
        finally:
            conn.close()
        assert remaining == [saved[0]["id"]], remaining

        # Order normalization: submit three rows carrying non-sequential
        # client `order` values (7, 2, 9) in this exact array position -
        # the server must ignore those values and re-derive order from
        # submission position, returning [0, 1, 2] in that same order.
        reorder_resp = client.patch(f"/api/runs/{run_id}/brief_points", json={"messages": [
            {"id": None, "label": "First", "description": "", "included": True, "order": 7},
            {"id": None, "label": "Second", "description": "", "included": True, "order": 2},
            {"id": None, "label": "Third", "description": "", "included": True, "order": 9},
        ]})
        assert reorder_resp.status_code == 200, reorder_resp.text
        reordered = reorder_resp.json()["messages"]
        assert [m["label"] for m in reordered] == ["First", "Second", "Third"]
        assert [m["order"] for m in reordered] == [0, 1, 2], reordered

        proceed = client.post(f"/api/runs/{run_id}/proceed")
        assert proceed.status_code == 200, proceed.text
        assert _wait_until(
            lambda: client.get(f"/api/runs/{run_id}").json()["status"] in ("complete", "failed"))
    finally:
        _stop(patches, run_id)
    print("  ok  id:null mints a server UUID, a full PATCH deletes rows "
          "absent from the submitted list, and order is normalized to "
          "submission position regardless of client-supplied order values")


def test_duplicate_non_null_id_rejected_422():
    session_id, campaign_id = _new_session_with_video()

    run_id, patches = _run_to_brief_pause(session_id, [("Idea", "d")])
    try:
        run = client.get(f"/api/runs/{run_id}").json()
        pid = run["briefPoints"][0]["id"]

        resp = client.patch(f"/api/runs/{run_id}/brief_points", json={"messages": [
            {"id": pid, "label": "First", "description": "", "included": True, "order": 0},
            {"id": pid, "label": "Second", "description": "", "included": True, "order": 1},
        ]})
        assert resp.status_code == 422, resp.text
        assert resp.json() == {
            "error": "VALIDATION_ERROR",
            "message": f"Duplicate Key Message id: {pid}.",
            "field": "messages",
        }, resp.json()

        client.post(f"/api/runs/{run_id}/proceed")
        assert _wait_until(
            lambda: client.get(f"/api/runs/{run_id}").json()["status"] in ("complete", "failed"))
    finally:
        _stop(patches, run_id)
    print("  ok  duplicate non-null id in one PATCH -> exact unwrapped 422 "
          "VALIDATION_ERROR, field messages")


def test_unknown_id_rejected_422():
    session_id, campaign_id = _new_session_with_video()

    run_id, patches = _run_to_brief_pause(session_id, [("Idea", "d")])
    try:
        unknown_id = str(uuid.uuid4())
        resp = client.patch(f"/api/runs/{run_id}/brief_points", json={"messages": [
            {"id": unknown_id, "label": "Ghost", "description": "", "included": True, "order": 0},
        ]})
        assert resp.status_code == 422, resp.text
        assert resp.json() == {
            "error": "VALIDATION_ERROR",
            "message": f"Unknown Key Message id: {unknown_id}.",
            "field": "messages",
        }, resp.json()

        client.post(f"/api/runs/{run_id}/proceed")
        assert _wait_until(
            lambda: client.get(f"/api/runs/{run_id}").json()["status"] in ("complete", "failed"))
    finally:
        _stop(patches, run_id)
    print("  ok  unknown non-null id -> exact unwrapped 422 VALIDATION_ERROR, "
          "field messages")


def test_cross_run_id_rejected_422():
    """An id that is real and belongs to a DIFFERENT run's brief_points
    must be rejected exactly like an id that does not exist anywhere -
    existing_ids in update_brief_points is scoped to the target run_id,
    so a foreign row's id is equally "not in existing_ids"."""
    session_a, _ = _new_session_with_video()
    run_a, patches_a = _run_to_brief_pause(session_a, [("Idea A", "a")])
    try:
        run_a_point_id = client.get(f"/api/runs/{run_a}").json()["briefPoints"][0]["id"]
        client.post(f"/api/runs/{run_a}/proceed")
        assert _wait_until(
            lambda: client.get(f"/api/runs/{run_a}").json()["status"] in ("complete", "failed"))
    finally:
        _stop(patches_a, run_a)

    session_b, _ = _new_session_with_video()
    run_b, patches_b = _run_to_brief_pause(session_b, [("Idea B", "b")])
    try:
        resp = client.patch(f"/api/runs/{run_b}/brief_points", json={"messages": [
            {"id": run_a_point_id, "label": "Borrowed id", "description": "",
             "included": True, "order": 0},
        ]})
        assert resp.status_code == 422, resp.text
        assert resp.json() == {
            "error": "VALIDATION_ERROR",
            "message": f"Unknown Key Message id: {run_a_point_id}.",
            "field": "messages",
        }, resp.json()

        client.post(f"/api/runs/{run_b}/proceed")
        assert _wait_until(
            lambda: client.get(f"/api/runs/{run_b}").json()["status"] in ("complete", "failed"))
    finally:
        _stop(patches_b, run_b)
    print("  ok  an id owned by another run's brief_points is rejected "
          "with the same exact 422 VALIDATION_ERROR as an unknown id")


def test_empty_reconciled_list_allows_insert_with_server_generated_id():
    """A Session with no Key Messages and transcripts with no derivable
    points reconciles to []. The user must still be able to add an idea
    at brief_pause; the id they submit for a brand-new row must never be
    trusted, so the server mints its own."""
    session_id, campaign_id = _new_session_with_video()

    run_id, patches = _run_to_brief_pause(session_id, [])
    try:
        run = client.get(f"/api/runs/{run_id}").json()
        assert run["briefPoints"] == [], run["briefPoints"]

        resp = client.patch(f"/api/runs/{run_id}/brief_points", json={"messages": [
            {"id": None, "label": "Added at pause",
             "description": "d", "included": True, "order": 0}
        ]})
        assert resp.status_code == 200, resp.text
        saved = resp.json()["messages"]
        assert len(saved) == 1
        assert uuid.UUID(saved[0]["id"])  # server minted a real UUID
        assert saved[0]["label"] == "Added at pause"

        proceed = client.post(f"/api/runs/{run_id}/proceed")
        assert proceed.status_code == 200, proceed.text
        assert _wait_until(
            lambda: client.get(f"/api/runs/{run_id}").json()["status"] in ("complete", "failed"))
    finally:
        _stop(patches, run_id)
    print("  ok  empty reconciled list at pause accepts a new idea via a "
          "server-generated id, not the client-supplied one")


def test_proceed_still_requires_at_least_one_included():
    """PATCH itself already rejects an all-excluded submission (existing
    behaviour, unchanged), so reaching an all-excluded DB state means
    going around PATCH - exactly what a stale/partial write, or a future
    caller that bypasses this route, could produce. proceed() must catch
    that independently rather than trusting PATCH's validation alone."""
    session_id, campaign_id = _new_session_with_video()
    _seed_key_message(session_id, "Idea", "d")

    run_id, patches = _run_to_brief_pause(session_id, [("Idea", "d")])
    try:
        run = client.get(f"/api/runs/{run_id}").json()
        pid = run["briefPoints"][0]["id"]

        conn = db.get_conn()
        try:
            conn.execute("UPDATE brief_points SET included = 0 WHERE id = ?", (pid,))
            conn.commit()
        finally:
            conn.close()

        resp = client.post(f"/api/runs/{run_id}/proceed")
        assert resp.status_code == 422, resp.text
        assert resp.json() == {
            "error": "VALIDATION_ERROR",
            "message": "Include at least one Key Message before continuing.",
            "field": "messages",
        }, resp.json()

        # Fix it and let the run finish so no daemon thread outlives the mocks.
        conn = db.get_conn()
        try:
            conn.execute("UPDATE brief_points SET included = 1 WHERE id = ?", (pid,))
            conn.commit()
        finally:
            conn.close()
        client.post(f"/api/runs/{run_id}/proceed")
        assert _wait_until(
            lambda: client.get(f"/api/runs/{run_id}").json()["status"] in ("complete", "failed"))
    finally:
        _stop(patches, run_id)
    print("  ok  proceed rejects an all-excluded brief point list with the "
          "exact 422 VALIDATION_ERROR body, even when PATCH's own "
          "validation was bypassed")


if __name__ == "__main__":
    tests = [
        test_second_concurrent_run_rejected_with_409_before_delete,
        test_snapshot_and_reconcile_replace_brief_points_before_pause,
        test_session_key_messages_never_mutated_by_a_run,
        test_patch_and_proceed_gated_on_persisted_brief_pause_stage,
        test_id_null_creates_uuid_and_full_ordered_replace,
        test_duplicate_non_null_id_rejected_422,
        test_unknown_id_rejected_422,
        test_cross_run_id_rejected_422,
        test_empty_reconciled_list_allows_insert_with_server_generated_id,
        test_proceed_still_requires_at_least_one_included,
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

    db._DB_PATH = _ORIG_DB_PATH
    storage._ROOT = _ORIG_STORAGE_ROOT

    if failed:
        print(f"\nFAIL ({failed}/{len(tests)} failed)")
        sys.exit(1)
    print(f"\nPASS ({len(tests)}/{len(tests)})")
