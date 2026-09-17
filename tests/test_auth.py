"""
Offline self-check for the Google sign-in boundary in server.py.

Covers: startup refuses to start without the sign-in env; every path type
(static, API, artifact download, SSE) is refused without a valid login
session; the OAuth callback rejects a bad state and every disallowed ID
token claim, and signs a valid user in; expired and blocked sessions stop
working; logout ends the session; admin-only user management, including
the self-block and self-erase guards; Sessions record their creator; no
token appears in a response body.

Google's token endpoint is patched (httpx.post). No network, no model.

Run: python tests/test_auth.py
"""

import base64
import hashlib
import json
import os
import pathlib
import sys
import tempfile
import threading
import time
import uuid
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
db._DB_PATH = pathlib.Path(tempfile.mkdtemp()) / "app.db"

import storage
storage._ROOT = tempfile.mkdtemp()

from starlette.testclient import TestClient

from auth_helper import SESSION_COOKIE, create_login, login  # sets the sign-in env

import server
import adapter

db.init()


def _client(**kwargs):
    return TestClient(server.app, follow_redirects=False, **kwargs)


def _session_rows(email):
    conn = db.get_conn()
    try:
        return conn.execute(
            "SELECT s.* FROM auth_sessions s JOIN users u ON u.id = s.user_id WHERE u.email = ?",
            (email,)).fetchall()
    finally:
        conn.close()


def _user_id(email):
    conn = db.get_conn()
    try:
        row = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        return row["id"] if row else None
    finally:
        conn.close()


def _seed_run(session_id, state):
    conn = db.get_conn()
    try:
        rid = str(uuid.uuid4())
        conn.execute("INSERT INTO runs (id, session_id, state) VALUES (?, ?, ?)",
                     (rid, session_id, state))
        conn.commit()
    finally:
        conn.close()
    return rid


def test_startup_fails_closed():
    for name in server._REQUIRED_AUTH_ENV:
        original = os.environ[name]
        try:
            for value in (None, "", "   "):
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
                try:
                    server._startup()
                    raise AssertionError(f"_startup() did not raise for {name}={value!r}")
                except RuntimeError as exc:
                    assert str(exc) == f"{name} must be set before the server can start.", str(exc)
        finally:
            os.environ[name] = original
    original = os.environ["APP_BASE_URL"]
    try:
        os.environ["APP_BASE_URL"] = "yt.example.com"
        try:
            server._startup()
            raise AssertionError("_startup() accepted an APP_BASE_URL without a scheme")
        except RuntimeError as exc:
            assert "APP_BASE_URL" in str(exc)
    finally:
        os.environ["APP_BASE_URL"] = original
    print("  ok  startup fails closed for each missing sign-in variable and a bad APP_BASE_URL")


def test_unauthenticated_requests_are_refused():
    anon = _client()
    for path in ("/api/sessions", "/api/me", "/api/users",
                 "/api/runs/missing/artifacts/missing", "/api/runs/missing/events"):
        resp = anon.get(path)
        assert resp.status_code == 401, (path, resp.status_code)
        assert resp.json() == {"error": "UNAUTHENTICATED", "message": "Sign in required.",
                               "field": None}, resp.text
    resp = anon.post("/api/sessions", json={"name": "nope"})
    assert resp.status_code == 401
    for path in ("/", "/app.js", "/index.html"):
        resp = anon.get(path)
        assert resp.status_code == 302, (path, resp.status_code)
        assert resp.headers["location"] == "/auth/login"
    resp = _client(cookies={SESSION_COOKIE: "not-a-real-token"}).get("/api/sessions")
    assert resp.status_code == 401
    print("  ok  no session: API, download, and SSE get 401 JSON; pages and static files redirect to sign-in")


def test_login_redirects_to_google():
    resp = _client().get("/auth/login")
    assert resp.status_code == 302
    url = urlsplit(resp.headers["location"])
    assert f"{url.scheme}://{url.netloc}{url.path}" == server._GOOGLE_AUTH_URL
    q = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert q["client_id"] == "test-client-id"
    assert q["redirect_uri"] == "http://testserver/auth/callback"
    assert q["scope"] == "openid email profile"
    assert q["hd"] == "example.com"
    assert q["code_challenge_method"] == "S256"
    state, nonce, verifier = resp.cookies[server._OAUTH_COOKIE].split(".")
    assert q["state"] == state and q["nonce"] == nonce
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert q["code_challenge"] == expected
    assert "HttpOnly" in resp.headers["set-cookie"] and "samesite=lax" in resp.headers["set-cookie"].lower()
    print("  ok  /auth/login redirects to Google with state, nonce, PKCE, and the hd hint")


