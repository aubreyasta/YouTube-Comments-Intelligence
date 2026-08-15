"""
Qwen benchmark preparation: real elapsed time, Macro F1, and a GPU memory
snapshot for a local Ollama Qwen model tag against a labelled comment
corpus, driven through a single merged prompt per batch covering Theme,
Key Messages, Sentiment, and Emotions together.

Non-thinking mode is set explicitly in this script's own /api/generate
requests (think=False) and recorded in the output record's thinkingMode
field. Before any full run, a single-batch timing gate sends one packed
batch of at most GATE_BATCH_SIZE comments and projects 3,000-comment
elapsed time from it; the full run aborts when that projection exceeds
RELEASE_THRESHOLD_SECONDS. GPU memory is a whole-GPU before/after
nvidia-smi reading, not a sampled peak: it will not catch a spike between
the two readings.

JSONL corpus format (one JSON object per line):
  {"id": "c1", "video_id": "abc123", "text": "text",
   "allowed_theme_labels": ["Praise", "Criticism"],
   "allowed_key_message_ids": ["km1", "km2"]}
  id                        required, str        - row identifier.
  video_id                  required, str        - groups comments the way analyze.classify does.
  text                      required, str        - the comment text.
  allowed_theme_labels      required, list[str]  - non-empty; index space the model emits theme choices against.
  allowed_key_message_ids   required, list[str]  - may be empty; index space for Key Message choices.

JSONL comparison format (one JSON object per line), resolved labels/IDs, never indices:
  {"id": "c1", "true_theme": "Praise", "predicted_theme": "Criticism",
   "true_key_message_ids": ["km1"], "predicted_key_message_ids": ["km2"]}

Model output for theme and Key Messages is a zero-based index (or list of
indices) into the row's allowed_theme_labels / allowed_key_message_ids
arrays; resolve_theme_index() and resolve_key_message_indices() convert
those indices back to labels/IDs and count invalid indices instead of
raising. Sentiment and Emotions are single-character codes resolved
through the fixed SENTIMENT_CODES / EMOTION_CODES maps.

Rows sharing one allowed-array identity are grouped into batches by
group_batches(); a batch never mixes different allowed_theme_labels /
allowed_key_message_ids, since the model answers with indices into those
arrays.

Themes and Key Messages are not read from the corpus for classification
purposes beyond what a row allows. This script classifies against a small
fixed theme book and no Key Messages, because discovery (analyze.build) is
a separate LLM call this benchmark is not timing.

Usage:
  python tests/bench_qwen.py --model qwen3:14b-q4_K_M --corpus corpus.jsonl
  python tests/bench_qwen.py --model qwen3:14b-q4_K_M --corpus corpus.jsonl --limit 3000 --output results.json
  python tests/bench_qwen.py --self-check
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from pipeline import llm
from pipeline.config_types import PipelineConfig

# Single-character codes the model emits for Sentiment and Emotions. Fixed
# map, resolved in Python. "neutral" takes U because N is negative Sentiment.
SENTIMENT_CODES = {"P": "positive", "N": "negative", "U": "neutral"}
EMOTION_CODES = {"A": "anger", "F": "fear", "J": "joy", "S": "sadness",
                 "O": "other_neutral"}
CORPUS_COLUMNS = ["id", "video_id", "text",
                  "allowed_theme_labels", "allowed_key_message_ids"]

RELEASE_THRESHOLD_SECONDS = 7200      # 2 hours for 3,000 comments
GATE_BATCH_SIZE = 20                  # packed batch the timing gate measures
THEME_MACRO_F1_FLOOR = 0.75
KEY_MESSAGE_MACRO_F1_FLOOR = 0.70
MIXED_MAX_DROP = 0.05                 # mixed stratum may trail overall by this
QWEN_CONFIDENCE_FILLER = 1.0          # schema filler, never a calibrated probability


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
            prefix = f"{path}:{line_number}"
            if not isinstance(row, dict):
                raise ValueError(f"{prefix}: each line must be a JSON object")
            required = ["id", "video_id", "text", "allowed_theme_labels", "allowed_key_message_ids"]
            missing = [field for field in required if field not in row]
            if missing:
                raise ValueError(f"{prefix}: missing field(s) {', '.join(missing)}")
            if not isinstance(row["id"], str) or not row["id"]:
                raise ValueError(f"{prefix}: id must be a non-empty string")
            if not isinstance(row["video_id"], str):
                raise ValueError(f"{prefix}: video_id must be a string")
            if not isinstance(row["text"], str):
                raise ValueError(f"{prefix}: text must be a string")
            allowed_theme_labels = row["allowed_theme_labels"]
            if not isinstance(allowed_theme_labels, list) or not allowed_theme_labels or \
                    not all(isinstance(item, str) for item in allowed_theme_labels):
                raise ValueError(f"{prefix}: allowed_theme_labels must be a non-empty list of strings")
            allowed_key_message_ids = row["allowed_key_message_ids"]
            if not isinstance(allowed_key_message_ids, list) or \
                    not all(isinstance(item, str) for item in allowed_key_message_ids):
                raise ValueError(f"{prefix}: allowed_key_message_ids must be a list of strings")
            rows.append({
                "id": row["id"],
                "video_id": row["video_id"],
                "text": row["text"],
                "allowed_theme_labels": list(allowed_theme_labels),
                "allowed_key_message_ids": list(allowed_key_message_ids),
            })
            if limit is not None and len(rows) >= limit:
                break
    if not rows:
        raise ValueError(f"{path}: no usable rows found")
    return pd.DataFrame(rows, columns=CORPUS_COLUMNS)


def load_comparison(path: str) -> dict[str, dict]:
    """
    Load the comparison JSONL row. Comparison rows store resolved labels
    and IDs, never indices.
    """
    rows: dict[str, dict] = {}
    with open(path, "r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON ({exc})") from exc
            prefix = f"{path}:{line_number}"
            if not isinstance(row, dict):
                raise ValueError(f"{prefix}: each line must be a JSON object")
            required = ["id", "true_theme", "predicted_theme",
                        "true_key_message_ids", "predicted_key_message_ids"]
            missing = [field for field in required if field not in row]
            if missing:
                raise ValueError(f"{prefix}: missing field(s) {', '.join(missing)}")
            if not isinstance(row["id"], str) or not row["id"]:
                raise ValueError(f"{prefix}: id must be a non-empty string")
            if not isinstance(row["true_theme"], str):
                raise ValueError(f"{prefix}: true_theme must be a string")
            if not isinstance(row["predicted_theme"], str):
                raise ValueError(f"{prefix}: predicted_theme must be a string")
            for field in ("true_key_message_ids", "predicted_key_message_ids"):
                value = row[field]
                if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                    raise ValueError(f"{prefix}: {field} must be a list of strings")
            if row["id"] in rows:
                raise ValueError(f"{prefix}: duplicate id {row['id']!r}")
            rows[row["id"]] = row
    if not rows:
        raise ValueError(f"{path}: no rows")
    return rows


def resolve_theme_index(value: object, allowed_theme_labels: list[str]) -> str | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value < 0 or value >= len(allowed_theme_labels):
        return None
    return allowed_theme_labels[value]


def resolve_key_message_indices(
    values: object, allowed_key_message_ids: list[str]
) -> tuple[list[str], int]:
    if not isinstance(values, list):
        return [], 1
    resolved: list[str] = []
    invalid_count = 0
    for value in values:
        label = resolve_theme_index(value, allowed_key_message_ids)
        if label is None:
            invalid_count += 1
            continue
        if label not in resolved:
            resolved.append(label)
    return resolved, invalid_count


def resolve_sentiment_code(code: object) -> str | None:
    if not isinstance(code, str):
        return None
    return SENTIMENT_CODES.get(code)


def resolve_emotion_code(code: object) -> str | None:
    if not isinstance(code, str):
        return None
    return EMOTION_CODES.get(code)


def preflight_text_model(cfg: PipelineConfig) -> tuple[str, str | None]:
    """
    Text-only preflight: checks Ollama version and TEXT_MODEL only.

    llm.preflight() also requires VISION_MODEL, which this benchmark
    never calls (classify() is text-only). Duplicated here instead of
    widening llm.preflight(), which is production preflight for the
    full pipeline including vision.

    Returns (ollama_version, model_digest_or_None).
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
    digests = {
        item.get("name", item.get("model")): item.get("digest")
        for item in models if isinstance(item, dict)
    }
    size = local.get(cfg.TEXT_MODEL)
    if cfg.TEXT_MODEL not in local or isinstance(size, bool) or not isinstance(size, (int, float)) or size <= 0:
        raise llm.OllamaModelError(f"Required local Ollama model tag missing:\nollama pull {cfg.TEXT_MODEL}")
    digest = digests.get(cfg.TEXT_MODEL)
    return version, digest if isinstance(digest, str) else None


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


