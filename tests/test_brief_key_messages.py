"""Offline checks for the Session-level Key Message split in pipeline/brief.py.

Covers draft_from_inputs() (User Inputs only, no transcripts) and
reconcile() (existing list vs. transcript-derived points): stable ids on
edited/matched entries, empty-existing-list drafting, and the
no-source-yields-empty-list case. Mocks llm.ask_json and
llm.extract_image_context; no network, no model calls.

Run: python tests/test_brief_key_messages.py
"""

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import brief, llm
from pipeline.config_types import PipelineConfig

_KEY_MESSAGE_KEYS = {"id", "label", "description", "included", "order", "edited"}


def make_cfg():
    return PipelineConfig(
        YOUTUBE_API_KEY="test", OLLAMA_BASE_URL="http://127.0.0.1:11434",
        MODEL="qwen3.5:4b", OLLAMA_NUM_CTX=32768,
        OLLAMA_TIMEOUT_SECONDS=2, OLLAMA_KEEP_ALIVE="5m",
        VIDEOS=[], SESSION_NAME="test", OUTPUT_DIR="output",
        KEEP_LANGUAGES={"en"}, MIN_COMMENT_LETTERS=4,
        MAX_COMMENTS_PER_VIDEO=100, CODEBOOK_SAMPLE_SIZE=10,
        CODEBOOK_SAMPLE_MAX=50, CLASSIFY_BATCH_SIZE=25,
        UNCLASSIFIED_LIMIT=30, REPORT_LANGUAGE="English",
        CAMPAIGN_CONTEXT="",
    )


def make_meta_df():
    return pd.DataFrame([
        {"group": "G1", "kind": "explainer", "video_id": "v1",
         "title": "Launch video", "channel": "Brand", "description": "d",
         "transcript": "We cut the price and it lasts longer.",
         "has_transcript": True},
    ])


def _patch(monkeypatch_ask_json=None, monkeypatch_extract=None):
    """Return (restore) after swapping llm.ask_json / llm.extract_image_context."""
    original_ask_json = llm.ask_json
    original_extract = llm.extract_image_context
    if monkeypatch_ask_json is not None:
        llm.ask_json = monkeypatch_ask_json
    if monkeypatch_extract is not None:
        llm.extract_image_context = monkeypatch_extract

    def restore():
        llm.ask_json = original_ask_json
        llm.extract_image_context = original_extract
    return restore


def test_draft_from_inputs_excludes_transcripts():
    """draft_from_inputs() must never see meta_df/transcripts: the fake
    ask_json asserts the prompt contains only the supplied material."""
    seen = {}

    def fake_ask_json(prompt, cfg, **kwargs):
        seen["prompt"] = prompt
        return {
            "summary": "s",
            "points": [{"label": "Lower price", "video_id": "material",
                       "description": "Cheaper than before."}],
        }

    restore = _patch(monkeypatch_ask_json=fake_ask_json)
    try:
        result = brief.draft_from_inputs(
            "Our new price is $10, down from $15.", [], make_cfg())
    finally:
        restore()

    assert "We cut the price and it lasts longer" not in seen["prompt"], \
        "draft_from_inputs must not see transcript text"
    assert "$10" in seen["prompt"]
    assert len(result) == 1
    assert set(result[0].keys()) == _KEY_MESSAGE_KEYS, result[0].keys()
    assert result[0]["label"] == "Lower price"
    assert result[0]["included"] is True
    assert result[0]["order"] == 0
    assert result[0]["edited"] is False
    print("  ok  draft_from_inputs excludes transcripts, returns KeyMessage shape")


def test_draft_from_inputs_empty_source_yields_empty_list():
    def fail_ask_json(*args, **kwargs):
        raise AssertionError("ask_json must not be called with no source")

    restore = _patch(monkeypatch_ask_json=fail_ask_json)
    try:
        result = brief.draft_from_inputs("", [], make_cfg())
    finally:
        restore()
    assert result == []
    print("  ok  draft_from_inputs with no text/images returns [] without a model call")


def test_reconcile_empty_existing_drafts_from_transcripts():
    def fake_ask_json(prompt, cfg, **kwargs):
        return {
            "summary": "s",
            "points": [{"label": "Durability", "video_id": "v1",
                       "description": "Lasts longer, per the transcript."}],
        }

    restore = _patch(monkeypatch_ask_json=fake_ask_json)
    try:
        result = brief.reconcile([], make_meta_df(), make_cfg())
    finally:
        restore()

    assert len(result) == 1
    assert set(result[0].keys()) == _KEY_MESSAGE_KEYS, result[0].keys()
    assert result[0]["label"] == "Durability"
    assert result[0]["order"] == 0
    print("  ok  reconcile with an empty existing list drafts the initial list "
          "from transcripts")


def test_reconcile_preserves_edited_entry_and_stable_id_on_match():
    def fake_ask_json(prompt, cfg, **kwargs):
        return {
            "summary": "s",
            "points": [
                {"label": "Lower price", "video_id": "v1",
                 "description": "Fresh transcript description."},
                {"label": "Durability", "video_id": "v1",
                 "description": "Also mentioned in the transcript."},
            ],
        }

    existing = [
        {"id": "id-edited", "label": "Lower price",
         "description": "User's own wording, do not overwrite.",
         "included": True, "order": 0, "edited": True},
        {"id": "id-matched", "label": "Durability",
         "description": "Stale description from a prior draft.",
         "included": True, "order": 1, "edited": False},
    ]

    restore = _patch(monkeypatch_ask_json=fake_ask_json)
    try:
        result = brief.reconcile(existing, make_meta_df(), make_cfg())
    finally:
        restore()

    by_id = {m["id"]: m for m in result}
    assert len(result) == 2, result

    edited = by_id["id-edited"]
    assert edited["label"] == "Lower price"
    assert edited["description"] == "User's own wording, do not overwrite."
    assert edited["edited"] is True

    matched = by_id["id-matched"]
    assert matched["label"] == "Durability"
    assert matched["description"] == "Also mentioned in the transcript."
    print("  ok  reconcile preserves an edited entry verbatim and keeps the "
          "stable id + refreshed description on an unedited match")


def test_reconcile_no_source_yields_empty_list():
    def fake_ask_json(prompt, cfg, **kwargs):
        return {"summary": "s", "points": []}

    empty_meta = pd.DataFrame([
        {"group": "G1", "kind": "explainer", "video_id": "v1",
         "title": "t", "channel": "c", "description": "d",
         "transcript": "", "has_transcript": False},
    ])

    restore = _patch(monkeypatch_ask_json=fake_ask_json)
    try:
        result = brief.reconcile([], empty_meta, make_cfg())
    finally:
        restore()
    assert result == []
    print("  ok  reconcile with no existing list and no transcript-derived "
          "points returns []")


if __name__ == "__main__":
    tests = [
        test_draft_from_inputs_excludes_transcripts,
        test_draft_from_inputs_empty_source_yields_empty_list,
        test_reconcile_empty_existing_drafts_from_transcripts,
        test_reconcile_preserves_edited_entry_and_stable_id_on_match,
        test_reconcile_no_source_yields_empty_list,
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
