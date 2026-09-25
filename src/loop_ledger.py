"""i40 Pilot layer 4 - no loop may finish without writing what it measured. Build map step 5.

``i40_pilot.loop_closure`` already asks the right question of ONE loop: *has anything the system
observed actually changed what it does?* It found that the Daily Learning task had recorded eleven
consecutive runs saying the live model loses money on unseen bars while not one threshold, weight or
gate had moved. That is an OPEN loop - a refresh cadence wearing a loop's clothes.

This generalises it to every loop, of which the system runs three kinds:

    scheduled   a heartbeat: health, learning, reports
    event       something changed: a trade closed, a page failed
    goal        a multi-step objective that outlives a single session

Each ends the same way, and the ending is the point:

    perceive -> decide -> act -> VERIFY -> RECORD

A loop that cannot say what it measured is a script on a timer. Three states, and the middle one is
the one worth catching because it looks healthy from the outside:

    SILENT   never writes a closure record at all - invisible, could be broken for weeks
    OPEN     records observations, but nothing it observes ever changes what it does
    CLOSED   observations lead to actions

``closing_run`` is the enforcement: a loop wrapped in it writes a record on the way out whether it
succeeded, decided to do nothing, or raised. Especially when it raised - a crashed loop that leaves
no trace is indistinguishable from one that had nothing to do.
"""
from __future__ import annotations

import json
import os
import socket
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from .runtime_paths import smartentry_data_dir

LEDGER_NAME = "loop_closures.jsonl"

# The loops this system is known to run. A loop absent from the ledger is reported as SILENT rather
# than assumed fine: the failure mode being guarded against is a scheduled task that quietly stopped
# and was missed because nothing watched for its absence.
KNOWN_LOOPS = {
    "paper_trader": "scheduled", "strategy_lab": "scheduled", "system_doctor": "scheduled",
    "demo_pullback": "scheduled", "demo_breakout": "scheduled", "demo_sweep": "scheduled",
    "demo_plan": "scheduled", "daily_learning": "scheduled", "daily_report": "scheduled",
    "ai_employee": "scheduled", "obsidian_notes": "scheduled", "crt_forward": "scheduled",
    "i40_pilot": "scheduled",
    # Not on a timer: it turns when an experiment is opened or settled. Listed anyway, so a
    # loop that stops asking questions is visible instead of simply quiet.
    "self_improvement": "on demand",
}


def ledger_path(path: Optional[Path] = None) -> Path:
    return Path(path) if path else smartentry_data_dir() / LEDGER_NAME


def record_closure(loop: str, *, kind: str = "scheduled", observed: str = "", decided: str = "",
                   acted: bool = False, measured: Optional[dict] = None,
                   acceptance_passed: Optional[bool] = None, note: str = "",
                   seconds: Optional[float] = None, path: Optional[Path] = None) -> dict:
    """Write one closure record. Append-only, one line, never rewritten.

    ``acted`` is the field that separates a loop from a cadence: False means the loop ran and changed
    nothing, which is a perfectly good outcome once and a finding after eleven times.

    ``measured`` holds the numbers the loop actually produced. A loop that records ``{}`` here is
    telling the truth about having measured nothing, which is itself worth seeing.
    """
    row = {
        "at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "loop": loop, "kind": kind,
        "observed": str(observed)[:300], "decided": str(decided)[:300],
        "acted": bool(acted),
        "measured": measured or {},
        "acceptance_passed": acceptance_passed,
        "note": str(note)[:300],
        "seconds": round(seconds, 1) if seconds is not None else None,
        "host": socket.gethostname(), "pid": os.getpid(),
    }
    target = ledger_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, default=str) + "\n")
    return row


