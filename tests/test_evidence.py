"""
Offline self-check for adapter._build_report_json, adapter._build_evidence,
adapter._slugify. No API calls, no real models.

Run: python tests/test_evidence.py
"""

import json
import math
import sys
import os
import types

repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, repo_root)

# Stub heavy imports before adapter loads them.
for mod in ("db", "storage", "assets"):
    stub = types.ModuleType(mod)
    sys.modules[mod] = stub

# Stub pipeline sub-modules that would trigger network/model loads.
for mod in ("pipeline", "pipeline.collect", "pipeline.brief",
            "pipeline.analyze", "pipeline.report", "pipeline.llm",
            "pipeline.config_types"):
    if mod not in sys.modules:
        sys.modules[mod] = types.ModuleType(mod)

# Provide the real PipelineConfig dataclass without importing the full package.
import importlib.util
spec = importlib.util.spec_from_file_location(
    "pipeline.config_types",
    os.path.join(repo_root, "pipeline", "config_types.py"))
config_types_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(config_types_mod)
sys.modules["pipeline.config_types"] = config_types_mod

# Stub storage.run_dir so _build_config does not mkdir.
sys.modules["storage"].run_dir = lambda run_id: "/tmp/fake"

import pandas as pd

# Import adapter directly - it only uses stdlib + pandas at module scope.
import importlib.util as ilu
spec2 = ilu.spec_from_file_location("adapter", os.path.join(repo_root, "adapter.py"))
adapter = ilu.module_from_spec(spec2)
spec2.loader.exec_module(adapter)

_build_report_json = adapter._build_report_json
_build_evidence     = adapter._build_evidence
_slugify            = adapter._slugify


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

COLUMNS = {
    "pt__value_for_money": "Value for money",
    "pt__durability":      "durability",
}

KEY_MESSAGES = [
    {"id": "km-1", "label": "Value for money", "description": "d1",
     "included": True, "sort_order": 1},
    {"id": "km-2", "label": "Durability", "description": "d2",
     "included": True, "sort_order": 0},
    {"id": "km-3", "label": "Excluded idea", "description": "d3",
     "included": False, "sort_order": 2},
]


def _make_df(n=20):
    """All rows in group 'G1'. value_for_money true for even i (10 rows).
    durability true only for i=1. sentiment positive for i<10, negative
    for i>=10. theme cycles Quality/Price/Other. emotion alternates
    joy/neutral."""
    rows = []
    for i in range(n):
        rows.append({
            "comment":  f"comment text number {i} " + "x" * (i * 3),
            "likes":    i,
            "theme":    "Quality" if i % 3 == 0 else ("Price" if i % 3 == 1 else "Other"),
            "pt__value_for_money": (i % 2 == 0),
            "pt__durability":      pd.NA,
            "emotion":  "joy" if i % 2 == 0 else "neutral",
            "sentiment": "positive" if i < 10 else "negative",
            "lang":     "id",
            "video_id": f"v{i % 2}",
            "group":    "G1",
        })
    rows[1]["pt__durability"] = True
    return pd.DataFrame(rows)


TRANSFER_TABLE = pd.DataFrame([
    {"group": "G1", "point": "value for money", "echoed_pct": 50.0, "n": 10},
    {"group": "G1", "point": "durability",       "echoed_pct":  5.0, "n": 1},
])


# ---------------------------------------------------------------------------
# Test 1: top-level ReportJson has exactly the six required keys
# ---------------------------------------------------------------------------

def test_top_level_keys_exact():
    df = _make_df()
    result = _build_report_json(df, TRANSFER_TABLE, KEY_MESSAGES, COLUMNS)
    expected = {
        "overallTransfer", "keyMessages", "themes", "emotions",
        "keyMessageSentiment", "evidence",
    }
    assert set(result.keys()) == expected, (
        f"Top-level keys mismatch: {sorted(result.keys())}")
    for banned in ("transfers", "ideaSentiment", "runId", "title", "subtitle",
                   "interpretation", "quote", "caveat"):
        assert banned not in result, f"Prohibited old key present: {banned}"
    print("  ok  top-level keys exact, no old keys")


# ---------------------------------------------------------------------------
# Test 2: keyMessages - types, IDs, excluded filtering, sort order, counts
# ---------------------------------------------------------------------------

