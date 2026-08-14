# UNAUTHENTICATED - binds 127.0.0.1:8000, localhost-only by design. Single user, no auth.

import asyncio
import csv
import json
import logging
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Load .env before anything else touches environment variables.
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

from fastapi import FastAPI, HTTPException, Request, UploadFile, File
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import assets
import db
import storage
import adapter
from pipeline import brief as pipeline_brief

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="YouTube Comment Intelligence")


@app.on_event("startup")
def _startup():
    db.init()
    for key in ("YOUTUBE_API_KEY",):
        if not os.environ.get(key):
            logger.warning("%s is not set - runs will fail without it", key)


# ---------------------------------------------------------------------------
# Error helpers
# ---------------------------------------------------------------------------

def _err(status: int, code: str, message: str, field=None):
    raise HTTPException(status_code=status, detail={"error": code, "message": message, "field": field})


def _404(message="Not found."):
    _err(404, "NOT_FOUND", message)


def _409(message, code="CONFLICT"):
    _err(409, code, message)


def _422(message, field=None):
    _err(422, "VALIDATION_ERROR", message, field)


def _413(message):
    _err(413, "FILE_TOO_LARGE", message)


# FastAPI wraps HTTPException.detail as {"detail": ...} by default and
# emits its own {"detail": [...]} shape for Pydantic validation errors.
# Both handlers below unwrap to the bare {error, message, field} shape
# every client-facing error must have (PRD "HTTP errors").
#
# Registered on starlette.exceptions.HTTPException, not fastapi.HTTPException:
# fastapi.HTTPException is a subclass, so routes raising it are still
# covered, but routing-level 404/405 responses (unmatched path, wrong
# method) are raised by Starlette itself as the base class and would
# bypass a handler registered only on the FastAPI subclass.
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException


@app.exception_handler(StarletteHTTPException)
async def _http_exc(request, exc):
    detail = exc.detail
    if isinstance(detail, dict) and "error" in detail:
        return JSONResponse(status_code=exc.status_code, content=detail)
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": "ERROR", "message": str(detail), "field": None},
    )


@app.exception_handler(RequestValidationError)
async def _validation_exc(request, exc):
    first = exc.errors()[0] if exc.errors() else {}
    field = ".".join(str(x) for x in first.get("loc", [])[1:]) or None
    return JSONResponse(
        status_code=422,
        content={"error": "VALIDATION_ERROR", "message": first.get("msg", "Invalid input."), "field": field},
    )


# ---------------------------------------------------------------------------
# ISO timestamp helper
# ---------------------------------------------------------------------------

def _now():
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# YouTube URL parsing (server-side trust boundary)
# ---------------------------------------------------------------------------

_PLAYLIST_RE = re.compile(r"[?&]list=", re.I)
_WATCH_RE = re.compile(r"^(?:https?://)?(?:www\.)?youtube\.com/watch\?([^#]*)$", re.I)
_SHORT_RE = re.compile(r"^(?:https?://)?youtu\.be/([\w-]{6,20})(?:[?#].*)?$", re.I)
_SHORTS_RE = re.compile(r"^(?:https?://)?(?:www\.)?youtube\.com/shorts/([\w-]{6,20})(?:[?#].*)?$", re.I)
_VID_ID_RE = re.compile(r"^[\w-]{6,20}$")
_ALLOWED_KINDS = {"auto", "brand_ad", "review", "explainer"}


def _parse_youtube_url(raw: str) -> str:
    """Return the youtube_id or raise 422."""
    u = (raw or "").strip()
    if not u:
        _422("Paste a YouTube link first.", "url")
    if _PLAYLIST_RE.search(u):
        _422("Playlists are not supported. Add videos one at a time.", "url")
    m = _WATCH_RE.match(u)
    if m:
        vid = dict(x.split("=", 1) for x in m.group(1).split("&") if "=" in x).get("v", "")
        if vid and _VID_ID_RE.match(vid):
            return vid
        _422("That watch link is missing a valid video ID.", "url")
    m = _SHORT_RE.match(u)
    if m:
        return m.group(1)
    m = _SHORTS_RE.match(u)
    if m:
        return m.group(1)
    _422("Only youtube.com/watch?v=, youtu.be/ and youtube.com/shorts/ links work.", "url")


# ---------------------------------------------------------------------------
# Upload validation
# ---------------------------------------------------------------------------

_DOC_EXTS = {"pdf", "pptx", "docx"}
_IMG_EXTS = {"png", "jpg", "jpeg", "webp"}
_ALLOWED_EXTS = _DOC_EXTS | _IMG_EXTS
_MAX_UPLOAD = 10 * 1024 * 1024  # 10 MB


def _asset_kind_from_ext(ext: str) -> str:
    return "image" if ext in _IMG_EXTS else "document"


# ---------------------------------------------------------------------------
# Serializers - camelCase dicts matching demoApi shapes
# ---------------------------------------------------------------------------

def _session_status(session_id: str, conn) -> str:
    """Derive session status from its most recent run."""
    row = conn.execute(
        "SELECT state FROM runs WHERE session_id = ? ORDER BY started_at DESC, rowid DESC LIMIT 1",
        (session_id,)
    ).fetchone()
    if row is None:
        return "ready"
    s = row["state"]
    if s in ("queued", "running"):
        return "running"
    if s == "complete":
        return "complete"
    if s == "failed":
        return "failed"
    return "ready"


def _session_comment_count(session_id: str, conn) -> int:
    """Comment count from the latest complete run's comments.csv, or 0."""
    run = conn.execute(
        "SELECT id FROM runs WHERE session_id = ? AND state = 'complete' "
        "ORDER BY finished_at DESC, rowid DESC LIMIT 1",
        (session_id,)
    ).fetchone()
    if run is None:
        return 0
    art = conn.execute(
        "SELECT file_path FROM run_artifacts WHERE run_id = ? AND kind = 'comments_csv'",
        (run["id"],)
    ).fetchone()
    if art is None:
        return 0
    try:
        with open(art["file_path"], encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f)
            next(reader)
            return sum(1 for _ in reader)
    except (OSError, csv.Error, StopIteration):
        return 0


