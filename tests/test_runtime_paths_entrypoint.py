"""`python -m src.runtime_paths` must actually run. Seventeen days of learning died because it did not.

The entry-point guard used to sit at line 219, seventeen lines ABOVE ``HOME_ENV = "I40_HOME"`` at
line 236. Running the module as a script therefore executed `_main()` before that constant existed,
and the first call into `_owned_dir` raised::

    File "src/runtime_paths.py", line 36, in _owned_dir
        home = os.environ.get(HOME_ENV)
    NameError: name 'HOME_ENV' is not defined

Importing the module normally ran the whole file first, so `HOME_ENV` was there and nothing looked
wrong from inside the app. Only the scheduled task took the broken path.

What it cost: `scripts/run_daily_learning.cmd` writes its start marker through this exact entry
point. The marker never refreshed, so every training run self-blocked as "outside the learning
window" with *"Nothing was trained and no model file was written"* - while the task exited 0 and
every freshness check saw `learning_decisions.json` being written on time. The live RF and LSTM
champions were last written on 18 September. The owner's standing rule is that self-learning runs
every day.

These tests run the module as a SUBPROCESS, the way the task does. Importing it would pass even with
the bug, which is precisely why nothing caught this.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _run(args, env_extra=None):
    import os
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env.update(env_extra or {})
    return subprocess.run([sys.executable, "-m", "src.runtime_paths", *args],
                          cwd=ROOT, capture_output=True, text=True, timeout=120, env=env)


def test_the_module_runs_as_a_script_at_all(tmp_path):
    """The regression. Exit 0, and no NameError anywhere in the output."""
    done = _run(["--mark-task", "pytest marker"], {"SMARTENTRY_DATA_DIR": str(tmp_path)})
    combined = done.stdout + done.stderr
    assert "NameError" not in combined, combined[-800:]
    assert "HOME_ENV" not in done.stderr, done.stderr[-800:]
    assert done.returncode == 0, combined[-800:]


def test_the_start_marker_the_learning_gate_depends_on_is_written(tmp_path):
    """The marker is the whole point: without a fresh one, training refuses itself."""
    done = _run(["--mark-task", "SmartEntry Daily Learning"], {"SMARTENTRY_DATA_DIR": str(tmp_path)})
    assert done.returncode == 0, (done.stdout + done.stderr)[-800:]
    marker = tmp_path / "learning" / "daily_learning_task_marker.json"
    assert marker.exists(), f"no marker written; stdout was {done.stdout!r}"
    record = json.loads(marker.read_text(encoding="utf-8"))
    assert record["task"] == "SmartEntry Daily Learning"
    assert record["started_utc"] and record["pid"]


def test_running_with_no_arguments_also_works(tmp_path):
    """It prints the window and the gate. That path reaches the same constants."""
    done = _run([], {"SMARTENTRY_DATA_DIR": str(tmp_path)})
    assert "NameError" not in (done.stdout + done.stderr)
    assert done.returncode == 0
    assert "window" in done.stdout and "gate" in done.stdout


def test_every_module_level_name_is_defined_before_the_entry_point():
    """The structural guard: the __main__ block must be the LAST thing in the file.

    Anything defined after it is invisible to `_main()`, which is the whole bug. A line-number check
    is cruder than running it, but it fails loudly the moment somebody moves the guard back up.
    """
    source = (ROOT / "src" / "runtime_paths.py").read_text(encoding="utf-8")
    lines = source.splitlines()
    guard = next(i for i, line in enumerate(lines) if line.startswith('if __name__ == "__main__"'))
    after = [line for line in lines[guard + 1:]
             if line.strip() and not line.startswith((" ", "\t", "#"))]
    assert after == [], (
        f"these module-level statements come AFTER the __main__ guard and are invisible to _main(): {after}")


def test_the_gate_still_refuses_outside_the_window(monkeypatch):
    """Fixing the entry point must not have opened the training gate.

    The gate needs BOTH the window AND a fresh marker. Writing a marker must not let a retrain happen
    outside 04:25-05:30 UTC, or the fix would have swapped a silent failure for a live one.

    Both sandbox escapes are cleared first. `live_training_allowed` short-circuits to True whenever
    SMARTENTRY_MODELS_DIR is redirected, which is the safety that stops the test suite overwriting the
    live champions - and which tests/conftest.py sets on every run. Testing the real gate means
    removing it, which is also a reminder of how little stands between a stray retrain and the live
    models when that variable is NOT set.
    """
    sys.path.insert(0, str(ROOT))
    from datetime import datetime, timezone
    from src import runtime_paths as rp

    monkeypatch.delenv(rp.MODELS_DIR_ENV, raising=False)
    monkeypatch.delenv(rp.OFFSCHEDULE_TRAINING_ENV, raising=False)

    noon = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
    assert rp.inside_learning_window(noon) is False
    allowed, reason = rp.live_training_allowed(noon)
    assert allowed is False, reason
    assert "outside the learning window" in reason
    assert "Nothing was trained" in reason


def test_the_two_sandbox_escapes_are_the_only_ways_past_the_gate(monkeypatch):
    """Both are deliberate and both must stay explicit: a redirected models folder, or the owner's
    own override. Nothing else may open it."""
    sys.path.insert(0, str(ROOT))
    from datetime import datetime, timezone
    from src import runtime_paths as rp

    noon = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)

    monkeypatch.delenv(rp.OFFSCHEDULE_TRAINING_ENV, raising=False)
    monkeypatch.setenv(rp.MODELS_DIR_ENV, "some/sandbox")
    allowed, reason = rp.live_training_allowed(noon)
    assert allowed is True and "redirected away from the live champions" in reason

    monkeypatch.delenv(rp.MODELS_DIR_ENV, raising=False)
    monkeypatch.setenv(rp.OFFSCHEDULE_TRAINING_ENV, "1")
    allowed, reason = rp.live_training_allowed(noon)
    assert allowed is True and "owner override" in reason
