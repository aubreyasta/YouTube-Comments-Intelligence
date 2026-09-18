"""
progress.py - the Run progress snapshot: one persisted record per run.

The run thread writes it; GET /runs/{id} and the SSE stream read it through
read(). Nothing else knows the stage vocabulary, the counts, or how a run
waits at brief_pause. Every reader sees the same record, so a reopened tab,
a second tab, and a live stream cannot disagree.

Snapshot (camelCase, the same object over GET and SSE):
  {"stage": str, "pct": int, "message": str,
   "counts": {"total", "labelled", "themes", "batch", "batches", "otherShare"},
   "error": str | None}
"""

import json
import re
import threading
from datetime import datetime, timezone

import db

# Run order. The frontend maps these to its steps; brief_pause is the one
# point a run waits for a person.
STAGES = ("queued", "collect", "brief", "brief_pause", "themes", "classify",
          "emotion", "report", "complete", "error")
TERMINAL = ("complete", "error")
_ACTIVE_STATES = ("queued", "running")

# Wake-ups for runs blocked in await_review. The run thread and the server
# share one process, and a restart fails every active run (fail_orphans), so
# an in-memory event never outlives the run it wakes.
_review_events: dict[str, threading.Event] = {}
_lock = threading.Lock()


class NotPaused(Exception):
    """proceed() on a run that is not waiting at brief_pause."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _camel(key: str) -> str:
    return re.sub(r"_(\w)", lambda m: m.group(1).upper(), key)


def _write(run_id: str, sql: str, params: tuple) -> int:
    conn = db.get_conn()
    try:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def start(run_id: str) -> None:
    _write(run_id, "UPDATE runs SET state = 'running', started_at = ? WHERE id = ?",
           (_now(), run_id))


def publish(run_id: str, stage: str, message: str, pct: int, **counts) -> None:
    """Set the stage and merge counts into the stored ones, so a later stage
    never erases a count an earlier one set. A run deleted mid-flight by a
    later overwrite updates nothing, which is fine."""
    assert stage in STAGES and stage not in TERMINAL, f"bad progress stage {stage!r}"
    patch = {"pct": pct, "message": message,
             "counts": {_camel(k): v for k, v in counts.items()}}
    _write(run_id,
           "UPDATE runs SET stage = ?, progress = json_patch(COALESCE(progress, '{}'), ?) "
           "WHERE id = ?",
           (stage, json.dumps(patch), run_id))


def await_review(run_id: str, skip: bool) -> None:
    """Pause at brief_pause until proceed(), unless the user asked to skip.
    The event exists before the stage is written, so a proceed() that lands
    right after the write always finds something to wake."""
    if skip:
        publish(run_id, "brief", "Brief ready - skipping review", 40)
        return
    event = threading.Event()
    with _lock:
        _review_events[run_id] = event
    publish(run_id, "brief_pause", "Brief ready for review. Waiting for approval.", 40)
    try:
        event.wait()
    finally:
        with _lock:
            _review_events.pop(run_id, None)


def proceed(run_id: str) -> None:
    """Leave brief_pause. The conditional UPDATE is the check: exactly one
    caller moves the stage on, and every later one gets NotPaused."""
    changed = _write(
        run_id,
        "UPDATE runs SET stage = 'themes', "
        "progress = json_patch(COALESCE(progress, '{}'), ?) "
        "WHERE id = ? AND stage = 'brief_pause' AND state IN ('queued', 'running')",
        (json.dumps({"message": "Review confirmed"}), run_id))
    if not changed:
        raise NotPaused(run_id)
    with _lock:
        event = _review_events.get(run_id)
    if event is not None:
        event.set()


def is_paused(row) -> bool:
    return row["state"] in _ACTIVE_STATES and row["stage"] == "brief_pause"


def finish(run_id: str, error: str | None = None) -> None:
    state, stage = ("failed", "error") if error else ("complete", "complete")
    patch = {"message": "Run failed" if error else "Run complete"}
    if not error:
        patch["pct"] = 100
    _write(run_id,
           "UPDATE runs SET state = ?, stage = ?, finished_at = ?, error = ?, "
           "progress = json_patch(COALESCE(progress, '{}'), ?) WHERE id = ?",
           (state, stage, _now(), error, json.dumps(patch), run_id))


def fail_orphans() -> None:
    """Run threads die with the process, so an active row at startup has no
    worker behind it and would block every new run forever."""
    conn = db.get_conn()
    try:
        conn.execute(
            "UPDATE runs SET state = 'failed', stage = 'error', finished_at = ?, error = ? "
            "WHERE state IN ('queued', 'running')",
            (_now(), "Interrupted: the server restarted before this run finished."))
        conn.commit()
    finally:
        conn.close()


def read(row) -> dict:
    """The snapshot for a runs row. The terminal state wins over the stored
    stage, since a crash can leave a stale non-terminal stage behind."""
    state = row["state"]
    stage = ("complete" if state == "complete"
             else "error" if state == "failed"
             else row["stage"] or "queued")
    stored = json.loads(row["progress"] or "{}")
    return {
        "stage": stage,
        "pct": 100 if state == "complete" else stored.get("pct", 0),
        "message": stored.get("message", ""),
        "counts": stored.get("counts", {}),
        "error": row["error"],
    }


def load(run_id: str) -> dict | None:
    conn = db.get_conn()
    try:
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return read(row) if row else None
    finally:
        conn.close()


if __name__ == "__main__":
    import pathlib
    import tempfile
    import time

    db._DB_PATH = pathlib.Path(tempfile.mkdtemp()) / "app.db"
    db.init()
    c = db.get_conn()
    c.execute("INSERT INTO sessions (id, name, created_at, updated_at) VALUES ('s', 's', '', '')")
    c.execute("INSERT INTO runs (id, session_id) VALUES ('r', 's')")
    c.commit()
    c.close()

    start("r")
    publish("r", "collect", "Collected", 20, total=3)
    publish("r", "classify", "Batch 1", 55, labelled=2, themes=2, other_share=1.5)
    snap = load("r")
    assert snap["counts"] == {"total": 3, "labelled": 2, "themes": 2, "otherShare": 1.5}, snap
    assert snap["stage"] == "classify" and snap["pct"] == 55

    try:
        proceed("r")
        raise AssertionError("proceed outside brief_pause must raise")
    except NotPaused:
        pass

    t = threading.Thread(target=await_review, args=("r", False))
    t.start()
    while load("r")["stage"] != "brief_pause":
        time.sleep(0.01)
    proceed("r")
    t.join(2)
    assert not t.is_alive() and load("r")["stage"] == "themes"

    finish("r", "boom")
    snap = load("r")
    assert snap["stage"] == "error" and snap["error"] == "boom" and snap["counts"]["total"] == 3
    print("progress self-check ok")