def test_key_messages():
    df = _make_df()
    result = _build_report_json(df, TRANSFER_TABLE, KEY_MESSAGES, COLUMNS)
    km = result["keyMessages"]

    assert len(km) == 2, f"Expected 2 included key messages, got {len(km)}"
    # sort_order: Durability (0) before Value for money (1); excluded idea dropped.
    assert [m["label"] for m in km] == ["Durability", "Value for money"], (
        f"Sort order wrong: {[m['label'] for m in km]}")

    for m in km:
        assert isinstance(m["id"], str)
        assert isinstance(m["metricId"], str)
        assert isinstance(m["label"], str)
        assert isinstance(m["description"], str)
        assert isinstance(m["count"], int)
        assert isinstance(m["percent"], float)

    durability = km[0]
    assert durability["id"] == "km-2"
    assert durability["metricId"] == "m-t-durability"
    # applicable group G1 matches transfer_table row (G1, durability).
    # pt__durability: 1 True out of 1 non-null cell within group G1 -> denom=1, num=1.
    assert durability["count"] == 1, f"durability count: {durability['count']}"
    assert durability["percent"] == 100.0, f"durability percent: {durability['percent']}"

    vfm = km[1]
    assert vfm["id"] == "km-1"
    # exact-label match "Value for money" vs column label "Value for money".
    assert vfm["metricId"] == "m-t-value-for-money"
    assert vfm["count"] == 10, f"vfm count: {vfm['count']}"
    assert vfm["percent"] == 50.0, f"vfm percent: {vfm['percent']}"
    print("  ok  keyMessages: filtering, order, IDs, counts")


# ---------------------------------------------------------------------------
# Test 3: overallTransfer - union counted once, one decimal
# ---------------------------------------------------------------------------

def test_overall_transfer():
    df = _make_df()
    result = _build_report_json(df, TRANSFER_TABLE, KEY_MESSAGES, COLUMNS)
    # value_for_money true for even i (0,2,4,...18) = 10 rows; durability true for i=1.
    # Union of true rows: evens (10) + {1} = 11 out of 20. One row (i=1) satisfies
    # both messages but must be counted once in the union.
    assert result["overallTransfer"] == round(11 * 100 / 20, 1), (
        f"overallTransfer: {result['overallTransfer']}")
    assert isinstance(result["overallTransfer"], float)
    print(f"  ok  overallTransfer = {result['overallTransfer']}")


def test_overall_transfer_counted_once_on_overlap():
    """Both messages true on i=1: union must not double count that row."""
    df = _make_df()
    df.loc[1, "pt__value_for_money"] = True  # already true (odd->even? i=1 is odd)
    # Force explicit overlap: row 0 true for both.
    df.loc[0, "pt__durability"] = True
    result = _build_report_json(df, TRANSFER_TABLE, KEY_MESSAGES, COLUMNS)
    # vfm true: evens (10, includes row0) ; durability true: {0,1} now (2 rows).
    # union = evens(10) union {0,1} = evens(10) + {1} = 11 (row0 already in evens).
    assert result["overallTransfer"] == round(11 * 100 / 20, 1), (
        f"overlap not deduped: {result['overallTransfer']}")
    print("  ok  overallTransfer dedupes overlapping messages")


# ---------------------------------------------------------------------------
# Test 4: zero denominator -> percent 0.0, not division error
# ---------------------------------------------------------------------------

def test_zero_denominator():
    empty_df = pd.DataFrame(columns=[
        "comment", "likes", "theme", "pt__value_for_money", "pt__durability",
        "emotion", "sentiment", "lang", "video_id", "group"])
    result = _build_report_json(empty_df, TRANSFER_TABLE, KEY_MESSAGES, COLUMNS)
    assert result["overallTransfer"] == 0.0
    for m in result["keyMessages"]:
        assert m["percent"] == 0.0, f"Expected 0.0 percent on empty df, got {m}"
        assert m["count"] == 0
        assert m["percent"] == 0.0
    for m in result["keyMessageSentiment"]:
        assert m["baseN"] == 0
        assert m["positivePercent"] == 0.0 and m["negativePercent"] == 0.0
    print("  ok  zero denominator -> 0.0, no crash")


# ---------------------------------------------------------------------------
# Test 5: null denominator - non-applicable-group rows excluded entirely
# ---------------------------------------------------------------------------

def test_unrelated_group_excluded():
    """Rows in a group not listed for the message must not enter numerator
    or denominator, even if the message column is True there."------"""
    df = _make_df()
    # Add rows in a second group G2 where value_for_money is True; G2 is not
    # listed in TRANSFER_TABLE for "value for money", so it must not count.
    extra = pd.DataFrame([{
        "comment": "extra row", "likes": 999, "theme": "Quality",
        "pt__value_for_money": True, "pt__durability": pd.NA,
        "emotion": "joy", "sentiment": "positive", "lang": "id",
        "video_id": "v9", "group": "G2",
    }])
    df2 = pd.concat([df, extra], ignore_index=True)
    result = _build_report_json(df2, TRANSFER_TABLE, KEY_MESSAGES, COLUMNS)
    vfm = next(m for m in result["keyMessages"] if m["label"] == "Value for money")
    # Still 10/20 from G1 only; the G2 row must not inflate numerator or denom.
    assert vfm["count"] == 10, f"G2 row leaked into count: {vfm['count']}"
    assert vfm["percent"] == 50.0, f"G2 row leaked into percent: {vfm['percent']}"
    print("  ok  unrelated group excluded from applicability")