def build_merged_prompt(batch_rows: list[dict]) -> str:
    """
    Build one packed prompt covering Theme, Key Messages, Sentiment, and
    Emotions for every row in batch_rows in a single call.

    All rows in batch_rows must share the same allowed_theme_labels and
    allowed_key_message_ids, since the model answers with indices into
    those arrays and a mixed batch would make the index space ambiguous.
    """
    first = batch_rows[0]
    allowed_theme_labels = first["allowed_theme_labels"]
    allowed_key_message_ids = first["allowed_key_message_ids"]
    for row in batch_rows:
        if row["allowed_theme_labels"] != allowed_theme_labels or \
                row["allowed_key_message_ids"] != allowed_key_message_ids:
            raise ValueError(
                "build_merged_prompt requires every row in a batch to share "
                "the same allowed_theme_labels and allowed_key_message_ids"
            )

    theme_list = "\n".join(f"{i}. {label}" for i, label in enumerate(allowed_theme_labels))
    if allowed_key_message_ids:
        key_message_list = "\n".join(
            f"{i}. {km_id}" for i, km_id in enumerate(allowed_key_message_ids)
        )
    else:
        key_message_list = "(none available; key_messages must be [])"
    sentiment_legend = "\n".join(f'"{code}" = {label}' for code, label in SENTIMENT_CODES.items())
    emotion_legend = "\n".join(f'"{code}" = {label}' for code, label in EMOTION_CODES.items())

    comments_block = "\n".join(
        f"{i}. (id={row['id']}) {row['text']}" for i, row in enumerate(batch_rows)
    )

    return (
        "You are labelling YouTube comments. For each comment below, "
        "return exactly one JSON object with exactly four fields:\n"
        '  "index": the zero-based integer index of the comment being answered '
        "(matching its position in the list below),\n"
        '  "theme": a single zero-based integer index into the Theme list, exactly one required,\n'
        '  "key_messages": a list of zero-based integer indices into the Key Message list, may be empty,\n'
        '  "sentiment": a single-character Sentiment code from the legend below,\n'
        '  "emotion": a single-character Emotions code from the legend below.\n\n'
        "You must answer every comment listed. Return a JSON array of these objects, "
        "one per comment.\n\n"
        f"Theme list:\n{theme_list}\n\n"
        f"Key Message list:\n{key_message_list}\n\n"
        f"Sentiment legend:\n{sentiment_legend}\n\n"
        f"Emotions legend:\n{emotion_legend}\n\n"
        f"Comments:\n{comments_block}\n"
    )


def parse_merged_reply(text: str, batch_rows: list[dict]) -> tuple[dict, dict]:
    """
    Parse a merged-prompt reply. Never raises on bad model output; counting
    invalid/malformed answers is the contract.

    Returns (resolved, invalid):
      resolved: id -> {"theme": str|None, "key_message_ids": list[str],
                        "sentiment": str|None, "emotion": str|None}
      invalid: {"theme": int, "keyMessageId": int, "sentiment": int,
                 "emotion": int, "malformed": int}
    """
    invalid = {"theme": 0, "keyMessageId": 0, "sentiment": 0, "emotion": 0, "malformed": 0}
    resolved = {
        row["id"]: {"theme": None, "key_message_ids": [], "sentiment": None, "emotion": None}
        for row in batch_rows
    }

    objects: list[dict] = []
    decoder = json.JSONDecoder()
    pos = 0
    length = len(text)
    while pos < length:
        start = None
        for i in range(pos, length):
            if text[i] in "{[":
                start = i
                break
        if start is None:
            break
        try:
            value, end = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            pos = start + 1
            continue
        if isinstance(value, dict):
            objects.append(value)
        elif isinstance(value, list):
            objects.extend(item for item in value if isinstance(item, dict))
        pos = end

    if not objects:
        invalid["malformed"] += 1
        return resolved, invalid

    addressed = [False] * len(batch_rows)
    for obj in objects:
        index = obj.get("index")
        if isinstance(index, bool) or not isinstance(index, int) or \
                index < 0 or index >= len(batch_rows):
            invalid["malformed"] += 1
            continue
        row = batch_rows[index]
        addressed[index] = True
        entry = resolved[row["id"]]

        theme = resolve_theme_index(obj.get("theme"), row["allowed_theme_labels"])
        if theme is None:
            invalid["theme"] += 1
        entry["theme"] = theme

        key_message_ids, km_invalid_count = resolve_key_message_indices(
            obj.get("key_messages"), row["allowed_key_message_ids"])
        invalid["keyMessageId"] += km_invalid_count
        entry["key_message_ids"] = key_message_ids

        sentiment = resolve_sentiment_code(obj.get("sentiment"))
        if sentiment is None:
            invalid["sentiment"] += 1
        entry["sentiment"] = sentiment

        emotion = resolve_emotion_code(obj.get("emotion"))
        if emotion is None:
            invalid["emotion"] += 1
        entry["emotion"] = emotion

    for was_addressed in addressed:
        if not was_addressed:
            invalid["malformed"] += 1

    return resolved, invalid


def macro_f1(y_true: list, y_pred: list) -> float:
    """
    Unweighted mean of per-label one-vs-rest F1 over the union of observed
    labels. A None prediction participates as a distinct non-matching
    value; it is never dropped, since dropping an invalid answer would
    flatter the model.
    """
    labels = sorted({label for label in y_true} | {label for label in y_pred}, key=repr)
    if not labels:
        return 0.0
    f1_scores = []
    for label in labels:
        tp = fp = fn = 0
        for true_value, pred_value in zip(y_true, y_pred):
            pred_is_label = pred_value == label
            true_is_label = true_value == label
            if pred_is_label and true_is_label:
                tp += 1
            elif pred_is_label and not true_is_label:
                fp += 1
            elif true_is_label and not pred_is_label:
                fn += 1
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
        f1_scores.append(f1)
    return sum(f1_scores) / len(f1_scores)


def key_message_macro_f1(true_map: dict, pred_map: dict, allowed_ids: list[str]) -> float:
    """
    Both maps go from comment id to a list of Key Message IDs. For each ID
    in allowed_ids, compute one-vs-rest F1 across all comments treating
    "this comment carries this ID" as the positive class. Returns the
    unweighted mean across allowed_ids, without reordering allowed_ids.
    """
    if not allowed_ids:
        return 0.0
    comment_ids = list(true_map.keys())
    f1_scores = []
    for km_id in allowed_ids:
        tp = fp = fn = 0
        for comment_id in comment_ids:
            true_has = km_id in true_map.get(comment_id, [])
            pred_has = km_id in pred_map.get(comment_id, [])
            if pred_has and true_has:
                tp += 1
            elif pred_has and not true_has:
                fp += 1
            elif true_has and not pred_has:
                fn += 1
        if tp == 0 and fp == 0 and fn == 0:
            f1_scores.append(0.0)
            continue
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
        f1_scores.append(f1)
    return sum(f1_scores) / len(f1_scores)


def group_batches(rows: list[dict], batch_size: int) -> list[list[dict]]:
    """
    Group rows sharing one allowed-array identity, then split each group
    into consecutive chunks of at most batch_size. A batch never mixes
    rows from different allowed_theme_labels / allowed_key_message_ids,
    since the model answers with indices into those arrays and mixing
    would make the index space ambiguous.

    Preserves first-appearance order of groups and original row order
    within a group. Never sorts, reverses, or dedupes either allowed array.
    """
    groups: dict[tuple, list[dict]] = {}
    for row in rows:
        key = (tuple(row["allowed_theme_labels"]), tuple(row["allowed_key_message_ids"]))
        groups.setdefault(key, []).append(row)

    batches: list[list[dict]] = []
    for group_rows in groups.values():
        for start in range(0, len(group_rows), batch_size):
            batches.append(group_rows[start:start + batch_size])
    return batches