def _ser_session(row, conn) -> dict:
    sid = row["id"]
    campaigns = conn.execute(
        "SELECT id FROM campaigns WHERE session_id = ?", (sid,)
    ).fetchall()
    campaign_ids = [c["id"] for c in campaigns]

    latest_run_row = conn.execute(
        "SELECT * FROM runs WHERE session_id = ? ORDER BY started_at DESC, rowid DESC LIMIT 1",
        (sid,)
    ).fetchone()
    if latest_run_row is not None:
        state = latest_run_row["state"]
        stage = state if state in ("complete", "failed") else ("connecting" if state == "queued" else "running")
        latest_run = {
            "id": latest_run_row["id"],
            "status": state,
            "stage": stage,
            "pct": 100 if state == "complete" else 0,
            "message": "",
            "error": latest_run_row["error"],
        }
    else:
        latest_run = None

    return {
        "id": sid,
        "name": row["name"],
        "campaignIds": campaign_ids,
        "commentCount": _session_comment_count(sid, conn),
        "status": _session_status(sid, conn),
        "updatedAt": row["updated_at"],
        "createdAt": row["created_at"],
        "latestRun": latest_run,
        "keyMessages": _ser_key_message_draft(row, conn),
    }


def _ser_video(row) -> dict:
    return {
        "id": row["id"],
        "campaignId": row["campaign_id"],
        "url": row["url"],
        "videoId": row["youtube_id"],
        "kind": row["kind"],
    }


def _ser_asset(row) -> dict:
    row = dict(row)
    return {
        "id": row["id"],
        "campaignId": row["campaign_id"],
        "kind": row["kind"],
        "name": row.get("filename") or (row.get("url") or ""),
        "sourceUrl": row.get("url"),
        "mimeType": _mime_from_kind(row["kind"], row.get("filename")),
        "size": _file_size(row.get("file_path")),
        "addedAt": row.get("retrieved_at") or _now(),
        "status": "ready",
    }


def _mime_from_kind(kind: str, filename) -> str:
    if kind == "article":
        return "text/html"
    if not filename:
        return "application/octet-stream"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return {
        "pdf": "application/pdf",
        "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "webp": "image/webp",
    }.get(ext, "application/octet-stream")


def _file_size(path) -> int | None:
    if not path:
        return None
    try:
        return os.path.getsize(path)
    except OSError:
        return None


def _ser_campaign(row, conn) -> dict:
    cid = row["id"]
    videos = conn.execute("SELECT * FROM videos WHERE campaign_id = ?", (cid,)).fetchall()
    assets = conn.execute("SELECT * FROM assets WHERE campaign_id = ?", (cid,)).fetchall()
    # Latest run brief points
    run = conn.execute(
        "SELECT id FROM runs WHERE session_id = ? ORDER BY started_at DESC, rowid DESC LIMIT 1",
        (row["session_id"],)
    ).fetchone()
    brief_point_ids = []
    if run:
        bp_rows = conn.execute(
            "SELECT id FROM brief_points WHERE run_id = ? ORDER BY sort_order", (run["id"],)
        ).fetchall()
        brief_point_ids = [r["id"] for r in bp_rows]
    return {
        "id": cid,
        "sessionId": row["session_id"],
        "name": row["name"],
        "videoIds": [v["id"] for v in videos],
        "assetIds": [a["id"] for a in assets],
        "briefPointIds": brief_point_ids,
        "videos": [_ser_video(v) for v in videos],
        "assets": [_ser_asset(a) for a in assets],
    }


_RUN_STAGES = {"queued", "collect", "brief", "brief_pause", "classify",
               "emotion", "report", "complete", "error"}


def _ser_run(row, conn) -> dict:
    """RunSnapshot shape (PRD "Runs and SSE"): briefPoints and
    artifacts are always present, including empty arrays, so a fresh
    queued run and a completed run have the same shape."""
    rid = row["id"]
    state = row["state"]
    # stage: terminal DB states always win over whatever was last
    # persisted, since a run can crash leaving a stale non-terminal stage
    # on the row. Otherwise use the persisted fine-grained stage
    # adapter.py writes on every progress push (see adapter._set_run_stage)
    # so a tab reopened with no SSE connection to replay from can still
    # tell brief_pause apart from plain "running". "failed" is a run
    # status, not a RunStage - it maps to the "error" stage. row["stage"]
    # is never empty (schema default 'queued'); the `or` below is a
    # defensive fallback, not a case that happens in practice.
    if state == "complete":
        stage = "complete"
    elif state == "failed":
        stage = "error"
    else:
        stage = row["stage"] or "queued"
    assert stage in _RUN_STAGES, f"unexpected run stage {stage!r}"

    bp_rows = conn.execute(
        "SELECT * FROM brief_points WHERE run_id = ? ORDER BY sort_order", (rid,)
    ).fetchall()
    arts = conn.execute(
        "SELECT * FROM run_artifacts WHERE run_id = ?", (rid,)
    ).fetchall()
    # contract.get() is None for a legacy/unknown kind (old runs, or a kind
    # retired from the contract); such rows are skipped instead of a
    # KeyError crashing the whole snapshot.
    public_arts = []
    for a in arts:
        contract = _ARTIFACT_CONTRACT.get(a["kind"])
        if contract is not None and contract[3]:
            public_arts.append(a)
    public_arts.sort(key=lambda a: _ARTIFACT_CONTRACT[a["kind"]][0])

    return {
        "id": rid,
        "sessionId": row["session_id"],
        "status": state,
        "stage": stage,
        "pct": 100 if state == "complete" else 0,
        "message": "",
        "error": row["error"],
        "briefPoints": [_ser_brief_point(r) for r in bp_rows],
        "artifacts": [_ser_artifact(a) for a in public_arts],
    }