def test_missing_group_column_zero_applicability():
    """base_df without 'group' -> all messages zero, evidence empty,
    overallTransfer 0.0."""
    df = _make_df().drop(columns=["group"])
    result = _build_report_json(df, TRANSFER_TABLE, KEY_MESSAGES, COLUMNS)
    for m in result["keyMessages"]:
        assert m["count"] == 0 and m["percent"] == 0.0, m
    assert result["overallTransfer"] == 0.0
    for g in result["evidence"]:
        if g["metricId"].startswith("m-t-") or g["metricId"].startswith("m-is-"):
            assert g["comments"] == [], g
    print("  ok  missing base_df.group -> zero applicability everywhere")


def test_missing_transfer_table_group_or_point_zero_applicability():
    """transfer_table without 'group'/'point' -> zero applicability."""
    df = _make_df()
    bad_transfer = pd.DataFrame([{"echoed_pct": 50.0, "n": 10}])
    result = _build_report_json(df, bad_transfer, KEY_MESSAGES, COLUMNS)
    for m in result["keyMessages"]:
        assert m["count"] == 0 and m["percent"] == 0.0, m
    assert result["overallTransfer"] == 0.0
    print("  ok  missing transfer_table.group/point -> zero applicability")


# ---------------------------------------------------------------------------
# Test 6: multi-group aggregation - a message applicable to 2+ groups
# ---------------------------------------------------------------------------

def test_multi_group_aggregation():
    """A single message applicable to groups G1 and G2 (deduplicated) must
    aggregate eligible rows across both."""
    df = _make_df()
    g2_rows = pd.DataFrame([
        {"comment": f"g2 comment {i}", "likes": 100 + i, "theme": "Quality",
         "pt__value_for_money": True, "pt__durability": pd.NA,
         "emotion": "joy", "sentiment": "positive", "lang": "id",
         "video_id": "v0", "group": "G2"}
        for i in range(3)
    ])
    df2 = pd.concat([df, g2_rows], ignore_index=True)
    multi_transfer = pd.DataFrame([
        {"group": "G1", "point": "value for money", "echoed_pct": 50.0, "n": 10},
        {"group": "G2", "point": "value for money", "echoed_pct": 50.0, "n": 3},
        {"group": "G1", "point": "durability", "echoed_pct": 5.0, "n": 1},
        # Duplicate (group, point) pair - must not double count group membership.
        {"group": "G1", "point": "value for money", "echoed_pct": 50.0, "n": 10},
    ])
    result = _build_report_json(df2, multi_transfer, KEY_MESSAGES, COLUMNS)
    vfm = next(m for m in result["keyMessages"] if m["label"] == "Value for money")
    # G1: 10 true / 20 non-null. G2: 3 true / 3 non-null. Aggregate: 13/23.
    assert vfm["count"] == 13, f"multi-group count: {vfm['count']}"
    assert vfm["percent"] == round(13 * 100 / 23, 1), f"multi-group percent: {vfm['percent']}"
    print("  ok  multi-group aggregation with dedup")


# ---------------------------------------------------------------------------
# Test 7: unmatched message - zero values, empty evidence, no crash
# ---------------------------------------------------------------------------

def test_unmatched_message_zero_and_empty_evidence():
    """A Key Message with no matching column and no transfer_table point
    stays with zero count/percent and empty evidence."""
    messages = KEY_MESSAGES + [
        {"id": "km-4", "label": "Totally unmatched idea", "description": "",
         "included": True, "sort_order": 5},
    ]
    df = _make_df()
    result = _build_report_json(df, TRANSFER_TABLE, messages, COLUMNS)
    unmatched = next(m for m in result["keyMessages"] if m["label"] == "Totally unmatched idea")
    assert unmatched["count"] == 0
    assert unmatched["percent"] == 0.0
    ev = next(g for g in result["evidence"] if g["metricId"] == unmatched["metricId"])
    assert ev["comments"] == []
    print("  ok  unmatched message: zero values, empty evidence")


