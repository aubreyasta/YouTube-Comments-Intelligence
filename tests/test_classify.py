"""
Offline self-check for analyze.classify() and analyze.summarise().

Stubs llm.classify_batch with canned labels. No API calls, no framework.
Run: python tests/test_classify.py
"""

import sys
import os
import types

# ---------------------------------------------------------------------------
# Minimal stubs so pipeline modules import without real config or API keys.
# ---------------------------------------------------------------------------

# sys.path must be set BEFORE any sys.modules stub that references the real
# package, otherwise Python can't resolve the real 'pipeline' package at all.
repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, repo_root)

# Stub config module before any pipeline import touches it.
cfg = types.ModuleType("config")
cfg.CODEBOOK_SAMPLE_SIZE = 50
cfg.CODEBOOK_SAMPLE_MAX = 500
cfg.CLASSIFY_BATCH_SIZE = 25
cfg.UNCLASSIFIED_LIMIT = 30
cfg.EMOTION_MODEL = ""
cfg.SENTIMENT_MODEL = ""
cfg.KEEP_LANGUAGES = None
cfg.MIN_COMMENT_LETTERS = 4
cfg.MODEL = "stub"
sys.modules["config"] = cfg

# Stub only pipeline.llm - leave the real 'pipeline' package untouched so
# Python can find pipeline.analyze as a real submodule.
llm_stub = types.ModuleType("pipeline.llm")
llm_stub.ask_json = None
llm_stub.classify_batch = None
sys.modules["pipeline.llm"] = llm_stub

import pipeline.llm as llm          # resolves to the stub above
import pipeline.analyze as analyze  # real module, imports stub llm


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

import pandas as pd  # noqa: E402

THEMES = [
    {"name": "Quality", "definition": "Comments about product quality."},
    {"name": "Price",   "definition": "Comments about cost or value."},
    {"name": "Design",  "definition": "Comments about looks or aesthetics."},
]

THEME_NAMES = [t["name"] for t in THEMES]

# Two videos with 5 comments each (10 total).
COMMENTS = [
    # video_1 - indices 0-4
    {"video_id": "video_1", "group": "G1", "comment": "Really great build quality",   "likes": 10, "reply_count": 2},
    {"video_id": "video_1", "group": "G1", "comment": "Too expensive for what it is", "likes":  5, "reply_count": 0},
    {"video_id": "video_1", "group": "G1", "comment": "Love the sleek design",         "likes":  8, "reply_count": 1},
    {"video_id": "video_1", "group": "G1", "comment": "Worth every penny",             "likes":  3, "reply_count": 0},
    {"video_id": "video_1", "group": "G1", "comment": "Random off-topic comment",      "likes":  1, "reply_count": 0},
    # video_2 - indices 5-9
    {"video_id": "video_2", "group": "G2", "comment": "Durable and well made",         "likes": 12, "reply_count": 3},
    {"video_id": "video_2", "group": "G2", "comment": "Costs a fortune",               "likes":  4, "reply_count": 0},
    {"video_id": "video_2", "group": "G2", "comment": "Looks amazing",                 "likes":  9, "reply_count": 0},
    {"video_id": "video_2", "group": "G2", "comment": "Good value for money",          "likes":  6, "reply_count": 0},
    {"video_id": "video_2", "group": "G2", "comment": "Completely unrelated",          "likes":  2, "reply_count": 0},
]
DF = pd.DataFrame(COMMENTS)

# Points: one per video. video_1 has "value for money", video_2 has "durability".
POINTS = [
    {"video_id": "video_1", "group": "G1", "label": "value for money",
     "description": "The product is worth its price."},
    {"video_id": "video_2", "group": "G2", "label": "durability",
     "description": "The product is built to last."},
]

# Canned stub: returns labels for whatever indices it is given.
# Index 4 and 9 are intentionally omitted to test the "omitted -> Other" path.
CANNED = {
    0: {"theme": "Quality", "echoed": []},
    1: {"theme": "Price",   "echoed": ["value for money"]},
    2: {"theme": "Design",  "echoed": []},
    3: {"theme": "Price",   "echoed": ["value for money"]},
    # 4 omitted -> must become "Other"
    5: {"theme": "Quality", "echoed": ["durability"]},
    6: {"theme": "Price",   "echoed": []},
    7: {"theme": "Design",  "echoed": []},
    8: {"theme": "Price",   "echoed": []},
    # 9 omitted -> must become "Other"
}