def _id_token(**overrides):
    claims = {"iss": "https://accounts.google.com", "aud": "test-client-id",
              "exp": time.time() + 3600, "email": "new.user@example.com",
              "email_verified": True, "hd": "example.com", "name": "New User"}
    claims.update(overrides)
    claims = {k: v for k, v in claims.items() if v is not None}
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    return f"eyJhbGciOiJSUzI1NiJ9.{payload}.signature"


def _callback(claims=None, *, state_override=None, token_error=False):
    """Run /auth/login then /auth/callback with a patched token endpoint."""
    client = _client()
    login_resp = client.get("/auth/login")
    state, nonce, _ = login_resp.cookies[server._OAUTH_COOKIE].split(".")
    token_resp = MagicMock()
    if token_error:
        import httpx
        token_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "bad", request=MagicMock(), response=MagicMock())
    token_resp.json.return_value = {"id_token": _id_token(**{"nonce": nonce, **(claims or {})})}
    with patch.object(server.httpx, "post", return_value=token_resp) as post:
        resp = client.get("/auth/callback", params={
            "code": "auth-code", "state": state if state_override is None else state_override})
    return client, resp, post


def test_callback_rejects_bad_state_and_claims():
    before = _session_count()
    _, resp, post = _callback(state_override="forged")
    assert resp.status_code == 403 and not post.called

    resp = _client().get("/auth/callback", params={"code": "x", "state": "y"})
    assert resp.status_code == 403, "callback without the oauth cookie must be refused"
    resp = _client().get("/auth/callback", params={"error": "access_denied"})
    assert resp.status_code == 403

    for name, claims in {
        "wrong hd": {"hd": "other.com"},
        "no hd (personal account)": {"hd": None},
        "wrong aud": {"aud": "someone-else"},
        "wrong iss": {"iss": "https://evil.example"},
        "expired": {"exp": time.time() - 10},
        "wrong nonce": {"nonce": "replayed"},
        "unverified email": {"email_verified": False},
    }.items():
        _, resp, _ = _callback(claims)
        assert resp.status_code == 403, (name, resp.status_code)
        assert SESSION_COOKIE not in resp.cookies, name
        assert "auth-code" not in resp.text and "signature" not in resp.text, name

    _, resp, _ = _callback(token_error=True)
    assert resp.status_code == 403
    assert _session_count() == before, "a rejected callback created a login session"
    assert _user_id("new.user@example.com") is None, "a rejected callback created a user"
    print("  ok  callback refuses bad state, missing cookie, cancel, token error, and each disallowed claim")


def _session_count():
    conn = db.get_conn()
    try:
        return conn.execute("SELECT COUNT(*) FROM auth_sessions").fetchone()[0]
    finally:
        conn.close()


def test_callback_signs_in_and_logout_ends_session():
    client, resp, post = _callback({"email": "Valid.User@Example.com"})
    assert resp.status_code == 302 and resp.headers["location"] == "/", resp.status_code
    sent = post.call_args.kwargs["data"]
    assert sent["code"] == "auth-code" and sent["redirect_uri"] == "http://testserver/auth/callback"
    assert sent["code_verifier"], "PKCE verifier was not sent"
    token = resp.cookies[SESSION_COOKIE]
    rows = _session_rows("valid.user@example.com")
    assert len(rows) == 1, "email is stored lowercased with one session"
    assert rows[0]["token_hash"] == hashlib.sha256(token.encode()).hexdigest()
    assert token not in json.dumps([dict(r) for r in rows]), "raw token stored"

    me = client.get("/api/me")
    assert me.status_code == 200
    assert me.json() == {"email": "valid.user@example.com", "name": "New User", "isAdmin": False}
    assert token not in me.text
    assert client.get("/").status_code == 200

    assert client.post("/auth/logout").status_code == 204
    assert _session_rows("valid.user@example.com") == []
    client.cookies.set(SESSION_COOKIE, token)
    assert client.get("/api/me").status_code == 401, "a logged-out token still works"
    assert _client().post("/auth/logout").status_code == 204, "logout without a cookie must not fail"
    print("  ok  valid callback creates the user and a hashed session; logout revokes it")


