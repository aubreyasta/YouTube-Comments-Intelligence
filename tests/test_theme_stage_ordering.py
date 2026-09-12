"""
Issue #5: the run page must not mark theme discovery done before it
runs. adapter._execute() has to push a `themes` stage around
analyze.build() rather than reporting `classify` before that call
starts, or the frontend stepper ticks "Found what people are talking
about" as done while the LLM call behind it is still in flight.

Points db._DB_PATH and storage._ROOT at temp locations before importing
server (same reason as test_skip_pause.py), drives a run through
FastAPI's TestClient with every pipeline edge mocked except
analyze.build(), which is given an artificial delay standing in for
the real ~40s LM Studio call the bug report observed. Polls
GET /api/runs/{id} while that delay is running and asserts the stage
never reads `classify` before it elapses.

Run: python tests/test_theme_stage_ordering.py
"""

import os
import pathlib
import sys
import tempfile
import threading
import time
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
_ORIG_DB_PATH = db._DB_PATH
db._DB_PATH = pathlib.Path(tempfile.mkdtemp()) / "app.db"

import storage
_ORIG_STORAGE_ROOT = storage._ROOT
storage._ROOT = tempfile.mkdtemp()

os.environ.setdefault("APP_PASSWORD", "test-password")

from starlette.testclient import TestClient

import server
import adapter

db.init()
client = TestClient(server.app, headers={"Authorization": "Basic b2ZmaWNlOnRlc3QtcGFzc3dvcmQ="})

BUILD_SECONDS = 0.3


def _wait_until(pred, timeout=5.0, interval=0.02):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(interval)
    return False


def _new_session_with_video():
    sid = client.post("/api/sessions", json={"name": "S"}).json()["id"]
    cid = client.post(f"/api/sessions/{sid}/campaigns", json={"name": "C"}).json()["id"]
    client.post(f"/api/campaigns/{cid}/videos", json={"url": "https://youtu.be/abcdefghijk"})
    return sid, cid


def _fake_comments_df():
    return pd.DataFrame([
        {"video_id": "abcdefghijk", "group": "C", "comment": "Great value",
         "likes": 3, "reply_count": 0, "in_base": True, "theme": "Other",
         "emotion": "neutral", "sentiment": "positive"},
    ])


def _fake_meta_df():
    return pd.DataFrame([
        {"video_id": "abcdefghijk", "group": "C", "kind": "auto",
         "title": "t", "channel": "ch", "description": "d",
         "transcript": "We cut the price.", "has_transcript": True},
    ])


def _fake_affect_result():
    empty = pd.DataFrame()
    return {"emotion": {"table": empty, "caveat": ""},
            "sentiment": {"table": empty, "caveat": ""}}


def _reconcile(existing, meta_df, cfg, context_map=None, images_map=None,
               id_factory=None, include_grounded=False):
    reconciled = [{"id": "p1", "label": "Idea", "description": "d",
                   "included": True, "order": 0, "edited": False}]
    return ("GROUNDED", reconciled)


def _slow_build(df, summary, cfg):
    time.sleep(BUILD_SECONDS)
    return []


def _fake_render(markdown, out_dir, cfg, debug_dir=None, _df=None, _transfer=None):
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "report.pdf"), "wb") as f:
        f.write(b"%PDF-1.4 fake report\n")


def _fake_export(base_df, theme_table, transfer_table, affect_result, meta_df, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    for name in ("comments.csv", "key-messages.csv", "themes.csv",
                 "sentiment.csv", "emotions.csv"):
        with open(os.path.join(out_dir, name), "w", encoding="utf-8") as f:
            f.write("placeholder\n")


def _patched_pipeline():
    return (
        patch.object(adapter.pipeline_llm, "preflight", return_value=None),
        patch.object(adapter.collect, "fetch",
                    return_value=(_fake_comments_df(), _fake_meta_df())),
        patch.object(adapter.collect, "clean", side_effect=lambda df, cfg: df),
        patch.object(adapter.brief, "reconcile", side_effect=_reconcile),
        patch.object(adapter.analyze, "build", side_effect=_slow_build),
        patch.object(adapter.analyze, "classify",
                    side_effect=lambda df, themes, points, cfg, on_progress=None: (df, {})),
        patch.object(adapter.analyze, "extend",
                    side_effect=lambda df, themes, points, summary, cfg, on_progress=None: (df, themes, 0.0)),
        patch.object(adapter.analyze, "affect",
                    side_effect=lambda df, cfg: (df, _fake_affect_result())),
        patch.object(adapter.pipeline_report, "write", return_value="# report"),
        patch.object(adapter.pipeline_report, "render", side_effect=_fake_render),
        patch.object(adapter.pipeline_report, "export", side_effect=_fake_export),
        patch.object(adapter, "_build_prose",
                    return_value={"title": "t", "interpretation": "i",
                                  "quote": {"text": "q", "attr": "a"}, "caveat": "c"}),
    )


def _wait_for_run_thread(run_id, timeout=5.0):
    name = f"adapter-run-{run_id[:8]}"
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not any(t.name == name for t in threading.enumerate()):
            return True
        time.sleep(0.02)
    return False


def _stop(patches, run_id):
    adapter.get_proceed_event(run_id).set()
    _wait_for_run_thread(run_id)
    for p in patches:
        p.stop()


def test_classify_stage_never_reported_before_theme_discovery_returns():
    session_id, _ = _new_session_with_video()

    patches = _patched_pipeline()
    for p in patches:
        p.start()
    run_id = None
    try:
        resp = client.post(f"/api/sessions/{session_id}/runs")
        assert resp.status_code == 202, resp.text
        run_id = resp.json()["id"]

        assert _wait_until(
            lambda: client.get(f"/api/runs/{run_id}").json()["stage"] == "brief_pause")

        t0 = time.monotonic()
        client.post(f"/api/runs/{run_id}/proceed")

        seen_themes = False
        while time.monotonic() - t0 < BUILD_SECONDS:
            stage = client.get(f"/api/runs/{run_id}").json()["stage"]
            assert stage != "classify", (
                f"stage reported 'classify' after only "
                f"{time.monotonic() - t0:.3f}s, but analyze.build() (theme "
                f"discovery) sleeps for {BUILD_SECONDS}s before returning - "
                "the stepper would tick 'Found what people are talking "
                "about' as done before it ran")
            if stage == "themes":
                seen_themes = True
            time.sleep(0.01)
        assert seen_themes, "stage never reported 'themes' while analyze.build() was running"

        assert _wait_until(
            lambda: client.get(f"/api/runs/{run_id}").json()["status"] in ("complete", "failed"),
            timeout=10.0), "run never reached a terminal status after analyze.build() returned"
        final = client.get(f"/api/runs/{run_id}").json()
        assert final["status"] == "complete", (
            f"run finished with status {final['status']!r} at stage "
            f"{final['stage']!r}: {final.get('error')!r}")
    finally:
        _stop(patches, run_id)
    print("  ok  stage reads 'themes' for the full duration of analyze.build() "
          "and only advances to 'classify' once it returns")


def main():
    tests = [test_classify_stage_never_reported_before_theme_discovery_returns]
    failed = 0
    for fn in tests:
        try:
            fn()
        except AssertionError as exc:
            print(f"  FAIL {fn.__name__}: {exc}")
            failed += 1
    if failed:
        print(f"\nFAIL ({failed}/{len(tests)} failed)")
        return 1
    print(f"\nPASS ({len(tests)}/{len(tests)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