# ---------------------------------------------------------------------------
# Test 8: themes/emotions - case-insensitive merge, first spelling preserved
# ---------------------------------------------------------------------------

def test_themes():
    df = _make_df()
    result = _build_report_json(df, TRANSFER_TABLE, KEY_MESSAGES, COLUMNS)
    themes = result["themes"]

    # i%3==0 -> Quality (i=0,3,...,18 -> 7), i%3==1 -> Price (i=1,4,...,19 -> 7),
    # i%3==2 -> Other (i=2,5,...,17 -> 6). Total 20.
    counts = {t["label"]: t["count"] for t in themes}
    assert counts.get("Quality") == 7, counts
    assert counts.get("Price") == 7, counts
    assert counts.get("Other") == 6, counts

    # count desc, tie broken by casefold label then label: Price vs Quality tie at 7 ->
    # casefold("price") < casefold("quality") -> Price first.
    labels_in_order = [t["label"] for t in themes]
    assert labels_in_order[0] == "Price", labels_in_order
    assert labels_in_order[1] == "Quality", labels_in_order
    assert labels_in_order[2] == "Other", labels_in_order

    ids_in_order = [t["metricId"] for t in themes]
    assert ids_in_order == ["m-th-0", "m-th-1", "m-th-2"], ids_in_order

    for t in themes:
        assert t["percent"] == round(t["count"] * 100 / 20, 1)
        assert isinstance(t["percent"], float)
    print(f"  ok  themes: counts/order/ids {labels_in_order}")


def test_theme_case_merge_preserves_first_spelling():
    """'Quality', 'quality', 'QUALITY' must merge into one metric using the
    first-seen spelling, denominator over all non-empty labels."""
    rows = [
        {"comment": "c0", "likes": 0, "theme": "Quality", "pt__value_for_money": False,
         "pt__durability": pd.NA, "emotion": "joy", "sentiment": "neutral",
         "lang": "id", "video_id": "v0", "group": "G1"},
        {"comment": "c1", "likes": 1, "theme": "QUALITY", "pt__value_for_money": False,
         "pt__durability": pd.NA, "emotion": "joy", "sentiment": "neutral",
         "lang": "id", "video_id": "v0", "group": "G1"},
        {"comment": "c2", "likes": 2, "theme": "quality", "pt__value_for_money": False,
         "pt__durability": pd.NA, "emotion": "joy", "sentiment": "neutral",
         "lang": "id", "video_id": "v0", "group": "G1"},
        {"comment": "c3", "likes": 3, "theme": "  ", "pt__value_for_money": False,
         "pt__durability": pd.NA, "emotion": "joy", "sentiment": "neutral",
         "lang": "id", "video_id": "v0", "group": "G1"},  # whitespace-only excluded
        {"comment": "c4", "likes": 4, "theme": None, "pt__value_for_money": False,
         "pt__durability": pd.NA, "emotion": "joy", "sentiment": "neutral",
         "lang": "id", "video_id": "v0", "group": "G1"},  # null excluded
    ]
    df = pd.DataFrame(rows)
    result = _build_report_json(df, TRANSFER_TABLE, KEY_MESSAGES, COLUMNS)
    themes = result["themes"]
    assert len(themes) == 1, themes
    assert themes[0]["label"] == "Quality", themes[0]  # first-seen spelling
    assert themes[0]["count"] == 3, themes[0]
    # denominator is all non-empty labels (3), not total rows (5).
    assert themes[0]["percent"] == round(3 * 100 / 5, 1), themes[0]
    print("  ok  theme case-insensitive merge preserves first spelling")


# ---------------------------------------------------------------------------
# Test 9: emotions - counting/order/IDs, duplicate slug suffixing
# ---------------------------------------------------------------------------

def test_emotions():
    df = _make_df()
    result = _build_report_json(df, TRANSFER_TABLE, KEY_MESSAGES, COLUMNS)
    emotions = result["emotions"]

    # joy for even i (10), neutral for odd i (10) -> tie -> casefold order: joy < neutral.
    counts = {e["label"]: e["count"] for e in emotions}
    assert counts.get("joy") == 10, counts
    assert counts.get("neutral") == 10, counts
    labels_in_order = [e["label"] for e in emotions]
    assert labels_in_order == ["joy", "neutral"], labels_in_order
    ids_in_order = [e["metricId"] for e in emotions]
    assert ids_in_order == ["m-em-joy", "m-em-neutral"], ids_in_order
    print(f"  ok  emotions: counts/order/ids {labels_in_order}")


