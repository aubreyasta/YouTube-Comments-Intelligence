"""
Offline self-check for POST /api/sessions/{id}/key_messages/draft in server.py.

Points db._DB_PATH at a temp file before importing server (server.app's
startup hook calls db.init() against whatever path db._DB_PATH holds at
that time), then drives the route through FastAPI's TestClient.
pipeline.brief.draft_from_inputs is mocked - no network, no Ollama, no
adapter thread.

Run: python tests/test_key_messages_draft.py
"""

import os
import pathlib
import sys
import tempfile
import threading
import time
import uuid
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
db._DB_PATH = pathlib.Path(tempfile.mkdtemp()) / "app.db"

from starlette.testclient import TestClient

# server refuses to start without APP_PASSWORD, and its Basic Auth
# middleware guards every route. Set before import so the startup hook sees it.
os.environ.setdefault("APP_PASSWORD", "test-password")

import server

db.init()  # server's startup hook only fires inside TestClient's `with` block
client = TestClient(server.app, headers={"Authorization": "Basic b2ZmaWNlOnRlc3QtcGFzc3dvcmQ="})

_DRAFT_KEYS = {"status", "messages", "error", "revision"}
_MESSAGE_KEYS = {"id", "label", "description", "included", "order"}


def _new_session():
    r = client.post("/api/sessions", json={"name": "Test Session"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _seed_message(session_id, label, description="", included=True, order=0, edited=0):
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


def _revision(session_id):
    conn = db.get_conn()
    try:
        return conn.execute(
            "SELECT key_messages_revision FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()["key_messages_revision"]
    finally:
        conn.close()


def _draft_of(proposals):
    """A drop-in for pipeline.brief.draft_from_inputs that ignores its
    (text, images, cfg) arguments and returns fixed proposals."""
    def fn(text, images, cfg):
        return [
            {"label": lbl, "description": desc, "included": True,
             "order": i, "edited": False}
            for i, (lbl, desc) in enumerate(proposals)
        ]
    return fn


def test_draft_with_no_inputs_and_no_prior_messages_is_empty_without_model_call():
    """Uses the real pipeline.brief.draft_from_inputs (unmocked) and
    only stubs the model boundary (llm.ask_json), to prove the "no model
    call" guarantee end-to-end rather than against a test double."""
    session_id = _new_session()

    def fail(*args, **kwargs):
        raise AssertionError("the model must not be called with no source and no prior state")

    with patch.object(server.pipeline_brief.llm, "ask_json", side_effect=fail):
        r = client.post(f"/api/sessions/{session_id}/key_messages/draft")

    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body.keys()) == _DRAFT_KEYS, body.keys()
    assert body["status"] == "empty", body
    assert body["messages"] == []
    assert body["error"] is None
    assert body["revision"] == 1, body["revision"]
    assert isinstance(body["revision"], int), type(body["revision"])
    print("  ok  no campaign/no inputs and no prior messages -> empty, no model call, revision 1")


def test_draft_success_returns_ready_with_proposals():
    session_id = _new_session()
    client.post(f"/api/sessions/{session_id}/campaigns", json={"name": "Campaign"})

    with patch.object(server.pipeline_brief, "draft_from_inputs",
                      side_effect=_draft_of([("Lower price", "Cheaper than before.")])):
        r = client.post(f"/api/sessions/{session_id}/key_messages/draft")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ready", body
    assert body["error"] is None
    assert len(body["messages"]) == 1
    msg = body["messages"][0]
    assert set(msg.keys()) == _MESSAGE_KEYS, msg.keys()
    assert msg["label"] == "Lower price"
    assert msg["description"] == "Cheaper than before."
    assert msg["included"] is True
    assert body["revision"] == 1, body["revision"]
    assert isinstance(body["revision"], int), type(body["revision"])
    print("  ok  successful draft with proposals -> ready with messages, revision 1")


def test_draft_preserves_edited_message_even_when_absent_from_proposals():
    session_id = _new_session()
    edited_id = _seed_message(session_id, "Durability", "User's own wording",
                              included=False, order=3, edited=1)

    with patch.object(server.pipeline_brief, "draft_from_inputs",
                      side_effect=_draft_of([("Lower price", "Cheaper than before.")])):
        r = client.post(f"/api/sessions/{session_id}/key_messages/draft")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ready", body
    by_label = {m["label"]: m for m in body["messages"]}
    assert "Durability" in by_label, "edited message dropped despite absence from proposals"
    survivor = by_label["Durability"]
    assert survivor["id"] == edited_id, "edited message lost its stable id"
    assert survivor["description"] == "User's own wording", "edited description was overwritten"
    assert survivor["included"] is False, "manual included value not preserved"
    assert survivor["order"] == 3, "manual order value not preserved"
    assert "Lower price" in by_label, "new proposal not appended"
    print("  ok  edited message survives verbatim (id, description, included, order) "
          "even when absent from proposals")


def test_draft_replaces_unedited_message_absent_from_proposals():
    session_id = _new_session()
    _seed_message(session_id, "Old stale idea", "stale text", included=True, order=0, edited=0)

    with patch.object(server.pipeline_brief, "draft_from_inputs",
                      side_effect=_draft_of([("Fresh idea", "fresh text")])):
        r = client.post(f"/api/sessions/{session_id}/key_messages/draft")

    assert r.status_code == 200, r.text
    body = r.json()
    labels = [m["label"] for m in body["messages"]]
    assert "Old stale idea" not in labels, "unedited message not replaced by fresh draft"
    assert labels == ["Fresh idea"], labels
    print("  ok  unedited message absent from proposals is dropped, not kept")


def test_draft_matches_unedited_message_case_insensitively_and_keeps_manual_fields():
    session_id = _new_session()
    stable_id = _seed_message(session_id, "  Lower Price  ", "old description",
                              included=False, order=7, edited=0)

    with patch.object(server.pipeline_brief, "draft_from_inputs",
                      side_effect=_draft_of([("lower price", "new grounded description")])):
        r = client.post(f"/api/sessions/{session_id}/key_messages/draft")

    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["messages"]) == 1, body["messages"]
    msg = body["messages"][0]
    assert msg["id"] == stable_id, "case-insensitive label match did not keep the stable id"
    assert msg["description"] == "new grounded description"
    assert msg["included"] is False, "manual included value not preserved on match"
    assert msg["order"] == 7, "manual order value not preserved on match"
    print("  ok  case-insensitive label match preserves stable id + manual "
          "included/order, refreshes description")


def test_draft_failure_keeps_prior_messages_and_marks_stale():
    session_id = _new_session()
    kept_id = _seed_message(session_id, "Kept idea", "kept description",
                            included=True, order=0, edited=0)

    def boom(*args, **kwargs):
        raise RuntimeError("Ollama unreachable")

    with patch.object(server.pipeline_brief, "draft_from_inputs", side_effect=boom):
        r = client.post(f"/api/sessions/{session_id}/key_messages/draft")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "stale", body
    assert body["error"] is not None and "Ollama unreachable" in body["error"]
    assert len(body["messages"]) == 1
    assert body["messages"][0]["id"] == kept_id
    assert body["messages"][0]["description"] == "kept description"
    assert body["revision"] == 1, body["revision"]
    print("  ok  model failure with prior messages -> stale, prior messages unchanged, revision 1")


def test_draft_failure_with_no_prior_messages_marks_failed():
    session_id = _new_session()
    client.post(f"/api/sessions/{session_id}/campaigns", json={"name": "Campaign"})

    def boom(*args, **kwargs):
        raise RuntimeError("Ollama unreachable")

    with patch.object(server.pipeline_brief, "draft_from_inputs", side_effect=boom):
        r = client.post(f"/api/sessions/{session_id}/key_messages/draft")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "failed", body
    assert body["error"] is not None
    assert body["messages"] == []
    assert body["revision"] == 1, body["revision"]
    print("  ok  model failure with no prior messages -> failed, empty list, safe error string, revision 1")


def test_draft_unknown_session_404():
    r = client.post("/api/sessions/does-not-exist/key_messages/draft")
    assert r.status_code == 404, r.text
    print("  ok  draft on an unknown session returns 404")


def test_concurrent_requests_coalesce_to_at_most_two_model_calls():
    """3 concurrent requests against one session: the first acquires the
    draft lock and runs; the other two coalesce into exactly one rerun.
    At most 2 model calls total, and every response reflects the final
    (second) call's proposals."""
    session_id = _new_session()

    call_count = {"n": 0}
    call_lock = threading.Lock()
    release_first_call = threading.Event()
    first_call_started = threading.Event()

    def slow_draft(text, images, cfg):
        with call_lock:
            call_count["n"] += 1
            n = call_count["n"]
        if n == 1:
            first_call_started.set()
            release_first_call.wait(timeout=5)
            return _draft_of([("First idea", "from call 1")])(text, images, cfg)
        return _draft_of([("Second idea", "from call 2")])(text, images, cfg)

    results = [None, None, None]

    def make_request(i):
        r = client.post(f"/api/sessions/{session_id}/key_messages/draft")
        results[i] = r

    with patch.object(server.pipeline_brief, "draft_from_inputs", side_effect=slow_draft):
        t0 = threading.Thread(target=make_request, args=(0,))
        t0.start()
        assert first_call_started.wait(timeout=5), "first call never started"

        # Two more requests arrive while the first call is in flight.
        t1 = threading.Thread(target=make_request, args=(1,))
        t2 = threading.Thread(target=make_request, args=(2,))
        t1.start()
        time.sleep(0.1)  # let t1 register its rerun request before t2
        t2.start()
        time.sleep(0.1)

        release_first_call.set()
        t0.join(timeout=5)
        t1.join(timeout=5)
        t2.join(timeout=5)

    assert call_count["n"] == 2, f"expected at most 2 model calls, got {call_count['n']}"
    for i, r in enumerate(results):
        assert r is not None, f"request {i} did not complete"
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "ready", body
        labels = [m["label"] for m in body["messages"]]
        assert labels == ["Second idea"], (
            f"request {i} did not see the final coalesced result: {labels}")
        assert body["revision"] == 2, body["revision"]
        assert isinstance(body["revision"], int), type(body["revision"])
    print("  ok  3 concurrent requests -> exactly 2 model calls, all responses "
          "reflect the latest coalesced result and final revision 2")


if __name__ == "__main__":
    tests = [
        test_draft_with_no_inputs_and_no_prior_messages_is_empty_without_model_call,
        test_draft_success_returns_ready_with_proposals,
        test_draft_preserves_edited_message_even_when_absent_from_proposals,
        test_draft_replaces_unedited_message_absent_from_proposals,
        test_draft_matches_unedited_message_case_insensitively_and_keeps_manual_fields,
        test_draft_failure_keeps_prior_messages_and_marks_stale,
        test_draft_failure_with_no_prior_messages_marks_failed,
        test_draft_unknown_session_404,
        test_concurrent_requests_coalesce_to_at_most_two_model_calls,
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
