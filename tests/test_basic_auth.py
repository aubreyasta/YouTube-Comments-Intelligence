"""
Offline self-check for the fail-closed HTTP Basic Auth boundary in server.py.

Covers: startup refuses to start without APP_PASSWORD; every route (static,
API, artifact download, SSE) returns 401 with the exact challenge header and
body when unauthenticated or misauthenticated; a non-ASCII password produces
401 not 500; valid credentials reach routing including a streaming SSE
response; no submitted credential text ever appears in a response body.

No live network request, no model load, no password written to disk.

Run: python tests/test_basic_auth.py
"""

import base64
import os
import pathlib
import sys
import tempfile
import threading
import uuid
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
db._DB_PATH = pathlib.Path(tempfile.mkdtemp()) / "app.db"

import storage
storage._ROOT = tempfile.mkdtemp()

from starlette.testclient import TestClient

os.environ.setdefault("APP_PASSWORD", "test-password")

import server
import adapter

db.init()

auth_client = TestClient(server.app, headers={"Authorization": "Basic b2ZmaWNlOnRlc3QtcGFzc3dvcmQ="})
anon_client = TestClient(server.app)

_CHALLENGE = 'Basic realm="YouTube Intelligence", charset="UTF-8"'
_STATIC_ASSET = "/app.js"


def _seed_run(session_id, state):
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


def _bounded_sse_status(open_stream, run_id, teardown_delay=0.8, timeout=5.0):
    """Open an SSE stream and return (status_code, headers), bounded in
    wall-clock time no matter what the stream does.

    server.run_events._generate() never returns for a non-terminal run - it
    loops forever waiting on the queue - so a test must supply the terminal
    condition itself. This arms a threading.Timer that, if the stream is
    still open after `teardown_delay`, flips adapter._terminal for run_id AND
    pushes a "complete" item onto its queue (setting the flag alone would not
    wake a generator parked in the queue wait). The whole read runs in a
    worker thread with a hard join timeout, so a stream that still fails to
    end is a test failure, never a hang.
    """
    holder = {"status": None, "headers": None, "exc": None}

    def _force_terminal():
        adapter._terminal[run_id] = True
        adapter.get_queue(run_id).put(
            {"run_id": run_id, "stage": "complete", "pct": 100, "message": "test teardown", "detail": None}
        )

    timer = threading.Timer(teardown_delay, _force_terminal)
    timer.start()

    def _worker():
        try:
            with open_stream() as resp:
                holder["status"] = resp.status_code
                holder["headers"] = dict(resp.headers)
                for _ in resp.iter_raw():
                    pass
        except Exception as exc:  # noqa: BLE001 - surfaced to the main thread below
            holder["exc"] = exc

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    thread.join(timeout=timeout)

    timer.cancel()
    adapter._terminal.pop(run_id, None)
    adapter._queues.pop(run_id, None)

    assert not thread.is_alive(), "SSE stream read did not terminate within the bounded timeout"
    if holder["exc"] is not None:
        raise holder["exc"]
    return holder["status"], holder["headers"]


def _session_count():
    conn = db.get_conn()
    try:
        return conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    finally:
        conn.close()


def test_startup_fails_closed():
    original = os.environ.get("APP_PASSWORD")
    try:
        for value, has_key in ((None, False), ("", True), ("   ", True)):
            if has_key:
                os.environ["APP_PASSWORD"] = value
            else:
                os.environ.pop("APP_PASSWORD", None)
            try:
                server._startup()
                raise AssertionError(f"_startup() did not raise for APP_PASSWORD={value!r}")
            except RuntimeError as exc:
                assert str(exc) == "APP_PASSWORD must be set before the server can start.", str(exc)
    finally:
        if original is None:
            os.environ.pop("APP_PASSWORD", None)
        else:
            os.environ["APP_PASSWORD"] = original
    print("  ok  startup fails closed when APP_PASSWORD is missing, empty, or whitespace")