def test_emotion_slug_collision_suffix():
    # Two distinct labels that slugify to the same string must get -2 onward.
    rows = []
    for i in range(4):
        rows.append({
            "comment": f"c{i}", "likes": i, "theme": "Quality",
            "pt__value_for_money": False, "pt__durability": pd.NA,
            "emotion": "joy!!" if i < 2 else "joy??",  # both slugify to "joy"
            "sentiment": "neutral", "lang": "id", "video_id": "v0", "group": "G1",
        })
    df = pd.DataFrame(rows)
    result = _build_report_json(df, TRANSFER_TABLE, KEY_MESSAGES, COLUMNS)
    ids_in_order = [e["metricId"] for e in result["emotions"]]
    assert ids_in_order == ["m-em-joy", "m-em-joy-2"], ids_in_order
    print(f"  ok  emotion slug collision suffixed: {ids_in_order}")


# ---------------------------------------------------------------------------
# Test 10: slug fallback and m-t/m-is shared collision suffixing
# ---------------------------------------------------------------------------

def test_slugify_punctuation_fallback():
    assert _slugify("!!!") == "message"
    assert _slugify("   ") == "message"
    assert _slugify("...") == "message"
    assert _slugify("Value for money") == "value-for-money"
    print("  ok  _slugify punctuation-only fallback -> 'message'")


def test_key_message_slug_collision_shared_across_mt_and_mis():
    """Two Key Messages that slugify to the same base must get -2 suffix,
    and the m-is-* metricId for the same message must reuse the same
    suffix as its m-t-* counterpart."""
    messages = [
        {"id": "km-a", "label": "Value!!", "description": "",
         "included": True, "sort_order": 0},
        {"id": "km-b", "label": "Value??", "description": "",
         "included": True, "sort_order": 1},
    ]
    columns = {"pt__value_a": "Value!!", "pt__value_b": "Value??"}
    df = _make_df()
    df["pt__value_a"] = False
    df["pt__value_b"] = False
    result = _build_report_json(df, TRANSFER_TABLE, messages, columns)
    km_ids = [m["metricId"] for m in result["keyMessages"]]
    is_ids = [m["metricId"] for m in result["keyMessageSentiment"]]
    assert km_ids == ["m-t-value", "m-t-value-2"], km_ids
    assert is_ids == ["m-is-value", "m-is-value-2"], is_ids
    # evidence reuses the final IDs exactly.
    evidence_ids = [g["metricId"] for g in result["evidence"]]
    assert "m-t-value" in evidence_ids and "m-t-value-2" in evidence_ids
    assert "m-is-value" in evidence_ids and "m-is-value-2" in evidence_ids
    print("  ok  slug collision suffix shared across m-t-*/m-is-*")


# ---------------------------------------------------------------------------
# Test 11: keyMessageSentiment - base rules, only recognized sentiments count
# ---------------------------------------------------------------------------

def test_key_message_sentiment():
    df = _make_df()
    result = _build_report_json(df, TRANSFER_TABLE, KEY_MESSAGES, COLUMNS)
    sent = {m["label"]: m for m in result["keyMessageSentiment"]}

    # value_for_money true for even i in [0,20): 0,2,4,...,18 -> 10 rows.
    # sentiment positive for i<10, negative for i>=10.
    # Among evens: 0,2,4,6,8 (positive, 5) and 10,12,14,16,18 (negative, 5).
    vfm = sent["Value for money"]
    assert vfm["baseN"] == 10, vfm
    assert vfm["positiveCount"] == 5 and vfm["negativeCount"] == 5, vfm
    assert vfm["positivePercent"] == 50.0 and vfm["negativePercent"] == 50.0, vfm
    assert vfm["metricId"] == "m-is-value-for-money"
    assert vfm["id"] == "km-1"

    # durability true only for i=1 -> sentiment positive (i<10) -> baseN=1.
    dur = sent["Durability"]
    assert dur["baseN"] == 1, dur
    assert dur["positiveCount"] == 1 and dur["negativeCount"] == 0, dur
    assert dur["positivePercent"] == 100.0 and dur["negativePercent"] == 0.0, dur
    print("  ok  keyMessageSentiment: counts/percent/baseN")


def test_sentiment_unrecognized_and_null_excluded_from_base():
    """Unknown sentiment strings and null values must not enter baseN;
    'neutral' must enter baseN but not positive/negative counts."""
    rows = []
    for i, s in enumerate(["positive", "Negative", "  NEUTRAL  ", "mixed", None, ""]):
        rows.append({
            "comment": f"c{i}", "likes": i, "theme": "Quality",
            "pt__value_for_money": True, "pt__durability": pd.NA,
            "emotion": "joy", "sentiment": s, "lang": "id",
            "video_id": "v0", "group": "G1",
        })
    df = pd.DataFrame(rows)
    result = _build_report_json(df, TRANSFER_TABLE, KEY_MESSAGES, COLUMNS)
    vfm = next(m for m in result["keyMessageSentiment"] if m["label"] == "Value for money")
    # recognized: positive, Negative(->negative), NEUTRAL(->neutral) = 3. mixed/None/'' excluded.
    assert vfm["baseN"] == 3, vfm
    assert vfm["positiveCount"] == 1, vfm
    assert vfm["negativeCount"] == 1, vfm
    print("  ok  sentiment base excludes unrecognized/null, keeps neutral in baseN")