def _ser_brief_point(row) -> dict:
    """BriefPoint shape (== KeyMessage, PRD "Key Messages"): only
    these five fields. `approved`/`edited`/`runId`/`campaignId`/`videoId`
    stay in the DB row for internal bookkeeping but never cross the wire."""
    return {
        "id": row["id"],
        "label": row["label"],
        "description": row["description"],
        "included": bool(row["included"]),
        "order": row["sort_order"],
    }


def _ser_key_message(row) -> dict:
    return {
        "id": row["id"],
        "label": row["label"],
        "description": row["description"],
        "included": bool(row["included"]),
        "order": row["sort_order"],
    }


def _ser_key_message_draft(session_row, conn) -> dict:
    """KeyMessageDraft shape: {status, messages, error}. See PRD."""
    rows = conn.execute(
        "SELECT * FROM key_messages WHERE session_id = ? ORDER BY sort_order",
        (session_row["id"],)
    ).fetchall()
    return {
        "status": session_row["key_messages_status"],
        "messages": [_ser_key_message(r) for r in rows],
        "error": session_row["key_messages_error"],
        "revision": int(session_row["key_messages_revision"]),
    }


# Single source of truth for artifact order, filename, MIME, and public
# status (PRD "Artifacts"). order also drives RunSnapshot.artifacts
# ordering; public=False keeps report_json out of that list and download.
_ARTIFACT_CONTRACT = {
    "report_pdf":       (1, "report.pdf",       "application/pdf",  True),
    "comments_csv":     (2, "comments.csv",     "text/csv",         True),
    "key_messages_csv": (3, "key-messages.csv", "text/csv",         True),
    "themes_csv":       (4, "themes.csv",       "text/csv",         True),
    "sentiment_csv":    (5, "sentiment.csv",    "text/csv",         True),
    "emotions_csv":     (6, "emotions.csv",     "text/csv",         True),
    "report_json":      (7, "report.json",      "application/json", False),
}


def _ser_artifact(row) -> dict:
    _order, filename, content_type, _public = _ARTIFACT_CONTRACT[row["kind"]]
    return {
        "id": row["id"],
        "kind": row["kind"],
        "filename": filename,
        "contentType": content_type,
        "downloadUrl": f"/api/runs/{row['run_id']}/artifacts/{row['id']}",
    }


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------

class SessionBody(BaseModel):
    name: str

class CampaignBody(BaseModel):
    name: str

class VideoBody(BaseModel):
    url: str
    kind: str = "auto"

class ArticleBody(BaseModel):
    url: str

class KeyMessageIn(BaseModel):
    """Matches the KeyMessageInput type in PRD.md exactly: id is
    nullable (None creates a server-generated row), every other field is
    always resent by the client."""
    id: str | None
    label: str
    description: str
    included: bool
    order: int

class SaveKeyMessagesBody(BaseModel):
    messages: list[KeyMessageIn]

class BriefPointsBody(BaseModel):
    """BriefPointInput == KeyMessageInput (PRD "Runs and SSE")."""
    messages: list[KeyMessageIn]


# ---------------------------------------------------------------------------
# /api/sessions
# ---------------------------------------------------------------------------

@app.post("/api/sessions", status_code=201)
def create_session(body: SessionBody):
    name = (body.name or "").strip()
    if not name:
        _422("Session name is required.", "name")
    sid = str(uuid.uuid4())
    now = _now()
    conn = db.get_conn()
    try:
        conn.execute(
            "INSERT INTO sessions (id, name, created_at, updated_at) VALUES (?,?,?,?)",
            (sid, name, now, now)
        )
        conn.commit()
        row = conn.execute("SELECT * FROM sessions WHERE id = ?", (sid,)).fetchone()
        return _ser_session(row, conn)
    finally:
        conn.close()


@app.get("/api/sessions")
def list_sessions():
    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM sessions ORDER BY updated_at DESC"
        ).fetchall()
        result = [_ser_session(r, conn) for r in rows]
        # campaignCount for convenience (AGENTS.md mentions it for list)
        for item in result:
            item["campaignCount"] = len(item["campaignIds"])
        return result
    finally:
        conn.close()


@app.get("/api/sessions/{session_id}")
def get_session(session_id: str):
    conn = db.get_conn()
    try:
        row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            _404("Session not found.")
        s = _ser_session(row, conn)
        # Nested campaigns (with their nested videos + assets)
        camps = conn.execute(
            "SELECT * FROM campaigns WHERE session_id = ?", (session_id,)
        ).fetchall()
        s["campaigns"] = [_ser_campaign(c, conn) for c in camps]
        # Run summaries
        runs = conn.execute(
            "SELECT * FROM runs WHERE session_id = ? ORDER BY started_at DESC, rowid DESC",
            (session_id,)
        ).fetchall()
        s["runs"] = [_ser_run(r, conn) for r in runs]
        return s
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# /api/sessions/{id}/key_messages/draft - concurrency control
# ---------------------------------------------------------------------------
#
# At most one active draft per session. A request arriving while a draft
# is in flight for the same session does not spawn a second model call:
# it flags one coalesced rerun (covering every asset saved up to that
# point) and waits for it, then returns that rerun's result. Any number
# of concurrent latecomers share the same wait and the same rerun.
#
# Three dicts, one lock, keyed by session_id - same shape as adapter.py's
# _queues/_proceed_events/_lock for per-run state. No thread is ever
# spawned here: the thread that first acquires a session's lock (an
# existing FastAPI threadpool thread, not a new one) runs the draft pass,
# and loops to run one more pass in place if a rerun was requested while
# it worked. Latecomer threads only ever wait; they never draft.

_draft_locks: dict[str, threading.Lock] = {}
_draft_rerun_requested: dict[str, bool] = {}
_draft_done_events: dict[str, threading.Event] = {}
_draft_state_lock = threading.Lock()


