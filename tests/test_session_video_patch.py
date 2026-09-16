"""
Offline self-check for PATCH /api/videos/{id} (change kind) and
PATCH /api/sessions/{id} (rename) in server.py.

Points db._DB_PATH at a temp file before importing server, then drives
the routes through FastAPI's TestClient. No network, no pipeline, no
adapter thread.

Run: python tests/test_session_video_patch.py
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


def _new_session_with_video():
    sid = client.post("/api/sessions", json={"name": "Original"}).json()["id"]
    cid = client.post(f"/api/sessions/{sid}/campaigns", json={"name": "Original"}).json()["id"]
    video = client.post(f"/api/campaigns/{cid}/videos",
                        json={"url": "https://youtu.be/abcdefghijk"}).json()
    return sid, cid, video


def _stale_updated_at(session_id):
    conn = db.get_conn()
    try:
        conn.execute("UPDATE sessions SET updated_at = 'stale' WHERE id = ?", (session_id,))
        conn.commit()
    finally:
        conn.close()


def _updated_at(session_id):
    conn = db.get_conn()
    try:
        return conn.execute(
            "SELECT updated_at FROM sessions WHERE id = ?", (session_id,)).fetchone()[0]
    finally:
        conn.close()


def _assert_error(resp, status, error, field):
    assert resp.status_code == status, resp.text
    body = resp.json()
    assert set(body.keys()) == {"error", "message", "field"}, body
    assert body["error"] == error, body
    assert body["field"] == field, body


def test_patch_video_kind():
    sid, cid, video = _new_session_with_video()
    assert video["kind"] == "auto"
    _stale_updated_at(sid)

    resp = client.patch(f"/api/videos/{video['id']}", json={"kind": "review"})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {**video, "kind": "review"}, resp.json()
    assert _updated_at(sid) != "stale", "session updated_at not touched"

    listed = client.get(f"/api/sessions/{sid}").json()["campaigns"][0]["videos"]
    assert listed[0]["kind"] == "review", listed
    print("  ok  PATCH /videos/{id} changes kind, returns the video, touches "
          "session updated_at")


def test_patch_video_kind_rejects_unknown_kind_and_id():
    sid, cid, video = _new_session_with_video()

    bad = client.patch(f"/api/videos/{video['id']}", json={"kind": "podcast"})
    _assert_error(bad, 422, "VALIDATION_ERROR", "kind")
    assert bad.json()["message"] == "kind must be one of: auto, brand_ad, explainer, review."

    missing = client.patch(f"/api/videos/{uuid.uuid4()}", json={"kind": "review"})
    _assert_error(missing, 404, "NOT_FOUND", None)
    print("  ok  PATCH /videos/{id} unknown kind -> 422 field kind; unknown id -> 404")


def test_patch_session_rename():
    sid, cid, video = _new_session_with_video()
    _stale_updated_at(sid)

    resp = client.patch(f"/api/sessions/{sid}", json={"name": "  Renamed  "})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "Renamed", body["name"]
    assert body["campaigns"][0]["name"] == "Renamed", body["campaigns"]
    assert body == client.get(f"/api/sessions/{sid}").json(), \
        "rename response differs from GET /sessions/{id}"
    assert _updated_at(sid) != "stale", "session updated_at not touched"
    print("  ok  PATCH /sessions/{id} renames the Session and its campaign, "
          "returns the GET /sessions/{id} shape, touches updated_at")


def test_patch_session_rename_rejects_empty_name_and_unknown_id():
    sid, cid, video = _new_session_with_video()

    for name in ("", "   "):
        resp = client.patch(f"/api/sessions/{sid}", json={"name": name})
        _assert_error(resp, 422, "VALIDATION_ERROR", "name")
        assert resp.json()["message"] == "Session name is required.", resp.json()

    missing_field = client.patch(f"/api/sessions/{sid}", json={})
    _assert_error(missing_field, 422, "VALIDATION_ERROR", "name")

    assert client.get(f"/api/sessions/{sid}").json()["name"] == "Original"

    missing = client.patch(f"/api/sessions/{uuid.uuid4()}", json={"name": "X"})
    _assert_error(missing, 404, "NOT_FOUND", None)
    print("  ok  PATCH /sessions/{id} empty/blank/missing name -> 422 field name, "
          "name unchanged; unknown id -> 404")


if __name__ == "__main__":
    tests = [
        test_patch_video_kind,
        test_patch_video_kind_rejects_unknown_kind_and_id,
        test_patch_session_rename,
        test_patch_session_rename_rejects_empty_name_and_unknown_id,
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
