"""
Offline self-check for Wave 2 run integration: Session Key Message
snapshot -> transcript reconciliation -> run brief_points, the persisted
run stage, and the associated 409/PATCH/proceed gating in server.py.

Points db._DB_PATH at a temp file before importing server (server.app's
startup hook calls db.init() against whatever path db._DB_PATH holds at
that time), then drives the routes through FastAPI's TestClient.
adapter._execute()'s pipeline edges (collect, brief.reconcile, analyze,
report) are mocked - no network, no Ollama, no real PDF render - but the
run still executes on adapter.start_run()'s real daemon thread, so the
brief_pause block and DB writes are exercised for real.

Run: python tests/test_run_key_messages.py
"""

import os
import pathlib
import sys
import tempfile
import time
import uuid
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
db._DB_PATH = pathlib.Path(tempfile.mkdtemp()) / "app.db"

from starlette.testclient import TestClient
import server
import adapter

db.init()  # server's startup hook only fires inside TestClient's `with` block
client = TestClient(server.app)


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
         "likes": 3, "reply_count": 0, "in_base": True},
    ])


def _fake_meta_df():
    return pd.DataFrame([
        {"video_id": "abcdefghijk", "group": "C", "kind": "auto",
         "title": "t", "channel": "ch", "description": "d",
         "transcript": "We cut the price.", "has_transcript": True},
    ])