def timing_gate(cfg: PipelineConfig, batch_rows: list[dict]) -> dict:
    """
    Process exactly one packed batch of at most GATE_BATCH_SIZE comments
    through the merged prompt, and project (never "measure") 3,000-comment
    elapsed time from it.
    """
    gate_rows = batch_rows[:GATE_BATCH_SIZE]
    prompt = build_merged_prompt(gate_rows)
    started = time.monotonic()
    reply = llm._call(cfg, "POST", "/api/generate", {
        "model": cfg.TEXT_MODEL, "prompt": prompt, "think": False, "stream": False,
    })
    single_batch_seconds = time.monotonic() - started
    output_tokens = reply.get("eval_count") if isinstance(reply, dict) else None
    if isinstance(output_tokens, bool) or not isinstance(output_tokens, int):
        output_tokens = None
    projected_from_single_batch_seconds = single_batch_seconds / len(gate_rows) * 3000
    return {
        "singleBatchSeconds": single_batch_seconds,
        "singleBatchOutputTokens": output_tokens,
        "projectedFromSingleBatchSeconds": projected_from_single_batch_seconds,
    }


def write_predictions(path: str, rows: list[dict], field: str, model: str) -> None:
    """
    Write UTF-8 JSONL prediction rows, one object per line:
      {"id": ..., field: ..., "confidence": QWEN_CONFIDENCE_FILLER, "model": model}

    confidence is a schema filler carrying a constant (QWEN_CONFIDENCE_FILLER).
    It exists only so these files load through the classifier harness's
    prediction loader. No acceptance rule, selection rule, or exported
    column may read it.

    A row whose resolved label (rows[i][field]) is None is skipped: an
    unresolved label is not a prediction.
    """
    with open(path, "w", encoding="utf-8") as handle:
        for row in rows:
            label = row[field]
            if label is None:
                continue
            handle.write(json.dumps({
                "id": row["id"],
                field: label,
                "confidence": QWEN_CONFIDENCE_FILLER,
                "model": model,
            }) + "\n")


def run_benchmark(
    model: str, corpus_path: str, limit: int | None, batch_size: int,
    comparison_path: str | None = None, hardware: str = "unrecorded",
) -> dict:
    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size <= 0:
        raise ValueError("batch_size must be a positive integer")

    df = load_corpus(corpus_path, limit)
    cfg = build_config(model, batch_size)

    ollama_version, model_digest = preflight_text_model(cfg)

    context_length = int(os.environ.get("OLLAMA_CONTEXT_LENGTH", "4096"))
    num_parallel = int(os.environ.get("OLLAMA_NUM_PARALLEL", "1"))
    thinking_mode = "non-thinking: think=False in /api/generate payload"

    batch_rows = df.to_dict("records")
    grouped_batches = group_batches(batch_rows, batch_size)
    gate = timing_gate(cfg, grouped_batches[0])
    generated_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

    if gate["projectedFromSingleBatchSeconds"] > RELEASE_THRESHOLD_SECONDS:
        print(
            f"timing gate: projected {gate['projectedFromSingleBatchSeconds']:.1f}s for "
            f"3,000 comments exceeds the {RELEASE_THRESHOLD_SECONDS}s threshold; "
            "aborting before the full run."
        )
        return {
            "generatedAt": generated_at, "hardware": hardware, "ollamaVersion": ollama_version,
            "model": model, "modelDigest": model_digest, "batchSize": batch_size,
            "thinkingMode": thinking_mode, "contextLength": context_length,
            "numParallel": num_parallel,
            "singleBatchSeconds": gate["singleBatchSeconds"],
            "singleBatchOutputTokens": gate["singleBatchOutputTokens"],
            "projectedFromSingleBatchSeconds": gate["projectedFromSingleBatchSeconds"],
            "commentsProcessed": 0, "elapsedSeconds": None, "measured3000": False,
            "projected3000Seconds": None,
            "gpuMemoryBeforeMiB": None, "gpuMemoryAfterMiB": None,
            "malformedFinalBatches": None, "invalidThemeCount": None,
            "invalidKeyMessageIdCount": None, "invalidSentimentCount": None,
            "invalidEmotionCount": None, "themeMacroF1": None, "keyMessageMacroF1": None,
            "mixedThemeMacroF1": None, "mixedKeyMessageMacroF1": None,
            "passed": False,
        }

    comparison = load_comparison(comparison_path) if comparison_path else {}

    vram_before, vram_note_before = gpu_memory_snapshot_mib()
    started = time.monotonic()

    resolved_by_id: dict[str, dict] = {}
    malformed_final_batches = 0
    invalid_theme = invalid_key_message_id = invalid_sentiment = invalid_emotion = 0
    for chunk in grouped_batches:
        prompt = build_merged_prompt(chunk)
        reply = llm._call(cfg, "POST", "/api/generate", {
            "model": cfg.TEXT_MODEL, "prompt": prompt, "think": False, "stream": False,
        })
        reply_text = reply.get("response", "") if isinstance(reply, dict) else ""
        chunk_resolved, chunk_invalid = parse_merged_reply(reply_text, chunk)
        resolved_by_id.update(chunk_resolved)
        invalid_theme += chunk_invalid["theme"]
        invalid_key_message_id += chunk_invalid["keyMessageId"]
        invalid_sentiment += chunk_invalid["sentiment"]
        invalid_emotion += chunk_invalid["emotion"]
        malformed_final_batches += chunk_invalid["malformed"]

    elapsed_seconds = time.monotonic() - started
    vram_after, vram_note_after = gpu_memory_snapshot_mib()

    comments_processed = len(df)
    requested_full_run = limit is None or limit >= 3000
    measured_3000 = requested_full_run and comments_processed >= 3000
    projected_3000_seconds = (
        None if measured_3000 or comments_processed == 0
        else elapsed_seconds / comments_processed * 3000
    )

    theme_true = [comparison[row["id"]]["true_theme"] for row in batch_rows if row["id"] in comparison]
    theme_pred = [resolved_by_id[row["id"]]["theme"] for row in batch_rows if row["id"] in comparison]
    theme_macro_f1 = macro_f1(theme_true, theme_pred) if theme_true else None

    km_true_map = {row["id"]: comparison[row["id"]]["true_key_message_ids"]
                   for row in batch_rows if row["id"] in comparison}
    km_pred_map = {row["id"]: resolved_by_id[row["id"]]["key_message_ids"]
                   for row in batch_rows if row["id"] in comparison}
    all_allowed_ids: list[str] = []
    for row in batch_rows:
        if row["id"] in comparison:
            for km_id in row["allowed_key_message_ids"]:
                if km_id not in all_allowed_ids:
                    all_allowed_ids.append(km_id)
    key_message_macro_f1_score = (
        key_message_macro_f1(km_true_map, km_pred_map, all_allowed_ids)
        if km_true_map else None
    )

    mixed_ids = {
        row["id"] for row in batch_rows
        if row["id"] in comparison and row.get("language_stratum") == "mixed"
    }
    if mixed_ids:
        mixed_theme_true = [comparison[cid]["true_theme"] for cid in mixed_ids]
        mixed_theme_pred = [resolved_by_id[cid]["theme"] for cid in mixed_ids]
        mixed_theme_macro_f1 = macro_f1(mixed_theme_true, mixed_theme_pred)
        mixed_km_true_map = {cid: comparison[cid]["true_key_message_ids"] for cid in mixed_ids}
        mixed_km_pred_map = {cid: resolved_by_id[cid]["key_message_ids"] for cid in mixed_ids}
        mixed_key_message_macro_f1 = key_message_macro_f1(
            mixed_km_true_map, mixed_km_pred_map, all_allowed_ids)
    else:
        mixed_theme_macro_f1 = None
        mixed_key_message_macro_f1 = None

    invalid_counts = [invalid_theme, invalid_key_message_id, invalid_sentiment, invalid_emotion]
    required_numbers = [
        elapsed_seconds, malformed_final_batches, *invalid_counts,
        theme_macro_f1, key_message_macro_f1_score,
    ]
    mixed_ok = True
    if mixed_theme_macro_f1 is not None and theme_macro_f1 is not None:
        mixed_ok = mixed_ok and (theme_macro_f1 - mixed_theme_macro_f1 <= MIXED_MAX_DROP)
    if mixed_key_message_macro_f1 is not None and key_message_macro_f1_score is not None:
        mixed_ok = mixed_ok and (
            key_message_macro_f1_score - mixed_key_message_macro_f1 <= MIXED_MAX_DROP)

    passed = (
        measured_3000 is True
        and all(value is not None for value in required_numbers)
        and elapsed_seconds <= RELEASE_THRESHOLD_SECONDS
        and malformed_final_batches == 0
        and all(count == 0 for count in invalid_counts)
        and theme_macro_f1 is not None and theme_macro_f1 >= THEME_MACRO_F1_FLOOR
        and key_message_macro_f1_score is not None
        and key_message_macro_f1_score >= KEY_MESSAGE_MACRO_F1_FLOOR
        and mixed_ok
    )

    return {
        "generatedAt": generated_at,
        "hardware": hardware,
        "ollamaVersion": ollama_version,
        "model": model,
        "modelDigest": model_digest,
        "batchSize": batch_size,
        "thinkingMode": thinking_mode,
        "contextLength": context_length,
        "numParallel": num_parallel,
        "singleBatchSeconds": gate["singleBatchSeconds"],
        "singleBatchOutputTokens": gate["singleBatchOutputTokens"],
        "projectedFromSingleBatchSeconds": gate["projectedFromSingleBatchSeconds"],
        "commentsProcessed": comments_processed,
        "elapsedSeconds": elapsed_seconds,
        "measured3000": measured_3000,
        "projected3000Seconds": projected_3000_seconds,
        "gpuMemoryBeforeMiB": vram_before,
        "gpuMemoryAfterMiB": vram_after,
        "malformedFinalBatches": malformed_final_batches,
        "invalidThemeCount": invalid_theme,
        "invalidKeyMessageIdCount": invalid_key_message_id,
        "invalidSentimentCount": invalid_sentiment,
        "invalidEmotionCount": invalid_emotion,
        "themeMacroF1": theme_macro_f1,
        "keyMessageMacroF1": key_message_macro_f1_score,
        "mixedThemeMacroF1": mixed_theme_macro_f1,
        "mixedKeyMessageMacroF1": mixed_key_message_macro_f1,
        "passed": passed,
    }


