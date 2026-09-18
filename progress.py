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
# Queue order: claim_next() starts the first run by it, and queuePosition
# counts the runs ahead by it.
_QUEUE_KEY = "started_at, id"

# Wake-ups for runs blocked in await_review. The run thread and the server
# share one process, and a restart fails every running run (fail_orphans), so
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


def claim_next() -> str | None:
    """Move the oldest queued run to running, unless a run is already
    running. BEGIN IMMEDIATE takes the write lock before the check, so a new
    start and a finishing run calling this at once never both claim: one GPU,
    at most one running run. Returns the claimed run id, or None."""
    conn = db.get_conn()
    try:
        conn.isolation_level = None
        conn.execute("BEGIN IMMEDIATE")
        row = None
        if conn.execute("SELECT 1 FROM runs WHERE state = 'running'").fetchone() is None:
            row = conn.execute("SELECT id FROM runs WHERE state = 'queued' "
                               f"ORDER BY {_QUEUE_KEY} LIMIT 1").fetchone()
            if row is not None:
                conn.execute("UPDATE runs SET state = 'running', started_at = ? WHERE id = ?",
                             (_now(), row["id"]))
        conn.execute("COMMIT")
        return row["id"] if row else None
    finally:
        conn.close()


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
    return row["state"] == "running" and row["stage"] == "brief_pause"


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
    """Run threads die with the process, so a running row at startup has no
    worker behind it and would hold the queue forever. Queued rows have no
    worker yet either, so they survive and the next claim picks them up."""
    conn = db.get_conn()
    try:
        conn.execute(
            "UPDATE runs SET state = 'failed', stage = 'error', finished_at = ?, error = ? "
            "WHERE state = 'running'",
            (_now(), "Interrupted: the server restarted before this run finished."))
        conn.commit()
    finally:
        conn.close()


def read(row, conn) -> dict:
    """The snapshot for a runs row. The terminal state wins over the stored
    stage, since a crash can leave a stale non-terminal stage behind.
    queuePosition is 1 for the next run to start, None once it has."""
    state = row["state"]
    stage = ("complete" if state == "complete"
             else "error" if state == "failed"
             else row["stage"] or "queued")
    stored = json.loads(row["progress"] or "{}")
    position = None
    if state == "queued":
        position = conn.execute(
            f"SELECT COUNT(*) FROM runs WHERE state = 'queued' AND ({_QUEUE_KEY}) < (?, ?)",
            (row["started_at"], row["id"])).fetchone()[0] + 1
    return {
        "stage": stage,
        "pct": 100 if state == "complete" else stored.get("pct", 0),
        "message": stored.get("message", ""),
        "counts": stored.get("counts", {}),
        "error": row["error"],
        "queuePosition": position,
    }


def load(run_id: str) -> dict | None:
    conn = db.get_conn()
    try:
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return read(row, conn) if row else None
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
    for rid, at in (("r", "1"), ("q2", "2"), ("q3", "3")):
        c.execute("INSERT INTO runs (id, session_id, started_at) VALUES (?, 's', ?)", (rid, at))
    c.commit()
    c.close()

    assert [load(r)["queuePosition"] for r in ("r", "q2", "q3")] == [1, 2, 3]
    assert claim_next() == "r" and claim_next() is None, "claimed a second running run"
    assert load("r")["queuePosition"] is None and load("q3")["queuePosition"] == 2
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
    assert claim_next() == "q2", "a finished run did not free the slot"
    fail_orphans()
    assert load("q2")["stage"] == "error" and load("q3")["queuePosition"] == 1
    print("progress self-check ok")
