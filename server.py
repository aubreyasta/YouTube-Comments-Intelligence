# UNAUTHENTICATED - binds 127.0.0.1:8000, localhost-only by design. Single user, no auth.

import asyncio
import json
import logging
import os
import re
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

import db
import storage
import adapter

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="YouTube Comment Intelligence")


@app.on_event("startup")
def _startup():
    db.init()
    for key in ("YOUTUBE_API_KEY", "GEMINI_API_KEY"):
        if not os.environ.get(key):
            logger.warning("%s is not set - runs will fail without it", key)


# ---------------------------------------------------------------------------
# Error helpers
# ---------------------------------------------------------------------------

def _err(status: int, code: str, message: str, field=None):
    raise HTTPException(status_code=status, detail={"error": code, "message": message, "field": field})


def _404(message="Not found."):
    _err(404, "not_found", message)


def _409(message):
    _err(409, "conflict", message)


def _422(message, field=None):
    _err(422, "validation", message, field)


def _413(message):
    _err(413, "file_too_large", message)


# FastAPI returns its own 422 for Pydantic; we override the detail format for
# our own validation errors using the helpers above. HTTPException detail is
# passed through as-is when it's already a dict.
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError


@app.exception_handler(RequestValidationError)
async def _validation_exc(request, exc):
    first = exc.errors()[0] if exc.errors() else {}
    field = ".".join(str(x) for x in first.get("loc", [])[1:]) or None
    return JSONResponse(
        status_code=422,
        content={"error": "validation", "message": first.get("msg", "Invalid input."), "field": field},
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
    """Comment count from the latest complete run's report.json, or 0."""
    run = conn.execute(
        "SELECT id FROM runs WHERE session_id = ? AND state = 'complete' ORDER BY finished_at DESC LIMIT 1",
        (session_id,)
    ).fetchone()
    if run is None:
        return 0
    art = conn.execute(
        "SELECT file_path FROM run_artifacts WHERE run_id = ? AND kind = 'report_json'",
        (run["id"],)
    ).fetchone()
    if art is None:
        return 0
    try:
        with open(art["file_path"], encoding="utf-8") as f:
            data = json.load(f)
        # subtitle e.g. "1,234 comments · ..."
        m = re.match(r"([\d,]+)\s+comments", data.get("subtitle", ""))
        if m:
            return int(m.group(1).replace(",", ""))
    except Exception:
        pass
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
        "isKeyVisual": False,  # ponytail: key-visual toggle deferred (no route yet)
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


def _ser_run(row, conn) -> dict:
    rid = row["id"]
    bp_rows = conn.execute(
        "SELECT id FROM brief_points WHERE run_id = ? ORDER BY sort_order", (rid,)
    ).fetchall()
    brief_point_ids = [r["id"] for r in bp_rows]

    # Derive frontend status/stage from DB state.
    # DB states: queued | running | complete | failed
    # Frontend status: queued | running | complete | failed
    state = row["state"]
    # stage is not stored in DB; when running we report "running" as a generic stage.
    # The SSE stream carries real stage updates; getRun is a snapshot.
    stage = state if state in ("complete", "failed") else ("connecting" if state == "queued" else "running")

    result = {
        "id": rid,
        "sessionId": row["session_id"],
        "status": state,
        "stage": stage,
        "pct": 100 if state == "complete" else 0,
        "message": "",
        "briefPointIds": brief_point_ids,
        "error": row["error"],
        "createdAt": row["started_at"] or _now(),
    }

    # When complete, include artifacts.
    if state == "complete":
        arts = conn.execute(
            "SELECT * FROM run_artifacts WHERE run_id = ?", (rid,)
        ).fetchall()
        result["artifacts"] = [_ser_artifact(a) for a in arts]

    return result


def _ser_brief_point(row) -> dict:
    return {
        "id": row["id"],
        "runId": row["run_id"],
        "campaignId": row["campaign_id"],
        "videoId": row["video_id"],
        "label": row["label"],
        "description": row["description"],
        "approved": bool(row["approved"]),
        "edited": bool(row["edited"]),
        "included": bool(row["included"]),
        "order": row["sort_order"],
    }


_ARTIFACT_TIER = {
    "report_pdf": "primary",
    "summary_csv": "primary",
    "chart_transfer_csv": "primary",
    "chart_themes_csv": "primary",
    "report_json": "primary",
    "comments_csv": "advanced",
}


def _ser_artifact(row) -> dict:
    rid = row["run_id"]
    filename = os.path.basename(row["file_path"])
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    kind_to_name = {
        "report_pdf": "report.pdf",
        "comments_csv": "comments.csv",
        "summary_csv": "summary.csv",
        "chart_transfer_csv": "chart_transfer.csv",
        "chart_themes_csv": "chart_themes.csv",
        "report_json": "report.json",
    }
    return {
        "id": row["id"],
        "runId": rid,
        "kind": ext if ext in ("pdf", "csv", "json") else "file",
        "name": kind_to_name.get(row["kind"], filename),
        "fileKind": row["kind"],
        "tier": _ARTIFACT_TIER.get(row["kind"], "advanced"),
        "size": _file_size(row["file_path"]),
        "addedAt": _now(),  # ponytail: add created_at to run_artifacts when schema evolves
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

class BriefPointUpdate(BaseModel):
    id: str
    label: str
    description: str
    approved: bool = False
    included: bool = True
    order: int = 0

class BriefPointsBody(BaseModel):
    points: list[BriefPointUpdate]


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
        aid = str(uuid.uuid4())
        now = _now()
        conn.execute(
            """INSERT INTO assets (id, campaign_id, kind, filename, url, title, text, retrieved_at, file_path)
               VALUES (?,?,?,?,NULL,NULL,NULL,?,?)""",
            (aid, campaign_id, kind, filename, now, file_path)
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

        aid = str(uuid.uuid4())
        now = _now()
        conn.execute(
            """INSERT INTO assets (id, campaign_id, kind, filename, url, title, text, retrieved_at, file_path)
               VALUES (?,?,?,NULL,?,NULL,NULL,?,NULL)""",
            (aid, campaign_id, "article", url, now)
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
        result = _ser_run(row, conn)
        # Include brief_points when present (brief_pause and beyond).
        bp_rows = conn.execute(
            "SELECT * FROM brief_points WHERE run_id = ? ORDER BY sort_order", (run_id,)
        ).fetchall()
        if bp_rows:
            result["briefPoints"] = [_ser_brief_point(r) for r in bp_rows]
        return result
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

        # 409 if past brief phase: the run must currently be paused at brief_pause.
        # We detect this by checking that:
        # (a) the run is still running (not complete/failed), AND
        # (b) brief_points exist (adapter inserted them at brief_pause), AND
        # (c) the proceed event has NOT been set yet (run still blocked).
        # Simplest reliable check: run state == running, brief_points exist,
        # proceed event not set.
        if run["state"] not in ("running", "queued"):
            _409("Brief review is only open while the run is paused at the brief stage.")

        existing = conn.execute(
            "SELECT id FROM brief_points WHERE run_id = ?", (run_id,)
        ).fetchall()
        if not existing:
            _409("Brief review is only open while the run is paused at the brief stage.")

        proceed_event = adapter.get_proceed_event(run_id)
        if proceed_event.is_set():
            _409("Brief has already been approved and the run has continued.")

        points = body.points
        if not points:
            _422("At least one brief point is required.", "points")
        if not any(p.included for p in points):
            _422("Keep at least one idea included.", "points")

        # Bulk update: only update rows that belong to this run.
        existing_ids = {r["id"] for r in existing}
        for p in points:
            label = (p.label or "").strip()
            if not label:
                _422("Every idea needs a label.", "label")
            if p.id not in existing_ids:
                continue  # silently skip unknown IDs; they may be new additions in future
            conn.execute(
                """UPDATE brief_points
                   SET label = ?, description = ?, approved = ?, included = ?, sort_order = ?, edited = 1
                   WHERE id = ? AND run_id = ?""",
                (label, (p.description or "").strip(), int(p.approved),
                 int(p.included), p.order, p.id, run_id)
            )
        conn.commit()

        saved = conn.execute(
            "SELECT * FROM brief_points WHERE run_id = ? ORDER BY sort_order", (run_id,)
        ).fetchall()
        return [_ser_brief_point(r) for r in saved]
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

        if run["state"] not in ("running", "queued"):
            _409("This run is not waiting for review.")

        # brief_points must exist (inserted at brief_pause).
        bp = conn.execute(
            "SELECT * FROM brief_points WHERE run_id = ? ORDER BY sort_order", (run_id,)
        ).fetchall()
        if not bp:
            _409("This run is not waiting for review.")

        proceed_event = adapter.get_proceed_event(run_id)
        if proceed_event.is_set():
            _409("This run is not waiting for review.")

        # Verify at least one included point.
        if not any(r["included"] for r in bp):
            _422("Keep at least one idea included before continuing.", "points")

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
def get_artifact(run_id: str, artifact_id: str):
    conn = db.get_conn()
    try:
        art = conn.execute(
            "SELECT * FROM run_artifacts WHERE id = ? AND run_id = ?",
            (artifact_id, run_id)
        ).fetchone()
        if art is None:
            _404("Artifact not found.")
        path = art["file_path"]
        if not os.path.isfile(path):
            _404("Artifact file not found on disk.")
        filename = os.path.basename(path)
        return FileResponse(
            path,
            filename=filename,
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
