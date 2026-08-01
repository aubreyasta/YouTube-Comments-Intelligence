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

# Load .env at repo root if present; must happen before any env reads.
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass  # python-dotenv optional; env vars may be set by the shell

import db
import storage
import assets as assets_mod
from pipeline import collect, brief, analyze, report as pipeline_report
from pipeline import llm as pipeline_llm
from pipeline.config_types import PipelineConfig

logger = logging.getLogger(__name__)

# Module-level state: one queue and one event per run.
_queues: dict[str, queue.Queue] = {}
_proceed_events: dict[str, threading.Event] = {}
_terminal: dict[str, bool] = {}
_lock = threading.Lock()


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


def _update_asset(asset_id: str, **cols) -> None:
    set_clause = ", ".join(f"{k} = ?" for k in cols)
    conn = db.get_conn()
    try:
        conn.execute(
            f"UPDATE assets SET {set_clause} WHERE id = ?",
            (*cols.values(), asset_id))
        conn.commit()
    finally:
        conn.close()


def _insert_brief_points(run_id: str, campaign_id: str, points: list[dict]) -> None:
    """Insert one row per point. video_id is the YouTube ID string."""
    conn = db.get_conn()
    try:
        for i, pt in enumerate(points):
            conn.execute(
                """INSERT INTO brief_points
                   (id, run_id, campaign_id, video_id, label, description,
                    approved, edited, included, sort_order)
                   VALUES (?, ?, ?, ?, ?, ?, 0, 0, 1, ?)""",
                (str(uuid.uuid4()), run_id, campaign_id,
                 pt["video_id"], pt["label"], pt["description"], i))
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

def _build_config(run_id: str, session_row: dict, campaign: dict,
                  videos: list[dict], campaign_context: dict) -> PipelineConfig:
    """Build a PipelineConfig from DB rows and environment variables."""
    return PipelineConfig(
        YOUTUBE_API_KEY=os.environ.get("YOUTUBE_API_KEY", ""),
        GEMINI_API_KEY=os.environ.get("GEMINI_API_KEY", ""),
        MODEL=os.environ.get("GEMINI_MODEL") or "gemini-3-pro",
        VIDEOS=[
            {"url": v["url"], "group": campaign["name"],
             "kind": v.get("kind", "auto")}
            for v in videos
        ],
        SESSION_NAME=session_row["name"],
        OUTPUT_DIR=storage.run_dir(run_id),
        # CAMPAIGN_CONTEXT is stored as a dict in adapter but the dataclass
        # field is typed str; keep the dict here - pipeline uses context_map
        # param directly, not cfg.CAMPAIGN_CONTEXT, so the str cast is fine.
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
        KEY_VISUALS={},
        KEEP_INTERMEDIATE=False,
    )


# ---------------------------------------------------------------------------
# Evidence builder (for report.json)
# ---------------------------------------------------------------------------

