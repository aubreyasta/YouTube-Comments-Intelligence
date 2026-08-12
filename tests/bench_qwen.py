"""
Qwen benchmark preparation: real elapsed time and a GPU memory snapshot
for pipeline.analyze.classify() against a labelled comment corpus, driven
through the same llm.classify_batch() boundary run.py uses.

This script does not download models or judge pass/fail. It measures.
The 40-minute/3000-comment release benchmark and the multilingual Macro F1
model-selection benchmark (see CHANGELOG.md "Approved plan") both need
this measurement; this script produces the timing and GPU-memory half of
that. GPU memory is a whole-GPU before/after nvidia-smi reading, not a
sampled peak: it will not catch a spike between the two readings.

JSONL corpus format (one JSON object per line):
  {"video_id": "abc123", "comment": "text"}
  video_id  required, str  - groups comments the way analyze.classify does.
  comment   required, str  - the comment text.

Themes and Key Messages are not read from the corpus. This script
classifies against a small fixed theme book and no Key Messages, because
discovery (analyze.build) is a separate LLM call this benchmark is not
timing. Only classify() throughput is measured.

Usage:
  python tests/bench_qwen.py qwen3:14b-q4_K_M corpus.jsonl
  python tests/bench_qwen.py qwen3:14b-q4_K_M corpus.jsonl --limit 3000 --out results.json
  python tests/bench_qwen.py --self-check
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from pipeline import analyze, llm
from pipeline.config_types import PipelineConfig

FIXED_THEMES = [
    {"name": "Praise", "definition": "Generic positive reaction."},
    {"name": "Criticism", "definition": "Generic negative reaction."},
    {"name": "Question", "definition": "Asks something about the video or product."},
    {"name": "Off-topic", "definition": "Unrelated banter or spam."},
]


def load_corpus(path: str, limit: int | None) -> pd.DataFrame:
    rows = []
    with open(path, "r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON ({exc})") from exc
            if not isinstance(row, dict) or "video_id" not in row or "comment" not in row:
                raise ValueError(f"{path}:{line_number}: each line needs video_id and comment")
            rows.append({
                "video_id": str(row["video_id"]),
                "comment": str(row["comment"]),
            })
            if limit is not None and len(rows) >= limit:
                break
    if not rows:
        raise ValueError(f"{path}: no usable rows found")
    return pd.DataFrame(rows)


def preflight_text_model(cfg: PipelineConfig) -> None:
    """
    Text-only preflight: checks Ollama version and TEXT_MODEL only.

    llm.preflight() also requires VISION_MODEL, which this benchmark
    never calls (classify() is text-only). Duplicated here instead of
    widening llm.preflight(), which is production preflight for the
    full pipeline including vision.
    """
    version = llm._call(cfg, "GET", "/api/version").get("version")
    if not isinstance(version, str):
        raise llm.OllamaResponseError("Ollama version response is invalid.")
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:[-+].*)?", version)
    if not match:
        raise llm.OllamaResponseError("Ollama version response is invalid.")
    if tuple(map(int, match.groups())) < llm.MIN_OLLAMA_VERSION:
        floor = ".".join(map(str, llm.MIN_OLLAMA_VERSION))
        raise llm.OllamaError(f"Ollama {floor} or newer is required; found {version}.")
    models = llm._call(cfg, "GET", "/api/tags").get("models")
    if not isinstance(models, list):
        raise llm.OllamaResponseError("Ollama tags response is invalid.")
    local = {
        item.get("name", item.get("model")): item.get("size")
        for item in models if isinstance(item, dict)
    }
    size = local.get(cfg.TEXT_MODEL)
    if cfg.TEXT_MODEL not in local or isinstance(size, bool) or not isinstance(size, (int, float)) or size <= 0:
        raise llm.OllamaModelError(f"Required local Ollama model tag missing:\nollama pull {cfg.TEXT_MODEL}")


def gpu_memory_snapshot_mib() -> tuple[float | None, str]:
    """Best-effort whole-GPU memory snapshot via nvidia-smi, at a single instant.

    This is a before/after reading, not a sampled peak: nvidia-smi is
    polled once per call, so a spike between calls is invisible. Do not
    call the result a peak.
    """
    if shutil.which("nvidia-smi") is None:
        return None, "unavailable (nvidia-smi not found)"
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10, check=True,
        ).stdout
        values = [float(line.strip()) for line in out.splitlines() if line.strip()]
        if not values:
            return None, "unavailable (nvidia-smi returned no data)"
        return max(values), "measured"
    except (subprocess.SubprocessError, OSError, ValueError) as exc:
        return None, f"unavailable ({exc.__class__.__name__})"


def build_config(model: str, batch_size: int) -> PipelineConfig:
    return PipelineConfig(
        YOUTUBE_API_KEY="", VIDEOS=[], SESSION_NAME="bench", OUTPUT_DIR="output",
        KEEP_LANGUAGES={"en", "id"}, MIN_COMMENT_LETTERS=4,
        MAX_COMMENTS_PER_VIDEO=100000, CODEBOOK_SAMPLE_SIZE=10,
        CODEBOOK_SAMPLE_MAX=50, CLASSIFY_BATCH_SIZE=batch_size,
        UNCLASSIFIED_LIMIT=30, EMOTION_MODEL="", SENTIMENT_MODEL="",
        REPORT_LANGUAGE="English", CAMPAIGN_CONTEXT="",
        TEXT_MODEL=model,
    )


def run_benchmark(model: str, corpus_path: str, limit: int | None, batch_size: int) -> dict:
    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size <= 0:
        raise ValueError("batch_size must be a positive integer")

    df = load_corpus(corpus_path, limit)
    cfg = build_config(model, batch_size)

    preflight_text_model(cfg)

    vram_before, vram_note_before = gpu_memory_snapshot_mib()
    started = time.monotonic()
    analyze.classify(df, FIXED_THEMES, [], cfg)
    elapsed_seconds = time.monotonic() - started
    vram_after, vram_note_after = gpu_memory_snapshot_mib()

    comments_measured = len(df)
    requested_full_run = limit is None or limit >= 3000
    measured_3000 = requested_full_run and comments_measured >= 3000

    # Report honestly when the before/after snapshots disagree (e.g. one
    # measured and the other failed) instead of only surfacing the
    # "before" note and silently dropping the "after" outcome.
    if vram_note_before == vram_note_after:
        vram_note = vram_note_before
    else:
        vram_note = f"before: {vram_note_before}; after: {vram_note_after}"

    return {
        "model": model,
        "corpus_path": corpus_path,
        "batch_size": batch_size,
        "comments_measured": comments_measured,
        "elapsed_seconds": elapsed_seconds,
        "comments_per_second": comments_measured / elapsed_seconds if elapsed_seconds > 0 else None,
        "measured_3000_comments": measured_3000,
        "projected_3000_comment_seconds": (
            None if measured_3000 or comments_measured == 0
            else elapsed_seconds / comments_measured * 3000
        ),
        "gpu_mem_before_mib": vram_before,
        "gpu_mem_after_mib": vram_after,
        "vram_note": vram_note,
    }


def self_check() -> None:
    """
    Offline check. No Ollama, no nvidia-smi, no network.

    Corpus parsing is checked directly. Everything past that point runs
    through the real run_benchmark() path, with only the three external
    edges swapped: llm._call (HTTP transport preflight_text_model uses),
    analyze.classify (the actual model call), and gpu_memory_snapshot_mib
    (nvidia-smi). preflight_text_model, run_benchmark, and the 3000-comment
    honesty logic all run for real, unmocked.
    """
    import tempfile
    from unittest.mock import patch

    this_module = sys.modules[__name__]

    lines = [
        {"video_id": "v1", "comment": "nice"},
        {"video_id": "v1", "comment": "bad"},
        {"video_id": "v2", "comment": "why though"},
    ]
    with tempfile.TemporaryDirectory() as tmp:
        corpus_path = os.path.join(tmp, "corpus.jsonl")
        with open(corpus_path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(json.dumps(row) for row in lines) + "\n\n")

        df = load_corpus(corpus_path, None)
        assert len(df) == 3, f"expected 3 rows, got {len(df)}"
        assert list(df.columns) == ["video_id", "comment"], (
            f"corpus should carry only video_id and comment, got {list(df.columns)}")

        limited = load_corpus(corpus_path, 2)
        assert len(limited) == 2, f"--limit should cap rows, got {len(limited)}"

        bad_path = os.path.join(tmp, "bad.jsonl")
        with open(bad_path, "w", encoding="utf-8") as handle:
            handle.write('{"video_id": "v1"}\n')  # missing "comment"
        try:
            load_corpus(bad_path, None)
        except ValueError as exc:
            assert "video_id and comment" in str(exc)
        else:
            raise AssertionError("missing comment field should raise")

        # --- real run_benchmark path, network and model edges mocked ---

        def fake_call(cfg, method, path, payload=None):
            if path == "/api/version":
                return {"version": "0.12.7"}
            if path == "/api/tags":
                # Only TEXT_MODEL is registered. If preflight_text_model
                # required VISION_MODEL (the bug being fixed), this would
                # raise, since VISION_MODEL never appears here.
                return {"models": [{"name": cfg.TEXT_MODEL, "size": 123}]}
            raise AssertionError(f"unexpected call: {method} {path}")

        def fake_classify(df, themes, points, cfg, on_progress=None):
            classify_calls.append(cfg)
            return df, {}

        classify_calls = []
        with patch.object(llm, "_call", side_effect=fake_call) as call_mock, \
             patch.object(analyze, "classify", side_effect=fake_classify), \
             patch.object(this_module, "gpu_memory_snapshot_mib",
                          return_value=(1000.0, "measured")):
            result = run_benchmark("qwen3:14b-q4_K_M", corpus_path, None, batch_size=10)

        assert call_mock.call_count == 2, (
            f"preflight should make exactly 2 HTTP calls (version, tags), got {call_mock.call_count}")
        assert len(classify_calls) == 1, "analyze.classify should run exactly once"
        assert classify_calls[0].CLASSIFY_BATCH_SIZE == 10, "batch_size should reach cfg unchanged"
        assert result["comments_measured"] == 3
        assert result["measured_3000_comments"] is False, "3 comments must never read as measuring 3000"
        expected_projection = result["elapsed_seconds"] / 3 * 3000
        assert abs(result["projected_3000_comment_seconds"] - expected_projection) < 1e-9, (
            "projection must be computed from this run's own elapsed_seconds, not a fixed number")
        assert result["gpu_mem_before_mib"] == 1000.0 and result["gpu_mem_after_mib"] == 1000.0
        assert result["vram_note"] == "measured"

        # measured_3000_comments must flip True only once 3000 were actually
        # processed, regardless of what --limit requested. A 3000-row
        # in-memory frame stands in for a real 3000-comment corpus so the
        # self-check stays fast.
        import pandas as pd
        big_df = pd.DataFrame({"video_id": ["v1"] * 3000, "comment": ["x"] * 3000})
        with patch.object(this_module, "load_corpus", return_value=big_df), \
             patch.object(llm, "_call", side_effect=fake_call), \
             patch.object(analyze, "classify", side_effect=fake_classify), \
             patch.object(this_module, "gpu_memory_snapshot_mib",
                          return_value=(None, "unavailable (nvidia-smi not found)")):
            big_result = run_benchmark("qwen3:14b-q4_K_M", corpus_path, None, batch_size=10)
        assert big_result["measured_3000_comments"] is True
        assert big_result["projected_3000_comment_seconds"] is None, (
            "an actually-measured run must not also carry a projection")

        # --batch-size <= 0 must be rejected before any preflight or model call.
        with patch.object(llm, "_call", side_effect=AssertionError("preflight must not run")), \
             patch.object(analyze, "classify", side_effect=AssertionError("classify must not run")):
            try:
                run_benchmark("qwen3:14b-q4_K_M", corpus_path, None, batch_size=0)
            except ValueError as exc:
                assert "batch_size" in str(exc)
            else:
                raise AssertionError("batch_size <= 0 should raise before execution")

        # A before/after snapshot mismatch (e.g. nvidia-smi flakes on one
        # call but not the other) must be reported honestly, not collapsed
        # to only the "before" outcome.
        snapshot_calls = [(1000.0, "measured"), (None, "unavailable (SubprocessError)")]
        with patch.object(llm, "_call", side_effect=fake_call), \
             patch.object(analyze, "classify", side_effect=fake_classify), \
             patch.object(this_module, "gpu_memory_snapshot_mib",
                          side_effect=lambda: snapshot_calls.pop(0)):
            mismatched = run_benchmark("qwen3:14b-q4_K_M", corpus_path, None, batch_size=10)
        assert mismatched["gpu_mem_before_mib"] == 1000.0
        assert mismatched["gpu_mem_after_mib"] is None
        assert mismatched["vram_note"] == (
            "before: measured; after: unavailable (SubprocessError)"
        ), mismatched["vram_note"]

    print("self-check: ok")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Measure real classify() throughput and a GPU memory "
                    "snapshot for a local Ollama Qwen model tag. Prepares "
                    "numbers for the release benchmark; does not judge pass/fail.",
        epilog="JSONL corpus format (one JSON object per line):\n"
              '  {"video_id": "abc123", "comment": "text"}\n'
              "  video_id  required, str  - groups comments the way analyze.classify does.\n"
              "  comment   required, str  - the comment text.\n\n"
              "Themes and Key Messages are not read from the corpus; classification runs\n"
              "against a small fixed theme book and no Key Messages, since theme discovery\n"
              "(analyze.build) is a separate LLM call this script does not time.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("model", nargs="?", help="Ollama model tag, e.g. qwen3:14b-q4_K_M")
    parser.add_argument("corpus", nargs="?", help="Path to a JSONL comment corpus")
    parser.add_argument("--out", help="Path to write machine-readable JSON results")
    parser.add_argument("--limit", type=int, default=None,
                        help="Only classify the first N comments from the corpus")
    parser.add_argument("--batch-size", type=int, default=25,
                        help="CLASSIFY_BATCH_SIZE to use (default: 25, matches config-template.py)")
    parser.add_argument("--self-check", action="store_true",
                        help="Run offline parsing/timing self-check and exit; no Ollama required")
    args = parser.parse_args()

    if args.self_check:
        self_check()
        return

    if not args.model or not args.corpus:
        parser.error("model and corpus are required unless --self-check is given")
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit must be a positive integer")
    if args.batch_size <= 0:
        parser.error("--batch-size must be a positive integer")

    result = run_benchmark(args.model, args.corpus, args.limit, args.batch_size)

    print(f"model:              {result['model']}")
    print(f"comments measured:  {result['comments_measured']}")
    print(f"elapsed:            {result['elapsed_seconds']:.1f}s")
    if result["comments_per_second"] is not None:
        print(f"rate:               {result['comments_per_second']:.2f} comments/s")
    else:
        print("rate:               n/a")
    if result["measured_3000_comments"]:
        print("3,000-comment timing: ACTUALLY MEASURED")
    else:
        print("3,000-comment timing: NOT measured this run "
              f"(projected from {result['comments_measured']}: "
              f"{result['projected_3000_comment_seconds']:.1f}s, projection only)")
    if result["gpu_mem_before_mib"] is not None and result["gpu_mem_after_mib"] is not None:
        print(f"GPU memory before:  {result['gpu_mem_before_mib']:.0f} MiB")
        print(f"GPU memory after:   {result['gpu_mem_after_mib']:.0f} MiB")
    else:
        print(f"GPU memory:         {result['vram_note']}")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(result, handle, indent=2)
        print(f"results written to {args.out}")


if __name__ == "__main__":
    main()