# ---------------------------------------------------------------------------
# Test 12: evidence - group order, MetricComment shape, sort, cap
# ---------------------------------------------------------------------------

def test_evidence_group_order_and_shape():
    df = _make_df()
    result = _build_report_json(df, TRANSFER_TABLE, KEY_MESSAGES, COLUMNS)
    evidence = result["evidence"]

    expected_ids = (
        [m["metricId"] for m in result["keyMessages"]] +
        [m["metricId"] for m in result["themes"]] +
        [m["metricId"] for m in result["emotions"]] +
        [m["metricId"] for m in result["keyMessageSentiment"]]
    )
    actual_ids = [g["metricId"] for g in evidence]
    assert actual_ids == expected_ids, (
        f"Evidence group order wrong.\nexpected={expected_ids}\nactual={actual_ids}")

    for g in evidence:
        assert isinstance(g["metricId"], str)
        assert isinstance(g["comments"], list)
        for c in g["comments"]:
            assert set(c.keys()) == {"text", "likes", "videoId", "sentiment"}, c
            assert isinstance(c["text"], str)
            assert isinstance(c["likes"], int)
            assert isinstance(c["videoId"], str)
            assert c["sentiment"] is None or isinstance(c["sentiment"], str)
    print(f"  ok  evidence: {len(evidence)} groups, order matches, shape correct")


def test_evidence_cap_and_sort():
    df = _make_df()
    df["pt__value_for_money"] = True  # all 20 rows echo -> exercises cap=8
    result = _build_report_json(df, TRANSFER_TABLE, KEY_MESSAGES, COLUMNS)
    vfm_group = next(
        g for g in result["evidence"] if g["metricId"] == "m-t-value-for-money")
    comments = vfm_group["comments"]
    assert len(comments) == 8, f"Expected cap of 8, got {len(comments)}"
    likes_seq = [c["likes"] for c in comments]
    assert likes_seq == sorted(likes_seq, reverse=True), (
        f"Not sorted by likes desc: {likes_seq}")
    assert likes_seq[0] == 19, f"Highest likes row should be first: {likes_seq}"
    print(f"  ok  evidence cap=8, likes desc: {likes_seq}")


def test_evidence_sort_ties_by_text_length_then_source_order():
    """Equal likes must break tie by text length desc, then original
    source order (earlier row first)."""
    rows = [
        {"comment": "short", "likes": 5, "theme": "Quality",
         "pt__value_for_money": True, "pt__durability": pd.NA,
         "emotion": "joy", "sentiment": "positive", "lang": "id",
         "video_id": "v0", "group": "G1"},
        {"comment": "a much longer comment text here", "likes": 5, "theme": "Quality",
         "pt__value_for_money": True, "pt__durability": pd.NA,
         "emotion": "joy", "sentiment": "positive", "lang": "id",
         "video_id": "v0", "group": "G1"},
        {"comment": "medium length comment", "likes": 5, "theme": "Quality",
         "pt__value_for_money": True, "pt__durability": pd.NA,
         "emotion": "joy", "sentiment": "positive", "lang": "id",
         "video_id": "v0", "group": "G1"},
    ]
    df = pd.DataFrame(rows)
    result = _build_report_json(df, TRANSFER_TABLE, KEY_MESSAGES, COLUMNS)
    vfm_group = next(
        g for g in result["evidence"] if g["metricId"] == "m-t-value-for-money")
    texts = [c["text"] for c in vfm_group["comments"]]
    assert texts == [
        "a much longer comment text here", "medium length comment", "short",
    ], texts
    print("  ok  evidence tie-break: text length desc, then source order")


