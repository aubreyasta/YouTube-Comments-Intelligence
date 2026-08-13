"""
Offline self-check for Task 2.3 (Register artifacts): adapter._execute()
must register all seven files in the artifact contract as run_artifacts
rows, and server.py must serialize only the six public ones onto
RunSnapshot.artifacts in fixed order, with report_json hidden from that
list but reachable through GET /runs/{id}/report.

Points db._DB_PATH and storage._ROOT at temp locations before importing
server (server.app's startup hook calls db.init() against whatever path
db._DB_PATH holds at that time; storage._ROOT gates every data/runs and
data/artifacts write adapter.py makes), then drives the routes through
FastAPI's TestClient. Both globals are restored on exit so this file
never leaves anything under the repo's real data/ tree.

This does not run the pipeline. It seeds run_artifacts rows directly,
the same way adapter._insert_artifact() would after a real run, using
adapter.py's own kind strings so this fails if adapter.py's kind
vocabulary drifts from the CHANGELOG "Artifacts" contract table. No
network, model, browser, or external API is touched.

Run: python tests/test_run_artifacts.py
"""

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
import server
import adapter

db.init()  # server's startup hook only fires inside TestClient's `with` block
client = TestClient(server.app)

# CHANGELOG.md "Artifacts": stored order (all seven) and public order
# (the six with a downloadUrl). kind -> (filename, contentType).
_ARTIFACT_CONTRACT = [
    ("report_pdf",       "report.pdf",         "application/pdf", True),
    ("comments_csv",     "comments.csv",       "text/csv",        True),
    ("key_messages_csv", "key-messages.csv",   "text/csv",        True),
    ("themes_csv",       "themes.csv",         "text/csv",        True),
    ("sentiment_csv",    "sentiment.csv",      "text/csv",        True),
    ("emotions_csv",     "emotions.csv",       "text/csv",        True),
    ("report_json",      "report.json",        "application/json", False),
]
_PUBLIC_CONTRACT = [row for row in _ARTIFACT_CONTRACT if row[3]]
_OLD_KINDS = {"summary_csv", "chart_transfer_csv", "chart_themes_csv"}

_EXPECTED_ARTIFACT_FILES = [
    ("report_pdf",        "report.pdf"),
    ("comments_csv",      "comments.csv"),
    ("key_messages_csv",  "key-messages.csv"),
    ("themes_csv",        "themes.csv"),
    ("sentiment_csv",     "sentiment.csv"),
    ("emotions_csv",      "emotions.csv"),
    ("report_json",       "report.json"),
]


def _new_session_with_run():
    sid = client.post("/api/sessions", json={"name": "S"}).json()["id"]
    conn = db.get_conn()
    try:
        rid = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO runs (id, session_id, state, stage) "
            "VALUES (?, ?, 'complete', 'complete')",
            (rid, sid),
        )
        conn.commit()
    finally:
        conn.close()
    return sid, rid


def _stub_content(kind):
    """report_json must be valid JSON: GET /runs/{id}/report json.load()s
    it directly. Every other kind is a plain-text stand-in for a binary
    or CSV blob; only its bytes and headers are checked."""
    if kind == "report_json":
        return '{"stub": "' + kind + '"}'
    return f"stub content for {kind}"


def _seed_all_artifacts(run_id):
    """Write one real file per contract kind under this run's artifacts
    dir and register it exactly as adapter._insert_artifact() would."""
    art_dir = storage.artifacts_dir(run_id)
    for kind, filename, _content_type, _public in _ARTIFACT_CONTRACT:
        path = os.path.join(art_dir, filename)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(_stub_content(kind))
        adapter._insert_artifact(run_id, kind, path)
    return art_dir


def _rows_for_run(run_id):
    conn = db.get_conn()
    try:
        return conn.execute(
            "SELECT * FROM run_artifacts WHERE run_id = ?", (run_id,)
        ).fetchall()
    finally:
        conn.close()