def _build_evidence(base_df, transfer_table, themes: list[dict],
                    columns: dict) -> tuple[list, list, list]:
    """
    Return (transfers_json, themes_json, evidence_json).

    Discriminates metric kind by metric-id prefix:
      m-th-<i>   -> theme rows  (base_df["theme"] == label)
      m-t-<slug> -> transfer rows (pt__ column from columns map)
      m-is-<slug>-> idea-sentiment rows (same pt__ column as m-t-)

    Evidence: top 8 per metric, ranked likes desc then text-length desc.
    No global cap. Row shape: {id, metricId, text, emotion, sentiment, likes}.
    """
    import pandas as pd

    # --- transfers -----------------------------------------------------------
    transfers = []
    if not transfer_table.empty:
        for row in transfer_table.itertuples():
            slug = re.sub(r"\W+", "-", str(row.point).lower()).strip("-")
            transfers.append({
                "id":            f"m-t-{slug}",
                "label":         row.point,
                "value":         round(float(row.echoed_pct)),
                "evidenceCount": int(row.n),
            })

    # --- themes --------------------------------------------------------------
    total = len(base_df)
    if total == 0:
        themes_json = []
    else:
        theme_counts = base_df["theme"].value_counts()
        theme_pcts = (theme_counts / total * 100).round(1)
        other_pct = theme_pcts.get("Other", 0)
        non_other = theme_pcts.drop("Other", errors="ignore").sort_values(ascending=False)
        ordered = list(non_other.items())
        if "Other" in theme_pcts.index:
            ordered.append(("Other", other_pct))
        themes_json = [
            {"id": f"m-th-{i}", "label": name, "value": round(float(pct))}
            for i, (name, pct) in enumerate(ordered)
        ]

    # --- evidence ------------------------------------------------------------
    # Build label->col map from columns (pt__col->label), inverted.
    label_to_col: dict[str, str] = {v: k for k, v in columns.items()}

    ev_rows = []

    # Theme metrics (m-th-).
    for t in themes_json:
        if t["label"] == "Other":
            continue
        sub = base_df[base_df["theme"] == t["label"]]
        if sub.empty:
            continue
        kept = (sub.assign(_tlen=sub["comment"].str.len())
                   .sort_values(["likes", "_tlen"], ascending=[False, False])
                   .drop(columns="_tlen")
                   .head(8))
        for _, row in kept.iterrows():
            ev_rows.append({
                "metricId":  t["id"],
                "text":      str(row["comment"])[:500],
                "emotion":   str(row.get("emotion", "")),
                "sentiment": str(row.get("sentiment", "")),
                "likes":     int(row.get("likes", 0)),
            })

    # Transfer metrics (m-t-) and idea-sentiment (m-is-) share same rows.
    for t in transfers:
        label = t["label"]
        col = label_to_col.get(label)
        if col is None or col not in base_df.columns:
            continue
        sub = base_df[base_df[col] == True]
        if sub.empty:
            continue
        kept = (sub.assign(_tlen=sub["comment"].str.len())
                   .sort_values(["likes", "_tlen"], ascending=[False, False])
                   .drop(columns="_tlen")
                   .head(8))
        for _, row in kept.iterrows():
            ev_rows.append({
                "metricId":  t["id"],
                "text":      str(row["comment"])[:500],
                "emotion":   str(row.get("emotion", "")),
                "sentiment": str(row.get("sentiment", "")),
                "likes":     int(row.get("likes", 0)),
            })

    evidence = [
        {"id": f"ev-{i}", **r}
        for i, r in enumerate(ev_rows)
    ]

    return transfers, themes_json, evidence


# ---------------------------------------------------------------------------
# report.json prose builder
# ---------------------------------------------------------------------------