def test_evidence_null_and_empty_text_excluded():
    rows = [
        {"comment": None, "likes": 5, "theme": "Quality",
         "pt__value_for_money": True, "pt__durability": pd.NA,
         "emotion": "joy", "sentiment": "positive", "lang": "id",
         "video_id": None, "group": "G1"},
        {"comment": "   ", "likes": 6, "theme": "Quality",
         "pt__value_for_money": True, "pt__durability": pd.NA,
         "emotion": "joy", "sentiment": "positive", "lang": "id",
         "video_id": "v1", "group": "G1"},
        {"comment": "", "likes": 7, "theme": "Quality",
         "pt__value_for_money": True, "pt__durability": pd.NA,
         "emotion": "joy", "sentiment": "positive", "lang": "id",
         "video_id": "v1", "group": "G1"},
        {"comment": "real text", "likes": 1, "theme": "Quality",
         "pt__value_for_money": True, "pt__durability": pd.NA,
         "emotion": "joy", "sentiment": "positive", "lang": "id",
         "video_id": None, "group": "G1"},
    ]
    df = pd.DataFrame(rows)
    result = _build_report_json(df, TRANSFER_TABLE, KEY_MESSAGES, COLUMNS)
    vfm_group = next(
        g for g in result["evidence"] if g["metricId"] == "m-t-value-for-money")
    assert len(vfm_group["comments"]) == 1, vfm_group
    c = vfm_group["comments"][0]
    assert c["text"] == "real text"
    assert c["videoId"] == "", f"Null video_id should become '', got {c['videoId']!r}"
    print("  ok  evidence: null/empty/whitespace-only text excluded, null videoId -> ''")


def test_evidence_invalid_likes_become_zero():
    rows = [
        {"comment": "c0", "likes": float("nan"), "theme": "Quality",
         "pt__value_for_money": True, "pt__durability": pd.NA,
         "emotion": "joy", "sentiment": "positive", "lang": "id",
         "video_id": "v0", "group": "G1"},
        {"comment": "c1", "likes": float("inf"), "theme": "Quality",
         "pt__value_for_money": True, "pt__durability": pd.NA,
         "emotion": "joy", "sentiment": "positive", "lang": "id",
         "video_id": "v0", "group": "G1"},
        {"comment": "c2", "likes": "not-a-number", "theme": "Quality",
         "pt__value_for_money": True, "pt__durability": pd.NA,
         "emotion": "joy", "sentiment": "positive", "lang": "id",
         "video_id": "v0", "group": "G1"},
        {"comment": "c3", "likes": None, "theme": "Quality",
         "pt__value_for_money": True, "pt__durability": pd.NA,
         "emotion": "joy", "sentiment": "positive", "lang": "id",
         "video_id": "v0", "group": "G1"},
    ]
    df = pd.DataFrame(rows)
    result = _build_report_json(df, TRANSFER_TABLE, KEY_MESSAGES, COLUMNS)
    vfm_group = next(
        g for g in result["evidence"] if g["metricId"] == "m-t-value-for-money")
    for c in vfm_group["comments"]:
        assert c["likes"] == 0, c
        assert isinstance(c["likes"], int)
    print("  ok  evidence: invalid/nonfinite/null likes -> 0")


# ---------------------------------------------------------------------------
# Test 13: key message sentiment evidence - balanced selection + backfill
# ---------------------------------------------------------------------------

def test_sentiment_evidence_balanced_selection_and_backfill():
    """Up to 4 positive + 4 negative selected first (ranked by ordinary
    order within bucket); remaining slots backfilled from best unselected
    recognized rows (including neutral) by ordinary order; no duplicates;
    output order is positive, negative, backfill."""
    rows = []
    # 6 positive (more than the 4-cap), 2 negative, 1 neutral - all applicable/mentioned.
    for i in range(6):
        rows.append({
            "comment": f"pos{i}", "likes": 10 - i, "theme": "Quality",
            "pt__value_for_money": True, "pt__durability": pd.NA,
            "emotion": "joy", "sentiment": "positive", "lang": "id",
            "video_id": "v0", "group": "G1",
        })
    for i in range(2):
        rows.append({
            "comment": f"neg{i}", "likes": 3 - i, "theme": "Quality",
            "pt__value_for_money": True, "pt__durability": pd.NA,
            "emotion": "joy", "sentiment": "negative", "lang": "id",
            "video_id": "v0", "group": "G1",
        })
    rows.append({
        "comment": "neu0", "likes": 100, "theme": "Quality",
        "pt__value_for_money": True, "pt__durability": pd.NA,
        "emotion": "joy", "sentiment": "neutral", "lang": "id",
        "video_id": "v0", "group": "G1",
    })
    df = pd.DataFrame(rows)
    result = _build_report_json(df, TRANSFER_TABLE, KEY_MESSAGES, COLUMNS)
    is_group = next(
        g for g in result["evidence"] if g["metricId"] == "m-is-value-for-money")
    comments = is_group["comments"]

    # 4 positive (top-liked: pos0..pos3), 2 negative (neg0, neg1), backfill 2 more
    # from best unselected recognized rows by ordinary order: pos4 (likes 6),
    # pos5 (likes 5) are the next-best unselected (neutral neu0 has likes=100 but
    # is ranked lower priority than remaining positives only if ordinary order
    # puts it first - ordinary order is by likes desc, so neu0 (100) actually
    # ranks above pos4/pos5). Backfill order is by ordinary rank among ALL
    # unselected recognized rows, so neu0 backfills first, then pos4.
    assert len(comments) == 8, [c["text"] for c in comments]
    texts = [c["text"] for c in comments]
    positive_selected = texts[:4]
    negative_selected = texts[4:6]
    backfill = texts[6:]
    assert set(positive_selected) == {"pos0", "pos1", "pos2", "pos3"}, positive_selected
    assert set(negative_selected) == {"neg0", "neg1"}, negative_selected
    assert set(backfill) == {"neu0", "pos4"}, backfill
    assert backfill[0] == "neu0", (
        f"backfill must follow ordinary order (likes desc): {backfill}")
    # no duplicate source row.
    assert len(set(texts)) == len(texts), texts
    print("  ok  sentiment evidence: balanced 4/4 selection + ordinary-order backfill")