def test_seven_rows_registered_no_old_kinds():
    sid, rid = _new_session_with_run()
    _seed_all_artifacts(rid)

    rows = _rows_for_run(rid)
    assert len(rows) == 7, f"expected 7 run_artifacts rows, got {len(rows)}"

    kinds = {r["kind"] for r in rows}
    expected_kinds = {k for k, *_ in _ARTIFACT_CONTRACT}
    assert kinds == expected_kinds, (
        f"registered kinds {kinds} != contract kinds {expected_kinds}"
    )
    assert not (kinds & _OLD_KINDS), (
        f"old kinds still registered: {kinds & _OLD_KINDS}"
    )
    print("  ok  seven run_artifacts rows registered, exact kind set, no old kinds")


def test_six_public_artifacts_fixed_order_exact_fields():
    sid, rid = _new_session_with_run()
    _seed_all_artifacts(rid)

    snapshot = client.get(f"/api/runs/{rid}").json()
    artifacts = snapshot["artifacts"]

    assert len(artifacts) == 6, (
        f"expected 6 public artifacts on RunSnapshot, got {len(artifacts)}: {artifacts}"
    )

    for i, (art, (kind, filename, content_type, _public)) in enumerate(
        zip(artifacts, _PUBLIC_CONTRACT)
    ):
        assert art["kind"] == kind, (
            f"artifact {i}: kind {art['kind']!r} != expected {kind!r}"
        )
        assert art["filename"] == filename, (
            f"artifact {i} ({kind}): filename {art['filename']!r} != expected {filename!r}"
        )
        assert art["contentType"] == content_type, (
            f"artifact {i} ({kind}): contentType {art['contentType']!r} != expected {content_type!r}"
        )
        expected_url = f"/api/runs/{rid}/artifacts/{art['id']}"
        assert art["downloadUrl"] == expected_url, (
            f"artifact {i} ({kind}): downloadUrl {art['downloadUrl']!r} != expected {expected_url!r}"
        )
        assert set(art.keys()) == {"id", "kind", "filename", "contentType", "downloadUrl"}, (
            f"artifact {i} ({kind}): unexpected field set {sorted(art.keys())}"
        )

    public_kinds = {a["kind"] for a in artifacts}
    assert "report_json" not in public_kinds, "report_json leaked into RunSnapshot.artifacts"
    assert not (public_kinds & _OLD_KINDS), (
        f"old kinds leaked into RunSnapshot.artifacts: {public_kinds & _OLD_KINDS}"
    )
    print("  ok  six public artifacts in fixed order, exact kind/filename/contentType/downloadUrl, report_json hidden")


def test_public_download_serves_correct_blob():
    sid, rid = _new_session_with_run()
    _seed_all_artifacts(rid)

    snapshot = client.get(f"/api/runs/{rid}").json()
    for art, (kind, filename, content_type, _public) in zip(
        snapshot["artifacts"], _PUBLIC_CONTRACT
    ):
        resp = client.get(art["downloadUrl"])
        assert resp.status_code == 200, (
            f"download of {kind} failed: {resp.status_code} {resp.text}"
        )
        assert resp.content == _stub_content(kind).encode("utf-8"), (
            f"download of {kind} returned wrong bytes"
        )
        disposition = resp.headers.get("content-disposition", "")
        assert f'filename="{filename}"' in disposition, (
            f"download of {kind}: Content-Disposition {disposition!r} missing filename={filename!r}"
        )
        ctype = resp.headers.get("content-type", "")
        assert ctype.split(";")[0] == content_type, (
            f"download of {kind}: Content-Type {ctype!r} != expected {content_type!r}"
        )
    print("  ok  every public artifact downloads with correct bytes, filename, and MIME")


def test_report_json_hidden_but_report_endpoint_works():
    sid, rid = _new_session_with_run()
    _seed_all_artifacts(rid)

    snapshot = client.get(f"/api/runs/{rid}").json()
    public_kinds = {a["kind"] for a in snapshot["artifacts"]}
    assert "report_json" not in public_kinds

    resp = client.get(f"/api/runs/{rid}/report")
    assert resp.status_code == 200, f"GET /report failed: {resp.status_code} {resp.text}"
    assert resp.json() == {"stub": "report_json"}
    print("  ok  report_json absent from artifacts list, GET /runs/{id}/report still serves it")


