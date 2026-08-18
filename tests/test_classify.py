"""Offline checks for atomic comment classification and summaries.

Run: python tests/test_classify.py
"""

import inspect
import os
import re
import sys
import tempfile

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import analyze, llm, report
from pipeline.config_types import PipelineConfig


THEMES = [
    {"name": "Quality", "definition": "Comments about product quality."},
    {"name": "Price", "definition": "Comments about cost or value."},
]
COMMENTS = [
    {"video_id": "video_1", "group": "G1", "comment": "Great build quality",
     "likes": 10, "reply_count": 2},
    {"video_id": "video_1", "group": "G1", "comment": "Worth the price",
     "likes": 5, "reply_count": 0},
    {"video_id": "video_2", "group": "G2", "comment": "Built to last",
     "likes": 12, "reply_count": 3},
    {"video_id": "video_2", "group": "G2", "comment": "Too expensive",
     "likes": 4, "reply_count": 0},
]
DF = pd.DataFrame(COMMENTS)
POINTS = [
    {"video_id": "video_1", "group": "G1", "label": "value for money",
     "description": "The product earns its price."},
    {"video_id": "video_2", "group": "G2", "label": "durability",
     "description": "The product is built to last."},
]
CANNED = {
    0: {"theme": "Quality", "echoed": []},
    1: {"theme": "Price", "echoed": ["value for money"]},
    2: {"theme": "Quality", "echoed": ["durability"]},
    3: {"theme": "Price", "echoed": []},
}


def make_cfg(batch_size=1):
    values = {
        "YOUTUBE_API_KEY": "test", "OLLAMA_BASE_URL": "http://127.0.0.1:11434",
        "TEXT_MODEL": "qwen3:8b", "VISION_MODEL": "qwen3-vl:8b",
        "OLLAMA_TEXT_NUM_CTX": 32768, "OLLAMA_VISION_NUM_CTX": 16384,
        "OLLAMA_TIMEOUT_SECONDS": 2, "OLLAMA_KEEP_ALIVE": "5m",
        "VIDEOS": [], "SESSION_NAME": "test", "OUTPUT_DIR": "output",
        "KEEP_LANGUAGES": {"en"}, "MIN_COMMENT_LETTERS": 4,
        "MAX_COMMENTS_PER_VIDEO": 100, "CODEBOOK_SAMPLE_SIZE": 10,
        "CODEBOOK_SAMPLE_MAX": 50, "CLASSIFY_BATCH_SIZE": batch_size,
        "UNCLASSIFIED_LIMIT": 30, "REPORT_LANGUAGE": "English",
        "CAMPAIGN_CONTEXT": "",
    }
    parameters = inspect.signature(PipelineConfig).parameters
    missing = [name for name in values if name not in parameters]
    assert not missing, f"PipelineConfig missing fields: {missing}"
    return PipelineConfig(**{name: value for name, value in values.items()
                             if name in parameters})


def canned_batch(prompt, expected_indices, theme_names, point_labels, cfg):
    indices = [int(match.group(1)) for line in prompt.splitlines()
               if (match := re.match(r"^(\d+):", line.strip()))]
    return [{"index": index, **CANNED[index]} for index in indices]


def test_exact_coverage_and_video_specific_points():
    original = llm.classify_batch
    seen_labels = []

    def recording_batch(prompt, expected_indices, theme_names, point_labels, cfg):
        seen_labels.append(tuple(point_labels))
        return canned_batch(prompt, expected_indices, theme_names, point_labels, cfg)

    llm.classify_batch = recording_batch
    try:
        out, columns = analyze.classify(DF, THEMES, POINTS, make_cfg(2))
    finally:
        llm.classify_batch = original
    assert out["theme"].tolist() == ["Quality", "Price", "Quality", "Price"]
    assert seen_labels == [("value for money",), ("durability",)]
    assert out.loc[:1, "pt__durability"].isna().all()
    assert out.loc[2:, "pt__value_for_money"].isna().all()
    assert columns == {"pt__value_for_money": "value for money",
                       "pt__durability": "durability"}