def _get_draft_lock(session_id: str) -> threading.Lock:
    with _draft_state_lock:
        if session_id not in _draft_locks:
            _draft_locks[session_id] = threading.Lock()
        return _draft_locks[session_id]


# ---------------------------------------------------------------------------
# /api/sessions/{id}/key_messages/draft - input loading
# ---------------------------------------------------------------------------

def _load_draft_inputs(session_id: str, conn) -> tuple[str, list[tuple[bytes, str]], dict | None]:
    """Return (text, images, campaign) from every persisted User Input on
    this Session's campaign. A Session with no campaign yet (setup not
    started) has no inputs: ("", [], None).

    text concatenates every asset's extracted/fetched text (documents,
    articles). images is capped at 6 and skips files over 5 MB, matching
    adapter.py's run-time image collection (docs/architecture.md, User
    Inputs section). campaign is returned alongside so the caller can
    build a PipelineConfig without a second query.
    """
    camp = conn.execute(
        "SELECT * FROM campaigns WHERE session_id = ?", (session_id,)
    ).fetchone()
    if camp is None:
        return "", [], None
    camp = dict(camp)

    asset_rows = conn.execute(
        "SELECT * FROM assets WHERE campaign_id = ?", (camp["id"],)
    ).fetchall()

    text_parts = []
    images: list[tuple[bytes, str]] = []
    for a in asset_rows:
        if a["text"]:
            text_parts.append(a["text"])
        if a["kind"] == "image" and len(images) < 6:
            fp = a["file_path"]
            if fp and os.path.isfile(fp):
                try:
                    if os.path.getsize(fp) <= 5 * 1024 * 1024:
                        with open(fp, "rb") as fh:
                            images.append((fh.read(), _mime_from_kind("image", a["filename"])))
                except OSError:
                    pass  # unreadable file - skip silently, same as adapter.py

    return "\n\n".join(text_parts), images, camp


# ---------------------------------------------------------------------------
# /api/sessions/{id}/key_messages/draft - proposal merge
# ---------------------------------------------------------------------------

def _merge_key_messages(existing: list[dict], proposals: list[dict]) -> list[dict]:
    """Merge fresh model proposals into the current Key Message list.

    Rules (PRD "Draft Key Messages"):
    - A match is case-insensitive, whitespace-normalized label equality
      between an existing row and a proposal.
    - An edited existing row survives verbatim (label, description,
      included, order) whether or not a proposal matches it.
    - An unedited existing row is replaced by the matching proposal's
      label/description if one matches (its included/order survive, per
      "preserve manual included/order values"); it is dropped if no
      proposal matches, since unedited rows are exactly the ones a fresh
      draft is allowed to replace.
    - A proposal that matches no existing row is appended as new,
      unedited, included, ordered after everything kept.

    Returns KeyMessage-shaped dicts (id, label, description, included,
    order, edited), ready to persist as key_messages rows.
    """
    def norm(label: str) -> str:
        return (label or "").strip().lower()

    def as_key_message(row: dict) -> dict:
        """Normalize a key_messages DB row (sort_order/int flags) to the
        KeyMessage shape (order/bool flags) this function works in."""
        return {
            "id": row["id"],
            "label": row["label"],
            "description": row.get("description", ""),
            "included": bool(row["included"]),
            "order": row["sort_order"],
            "edited": bool(row["edited"]),
        }

    proposals_by_key: dict[str, dict] = {}
    for p in proposals:
        key = norm(p["label"])
        proposals_by_key.setdefault(key, p)  # first occurrence wins - most central

    matched_keys = set()
    kept = []
    for row in existing:
        entry = as_key_message(row)
        key = norm(entry["label"])
        if entry["edited"]:
            kept.append(entry)
            if key in proposals_by_key:
                matched_keys.add(key)
            continue
        proposal = proposals_by_key.get(key)
        if proposal is None:
            continue  # unedited and not reconfirmed by the latest draft - drop
        matched_keys.add(key)
        kept.append({
            **entry,
            "label": proposal["label"],
            "description": proposal.get("description", ""),
        })

    next_order = max((r["order"] for r in kept), default=-1) + 1
    for p in proposals:
        key = norm(p["label"])
        if key in matched_keys:
            continue
        matched_keys.add(key)
        kept.append({
            "id": str(uuid.uuid4()),
            "label": p["label"],
            "description": p.get("description", ""),
            "included": True,
            "order": next_order,
            "edited": False,
        })
        next_order += 1

    return kept


# ---------------------------------------------------------------------------
# /api/sessions/{id}/key_messages/draft - one drafting pass
# ---------------------------------------------------------------------------

