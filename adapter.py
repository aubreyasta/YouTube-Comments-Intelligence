"""
adapter.py - run execution engine for the YouTube Comment Intelligence backend.

This module wraps the existing pipeline in a daemon thread, streams progress
via per-run queues, and handles the brief-pause/proceed interrupt.

Public API (for server.py / Wave 3):
-------------------------------------
  start_run(run_id: str) -> None
      Start a daemon thread executing the full pipeline for run_id.
      Returns immediately. Raises ValueError if run_id not found in DB.

  get_queue(run_id: str) -> queue.Queue
      Return the progress queue for run_id. Creates it on demand.
      Each item is a progress dict (see PROGRESS_SHAPE below).

  get_proceed_event(run_id: str) -> threading.Event
      Return the proceed event for run_id. The SSE endpoint should call
      .set() on this after PATCH /runs/{id}/brief_points + POST /runs/{id}/proceed.

  is_terminal(run_id: str) -> bool
      True if the run has pushed a 'complete' or 'error' stage event.
      Use this to know when to close the SSE stream.

PROGRESS_SHAPE:
  {
    "run_id":  str,           # the run UUID
    "stage":   str,           # collect | brief | brief_pause | classify |
                              # emotion | report | complete | error
    "message": str,           # human-readable status line
    "pct":     int,           # 0-100
    "detail":  str | None,    # extra context (error message, counts, etc.)
  }
"""

import json
import logging
import os
import queue
import re
import shutil
import threading
import uuid
from datetime import datetime, timezone

import pandas as pd

# Load .env at repo root if present; must happen before any env reads.
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass  # python-dotenv optional; env vars may be set by the shell

import db
import storage
from pipeline import collect, brief, analyze, report as pipeline_report
from pipeline import llm as pipeline_llm
from pipeline.config_types import PipelineConfig

logger = logging.getLogger(__name__)

# Module-level state: one queue and one event per run.
_queues: dict[str, queue.Queue] = {}
_proceed_events: dict[str, threading.Event] = {}
_terminal: dict[str, bool] = {}
_lock = threading.Lock()

# Single source of truth for the seven (kind, filename) pairs a completed
# run must produce under out_dir. server.py's _ARTIFACT_CONTRACT carries
# the matching order/MIME/public-visibility contract for the API side.
_ARTIFACT_FILES: list[tuple[str, str]] = [
    ("report_pdf",        "report.pdf"),
    ("comments_csv",      "comments.csv"),
    ("key_messages_csv",  "key-messages.csv"),
    ("themes_csv",        "themes.csv"),
    ("sentiment_csv",     "sentiment.csv"),
    ("emotions_csv",      "emotions.csv"),
    ("report_json",       "report.json"),
]


def _missing_required_artifacts(out_dir: str) -> list[str]:
    """Filenames from _ARTIFACT_FILES not present under out_dir."""
    return [filename for _kind, filename in _ARTIFACT_FILES
            if not os.path.isfile(os.path.join(out_dir, filename))]


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def get_queue(run_id: str) -> queue.Queue:
    """Return (create on demand) the progress queue for run_id."""
    with _lock:
        if run_id not in _queues:
            _queues[run_id] = queue.Queue()
        return _queues[run_id]


def get_proceed_event(run_id: str) -> threading.Event:
    """Return (create on demand) the proceed threading.Event for run_id."""
    with _lock:
        if run_id not in _proceed_events:
            _proceed_events[run_id] = threading.Event()
        return _proceed_events[run_id]


def is_terminal(run_id: str) -> bool:
    """True once 'complete' or 'error' has been pushed to the queue."""
    return _terminal.get(run_id, False)


def start_run(run_id: str) -> None:
    """
    Spawn a daemon thread executing _execute(run_id). Returns immediately.
    Raises ValueError if the run does not exist in the DB.
    """
    conn = db.get_conn()
    try:
        row = conn.execute("SELECT id FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise ValueError(f"run {run_id!r} not found")
    finally:
        conn.close()

    t = threading.Thread(target=_execute, args=(run_id,), daemon=True,
                         name=f"adapter-run-{run_id[:8]}")
    t.start()


# ---------------------------------------------------------------------------
# Progress helpers
# ---------------------------------------------------------------------------

def _push(run_id: str, stage: str, message: str, pct: int,
          detail: str | None = None) -> None:
    get_queue(run_id).put({
        "run_id": run_id,
        "stage": stage,
        "message": message,
        "pct": pct,
        "detail": detail,
    })
    # Persisted alongside the queue event (not instead of it) so GET
    # /runs/{id} can report brief_pause after a tab reopens with no SSE
    # connection to replay from - the queue is per-process and empties
    # once drained, but this column survives.
    _set_run_stage(run_id, stage)
    if stage in ("complete", "error"):
        with _lock:
            _terminal[run_id] = True


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _set_run_state(run_id: str, state: str, **extra_cols) -> None:
    cols = {"state": state, **extra_cols}
    set_clause = ", ".join(f"{k} = ?" for k in cols)
    conn = db.get_conn()
    try:
        conn.execute(
            f"UPDATE runs SET {set_clause} WHERE id = ?",
            (*cols.values(), run_id))
        conn.commit()
    finally:
        conn.close()


def _set_run_stage(run_id: str, stage: str) -> None:
    """Persist the fine-grained SSE stage onto the run row. Best-effort:
    a run that vanished mid-flight (deleted by a later overwrite) is not
    an error worth surfacing from inside a progress callback."""
    conn = db.get_conn()
    try:
        conn.execute("UPDATE runs SET stage = ? WHERE id = ?", (stage, run_id))
        conn.commit()
    finally:
        conn.close()


def _load_run(run_id: str) -> dict:
    """Return the run row as a plain dict."""
    conn = db.get_conn()
    try:
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise ValueError(f"run {run_id!r} not found")
        return dict(row)
    finally:
        conn.close()


def _load_session(session_id: str) -> dict:
    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return dict(row)
    finally:
        conn.close()


def _load_campaign(session_id: str) -> dict:
    """Return the single campaign for this session (single-campaign model)."""
    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM campaigns WHERE session_id = ?", (session_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"no campaign for session {session_id!r}")
        return dict(row)
    finally:
        conn.close()


def _load_videos(campaign_id: str) -> list[dict]:
    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM videos WHERE campaign_id = ?", (campaign_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _load_assets(campaign_id: str) -> list[dict]:
    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM assets WHERE campaign_id = ?", (campaign_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _load_session_key_messages(session_id: str) -> list[dict]:
    """Return the Session's key_messages rows as KeyMessage dicts (id,
    label, description, included, order, edited), in sort_order. This is
    the immutable read side of the run snapshot: adapter.py never writes
    back to this table."""
    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM key_messages WHERE session_id = ? ORDER BY sort_order",
            (session_id,)).fetchall()
        return [
            {
                "id": r["id"], "label": r["label"], "description": r["description"],
                "included": bool(r["included"]), "order": r["sort_order"],
                "edited": bool(r["edited"]),
            }
            for r in rows
        ]
    finally:
        conn.close()


