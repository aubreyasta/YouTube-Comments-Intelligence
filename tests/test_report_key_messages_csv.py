"""
Offline self-check for report.export's key-messages.csv output.
No API calls, no real models.

Run: python tests/test_report_key_messages_csv.py
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

HEADER = ["group", "key_message", "count", "percent", "base_n",
          "positive_count", "positive_percent",
          "negative_count", "negative_percent", "sentiment_base_n"]


def _read_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def test_key_messages_csv_rows_and_math():
    # G1's video was shown both Key Messages; G2's video only "Speed".
    df = pd.DataFrame([
        {"group": "G1", "pt__value_for_money": True, "pt__speed": True,
         "sentiment": "Positive", "likes": 1, "video_id": "v1"},
        {"group": "G1", "pt__value_for_money": True, "pt__speed": False,
         "sentiment": "NEGATIVE", "likes": 2, "video_id": "v1"},
        {"group": "G1", "pt__value_for_money": False, "pt__speed": True,
         "sentiment": None, "likes": 3, "video_id": "v1"},
        {"group": "G1", "pt__value_for_money": False, "pt__speed": False,
         "sentiment": "neutral", "likes": 4, "video_id": "v1"},
        {"group": "G1", "pt__value_for_money": False, "pt__speed": False,
         "sentiment": "neutral", "likes": 5, "video_id": "v1"},
        {"group": "G2", "pt__value_for_money": pd.NA, "pt__speed": False,
         "sentiment": "positive", "likes": 6, "video_id": "v2"},
        {"group": "G2", "pt__value_for_money": pd.NA, "pt__speed": False,
         "sentiment": "negative", "likes": 7, "video_id": "v2"},
        {"group": "G2", "pt__value_for_money": pd.NA, "pt__speed": False,
         "sentiment": "positive", "likes": 8, "video_id": "v2"},
    ])
    meta_df = pd.DataFrame([
        {"group": "G1", "video_id": "v1"},
        {"group": "G2", "video_id": "v2"},
    ])
    transfer = pd.DataFrame([
        {"group": "G1", "point": "Value For Money", "echoed_pct": 40.0, "n": 2},
        {"group": "G1", "point": "Speed", "echoed_pct": 40.0, "n": 2},
        {"group": "G2", "point": "Speed", "echoed_pct": 0.0, "n": 0},
    ])

    with tempfile.TemporaryDirectory() as out_dir:
        report.export(df, EMPTY_TABLE, transfer, AFFECT_EMPTY, meta_df,
                      out_dir)
        path = os.path.join(out_dir, "key-messages.csv")
        with open(path, newline="", encoding="utf-8-sig") as fh:
            header = next(csv.reader(fh))
        rows = _read_csv(path)

    assert header == HEADER, f"header mismatch: {header}"
    assert len(rows) == 3, f"expected 3 (group, key_message) rows, got {len(rows)}"

    by_key = {(r["group"], r["key_message"]): r for r in rows}

    g1_value = by_key[("G1", "Value For Money")]
    assert g1_value["base_n"] == "5"
    assert g1_value["count"] == "2"
    assert g1_value["percent"] == "40.0"
    assert g1_value["sentiment_base_n"] == "2"   # rows 1,2: both non-null
    assert g1_value["positive_count"] == "1"
    assert g1_value["positive_percent"] == "50.0"
    assert g1_value["negative_count"] == "1"
    assert g1_value["negative_percent"] == "50.0"

    g1_speed = by_key[("G1", "Speed")]
    assert g1_speed["base_n"] == "5"
    assert g1_speed["count"] == "2"
    assert g1_speed["percent"] == "40.0"
    assert g1_speed["sentiment_base_n"] == "1"   # row 3's null sentiment excluded
    assert g1_speed["positive_count"] == "1"
    assert g1_speed["positive_percent"] == "100.0"
    assert g1_speed["negative_count"] == "0"
    assert g1_speed["negative_percent"] == "0.0"

    # Zero-mention row still present because the Key Message was applicable.
    g2_speed = by_key[("G2", "Speed")]
    assert g2_speed["base_n"] == "3"
    assert g2_speed["count"] == "0"
    assert g2_speed["percent"] == "0.0"
    assert g2_speed["sentiment_base_n"] == "0"
    assert g2_speed["positive_count"] == "0"
    assert g2_speed["negative_count"] == "0"

    # G2 was never shown "Value For Money" (pt__ column is NA there), so
    # no row for it - inapplicable, not a zero.
    assert ("G2", "Value For Money") not in by_key
    print(f"  ok  key-messages.csv rows and percent math: {rows}")


def test_key_messages_csv_empty_input_still_has_headers():
    df = pd.DataFrame(columns=["group", "sentiment", "likes", "video_id"])
    meta_df = pd.DataFrame(columns=["group", "video_id"])
    with tempfile.TemporaryDirectory() as out_dir:
        report.export(df, EMPTY_TABLE, pd.DataFrame(), AFFECT_EMPTY,
                      meta_df, out_dir)
        path = os.path.join(out_dir, "key-messages.csv")
        with open(path, newline="", encoding="utf-8-sig") as fh:
            header = next(csv.reader(fh))
        rows = _read_csv(path)

    assert header == HEADER, f"header mismatch on empty input: {header}"
    assert rows == [], f"expected no data rows, got {rows}"
    print("  ok  empty input still writes key-messages.csv headers")


if __name__ == "__main__":
    tests = [
        test_key_messages_csv_rows_and_math,
        test_key_messages_csv_empty_input_still_has_headers,
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
