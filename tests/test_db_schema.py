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


def _make_run(conn, run_id, session_id):
    conn.execute(
        "INSERT INTO runs (id, session_id) VALUES (?, ?)",
        (run_id, session_id),
    )
    conn.commit()


def test_runs_stage_default_and_brief_pause_persistence():
    conn = _fresh_db()
    session_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    _make_session(conn, session_id)
    _make_run(conn, run_id, session_id)

    row = conn.execute(
        "SELECT stage FROM runs WHERE id = ?", (run_id,)
    ).fetchone()
    assert row["stage"] == "queued", row["stage"]

    conn.execute("UPDATE runs SET stage = 'brief_pause' WHERE id = ?", (run_id,))
    conn.commit()
    conn.close()

    conn = db.get_conn()
    row = conn.execute(
        "SELECT stage FROM runs WHERE id = ?", (run_id,)
    ).fetchone()
    assert row["stage"] == "brief_pause", row["stage"]
    conn.close()
    print("  ok  runs.stage defaults to 'queued' and persists 'brief_pause' "
          "across a reopened connection")


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


def test_init_migrates_pre_stage_database():
    """A database created before `runs.stage` existed (simulated here by
    creating the `runs` table without that column, bypassing db._SCHEMA)
    must gain `stage TEXT NOT NULL DEFAULT 'queued'` the next time
    db.init() opens it, without losing existing rows."""
    tmp_dir = tempfile.mkdtemp()
    path = __import__("pathlib").Path(tmp_dir) / "app.db"
    db._DB_PATH = path

    pre_conn = sqlite3.connect(path)
    pre_conn.execute(
        "CREATE TABLE sessions (id TEXT PRIMARY KEY, name TEXT NOT NULL, "
        "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    pre_conn.execute(
        "CREATE TABLE runs (id TEXT PRIMARY KEY, session_id TEXT NOT NULL, "
        "state TEXT NOT NULL DEFAULT 'queued', started_at TEXT, "
        "finished_at TEXT, error TEXT)"
    )
    session_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    pre_conn.execute(
        "INSERT INTO sessions (id, name, created_at, updated_at) "
        "VALUES (?, 'pre-stage', 't', 't')", (session_id,))
    pre_conn.execute(
        "INSERT INTO runs (id, session_id) VALUES (?, ?)", (run_id, session_id))
    pre_conn.commit()
    pre_conn.close()

    cols_before = {row[1] for row in sqlite3.connect(path).execute("PRAGMA table_info(runs)")}
    assert "stage" not in cols_before, "test setup already has a stage column"

    db.init()

    conn = db.get_conn()
    cols_after = {row[1] for row in conn.execute("PRAGMA table_info(runs)")}
    assert "stage" in cols_after, "db.init() did not add runs.stage to a pre-stage database"
    row = conn.execute("SELECT stage FROM runs WHERE id = ?", (run_id,)).fetchone()
    assert row["stage"] == "queued", row["stage"]

    try:
        conn.execute("UPDATE runs SET stage = NULL WHERE id = ?", (run_id,))
        conn.commit()
        raised = False
    except sqlite3.IntegrityError:
        raised = True
    assert raised, "migrated stage column did not reject NULL"

    new_run_id = str(uuid.uuid4())
    conn.execute("INSERT INTO runs (id, session_id) VALUES (?, ?)", (new_run_id, session_id))
    conn.commit()
    row = conn.execute("SELECT stage FROM runs WHERE id = ?", (new_run_id,)).fetchone()
    assert row["stage"] == "queued", row["stage"]
    conn.close()
    print("  ok  db.init() migrates a pre-stage database by adding "
          "runs.stage TEXT NOT NULL DEFAULT 'queued', existing rows kept, "
          "NULL rejected, new rows default to 'queued'")


def test_fresh_db_runs_skip_pause_column_shape():
    _fresh_db()
    conn = db.get_conn()
    try:
        cols = {row[1]: row for row in conn.execute("PRAGMA table_info(runs)")}
    finally:
        conn.close()
    assert "skip_pause" in cols, "runs.skip_pause missing from a fresh database"
    row = cols["skip_pause"]
    # PRAGMA table_info columns: (cid, name, type, notnull, dflt_value, pk)
    assert row[2] == "INTEGER", row[2]
    assert row[3] == 1, "runs.skip_pause is not NOT NULL"
    assert row[4] == "0", row[4]
    print("  ok  a fresh db.init() database has runs.skip_pause "
          "INTEGER NOT NULL DEFAULT 0")


def test_init_migrates_pre_skip_pause_database():
    """A database created before `runs.skip_pause` existed (simulated
    here by creating the `runs` table without that column, bypassing
    db._SCHEMA) must gain `skip_pause INTEGER NOT NULL DEFAULT 0` the
    next time db.init() opens it, without losing existing rows."""
    tmp_dir = tempfile.mkdtemp()
    path = __import__("pathlib").Path(tmp_dir) / "app.db"
    saved_path = db._DB_PATH
    db._DB_PATH = path
    try:
        pre_conn = sqlite3.connect(path)
        pre_conn.execute(
            "CREATE TABLE sessions (id TEXT PRIMARY KEY, name TEXT NOT NULL, "
            "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        pre_conn.execute(
            "CREATE TABLE runs (id TEXT PRIMARY KEY, session_id TEXT NOT NULL, "
            "state TEXT NOT NULL DEFAULT 'queued', stage TEXT NOT NULL DEFAULT 'queued', "
            "started_at TEXT, finished_at TEXT, error TEXT)"
        )
        session_id = str(uuid.uuid4())
        run_id = str(uuid.uuid4())
        pre_conn.execute(
            "INSERT INTO sessions (id, name, created_at, updated_at) "
            "VALUES (?, 'pre-skip-pause', 't', 't')", (session_id,))
        pre_conn.execute(
            "INSERT INTO runs (id, session_id) VALUES (?, ?)", (run_id, session_id))
        pre_conn.commit()
        pre_conn.close()

        cols_before = {row[1] for row in sqlite3.connect(path).execute("PRAGMA table_info(runs)")}
        assert "skip_pause" not in cols_before, "test setup already has a skip_pause column"

        db.init()

        conn = db.get_conn()
        cols_after = {row[1] for row in conn.execute("PRAGMA table_info(runs)")}
        assert "skip_pause" in cols_after, (
            "db.init() did not add runs.skip_pause to a pre-skip_pause database")
        row = conn.execute("SELECT skip_pause FROM runs WHERE id = ?", (run_id,)).fetchone()
        assert row["skip_pause"] == 0, row["skip_pause"]
        conn.close()
    finally:
        db._DB_PATH = saved_path
    print("  ok  db.init() migrates a pre-skip_pause database by adding "
          "runs.skip_pause INTEGER NOT NULL DEFAULT 0, existing row reads 0")


def test_init_idempotent_on_skip_pause_column():
    conn = _fresh_db()
    conn.close()
    db.init()
    db.init()
    conn = db.get_conn()
    try:
        cols = [row[1] for row in conn.execute("PRAGMA table_info(runs)")]
    finally:
        conn.close()
    assert cols.count("skip_pause") == 1, cols
    print("  ok  calling db.init() repeatedly raises nothing and leaves "
          "exactly one runs.skip_pause column")


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
        test_runs_stage_default_and_brief_pause_persistence,
        test_init_migrates_pre_stage_database,
        test_key_messages_status_default_and_check,
        test_key_messages_row_defaults_and_fk_cascade,
        test_fresh_db_runs_skip_pause_column_shape,
        test_init_migrates_pre_skip_pause_database,
        test_init_idempotent_on_skip_pause_column,
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