def test_missing_artifact_returns_exact_404():
    sid, rid = _new_session_with_run()
    _seed_all_artifacts(rid)

    resp = client.get(f"/api/runs/{rid}/artifacts/{uuid.uuid4()}")
    assert resp.status_code == 404, resp.text
    assert resp.json() == {
        "error": "NOT_FOUND",
        "message": "Artifact not found.",
        "field": None,
    }
    print("  ok  unknown artifact id returns exact NOT_FOUND 404 shape")


def test_report_json_direct_download_returns_exact_404():
    """report_json is registered in run_artifacts like any other kind, but
    it has no downloadUrl on RunSnapshot.artifacts (see
    test_six_public_artifacts_fixed_order_exact_fields). Its row's id must
    still 404 through the generic artifact-download route with the same
    NOT_FOUND shape as an unknown id - report.json is only reachable via
    GET /runs/{id}/report, never via /artifacts/{id}."""
    sid, rid = _new_session_with_run()
    _seed_all_artifacts(rid)

    rows = _rows_for_run(rid)
    report_json_row = next(r for r in rows if r["kind"] == "report_json")

    resp = client.get(f"/api/runs/{rid}/artifacts/{report_json_row['id']}")
    assert resp.status_code == 404, resp.text
    assert resp.json() == {
        "error": "NOT_FOUND",
        "message": "Artifact not found.",
        "field": None,
    }
    print("  ok  direct download of report_json's own artifact id still 404s")


def test_partial_run_missing_artifact_raises_before_insert():
    """adapter._missing_required_artifacts (the pure helper _execute calls
    before copying/registering anything) must catch a run one file short
    of the seven required outputs - the exact boundary _execute relies on
    to never mark a run complete with a phantom artifact set."""
    out_dir = tempfile.mkdtemp()
    for _kind, filename in adapter._ARTIFACT_FILES[:-1]:
        with open(os.path.join(out_dir, filename), "w", encoding="utf-8") as fh:
            fh.write("stub")

    missing = adapter._missing_required_artifacts(out_dir)
    assert missing == [adapter._ARTIFACT_FILES[-1][1]], (
        f"expected only the last file missing, got {missing}"
    )
    print("  ok  _missing_required_artifacts flags the one absent file, six present")


def test_artifact_files_matches_contract():
    assert adapter._ARTIFACT_FILES == _EXPECTED_ARTIFACT_FILES, (
        f"adapter._ARTIFACT_FILES {adapter._ARTIFACT_FILES} != "
        f"expected {_EXPECTED_ARTIFACT_FILES}"
    )
    print("  ok  adapter._ARTIFACT_FILES matches the exact seven (kind, filename) pairs")


def test_legacy_kind_row_does_not_crash_run_snapshot():
    """A run_artifacts row with a kind outside _ARTIFACT_CONTRACT (a
    retired kind from before a schema change, or hand-inserted test data)
    must not raise KeyError out of GET /runs/{id}, and must never surface
    on RunSnapshot.artifacts."""
    sid, rid = _new_session_with_run()
    art_dir = storage.artifacts_dir(rid)
    path = os.path.join(art_dir, "legacy.csv")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("stub content for legacy_kind")
    adapter._insert_artifact(rid, "legacy_kind", path)

    resp = client.get(f"/api/runs/{rid}")
    assert resp.status_code == 200, f"GET /runs/{{id}} crashed: {resp.status_code} {resp.text}"
    public_kinds = {a["kind"] for a in resp.json()["artifacts"]}
    assert "legacy_kind" not in public_kinds, "unknown kind leaked into RunSnapshot.artifacts"
    print("  ok  legacy/unknown artifact kind does not crash GET /runs/{id} or leak into artifacts")


def main() -> None:
    tests = [
        test_seven_rows_registered_no_old_kinds,
        test_six_public_artifacts_fixed_order_exact_fields,
        test_public_download_serves_correct_blob,
        test_report_json_hidden_but_report_endpoint_works,
        test_missing_artifact_returns_exact_404,
        test_report_json_direct_download_returns_exact_404,
        test_partial_run_missing_artifact_raises_before_insert,
        test_artifact_files_matches_contract,
        test_legacy_kind_row_does_not_crash_run_snapshot,
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
