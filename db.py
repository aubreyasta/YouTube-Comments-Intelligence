import sqlite3
from pathlib import Path

_DB_PATH = Path("data/app.db")

_SCHEMA = """
-- People who have signed in with Google. Admin rights are not stored: they
-- come from ADMIN_EMAILS on every request (server._session_user).
CREATE TABLE IF NOT EXISTS users (
    id            TEXT PRIMARY KEY,
    email         TEXT NOT NULL UNIQUE,
    name          TEXT,
    blocked       INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    last_login_at TEXT
);

-- Login sessions. The cookie holds a random token; only its SHA-256 is
-- stored, so a copy of this file cannot be replayed as a cookie.
CREATE TABLE IF NOT EXISTS auth_sessions (
    token_hash TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id                   TEXT PRIMARY KEY,
    name                 TEXT NOT NULL,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL,
    -- The user who created the Session. NULL for Sessions created before
    -- sign-in existed, or after that user was erased.
    created_by           TEXT REFERENCES users(id) ON DELETE SET NULL,
    -- Session-level Key Messages draft state. One draft per session; the
    -- messages themselves live in key_messages below. status matches the
    -- KeyMessageDraft contract (empty|drafting|ready|stale|failed).
    -- revision increments on every POST .../key_messages/draft call, so a
    -- future background drafting worker can detect and discard a stale
    -- in-flight generation superseded by a newer request.
    key_messages_status   TEXT NOT NULL DEFAULT 'empty'
        CHECK (key_messages_status IN ('empty', 'drafting', 'ready', 'stale', 'failed')),
    key_messages_error    TEXT,
    key_messages_revision INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS campaigns (
    id         TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    name       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS videos (
    id          TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    url         TEXT NOT NULL,
    youtube_id  TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'auto'
);

CREATE TABLE IF NOT EXISTS assets (
    id           TEXT PRIMARY KEY,
    campaign_id  TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    kind         TEXT NOT NULL,
    filename     TEXT,
    url          TEXT,
    title        TEXT,
    text         TEXT,
    retrieved_at TEXT,
    file_path    TEXT
);

CREATE TABLE IF NOT EXISTS runs (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    state       TEXT NOT NULL DEFAULT 'queued',
    -- Progress stage, one of progress.STAGES. A column, not part of
    -- `progress`, because admission and the brief_pause checks query it.
    stage       TEXT NOT NULL DEFAULT 'queued',
    -- 1 when the user asked to skip the Key Message review pause. The
    -- pause still happens when reconciliation leaves zero included
    -- messages, so this is a request, not a guarantee.
    skip_pause  INTEGER NOT NULL DEFAULT 0,
    -- The rest of the Run progress snapshot as JSON {pct, message, counts}.
    -- Written and read only by progress.py.
    progress    TEXT,
    started_at  TEXT,
    finished_at TEXT,
    error       TEXT
);

-- video_id is NULL for a Session-level Key Message: it applies to every
-- video in the campaign, not one. analyze.classify() broadcasts a NULL-
-- video_id point to every video's batch instead of scoping it to one.
CREATE TABLE IF NOT EXISTS brief_points (
    id          TEXT PRIMARY KEY,
    run_id      TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    campaign_id TEXT NOT NULL,
    video_id    TEXT,
    label       TEXT NOT NULL,
    description TEXT NOT NULL,
    approved    INTEGER NOT NULL DEFAULT 0,
    edited      INTEGER NOT NULL DEFAULT 0,
    included    INTEGER NOT NULL DEFAULT 1,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    -- Where the point came from: input (User Inputs or the review screen),
    -- sharpened (an input point whose description a transcript refreshed),
    -- or transcript (derived from transcripts alone). See brief.reconcile().
    source      TEXT NOT NULL DEFAULT 'input'
);

CREATE TABLE IF NOT EXISTS run_artifacts (
    id        TEXT PRIMARY KEY,
    run_id    TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    kind      TEXT NOT NULL,
    file_path TEXT NOT NULL
);

-- Session-level Key Messages (product term). Stable once created; a
-- PATCH replaces the whole ordered list in one transaction but keeps
-- existing ids that are resubmitted, so "manual-edit" state on an id a
-- user has already touched survives a later redraft that preserves it.
CREATE TABLE IF NOT EXISTS key_messages (
    id         TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    label      TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    included  INTEGER NOT NULL DEFAULT 1,
    sort_order INTEGER NOT NULL DEFAULT 0,
    edited    INTEGER NOT NULL DEFAULT 0
);
"""


def init() -> None:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_DB_PATH)
    try:
        conn.executescript(_SCHEMA)
        # Migration: a pre-stage database has a runs table without the
        # `stage` column (CREATE TABLE IF NOT EXISTS above is a no-op on
        # an existing table). ALTER TABLE ADD COLUMN is a one-shot,
        # idempotent-by-guard fix rather than a migrations framework.
        cols = {row[1] for row in conn.execute("PRAGMA table_info(runs)")}
        if "stage" not in cols:
            conn.execute(
                "ALTER TABLE runs ADD COLUMN stage TEXT NOT NULL DEFAULT 'queued'")
        if "skip_pause" not in cols:
            conn.execute(
                "ALTER TABLE runs ADD COLUMN skip_pause INTEGER NOT NULL DEFAULT 0")
        if "progress" not in cols:
            conn.execute("ALTER TABLE runs ADD COLUMN progress TEXT")
        # total_comments and progress_detail became `progress`. Only the
        # total is worth carrying over; the old detail was a mid-classify
        # string, and every run that wrote one is now terminal.
        if "total_comments" in cols:
            conn.execute(
                "UPDATE runs SET progress = json_object('counts', json_object('total', total_comments)) "
                "WHERE total_comments IS NOT NULL AND progress IS NULL")
            conn.execute("ALTER TABLE runs DROP COLUMN total_comments")
        if "progress_detail" in cols:
            conn.execute("ALTER TABLE runs DROP COLUMN progress_detail")
        session_cols = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
        if "created_by" not in session_cols:
            conn.execute(
                "ALTER TABLE sessions ADD COLUMN created_by TEXT "
                "REFERENCES users(id) ON DELETE SET NULL")
        bp_cols = {row[1] for row in conn.execute("PRAGMA table_info(brief_points)")}
        if "source" not in bp_cols:
            conn.execute(
                "ALTER TABLE brief_points ADD COLUMN source TEXT NOT NULL DEFAULT 'input'")
        conn.commit()
    finally:
        conn.close()


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn
