"""
Stand-ins for adapter._execute()'s pipeline edges, shared by the test files
that need a run to execute for real without a network, a model, or a PDF
render.

Nothing here touches db._DB_PATH or storage._ROOT. Each test file still
points those at its own temp dir at import, which is the contract
conftest.py restores per file; these helpers only patch attributes on
adapter and read nothing at import time.

Not a test file. `pytest tests/` collects no tests from it.
"""

import os
import threading
import time
import uuid
from unittest.mock import patch

import pandas as pd

import adapter


def fake_comments_df():
    return pd.DataFrame([
        {"video_id": "abcdefghijk", "group": "C", "comment": "Great value",
         "likes": 3, "reply_count": 0, "in_base": True, "theme": "Other",
         "emotion": "neutral", "sentiment": "positive"},
    ])


def fake_meta_df():
    return pd.DataFrame([
        {"video_id": "abcdefghijk", "group": "C", "kind": "auto",
         "title": "t", "channel": "ch", "description": "d",
         "transcript": "We cut the price.", "has_transcript": True},
    ])


def fake_affect_result():
    empty_table = pd.DataFrame()
    return {
        "emotion": {"table": empty_table, "caveat": ""},
        "sentiment": {"table": empty_table, "caveat": ""},
    }


def fake_render(markdown, out_dir, cfg, debug_dir, _df=None, _transfer=None):
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "report.pdf"), "wb") as f:
        f.write(b"%PDF-1.4 fake report\n")


def fake_export(base_df, theme_table, transfer_table, affect_result, meta_df, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    for name in ("comments.csv", "key-messages.csv", "themes.csv",
                 "sentiment.csv", "emotions.csv"):
        with open(os.path.join(out_dir, name), "w", encoding="utf-8") as f:
            f.write("placeholder\n")


def patched_pipeline(reconcile_side_effect):
    """Context managers covering every pipeline edge _execute() touches,
    including every stage after brief/brief_pause, so the run reaches
    complete deterministically with no network or model call."""
    fake_df = fake_comments_df()
    return (
        patch.object(adapter.pipeline_llm, "preflight", return_value=None),
        patch.object(adapter.collect, "fetch",
                     return_value=(fake_df, fake_meta_df())),
        patch.object(adapter.collect, "clean", side_effect=lambda df, cfg: df),
        patch.object(adapter.brief, "reconcile", side_effect=reconcile_side_effect),
        patch.object(adapter.analyze, "build", return_value=[]),
        patch.object(adapter.analyze, "classify",
                     side_effect=lambda df, themes, points, cfg, on_progress=None: (df, {})),
        patch.object(adapter.analyze, "extend",
                     side_effect=lambda df, themes, points, summary, cfg, on_progress=None: (df, themes, 0.0)),
        patch.object(adapter.analyze, "affect",
                     side_effect=lambda df, cfg: (df, fake_affect_result())),
        patch.object(adapter.pipeline_report, "write", return_value="# report"),
        patch.object(adapter.pipeline_report, "render", side_effect=fake_render),
        patch.object(adapter.pipeline_report, "export", side_effect=fake_export),
        patch.object(adapter, "_build_prose",
                     return_value={"title": "t", "interpretation": "i",
                                   "quote": {"text": "q", "attr": "a"},
                                   "caveat": "c"}),
    )


def run_reconcile_of(proposals, included=True):
    """A drop-in for brief.reconcile(..., include_grounded=True) that
    ignores its transcript/context arguments and returns fixed points.
    `included` is parametrized so an all-excluded list can be produced
    (skip_pause must still pause with zero included points)."""
    def fn(existing, meta_df, cfg, context_map=None, images_map=None,
           id_factory=uuid.uuid4, include_grounded=False):
        reconciled = [
            {"id": str(id_factory()), "label": lbl, "description": desc,
             "included": included, "order": i, "edited": False}
            for i, (lbl, desc) in enumerate(proposals)
        ]
        return ("GROUNDED", reconciled) if include_grounded else reconciled
    return fn


def wait_for_run_thread(run_id, timeout=5.0):
    """Persisted terminal status can precede daemon thread exit.
    Wait for the thread before teardown removes active pipeline mocks."""
    name = f"adapter-run-{run_id[:8]}"
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not any(t.name == name for t in threading.enumerate()):
            return True
        time.sleep(0.02)
    return False
