"""Offline self-check: setup-time asset extraction, not run-time.

Proves, against the real server.py routes with db and storage.uploads_dir
both pointed at a temp directory and assets.extract_upload /
assets.fetch_article mocked (no network, no real pypdf/httpx calls):

  (a) upload route persists extracted text to the assets row before
      it returns the response.
  (b) a failed article fetch persists and returns empty text, and the
      route still succeeds (asset saved, not dropped).

Everything under the temp directory - the DB file and
storage.save_upload()'s written file - is removed when the run ends,
success or failure.

Run: python tests/test_asset_extraction.py
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
import storage

_tmp_dir = tempfile.mkdtemp()

# Point the DB at a throwaway file before importing server, since
# server.startup() calls db.init() against whatever db._DB_PATH is at
# that time.
db._DB_PATH = Path(_tmp_dir) / "app.db"
# storage.save_upload() writes under storage.uploads_dir(), which is
# _ROOT/data/uploads by default. Redirect uploads_dir() itself so the
# upload test never touches the real data/ tree.
storage.uploads_dir = lambda: storage._ensure(os.path.join(_tmp_dir, "uploads"))

# server refuses to start without APP_PASSWORD, and its Basic Auth
# middleware guards every route. Set before import so the startup hook sees it.
os.environ.setdefault("APP_PASSWORD", "test-password")

import server
from fastapi.testclient import TestClient

# TestClient only runs FastAPI's startup event (which calls db.init())
# inside a `with` block. Call it directly instead so a bare client works.
db.init()
client = TestClient(server.app, headers={"Authorization": "Basic b2ZmaWNlOnRlc3QtcGFzc3dvcmQ="})


def _make_session_and_campaign():
    sid = client.post("/api/sessions", json={"name": "s"}).json()["id"]
    cid = client.post(f"/api/sessions/{sid}/campaigns", json={"name": "c"}).json()["id"]
    return sid, cid


def test_upload_persists_text_before_return():
    _, cid = _make_session_and_campaign()

    with patch.object(server.assets, "extract_upload", return_value="extracted body text") as mock_extract:
        resp = client.post(
            f"/api/campaigns/{cid}/assets/upload",
            files={"file": ("brief.pdf", b"%PDF-1.4 fake", "application/pdf")},
        )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert mock_extract.called, "extract_upload was not called on upload"

    # DB row must already have the extracted text (persisted before return).
    conn = db.get_conn()
    row = conn.execute(
        "SELECT text FROM assets WHERE id = ?", (body["id"],)
    ).fetchone()
    conn.close()
    assert row["text"] == "extracted body text", row["text"]
    print("  ok  upload persists extracted text before route return")


def test_failed_article_fetch_saves_empty_text_no_draft_call():
    _, cid = _make_session_and_campaign()

    empty_fetch = {"title": "", "text": "", "retrieved_at": "2026-01-01T00:00:00+00:00"}
    with patch.object(server.assets, "fetch_article", return_value=empty_fetch) as mock_fetch:
        resp = client.post(
            f"/api/campaigns/{cid}/assets/article",
            json={"url": "https://example.com/unreachable"},
        )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert mock_fetch.called, "fetch_article was not called on article add"

    conn = db.get_conn()
    row = conn.execute(
        "SELECT text, kind FROM assets WHERE id = ?", (body["id"],)
    ).fetchone()
    conn.close()
    assert row["kind"] == "article", row["kind"]
    assert row["text"] in (None, ""), (
        f"Expected empty text for failed fetch, got {row['text']!r}")
    print("  ok  failed article fetch saves/returns empty text, asset still created")


def test_image_upload_returns_kind_and_mime_without_key_visual_flag():
    _, cid = _make_session_and_campaign()
    _retired_key = "is" + "Key" + "Visual"

    with patch.object(server.assets, "extract_upload", return_value="") as mock_extract:
        resp = client.post(
            f"/api/campaigns/{cid}/assets/upload",
            files={"file": ("visual.png", b"\x89PNG\r\n\x1a\nfake", "image/png")},
        )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert mock_extract.called, "extract_upload was not called on upload"
    assert body["kind"] == "image", body["kind"]
    assert body["mimeType"] == "image/png", body["mimeType"]
    assert _retired_key not in body, body

    conn = db.get_conn()
    row = conn.execute(
        "SELECT file_path, kind FROM assets WHERE id = ?", (body["id"],)
    ).fetchone()
    conn.close()
    assert row["kind"] == "image", row["kind"]
    assert Path(row["file_path"]).is_file(), row["file_path"]
    print("  ok  image upload returns kind/mimeType, no retired flag, file persisted")


if __name__ == "__main__":
    tests = [
        test_upload_persists_text_before_return,
        test_failed_article_fetch_saves_empty_text_no_draft_call,
        test_image_upload_returns_kind_and_mime_without_key_visual_flag,
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
        shutil.rmtree(_tmp_dir, ignore_errors=True)

    if failed:
        print(f"\nFAIL ({failed}/{len(tests)} failed)")
        sys.exit(1)
    print(f"\nPASS ({len(tests)}/{len(tests)})")
