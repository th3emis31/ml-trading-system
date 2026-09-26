"""No loop may finish without writing what it measured - i40 Pilot build map step 5.

The distinction that matters is not running versus broken, it is CLOSED versus OPEN. A loop that
observes, records and displays, while nothing it sees ever changes what it does, is a refresh cadence
wearing a loop's clothes. The learning task reached eleven consecutive runs reporting that the live
model lost money on unseen bars without one threshold moving, and file-freshness monitoring could not
see it, because the files were perfectly fresh.
"""
import json
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


# --- run_main, and the mistake made while adding it (26 Sep 2026) -------------------------------

def test_the_contextmanager_decorator_sits_on_closing_run():
    """The bug: run_main was inserted BETWEEN @contextmanager and def closing_run, so the decorator
    landed on run_main and closing_run became a plain generator - `AttributeError: __enter__` at the
    first use. A source-order mistake that no type checker would catch."""
    import inspect
    import re

    from src import loop_ledger

    source = inspect.getsource(loop_ledger)
    match = re.search(r"@contextmanager\s*\ndef (\w+)", source)
    assert match is not None, "the decorator must still be present"
    assert match.group(1) == "closing_run", f"@contextmanager is on {match.group(1)}, not closing_run"
    assert source.index("def run_main(") > source.index("def closing_run("), (
        "run_main must sit AFTER closing_run, never between it and its decorator")


def test_closing_run_is_actually_usable_as_a_context_manager():
    """The direct consequence of the bug above, asserted separately so it cannot regress silently."""
    from src.loop_ledger import closing_run

    manager = closing_run("test_loop")
    assert hasattr(manager, "__enter__") and hasattr(manager, "__exit__")


def test_run_main_records_a_closure_and_returns_the_exit_code(tmp_path, monkeypatch):
    monkeypatch.setenv("SMARTENTRY_DATA_DIR", str(tmp_path))
    from src.loop_ledger import ledger_path, run_main

    assert run_main("paper_trader", lambda: 0) == 0
    rows = ledger_path().read_text(encoding="utf-8").strip().splitlines()
    assert len(rows) == 1
    record = json.loads(rows[0])
    assert record["loop"] == "paper_trader"
    assert "exit 0" in record["decided"]


def test_a_main_that_returns_nothing_is_treated_as_success():
    from src.loop_ledger import run_main

    assert run_main("paper_trader", lambda: None) == 0


def test_run_main_leaves_acted_false_and_says_why_rather_than_claiming_a_closed_loop(tmp_path, monkeypatch):
    """Whether these loops CHANGED anything is not inferable from an exit code. Recording "it ran, and
    that is not measured" is the truth; claiming a closed loop would not be."""
    monkeypatch.setenv("SMARTENTRY_DATA_DIR", str(tmp_path))
    from src.loop_ledger import ledger_path, run_main

    run_main("daily_report", lambda: 0)
    record = json.loads(ledger_path().read_text(encoding="utf-8").strip().splitlines()[-1])
    assert record["acted"] is False
    assert "not instrumented" in record["note"]


def test_a_crashing_loop_still_leaves_a_record_and_the_error_is_re_raised(tmp_path, monkeypatch):
    """The whole point: a killed or crashed loop that leaves no trace is indistinguishable from an hour
    with nothing to do. On 26 Sep 2026 both demo tasks were killed and left exactly nothing."""
    monkeypatch.setenv("SMARTENTRY_DATA_DIR", str(tmp_path))
    from src.loop_ledger import ledger_path, run_main

    def boom():
        raise RuntimeError("killed mid-run")

    with pytest.raises(RuntimeError):
        run_main("demo_breakout", boom)
    record = json.loads(ledger_path().read_text(encoding="utf-8").strip().splitlines()[-1])
    assert record["acted"] is False
    assert "RuntimeError" in record["note"] and "killed mid-run" in record["note"]


def test_even_a_keyboard_interrupt_is_recorded(tmp_path, monkeypatch):
    """A Ctrl+C is what actually happened - exit 0xC000013A on both demo tasks. It is a BaseException,
    not an Exception, so catching only Exception would have missed the very case this was built for."""
    monkeypatch.setenv("SMARTENTRY_DATA_DIR", str(tmp_path))
    from src.loop_ledger import ledger_path, run_main

    def interrupted():
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run_main("demo_pullback", interrupted)
    record = json.loads(ledger_path().read_text(encoding="utf-8").strip().splitlines()[-1])
    assert "KeyboardInterrupt" in record["note"]


def test_every_wired_module_calls_run_main_or_closing_run():
    """The wiring itself, so a module cannot quietly lose it in a later edit."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    wired = {"demo_volatility_breakout", "demo_session_pullback", "demo_sweep_trader",
             "demo_plan_trader", "paper_trader", "daily_report", "ai_employee",
             "obsidian_notes", "crt_forward", "strategy_lab"}
    missing = []
    for name in sorted(wired):
        text = (root / "src" / f"{name}.py").read_text(encoding="utf-8", errors="ignore")
        if "run_main(" not in text and "closing_run(" not in text:
            missing.append(name)
    assert not missing, f"these loops lost their ledger wiring: {missing}"