@contextmanager
def closing_run(loop: str, *, kind: str = "scheduled", observed: str = "",
                path: Optional[Path] = None):
    """Run a loop body so that it CANNOT finish without leaving a record.

    Use it as::

        with closing_run("demo_pullback", observed="12 closed bars") as run:
            ...
            run.measured = {"entries": 1}
            run.acted = True
            run.acceptance_passed = True

    On an exception the record is still written, marked not-acted and with the error as its note, and
    the exception is re-raised. A crashed loop that leaves no trace looks exactly like one that had
    nothing to do, and the two need to be told apart.
    """

    class _Run:
        def __init__(self):
            self.observed = observed
            self.decided = ""
            self.acted = False
            self.measured: dict = {}
            self.acceptance_passed: Optional[bool] = None
            self.note = ""

    run = _Run()
    started = time.monotonic()
    try:
        yield run
    except BaseException as exc:                       # noqa: BLE001 - deliberately catch and re-raise
        record_closure(loop, kind=kind, observed=run.observed,
                       decided=run.decided or "raised before deciding", acted=False,
                       measured=run.measured, acceptance_passed=False,
                       note=f"{type(exc).__name__}: {exc}"[:300],
                       seconds=time.monotonic() - started, path=path)
        raise
    else:
        record_closure(loop, kind=kind, observed=run.observed, decided=run.decided,
                       acted=run.acted, measured=run.measured,
                       acceptance_passed=run.acceptance_passed, note=run.note,
                       seconds=time.monotonic() - started, path=path)


def read_ledger(path: Optional[Path] = None, limit: int = 4000) -> list:
    target = ledger_path(path)
    if not target.exists():
        return []
    rows = []
    for line in target.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]:
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def _age_days(stamp: str, now: datetime) -> Optional[float]:
    try:
        when = datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None
    return round((now - when).total_seconds() / 86400.0, 2)


def closure_report(path: Optional[Path] = None, now=None, known: Optional[dict] = None,
                   open_after: int = 8, stale_days: float = 2.0) -> dict:
    """Which loops close, which only observe, and which have gone quiet.

    ``open_after`` is the number of consecutive no-action runs that turns "nothing to do" into a
    finding. Eight is deliberately lower than the eleven the learning loop reached before anybody
    noticed, and one no-action run is never a complaint.
    """
    now = now or datetime.now(timezone.utc)
    known = known if known is not None else KNOWN_LOOPS
    rows = read_ledger(path)

    by_loop: dict = {}
    for row in rows:
        by_loop.setdefault(row.get("loop") or "?", []).append(row)

    states = []
    for name, kind in sorted(known.items()):
        runs = by_loop.get(name) or []
        if not runs:
            states.append({"loop": name, "kind": kind, "state": "SILENT", "runs": 0,
                           "why": "has never written a closure record, so nothing can tell whether "
                                  "it ran, did nothing, or died"})
            continue
        last = runs[-1]
        age = _age_days(last.get("at", ""), now)
        actions = [r for r in runs if r.get("acted")]
        since_action = 0
        for row in reversed(runs):
            if row.get("acted"):
                break
            since_action += 1
        failed = [r for r in runs if r.get("acceptance_passed") is False]
        if age is not None and age > stale_days:
            state, why = "STALE", f"last closed {age} days ago"
        elif not actions and since_action >= open_after:
            state = "OPEN"
            why = (f"{since_action} consecutive runs changed nothing. Observing and recording is not "
                   "a loop - nothing it sees is reaching what it does.")
        elif not actions:
            state, why = "WATCHING", f"{since_action} run(s) with nothing to do - not yet a finding"
        else:
            state, why = "CLOSED", f"{len(actions)} of {len(runs)} runs changed something"
        states.append({"loop": name, "kind": kind, "state": state, "runs": len(runs),
                       "last_at": last.get("at"), "age_days": age,
                       "acted_runs": len(actions), "runs_since_action": since_action,
                       "failed_acceptance": len(failed), "why": why})

    unknown = sorted(set(by_loop) - set(known))
    counts: dict = {}
    for row in states:
        counts[row["state"]] = counts.get(row["state"], 0) + 1
    return {
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "loops": states, "counts": counts,
        "reporting": sum(1 for s in states if s["state"] != "SILENT"),
        "total_known": len(known),
        "coverage_pct": round(100.0 * sum(1 for s in states if s["state"] != "SILENT") / len(known), 1)
                        if known else None,
        "unregistered": unknown,
        "rule": ("A loop that cannot say what it measured is a script on a timer. SILENT means it has "
                 "never reported; OPEN means it reports but nothing it observes changes what it does."),
    }


def recent(loop: str, limit: int = 10, path: Optional[Path] = None) -> list:
    return [r for r in read_ledger(path) if r.get("loop") == loop][-limit:]


if __name__ == "__main__":       # pragma: no cover - a hand check, not a test
    print(json.dumps(closure_report(), indent=1))
