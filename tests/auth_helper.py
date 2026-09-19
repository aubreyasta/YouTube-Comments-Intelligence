"""
Sign-in for offline server tests.

Import before `import server`: it sets the sign-in environment the startup
hook requires. Assigned, not setdefault, so a developer machine with real
values exported still runs the tests against these.

login() skips the Google round trip: it inserts a user and a login session
straight into the database (db.init() must have run) and returns the cookie
token. tests/test_auth.py covers the real callback.

wait_until() polls for state a background run thread writes.
"""

import os
import time

os.environ.update(
    GOOGLE_CLIENT_ID="test-client-id",
    GOOGLE_CLIENT_SECRET="test-client-secret",
    APP_BASE_URL="http://testserver",
    AUTH_ALLOWED_DOMAINS="example.com",
    ADMIN_EMAILS="admin@example.com",
)

SESSION_COOKIE = "yi_session"


def create_login(email="office@example.com"):
    """Create (or reuse) the user and a fresh 7-day login session. Returns the token."""
    import secrets
    import uuid
    from datetime import datetime, timezone

    import db
    import server

    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    conn = db.get_conn()
    try:
        conn.execute(
            "INSERT INTO users (id, email, created_at) VALUES (?,?,?) ON CONFLICT(email) DO NOTHING",
            (str(uuid.uuid4()), email, now.isoformat()))
        user_id = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()["id"]
        conn.execute(
            "INSERT INTO auth_sessions (token_hash, user_id, created_at, expires_at) VALUES (?,?,?,?)",
            (server._token_hash(token), user_id, now.isoformat(),
             (now + server._SESSION_TTL).isoformat()))
        conn.commit()
    finally:
        conn.close()
    return token


def login(client, email="office@example.com"):
    """Sign a TestClient in as `email` and return it."""
    client.cookies.set(SESSION_COOKIE, create_login(email))
    return client


def wait_until(pred, timeout=5.0):
    """True once pred() is truthy, False after `timeout` seconds."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.02)
    return False
