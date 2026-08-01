import sqlite3
from pathlib import Path

_DB_PATH = Path("data/app.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
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
    id         TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    state      TEXT NOT NULL DEFAULT 'queued',
    started_at TEXT,
    finished_at TEXT,
    error      TEXT
);

CREATE TABLE IF NOT EXISTS brief_points (
    id          TEXT PRIMARY KEY,
    run_id      TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    campaign_id TEXT NOT NULL,
    video_id    TEXT NOT NULL,
    label       TEXT NOT NULL,
    description TEXT NOT NULL,
    approved    INTEGER NOT NULL DEFAULT 0,
    edited      INTEGER NOT NULL DEFAULT 0,
    included    INTEGER NOT NULL DEFAULT 1,
    sort_order  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS run_artifacts (
    id        TEXT PRIMARY KEY,
    run_id    TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    kind      TEXT NOT NULL,
    file_path TEXT NOT NULL
);
"""


def init() -> None:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_DB_PATH)
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn
