"""No loop may finish without writing what it measured - i40 Pilot build map step 5.

The distinction that matters is not running versus broken, it is CLOSED versus OPEN. A loop that
observes, records and displays, while nothing it sees ever changes what it does, is a refresh cadence
wearing a loop's clothes. The learning task reached eleven consecutive runs reporting that the live
model lost money on unseen bars without one threshold moving, and file-freshness monitoring could not
see it, because the files were perfectly fresh.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from src import loop_ledger as ll


def _ledger(tmp_path):
    return tmp_path / "closures.jsonl"


def test_a_loop_that_never_reports_is_silent_not_assumed_fine(tmp_path):
    """A scheduled task that quietly stopped is invisible unless something watches for its absence."""
    report = ll.closure_report(path=_ledger(tmp_path), known={"ghost": "scheduled"})
    state = report["loops"][0]
    assert state["state"] == "SILENT"
    assert "never written" in state["why"]
    assert report["coverage_pct"] == 0.0


def test_a_run_that_changes_nothing_is_watching_not_a_complaint(tmp_path):
    path = _ledger(tmp_path)
    ll.record_closure("doc", acted=False, observed="20 checks", path=path)
    state = ll.closure_report(path=path, known={"doc": "scheduled"})["loops"][0]
    assert state["state"] == "WATCHING", "one quiet run is a perfectly good outcome"


def test_enough_no_action_runs_becomes_an_open_loop(tmp_path):
    """Once is nothing to do; eight times means nothing it observes reaches what it does."""
    path = _ledger(tmp_path)
    for _ in range(9):
        ll.record_closure("learner", acted=False, observed="model loses money on unseen bars", path=path)
    state = ll.closure_report(path=path, known={"learner": "scheduled"})["loops"][0]
    assert state["state"] == "OPEN"
    assert "not a loop" in state["why"]


def test_an_action_closes_the_loop(tmp_path):
    path = _ledger(tmp_path)
    for _ in range(9):
        ll.record_closure("learner", acted=False, path=path)
    ll.record_closure("learner", acted=True, note="promoted", path=path)
    state = ll.closure_report(path=path, known={"learner": "scheduled"})["loops"][0]
    assert state["state"] == "CLOSED" and state["acted_runs"] == 1


def test_a_loop_that_has_gone_quiet_is_stale(tmp_path):
    from datetime import datetime, timedelta, timezone

    path = _ledger(tmp_path)
    ll.record_closure("doc", acted=True, path=path)
    later = datetime.now(timezone.utc) + timedelta(days=5)
    state = ll.closure_report(path=path, now=later, known={"doc": "scheduled"})["loops"][0]
    assert state["state"] == "STALE" and state["age_days"] >= 4


def test_closing_run_writes_a_record_on_the_happy_path(tmp_path):
    path = _ledger(tmp_path)
    with ll.closing_run("demo", observed="12 bars", path=path) as run:
        run.decided = "entered long"
        run.acted = True
        run.measured = {"entries": 1}
        run.acceptance_passed = True
    rows = ll.read_ledger(path)
    assert len(rows) == 1 and rows[0]["acted"] is True
    assert rows[0]["measured"] == {"entries": 1} and rows[0]["seconds"] is not None


def test_closing_run_still_records_when_the_body_raises(tmp_path):
    """A crashed loop leaving no trace is indistinguishable from one that had nothing to do."""
    path = _ledger(tmp_path)
    with pytest.raises(ValueError):
        with ll.closing_run("demo", path=path) as run:
            run.decided = "about to fail"
            raise ValueError("broker refused")
    rows = ll.read_ledger(path)
    assert len(rows) == 1
    assert rows[0]["acted"] is False
    assert rows[0]["acceptance_passed"] is False
    assert "broker refused" in rows[0]["note"]


def test_the_ledger_is_append_only(tmp_path):
    path = _ledger(tmp_path)
    ll.record_closure("a", path=path)
    ll.record_closure("b", path=path)
    ll.record_closure("a", path=path)
    assert len(ll.read_ledger(path)) == 3, "records are appended, never rewritten"


def test_a_loop_nobody_registered_is_surfaced_not_ignored(tmp_path):
    path = _ledger(tmp_path)
    ll.record_closure("some_new_job", path=path)
    report = ll.closure_report(path=path, known={"doc": "scheduled"})
    assert "some_new_job" in report["unregistered"]


def test_the_doctor_closes_its_own_loop():
    """Wired for real, not just available: a run that finds nothing and a doctor that stopped running
    look identical from outside unless the run says so itself."""
    source = Path(ll.__file__).parent.joinpath("system_doctor.py").read_text(encoding="utf-8")
    assert "record_closure(" in source and '"system_doctor"' in source
    assert "closure_error" in source, "a ledger failure must never fail a health check"


def test_the_learning_loop_closes_its_own_loop():
    source = Path(ll.__file__).parent.joinpath("daily_learning.py").read_text(encoding="utf-8")
    assert "record_closure(" in source and '"daily_learning"' in source
    assert "acted=promoted" in source, "promotion is the action; keeping the champion is not"