def _stub_classify_batch(prompt, theme_names, point_labels, model=None):
    """
    Parse the comment indices out of the prompt text and return canned labels.
    The prompt format is "N: text" per line in the COMMENTS section.
    """
    results = []
    for line in prompt.splitlines():
        m = __import__("re").match(r"^(\d+):", line.strip())
        if m:
            idx = int(m.group(1))
            if idx in CANNED:
                entry = dict(CANNED[idx])
                results.append({"index": idx, **entry})
            # If not in CANNED, omit entirely - simulates model skipping.
    return results


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_every_comment_gets_a_theme():
    llm.classify_batch = _stub_classify_batch
    df_out, cols = analyze.classify(DF, THEMES, POINTS)
    missing = df_out[df_out["theme"].isna()]
    assert len(missing) == 0, f"Some comments have no theme: {missing.index.tolist()}"


def test_omitted_indices_become_other():
    llm.classify_batch = _stub_classify_batch
    df_out, cols = analyze.classify(DF, THEMES, POINTS)
    assert df_out.at[4, "theme"] == "Other", \
        f"Index 4 should be Other, got {df_out.at[4, 'theme']!r}"
    assert df_out.at[9, "theme"] == "Other", \
        f"Index 9 should be Other, got {df_out.at[9, 'theme']!r}"


def test_pt_columns_true_only_on_correct_video():
    llm.classify_batch = _stub_classify_batch
    df_out, cols = analyze.classify(DF, THEMES, POINTS)

    # "value for money" belongs to video_1 only.
    pt_value = "pt__value_for_money"
    assert pt_value in cols, f"{pt_value} not in columns"
    v1_rows = df_out[df_out["video_id"] == "video_1"]
    v2_rows = df_out[df_out["video_id"] == "video_2"]
    assert v1_rows[pt_value].any(), \
        "pt__value_for_money should be True on some video_1 rows"
    assert not v2_rows[pt_value].any(), \
        "pt__value_for_money must not be True on video_2 rows"

    # "durability" belongs to video_2 only.
    pt_dura = "pt__durability"
    assert pt_dura in cols, f"{pt_dura} not in columns"
    assert v2_rows[pt_dura].any(), \
        "pt__durability should be True on some video_2 rows"
    assert not v1_rows[pt_dura].any(), \
        "pt__durability must not be True on video_1 rows"


def test_summarise_percentages_match_hand_count():
    llm.classify_batch = _stub_classify_batch
    df_out, cols = analyze.classify(DF, THEMES, POINTS)
    theme_table, transfer_table = analyze.summarise(df_out, cols)

    # Hand count for G1 (video_1, 5 comments):
    # Quality=1 (20%), Price=2 (40%), Design=1 (20%), Other=1 (20%)
    g1 = theme_table.loc["G1"]
    assert abs(g1.get("Quality", 0) - 20.0) < 0.2, \
        f"G1 Quality expected 20.0, got {g1.get('Quality', 0)}"
    assert abs(g1.get("Price", 0) - 40.0) < 0.2, \
        f"G1 Price expected 40.0, got {g1.get('Price', 0)}"
    assert abs(g1.get("Other", 0) - 20.0) < 0.2, \
        f"G1 Other expected 20.0, got {g1.get('Other', 0)}"

    # Hand count for G2 (video_2, 5 comments):
    # Quality=1 (20%), Price=2 (40%), Design=1 (20%), Other=1 (20%)
    g2 = theme_table.loc["G2"]
    assert abs(g2.get("Quality", 0) - 20.0) < 0.2, \
        f"G2 Quality expected 20.0, got {g2.get('Quality', 0)}"
    assert abs(g2.get("Price", 0) - 40.0) < 0.2, \
        f"G2 Price expected 40.0, got {g2.get('Price', 0)}"

    # Transfer: "value for money" echoed at indices 1 and 3 -> 2/5 = 40%
    vfm_rows = transfer_table[transfer_table["point"] == "value for money"]
    assert len(vfm_rows) == 1, "Expected one transfer row for 'value for money'"
    assert abs(vfm_rows.iloc[0]["echoed_pct"] - 40.0) < 0.2, \
        f"value for money pct expected 40.0, got {vfm_rows.iloc[0]['echoed_pct']}"

    # Transfer: "durability" echoed at index 5 only -> 1/5 = 20%
    dur_rows = transfer_table[transfer_table["point"] == "durability"]
    assert len(dur_rows) == 1, "Expected one transfer row for 'durability'"
    assert abs(dur_rows.iloc[0]["echoed_pct"] - 20.0) < 0.2, \
        f"durability pct expected 20.0, got {dur_rows.iloc[0]['echoed_pct']}"


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [
        test_every_comment_gets_a_theme,
        test_omitted_indices_become_other,
        test_pt_columns_true_only_on_correct_video,
        test_summarise_percentages_match_hand_count,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  ok  {t.__name__}")
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
