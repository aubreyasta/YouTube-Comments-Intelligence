"""
Offline self-check for adapter._build_evidence and _build_report_json
idea-sentiment logic. No API calls, no real models.

Run: python tests/test_evidence.py
"""

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

# Import the two private functions directly - they only use stdlib + pandas.
import importlib.util as ilu
spec2 = ilu.spec_from_file_location("adapter", os.path.join(repo_root, "adapter.py"))
adapter = ilu.module_from_spec(spec2)
spec2.loader.exec_module(adapter)

_build_evidence   = adapter._build_evidence
_build_report_json = adapter._build_report_json


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

COLUMNS = {
    "pt__value_for_money": "value for money",
    "pt__durability":      "durability",
}

def _make_df():
    rows = []
    for i in range(20):
        rows.append({
            "comment": f"comment text number {i} " + "x" * (i * 3),
            "likes": i,
            "theme": "Quality" if i % 3 == 0 else ("Price" if i % 3 == 1 else "Other"),
            "pt__value_for_money": (i % 2 == 0),
            "pt__durability": pd.NA,
            "emotion": "joy" if i % 2 == 0 else "neutral",
            "emotion_confidence": 0.8,
            "sentiment": "positive" if i < 10 else "negative",
            "sentiment_confidence": 0.75,
            "lang": "id",
            "group": "G1",
            "video_id": "v1",
        })
    # One zero-echo row for durability (all NA - treated as not shown)
    # Make one durability-echo comment to test non-zero path too.
    rows[1]["pt__durability"] = True
    df = pd.DataFrame(rows)
    # Convert pt__ cols: NA stays NA, True/False as bool-ish
    df["pt__value_for_money"] = df["pt__value_for_money"].astype(object)
    df["pt__durability"] = df["pt__durability"]
    return df

TRANSFER_TABLE = pd.DataFrame([
    {"group": "G1", "point": "value for money", "echoed_pct": 50.0, "n": 10},
    {"group": "G1", "point": "durability",       "echoed_pct":  5.0, "n": 1},
])

THEMES_LIST = [
    {"name": "Quality",    "definition": "..."},
    {"name": "Price",      "definition": "..."},
]

THEME_TABLE = pd.DataFrame()  # not used in _build_evidence


# ---------------------------------------------------------------------------
# Test (a): _build_evidence returns rows for a theme metric (m-th-)
# proving the old index-membership bug is fixed
# ---------------------------------------------------------------------------

def test_theme_evidence_not_empty():
    df = _make_df()
    transfers, themes_json, evidence = _build_evidence(
        df, TRANSFER_TABLE, THEMES_LIST, COLUMNS)

    theme_metric_ids = {t["id"] for t in themes_json if t["label"] != "Other"}
    assert theme_metric_ids, "No non-Other theme metrics built"

    ev_for_themes = [e for e in evidence if e["metricId"] in theme_metric_ids]
    assert len(ev_for_themes) > 0, (
        "BUG: _build_evidence returned zero evidence rows for theme metrics. "
        "The old index-membership bug is still present.")
    print(f"  ok  theme evidence: {len(ev_for_themes)} rows for {len(theme_metric_ids)} theme metrics")


# ---------------------------------------------------------------------------
# Test (b): ideaSentiment for a zero-echo idea yields n=0 and 0/0/0
# ---------------------------------------------------------------------------