def test_expired_session_is_rejected_and_deleted():
    client = login(_client(), "expired@example.com")
    conn = db.get_conn()
    try:
        conn.execute("UPDATE auth_sessions SET expires_at = '2000-01-01T00:00:00+00:00' "
                     "WHERE user_id = (SELECT id FROM users WHERE email = 'expired@example.com')")
        conn.commit()
    finally:
        conn.close()
    assert client.get("/api/me").status_code == 401
    assert _session_rows("expired@example.com") == []
    print("  ok  an expired session gets 401 and its row is deleted")


def _users_by_email(admin):
    return {u["email"]: u for u in admin.get("/api/users").json()}


def test_admin_user_management():
    admin = login(_client(), "admin@example.com")
    member = login(_client(), "member@example.com")
    member_id = _user_id("member@example.com")
    admin_id = _user_id("admin@example.com")

    assert admin.get("/api/me").json()["isAdmin"] is True
    for resp in (member.get("/api/users"),
                 member.patch(f"/api/users/{admin_id}", json={"blocked": True}),
                 member.delete(f"/api/users/{admin_id}")):
        assert resp.status_code == 403, resp.status_code
        assert resp.json()["error"] == "FORBIDDEN"

    by_email = _users_by_email(admin)
    assert set(by_email["member@example.com"]) == {
        "id", "email", "name", "blocked", "isAdmin", "lastLoginAt"}
    assert by_email["admin@example.com"]["isAdmin"] is True

    assert admin.patch(f"/api/users/{admin_id}", json={"blocked": True}).status_code == 422
    assert admin.delete(f"/api/users/{admin_id}").status_code == 422
    assert admin.patch("/api/users/missing", json={"blocked": True}).status_code == 404
    assert admin.delete("/api/users/missing").status_code == 404

    assert admin.patch(f"/api/users/{member_id}", json={"blocked": True}).status_code == 204
    assert _users_by_email(admin)["member@example.com"]["blocked"] is True
    assert member.get("/api/sessions").status_code == 401, "a blocked user's open session still works"
    assert _session_rows("member@example.com") == []

    _, resp, _ = _callback({"email": "member@example.com"})
    assert resp.status_code == 403, "a blocked user signed in again"

    assert admin.patch(f"/api/users/{member_id}", json={"blocked": False}).status_code == 204
    assert _users_by_email(admin)["member@example.com"]["blocked"] is False
    _, resp, _ = _callback({"email": "member@example.com"})
    assert resp.status_code == 302, "an unblocked user cannot sign in"

    member = login(_client(), "member@example.com")
    session_id = member.post("/api/sessions", json={"name": "Made by member"}).json()["id"]
    assert admin.get(f"/api/sessions/{session_id}").json()["createdBy"] == "member@example.com"

    assert admin.delete(f"/api/users/{member_id}").status_code == 204
    assert member.get("/api/me").status_code == 401
    assert _user_id("member@example.com") is None
    assert admin.get(f"/api/sessions/{session_id}").json()["createdBy"] is None, \
        "erasing a user must keep their Session without a creator"
    print("  ok  admin-only list/block/unblock/erase; block revokes sessions and sign-in; self-guard; createdBy")


def test_authenticated_sse_streams():
    client = login(_client())
    with patch.object(server, "_SSE_HEARTBEAT_SECONDS", 0.2):
        session_id = client.post("/api/sessions", json={"name": "SSE"}).json()["id"]
        run_id = _seed_run(session_id, "queued")

        def _force_terminal():
            adapter._terminal[run_id] = True
            adapter.get_queue(run_id).put({"run_id": run_id, "stage": "complete", "pct": 100,
                                           "message": "test teardown", "detail": None})

        timer = threading.Timer(0.8, _force_terminal)
        timer.start()
        try:
            with client.stream("GET", f"/api/runs/{run_id}/events") as resp:
                status, content_type = resp.status_code, resp.headers["content-type"]
                for _ in resp.iter_raw():
                    pass
        finally:
            timer.cancel()
            adapter._terminal.pop(run_id, None)
            adapter._queues.pop(run_id, None)
    assert status == 200, status
    assert content_type.startswith("text/event-stream"), content_type
    print("  ok  a signed-in SSE request streams a 200 text/event-stream response")


if __name__ == "__main__":
    tests = [
        test_startup_fails_closed,
        test_unauthenticated_requests_are_refused,
        test_login_redirects_to_google,
        test_callback_rejects_bad_state_and_claims,
        test_callback_signs_in_and_logout_ends_session,
        test_expired_session_is_rejected_and_deleted,
        test_admin_user_management,
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