def _run_one_draft_pass(session_id: str) -> None:
    """Draft once against the latest persisted assets and persist the
    result atomically. Never raises: a model failure is recorded as a
    status/error update, not an exception.

    Guards every write with `WHERE key_messages_revision = expected`, so
    a write from a superseded pass can never clobber a newer one - see
    PRD "Increment/use session revision to prevent obsolete
    results overwriting newer requests." Passes for one session are
    already serialized by the caller's lock, so this is a defensive
    no-op today, not a substitute for that lock.
    """
    conn = db.get_conn()
    try:
        session_row = conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if session_row is None:
            return  # session deleted mid-flight; nothing to draft
        expected_revision = session_row["key_messages_revision"]

        existing = [dict(r) for r in conn.execute(
            "SELECT * FROM key_messages WHERE session_id = ? ORDER BY sort_order",
            (session_id,)
        ).fetchall()]

        text, images, camp = _load_draft_inputs(session_id, conn)
    finally:
        conn.close()

    try:
        cfg = adapter._build_config(
            None, session_row, camp or {"name": session_row["name"]}, [], {})
        proposals = pipeline_brief.draft_from_inputs(text, images, cfg)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        status = "stale" if existing else "failed"
        conn = db.get_conn()
        try:
            conn.execute(
                """UPDATE sessions
                   SET key_messages_status = ?, key_messages_error = ?,
                       key_messages_revision = key_messages_revision + 1, updated_at = ?
                   WHERE id = ? AND key_messages_revision = ?""",
                (status, error, _now(), session_id, expected_revision)
            )
            conn.commit()
        finally:
            conn.close()
        return

    merged = _merge_key_messages(existing, proposals)
    status = "ready" if merged else "empty"
    conn = db.get_conn()
    try:
        result = conn.execute(
            "UPDATE sessions SET key_messages_revision = key_messages_revision + 1 "
            "WHERE id = ? AND key_messages_revision = ?",
            (session_id, expected_revision)
        )
        if result.rowcount == 0:
            return  # superseded by a newer pass between our read and this write
        conn.execute("DELETE FROM key_messages WHERE session_id = ?", (session_id,))
        for m in merged:
            conn.execute(
                """INSERT INTO key_messages
                   (id, session_id, label, description, included, sort_order, edited)
                   VALUES (?,?,?,?,?,?,?)""",
                (m["id"], session_id, m["label"], m["description"],
                 int(m["included"]), m["order"], int(m["edited"]))
            )
        conn.execute(
            "UPDATE sessions SET key_messages_status = ?, key_messages_error = NULL, "
            "updated_at = ? WHERE id = ?",
            (status, _now(), session_id)
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# /api/sessions/{id}/key_messages
# ---------------------------------------------------------------------------

@app.post("/api/sessions/{session_id}/key_messages/draft")
def draft_key_messages(session_id: str):
    conn = db.get_conn()
    try:
        if conn.execute("SELECT id FROM sessions WHERE id = ?", (session_id,)).fetchone() is None:
            _404("Session not found.")
    finally:
        conn.close()

    lock = _get_draft_lock(session_id)
    if lock.acquire(blocking=False):
        try:
            while True:
                _run_one_draft_pass(session_id)
                with _draft_state_lock:
                    rerun = _draft_rerun_requested.get(session_id, False)
                    _draft_rerun_requested[session_id] = False
                if not rerun:
                    break
        finally:
            lock.release()
            with _draft_state_lock:
                ev = _draft_done_events.get(session_id)
                _draft_done_events[session_id] = threading.Event()  # fresh for the next cycle
            if ev is not None:
                ev.set()
    else:
        # A draft is already active for this session. Ask it to run one
        # more pass against whatever is persisted by the time it gets to
        # it, and wait for that pass rather than starting a second one.
        with _draft_state_lock:
            _draft_rerun_requested[session_id] = True
            ev = _draft_done_events.setdefault(session_id, threading.Event())
        ev.wait()

    conn = db.get_conn()
    try:
        row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            _404("Session not found.")
        return _ser_key_message_draft(row, conn)
    finally:
        conn.close()


@app.patch("/api/sessions/{session_id}/key_messages")
def save_key_messages(session_id: str, body: SaveKeyMessagesBody):
    conn = db.get_conn()
    try:
        if conn.execute("SELECT id FROM sessions WHERE id = ?", (session_id,)).fetchone() is None:
            _404("Session not found.")

        messages = body.messages
        non_null_ids = [m.id for m in messages if m.id is not None]

        seen = set()
        for i in non_null_ids:
            if i in seen:
                _422(f"Duplicate Key Message id: {i}.", "messages")
            seen.add(i)

        # Non-null ids must belong to this Session (also rejects ids that
        # exist but were owned by another Session). id:null is a create
        # and gets a fresh server UUID below, so it is never checked here.
        existing_ids = {r["id"] for r in conn.execute(
            "SELECT id FROM key_messages WHERE session_id = ?", (session_id,)
        ).fetchall()}
        for i in non_null_ids:
            if i not in existing_ids:
                _422(f"Unknown Key Message id: {i}.", "messages")

        for m in messages:
            label = (m.label or "").strip()
            if not label:
                _422("Every Key Message needs a label.", "messages")
            if len(label) > 120:
                _422("Key Message label must be 120 characters or fewer.", "messages")
            if len((m.description or "").strip()) > 500:
                _422("Key Message description must be 500 characters or fewer.", "messages")

        # Complete ordered replacement in one transaction: delete then
        # reinsert. id:null mints a server UUID (the locked create rule);
        # every other id is the caller's, already validated above. Write
        # order is the submitted array position, zero-based, not the
        # client-supplied `order` field.
        conn.execute("DELETE FROM key_messages WHERE session_id = ?", (session_id,))
        for order, m in enumerate(messages):
            row_id = m.id if m.id is not None else str(uuid.uuid4())
            conn.execute(
                """INSERT INTO key_messages
                   (id, session_id, label, description, included, sort_order, edited)
                   VALUES (?,?,?,?,?,?,1)""",
                (row_id, session_id, m.label.strip(), (m.description or "").strip(),
                 int(m.included), order)
            )
        conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (_now(), session_id))
        conn.commit()

        row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return _ser_key_message_draft(row, conn)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# /api/sessions/{id}/campaigns
# ---------------------------------------------------------------------------

@app.post("/api/sessions/{session_id}/campaigns", status_code=201)
def create_campaign(session_id: str, body: CampaignBody):
    name = (body.name or "").strip()
    if not name:
        _422("Campaign name is required.", "name")
    conn = db.get_conn()
    try:
        if conn.execute("SELECT id FROM sessions WHERE id = ?", (session_id,)).fetchone() is None:
            _404("Session not found.")
        if conn.execute("SELECT id FROM campaigns WHERE session_id=?", (session_id,)).fetchone():
            _409("This session already has a campaign.")
        cid = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO campaigns (id, session_id, name) VALUES (?,?,?)",
            (cid, session_id, name)
        )
        # touch session updated_at
        conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (_now(), session_id))
        conn.commit()
        row = conn.execute("SELECT * FROM campaigns WHERE id = ?", (cid,)).fetchone()
        return _ser_campaign(row, conn)
    finally:
        conn.close()


