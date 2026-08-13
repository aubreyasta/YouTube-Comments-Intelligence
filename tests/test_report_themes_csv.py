"""
Offline self-check for report.export's themes.csv output.
No API calls, no real models.

Run: python tests/test_report_themes_csv.py
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


def test_themes_csv_rows_and_math():
    df = pd.DataFrame([
        {"group": "G1", "theme": "Quality", "video_id": "v1", "likes": 1},
        {"group": "G1", "theme": "Quality", "video_id": "v1", "likes": 2},
        {"group": "G1", "theme": "Price",   "video_id": "v1", "likes": 3},
        {"group": "G2", "theme": "Quality", "video_id": "v2", "likes": 4},
    ])
    meta_df = pd.DataFrame([
        {"group": "G1", "video_id": "v1"},
        {"group": "G2", "video_id": "v2"},
    ])
    with tempfile.TemporaryDirectory() as out_dir:
        report.export(df, EMPTY_TABLE, pd.DataFrame(), AFFECT_EMPTY,
                      meta_df, out_dir)
        rows = _read_csv(os.path.join(out_dir, "themes.csv"))

    assert set(rows[0].keys()) == {
        "group", "theme", "count", "percent", "base_n"}, (
        f"header mismatch: {rows[0].keys() if rows else 'no rows'}")
    assert len(rows) == 3, f"expected 3 group/theme rows, got {len(rows)}"

    by_key = {(r["group"], r["theme"]): r for r in rows}
    g1_quality = by_key[("G1", "Quality")]
    assert g1_quality["count"] == "2"
    assert g1_quality["base_n"] == "3"
    assert g1_quality["percent"] == "66.7"

    g1_price = by_key[("G1", "Price")]
    assert g1_price["count"] == "1"
    assert g1_price["base_n"] == "3"
    assert g1_price["percent"] == "33.3"

    g2_quality = by_key[("G2", "Quality")]
    assert g2_quality["count"] == "1"
    assert g2_quality["base_n"] == "1"
    assert g2_quality["percent"] == "100.0"
    print(f"  ok  themes.csv rows and percent math: {rows}")


def test_themes_csv_empty_input_still_has_headers():
    df = pd.DataFrame(columns=["group", "theme", "video_id", "likes"])
    meta_df = pd.DataFrame(columns=["group", "video_id"])
    with tempfile.TemporaryDirectory() as out_dir:
        report.export(df, EMPTY_TABLE, pd.DataFrame(), AFFECT_EMPTY,
                      meta_df, out_dir)
        path = os.path.join(out_dir, "themes.csv")
        with open(path, newline="", encoding="utf-8-sig") as fh:
            header = next(csv.reader(fh))
        rows = _read_csv(path)

    assert header == ["group", "theme", "count", "percent", "base_n"], (
        f"header mismatch on empty input: {header}")
    assert rows == [], f"expected no data rows, got {rows}"
    print("  ok  empty input still writes themes.csv headers")


if __name__ == "__main__":
    tests = [
        test_themes_csv_rows_and_math,
        test_themes_csv_empty_input_still_has_headers,
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