def _replace_brief_points(run_id: str, campaign_id: str, points: list[dict]) -> None:
    """Replace this run's brief_points with `points` (KeyMessage-shaped
    dicts: id, label, description, included, order, edited). video_id is
    always NULL here - these are Session-level Key Messages, not the old
    per-video brief() points - so analyze.classify() broadcasts each one
    to every video's batch (see pipeline/analyze.py).

    Called twice per run: once with the raw Session snapshot before
    collect (so a crash before brief_pause still leaves an auditable
    starting point), once with the transcript-reconciled list right
    before brief_pause. Both calls delete-then-reinsert under this run's
    id, never touching key_messages.
    """
    conn = db.get_conn()
    try:
        conn.execute("DELETE FROM brief_points WHERE run_id = ?", (run_id,))
        for pt in points:
            conn.execute(
                """INSERT INTO brief_points
                   (id, run_id, campaign_id, video_id, label, description,
                    approved, edited, included, sort_order)
                   VALUES (?, ?, ?, NULL, ?, ?, 0, ?, ?, ?)""",
                (pt["id"], run_id, campaign_id, pt["label"], pt.get("description", ""),
                 int(pt.get("edited", False)), int(pt.get("included", True)),
                 pt.get("order", 0)))
        conn.commit()
    finally:
        conn.close()