@app.get("/api/sessions/{session_id}/campaigns")
def list_campaigns(session_id: str):
    conn = db.get_conn()
    try:
        if conn.execute("SELECT id FROM sessions WHERE id = ?", (session_id,)).fetchone() is None:
            _404("Session not found.")
        rows = conn.execute(
            "SELECT * FROM campaigns WHERE session_id = ?", (session_id,)
        ).fetchall()
        return [_ser_campaign(r, conn) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# /api/campaigns/{id}/videos
# ---------------------------------------------------------------------------

@app.post("/api/campaigns/{campaign_id}/videos", status_code=201)
def add_video(campaign_id: str, body: VideoBody):
    conn = db.get_conn()
    try:
        camp = conn.execute("SELECT * FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()
        if camp is None:
            _404("Campaign not found.")

        kind = (body.kind or "auto").strip()
        if kind not in _ALLOWED_KINDS:
            _422(f"kind must be one of: {', '.join(sorted(_ALLOWED_KINDS))}.", "kind")

        youtube_id = _parse_youtube_url(body.url)

        # Duplicate check within this campaign.
        dup = conn.execute(
            "SELECT id FROM videos WHERE campaign_id = ? AND youtube_id = ?",
            (campaign_id, youtube_id)
        ).fetchone()
        if dup:
            _422("That video is already in this campaign.", "url")

        vid = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO videos (id, campaign_id, url, youtube_id, kind) VALUES (?,?,?,?,?)",
            (vid, campaign_id, body.url.strip(), youtube_id, kind)
        )
        conn.execute(
            "UPDATE sessions SET updated_at = ? WHERE id = ?",
            (_now(), camp["session_id"])
        )
        conn.commit()
        row = conn.execute("SELECT * FROM videos WHERE id = ?", (vid,)).fetchone()
        return _ser_video(row)
    finally:
        conn.close()


@app.delete("/api/videos/{video_id}", status_code=204)
def remove_video(video_id: str):
    conn = db.get_conn()
    try:
        v = conn.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
        if v is None:
            return  # idempotent
        camp = conn.execute("SELECT * FROM campaigns WHERE id = ?", (v["campaign_id"],)).fetchone()
        conn.execute("DELETE FROM videos WHERE id = ?", (video_id,))
        if camp:
            conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (_now(), camp["session_id"])
            )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# /api/campaigns/{id}/assets/upload
# ---------------------------------------------------------------------------

