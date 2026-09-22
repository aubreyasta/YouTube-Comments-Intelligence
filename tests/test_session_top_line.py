"""
Offline self-check for issue #54: a Session must carry `topLine`.

The Sessions list renders each complete Session's verdict from
session.topLine, which is the opening paragraph of the written read the
pipeline already stored in report.json. A missing or whole-document
topLine breaks the card: the grid either shows no verdict at all or a
four-paragraph wall of text.

Points db._DB_PATH and storage._ROOT at temp locations before importing
server, then drives the real routes through FastAPI's TestClient. Both
globals are restored on exit so this file never leaves anything under the
repo's real data/ tree.

Run: python tests/test_session_top_line.py
"""

import json
import os
import pathlib
import sys
import tempfile
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
_ORIG_DB_PATH = db._DB_PATH
db._DB_PATH = pathlib.Path(tempfile.mkdtemp()) / "app.db"

import storage
_ORIG_STORAGE_ROOT = storage._ROOT
storage._ROOT = tempfile.mkdtemp()

from starlette.testclient import TestClient

from auth_helper import login

import server

db.init()
client = login(TestClient(server.app))

_REPORT_DIR = pathlib.Path(tempfile.mkdtemp())

FIRST = "Only 41% of comments mentioned the location idea."
SECOND = "The product line never arrived at all."
INTERPRETATION = f"{FIRST}\n\n{SECOND}"


def _new_session():
    return client.post("/api/sessions", json={"name": "S"}).json()["id"]


def _seed_complete_run(sid, report: dict | None, kind="report_json"):
    """A complete run, optionally with a report.json artifact on disk."""
    rid = str(uuid.uuid4())
    conn = db.get_conn()
    try:
        conn.execute(
            "INSERT INTO runs (id, session_id, state, stage, started_at, finished_at) "
            "VALUES (?,?,?,?,?,?)",
            (rid, sid, "complete", "complete",
             "2026-09-20T08:00:00+00:00", "2026-09-20T09:00:00+00:00"),
        )
        if report is not None:
            path = _REPORT_DIR / f"{rid}.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            conn.execute(
                "INSERT INTO run_artifacts (id, run_id, kind, file_path) VALUES (?,?,?,?)",
                (str(uuid.uuid4()), rid, kind, str(path)),
            )
        conn.commit()
    finally:
        conn.close()
    return rid


def _top_line(sid):
    return client.get(f"/api/sessions/{sid}").json()["topLine"]


def test_top_line_is_the_first_paragraph():
    """Only the opening paragraph rides along, not the whole read."""
    sid = _new_session()
    _seed_complete_run(sid, {"interpretation": INTERPRETATION})
    assert _top_line(sid) == FIRST, f"got {_top_line(sid)!r}"


def test_top_line_absent_without_a_complete_run():
    """A Session that has never run has no verdict to show."""
    sid = _new_session()
    assert _top_line(sid) is None, f"got {_top_line(sid)!r}"


def test_top_line_absent_without_prose():
    """A run completed before the pipeline wrote prose degrades to None."""
    sid = _new_session()
    _seed_complete_run(sid, {"themes": []})
    assert _top_line(sid) is None, f"got {_top_line(sid)!r}"


def test_top_line_survives_a_missing_report_file():
    """A report row whose file is gone must not 500 the Sessions list."""
    sid = _new_session()
    rid = _seed_complete_run(sid, {"interpretation": INTERPRETATION})
    (_REPORT_DIR / f"{rid}.json").unlink()
    assert _top_line(sid) is None, f"got {_top_line(sid)!r}"


def test_top_line_in_the_list_response():
    """The list route carries it too, so the grid needs one request."""
    sid = _new_session()
    _seed_complete_run(sid, {"interpretation": INTERPRETATION})
    listed = client.get("/api/sessions").json()
    row = next(s for s in listed if s["id"] == sid)
    assert row["topLine"] == FIRST, f"got {row.get('topLine')!r}"


def main() -> None:
    tests = [
        test_top_line_is_the_first_paragraph,
        test_top_line_absent_without_a_complete_run,
        test_top_line_absent_without_prose,
        test_top_line_survives_a_missing_report_file,
        test_top_line_in_the_list_response,
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