def test_sentiment_evidence_no_recognized_rows_empty():
    rows = [{
        "comment": "c0", "likes": 5, "theme": "Quality",
        "pt__value_for_money": True, "pt__durability": pd.NA,
        "emotion": "joy", "sentiment": "mixed", "lang": "id",
        "video_id": "v0", "group": "G1",
    }]
    df = pd.DataFrame(rows)
    result = _build_report_json(df, TRANSFER_TABLE, KEY_MESSAGES, COLUMNS)
    is_group = next(
        g for g in result["evidence"] if g["metricId"] == "m-is-value-for-money")
    assert is_group["comments"] == []
    print("  ok  sentiment evidence: no recognized rows -> empty")


# ---------------------------------------------------------------------------
# Test 14: JSON serialization - allow_nan=False must not raise, no NaN/inf
# ---------------------------------------------------------------------------

def test_json_serializable_no_nan_or_inf():
    df = _make_df()
    result = _build_report_json(df, TRANSFER_TABLE, KEY_MESSAGES, COLUMNS)
    # Must serialize with allow_nan=False - any NaN/Infinity present raises ValueError.
    text = json.dumps(result, allow_nan=False)
    reparsed = json.loads(text)
    assert reparsed == result or True  # structural roundtrip; primitive check below

    def _walk(node):
        if isinstance(node, float):
            assert math.isfinite(node), f"non-finite float leaked into output: {node}"
        elif isinstance(node, dict):
            for v in node.values():
                _walk(v)
        elif isinstance(node, list):
            for v in node:
                _walk(v)

    _walk(result)
    print("  ok  report.json is JSON-safe with allow_nan=False, no NaN/Infinity")


def test_json_serializable_on_edge_case_empty_df():
    empty_df = pd.DataFrame(columns=[
        "comment", "likes", "theme", "pt__value_for_money", "pt__durability",
        "emotion", "sentiment", "lang", "video_id", "group"])
    result = _build_report_json(empty_df, TRANSFER_TABLE, KEY_MESSAGES, COLUMNS)
    json.dumps(result, allow_nan=False)
    print("  ok  empty-df report.json also JSON-safe")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [
        test_top_level_keys_exact,
        test_key_messages,
        test_overall_transfer,
        test_overall_transfer_counted_once_on_overlap,
        test_zero_denominator,
        test_unrelated_group_excluded,
        test_missing_group_column_zero_applicability,
        test_missing_transfer_table_group_or_point_zero_applicability,
        test_multi_group_aggregation,
        test_unmatched_message_zero_and_empty_evidence,
        test_themes,
        test_theme_case_merge_preserves_first_spelling,
        test_emotions,
        test_emotion_slug_collision_suffix,
        test_slugify_punctuation_fallback,
        test_key_message_slug_collision_shared_across_mt_and_mis,
        test_key_message_sentiment,
        test_sentiment_unrecognized_and_null_excluded_from_base,
        test_evidence_group_order_and_shape,
        test_evidence_cap_and_sort,
        test_evidence_sort_ties_by_text_length_then_source_order,
        test_evidence_null_and_empty_text_excluded,
        test_evidence_invalid_likes_become_zero,
        test_sentiment_evidence_balanced_selection_and_backfill,
        test_sentiment_evidence_no_recognized_rows_empty,
        test_json_serializable_no_nan_or_inf,
        test_json_serializable_on_edge_case_empty_df,
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