def self_check() -> None:
    """
    Offline check. No Ollama, no nvidia-smi, no network.

    Corpus parsing is checked directly. Everything past that point runs
    through the real run_benchmark() path, with only the two external
    edges swapped: llm._call (HTTP transport preflight_text_model and the
    merged-prompt /api/generate calls use) and gpu_memory_snapshot_mib
    (nvidia-smi). preflight_text_model, run_benchmark, group_batches, and
    the 3000-comment honesty logic all run for real, unmocked.
    """
    import tempfile
    from unittest.mock import patch

    this_module = sys.modules[__name__]

    lines = [
        {"id": "c1", "video_id": "v1", "text": "nice",
         "allowed_theme_labels": ["Praise", "Criticism"],
         "allowed_key_message_ids": ["km1", "km2"]},
        {"id": "c2", "video_id": "v1", "text": "bad",
         "allowed_theme_labels": ["Praise", "Criticism"],
         "allowed_key_message_ids": ["km1", "km2"]},
        {"id": "c3", "video_id": "v2", "text": "why though",
         "allowed_theme_labels": ["Question"],
         "allowed_key_message_ids": []},
    ]
    with tempfile.TemporaryDirectory() as tmp:
        corpus_path = os.path.join(tmp, "corpus.jsonl")
        with open(corpus_path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(json.dumps(row) for row in lines) + "\n\n")

        df = load_corpus(corpus_path, None)
        assert len(df) == 3, f"expected 3 rows, got {len(df)}"
        assert list(df.columns) == CORPUS_COLUMNS, (
            f"corpus should carry exactly CORPUS_COLUMNS, got {list(df.columns)}")

        limited = load_corpus(corpus_path, 2)
        assert len(limited) == 2, f"--limit should cap rows, got {len(limited)}"

        bad_path = os.path.join(tmp, "bad.jsonl")
        with open(bad_path, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "id": "c1", "video_id": "v1",
                "allowed_theme_labels": ["Praise"], "allowed_key_message_ids": [],
            }) + "\n")  # missing "text"
        try:
            load_corpus(bad_path, None)
        except ValueError as exc:
            assert "text" in str(exc)
        else:
            raise AssertionError("missing text field should raise")

        # --- new corpus schema assertions ---

        empty_theme_path = os.path.join(tmp, "empty_theme.jsonl")
        with open(empty_theme_path, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "id": "c1", "video_id": "v1", "text": "x",
                "allowed_theme_labels": [], "allowed_key_message_ids": [],
            }) + "\n")
        try:
            load_corpus(empty_theme_path, None)
        except ValueError:
            pass
        else:
            raise AssertionError("empty allowed_theme_labels should raise")

        empty_km_path = os.path.join(tmp, "empty_km.jsonl")
        with open(empty_km_path, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "id": "c1", "video_id": "v1", "text": "x",
                "allowed_theme_labels": ["Praise"], "allowed_key_message_ids": [],
            }) + "\n")
        empty_km_df = load_corpus(empty_km_path, None)
        assert list(empty_km_df.iloc[0]["allowed_key_message_ids"]) == []

        # Order preservation: allowed_theme_labels must survive load and
        # resolve without any sort/dedupe/reorder.
        order_path = os.path.join(tmp, "order.jsonl")
        non_alpha = ["Zebra", "Apple", "Mango"]
        with open(order_path, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "id": "c1", "video_id": "v1", "text": "x",
                "allowed_theme_labels": non_alpha, "allowed_key_message_ids": [],
            }) + "\n")
        order_df = load_corpus(order_path, None)
        loaded_labels = order_df.iloc[0]["allowed_theme_labels"]
        assert list(loaded_labels) == non_alpha, (
            f"allowed_theme_labels must preserve original order, got {list(loaded_labels)}")
        assert resolve_theme_index(0, loaded_labels) == "Zebra", (
            "resolve_theme_index(0, ...) must return the first element as-loaded, unsorted")

        # --- resolve_theme_index ---

        allowed = ["Praise", "Criticism", "Question"]
        assert resolve_theme_index(0, allowed) == "Praise"
        assert resolve_theme_index(len(allowed) - 1, allowed) == "Question"
        assert resolve_theme_index(-1, allowed) is None
        assert resolve_theme_index(len(allowed), allowed) is None
        assert resolve_theme_index("1", allowed) is None
        assert resolve_theme_index(1.0, allowed) is None
        assert resolve_theme_index(True, allowed) is None
        assert resolve_theme_index(None, allowed) is None

        # --- resolve_key_message_indices ---

        km_ids = ["km1", "km2", "km3"]
        resolved, invalid = resolve_key_message_indices([0, 2], km_ids)
        assert resolved == ["km1", "km3"] and invalid == 0
        resolved, invalid = resolve_key_message_indices([], km_ids)
        assert resolved == [] and invalid == 0
        resolved, invalid = resolve_key_message_indices([0, 0], km_ids)
        assert resolved == ["km1"] and invalid == 0
        resolved, invalid = resolve_key_message_indices([5], km_ids)
        assert resolved == [] and invalid == 1
        resolved, invalid = resolve_key_message_indices(["0"], km_ids)
        assert resolved == [] and invalid == 1
        resolved, invalid = resolve_key_message_indices([True], km_ids)
        assert resolved == [] and invalid == 1
        resolved, invalid = resolve_key_message_indices(None, km_ids)
        assert resolved == [] and invalid == 1
        resolved, invalid = resolve_key_message_indices([0, 9, 1], km_ids)
        assert resolved == ["km1", "km2"] and invalid == 1

        # --- resolve_sentiment_code / resolve_emotion_code ---

        assert resolve_sentiment_code("P") == "positive"
        assert resolve_sentiment_code("N") == "negative"
        assert resolve_sentiment_code("U") == "neutral"
        for bad in ("p", "X", "", " P", None, 0):
            assert resolve_sentiment_code(bad) is None, f"resolve_sentiment_code({bad!r}) should be None"

        assert resolve_emotion_code("A") == "anger"
        assert resolve_emotion_code("F") == "fear"
        assert resolve_emotion_code("J") == "joy"
        assert resolve_emotion_code("S") == "sadness"
        assert resolve_emotion_code("O") == "other_neutral"
        for bad in ("o", "Z", None):
            assert resolve_emotion_code(bad) is None, f"resolve_emotion_code({bad!r}) should be None"

        # Pin the two codes most likely to collide/drop in a future map edit.
        assert resolve_sentiment_code("N") == "negative"
        assert resolve_emotion_code("O") == "other_neutral"

        # --- load_comparison ---

        comparison_path = os.path.join(tmp, "comparison.jsonl")
        comparison_lines = [
            {"id": "c1", "true_theme": "Praise", "predicted_theme": "Criticism",
             "true_key_message_ids": ["km1"], "predicted_key_message_ids": []},
            {"id": "c2", "true_theme": "Question", "predicted_theme": "Question",
             "true_key_message_ids": [], "predicted_key_message_ids": ["km2"]},
        ]
        with open(comparison_path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(json.dumps(row) for row in comparison_lines) + "\n")
        comparison = load_comparison(comparison_path)
        assert set(comparison.keys()) == {"c1", "c2"}
        assert comparison["c1"]["true_theme"] == "Praise"

        dup_comparison_path = os.path.join(tmp, "dup_comparison.jsonl")
        with open(dup_comparison_path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(json.dumps(row) for row in comparison_lines + [comparison_lines[0]]) + "\n")
        try:
            load_comparison(dup_comparison_path)
        except ValueError as exc:
            assert "duplicate id" in str(exc)
        else:
            raise AssertionError("duplicate comparison id should raise")

        missing_field_comparison_path = os.path.join(tmp, "missing_field_comparison.jsonl")
        with open(missing_field_comparison_path, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "id": "c1", "true_theme": "Praise", "predicted_theme": "Criticism",
                "predicted_key_message_ids": [],
            }) + "\n")  # missing true_key_message_ids
        try:
            load_comparison(missing_field_comparison_path)
        except ValueError as exc:
            assert "true_key_message_ids" in str(exc)
        else:
            raise AssertionError("missing true_key_message_ids should raise")

        bad_type_comparison_path = os.path.join(tmp, "bad_type_comparison.jsonl")
        with open(bad_type_comparison_path, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "id": "c1", "true_theme": "Praise", "predicted_theme": "Criticism",
                "true_key_message_ids": "km1", "predicted_key_message_ids": [],
            }) + "\n")  # true_key_message_ids not a list
        try:
            load_comparison(bad_type_comparison_path)
        except ValueError:
            pass
        else:
            raise AssertionError("non-list true_key_message_ids should raise")

        # --- real run_benchmark path, network and model edges mocked ---

        def fake_call(cfg, method, path, payload=None):
            if path == "/api/version":
                return {"version": "0.12.7"}
            if path == "/api/tags":
                # Only TEXT_MODEL is registered. If preflight_text_model
                # required VISION_MODEL (the bug being fixed), this would
                # raise, since VISION_MODEL never appears here.
                return {"models": [{"name": cfg.TEXT_MODEL, "size": 123}]}
            if path == "/api/generate":
                generate_calls.append(payload)
                return {"response": "[]", "eval_count": 1}
            raise AssertionError(f"unexpected call: {method} {path}")

        generate_calls = []
        with patch.object(llm, "_call", side_effect=fake_call) as call_mock, \
             patch.object(this_module, "gpu_memory_snapshot_mib",
                          return_value=(1000.0, "measured")):
            result = run_benchmark("qwen3:14b-q4_K_M", corpus_path, None, batch_size=10)

        # preflight makes 2 calls (version, tags); the merged-prompt path
        # adds 1 gate call + 2 full-run batch calls, since the 3 corpus
        # rows split into two allowed-array groups (c1+c2 share one set,
        # c3 has its own), each its own batch under batch_size=10, and the
        # gate call is separate from the full-run calls.
        assert call_mock.call_count == 5, (
            "expected 2 preflight calls (version, tags) + 1 gate call + "
            f"2 full-run batch calls, got {call_mock.call_count}")
        assert len(generate_calls) == 3, (
            "the merged prompt should have been sent to the model: 1 gate call, 2 full-run batch calls, "
            f"got {len(generate_calls)}")
        assert result["batchSize"] == 10, "batch_size should reach the record unchanged"
        assert result["commentsProcessed"] == 3
        assert result["measured3000"] is False, "3 comments must never read as measuring 3000"
        expected_projection = result["elapsedSeconds"] / 3 * 3000
        assert abs(result["projected3000Seconds"] - expected_projection) < 1e-9, (
            "projection must be computed from this run's own elapsedSeconds, not a fixed number")
        assert result["gpuMemoryBeforeMiB"] == 1000.0 and result["gpuMemoryAfterMiB"] == 1000.0

        # comparison_path must be accepted without raising; the 28-key
        # contract has no field that echoes the path back, so only the
        # absence of an error is asserted here.
        with patch.object(llm, "_call", side_effect=fake_call), \
             patch.object(this_module, "gpu_memory_snapshot_mib",
                          return_value=(1000.0, "measured")):
            result_with_comparison = run_benchmark(
                "qwen3:14b-q4_K_M", corpus_path, None, batch_size=10,
                comparison_path=comparison_path)
        assert result_with_comparison["commentsProcessed"] == 3

        # measured3000 must flip True only once 3000 were actually
        # processed, regardless of what --limit requested. A 3000-row
        # in-memory frame stands in for a real 3000-comment corpus so the
        # self-check stays fast.
        import pandas as pd
        big_df = pd.DataFrame({
            "id": [f"c{i}" for i in range(3000)],
            "video_id": ["v1"] * 3000,
            "text": ["x"] * 3000,
            "allowed_theme_labels": [["Praise"]] * 3000,
            "allowed_key_message_ids": [[]] * 3000,
        })
        with patch.object(this_module, "load_corpus", return_value=big_df), \
             patch.object(llm, "_call", side_effect=fake_call), \
             patch.object(this_module, "gpu_memory_snapshot_mib",
                          return_value=(None, "unavailable (nvidia-smi not found)")):
            big_result = run_benchmark("qwen3:14b-q4_K_M", corpus_path, None, batch_size=10)
        assert big_result["measured3000"] is True
        assert big_result["projected3000Seconds"] is None, (
            "an actually-measured run must not also carry a projection")

        # --batch-size <= 0 must be rejected before any preflight or model call.
        with patch.object(llm, "_call", side_effect=AssertionError("preflight must not run")):
            try:
                run_benchmark("qwen3:14b-q4_K_M", corpus_path, None, batch_size=0)
            except ValueError as exc:
                assert "batch_size" in str(exc)
            else:
                raise AssertionError("batch_size <= 0 should raise before execution")

        # A before/after snapshot mismatch (e.g. nvidia-smi flakes on one
        # call but not the other) must be reported honestly. The 28-key
        # record has no vram-note field, so this property is now asserted
        # directly against gpu_memory_snapshot_mib's own return value,
        # calling it twice the way run_benchmark does (once "before", once
        # "after") rather than against the record.
        class _FakeCompletedProcess:
            def __init__(self, stdout):
                self.stdout = stdout

        snapshot_outcomes = [
            _FakeCompletedProcess("1000\n"),
            subprocess.SubprocessError("boom"),
        ]

        def fake_run(*args, **kwargs):
            outcome = snapshot_outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        with patch.object(shutil, "which", return_value="/usr/bin/nvidia-smi"), \
             patch.object(subprocess, "run", side_effect=fake_run):
            before_value, before_note = gpu_memory_snapshot_mib()
            after_value, after_note = gpu_memory_snapshot_mib()
        assert before_value == 1000.0 and before_note == "measured"
        assert after_value is None and after_note == "unavailable (SubprocessError)"
        vram_note = (
            before_note if before_note == after_note
            else f"before: {before_note}; after: {after_note}"
        )
        assert vram_note == "before: measured; after: unavailable (SubprocessError)", vram_note

        # --- build_merged_prompt ---

        two_row_batch = [
            {"id": "c1", "text": "nice", "allowed_theme_labels": ["Praise", "Criticism"],
             "allowed_key_message_ids": ["km1", "km2"]},
            {"id": "c2", "text": "bad", "allowed_theme_labels": ["Praise", "Criticism"],
             "allowed_key_message_ids": ["km1", "km2"]},
        ]
        prompt = build_merged_prompt(two_row_batch)
        for label in ["Praise", "Criticism"]:
            assert label in prompt, f"prompt must contain theme label {label!r}"
        for km_id in ["km1", "km2"]:
            assert km_id in prompt, f"prompt must contain Key Message ID {km_id!r}"
        for code in SENTIMENT_CODES:
            assert f'"{code}"' in prompt, f"prompt must contain Sentiment code {code!r}"
        for code in EMOTION_CODES:
            assert f'"{code}"' in prompt, f"prompt must contain Emotions code {code!r}"
        assert "0. Praise" in prompt and "1. Criticism" in prompt, (
            "theme list must be numbered from 0 in array order")
        assert "0. km1" in prompt and "1. km2" in prompt, (
            "Key Message list must be numbered from 0 in array order")

        mismatched_batch = [
            {"id": "c1", "text": "x", "allowed_theme_labels": ["Praise"],
             "allowed_key_message_ids": []},
            {"id": "c2", "text": "y", "allowed_theme_labels": ["Criticism"],
             "allowed_key_message_ids": []},
        ]
        try:
            build_merged_prompt(mismatched_batch)
        except ValueError:
            pass
        else:
            raise AssertionError("mismatched allowed arrays in one batch should raise ValueError")

        zebra_batch = [
            {"id": "c1", "text": "x", "allowed_theme_labels": ["Zebra", "Apple", "Mango"],
             "allowed_key_message_ids": []},
        ]
        zebra_prompt = build_merged_prompt(zebra_batch)
        assert "0. Zebra" in zebra_prompt, "index 0 must render next to the first array element as-loaded"
        zebra_pos = zebra_prompt.index("Zebra")
        apple_pos = zebra_prompt.index("Apple")
        mango_pos = zebra_prompt.index("Mango")
        assert zebra_pos < apple_pos < mango_pos, (
            "rendered theme order must match array order (Zebra, Apple, Mango), not alphabetical")

        # --- parse_merged_reply ---

        well_formed_reply = json.dumps([
            {"index": 0, "theme": 0, "key_messages": [1], "sentiment": "P", "emotion": "J"},
            {"index": 1, "theme": 1, "key_messages": [], "sentiment": "N", "emotion": "A"},
        ])
        resolved, invalid = parse_merged_reply(well_formed_reply, two_row_batch)
        assert resolved["c1"] == {"theme": "Praise", "key_message_ids": ["km2"],
                                   "sentiment": "positive", "emotion": "joy"}
        assert resolved["c2"] == {"theme": "Criticism", "key_message_ids": [],
                                   "sentiment": "negative", "emotion": "anger"}
        assert invalid == {"theme": 0, "keyMessageId": 0, "sentiment": 0, "emotion": 0, "malformed": 0}

        fenced_reply = f"Here you go:\n```json\n{well_formed_reply}\n```\nDone."
        fenced_resolved, fenced_invalid = parse_merged_reply(fenced_reply, two_row_batch)
        assert fenced_resolved["c1"]["theme"] == "Praise", "fenced code block reply must still parse"
        assert fenced_invalid["malformed"] == 0

        non_json_reply = "I refuse to answer in JSON today."
        non_json_resolved, non_json_invalid = parse_merged_reply(non_json_reply, two_row_batch)
        assert non_json_invalid["malformed"] >= 1
        assert all(entry["theme"] is None for entry in non_json_resolved.values()), (
            "non-JSON reply must leave every row unresolved")

        out_of_range_theme_reply = json.dumps([
            {"index": 0, "theme": 99, "key_messages": [0], "sentiment": "P", "emotion": "J"},
            {"index": 1, "theme": 1, "key_messages": [], "sentiment": "N", "emotion": "A"},
        ])
        _, oor_invalid = parse_merged_reply(out_of_range_theme_reply, two_row_batch)
        assert oor_invalid["theme"] == 1 and oor_invalid["keyMessageId"] == 0 and \
            oor_invalid["sentiment"] == 0 and oor_invalid["emotion"] == 0 and oor_invalid["malformed"] == 0, (
            "out-of-range theme index must increment only invalid['theme']")

        non_int_km_reply = json.dumps([
            {"index": 0, "theme": 0, "key_messages": ["km1"], "sentiment": "P", "emotion": "J"},
            {"index": 1, "theme": 1, "key_messages": [], "sentiment": "N", "emotion": "A"},
        ])
        _, km_invalid = parse_merged_reply(non_int_km_reply, two_row_batch)
        assert km_invalid["keyMessageId"] == 1 and km_invalid["theme"] == 0 and \
            km_invalid["sentiment"] == 0 and km_invalid["emotion"] == 0 and km_invalid["malformed"] == 0, (
            "non-integer Key Message index must increment only invalid['keyMessageId']")

        unmapped_sentiment_reply = json.dumps([
            {"index": 0, "theme": 0, "key_messages": [], "sentiment": "Q", "emotion": "J"},
            {"index": 1, "theme": 1, "key_messages": [], "sentiment": "N", "emotion": "A"},
        ])
        _, sentiment_invalid = parse_merged_reply(unmapped_sentiment_reply, two_row_batch)
        assert sentiment_invalid["sentiment"] == 1 and sentiment_invalid["theme"] == 0 and \
            sentiment_invalid["keyMessageId"] == 0 and sentiment_invalid["emotion"] == 0 and \
            sentiment_invalid["malformed"] == 0, (
            "unmapped Sentiment code must increment only invalid['sentiment']")

        unmapped_emotion_reply = json.dumps([
            {"index": 0, "theme": 0, "key_messages": [], "sentiment": "P", "emotion": "Z"},
            {"index": 1, "theme": 1, "key_messages": [], "sentiment": "N", "emotion": "A"},
        ])
        _, emotion_invalid = parse_merged_reply(unmapped_emotion_reply, two_row_batch)
        assert emotion_invalid["emotion"] == 1 and emotion_invalid["theme"] == 0 and \
            emotion_invalid["keyMessageId"] == 0 and emotion_invalid["sentiment"] == 0 and \
            emotion_invalid["malformed"] == 0, (
            "unmapped Emotions code must increment only invalid['emotion']")

        omitted_row_reply = json.dumps([
            {"index": 0, "theme": 0, "key_messages": [], "sentiment": "P", "emotion": "J"},
        ])
        omitted_resolved, omitted_invalid = parse_merged_reply(omitted_row_reply, two_row_batch)
        assert omitted_resolved["c2"] == {"theme": None, "key_message_ids": [],
                                           "sentiment": None, "emotion": None}
        assert omitted_invalid["malformed"] == 1

        # --- macro_f1 ---

        perfect_true = ["a", "b", "a", "b"]
        perfect_pred = ["a", "b", "a", "b"]
        assert macro_f1(perfect_true, perfect_pred) == 1.0

        majority_true = ["a", "a", "a", "b"]
        majority_pred = ["a", "a", "a", "a"]
        majority_accuracy = sum(t == p for t, p in zip(majority_true, majority_pred)) / len(majority_true)
        majority_f1 = macro_f1(majority_true, majority_pred)
        assert majority_f1 < majority_accuracy, (
            "Macro F1 on a majority-only fixture must fall below accuracy, "
            f"got macro_f1={majority_f1} accuracy={majority_accuracy}")

        # --- key_message_macro_f1 ---

        km_perfect_true = {"c1": ["km1"], "c2": ["km2"]}
        km_perfect_pred = {"c1": ["km1"], "c2": ["km2"]}
        assert key_message_macro_f1(km_perfect_true, km_perfect_pred, ["km1", "km2"]) == 1.0

        km_missing_true = {"c1": ["km1"], "c2": ["km1"]}
        km_missing_pred = {"c1": ["km1"], "c2": ["km1"]}
        # km2 never appears as true or predicted positive for any comment: contributes 0.0.
        # km1 is a perfect match: contributes 1.0. Mean over ["km1", "km2"] is 0.5.
        km_missing_score = key_message_macro_f1(km_missing_true, km_missing_pred, ["km1", "km2"])
        assert km_missing_score == 0.5, f"expected exactly 0.5, got {km_missing_score}"

        # --- timing_gate ---

        gate_fake_duration = [0.0]

        def fake_call_for_gate(cfg, method, path, payload=None):
            gate_fake_duration[0] = 4.0
            return {"response": "[]", "eval_count": 42}

        gate_rows = [
            {"id": f"c{i}", "text": "x", "allowed_theme_labels": ["Praise"],
             "allowed_key_message_ids": []}
            for i in range(5)
        ]
        with patch.object(llm, "_call", side_effect=fake_call_for_gate), \
             patch.object(time, "monotonic", side_effect=[100.0, 104.0]):
            gate_result = timing_gate(build_config("m", 5), gate_rows)
        assert gate_result["singleBatchSeconds"] == 4.0
        assert gate_result["singleBatchOutputTokens"] == 42
        expected_gate_projection = 4.0 / 5 * 3000
        assert abs(gate_result["projectedFromSingleBatchSeconds"] - expected_gate_projection) < 1e-9, (
            "timing_gate's projection must come from its own measured seconds, not a constant")

        # --- run_benchmark aborts on a failing timing gate ---

        def fake_call_slow_gate(cfg, method, path, payload=None):
            if path == "/api/version":
                return {"version": "0.12.7"}
            if path == "/api/tags":
                return {"models": [{"name": cfg.TEXT_MODEL, "size": 123}]}
            if path == "/api/generate":
                full_run_calls.append(payload)
                return {"response": "[]", "eval_count": 1}
            raise AssertionError(f"unexpected call: {method} {path}")

        full_run_calls = []
        gate_monotonic_values = [0.0, RELEASE_THRESHOLD_SECONDS + 1.0]
        with patch.object(llm, "_call", side_effect=fake_call_slow_gate), \
             patch.object(this_module, "gpu_memory_snapshot_mib", return_value=(1000.0, "measured")), \
             patch.object(time, "monotonic", side_effect=gate_monotonic_values + [0.0] * 20):
            aborted_result = run_benchmark("qwen3:14b-q4_K_M", corpus_path, None, batch_size=10)
        assert aborted_result["passed"] is False
        assert len(full_run_calls) == 1, (
            "only the gate's own /api/generate call may happen; the full-run path must never run "
            f"after an aborting gate, got {len(full_run_calls)} generate calls")

        # --- 28-key record contract ---

        expected_keys = [
            "generatedAt", "hardware", "ollamaVersion", "model", "modelDigest", "batchSize",
            "thinkingMode", "contextLength", "numParallel", "singleBatchSeconds",
            "singleBatchOutputTokens", "projectedFromSingleBatchSeconds", "commentsProcessed",
            "elapsedSeconds", "measured3000", "projected3000Seconds", "gpuMemoryBeforeMiB",
            "gpuMemoryAfterMiB", "malformedFinalBatches", "invalidThemeCount",
            "invalidKeyMessageIdCount", "invalidSentimentCount", "invalidEmotionCount",
            "themeMacroF1", "keyMessageMacroF1", "mixedThemeMacroF1", "mixedKeyMessageMacroF1",
            "passed",
        ]
        assert list(result.keys()) == expected_keys, (
            f"record keys must be exactly the 28 locked names in order, got {list(result.keys())}")
        assert list(aborted_result.keys()) == expected_keys, (
            "an aborted record must carry the same 28 keys as a completed one")

        # --- measured3000 implies projected3000Seconds is None ---

        assert big_result["measured3000"] is True and big_result["projected3000Seconds"] is None

        # --- passed is False when any single invalid counter is nonzero ---

        def make_passing_kwargs():
            return dict(
                measured_3000=True, elapsed_seconds=1.0, malformed_final_batches=0,
                invalid_theme=0, invalid_key_message_id=0, invalid_sentiment=0, invalid_emotion=0,
                theme_macro_f1=1.0, key_message_macro_f1_score=1.0,
                mixed_theme_macro_f1=None, mixed_key_message_macro_f1=None,
            )

        def compute_passed(kw):
            invalid_counts = [kw["invalid_theme"], kw["invalid_key_message_id"],
                               kw["invalid_sentiment"], kw["invalid_emotion"]]
            required_numbers = [kw["elapsed_seconds"], kw["malformed_final_batches"], *invalid_counts,
                                 kw["theme_macro_f1"], kw["key_message_macro_f1_score"]]
            mixed_ok = True
            if kw["mixed_theme_macro_f1"] is not None and kw["theme_macro_f1"] is not None:
                mixed_ok = mixed_ok and (
                    kw["theme_macro_f1"] - kw["mixed_theme_macro_f1"] <= MIXED_MAX_DROP)
            if kw["mixed_key_message_macro_f1"] is not None and kw["key_message_macro_f1_score"] is not None:
                mixed_ok = mixed_ok and (
                    kw["key_message_macro_f1_score"] - kw["mixed_key_message_macro_f1"] <= MIXED_MAX_DROP)
            return (
                kw["measured_3000"] is True
                and all(value is not None for value in required_numbers)
                and kw["elapsed_seconds"] <= RELEASE_THRESHOLD_SECONDS
                and kw["malformed_final_batches"] == 0
                and all(count == 0 for count in invalid_counts)
                and kw["theme_macro_f1"] >= THEME_MACRO_F1_FLOOR
                and kw["key_message_macro_f1_score"] >= KEY_MESSAGE_MACRO_F1_FLOOR
                and mixed_ok
            )

        baseline = make_passing_kwargs()
        assert compute_passed(baseline) is True, "sanity: the passing baseline itself must pass"
        for counter_name in ["invalid_theme", "invalid_key_message_id", "invalid_sentiment", "invalid_emotion"]:
            broken = make_passing_kwargs()
            broken[counter_name] = 1
            assert compute_passed(broken) is False, (
                f"passed must be False when {counter_name} is nonzero, all else held passing")

        # --- passed is False when the mixed score trails the overall score too far ---

        mixed_drop_broken = make_passing_kwargs()
        mixed_drop_broken["mixed_theme_macro_f1"] = baseline["theme_macro_f1"] - MIXED_MAX_DROP - 0.01
        assert compute_passed(mixed_drop_broken) is False, (
            "passed must be False when the mixed theme score trails the overall score by more than MIXED_MAX_DROP")

        # This arithmetic mirrors run_benchmark's own passed computation exactly;
        # cross-check it against the real aborted_result, which must independently be False.
        assert aborted_result["passed"] is False

        # --- write_predictions ---

        pred_path = os.path.join(tmp, "predictions.jsonl")
        pred_rows = [
            {"id": "c1", "theme": "Praise"},
            {"id": "c2", "theme": None},
            {"id": "c3", "theme": "Question"},
        ]
        write_predictions(pred_path, pred_rows, "theme", "qwen3:14b-q4_K_M")
        with open(pred_path, "r", encoding="utf-8") as handle:
            pred_lines = [json.loads(line) for line in handle if line.strip()]
        assert len(pred_lines) == 2, "the row with label None must be skipped"
        assert {line["id"] for line in pred_lines} == {"c1", "c3"}
        for line in pred_lines:
            assert line["confidence"] == QWEN_CONFIDENCE_FILLER
            assert line["model"] == "qwen3:14b-q4_K_M"

        # --- thinkingMode ---

        assert isinstance(result["thinkingMode"], str) and len(result["thinkingMode"]) > 0, (
            "thinkingMode must be a non-empty string naming the mechanism")

        # --- group_batches ---

        five_same_rows = [
            {"id": f"c{i}", "text": "x", "allowed_theme_labels": ["Praise"],
             "allowed_key_message_ids": []}
            for i in range(5)
        ]
        sized_batches = group_batches(five_same_rows, 2)
        assert [len(batch) for batch in sized_batches] == [2, 2, 1], (
            f"5 same-allowed rows at batch_size=2 must split into sizes [2, 2, 1], "
            f"got {[len(batch) for batch in sized_batches]}")

        two_group_rows = [
            {"id": "c1", "text": "x", "allowed_theme_labels": ["Praise"],
             "allowed_key_message_ids": []},
            {"id": "c2", "text": "y", "allowed_theme_labels": ["Question"],
             "allowed_key_message_ids": []},
            {"id": "c3", "text": "z", "allowed_theme_labels": ["Praise"],
             "allowed_key_message_ids": []},
        ]
        two_group_batches = group_batches(two_group_rows, 10)
        for batch in two_group_batches:
            labels_in_batch = {tuple(row["allowed_theme_labels"]) for row in batch}
            assert len(labels_in_batch) == 1, "a batch must never mix different allowed arrays"
            build_merged_prompt(batch)  # must not raise

        order_sensitive_rows = [
            {"id": "c1", "text": "x", "allowed_theme_labels": ["Praise", "Criticism"],
             "allowed_key_message_ids": []},
            {"id": "c2", "text": "y", "allowed_theme_labels": ["Criticism", "Praise"],
             "allowed_key_message_ids": []},
        ]
        order_sensitive_batches = group_batches(order_sensitive_rows, 10)
        assert len(order_sensitive_batches) == 2, (
            "rows whose allowed arrays hold the same labels in a different order must land in different batches")

        preserve_order_rows = [
            {"id": "c1", "text": "x", "allowed_theme_labels": ["Praise"], "allowed_key_message_ids": []},
            {"id": "c2", "text": "y", "allowed_theme_labels": ["Praise"], "allowed_key_message_ids": []},
            {"id": "c3", "text": "z", "allowed_theme_labels": ["Praise"], "allowed_key_message_ids": []},
        ]
        preserved_batches = group_batches(preserve_order_rows, 10)
        assert [row["id"] for row in preserved_batches[0]] == ["c1", "c2", "c3"], (
            "group_batches must preserve row order within a batch")

        mixed_allowed_corpus_path = os.path.join(tmp, "mixed_allowed.jsonl")
        mixed_allowed_lines = [
            {"id": "m1", "video_id": "v1", "text": "a", "allowed_theme_labels": ["Praise", "Criticism"],
             "allowed_key_message_ids": ["km1"]},
            {"id": "m2", "video_id": "v1", "text": "b", "allowed_theme_labels": ["Question"],
             "allowed_key_message_ids": []},
        ]
        with open(mixed_allowed_corpus_path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(json.dumps(row) for row in mixed_allowed_lines) + "\n")
        with patch.object(llm, "_call", side_effect=fake_call), \
             patch.object(this_module, "gpu_memory_snapshot_mib", return_value=(1000.0, "measured")):
            mixed_allowed_result = run_benchmark(
                "qwen3:14b-q4_K_M", mixed_allowed_corpus_path, None, batch_size=10)
        assert mixed_allowed_result["commentsProcessed"] == 2, (
            "run_benchmark must complete on a mixed-allowed-set corpus without raising")

    print("self-check: ok")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Measure real classify() throughput, Macro F1, and a GPU "
                    "memory snapshot for a local Ollama Qwen model tag using a "
                    "single merged Theme/Key Messages/Sentiment/Emotions prompt "
                    "per batch, run in explicit non-thinking mode. A single-batch "
                    "timing gate runs first and aborts the full run when its "
                    f"projection exceeds the {RELEASE_THRESHOLD_SECONDS}s release threshold.",
        epilog="JSONL corpus format (one JSON object per line):\n"
              '  {"id": "c1", "video_id": "abc123", "text": "text",\n'
              '   "allowed_theme_labels": ["Praise", "Criticism"],\n'
              '   "allowed_key_message_ids": ["km1", "km2"]}\n'
              "  id                        required, str        - row identifier.\n"
              "  video_id                  required, str        - groups comments the way analyze.classify does.\n"
              "  text                      required, str        - the comment text.\n"
              "  allowed_theme_labels      required, list[str]  - non-empty; index space for theme choices.\n"
              "  allowed_key_message_ids   required, list[str]  - may be empty; index space for Key Message choices.\n\n"
              "JSONL comparison format (one JSON object per line), resolved labels/IDs, never indices:\n"
              '  {"id": "c1", "true_theme": "Praise", "predicted_theme": "Criticism",\n'
              '   "true_key_message_ids": ["km1"], "predicted_key_message_ids": ["km2"]}\n\n'
              "The model answers one merged prompt per batch covering Theme, Key Messages,\n"
              "Sentiment, and Emotions together. Theme and Key Messages are zero-based integer\n"
              "indices (or lists of indices) into the row's allowed_theme_labels /\n"
              "allowed_key_message_ids arrays. Sentiment and Emotions are single-character\n"
              "codes resolved through the fixed SENTIMENT_CODES / EMOTION_CODES maps.\n\n"
              "Non-thinking mode is set explicitly in this script (think=False in the\n"
              "/api/generate payload) and recorded in the output record's thinkingMode field.\n\n"
              "Before the full run, a single-batch timing gate sends one packed batch of at\n"
              f"most {GATE_BATCH_SIZE} comments and projects 3,000-comment elapsed time from it.\n"
              f"If that projection exceeds {RELEASE_THRESHOLD_SECONDS}s, the full run is aborted\n"
              "and the record's passed field is False.\n\n"
              "Themes and Key Messages classified are not read from the corpus beyond what a\n"
              "row allows; classification runs against a small fixed theme book and no Key\n"
              "Messages, since theme discovery (analyze.build) is a separate LLM call this\n"
              "script does not time.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--model", help="Ollama model tag, e.g. qwen3:14b-q4_K_M")
    parser.add_argument("--corpus", help="Path to a JSONL comment corpus")
    parser.add_argument("--comparison", help="Path to a JSONL comparison file")
    parser.add_argument("--output", help="Path to write machine-readable JSON results")
    parser.add_argument("--limit", type=int, default=None,
                        help="Only classify the first N comments from the corpus")
    parser.add_argument("--batch-size", type=int, default=25,
                        help="CLASSIFY_BATCH_SIZE to use (default: 25, matches config-template.py)")
    parser.add_argument("--hardware", default="unrecorded",
                        help="Free-text hardware label recorded in the output (default: unrecorded)")
    parser.add_argument("--self-check", action="store_true",
                        help="Run offline parsing/timing self-check and exit; no Ollama required")
    args = parser.parse_args()

    if args.self_check:
        self_check()
        return

    if not args.model or not args.corpus:
        parser.error("--model and --corpus are required unless --self-check is given")
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit must be a positive integer")
    if args.batch_size <= 0:
        parser.error("--batch-size must be a positive integer")

    result = run_benchmark(
        args.model, args.corpus, args.limit, args.batch_size, args.comparison, args.hardware)

    print(f"model:              {result['model']}")
    print(f"thinking mode:      {result['thinkingMode']}")
    print(f"timing gate:        {result['projectedFromSingleBatchSeconds']:.1f}s projected for "
          f"3,000 comments (threshold {RELEASE_THRESHOLD_SECONDS}s)")
    print(f"comments processed: {result['commentsProcessed']}")
    if result["elapsedSeconds"] is not None:
        print(f"elapsed:            {result['elapsedSeconds']:.1f}s")
    else:
        print("elapsed:            n/a (aborted at timing gate)")
    if result["measured3000"]:
        print("3,000-comment timing: ACTUALLY MEASURED")
    elif result["projected3000Seconds"] is not None:
        print("3,000-comment timing: NOT measured this run "
              f"(projected from {result['commentsProcessed']}: "
              f"{result['projected3000Seconds']:.1f}s, projection only)")
    else:
        print("3,000-comment timing: n/a")
    print(f"invalid counts:     theme={result['invalidThemeCount']} "
          f"keyMessageId={result['invalidKeyMessageIdCount']} "
          f"sentiment={result['invalidSentimentCount']} "
          f"emotion={result['invalidEmotionCount']} "
          f"malformedFinalBatches={result['malformedFinalBatches']}")
    print(f"theme Macro F1:     {result['themeMacroF1']}")
    print(f"keyMessage Macro F1: {result['keyMessageMacroF1']}")
    if result["gpuMemoryBeforeMiB"] is not None and result["gpuMemoryAfterMiB"] is not None:
        print(f"GPU memory before:  {result['gpuMemoryBeforeMiB']:.0f} MiB")
        print(f"GPU memory after:   {result['gpuMemoryAfterMiB']:.0f} MiB")
    print(f"passed:             {result['passed']}")

    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            json.dump(result, handle, indent=2)
        print(f"results written to {args.output}")


if __name__ == "__main__":
    main()
