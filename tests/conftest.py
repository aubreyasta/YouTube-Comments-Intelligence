"""
Lets one `pytest tests/` run cover every file.

Each test file points db._DB_PATH, storage._ROOT, or storage.uploads_dir at
its own temp dir at import. pytest imports every file before running any, so
the last import would win for all of them. Record what each file sets while
it imports, from the untouched defaults, and put exactly that back before
the file's tests run. `python tests/<file>.py` never loads this file.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db  # noqa: E402
import storage  # noqa: E402

_SEAMS = [(db, "_DB_PATH"), (storage, "_ROOT"), (storage, "uploads_dir")]
_DEFAULTS = [getattr(mod, name) for mod, name in _SEAMS]
_per_file = {}


def _apply(values):
    for (mod, name), value in zip(_SEAMS, values):
        setattr(mod, name, value)


def pytest_collectstart(collector):
    if isinstance(collector, pytest.Module):
        _apply(_DEFAULTS)


def pytest_collectreport(report):
    if report.nodeid.endswith(".py"):
        _per_file[report.nodeid] = [getattr(mod, name) for mod, name in _SEAMS]


@pytest.fixture(autouse=True, scope="module")
def _file_seams(request):
    _apply(_per_file.get(request.node.nodeid, _DEFAULTS))