def _assert_401(resp):
    assert resp.status_code == 401, resp.status_code
    assert resp.headers["WWW-Authenticate"] == _CHALLENGE, resp.headers.get("WWW-Authenticate")
    assert resp.text == "Authentication required.", resp.text


def test_401_on_every_path_type():
    before = _session_count()

    _assert_401(anon_client.get("/"))
    _assert_401(anon_client.get(_STATIC_ASSET))
    _assert_401(anon_client.get("/api/sessions"))
    _assert_401(anon_client.post("/api/sessions", json={"name": "nope"}))
    _assert_401(anon_client.get("/api/runs/does-not-exist/artifacts/also-missing"))
    _assert_401(anon_client.get("/api/runs/does-not-exist/events"))

    assert _session_count() == before, "an unauthenticated POST created a session"
    print("  ok  every path type returns 401 with the exact challenge, and the POST created nothing")


def _malformed_cases():
    cases = {}
    cases["header absent"] = None
    cases["wrong scheme"] = "Bearer abc"
    cases["invalid base64"] = "Basic !!!not-base64!!!"
    cases["non-utf8 bytes"] = "Basic " + base64.b64encode(b"\xff\xfe:pw").decode()
    cases["missing colon"] = "Basic " + base64.b64encode(b"officetest-password").decode()
    cases["empty username"] = "Basic " + base64.b64encode(b":test-password").decode()
    cases["wrong password"] = "Basic " + base64.b64encode(b"office:wrong-password").decode()
    return cases


def test_401_for_malformed_credentials():
    for name, header in _malformed_cases().items():
        headers = {"Authorization": header} if header is not None else {}
        resp = anon_client.get("/api/sessions", headers=headers)
        _assert_401(resp)
        assert "wrong-password" not in resp.text
        assert "test-password" not in resp.text
        if header:
            assert header not in resp.text
    print("  ok  every malformed credential case returns 401 with the exact challenge")


def test_non_ascii_password_returns_401_not_500():
    header = "Basic " + base64.b64encode("office:pässwörd".encode("utf-8")).decode()
    resp = anon_client.get("/api/sessions", headers={"Authorization": header})
    assert resp.status_code == 401, resp.status_code
    assert "pässwörd" not in resp.text
    print("  ok  a non-ASCII password returns 401, not a 500 from secrets.compare_digest")


def test_valid_credentials_reach_routing():
    resp = auth_client.get("/")
    assert resp.status_code == 200, resp.status_code

    resp = auth_client.get(_STATIC_ASSET)
    assert resp.status_code == 200, resp.status_code

    resp = auth_client.get("/api/sessions")
    assert resp.status_code == 200, resp.status_code

    resp = auth_client.post("/api/sessions", json={"name": "S"})
    assert resp.status_code == 201, resp.text
    assert "id" in resp.json()

    resp = auth_client.get("/api/runs/does-not-exist/artifacts/also-missing")
    assert resp.status_code == 404, resp.status_code
    print("  ok  valid credentials reach routing: static 200, API 200/201, missing artifact 404 not 401")


def test_authenticated_sse_streams():
    with patch.object(server, "_SSE_HEARTBEAT_SECONDS", 0.2):
        session_id = auth_client.post("/api/sessions", json={"name": "SSE"}).json()["id"]
        run_id = _seed_run(session_id, "queued")

        status, headers = _bounded_sse_status(
            lambda: auth_client.stream("GET", f"/api/runs/{run_id}/events"), run_id
        )
        assert status == 200, status
        assert headers["content-type"].startswith("text/event-stream"), headers["content-type"]
    print("  ok  authenticated SSE request streams a 200 text/event-stream response")


if __name__ == "__main__":
    tests = [
        test_startup_fails_closed,
        test_401_on_every_path_type,
        test_401_for_malformed_credentials,
        test_non_ascii_password_returns_401_not_500,
        test_valid_credentials_reach_routing,
        test_authenticated_sse_streams,
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
