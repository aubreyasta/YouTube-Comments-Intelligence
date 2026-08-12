"""Offline self-check for the Session Key Message schema in db.py.

Points db._DB_PATH at a temp file (the module's only path seam) and
runs db.init() against it, so the real fresh-database schema is what
gets exercised.

Run: python tests/test_db_schema.py
"""

import os
import sqlite3
import sys
import tempfile
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db


def _fresh_db():
    """Point db at a throwaway sqlite file and initialize it."""
    tmp_dir = tempfile.mkdtemp()
    db._DB_PATH = __import__("pathlib").Path(tmp_dir) / "app.db"
    db.init()
    return db.get_conn()


def _make_session(conn, session_id):
    conn.execute(
        "INSERT INTO sessions (id, name, created_at, updated_at) "
        "VALUES (?, 'test session', 't', 't')",
        (session_id,),
    )
    conn.commit()


def test_key_messages_status_default_and_check():
    conn = _fresh_db()
    session_id = str(uuid.uuid4())
    _make_session(conn, session_id)

    row = conn.execute(
        "SELECT key_messages_status, key_messages_error, key_messages_revision "
        "FROM sessions WHERE id = ?",
        (session_id,),
    ).fetchone()
    assert row["key_messages_status"] == "empty", row["key_messages_status"]
    assert row["key_messages_error"] is None, row["key_messages_error"]
    assert row["key_messages_revision"] == 0, row["key_messages_revision"]

    try:
        conn.execute(
            "UPDATE sessions SET key_messages_status = 'bogus' WHERE id = ?",
            (session_id,),
        )
        conn.commit()
        raised = False
    except sqlite3.IntegrityError:
        raised = True
    assert raised, "CHECK constraint did not reject an invalid draft status"

    for status in ("empty", "drafting", "ready", "stale", "failed"):
        conn.execute(
            "UPDATE sessions SET key_messages_status = ? WHERE id = ?",
            (status, session_id),
        )
    conn.commit()
    conn.close()
    print("  ok  key_messages_status default 'empty', nullable error, "
          "revision=0, CHECK rejects unknown status, accepts all five")


def test_key_messages_row_defaults_and_fk_cascade():
    conn = _fresh_db()
    session_id = str(uuid.uuid4())
    _make_session(conn, session_id)

    message_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO key_messages (id, session_id, label) VALUES (?, ?, 'Label')",
        (message_id, session_id),
    )
    conn.commit()

    row = conn.execute(
        "SELECT description, included, sort_order, edited FROM key_messages WHERE id = ?",
        (message_id,),
    ).fetchone()
    assert row["description"] == "", row["description"]
    assert row["included"] == 1, row["included"]
    assert row["sort_order"] == 0, row["sort_order"]
    assert row["edited"] == 0, row["edited"]

    conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    conn.commit()
    remaining = conn.execute(
        "SELECT COUNT(*) AS n FROM key_messages WHERE id = ?", (message_id,)
    ).fetchone()["n"]
    assert remaining == 0, "ON DELETE CASCADE did not remove key_messages row"
    conn.close()
    print("  ok  key_messages defaults (description='', included=1, sort_order=0, "
          "edited=0) and FK cascade delete from sessions")


def test_key_messages_orphan_session_rejected():
    conn = _fresh_db()
    try:
        conn.execute(
            "INSERT INTO key_messages (id, session_id, label) VALUES (?, ?, 'Label')",
            (str(uuid.uuid4()), str(uuid.uuid4())),
        )
        conn.commit()
        raised = False
    except sqlite3.IntegrityError:
        raised = True
    assert raised, "FK did not reject a key_messages row with an unknown session_id"
    conn.close()
    print("  ok  key_messages.session_id FK rejects an unknown session")


if __name__ == "__main__":
    tests = [
        test_key_messages_status_default_and_check,
        test_key_messages_row_defaults_and_fk_cascade,
        test_key_messages_orphan_session_rejected,
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
