"""
Offline self-check for report.export's sentiment.csv and emotions.csv
output. No API calls, no real models.

Run: python tests/test_report_sentiment_emotions_csv.py
"""

import csv
import os
import sys
import tempfile

repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, repo_root)

import pandas as pd

from pipeline import report

EMPTY_TABLE = pd.DataFrame()
AFFECT_EMPTY = {
    "emotion":   {"table": pd.DataFrame()},
    "sentiment": {"table": pd.DataFrame()},
}


def _read_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def test_sentiment_and_emotions_csv_rows_and_math():
    df = pd.DataFrame([
        {"group": "G1", "sentiment": "Positive", "emotion": "Joy",
         "video_id": "v1", "likes": 1},
        {"group": "G1", "sentiment": "Positive", "emotion": "Joy",
         "video_id": "v1", "likes": 2},
        {"group": "G1", "sentiment": "Negative", "emotion": None,
         "video_id": "v1", "likes": 3},
        {"group": "G2", "sentiment": "Positive", "emotion": "Anger",
         "video_id": "v2", "likes": 4},
    ])
    meta_df = pd.DataFrame([
        {"group": "G1", "video_id": "v1"},
        {"group": "G2", "video_id": "v2"},
    ])
    with tempfile.TemporaryDirectory() as out_dir:
        report.export(df, EMPTY_TABLE, pd.DataFrame(), AFFECT_EMPTY,
                      meta_df, out_dir)
        sentiment_rows = _read_csv(os.path.join(out_dir, "sentiment.csv"))
        emotion_rows = _read_csv(os.path.join(out_dir, "emotions.csv"))

    assert set(sentiment_rows[0].keys()) == {
        "group", "sentiment", "count", "percent", "base_n"}, (
        f"sentiment header mismatch: {sentiment_rows[0].keys()}")
    assert set(emotion_rows[0].keys()) == {
        "group", "emotion", "count", "percent", "base_n"}, (
        f"emotion header mismatch: {emotion_rows[0].keys()}")

    # Sentiment: G1 has 3 labeled rows (base_n=3), G2 has 1 (base_n=1).
    by_key = {(r["group"], r["sentiment"]): r for r in sentiment_rows}
    g1_pos = by_key[("G1", "Positive")]
    assert g1_pos["count"] == "2"
    assert g1_pos["base_n"] == "3"
    assert g1_pos["percent"] == "66.7"

    g1_neg = by_key[("G1", "Negative")]
    assert g1_neg["count"] == "1"
    assert g1_neg["base_n"] == "3"
    assert g1_neg["percent"] == "33.3"

    g2_pos = by_key[("G2", "Positive")]
    assert g2_pos["count"] == "1"
    assert g2_pos["base_n"] == "1"
    assert g2_pos["percent"] == "100.0"

    # Emotion: G1's null emotion row is excluded, so base_n=2, not 3.
    e_by_key = {(r["group"], r["emotion"]): r for r in emotion_rows}
    g1_joy = e_by_key[("G1", "Joy")]
    assert g1_joy["count"] == "2"
    assert g1_joy["base_n"] == "2"
    assert g1_joy["percent"] == "100.0"
    assert ("G1", "") not in e_by_key

    g2_anger = e_by_key[("G2", "Anger")]
    assert g2_anger["count"] == "1"
    assert g2_anger["base_n"] == "1"
    assert g2_anger["percent"] == "100.0"
    print(f"  ok  sentiment.csv and emotions.csv rows and percent math")


def test_sentiment_and_emotions_csv_empty_input_still_has_headers():
    df = pd.DataFrame(columns=["group", "sentiment", "emotion",
                               "video_id", "likes"])
    meta_df = pd.DataFrame(columns=["group", "video_id"])
    with tempfile.TemporaryDirectory() as out_dir:
        report.export(df, EMPTY_TABLE, pd.DataFrame(), AFFECT_EMPTY,
                      meta_df, out_dir)
        sentiment_path = os.path.join(out_dir, "sentiment.csv")
        emotions_path = os.path.join(out_dir, "emotions.csv")
        with open(sentiment_path, newline="", encoding="utf-8-sig") as fh:
            sentiment_header = next(csv.reader(fh))
        with open(emotions_path, newline="", encoding="utf-8-sig") as fh:
            emotions_header = next(csv.reader(fh))
        sentiment_rows = _read_csv(sentiment_path)
        emotion_rows = _read_csv(emotions_path)

    assert sentiment_header == [
        "group", "sentiment", "count", "percent", "base_n"], (
        f"sentiment header mismatch on empty input: {sentiment_header}")
    assert emotions_header == [
        "group", "emotion", "count", "percent", "base_n"], (
        f"emotion header mismatch on empty input: {emotions_header}")
    assert sentiment_rows == [], f"expected no rows, got {sentiment_rows}"
    assert emotion_rows == [], f"expected no rows, got {emotion_rows}"
    print("  ok  empty input still writes sentiment.csv/emotions.csv headers")


if __name__ == "__main__":
    tests = [
        test_sentiment_and_emotions_csv_rows_and_math,
        test_sentiment_and_emotions_csv_empty_input_still_has_headers,
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