def test_omitted_index_fails_atomically():
    original = llm.classify_batch
    llm.classify_batch = lambda prompt, indices, themes, points, cfg: canned_batch(
        prompt, indices, themes, points, cfg)[:-1]
    try:
        try:
            analyze.classify(DF, THEMES, POINTS, make_cfg(2))
        except Exception as exc:
            assert "index" in str(exc).lower() or "coverage" in str(exc).lower(), str(exc)
        else:
            raise AssertionError("omitted index became Other instead of failing")
    finally:
        llm.classify_batch = original


def test_progress_reports_completed_and_total_batches():
    original = llm.classify_batch
    llm.classify_batch = canned_batch
    progress = []
    try:
        analyze.classify(DF, THEMES, POINTS, make_cfg(1),
                         on_progress=lambda completed, total:
                         progress.append((completed, total)))
    finally:
        llm.classify_batch = original
    assert progress == [(1, 4), (2, 4), (3, 4), (4, 4)], progress


def test_summarise_percentages():
    original = llm.classify_batch
    llm.classify_batch = canned_batch
    try:
        out, columns = analyze.classify(DF, THEMES, POINTS, make_cfg(2))
    finally:
        llm.classify_batch = original
    themes, mentions = analyze.summarise(out, columns)
    assert themes.loc["G1", "Quality"] == 50.0
    assert themes.loc["G2", "Price"] == 50.0
    assert mentions.set_index("point").loc["value for money", "echoed_pct"] == 50.0
    assert mentions.set_index("point").loc["durability", "echoed_pct"] == 50.0


def test_report_quotes_and_export_survive_pdna_pt_columns():
    """
    Regression: a multi-video single Campaign leaves pt__ columns with
    pd.NA on rows whose video is not associated with that Key Message
    (see analyze.classify). report._quotes and report.export used to
    truth-test those cells directly (`if row.get(c):` / `if row[c]`),
    which raises "boolean value of NA is ambiguous". Reproduces the real
    dtype/values analyze.classify() produces (object dtype, Python bool
    or pd.NA), not a synthetic all-bool frame.
    """
    original = llm.classify_batch
    llm.classify_batch = canned_batch
    try:
        out, columns = analyze.classify(DF, THEMES, POINTS, make_cfg(2))
    finally:
        llm.classify_batch = original

    # Confirm the fixture actually exercises pd.NA, not just False, so
    # this test fails loudly if analyze.classify's dtype behaviour changes.
    assert out["pt__durability"].iloc[:2].isna().all()
    assert out["pt__value_for_money"].iloc[2:].isna().all()

    # report._quotes must not raise on pd.NA cells.
    quotes = report._quotes(out)
    assert isinstance(quotes, str)
    assert "value for money" in quotes  # row 1 echoed it
    assert "durability" in quotes       # row 2 echoed it

    themes, transfer = analyze.summarise(out, columns)
    affect_result = {
        "emotion": {"table": pd.DataFrame(), "caveat": ""},
        "sentiment": {"table": pd.DataFrame(), "caveat": ""},
    }
    meta_df = pd.DataFrame([
        {"group": "G1", "video_id": "video_1"},
        {"group": "G2", "video_id": "video_2"},
    ])

    with tempfile.TemporaryDirectory() as out_dir:
        report.export(out, themes, transfer, affect_result, meta_df, out_dir)
        # keep_default_na=False: an empty key_message_* cell is a real
        # "not applicable" pd.NA, not a missing value, so it must not
        # round-trip through CSV as NaN.
        comments = pd.read_csv(os.path.join(out_dir, "comments.csv"),
                               keep_default_na=False)
        assert len(comments) == len(out)
        by_comment = comments.set_index("comment")
        # video_1's batch only carries "value for money"; durability is
        # not applicable there, so that column stays blank (NA).
        assert by_comment.loc["Great build quality", "key_message_value_for_money"] == "False"
        assert by_comment.loc["Great build quality", "key_message_durability"] == ""
        assert by_comment.loc["Worth the price", "key_message_value_for_money"] == "True"
        assert by_comment.loc["Worth the price", "key_message_durability"] == ""
        # video_2's batch only carries "durability"; value for money is
        # not applicable there.
        assert by_comment.loc["Built to last", "key_message_durability"] == "True"
        assert by_comment.loc["Built to last", "key_message_value_for_money"] == ""
        assert by_comment.loc["Too expensive", "key_message_durability"] == "False"
        assert by_comment.loc["Too expensive", "key_message_value_for_money"] == ""