def _patched_pipeline(reconcile_side_effect):
    """Context managers covering every pipeline edge _execute() touches
    after the brief_pause point we care about, so the run reaches
    brief_pause deterministically without network/model/PDF calls."""
    return (
        patch.object(adapter.pipeline_llm, "preflight", return_value=None),
        patch.object(adapter.collect, "fetch",
                    return_value=(_fake_comments_df(), _fake_meta_df())),
        patch.object(adapter.collect, "clean", side_effect=lambda df, cfg: df),
        patch.object(adapter.brief, "reconcile", side_effect=reconcile_side_effect),
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


def test_second_concurrent_run_rejected_with_409_before_delete():
    session_id, campaign_id = _new_session_with_video()
    _seed_key_message(session_id, "Lower price", "d")

    patches = _patched_pipeline(_run_reconcile_of([("Lower price", "d")]))
    with patches[0], patches[1], patches[2], patches[3]:
        r1 = client.post(f"/api/sessions/{session_id}/runs")
        assert r1.status_code == 202, r1.text
        first_run_id = r1.json()["id"]

        r2 = client.post(f"/api/sessions/{session_id}/runs")
        assert r2.status_code == 409, r2.text
        assert r2.json()["detail"]["error"] == "conflict"

        # The first run's row must still exist - rejecting the second
        # request must not have deleted anything.
        conn = db.get_conn()
        try:
            still_there = conn.execute(
                "SELECT id FROM runs WHERE id = ?", (first_run_id,)
            ).fetchone()
        finally:
            conn.close()
        assert still_there is not None, "409 path deleted the in-flight run"

        _wait_until(lambda: client.get(f"/api/runs/{first_run_id}").json()["stage"] == "brief_pause")
        client.post(f"/api/runs/{first_run_id}/proceed")
    print("  ok  second queued/running run rejected 409, first run's row untouched")


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
    with patches[0], patches[1], patches[2], patches[3]:
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
        assert all(p["videoId"] is None for p in run["briefPoints"])

        client.post(f"/api/runs/{run_id}/proceed")
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

    patches = _patched_pipeline(_run_reconcile_of([("Transcript idea", "d")]))
    with patches[0], patches[1], patches[2], patches[3]:
        run_id = client.post(f"/api/sessions/{session_id}/runs").json()["id"]
        assert _wait_until(
            lambda: client.get(f"/api/runs/{run_id}").json()["stage"] == "brief_pause")
        client.post(f"/api/runs/{run_id}/proceed")
        assert _wait_until(
            lambda: client.get(f"/api/runs/{run_id}").json()["status"] in ("complete", "failed"),
            timeout=5.0)

    after = before_snapshot()
    assert after == before, "Session key_messages were mutated by a run"
    print("  ok  Session key_messages table is untouched by a run, start to finish")


def test_patch_and_proceed_gated_on_persisted_brief_pause_stage():
    session_id, campaign_id = _new_session_with_video()
    _seed_key_message(session_id, "Idea", "d")

    # Before the run reaches brief_pause, PATCH/proceed must 409 even
    # though brief_points already exist (the pre-collect snapshot).
    patches = _patched_pipeline(_run_reconcile_of([("Idea", "d")]))
    with patches[0], patches[1], patches[2], patches[3]:
        run_id = client.post(f"/api/sessions/{session_id}/runs").json()["id"]

        r = client.get(f"/api/runs/{run_id}")
        pre_pause_points = r.json().get("briefPointIds", [])
        if pre_pause_points and r.json()["stage"] != "brief_pause":
            payload = {"points": [
                {"id": pre_pause_points[0], "label": "x", "description": "",
                 "included": True, "order": 0}
            ]}
            patch_resp = client.patch(f"/api/runs/{run_id}/brief_points", json=payload)
            assert patch_resp.status_code == 409, patch_resp.text
            proceed_resp = client.post(f"/api/runs/{run_id}/proceed")
            assert proceed_resp.status_code == 409, proceed_resp.text

        assert _wait_until(
            lambda: client.get(f"/api/runs/{run_id}").json()["stage"] == "brief_pause")

        # Now it's open.
        run = client.get(f"/api/runs/{run_id}").json()
        pid = run["briefPoints"][0]["id"]
        ok = client.patch(f"/api/runs/{run_id}/brief_points", json={"points": [
            {"id": pid, "label": "Edited", "description": "d2", "included": True, "order": 0}
        ]})
        assert ok.status_code == 200, ok.text

        client.post(f"/api/runs/{run_id}/proceed").raise_for_status()

        # After proceed, the review window is closed again.
        late = client.patch(f"/api/runs/{run_id}/brief_points", json={"points": [
            {"id": pid, "label": "Too late", "description": "", "included": True, "order": 0}
        ]})
        assert late.status_code == 409, late.text
    print("  ok  PATCH/proceed gated on the persisted brief_pause stage, "
          "not on brief_points existing")


def test_empty_reconciled_list_allows_insert_with_server_generated_id():
    """A Session with no Key Messages and transcripts with no derivable
    points reconciles to []. The user must still be able to add an idea
    at brief_pause; the id they submit for a brand-new row must never be
    trusted, so the server mints its own."""
    session_id, campaign_id = _new_session_with_video()

    patches = _patched_pipeline(_run_reconcile_of([]))
    with patches[0], patches[1], patches[2], patches[3]:
        run_id = client.post(f"/api/sessions/{session_id}/runs").json()["id"]
        assert _wait_until(
            lambda: client.get(f"/api/runs/{run_id}").json()["stage"] == "brief_pause")

        run = client.get(f"/api/runs/{run_id}").json()
        assert run.get("briefPoints", []) == [], run.get("briefPoints")

        client_supplied_id = "not-a-real-id"
        resp = client.patch(f"/api/runs/{run_id}/brief_points", json={"points": [
            {"id": client_supplied_id, "label": "Added at pause",
             "description": "d", "included": True, "order": 0}
        ]})
        assert resp.status_code == 200, resp.text
        saved = resp.json()
        assert len(saved) == 1
        assert saved[0]["id"] != client_supplied_id, (
            "server trusted a client-supplied id for a brand-new row")
        assert saved[0]["label"] == "Added at pause"

        proceed = client.post(f"/api/runs/{run_id}/proceed")
        assert proceed.status_code == 200, proceed.text
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

    patches = _patched_pipeline(_run_reconcile_of([("Idea", "d")]))
    with patches[0], patches[1], patches[2], patches[3]:
        run_id = client.post(f"/api/sessions/{session_id}/runs").json()["id"]
        assert _wait_until(
            lambda: client.get(f"/api/runs/{run_id}").json()["stage"] == "brief_pause")

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
        assert resp.json()["detail"]["error"] == "validation"
    print("  ok  proceed rejects an all-excluded brief point list even when "
          "PATCH's own validation was bypassed")


if __name__ == "__main__":
    tests = [
        test_second_concurrent_run_rejected_with_409_before_delete,
        test_snapshot_and_reconcile_replace_brief_points_before_pause,
        test_session_key_messages_never_mutated_by_a_run,
        test_patch_and_proceed_gated_on_persisted_brief_pause_stage,
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

    if failed:
        print(f"\nFAIL ({failed}/{len(tests)} failed)")
        sys.exit(1)
    print(f"\nPASS ({len(tests)}/{len(tests)})")