_PROSE_PROMPT = """You are writing the results summary for a YouTube comment analysis tool.

GROUNDED BRIEF (from video transcripts - rely on this):
{grounded}

THEME MIX (% of comments per theme):
{themes}

SIGNAL TRANSFER (% of comments echoing each idea the campaign put forward):
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


def _build_prose(grounded: str, transfer_table, base_df,
                 affect_result: dict, themes_json: list,
                 cfg: "PipelineConfig") -> dict:
    """
    One structured Gemini call -> {title, interpretation, quote, caveat}.

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
        result = pipeline_llm.ask_json(prompt, cfg)
        # Validate required keys are present.
        if not all(k in result for k in ("title", "interpretation", "quote", "caveat")):
            raise ValueError("prose JSON missing required keys")
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
        xfer_line = f"{best['point']}: {best['echoed_pct']:.0f}% of comments echoed it."
    else:
        xfer_line = "No transfer points measured."

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
        "Review the transfer and theme tables for the full breakdown."
    )
    caveat = (
        f"Results are based on {n:,} comments.\n"
        "Low transfer means an idea did not arrive, not that it was rejected.\n"
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

def _build_report_json(run_id: str, session_row: dict, videos: list[dict],
                       base_df, transfer_table, theme_table,
                       themes_list: list[dict], columns: dict,
                       grounded: str,
                       affect_result: dict,
                       cfg: "PipelineConfig") -> dict:
    """Build the full report.json dict consumed by the frontend results screen."""
    import pandas as pd

    # --- numbers from DataFrames (no markdown parsing) -----------------------
    transfers, themes_json, evidence = _build_evidence(
        base_df, transfer_table, themes_list, columns)

    # overallTransfer: share of base with at least one pt__ column True.
    pt_cols = [c for c in base_df.columns if c.startswith("pt__")]
    if pt_cols and len(base_df) > 0:
        any_echo = base_df[pt_cols].any(axis=1).sum()
        overall_transfer = round(any_echo / len(base_df) * 100)
    else:
        overall_transfer = 0

    # subtitle
    n_comments  = len(base_df)
    n_videos    = len(videos)
    n_themes    = sum(1 for t in themes_json if t["label"] != "Other")
    # languages: derive from lang column in base_df.
    if "lang" in base_df.columns:
        lang_map = {"id": "Indonesian", "ms": "Malay", "en": "English",
                    "tl": "Tagalog"}
        langs = sorted({
            lang_map.get(l, l)
            for l in base_df["lang"].unique()
            if l not in ("too_short", "unknown")
        })
        lang_str = " / ".join(langs) if langs else "Indonesian / English"
    else:
        lang_str = "Indonesian / English"

    subtitle = (f"{n_comments:,} comments · {n_videos} video"
                f"{'s' if n_videos != 1 else ''} · "
                f"{n_themes} theme{'s' if n_themes != 1 else ''} · {lang_str}")

    # --- emotions distribution -----------------------------------------------
    if "emotion" in base_df.columns and len(base_df) > 0:
        em_counts = base_df["emotion"].value_counts()
        emotions = [
            {"label": lbl, "value": round(cnt / len(base_df) * 100), "n": int(cnt)}
            for lbl, cnt in em_counts.items()
        ]
    else:
        emotions = []

    # --- idea-sentiment ------------------------------------------------------
    # One entry per transfer point. Slug matches the m-t- slug so the drawer
    # can align them. Zero-echo ideas get n=0 and all zeros.
    _SENTIMENT_POS = {"positive"}
    _SENTIMENT_NEG = {"negative"}
    # anything else (neutral / unknown / empty) maps to neutral

    label_to_col: dict[str, str] = {v: k for k, v in columns.items()}

    idea_sentiment = []
    for t in transfers:
        slug = t["id"][len("m-t-"):]  # reuse the same slug
        label = t["label"]
        col = label_to_col.get(label)
        if col and col in base_df.columns:
            sub = base_df[base_df[col] == True]
        else:
            sub = base_df.iloc[0:0]  # empty

        n = len(sub)
        if n == 0:
            idea_sentiment.append({
                "id": f"m-is-{slug}", "label": label,
                "positive": 0, "neutral": 0, "negative": 0, "n": 0,
            })
            continue

        sent_counts = sub["sentiment"].str.lower().value_counts() if "sentiment" in sub.columns else pd.Series(dtype=int)
        pos = int(round(sum(sent_counts.get(s, 0) for s in _SENTIMENT_POS) / n * 100))
        neg = int(round(sum(sent_counts.get(s, 0) for s in _SENTIMENT_NEG) / n * 100))
        neu = max(0, 100 - pos - neg)
        idea_sentiment.append({
            "id": f"m-is-{slug}", "label": label,
            "positive": pos, "neutral": neu, "negative": neg, "n": n,
        })

    # --- prose ---------------------------------------------------------------
    prose = _build_prose(grounded, transfer_table, base_df,
                         affect_result, themes_json, cfg)

    return {
        "runId":            run_id,
        "title":            prose["title"],
        "subtitle":         subtitle,
        "overallTransfer":  overall_transfer,
        "transfers":        transfers,
        "themes":           themes_json,
        "emotions":         emotions,
        "ideaSentiment":    idea_sentiment,
        "interpretation":   prose["interpretation"],
        "quote":            prose["quote"],
        "caveat":           prose["caveat"],
        "evidence":         evidence,
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

        # --- 2. Asset extraction --------------------------------------------
        _push(run_id, "collect", "Extracting assets", 2)
        campaign_context_parts = []
        images_by_group: dict[str, list[tuple[bytes, str]]] = {}

        for asset in asset_rows:
            if asset["kind"] != "article" and not asset.get("text"):
                fp = asset.get("file_path", "")
                if fp and os.path.isfile(fp):
                    text = assets_mod.extract_upload(fp)
                    if text:
                        _update_asset(asset["id"], text=text)
                        asset["text"] = text
            elif asset["kind"] == "article" and not asset.get("text"):
                url = asset.get("url", "")
                if url:
                    result = assets_mod.fetch_article(url)
                    _update_asset(asset["id"],
                                  text=result["text"],
                                  title=result["title"],
                                  retrieved_at=result["retrieved_at"])
                    asset["text"] = result["text"]

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

        # --- 4. Collect -----------------------------------------------------
        _push(run_id, "collect", "Fetching comments and transcripts", 5)
        comments_df, meta_df = collect.fetch(cfg)
        comments_df = collect.clean(comments_df, cfg)
        base_df = comments_df[comments_df["in_base"]].reset_index(drop=True)
        _push(run_id, "collect",
              f"Collected {len(base_df)} comments in analysis base", 20,
              detail=f"total fetched: {len(comments_df)}")

        # --- 5. Brief -------------------------------------------------------
        _push(run_id, "brief", "Reading the videos", 22)
        summary_str = "; ".join(
            meta_df["title"].fillna("").astype(str).head(6))
        grounded, points = brief.run(meta_df, cfg,
                                     context_map=campaign_context,
                                     images_map=images_map)
        _push(run_id, "brief",
              f"Brief complete - {len(points)} points discovered", 38,
              detail=str(len(points)))

        # --- 6. Insert brief points; brief pause ----------------------------
        _insert_brief_points(run_id, campaign["id"], points)
        _push(run_id, "brief_pause",
              "Brief ready for review. Waiting for approval.", 40,
              detail=str(len(points)))

        # Block until server.py calls proceed (sets the event).
        get_proceed_event(run_id).wait()

        # --- 7. Re-read approved / edited brief points ----------------------
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

        # --- 8. Classify ----------------------------------------------------
        _push(run_id, "classify", "Discovering themes", 42)
        themes = analyze.build(base_df, summary_str, cfg)
        _push(run_id, "classify",
              f"Classifying {len(base_df)} comments", 50,
              detail=f"{len(themes)} themes")
        base_df, columns = analyze.classify(base_df, themes, classifier_points, cfg)
        base_df, themes, other_share = analyze.extend(
            base_df, themes, classifier_points, summary_str, cfg,
            on_progress=lambda msg: _push(run_id, "classify", msg, 60))
        theme_table, transfer_table = analyze.summarise(base_df, columns)
        _push(run_id, "classify",
              f"Classification complete - {other_share:.0f}% Other", 65,
              detail=f"other_share={other_share:.1f}")

        # --- 9. Affect (emotion + sentiment) --------------------------------
        _push(run_id, "emotion", "Running emotion and sentiment analysis", 67)
        base_df, affect_result = analyze.affect(base_df, cfg)
        _push(run_id, "emotion", "Affect analysis complete", 75,
              detail=affect_result.get("emotion", {}).get("caveat", ""))

        # --- 10. Report -----------------------------------------------------
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

        # --- 11. Build report.json ------------------------------------------
        report_data = _build_report_json(
            run_id, session_row, videos, base_df, transfer_table,
            theme_table, themes, columns, grounded, affect_result, cfg)

        report_json_path = os.path.join(out_dir, "report.json")
        with open(report_json_path, "w", encoding="utf-8") as fh:
            json.dump(report_data, fh, ensure_ascii=False, indent=2)

        # --- 12. Copy artifacts to artifacts_dir and register in DB --------
        art_dir = storage.artifacts_dir(run_id)
        artifact_files = [
            ("report_pdf",         "report.pdf"),
            ("comments_csv",       "comments.csv"),
            ("summary_csv",        "summary.csv"),
            ("chart_transfer_csv", "chart_transfer.csv"),
            ("chart_themes_csv",   "chart_themes.csv"),
            ("report_json",        "report.json"),
        ]
        for kind, filename in artifact_files:
            src = os.path.join(out_dir, filename)
            if not os.path.isfile(src):
                logger.warning("artifact %s not found at %s, skipping", kind, src)
                continue
            dst = os.path.join(art_dir, filename)
            shutil.copy2(src, dst)
            _insert_artifact(run_id, kind, dst)

        # --- 13. Mark complete ----------------------------------------------
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
