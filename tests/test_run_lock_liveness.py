"""A lock held by a process that no longer exists must not block the next run.

This machine has 7.4 GB of RAM and routinely sits around 86 % used, so a long job can be killed
outright by the OS. On 19 September 2026 a re-score was killed that way. Its status file still said
"state": "running" with a heartbeat seconds old, so the lock kept the next run out for the rest of the
HEARTBEAT_STALE_SECONDS window even though nothing was running. The registry itself was fine - it is
written to a .tmp and os.replace'd, so a kill mid-write leaves the previous copy intact.
"""
import os

import pandas as pd
import pytest

from src import strategy_lab as lab


def _status(pid, *, age_seconds=1.0, state="running"):
    return {"state": state, "pid": pid,
            "heartbeat": lab._iso(pd.Timestamp.now(tz="UTC") - pd.Timedelta(seconds=age_seconds))}


def test_this_process_is_seen_as_alive():
    assert lab._process_is_alive(os.getpid()) is True


def test_a_pid_that_no_longer_exists_is_seen_as_dead():
    """Find a pid nothing owns rather than hard-coding one."""
    for pid in range(60000, 65000, 7):
        if lab._process_is_alive(pid) is False:
            return
    pytest.skip("no free pid found to test with")


def test_an_undeterminable_pid_never_frees_the_lock():
    """Any doubt must keep the lock: freeing one wrongly would run two searches at once."""
    for value in (None, "", "abc", 0, -1, {}):
        assert lab._process_is_alive(value) is None
    assert lab._run_is_active(_status(None)) is True, "an unknown owner keeps the lock"


def test_a_fresh_heartbeat_from_a_live_process_holds_the_lock():
    assert lab._run_is_active(_status(os.getpid())) is True


def test_a_fresh_heartbeat_from_a_dead_process_does_not():
    dead = next((p for p in range(60000, 65000, 7) if lab._process_is_alive(p) is False), None)
    if dead is None:
        pytest.skip("no free pid found to test with")
    assert lab._run_is_active(_status(dead)) is False


def test_a_stale_heartbeat_still_frees_the_lock_on_its_own():
    """The original rule is unchanged; the pid check only makes it act sooner."""
    assert lab._run_is_active(_status(os.getpid(), age_seconds=lab.HEARTBEAT_STALE_SECONDS + 1)) is False


def test_a_finished_run_never_holds_the_lock():
    assert lab._run_is_active(_status(os.getpid(), state="done")) is False


def test_the_registry_write_is_atomic_so_a_kill_cannot_corrupt_it(tmp_path):
    path = tmp_path / "registry.json"
    lab.save_registry({"version": 1, "markets": {}, "candidates": {}}, path)
    before = path.read_text(encoding="utf-8")
    assert not path.with_suffix(".tmp").exists(), "the temporary file must be replaced, not left behind"
    assert '"version"' in before
