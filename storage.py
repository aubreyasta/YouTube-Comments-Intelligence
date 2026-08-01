"""storage.py - path helpers and file writes for local storage.

All paths are relative to this file's directory (repo root).
Each helper creates its target directory on demand.
"""

import os
import shutil

_ROOT = os.path.dirname(os.path.abspath(__file__))


def _ensure(path: str) -> str:
    """Create directory at path if missing, return path."""
    os.makedirs(path, exist_ok=True)
    return path


def uploads_dir() -> str:
    """Return (and create) data/uploads/ absolute path."""
    return _ensure(os.path.join(_ROOT, "data", "uploads"))


def run_dir(run_id: str) -> str:
    """Return (and create) data/runs/{run_id}/ absolute path."""
    return _ensure(os.path.join(_ROOT, "data", "runs", run_id))


def artifacts_dir(run_id: str) -> str:
    """Return (and create) data/artifacts/{run_id}/ absolute path."""
    return _ensure(os.path.join(_ROOT, "data", "artifacts", run_id))


def clear_run(run_id: str) -> None:
    """Delete all files for a run. Missing dirs are silently ignored."""
    for d in (run_dir(run_id), artifacts_dir(run_id)):
        shutil.rmtree(d, ignore_errors=True)


def save_upload(filename: str, data: bytes) -> str:
    """Write data to data/uploads/{filename} and return its absolute path."""
    dest = os.path.join(uploads_dir(), filename)
    with open(dest, "wb") as f:
        f.write(data)
    return dest
