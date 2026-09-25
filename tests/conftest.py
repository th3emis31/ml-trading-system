"""Shared pytest fixtures, and isolation of the live models and learning records.

From 2026-09-15 18:38:59 until 2026-09-16 some tests (test_lstm.py, test_pipeline.py, test_ensemble_integration.py)
trained straight into the live models/ folder and wrote data/learning_decisions.json and the per-symbol learning
history, replacing the champions behind /api/signals. Every path those modules use now comes from
src/runtime_paths.py, and this file points it at a temporary folder BEFORE any src module is imported.
A session guard then fails the whole run if any live model or learning file changed anyway.
"""
import hashlib
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_SANDBOX_PREFIX = "smartentry_tests_"
# A sandbox from a run that never finished - Ctrl-C, a crash, a killed process - can never be removed
# by that run, so each new run sweeps the old ones. Six hours, not "all of them", because the deep
# doctor's 06:30 suite and a manual run can overlap and must not delete each other's working folder.
#
# This is not housekeeping for its own sake. The sandbox is a ~3.1 GB copy of data/ and models/, the
# suite runs at least daily, and nothing removed them: by 25 September 2026 there were 89 of them
# holding 225 GB, and the C: drive was down to 1.59 GB free - enough to stop the live app writing its
# own state. The leak was silent because a test suite that passes is never looked at.
_STALE_AFTER_SECONDS = 6 * 3600


def _sweep_stale_sandboxes() -> None:
    root = Path(tempfile.gettempdir())
    cutoff = time.time() - _STALE_AFTER_SECONDS
    for old in root.glob(_SANDBOX_PREFIX + "*"):
        try:
            if old.is_dir() and old.stat().st_mtime < cutoff:
                shutil.rmtree(old, ignore_errors=True)
        except OSError:
            pass                    # a folder another run holds open is simply left alone


_sweep_stale_sandboxes()
_ISOLATED = Path(tempfile.mkdtemp(prefix=_SANDBOX_PREFIX))
os.environ["SMARTENTRY_MODELS_DIR"] = str(_ISOLATED / "models")
os.environ["SMARTENTRY_DATA_DIR"] = str(_ISOLATED / "data")
(_ISOLATED / "models").mkdir(parents=True, exist_ok=True)
# The app reads a lot from data/ (state, signals, strategy lab), so tests get a copy of it; audio and logs are skipped.
#
# ``*.tmp`` matters: the Strategy Lab writes data/strategy_lab/registry.tmp and renames it over the
# registry, so the file exists when copytree lists the directory and is gone a moment later when it
# tries to read it. That raced three times on 21 September 2026 and took the whole suite down with an
# ImportError in this file - and since the hourly search now uses its full 32-minute slot, it would
# have raced most of every hour. Temporary files are not test fixtures, so they are skipped.
#
# ``_copy_data`` also retries once: any other file the running system happens to replace mid-copy is
# a transient, not a reason to fail every test.
def _copy_data() -> None:
    patterns = shutil.ignore_patterns("voice_uploads", "*.wav", "*.log", "*.bak_*", "*.tmp", "*.lock")
    for attempt in (1, 2):
        try:
            shutil.copytree(ROOT / "data", _ISOLATED / "data", dirs_exist_ok=True, ignore=patterns)
            return
        except shutil.Error:
            if attempt == 2:
                raise


_copy_data()
# Tests that load an existing champion get a copy of the live one, never the live file itself.
for _live_model in (ROOT / "models").glob("*"):
    if _live_model.is_file():
        shutil.copy2(_live_model, _ISOLATED / "models" / _live_model.name)

import pandas as pd  # noqa: E402
import pytest  # noqa: E402

from src.data import generate_synthetic_data  # noqa: E402


def _live_learning_fingerprint() -> dict:
    """SHA-256 of every live model file and learning record a test could have overwritten."""
    files = [p for p in (ROOT / "models").glob("*") if p.is_file()]
    # signals.json is left out on purpose: the running app appends to it whenever the live signal history updates,
    # so it can change during a test run without any test touching it (tests write their own copy via DATA_DIR).
    files += [ROOT / "data" / name for name in ("learning_decisions.json", "model_restores.json", "settings.json",
                                                 "knowledge.json")]
    files += list((ROOT / "data").glob("*_daily_history.json"))
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in files if p.is_file()}


_LIVE_AT_START = _live_learning_fingerprint()


def pytest_sessionfinish(session, exitstatus):
    """Check nothing escaped isolation, then remove this run's sandbox.

    The removal is last and never raises: a file TensorFlow still holds open is a reason to leave the
    folder for the next run's sweep, not a reason to fail a suite that passed.
    """
    after = _live_learning_fingerprint()
    changed = sorted(k for k in set(_LIVE_AT_START) | set(after) if _LIVE_AT_START.get(k) != after.get(k))
    if changed:
        print("\nLIVE MODEL OR LEARNING FILES CHANGED DURING THE TEST RUN (a test escaped isolation):")
        for name in changed:
            print("  ", name)
        session.exitstatus = 1

    # The sandbox holds nothing unique - it is a copy of data/ and models/ - so it is always removed,
    # pass or fail. What a failed run needs is its path printed, not 3.1 GB left on the disk.
    try:
        megabytes = sum(f.stat().st_size for f in _ISOLATED.rglob("*") if f.is_file()) / (1024 * 1024)
    except OSError:
        megabytes = 0.0
    shutil.rmtree(_ISOLATED, ignore_errors=True)
    if _ISOLATED.exists():
        print(f"\nsandbox could not be removed (a file is still open): {_ISOLATED}")
    else:
        print(f"\nsandbox removed, {megabytes:,.0f} MB reclaimed")


@pytest.fixture(scope="module")
def bars():
    """3,000 synthetic 4h gold bars from 2021 (Strategy Lab and strategy book tests)."""
    frame = generate_synthetic_data("XAUUSD", start_date="2020-01-01", end_date="2024-12-31", n=3000).reset_index(drop=True)
    if "volume" not in frame.columns:
        frame["volume"] = 0.0
    frame["datetime"] = pd.date_range("2021-01-01", periods=len(frame), freq="4h", tz="UTC")
    return frame