def _load_brief_points(run_id: str) -> list[dict]:
    """Return brief_point rows as plain dicts, in sort_order."""
    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM brief_points WHERE run_id = ? ORDER BY sort_order",
            (run_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _insert_artifact(run_id: str, kind: str, file_path: str) -> None:
    conn = db.get_conn()
    try:
        conn.execute(
            "INSERT INTO run_artifacts (id, run_id, kind, file_path) VALUES (?,?,?,?)",
            (str(uuid.uuid4()), run_id, kind, file_path))
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Config builder
# ---------------------------------------------------------------------------

def _build_config(run_id: str | None, session_row: dict, campaign: dict,
                  videos: list[dict], campaign_context: dict) -> PipelineConfig:
    """Build a PipelineConfig from DB rows and environment variables.

    run_id=None skips creating data/runs/{run_id}/ on disk. Used by the
    Key Message draft route (server.py), which calls draft_from_inputs()
    directly and never writes run output files.
    """
    return PipelineConfig(
        YOUTUBE_API_KEY=os.environ.get("YOUTUBE_API_KEY", ""),
        OLLAMA_BASE_URL=os.environ.get(
            "OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
        TEXT_MODEL=os.environ.get("OLLAMA_TEXT_MODEL", "qwen3:14b-q4_K_M"),
        VISION_MODEL=os.environ.get(
            "OLLAMA_VISION_MODEL", "qwen3-vl:8b-instruct-q4_K_M"),
        OLLAMA_TEXT_NUM_CTX=int(os.environ.get(
            "OLLAMA_TEXT_NUM_CTX", "32768")),
        OLLAMA_VISION_NUM_CTX=int(os.environ.get(
            "OLLAMA_VISION_NUM_CTX", "8192")),
        OLLAMA_TIMEOUT_SECONDS=int(os.environ.get(
            "OLLAMA_TIMEOUT_SECONDS", "600")),
        OLLAMA_KEEP_ALIVE=os.environ.get("OLLAMA_KEEP_ALIVE", "10m"),
        VIDEOS=[
            {"url": v["url"], "group": campaign["name"],
             "kind": v.get("kind", "auto")}
            for v in videos
        ],
        SESSION_NAME=session_row["name"],
        OUTPUT_DIR=storage.run_dir(run_id) if run_id else "",
        # brief.run() reads context_map directly, not cfg.CAMPAIGN_CONTEXT,
        # so this flattened string is only a fallback for other readers.
        CAMPAIGN_CONTEXT="\n\n".join(campaign_context.values()) if campaign_context else "",
        KEEP_LANGUAGES={"id", "ms", "en", "tl"},
        MIN_COMMENT_LETTERS=8,
        MAX_COMMENTS_PER_VIDEO=2000,
        CODEBOOK_SAMPLE_SIZE=150,
        CODEBOOK_SAMPLE_MAX=500,
        CLASSIFY_BATCH_SIZE=25,
        UNCLASSIFIED_LIMIT=30,
        EMOTION_MODEL="StevenLimcorn/indonesian-roberta-base-emotion-classifier",
        SENTIMENT_MODEL="w11wo/indonesian-roberta-base-sentiment-classifier",
        REPORT_LANGUAGE="English",
        KEEP_INTERMEDIATE=False,
    )


# ---------------------------------------------------------------------------
# Evidence builder (for report.json)
# ---------------------------------------------------------------------------

_RECOGNIZED_SENTIMENTS = {"positive", "negative", "neutral"}


def _clean_text(value) -> str | None:
    """None if null/empty/whitespace-only, else the exact string."""
    if value is None or (isinstance(value, float) and value != value):
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    text = str(value)
    if text.strip() == "":
        return None
    return text


def _clean_likes(value) -> int:
    """Invalid, null, or nonfinite likes become 0."""
    try:
        if pd.isna(value):
            return 0
    except (TypeError, ValueError):
        pass
    try:
        f = float(value)
    except (TypeError, ValueError):
        return 0
    if f != f or f in (float("inf"), float("-inf")):
        return 0
    return int(f)


def _clean_video_id(value) -> str:
    """Null video ID becomes empty string."""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    if value is None:
        return ""
    return str(value)


def _clean_sentiment(value) -> str | None:
    """Trimmed/casefold match against positive/negative/neutral, else None."""
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if value is None:
        return None
    token = str(value).strip().casefold()
    return token if token in _RECOGNIZED_SENTIMENTS else None


def _row_source_order(base_df) -> "pd.Series":
    return pd.Series(range(len(base_df)), index=base_df.index)


def _make_metric_comments(sub, order: "pd.Series") -> list[dict]:
    """
    Build MetricComment dicts {text, likes, videoId, sentiment} from sub (a
    base_df slice), excluding rows whose comment text is null, empty, or
    whitespace-only. Sort by likes desc, then text length desc, then
    original source order (via `order`, indexed to match base_df). Cap 8.
    """
    if len(sub) == 0:
        return []

    texts = sub["comment"].apply(_clean_text)
    keep_mask = texts.notna()
    sub = sub[keep_mask]
    texts = texts[keep_mask]
    if len(sub) == 0:
        return []

    likes = sub["likes"].apply(_clean_likes) if "likes" in sub.columns else pd.Series(
        0, index=sub.index)
    video_ids = (sub["video_id"].apply(_clean_video_id) if "video_id" in sub.columns
                 else pd.Series("", index=sub.index))
    sentiments = (sub["sentiment"].apply(_clean_sentiment) if "sentiment" in sub.columns
                  else pd.Series(None, index=sub.index))
    tlens = texts.str.len()
    src_order = order.loc[sub.index]

    frame = pd.DataFrame({
        "_text": texts, "_likes": likes, "_video": video_ids,
        "_sentiment": sentiments, "_tlen": tlens, "_order": src_order,
    })
    ranked = frame.sort_values(
        ["_likes", "_tlen", "_order"], ascending=[False, False, True]).head(8)

    return [
        {
            "text": row["_text"], "likes": int(row["_likes"]),
            "videoId": row["_video"], "sentiment": row["_sentiment"],
        }
        for _, row in ranked.iterrows()
    ]


def _select_sentiment_evidence(sub, order: "pd.Series") -> list[dict]:
    """
    Key Message Sentiment evidence: from mentioned+applicable rows (sub)
    with a recognized sentiment, rank within buckets by ordinary order
    (likes desc, text length desc, source order), select up to 4 positive
    then up to 4 negative, then backfill to 8 from the best unselected
    recognized rows (including neutral) by ordinary order. No duplicate
    source row. Output: selected positive, selected negative, backfill.
    """
    if len(sub) == 0:
        return []

    texts = sub["comment"].apply(_clean_text)
    keep_mask = texts.notna()
    sub = sub[keep_mask]
    texts = texts[keep_mask]
    if len(sub) == 0:
        return []

    likes = sub["likes"].apply(_clean_likes) if "likes" in sub.columns else pd.Series(
        0, index=sub.index)
    video_ids = (sub["video_id"].apply(_clean_video_id) if "video_id" in sub.columns
                 else pd.Series("", index=sub.index))
    sentiments = (sub["sentiment"].apply(_clean_sentiment) if "sentiment" in sub.columns
                  else pd.Series(None, index=sub.index))
    tlens = texts.str.len()
    src_order = order.loc[sub.index]

    frame = pd.DataFrame({
        "_text": texts, "_likes": likes, "_video": video_ids,
        "_sentiment": sentiments, "_tlen": tlens, "_order": src_order,
    })
    frame = frame[frame["_sentiment"].notna()]
    if len(frame) == 0:
        return []

    ranked = frame.sort_values(
        ["_likes", "_tlen", "_order"], ascending=[False, False, True])

    positive = ranked[ranked["_sentiment"] == "positive"].head(4)
    negative = ranked[ranked["_sentiment"] == "negative"].head(4)
    used_idx = set(positive.index) | set(negative.index)

    remaining = ranked[~ranked.index.isin(used_idx)]
    slots_left = 8 - len(positive) - len(negative)
    backfill = remaining.head(max(slots_left, 0))

    ordered = pd.concat([positive, negative, backfill])

    return [
        {
            "text": row["_text"], "likes": int(row["_likes"]),
            "videoId": row["_video"], "sentiment": row["_sentiment"],
        }
        for _, row in ordered.iterrows()
    ]


def _build_evidence(base_df, key_message_metrics: list[dict],
                    theme_metrics: list[dict], emotion_metrics: list[dict],
                    sentiment_metrics: list[dict], applicable_masks: dict) -> list[dict]:
    """
    Return evidence_json: a list of EvidenceMetric dicts {metricId, comments},
    in order: Key Messages, Themes, Emotions, Key Message Sentiment. Every
    metric group is emitted even if empty.

    applicable_masks maps a Key Message metricId to the boolean Series of
    base_df rows where that message applies AND was mentioned (exact True).
    Used for both the m-t and m-is groups (m-is instead ranks/selects for
    sentiment balance).
    """
    order = _row_source_order(base_df)
    groups = []

    # Key Messages: mentioned+applicable rows, ordinary ranking, cap 8.
    for m in key_message_metrics:
        mask = applicable_masks.get(m["metricId"])
        sub = base_df[mask] if mask is not None else base_df.iloc[0:0]
        groups.append({"metricId": m["metricId"],
                       "comments": _make_metric_comments(sub, order)})

    # Themes: exact trimmed label match.
    for m in theme_metrics:
        if "theme" in base_df.columns:
            sub = base_df[base_df["theme"].apply(
                lambda v: (_clean_text(v) or "").strip() == m["label"]
                if _clean_text(v) is not None else False)]
        else:
            sub = base_df.iloc[0:0]
        groups.append({"metricId": m["metricId"],
                       "comments": _make_metric_comments(sub, order)})

    # Emotions: trimmed casefold match.
    for m in emotion_metrics:
        target = m["label"].strip().casefold()
        if "emotion" in base_df.columns:
            sub = base_df[base_df["emotion"].apply(
                lambda v: (_clean_text(v) or "").strip().casefold() == target
                if _clean_text(v) is not None else False)]
        else:
            sub = base_df.iloc[0:0]
        groups.append({"metricId": m["metricId"],
                       "comments": _make_metric_comments(sub, order)})

    # Key Message Sentiment: reuses the same applicable+mentioned mask,
    # selected with balanced positive/negative + neutral backfill.
    for m in sentiment_metrics:
        mask = applicable_masks.get(m["_source_metric_id"])
        sub = base_df[mask] if mask is not None else base_df.iloc[0:0]
        groups.append({"metricId": m["metricId"],
                       "comments": _select_sentiment_evidence(sub, order)})

    return groups


# ---------------------------------------------------------------------------
# report.json prose builder
# ---------------------------------------------------------------------------

_PROSE_PROMPT = """You are writing the results summary for a YouTube comment analysis tool.

GROUNDED BRIEF (from video transcripts - rely on this):
{grounded}

THEME MIX (% of comments per theme):
{themes}

KEY MESSAGE MENTIONS (% of comments mentioning each idea the campaign put forward):
{transfer}

EMOTION SUMMARY:
{emotion_summary}

TOP QUOTES (verbatim from the data):
{quotes}

Return STRICT JSON with exactly these keys (no extra keys, no markdown):
{{
  "title": "two-line finding title separated by \\n, under 8 words per line, states the finding not the topic",
  "interpretation": "2-4 paragraphs separated by \\n\\n, dense strategic prose, no filler, each paragraph under 60 words",
  "quote": {{"text": "one verbatim quote from the list above", "attr": "attribution line e.g. comment · 47 likes"}},
  "caveat": "3-5 short lines covering limitations; MUST include: {emotion_caveat}"
}}

Write in English. No em dashes. Short declarative sentences."""


def _validate_prose_with_quote_grounding(candidates: list[str]):
    """
    Wrap llm.validate_results_prose with candidate-quote grounding.

    validate_results_prose only checks shape (nonempty strings in the
    right places). The prose prompt requires the quote be copied verbatim
    from the candidate pool shown to the model; that grounding is specific
    to this caller's prompt contract, so it lives here rather than in the
    shared pipeline/llm.py validator.
    """
    def _validate(value: object) -> object:
        result = pipeline_llm.validate_results_prose(value)
        if result["quote"]["text"] not in candidates:
            raise ValueError("quote must be verbatim from the candidate quote pool")
        return result
    return _validate


def _build_prose(grounded: str, transfer_table, base_df,
                 affect_result: dict, themes_json: list,
                 cfg: "PipelineConfig") -> dict:
    """
    One structured Qwen call -> {title, interpretation, quote, caveat}.

    Deviation note: AGENTS.md originally specified regex-parsing the markdown
    report for the prose fields. This implementation uses a direct ask_json
    call instead. Reason: the markdown report is written for PDF rendering
    (includes [[CHART:...]] tokens, table rows, blockquotes); reliably parsing
    prose fields from it via regex is fragile and would couple adapter.py to
    report.py's internal formatting. A structured LLM call is more robust and
    already has llm.ask_json's own retry/repair loop.
    """
    # Build a tight quote pool (top 8 liked comments).
    top_quotes = (base_df.nlargest(8, "likes")["comment"]
                  .str.slice(0, 280).tolist()) if len(base_df) > 0 else []
    quotes_str = "\n".join(f'- "{q}"' for q in top_quotes)

    theme_lines = "\n".join(
        f"  {t['label']}: {t['value']}%" for t in themes_json)

    if not transfer_table.empty:
        transfer_lines = "\n".join(
            f"  {row.point}: {row.echoed_pct:.0f}% ({row.n} comments)"
            for row in transfer_table.itertuples())
    else:
        transfer_lines = "  (no transfer measured)"

    emotion_tbl = affect_result.get("emotion", {}).get("table", None)
    emotion_summary = (emotion_tbl.to_string() if emotion_tbl is not None
                       else "(emotion not run)")
    emotion_caveat = affect_result.get("emotion", {}).get("caveat", "")

    prompt = _PROSE_PROMPT.format(
        grounded=grounded[:5000],
        themes=theme_lines,
        transfer=transfer_lines,
        emotion_summary=emotion_summary,
        quotes=quotes_str,
        emotion_caveat=emotion_caveat[:400],
    )

    try:
        result = pipeline_llm.ask_json(
            prompt[:80000], cfg, schema=pipeline_llm.RESULTS_PROSE_SCHEMA,
            validation=_validate_prose_with_quote_grounding(top_quotes),
            num_predict=2048)
        return result
    except Exception as exc:
        logger.warning("prose LLM call failed (%s), using deterministic fallback", exc)
        return _prose_fallback(base_df, transfer_table, themes_json, emotion_caveat)


def _prose_fallback(base_df, transfer_table, themes_json: list,
                    emotion_caveat: str) -> dict:
    """Deterministic prose when the LLM call fails. Never returns an invalid shape."""
    n = len(base_df)
    top_theme = themes_json[0]["label"] if themes_json else "various topics"

    if not transfer_table.empty:
        best = transfer_table.loc[transfer_table["echoed_pct"].idxmax()]
        xfer_line = f"{best['point']}: {best['echoed_pct']:.0f}% of comments mentioned it."
    else:
        xfer_line = "No Key Message mentions measured."

    # Pick the highest-liked comment as the quote.
    if n > 0:
        best_row = base_df.nlargest(1, "likes").iloc[0]
        quote_text = str(best_row["comment"])[:280]
        quote_attr = f"comment · {int(best_row.get('likes', 0))} likes"
    else:
        quote_text = "(no comments)"
        quote_attr = ""

    title = f"Audience response\nacross {n:,} comments"
    interpretation = (
        f"The analysis base is {n:,} comments. "
        f"The dominant theme is {top_theme}.\n\n"
        f"{xfer_line}\n\n"
        "Review the Key Message and theme tables for the full breakdown."
    )
    caveat = (
        f"Results are based on {n:,} comments.\n"
        "Few mentions mean an idea did not arrive, not that it was rejected.\n"
        f"{emotion_caveat}"
    )

    return {
        "title":          title,
        "interpretation": interpretation,
        "quote":          {"text": quote_text, "attr": quote_attr},
        "caveat":         caveat,
    }


# ---------------------------------------------------------------------------
# report.json assembler
# ---------------------------------------------------------------------------

def _slugify(label: str) -> str:
    """Slug for a Key Message label. Falls back to 'message' if the label
    has no slug-able characters (e.g. pure punctuation)."""
    slug = re.sub(r"\W+", "-", str(label).lower()).strip("-")
    return slug if slug else "message"


def _resolve_point_col(label: str, columns: dict) -> str | None:
    """Resolve a Key Message label to its pt__ column: exact label match
    first, then trim/casefold."""
    for col, col_label in columns.items():
        if col_label == label:
            return col
    target = str(label).strip().casefold()
    for col, col_label in columns.items():
        if str(col_label).strip().casefold() == target:
            return col
    return None


def _applicable_groups_for(label: str, transfer_table) -> set:
    """
    Exact groups where transfer_table.point matches `label` after
    trim/casefold. Empty set if transfer_table lacks group/point columns,
    or label has no match. Null group values are never included.
    """
    if transfer_table is None:
        return set()
    if "group" not in transfer_table.columns or "point" not in transfer_table.columns:
        return set()
    target = str(label).strip().casefold()
    points = transfer_table["point"].apply(
        lambda v: None if pd.isna(v) else str(v).strip().casefold())
    rows = transfer_table[points == target]
    groups = set()
    for g in rows["group"]:
        if g is None:
            continue
        try:
            if pd.isna(g):
                continue
        except (TypeError, ValueError):
            pass
        groups.add(g)
    return groups


def _applicable_mask(base_df, groups: set):
    """Boolean Series: True where base_df['group'] is an exact member of
    `groups`. All-False if base_df lacks 'group' or groups is empty. Null
    group values never match (isin already excludes NaN)."""
    if "group" not in base_df.columns or not groups:
        return pd.Series(False, index=base_df.index)
    return base_df["group"].isin(groups)


def _one_decimal(numerator: float, denominator: float) -> float:
    if not denominator:
        return 0.0
    return round(numerator * 100 / denominator, 1)


def _build_report_json(base_df, transfer_table, key_messages: list[dict],
                       columns: dict) -> dict:
    """Build the full report.json dict consumed by the frontend results
    screen. No prose, no model calls. key_messages is the run's snapshot
    of brief_points rows (id, label, included, sort_order, ...).

    Applicability: a Key Message applies only to the exact groups listed
    for it in transfer_table (trim/casefold point match against exact
    group). base_df['group'] must exactly match one of those groups for a
    row to count. Missing 'group' on either side means zero applicability
    everywhere (message shells stay at zero, evidence stays empty).
    """
    total = len(base_df)

    # --- key messages: filter included, sort by sort_order -------------------
    included = sorted(
        (pt for pt in key_messages if pt.get("included")),
        key=lambda pt: pt.get("sort_order", 0))

    # Shared slug-collision counter across m-t-* and m-is-* so both metric
    # families use the same suffix for the same Key Message.
    used_slugs: dict[str, int] = {}
    slug_for_pt: dict[str, str] = {}

    def _slug_for(pt_id: str, label: str) -> str:
        if pt_id in slug_for_pt:
            return slug_for_pt[pt_id]
        base = _slugify(label)
        if base in used_slugs:
            used_slugs[base] += 1
            slug = f"{base}-{used_slugs[base]}"
        else:
            used_slugs[base] = 1
            slug = base
        slug_for_pt[pt_id] = slug
        return slug

    key_message_metrics = []
    applicable_masks: dict[str, "pd.Series"] = {}
    mentioned_masks: dict[str, "pd.Series"] = {}
    for pt in included:
        label = pt["label"]
        col = _resolve_point_col(label, columns)
        groups = _applicable_groups_for(label, transfer_table)
        app_mask = _applicable_mask(base_df, groups)

        if col is not None and col in base_df.columns:
            cell = base_df[col]
            non_null = cell.notna()
            denom_mask = app_mask & non_null
            true_mask = app_mask & (cell == True)
        else:
            denom_mask = pd.Series(False, index=base_df.index)
            true_mask = pd.Series(False, index=base_df.index)

        denom = int(denom_mask.sum())
        numerator = int(true_mask.sum())
        percent = _one_decimal(numerator, denom)

        slug = _slug_for(pt["id"], label)
        metric_id = f"m-t-{slug}"
        key_message_metrics.append({
            "id":          pt["id"],
            "metricId":    metric_id,
            "label":       label,
            "description": pt.get("description", ""),
            "count":       numerator,
            "percent":     percent,
        })
        applicable_masks[metric_id] = true_mask
        mentioned_masks[pt["id"]] = true_mask

    # --- overallTransfer: rows mentioning >=1 applicable message, / total ----
    if included and total:
        union_mask = pd.Series(False, index=base_df.index)
        for pt in included:
            union_mask = union_mask | mentioned_masks.get(
                pt["id"], pd.Series(False, index=base_df.index))
        overall_transfer = _one_decimal(int(union_mask.sum()), total)
    else:
        overall_transfer = 0.0

    # --- themes / emotions: merge case-insensitively, preserve first spelling -
    def _merge_labels(col_name: str) -> list[tuple]:
        """Return [(display_label, count)] for non-empty labels in
        col_name, merged case-insensitively with first-seen spelling
        preserved, sorted count desc then casefold(label) then label."""
        if col_name not in base_df.columns or not total:
            return []
        display_by_key: dict[str, str] = {}
        counts: dict[str, int] = {}
        for v in base_df[col_name]:
            text = _clean_text(v)
            if text is None:
                continue
            text = text.strip()
            if text == "":
                continue
            key = text.casefold()
            if key not in display_by_key:
                display_by_key[key] = text
            counts[key] = counts.get(key, 0) + 1
        ordered_keys = sorted(
            counts.keys(),
            key=lambda k: (-counts[k], k, display_by_key[k]))
        return [(display_by_key[k], counts[k]) for k in ordered_keys]

    theme_metrics = []
    for i, (label, count) in enumerate(_merge_labels("theme")):
        theme_metrics.append({
            "metricId": f"m-th-{i}",
            "label":    label,
            "count":    count,
            "percent":  _one_decimal(count, total),
        })

    emotion_metrics = []
    emotion_used_slugs: dict[str, int] = {}
    for label, count in _merge_labels("emotion"):
        base_slug = _slugify(label)
        if base_slug in emotion_used_slugs:
            emotion_used_slugs[base_slug] += 1
            metric_id = f"m-em-{base_slug}-{emotion_used_slugs[base_slug]}"
        else:
            emotion_used_slugs[base_slug] = 1
            metric_id = f"m-em-{base_slug}"
        emotion_metrics.append({
            "metricId": metric_id,
            "label":    label,
            "count":    count,
            "percent":  _one_decimal(count, total),
        })

    # --- key message sentiment -------------------------------------------------
    key_message_sentiment = []
    sentiment_source_for: dict[str, str] = {}
    for pt in included:
        label = pt["label"]
        mentioned_mask = mentioned_masks.get(
            pt["id"], pd.Series(False, index=base_df.index))
        sub = base_df[mentioned_mask]

        if "sentiment" in base_df.columns and len(sub):
            sent = sub["sentiment"].apply(_clean_sentiment)
        else:
            sent = pd.Series(dtype=object)
        recognized = sent[sent.notna()]
        base_n = int(len(recognized))
        pos_count = int((recognized == "positive").sum())
        neg_count = int((recognized == "negative").sum())
        pos_percent = _one_decimal(pos_count, base_n)
        neg_percent = _one_decimal(neg_count, base_n)

        slug = _slug_for(pt["id"], label)
        metric_id = f"m-is-{slug}"
        key_message_sentiment.append({
            "id":              pt["id"],
            "metricId":        metric_id,
            "label":           label,
            "positiveCount":   pos_count,
            "positivePercent": pos_percent,
            "negativeCount":   neg_count,
            "negativePercent": neg_percent,
            "baseN":           base_n,
        })
        sentiment_source_for[metric_id] = f"m-t-{slug}"

    # --- evidence: attach the m-t metricId each m-is metric reuses --------------
    sentiment_metrics_for_evidence = [
        {**m, "_source_metric_id": sentiment_source_for[m["metricId"]]}
        for m in key_message_sentiment
    ]

    evidence = _build_evidence(
        base_df, key_message_metrics, theme_metrics, emotion_metrics,
        sentiment_metrics_for_evidence, applicable_masks)

    return {
        "overallTransfer":     overall_transfer,
        "keyMessages":         key_message_metrics,
        "themes":              theme_metrics,
        "emotions":            emotion_metrics,
        "keyMessageSentiment": key_message_sentiment,
        "evidence":            evidence,
    }


# ---------------------------------------------------------------------------
# Main execution
# ---------------------------------------------------------------------------

def _execute(run_id: str) -> None:
    """
    Full pipeline run on a daemon thread.
    All exceptions are caught; the run always ends in 'complete' or 'failed'.
    """
    out_dir = storage.run_dir(run_id)
    cfg = None

    try:
        # --- 1. Mark running ------------------------------------------------
        _set_run_state(run_id, "running", started_at=_now_iso())

        run_row     = _load_run(run_id)
        session_row = _load_session(run_row["session_id"])
        campaign    = _load_campaign(run_row["session_id"])
        videos      = _load_videos(campaign["id"])
        asset_rows  = _load_assets(campaign["id"])

        if not videos:
            raise ValueError("No videos in this campaign - cannot run.")

        # --- 2. Asset context assembly ---------------------------------------
        # Text extraction and article fetching happen at asset-creation time
        # (server.py's upload/article routes), not here. This stage only
        # assembles what was already extracted into the run's context.
        _push(run_id, "collect", "Assembling asset context", 2)
        campaign_context_parts = []
        images_by_group: dict[str, list[tuple[bytes, str]]] = {}

        for asset in asset_rows:
            if asset.get("text"):
                campaign_context_parts.append(asset["text"])

            # Collect image assets for images_map (cap: 6 per group, skip >5 MB).
            if asset.get("kind") == "image":
                fp = asset.get("file_path", "")
                if fp and os.path.isfile(fp):
                    group_name = campaign["name"]
                    bucket = images_by_group.setdefault(group_name, [])
                    if len(bucket) < 6:
                        try:
                            size = os.path.getsize(fp)
                            if size <= 5 * 1024 * 1024:
                                ext = os.path.splitext(fp)[1].lstrip(".").lower()
                                mime = {
                                    "png":  "image/png",
                                    "jpg":  "image/jpeg",
                                    "jpeg": "image/jpeg",
                                    "webp": "image/webp",
                                }.get(ext, "image/jpeg")
                                with open(fp, "rb") as fh:
                                    bucket.append((fh.read(), mime))
                        except OSError:
                            pass  # unreadable file - skip silently

        campaign_context = {
            campaign["name"]: "\n\n".join(campaign_context_parts)
        } if campaign_context_parts else {}

        images_map = images_by_group if images_by_group else None

        # --- 3. Build config ------------------------------------------------
        cfg = _build_config(run_id, session_row, campaign, videos,
                            campaign_context)

        # --- 4. Snapshot Session Key Messages into this run -----------------
        # Immutable from here: this run never writes back to key_messages
        # (see _load_session_key_messages docstring). Snapshotting before
        # collect means a crash mid-collect still leaves an auditable
        # brief_points row set rather than none at all.
        session_key_messages = _load_session_key_messages(run_row["session_id"])
        _replace_brief_points(run_id, campaign["id"], session_key_messages)

        # --- 5. Collect -------------------------------------------------------
        pipeline_llm.preflight(cfg)
        _push(run_id, "collect", "Fetching comments and transcripts", 5)
        comments_df, meta_df = collect.fetch(cfg)
        comments_df = collect.clean(comments_df, cfg)
        base_df = comments_df[comments_df["in_base"]].reset_index(drop=True)
        _push(run_id, "collect",
              f"Collected {len(base_df)} comments in analysis base", 20,
              detail=f"total fetched: {len(comments_df)}")

        # --- 6. Brief: reconcile the snapshot against transcripts -----------
        # brief.reconcile() keeps edited entries and stable ids verbatim,
        # refreshes unedited matches with the transcript-grounded
        # description, and appends transcript-only messages a Session
        # with no User Inputs would otherwise have none of (PRD
        # "No-input Sessions draft from transcripts").
        _push(run_id, "brief", "Reading the videos", 22)
        summary_str = "; ".join(
            meta_df["title"].fillna("").astype(str).head(6))
        grounded, reconciled = brief.reconcile(
            session_key_messages, meta_df, cfg,
            context_map=campaign_context, images_map=images_map,
            include_grounded=True)
        _push(run_id, "brief",
              f"Brief complete - {len(reconciled)} points discovered", 38,
              detail=str(len(reconciled)))

        # --- 7. Replace brief points with the reconciled list; brief pause --
        _replace_brief_points(run_id, campaign["id"], reconciled)
        _push(run_id, "brief_pause",
              "Brief ready for review. Waiting for approval.", 40,
              detail=str(len(reconciled)))

        # Block until server.py calls proceed (sets the event).
        get_proceed_event(run_id).wait()

        # --- 8. Re-read included brief points ---------------------------------
        # video_id is always NULL on these rows (Session-level Key
        # Messages, not the old per-video brief() points); analyze.classify()
        # broadcasts a None video_id to every video's batch.
        db_points = _load_brief_points(run_id)

        classifier_points = [
            {
                "group":       campaign["name"],
                "video_id":    pt["video_id"],
                "label":       pt["label"],
                "description": pt["description"],
            }
            for pt in db_points
            if pt["included"] == 1
        ]

        if not classifier_points:
            raise ValueError(
                "All brief points were excluded. At least one must be included.")

        # --- 9. Classify ----------------------------------------------------
        _push(run_id, "classify", "Discovering themes", 42)
        themes = analyze.build(base_df, summary_str, cfg)
        _push(run_id, "classify",
              f"Classifying {len(base_df)} comments", 50,
              detail=f"{len(themes)} themes")
        def classify_progress(completed, total):
            pct = 50 + int(completed / max(total, 1) * 9)
            _push(run_id, "classify",
                  f"Classified batch {completed} of {total}", pct,
                  detail=f"completed_batches={completed};total_batches={total}")

        base_df, columns = analyze.classify(
            base_df, themes, classifier_points, cfg,
            on_progress=classify_progress)
        base_df, themes, other_share = analyze.extend(
            base_df, themes, classifier_points, summary_str, cfg,
            on_progress=lambda msg: _push(run_id, "classify", msg, 60))
        theme_table, transfer_table = analyze.summarise(base_df, columns)
        _push(run_id, "classify",
              f"Classification complete - {other_share:.0f}% Other", 65,
              detail=f"other_share={other_share:.1f}")

        # --- 10. Emotion and sentiment ---------------------------------------
        # Best-effort: free VRAM before the HuggingFace models load. A
        # failure here must not abort the run; final cleanup below still
        # retries the unload.
        try:
            pipeline_llm.unload(cfg.TEXT_MODEL, cfg)
        except Exception:
            logger.warning("could not unload Ollama model %s before "
                           "emotion/sentiment", cfg.TEXT_MODEL, exc_info=True)
        _push(run_id, "emotion", "Running emotion and sentiment analysis", 67)
        base_df, affect_result = analyze.affect(base_df, cfg)
        _push(run_id, "emotion", "Emotion and sentiment analysis complete", 75,
              detail=affect_result.get("emotion", {}).get("caveat", ""))

        # --- 11. Report -----------------------------------------------------
        _push(run_id, "report", "Writing report", 77)
        markdown = pipeline_report.write(
            grounded, theme_table, transfer_table,
            affect_result, base_df, cfg)
        pipeline_report.render(
            markdown, out_dir, cfg, None,
            _df=base_df, _transfer=transfer_table)
        pipeline_report.export(
            base_df, theme_table, transfer_table, affect_result,
            meta_df, out_dir)
        _push(run_id, "report", "Report written", 88)

        # --- 12. Build report.json ------------------------------------------
        report_data = _build_report_json(
            base_df, transfer_table, db_points, columns)

        report_json_path = os.path.join(out_dir, "report.json")
        with open(report_json_path, "w", encoding="utf-8") as fh:
            json.dump(report_data, fh, ensure_ascii=False, indent=2)

        # --- 13. Copy artifacts to artifacts_dir and register in DB --------
        # Fixed order per _ARTIFACT_FILES. All seven are required: a run
        # cannot reach "complete" missing one, so a short source scan runs
        # first and raises before any DB row or state change.
        art_dir = storage.artifacts_dir(run_id)
        missing = _missing_required_artifacts(out_dir)
        if missing:
            raise ValueError(
                f"Run finished without required output files: {', '.join(missing)}")
        for kind, filename in _ARTIFACT_FILES:
            src = os.path.join(out_dir, filename)
            dst = os.path.join(art_dir, filename)
            shutil.copy2(src, dst)
            _insert_artifact(run_id, kind, dst)

        # --- 14. Mark complete ----------------------------------------------
        _set_run_state(run_id, "complete", finished_at=_now_iso())
        _push(run_id, "complete", "Run complete", 100,
              detail=f"artifacts: {art_dir}")

    except Exception as exc:
        err_str = f"{type(exc).__name__}: {exc}"
        logger.exception("run %s failed", run_id)
        try:
            _set_run_state(run_id, "failed", finished_at=_now_iso(), error=err_str)
        except Exception:
            pass
        _push(run_id, "error", "Run failed", 0, detail=err_str)
    finally:
        if cfg is not None:
            for model in (cfg.VISION_MODEL, cfg.TEXT_MODEL):
                try:
                    pipeline_llm.unload(model, cfg)
                except Exception:
                    logger.warning("could not unload Ollama model %s", model,
                                   exc_info=True)