@app.post("/api/campaigns/{campaign_id}/assets/upload", status_code=201)
async def upload_asset(campaign_id: str, file: UploadFile = File(...)):
    conn = db.get_conn()
    try:
        camp = conn.execute("SELECT * FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()
        if camp is None:
            _404("Campaign not found.")

        filename = (file.filename or "").strip()
        if not filename:
            _422("The file needs a name.", "file")

        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if ext not in _ALLOWED_EXTS:
            _422("PDF, PPTX, DOCX, PNG, JPG or WEBP only.", "file")

        # Read bytes BEFORE writing so we can validate size first.
        data = await file.read()
        if len(data) > _MAX_UPLOAD:
            _413("Files are limited to 10 MB.")

        # Unique filename to avoid collisions.
        unique_name = f"{uuid.uuid4().hex}_{filename}"
        file_path = storage.save_upload(unique_name, data)

        kind = _asset_kind_from_ext(ext)
        # Extraction runs on successful save, not deferred to run start.
        # assets.extract_upload() never raises and returns "" for images
        # and for any extraction failure (encrypted/scanned PDF, etc.) -
        # that empty text is stored as-is; it does not corrupt the row.
        text = assets.extract_upload(file_path)
        aid = str(uuid.uuid4())
        now = _now()
        conn.execute(
            """INSERT INTO assets (id, campaign_id, kind, filename, url, title, text, retrieved_at, file_path)
               VALUES (?,?,?,?,NULL,NULL,?,?,?)""",
            (aid, campaign_id, kind, filename, text or None, now, file_path)
        )
        conn.execute(
            "UPDATE sessions SET updated_at = ? WHERE id = ?", (now, camp["session_id"])
        )
        conn.commit()
        row = conn.execute("SELECT * FROM assets WHERE id = ?", (aid,)).fetchone()
        return _ser_asset(row)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# /api/campaigns/{id}/assets/article
# ---------------------------------------------------------------------------

@app.post("/api/campaigns/{campaign_id}/assets/article", status_code=201)
def add_article(campaign_id: str, body: ArticleBody):
    conn = db.get_conn()
    try:
        camp = conn.execute("SELECT * FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()
        if camp is None:
            _404("Campaign not found.")

        url = (body.url or "").strip()
        if not re.match(r"^https?://", url, re.I):
            _422("Articles need a full http:// or https:// link.", "url")

        # Fetch runs on successful save, not deferred to run start.
        # assets.fetch_article() never raises; a failed fetch returns
        # empty text and the asset is still saved (see Risks table).
        fetched = assets.fetch_article(url)

        aid = str(uuid.uuid4())
        conn.execute(
            """INSERT INTO assets (id, campaign_id, kind, filename, url, title, text, retrieved_at, file_path)
               VALUES (?,?,?,NULL,?,?,?,?,NULL)""",
            (aid, campaign_id, "article", url,
             fetched["title"] or None, fetched["text"] or None, fetched["retrieved_at"])
        )
        now = fetched["retrieved_at"]
        conn.execute(
            "UPDATE sessions SET updated_at = ? WHERE id = ?", (now, camp["session_id"])
        )
        conn.commit()
        row = conn.execute("SELECT * FROM assets WHERE id = ?", (aid,)).fetchone()
        return _ser_asset(row)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# /api/assets/{id} DELETE
# ---------------------------------------------------------------------------

@app.delete("/api/assets/{asset_id}", status_code=204)
def remove_asset(asset_id: str):
    conn = db.get_conn()
    try:
        a = conn.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
        if a is None:
            return  # idempotent
        camp = conn.execute("SELECT * FROM campaigns WHERE id = ?", (a["campaign_id"],)).fetchone()
        if a["kind"] in ("document", "image") and a["file_path"]:
            try:
                os.remove(a["file_path"])
            except OSError:
                pass
        conn.execute("DELETE FROM assets WHERE id = ?", (asset_id,))
        if camp:
            conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (_now(), camp["session_id"])
            )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# /api/assets/{id}/file  GET
# ---------------------------------------------------------------------------

@app.get("/api/assets/{asset_id}/file")
def get_asset_file(asset_id: str):
    conn = db.get_conn()
    try:
        a = conn.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
        if a is None:
            _404("Asset not found.")
        path = a["file_path"]
        if not path:
            _404("Asset not found.")  # ponytail: could distinguish article vs missing file; one message keeps it simple
        if not os.path.isfile(path):
            _404("Asset not found.")
        filename = os.path.basename(path)
        return FileResponse(
            path,
            filename=filename,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# /api/sessions/{id}/runs  POST
# ---------------------------------------------------------------------------

@app.post("/api/sessions/{session_id}/runs", status_code=202)
def start_run(session_id: str):
    conn = db.get_conn()
    try:
        if conn.execute("SELECT id FROM sessions WHERE id = ?", (session_id,)).fetchone() is None:
            _404("Session not found.")

        # Reject a second concurrent run BEFORE deleting anything: overwrite
        # (below) is only safe once we know no run is currently in flight,
        # otherwise this would nuke a running adapter thread's brief_points
        # and artifacts out from under it.
        active = conn.execute(
            "SELECT id FROM runs WHERE session_id = ? AND state IN ('queued', 'running')",
            (session_id,)
        ).fetchone()
        if active:
            _409("This session already has a run in progress.", "RUN_IN_PROGRESS")

        # Overwrite: clear prior runs for this session before inserting the new one.
        # ponytail: hard overwrite, no run history; if history is wanted later, keep rows
        # and add a `latest` flag instead of deleting.
        # The client is expected to confirm the overwrite before calling (frontend concern, Phase 2).
        old_ids = [r[0] for r in conn.execute(
            "SELECT id FROM runs WHERE session_id=?", (session_id,)
        ).fetchall()]
        for old_id in old_ids:
            storage.clear_run(old_id)
        conn.execute("DELETE FROM runs WHERE session_id=?", (session_id,))
        conn.commit()

        rid = str(uuid.uuid4())
        now = _now()
        conn.execute(
            "INSERT INTO runs (id, session_id, state, started_at) VALUES (?,?,?,?)",
            (rid, session_id, "queued", now)
        )
        conn.execute(
            "UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id)
        )
        conn.commit()
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (rid,)).fetchone()
        result = _ser_run(row, conn)
    finally:
        conn.close()

    # Start the adapter thread AFTER the DB transaction commits.
    adapter.start_run(rid)
    return result


# ---------------------------------------------------------------------------
# /api/runs/{id}  GET
# ---------------------------------------------------------------------------

@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    conn = db.get_conn()
    try:
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            _404("Run not found.")
        return _ser_run(row, conn)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# /api/runs/{id}/brief_points  PATCH
# ---------------------------------------------------------------------------

@app.patch("/api/runs/{run_id}/brief_points")
def update_brief_points(run_id: str, body: BriefPointsBody):
    conn = db.get_conn()
    try:
        run = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if run is None:
            _404("Run not found.")

        # 409 if past brief phase: the run must currently be paused at
        # brief_pause. Checked against the persisted stage column
        # (adapter._set_run_stage), not "brief_points exist" - a Session
        # with no Key Messages and no transcript-derived ones reconciles
        # to an empty list, and that empty list is still a legitimate
        # brief_pause the user must be able to edit (see the insert path
        # below), not an "already moved on" state.
        if run["state"] not in ("running", "queued") or run["stage"] != "brief_pause":
            _409("Brief review is only open while the run is paused at the brief stage.")

        proceed_event = adapter.get_proceed_event(run_id)
        if proceed_event.is_set():
            _409("Brief has already been approved and the run has continued.")

        messages = body.messages

        existing = conn.execute(
            "SELECT id, campaign_id FROM brief_points WHERE run_id = ?", (run_id,)
        ).fetchall()
        existing_ids = {r["id"] for r in existing}
        if existing:
            campaign_id = existing[0]["campaign_id"]
        else:
            campaign_row = conn.execute(
                "SELECT id FROM campaigns WHERE session_id = ?", (run["session_id"],)
            ).fetchone()
            if campaign_row is None:
                _404("Campaign not found.")
            campaign_id = campaign_row["id"]

        # Validate the complete list before any write (PRD "Key
        # Messages"): id:null mints a server UUID below; a duplicate
        # non-null id, an id unknown to this run, or an id owned by
        # another run (which is equally "not in existing_ids", since
        # existing_ids is scoped to this run_id) is a 422 naming
        # field "messages". At-least-one-included is proceed_run's job,
        # not this route's - an all-excluded save must still succeed so
        # the user can flip inclusion back on before proceeding.
        seen_ids = set()
        for m in messages:
            label = (m.label or "").strip()
            if not label:
                _422("Every Key Message needs a label.", "messages")
            if len(label) > 120:
                _422("Key Message labels are limited to 120 characters.", "messages")
            if len((m.description or "").strip()) > 500:
                _422("Key Message descriptions are limited to 500 characters.", "messages")
            if m.id is None:
                continue
            if m.id in seen_ids:
                _422(f"Duplicate Key Message id: {m.id}.", "messages")
            seen_ids.add(m.id)
            if m.id not in existing_ids:
                _422(f"Unknown Key Message id: {m.id}.", "messages")

        # Atomic full replace: delete then reinsert in submitted order.
        # A row omitted from `messages` is simply not reinserted, so it
        # is deleted along with everything else.
        conn.execute("DELETE FROM brief_points WHERE run_id = ?", (run_id,))
        for sort_order, m in enumerate(messages):
            row_id = m.id if m.id is not None else str(uuid.uuid4())
            conn.execute(
                """INSERT INTO brief_points
                   (id, run_id, campaign_id, video_id, label, description,
                    approved, edited, included, sort_order)
                   VALUES (?, ?, ?, NULL, ?, ?, 0, 1, ?, ?)""",
                (row_id, run_id, campaign_id, m.label.strip(),
                 (m.description or "").strip(), int(m.included), sort_order)
            )
        conn.commit()

        saved = conn.execute(
            "SELECT * FROM brief_points WHERE run_id = ? ORDER BY sort_order", (run_id,)
        ).fetchall()
        return {"messages": [_ser_brief_point(r) for r in saved]}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# /api/runs/{id}/proceed  POST
# ---------------------------------------------------------------------------

@app.post("/api/runs/{run_id}/proceed")
def proceed_run(run_id: str):
    conn = db.get_conn()
    try:
        run = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if run is None:
            _404("Run not found.")

        # Checked against the persisted stage, not "brief_points exist" -
        # brief_points now exist from the pre-collect snapshot onward
        # (see adapter._replace_brief_points), so their mere presence no
        # longer means the run has reached the review pause.
        if run["state"] not in ("running", "queued") or run["stage"] != "brief_pause":
            _409("This run is not waiting for review.")

        bp = conn.execute(
            "SELECT * FROM brief_points WHERE run_id = ? ORDER BY sort_order", (run_id,)
        ).fetchall()

        proceed_event = adapter.get_proceed_event(run_id)
        if proceed_event.is_set():
            _409("This run is not waiting for review.")

        # Verify at least one included point.
        if not any(r["included"] for r in bp):
            _422("Include at least one Key Message before continuing.", "messages")

        proceed_event.set()
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return _ser_run(row, conn)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# /api/runs/{id}/events  GET (SSE)
# ---------------------------------------------------------------------------

@app.get("/api/runs/{run_id}/events")
async def run_events(run_id: str):
    conn = db.get_conn()
    try:
        if conn.execute("SELECT id FROM runs WHERE id = ?", (run_id,)).fetchone() is None:
            _404("Run not found.")
    finally:
        conn.close()

    async def _generate():
        # If already terminal when the client connects, replay terminal state once.
        if adapter.is_terminal(run_id):
            q = adapter.get_queue(run_id)
            # Drain any buffered events first.
            while True:
                try:
                    item = q.get_nowait()
                    yield f"data: {json.dumps(item)}\n\n"
                    if item.get("stage") in ("complete", "error"):
                        return
                except Exception:
                    break
            # Queue empty but terminal - send a synthetic terminal event.
            conn2 = db.get_conn()
            try:
                run_row = conn2.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
                if run_row:
                    stage = "complete" if run_row["state"] == "complete" else "error"
                    yield f"data: {json.dumps({'run_id': run_id, 'stage': stage, 'message': run_row['error'] or stage, 'pct': 100 if stage == 'complete' else 0, 'detail': None})}\n\n"
            finally:
                conn2.close()
            return

        q = adapter.get_queue(run_id)
        loop = asyncio.get_event_loop()
        heartbeat_interval = 15.0
        last_event = loop.time()

        while True:
            elapsed = loop.time() - last_event
            wait = max(0.0, heartbeat_interval - elapsed)

            # Non-blocking poll with a short sleep to avoid CPU spin.
            item = None
            try:
                item = await loop.run_in_executor(
                    None, lambda: q.get(timeout=min(wait, 1.0))
                )
            except Exception:
                pass  # queue.Empty or timeout

            if item is not None:
                yield f"data: {json.dumps(item)}\n\n"
                last_event = loop.time()
                if item.get("stage") in ("complete", "error"):
                    return
            else:
                now = loop.time()
                if now - last_event >= heartbeat_interval:
                    yield ": heartbeat\n\n"
                    last_event = now
                # If adapter thread is gone and terminal, close.
                if adapter.is_terminal(run_id):
                    # Drain remaining items.
                    while True:
                        try:
                            leftover = q.get_nowait()
                            yield f"data: {json.dumps(leftover)}\n\n"
                            if leftover.get("stage") in ("complete", "error"):
                                return
                        except Exception:
                            break
                    return

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# /api/runs/{id}/report  GET
# ---------------------------------------------------------------------------

@app.get("/api/runs/{run_id}/report")
def get_report(run_id: str):
    conn = db.get_conn()
    try:
        run = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if run is None:
            _404("Run not found.")
        if run["state"] != "complete":
            _409("The report is only available once the run completes.")
        art = conn.execute(
            "SELECT file_path FROM run_artifacts WHERE run_id = ? AND kind = 'report_json'",
            (run_id,)
        ).fetchone()
        if art is None:
            _409("Report artifact not found.")
        try:
            with open(art["file_path"], encoding="utf-8") as f:
                return json.load(f)
        except OSError:
            _409("Report file is not readable.")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# /api/runs/{id}/artifacts/{artifact_id}  GET
# ---------------------------------------------------------------------------

@app.get("/api/runs/{run_id}/artifacts/{artifact_id}")
def download_artifact(run_id: str, artifact_id: str):
    conn = db.get_conn()
    try:
        art = conn.execute(
            "SELECT * FROM run_artifacts WHERE id = ? AND run_id = ?",
            (artifact_id, run_id)
        ).fetchone()
        if art is None:
            _404("Artifact not found.")
        contract = _ARTIFACT_CONTRACT.get(art["kind"])
        if contract is None or not contract[3]:
            _404("Artifact not found.")
        _order, filename, content_type, _public = contract
        path = art["file_path"]
        if not os.path.isfile(path):
            _404("Artifact not found.")
        return FileResponse(
            path,
            media_type=content_type,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Static files - mount AFTER all /api routes so API takes precedence.
# ---------------------------------------------------------------------------

_APP_DIR = Path(__file__).parent / "app"
if _APP_DIR.exists():
    app.mount("/", StaticFiles(directory=str(_APP_DIR), html=True), name="static")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=False)