def test_idea_sentiment_zero_echo():
    df = _make_df()
    # Make pt__durability all NA (no echoes).
    df["pt__durability"] = pd.NA

    TRANSFER_ZERO = pd.DataFrame([
        {"group": "G1", "point": "durability", "echoed_pct": 0.0, "n": 0},
    ])

    # Build a minimal affect_result so _build_report_json can run.
    affect_result = {
        "emotion":   {"table": pd.DataFrame(), "low_confidence_pct": 0, "caveat": "stub"},
        "sentiment": {"table": pd.DataFrame(), "low_confidence_pct": 0, "caveat": "stub"},
    }

    # Minimal PipelineConfig (only fields _build_report_json touches via _build_prose).
    from pipeline.config_types import PipelineConfig
    cfg = PipelineConfig(
        YOUTUBE_API_KEY="", OLLAMA_BASE_URL="http://127.0.0.1:11434",
        TEXT_MODEL="qwen3:8b", VISION_MODEL="qwen3-vl:8b",
        OLLAMA_TEXT_NUM_CTX=32768, OLLAMA_VISION_NUM_CTX=16384,
        OLLAMA_TIMEOUT_SECONDS=2, OLLAMA_KEEP_ALIVE="5m",
        VIDEOS=[], SESSION_NAME="test", OUTPUT_DIR="/tmp",
        KEEP_LANGUAGES={"id"}, MIN_COMMENT_LETTERS=4,
        MAX_COMMENTS_PER_VIDEO=100, CODEBOOK_SAMPLE_SIZE=10,
        CODEBOOK_SAMPLE_MAX=50, CLASSIFY_BATCH_SIZE=10,
        UNCLASSIFIED_LIMIT=30, EMOTION_MODEL="", SENTIMENT_MODEL="",
        REPORT_LANGUAGE="English", CAMPAIGN_CONTEXT="",
    )

    # Monkeypatch _build_prose to skip the LLM call.
    original_prose = adapter._build_prose
    adapter._build_prose = lambda *a, **kw: {
        "title": "stub", "interpretation": "stub",
        "quote": {"text": "q", "attr": "a"}, "caveat": "stub",
    }
    try:
        result = _build_report_json(
            "run1", {"name": "s1"}, [{"url": "x", "kind": "auto"}],
            df, TRANSFER_ZERO, THEME_TABLE, THEMES_LIST,
            {"pt__durability": "durability"},
            "grounded text", affect_result, cfg)
    finally:
        adapter._build_prose = original_prose

    idea_sent = result["ideaSentiment"]
    assert len(idea_sent) == 1, f"Expected 1 ideaSentiment row, got {len(idea_sent)}"
    row = idea_sent[0]
    assert row["n"] == 0, f"Expected n=0 for zero-echo idea, got {row['n']}"
    assert row["positive"] == 0 and row["neutral"] == 0 and row["negative"] == 0, (
        f"Expected 0/0/0 for zero-echo idea, got {row}")
    assert row["id"].startswith("m-is-"), f"id prefix wrong: {row['id']}"
    print(f"  ok  idea-sentiment zero-echo: {row}")


# ---------------------------------------------------------------------------
# Test (c): transfer metric evidence respects the 8-cap
# ---------------------------------------------------------------------------

def test_transfer_evidence_cap():
    # 20 rows all echo "value for money".
    df = _make_df()
    df["pt__value_for_money"] = True

    TRANSFER_ALL = pd.DataFrame([
        {"group": "G1", "point": "value for money", "echoed_pct": 100.0, "n": 20},
    ])

    _, _, evidence = _build_evidence(df, TRANSFER_ALL, THEMES_LIST, COLUMNS)

    vfm_ev = [e for e in evidence if e["metricId"] == "m-t-value-for-money"]
    assert len(vfm_ev) <= 8, (
        f"Expected at most 8 evidence rows for transfer metric, got {len(vfm_ev)}")
    assert len(vfm_ev) == 8, (
        f"Expected exactly 8 evidence rows (20 eligible, cap=8), got {len(vfm_ev)}")
    # Check they are ranked by likes desc.
    likes_seq = [e["likes"] for e in vfm_ev]
    assert likes_seq == sorted(likes_seq, reverse=True), (
        f"Evidence rows not sorted by likes desc: {likes_seq}")
    print(f"  ok  transfer evidence cap=8, likes desc: {likes_seq}")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [
        test_theme_evidence_not_empty,
        test_idea_sentiment_zero_echo,
        test_transfer_evidence_cap,
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
