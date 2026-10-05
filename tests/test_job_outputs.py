"""A scheduled task exiting 0 is not evidence its work happened.

`check_scheduled_tasks` asks whether a task exists, what it exited with, and which folder it runs
from. It never asks whether the job PRODUCED anything, and every silent failure of the week of
29 September 2026 fell through that gap:

* The System Doctor stopped writing for FOURTEEN HOURS after its own ``--fix`` launched
  start_trading.bat, which inherited the redirected log handle and locked it. The task still existed
  and still ran on schedule. Every page read the frozen report and showed a dead app.
* ``strategy_book update`` crashed on every hourly run for days. It runs inside the Strategy Lab
  task, whose lab half succeeded, so the task exited 0 and nothing said the book was not rebuilt.
* A half-finished rescore then reported "1 passed the locked holdout" when none had.

These tests reconstruct those three situations and require the check to speak.
"""
import datetime
import json
import time
from pathlib import Path

import pytest

from src import system_doctor as doc

MINUTE = 60.0


@pytest.fixture
def watched(tmp_path, monkeypatch):
    """A watch table pointing at a temp tree, so nothing here reads the live system."""
    monkeypatch.setattr(doc, "ROOT", tmp_path)
    table = (
        {"job": "SmartEntry System Doctor", "every": "30 min", "max_age_min": 90,
         "path": "data/system_health/doctor_latest.json", "why": "the report every page reads"},
        {"job": "SmartEntry Strategy Lab (book half)", "every": "hourly", "max_age_min": 240,
         "path": "data/strategy_lab/strategy_book.json", "why": "crashed hourly while the task exited 0"},
    )
    for item in table:
        target = tmp_path / item["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{}", encoding="utf-8")
    return table


def _age(tmp_path, rel, minutes):
    target = tmp_path / rel
    old = time.time() - minutes * MINUTE
    import os
    os.utime(target, (old, old))


def test_everything_fresh_is_ok(watched, tmp_path):
    out = doc.check_job_outputs(watched=watched)
    assert out["status"] == "ok"
    assert out["detail"]["stale"] == [] and out["detail"]["missing"] == []
    assert all(w["state"] == "fresh" for w in out["detail"]["watched"])


def test_the_doctor_freezing_for_fourteen_hours_is_caught(watched, tmp_path):
    """The 1 October failure, to the hour. The task ran; the report stopped moving."""
    _age(tmp_path, "data/system_health/doctor_latest.json", 14 * 60)
    out = doc.check_job_outputs(watched=watched)
    assert out["status"] == "warn"
    assert "SmartEntry System Doctor" in out["summary"]
    assert "report success but have stopped producing" in out["summary"]
    row = next(w for w in out["detail"]["watched"] if w["job"] == "SmartEntry System Doctor")
    assert row["state"] == "stale" and row["age_min"] == pytest.approx(840, abs=1)


def test_the_book_crashing_while_its_task_exits_zero_is_caught(watched, tmp_path):
    """The one nothing could see. The Strategy Lab task succeeded; the book half died every hour."""
    _age(tmp_path, "data/strategy_lab/strategy_book.json", 3 * 24 * 60)
    out = doc.check_job_outputs(watched=watched)
    assert out["status"] == "warn"
    assert "book half" in out["summary"]


def test_a_job_that_has_never_written_is_reported_as_missing_not_fresh(watched, tmp_path):
    (tmp_path / "data/strategy_lab/strategy_book.json").unlink()
    out = doc.check_job_outputs(watched=watched)
    assert out["status"] == "warn"
    assert "Nothing has been written by" in out["summary"]
    assert out["detail"]["missing"] == ["SmartEntry Strategy Lab (book half)"]
    row = next(w for w in out["detail"]["watched"] if "book half" in w["job"])
    assert row["state"] == "missing" and row["age_min"] is None


def test_ordinary_lateness_stays_silent(watched, tmp_path):
    """A check that cries wolf gets ignored, which is worse than not having it.

    Every limit is at least twice the interval, so a job that is merely late says nothing.
    """
    _age(tmp_path, "data/system_health/doctor_latest.json", 61)      # ran late, limit is 90
    _age(tmp_path, "data/strategy_lab/strategy_book.json", 180)      # ran late, limit is 240
    assert doc.check_job_outputs(watched=watched)["status"] == "ok"


def test_both_a_missing_and_a_stale_job_are_reported_together(watched, tmp_path):
    (tmp_path / "data/system_health/doctor_latest.json").unlink()
    _age(tmp_path, "data/strategy_lab/strategy_book.json", 3 * 24 * 60)
    out = doc.check_job_outputs(watched=watched)
    assert out["status"] == "warn"
    assert "Nothing has been written by" in out["summary"] and "stale" in out["summary"]


# ------------------------------------------------------------------ the live table, not a fixture
def test_the_live_table_watches_output_and_never_configuration():
    """`demo_session_pullback.json` is CONFIG and is correctly weeks old; the cycle writes
    `demo_session_pullback_state.json`. Watching the first would warn for ever and train everyone
    to ignore the check."""
    paths = [w["path"] for w in doc.WATCHED_OUTPUTS]
    for config_file in ("data/paper_trading/demo_session_pullback.json",
                        "data/paper_trading/demo_volatility_breakout.json",
                        "data/paper_trading/demo_sweep_trader.json",
                        "data/paper_trading/demo_plan_trader.json"):
        assert config_file not in paths, f"{config_file} is configuration, not output"
    for state_file in ("data/paper_trading/demo_session_pullback_state.json",
                       "data/paper_trading/demo_volatility_breakout_state.json"):
        assert state_file in paths, f"{state_file} is what the cycle actually writes"