def test_comments_csv_header_order_and_empty_input():
    """
    comments.csv: exact header/column order per the CSV contract
    (PRD.md), and empty input still writes headers with no data
    rows.
    """
    import csv as csv_module

    themes, transfer = pd.DataFrame(), pd.DataFrame()
    affect_result = {
        "emotion": {"table": pd.DataFrame(), "caveat": ""},
        "sentiment": {"table": pd.DataFrame(), "caveat": ""},
    }
    df = pd.DataFrame([
        {"video_id": "video_1", "group": "G1", "comment": "Hello",
         "likes": 3, "lang": "en", "theme": "Quality",
         "sentiment": "positive", "sentiment_confidence": 0.9,
         "emotion": "joy", "emotion_confidence": 0.8},
    ])
    meta_df = pd.DataFrame([{"group": "G1", "video_id": "video_1"}])

    with tempfile.TemporaryDirectory() as out_dir:
        report.export(df, themes, transfer, affect_result, meta_df, out_dir)
        path = os.path.join(out_dir, "comments.csv")
        with open(path, newline="", encoding="utf-8-sig") as fh:
            header = next(csv_module.reader(fh))
    assert header == [
        "video_id", "group", "comment", "likes", "language", "theme",
        "sentiment", "sentiment_confidence", "emotion",
        "emotion_confidence"], f"comments.csv header mismatch: {header}"

    empty_df = pd.DataFrame(columns=[
        "video_id", "group", "comment", "likes", "lang", "theme",
        "sentiment", "sentiment_confidence", "emotion",
        "emotion_confidence"])
    empty_meta = pd.DataFrame(columns=["group", "video_id"])
    with tempfile.TemporaryDirectory() as out_dir:
        report.export(empty_df, themes, transfer, affect_result, empty_meta,
                      out_dir)
        path = os.path.join(out_dir, "comments.csv")
        with open(path, newline="", encoding="utf-8-sig") as fh:
            reader = csv_module.reader(fh)
            empty_header = next(reader)
            data_rows = list(reader)
    assert empty_header == header, (
        f"comments.csv header mismatch on empty input: {empty_header}")
    assert data_rows == [], f"expected no data rows, got {data_rows}"


def test_classification_schema_empty_points_forbids_enum():
    """
    A video with no Key Messages must emit an "echoed" schema that only
    permits []. enum: [] would be unsatisfiable by any non-empty array
    and unsatisfiable is not the same as "must be empty" for every
    structured-output engine; maxItems: 0 says the latter unambiguously.
    """
    schema = llm.classification_schema(["Quality"], [])
    echoed = schema["items"]["properties"]["echoed"]
    assert echoed.get("maxItems") == 0
    assert "enum" not in echoed

    populated = llm.classification_schema(["Quality"], ["value for money"])
    assert populated["items"]["properties"]["echoed"]["items"]["enum"] == ["value for money"]

    # validate_classification already rejects any echoed label when none
    # are allowed for this batch - confirms schema and validator agree.
    try:
        llm.validate_classification(
            [{"index": 0, "theme": "Quality", "echoed": ["nope"]}],
            [0], ["Quality"], [])
    except ValueError:
        pass
    else:
        raise AssertionError("validate_classification allowed an echoed "
                             "label with an empty point_labels list")


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items())
             if name.startswith("test_") and callable(value)]
    failures = 0
    for test in tests:
        try:
            test()
            print(f"  ok  {test.__name__}")
        except Exception as exc:
            failures += 1
            print(f"  FAIL {test.__name__}: {type(exc).__name__}: {exc}")
    if failures:
        print(f"\nFAIL ({failures}/{len(tests)} failed)")
        sys.exit(1)
    print(f"\nPASS ({len(tests)}/{len(tests)})")
