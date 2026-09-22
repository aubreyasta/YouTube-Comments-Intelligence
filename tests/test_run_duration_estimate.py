"""
Offline self-check for issue #62: the Run page duration estimate.

adapter._estimate_duration_seconds() samples completed runs' wall-clock
duration and comment count to derive a (low, high) seconds range for a
new run of a given size, scoped to runs that used the same model and
batch size. Below _ESTIMATE_MIN_SAMPLES matches, or zero comments, it
returns None rather than guess - app/app.js only shows the sub-line
estimate when both bounds are present.

Points db._DB_PATH at a temp location before importing adapter, then
seeds `runs` rows directly by SQL (no server, no pipeline). Each test
uses its own model name, so tests can run in any order. Restored on
exit so this file never touches the repo's real data/ tree.

Run: python tests/test_run_duration_estimate.py
"""

import os
import pathlib
import sys
import tempfile
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
_ORIG_DB_PATH = db._DB_PATH
db._DB_PATH = pathlib.Path(tempfile.mkdtemp()) / "app.db"
db.init()

import adapter

_SESSION_ID = str(uuid.uuid4())
conn = db.get_conn()
try:
    conn.execute(
        "INSERT INTO sessions (id, name, created_at, updated_at) VALUES (?, 'S', '', '')",
        (_SESSION_ID,))
    conn.commit()
finally:
    conn.close()


def _seed_completed_run(started_at, finished_at, total, model, batch_size):
    conn = db.get_conn()
    try:
        conn.execute(
            "INSERT INTO runs (id, session_id, state, stage, started_at, finished_at, "
            "progress, llm_model, classify_batch_size) "
            "VALUES (?, ?, 'complete', 'complete', ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), _SESSION_ID, started_at, finished_at,
             f'{{"counts": {{"total": {total}}}}}', model, batch_size))
        conn.commit()
    finally:
        conn.close()


def test_below_min_samples_returns_none():
    for _ in range(adapter._ESTIMATE_MIN_SAMPLES - 1):
        _seed_completed_run("2026-09-01T00:00:00+00:00", "2026-09-01T00:10:00+00:00",
                            100, "model-sparse", 8)
    assert adapter._estimate_duration_seconds("model-sparse", 8, 200) is None


def test_enough_samples_gives_observed_range():
    # Three completed runs at three different rates: 6, 3, and 4.5 s/comment.
    _seed_completed_run("2026-09-01T00:00:00+00:00", "2026-09-01T00:10:00+00:00",
                        100, "model-a", 8)  # 6 s/comment
    _seed_completed_run("2026-09-01T00:00:00+00:00", "2026-09-01T00:05:00+00:00",
                        100, "model-a", 8)  # 3 s/comment
    _seed_completed_run("2026-09-01T00:00:00+00:00", "2026-09-01T00:07:30+00:00",
                        100, "model-a", 8)  # 4.5 s/comment
    low, high = adapter._estimate_duration_seconds("model-a", 8, 200)
    assert low == 200 * 3, f"low={low}"    # fastest observed rate
    assert high == 200 * 6, f"high={high}"  # slowest observed rate


def test_different_model_or_batch_size_excluded():
    _seed_completed_run("2026-09-01T00:00:00+00:00", "2026-09-01T00:05:00+00:00",
                        100, "model-b", 8)
    _seed_completed_run("2026-09-01T00:00:00+00:00", "2026-09-01T00:05:00+00:00",
                        100, "model-b", 8)
    _seed_completed_run("2026-09-01T00:00:00+00:00", "2026-09-01T00:05:00+00:00",
                        100, "model-b", 8)
    assert adapter._estimate_duration_seconds("model-b", 16, 200) is None, \
        "a different batch size must not count towards model-b's sample"


def test_zero_comments_returns_none():
    assert adapter._estimate_duration_seconds("model-a", 8, 0) is None


def test_record_run_config_persists_model_and_batch_size():
    rid = str(uuid.uuid4())
    conn = db.get_conn()
    try:
        conn.execute(
            "INSERT INTO runs (id, session_id, state, stage) VALUES (?, ?, 'running', 'collect')",
            (rid, _SESSION_ID))
        conn.commit()
    finally:
        conn.close()
    adapter._record_run_config(rid, "model-c", 12)
    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT llm_model, classify_batch_size FROM runs WHERE id = ?", (rid,)).fetchone()
    finally:
        conn.close()
    assert row["llm_model"] == "model-c" and row["classify_batch_size"] == 12, dict(row)


def main() -> None:
    tests = [
        test_below_min_samples_returns_none,
        test_enough_samples_gives_observed_range,
        test_different_model_or_batch_size_excluded,
        test_zero_comments_returns_none,
        test_record_run_config_persists_model_and_batch_size,
    ]
    failed = 0
    try:
        for t in tests:
            try:
                t()
            except AssertionError as exc:
                print(f"  FAIL {t.__name__}: {exc}")
                failed += 1
            except Exception as exc:
                print(f"  ERROR {t.__name__}: {type(exc).__name__}: {exc}")
                failed += 1
    finally:
        db._DB_PATH = _ORIG_DB_PATH

    if failed:
        print(f"\nFAIL ({failed}/{len(tests)} failed)")
        sys.exit(1)
    print(f"\nPASS ({len(tests)}/{len(tests)})")


if __name__ == "__main__":
    main()