def test_every_live_limit_is_at_least_twice_its_interval():
    hourly = [w for w in doc.WATCHED_OUTPUTS if w["every"].startswith("hourly")]
    assert hourly, "the table stopped describing intervals"
    for item in hourly:
        assert item["max_age_min"] >= 120, f"{item['job']} would warn on ordinary lateness"
    for item in doc.WATCHED_OUTPUTS:
        if item["every"].startswith("daily"):
            assert item["max_age_min"] >= 24 * 60 * 1.5, f"{item['job']} would warn on a normal day"


def test_the_three_jobs_that_failed_silently_are_all_watched():
    """Named explicitly. If somebody trims this table, these three must survive."""
    paths = [w["path"] for w in doc.WATCHED_OUTPUTS]
    assert "data/system_health/doctor_latest.json" in paths
    assert "data/strategy_lab/strategy_book.json" in paths
    assert "data/paper_trading/demo_execution_journal.json" in paths


def test_every_watched_row_carries_a_reason_a_human_can_read():
    for item in doc.WATCHED_OUTPUTS:
        assert item["why"] and len(item["why"]) > 15, f"{item['job']} has no usable reason"
        assert item["every"], f"{item['job']} does not say how often it should run"


def test_the_check_never_raises_even_with_a_nonsense_table(tmp_path, monkeypatch):
    """A broken check is a worse outcome than a missing one; the doctor wraps it, but it should not
    need wrapping."""
    monkeypatch.setattr(doc, "ROOT", tmp_path)
    out = doc.check_job_outputs(watched=({"job": "ghost", "every": "hourly", "max_age_min": 60,
                                          "path": "nothing/here.json", "why": "a path that is not there"},))
    assert out["status"] == "warn" and out["detail"]["missing"] == ["ghost"]


# --------------------------------------------------------------------------------------------------
# The "App errors" check could not go red. It read logs/app_stderr.log, which nothing has written
# since 13 September 2026, and printed "No errors in the app log in the last 24 h" every thirty
# minutes - straight through the 5 October crash-loop, where app.py died 18 times, took the broker
# interface down for minutes at a time and cost three strategies their hourly cycle.

def test_silence_from_a_dead_log_is_reported_as_blindness_not_health(tmp_path, monkeypatch):
    """The exact failure: a stale log must NEVER produce a green line."""
    monkeypatch.setattr(doc, "ROOT", tmp_path)
    folder = tmp_path / "logs" / "app"
    folder.mkdir(parents=True)
    stale = folder / "app_1234.log"
    stale.write_text("2026-09-13 11:27:00 INFO started\n", encoding="utf-8")
    import os
    old = time.time() - 22 * 24 * 3600
    os.utime(stale, (old, old))

    out = doc.check_app_errors()
    assert out["status"] == "warn", "a 22-day-old log must not read as healthy"
    assert "blind" in out["summary"].lower()


def test_no_app_log_at_all_is_a_warning_not_an_info(tmp_path, monkeypatch):
    monkeypatch.setattr(doc, "ROOT", tmp_path)
    out = doc.check_app_errors()
    assert out["status"] == "warn"
    assert "cannot be seen" in out["summary"]


def test_it_reads_the_newest_per_launch_log(tmp_path, monkeypatch):
    """One file per launch; the running app's is the newest."""
    monkeypatch.setattr(doc, "ROOT", tmp_path)
    folder = tmp_path / "logs" / "app"
    folder.mkdir(parents=True)
    import os
    older = folder / "app_111.log"
    older.write_text("2026-10-05 09:00:00 ERROR an old crash\n", encoding="utf-8")
    os.utime(older, (time.time() - 7200, time.time() - 7200))
    newest = folder / "app_222.log"
    newest.write_text("2026-10-05 12:00:00 INFO healthy\n", encoding="utf-8")

    assert doc.newest_app_log(tmp_path) == newest
    assert doc.check_app_errors()["status"] == "ok"


def test_a_real_error_in_the_current_log_goes_red(tmp_path, monkeypatch):
    monkeypatch.setattr(doc, "ROOT", tmp_path)
    folder = tmp_path / "logs" / "app"
    folder.mkdir(parents=True)
    now = datetime.datetime.now()
    (folder / "app_333.log").write_text(
        f"{now:%Y-%m-%d %H:%M:%S} ERROR Traceback (most recent call last): boom\n", encoding="utf-8")
    out = doc.check_app_errors()
    assert out["status"] == "warn" and "error lines" in out["summary"]


def test_the_launcher_really_writes_a_per_launch_log():
    """The check is only as good as the launcher feeding it."""
    from pathlib import Path
    bat = (Path(__file__).resolve().parents[1] / "start_trading.bat").read_text(encoding="utf-8")
    assert "APPLOG" in bat, "no per-launch log variable"
    assert "%RANDOM%%RANDOM%" in bat, "the log name is not unique per launch, so a child can lock it"
    assert 'app.py >> "%APPLOG%" 2>&1' in bat, "app.py output is not captured"
    # The only launch line must be the redirected one. A bare `... python.exe app.py` would send every
    # traceback back to a console window, which is the state that made the 5 October crashes invisible.
    bare = [line for line in bat.splitlines() if line.strip().lower().endswith("app.py")]
    assert bare == [], f"a bare unredirected launch remains: {bare}"
