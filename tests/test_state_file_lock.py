"""A locked state file must never be mistaken for a corrupt one.

On 28 September 2026 `data/auto_trader_state.json` was quarantined as corrupt and the live file was
lost. The quarantined copy parses as perfectly valid JSON, so nothing was ever damaged. The loader
collapsed an OSError and a parse error into the same `None`, and the caller read any `None` as
"corrupt" and moved the live file aside. A Windows file lock on READ, the same WinError 32 class
already fixed on the write side, was enough to destroy it.

`_read_state_json` is read out of app.py rather than imported, because importing app.py starts the
Flask application, the MetaTrader connections and the scheduler. The function has no dependency
beyond json and Path, so slicing it out tests the real code without any of that.
"""
import json
import msvcrt
import tempfile
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / "app.py"


def _loader():
    src = APP.read_text(encoding="utf-8")
    start = src.index("def _read_state_json")
    end = src.index("def load_auto_trader_state")
    namespace = {"Path": Path, "json": json}
    exec(src[start:end], namespace)
    return namespace["_read_state_json"]


@pytest.fixture(scope="module")
def read():
    return _loader()


@pytest.fixture(scope="module")
def tmp():
    return Path(tempfile.mkdtemp(prefix="state_lock_"))


def test_a_valid_file_reads_back(read, tmp):
    good = tmp / "ok.json"
    good.write_text('{"session": {"a": 1}}', encoding="utf-8")
    value, why = read(good, detail=True)
    assert why == "ok" and value == {"session": {"a": 1}}


def test_a_truncated_file_is_corrupt(read, tmp):
    bad = tmp / "bad.json"
    bad.write_text('{"session": {"a": 1}', encoding="utf-8")
    value, why = read(bad, detail=True)
    assert why == "corrupt" and value is None


def test_a_missing_file_is_missing_not_corrupt(read, tmp):
    value, why = read(tmp / "nothing-here.json", detail=True)
    assert why == "missing" and value is None


def test_a_LOCKED_but_valid_file_is_unreadable_never_corrupt(read, tmp):
    """The one that cost the real file.

    The content is perfect JSON. Only the lock stops it being opened, and a file that cannot be
    opened right now is not a damaged file. If this ever returns "corrupt" again, the caller will
    quarantine a live state file for the second time.
    """
    locked = tmp / "locked.json"
    locked.write_text('{"session": {"a": 1}}', encoding="utf-8")
    handle = open(locked, "r+", encoding="utf-8")
    try:
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        value, why = read(locked, detail=True)
    finally:
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        handle.close()
    assert why == "unreadable", "a locked file must never be reported as corrupt"
    assert value is None
    # and it is still intact afterwards, which is the whole point
    assert json.loads(locked.read_text(encoding="utf-8")) == {"session": {"a": 1}}


def test_the_default_call_shape_is_unchanged(read, tmp):
    """Existing callers pass no detail flag and must still get the bare value."""
    good = tmp / "plain.json"
    good.write_text('{"x": 1}', encoding="utf-8")
    assert read(good) == {"x": 1}
    assert read(tmp / "absent.json") is None
