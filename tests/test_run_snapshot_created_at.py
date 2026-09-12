"""
Offline self-check for issue #7: RunSnapshot must carry `createdAt`.

The results page kicker formats `new Date(run.createdAt)` (app/app.js
"STRATEGY NOTE"). The snapshot used to expose neither started_at nor
finished_at, so every live run rendered "STRATEGY NOTE · INVALID DATE".

Points db._DB_PATH and storage._ROOT at temp locations before importing
server (server.app's startup hook calls db.init() against whatever path
db._DB_PATH holds at that time), then drives the real routes through
FastAPI's TestClient. Both globals are restored on exit so this file
never leaves anything under the repo's real data/ tree.

adapter.start_run() (the real pipeline thread) is mocked to a no-op, so
POST /runs exercises the insert path without touching the network, a
model, or the GPU.

Run: python tests/test_run_snapshot_created_at.py
"""

import base64
import os
import pathlib
import sys
import tempfile
import uuid
from datetime import datetime
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
_ORIG_DB_PATH = db._DB_PATH
db._DB_PATH = pathlib.Path(tempfile.mkdtemp()) / "app.db"

import storage
_ORIG_STORAGE_ROOT = storage._ROOT
storage._ROOT = tempfile.mkdtemp()

from starlette.testclient import TestClient

# server refuses to start without APP_PASSWORD, and its Basic Auth
# middleware guards every route. Assigned, not setdefault: a developer
# machine with its own APP_PASSWORD exported would otherwise 401 every
# request here. server.py's load_dotenv() does not override an existing
# variable, so this value survives the import.
_TEST_PASSWORD = "test-password"
os.environ["APP_PASSWORD"] = _TEST_PASSWORD

import server
import adapter

db.init()  # server's startup hook only fires inside TestClient's `with` block
_AUTH = "Basic " + base64.b64encode(f"office:{_TEST_PASSWORD}".encode()).decode()
client = TestClient(server.app, headers={"Authorization": _AUTH})


def _new_session():
    return client.post("/api/sessions", json={"name": "S"}).json()["id"]


def _seed_run(sid, state, started_at):
    conn = db.get_conn()
    try:
        rid = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO runs (id, session_id, state, stage, started_at) VALUES (?,?,?,?,?)",
            (rid, sid, state, "complete" if state == "complete" else "queued", started_at),
        )
        conn.commit()
    finally:
        conn.close()
    return rid


def test_started_run_snapshot_carries_created_at():
    """POST /runs returns a createdAt a JS Date can parse."""
    sid = _new_session()
    with patch.object(adapter, "start_run", return_value=None):
        snap = client.post(f"/api/sessions/{sid}/runs").json()
    assert "createdAt" in snap, "POST /runs snapshot has no createdAt"
    # The exact failure mode of issue #7: new Date(undefined) is Invalid Date.
    assert snap["createdAt"], f"createdAt is falsy: {snap['createdAt']!r}"
    # ISO 8601 with a timezone, per docs/api-reference.md "Conventions".
    parsed = datetime.fromisoformat(snap["createdAt"])
    assert parsed.tzinfo is not None, f"createdAt has no timezone: {snap['createdAt']!r}"


def test_get_run_created_at_matches_started_at():
    """GET /runs/{id} reports the run's started_at, unchanged."""
    sid = _new_session()
    started = "2026-09-11T08:30:00+00:00"
    rid = _seed_run(sid, "complete", started)
    snap = client.get(f"/api/runs/{rid}").json()
    assert snap["createdAt"] == started, f"{snap['createdAt']!r} != {started!r}"


def test_created_at_present_in_every_run_state():
    """A failed or queued run dates the same way a complete one does."""
    sid = _new_session()
    for state in ("queued", "running", "complete", "failed"):
        rid = _seed_run(sid, state, "2026-09-11T08:30:00+00:00")
        snap = client.get(f"/api/runs/{rid}").json()
        assert snap.get("createdAt"), f"{state} run has no createdAt: {snap.get('createdAt')!r}"


def main() -> None:
    tests = [
        test_started_run_snapshot_carries_created_at,
        test_get_run_created_at_matches_started_at,
        test_created_at_present_in_every_run_state,
    ]
    failed = 0
    try:
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
        db._DB_PATH = _ORIG_DB_PATH
        storage._ROOT = _ORIG_STORAGE_ROOT

    if failed:
        print(f"\nFAIL ({failed}/{len(tests)} failed)")
        sys.exit(1)
    print(f"\nPASS ({len(tests)}/{len(tests)})")


if __name__ == "__main__":
    main()
