"""
Offline self-check for PATCH /api/sessions/{id}/key_messages in server.py.

Points db._DB_PATH at a temp file before importing server (server.app's
startup hook calls db.init() against whatever path db._DB_PATH holds at
that time), then drives the route through FastAPI's TestClient. No
network, no pipeline, no adapter thread.

Run: python tests/test_key_messages_patch.py
"""

import os
import pathlib
import sys
import tempfile
import uuid

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


def _assert_validation_error(resp):
    """Shared contract: unwrapped {error,message,field}, error uppercase VALIDATION_ERROR."""
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert set(body.keys()) == {"error", "message", "field"}, body
    assert body["error"] == "VALIDATION_ERROR", body
    assert body["field"] == "messages", body
    return body


def _new_session():
    r = client.post("/api/sessions", json={"name": "Test Session"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _seed_messages(session_id, n=2):
    """Insert key_messages rows directly, as a prior draft would have."""
    conn = db.get_conn()
    try:
        ids = []
        for i in range(n):
            mid = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO key_messages (id, session_id, label, description, "
                "included, sort_order, edited) VALUES (?,?,?,?,?,?,0)",
                (mid, session_id, f"Label {i}", f"Desc {i}", 1, i),
            )
            ids.append(mid)
        conn.commit()
    finally:
        conn.close()
    return ids


def test_patch_shape_and_new_session_field():
    """A fresh Session's keyMessages field matches the KeyMessageDraft shape."""
    session_id = _new_session()
    r = client.get(f"/api/sessions/{session_id}")
    assert r.status_code == 200, r.text
    km = r.json()["keyMessages"]
    assert set(km.keys()) == _DRAFT_KEYS, km.keys()
    assert km["status"] == "empty", km["status"]
    assert km["messages"] == []
    assert km["error"] is None
    assert km["revision"] == 0, km["revision"]
    assert isinstance(km["revision"], int), type(km["revision"])
    print("  ok  fresh Session keyMessages is an empty KeyMessageDraft with revision 0")


def test_patch_accepts_empty_list():
    session_id = _new_session()
    r = client.patch(f"/api/sessions/{session_id}/key_messages", json={"messages": []})
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body.keys()) == _DRAFT_KEYS, body.keys()
    assert body["messages"] == []
    print("  ok  PATCH accepts an empty message list and returns KeyMessageDraft shape")


def test_patch_returns_current_revision_without_incrementing():
    session_id = _new_session()
    r0 = client.get(f"/api/sessions/{session_id}")
    revision_before = r0.json()["keyMessages"]["revision"]

    r = client.patch(f"/api/sessions/{session_id}/key_messages", json={"messages": []})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["revision"] == revision_before, (
        "PATCH must not increment key_messages_revision"
    )

    r2 = client.get(f"/api/sessions/{session_id}")
    assert r2.json()["keyMessages"]["revision"] == revision_before
    print("  ok  PATCH returns the current revision without incrementing it")


def test_patch_replaces_atomically_preserving_order_and_zero_included():
    session_id = _new_session()
    ids = _seed_messages(session_id, n=2)

    payload = {
        "messages": [
            {"id": ids[1], "label": "Second", "description": "d2", "included": False, "order": 0},
            {"id": ids[0], "label": "First", "description": "d1", "included": False, "order": 1},
        ]
    }
    r = client.patch(f"/api/sessions/{session_id}/key_messages", json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body.keys()) == _DRAFT_KEYS, body.keys()
    msgs = body["messages"]
    assert len(msgs) == 2, msgs
    assert [m["id"] for m in msgs] == [ids[1], ids[0]], "submitted order not preserved"
    assert all(m["included"] is False for m in msgs), "zero-included list rejected"
    assert set(msgs[0].keys()) == _MESSAGE_KEYS, msgs[0].keys()
    print("  ok  PATCH replaces atomically, preserves submitted order, allows zero included")


def test_patch_rejects_duplicate_ids():
    session_id = _new_session()
    ids = _seed_messages(session_id, n=1)
    payload = {
        "messages": [
            {"id": ids[0], "label": "A", "description": "", "included": True, "order": 0},
            {"id": ids[0], "label": "B", "description": "", "included": True, "order": 1},
        ]
    }
    r = client.patch(f"/api/sessions/{session_id}/key_messages", json=payload)
    _assert_validation_error(r)
    print("  ok  PATCH rejects duplicate ids with 422 VALIDATION_ERROR/messages")


def test_patch_rejects_unknown_id_from_another_session():
    session_a = _new_session()
    session_b = _new_session()
    foreign_ids = _seed_messages(session_b, n=1)

    payload = {"messages": [
        {"id": foreign_ids[0], "label": "X", "description": "", "included": True, "order": 0}
    ]}
    r = client.patch(f"/api/sessions/{session_a}/key_messages", json=payload)
    _assert_validation_error(r)
    print("  ok  PATCH rejects an id belonging to a different session")


def test_patch_id_null_generates_uuid():
    """id:null creates a server-generated UUID string; returned row keeps submitted order."""
    session_id = _new_session()
    payload = {"messages": [
        {"id": None, "label": "New", "description": "d", "included": True, "order": 0},
    ]}
    r = client.patch(f"/api/sessions/{session_id}/key_messages", json=payload)
    assert r.status_code == 200, r.text
    msgs = r.json()["messages"]
    assert len(msgs) == 1, msgs
    uuid.UUID(msgs[0]["id"])  # raises ValueError if not a valid UUID string
    assert msgs[0]["label"] == "New", msgs[0]
    assert msgs[0]["order"] == 0, msgs[0]
    print("  ok  PATCH id:null generates a server UUID and returns ordered row")


def test_patch_rejects_label_and_description_over_max_length():
    session_id = _new_session()
    over_label = {"id": None, "label": "x" * 121, "description": "", "included": True, "order": 0}
    r = client.patch(f"/api/sessions/{session_id}/key_messages", json={"messages": [over_label]})
    _assert_validation_error(r)

    over_description = {"id": None, "label": "ok", "description": "x" * 501, "included": True, "order": 0}
    r = client.patch(f"/api/sessions/{session_id}/key_messages", json={"messages": [over_description]})
    _assert_validation_error(r)
    print("  ok  PATCH rejects label >120 and description >500 chars")


def test_patch_unknown_session_404():
    r = client.patch("/api/sessions/does-not-exist/key_messages", json={"messages": []})
    assert r.status_code == 404, r.text
    print("  ok  PATCH on an unknown session returns 404")


if __name__ == "__main__":
    tests = [
        test_patch_shape_and_new_session_field,
        test_patch_accepts_empty_list,
        test_patch_returns_current_revision_without_incrementing,
        test_patch_replaces_atomically_preserving_order_and_zero_included,
        test_patch_rejects_duplicate_ids,
        test_patch_rejects_unknown_id_from_another_session,
        test_patch_id_null_generates_uuid,
        test_patch_rejects_label_and_description_over_max_length,
        test_patch_unknown_session_404,
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
