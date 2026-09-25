"""The system improving itself: one experiment at a time, with the prediction locked before the answer.

Build map step 10. The system already PROPOSES - `src.ai_employee` writes proposals every morning -
and it already MEASURES, in the strategy lab, the learning gate and the backtests. What it could not
do is join the two: state what it expects, find out, and be held to what it said.

That join is the whole of this module, and it exists because of a specific failure. On 25 September
2026 the LSTM was investigated for a whole morning. Three explanations were offered in turn - too
little data, too small a window, a bad feature set - and each sounded convincing until it was
measured. Two were wrong. Nothing in the system recorded that they had been ruled out, so nothing
stops them being offered again next month as though they were new.

## What makes this honest rather than theatre

**The prediction is written and hashed BEFORE the measurement runs.** `propose()` stores a digest of
the metric, the direction, the threshold and the command that will settle it. `settle()` recomputes
that digest and refuses to record a verdict if it has changed. Nothing here prevents a person editing
the file - the point is that the edit cannot be silent, so "it improved" can never be produced by
moving the line after seeing where the ball landed.

**A refuted prediction is a success.** It removes a candidate explanation permanently, which is the
thing that was missing in the LSTM morning. `experiment_report()` therefore reports the refuted count
as progress and never as failure, and a loop whose experiments are all confirmed is treated as
suspicious: predictions that always come true were not predictions.

**The number has to come from somewhere.** `measure()` runs the command the experiment declared and
reads the number out of its output. If the command does not produce one, the verdict is
`inconclusive` with the reason. It is never filled in from reasoning, and there is no code path that
writes a result the measurement did not return - which is the one way a self-improving loop can
quietly start lying to its owner about its own progress.

**It may not touch anything that trades.** Every experiment carries a governance tier. Opening or
settling is read-and-local work, but ACTING on a confirmed result is checked through
`src.governance.check_permission`, and anything at T2 or above becomes a proposal for the owner
rather than an action. The loop can change what it believes on its own; it cannot change what the
account does.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .loop_ledger import _age_days          # same question, same format - see STAMP below
from .runtime_paths import smartentry_data_dir
from .second_brain import _console_safe     # cp1252 consoles cannot print the records

LEDGER_NAME = "self_improvement.jsonl"
# The SAME stamp format src/loop_ledger.py writes, so its _age_days reads these rows and the
# two ledgers can be read side by side without converting anything.
STAMP = "%Y-%m-%d %H:%M:%S"

DIRECTIONS = ("higher", "lower")
STATES = ("open", "measured", "closed")
VERDICTS = ("confirmed", "refuted", "inconclusive")

# How a measurement command hands its number back. Either line is enough; the first match wins.
# Deliberately narrow: a loose "find any number in the output" rule would pick up a row count or a
# timestamp and report it as the result.
METRIC_LINE = re.compile(r"^\s*METRIC\s+([A-Za-z_][\w.]*)\s*=\s*(-?\d+(?:\.\d+)?)\s*$", re.M)
METRIC_JSON = re.compile(r"^\s*@@\s*(\{.*\})\s*$", re.M)

# An experiment that has sat open this long is stale: either the measurement never ran or it was
# forgotten. Reported, because a forgotten experiment is how a question silently becomes an opinion.
STALE_DAYS = 14

# Confirmed predictions SHOULD be the minority. If nearly everything proposed turns out to be true,
# the predictions are being written after the fact or are too safe to be informative.
SUSPICIOUS_CONFIRM_RATE = 0.8
MIN_FOR_RATE = 5


@dataclass
class Prediction:
    """What is expected, stated precisely enough to be wrong."""

    metric: str
    direction: str                 # "higher" or "lower" than the threshold
    threshold: float
    baseline: Optional[float]      # what it is today, so the size of the claim is visible
    measured_by: str               # the exact command that settles it

    def as_dict(self) -> dict:
        return {"metric": self.metric, "direction": self.direction, "threshold": float(self.threshold),
                "baseline": None if self.baseline is None else float(self.baseline),
                "measured_by": self.measured_by}

    def holds(self, value: float) -> bool:
        return value > self.threshold if self.direction == "higher" else value < self.threshold

    def describe(self) -> str:
        base = "" if self.baseline is None else f" (today {self.baseline:g})"
        return f"{self.metric} {'>' if self.direction == 'higher' else '<'} {self.threshold:g}{base}"


@dataclass
class Experiment:
    id: str
    question: str
    source: str
    prediction: Prediction
    tier: str
    opened: str
    lock: str
    state: str = "open"
    result: dict = field(default_factory=dict)
    verdict: str = ""
    acted: str = ""
    closed: str = ""

    def as_dict(self) -> dict:
        return {"id": self.id, "question": self.question, "source": self.source,
                "prediction": self.prediction.as_dict(), "tier": self.tier, "opened": self.opened,
                "lock": self.lock, "state": self.state, "result": dict(self.result),
                "verdict": self.verdict, "acted": self.acted, "closed": self.closed}


def _utc_text() -> str:
    return datetime.now(timezone.utc).strftime(STAMP)


def ledger_file(path: Optional[Path] = None) -> Path:
    return Path(path) if path else smartentry_data_dir() / LEDGER_NAME


def prediction_digest(prediction, question: str = "") -> str:
    """A stable hash of the claim. Changing any part of what was predicted changes this.

    Sorted keys and a fixed float format, so the same prediction always hashes the same way on any
    machine and in any Python version - otherwise the lock would fail for reasons that are nothing to
    do with honesty.
    """
    body = prediction.as_dict() if isinstance(prediction, Prediction) else dict(prediction)
    payload = {"question": question.strip(),
               "metric": str(body.get("metric", "")),
               "direction": str(body.get("direction", "")),
               "threshold": f"{float(body.get('threshold', 0)):.10g}",
               "measured_by": str(body.get("measured_by", ""))}
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def verify_lock(row: dict) -> dict:
    """Does this row's prediction still match the hash taken when it was opened?

    The baseline is deliberately NOT part of the hash: recording that today's figure was re-measured
    more precisely is honest. The metric, the direction, the threshold and the settling command are,
    because those four are the claim itself.
    """
    stored = str(row.get("lock") or "")
    recomputed = prediction_digest(row.get("prediction") or {}, row.get("question") or "")
    ok = bool(stored) and stored == recomputed
    return {"ok": ok, "stored": stored, "recomputed": recomputed,
            "note": "" if ok else ("the prediction was changed after it was locked - a verdict from "
                                   "this row cannot be trusted")}


def _append(row: dict, path: Optional[Path] = None) -> Path:
    target = ledger_file(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, separators=(",", ":")) + "\n")
    return target


def experiments(path: Optional[Path] = None, limit: int = 4000) -> list:
    """Every experiment, latest write per id winning, so settling is an append rather than an edit."""
    target = ledger_file(path)
    if not target.exists():
        return []
    rows: dict = {}
    order: list = []
    try:
        lines = target.read_text(encoding="utf-8").splitlines()[-limit:]
    except OSError:
        return []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue                       # a truncated line is skipped, never guessed at
        key = row.get("id")
        if not key:
            continue
        if key not in rows:
            order.append(key)
        rows[key] = row
    return [rows[key] for key in order]


def _next_id(rows: list) -> str:
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    used = sum(1 for row in rows if str(row.get("id", "")).startswith(f"exp_{today}"))
    return f"exp_{today}_{used + 1:02d}"


def propose(question: str, *, metric: str, direction: str, threshold: float,
            measured_by: str, source: str, baseline: Optional[float] = None,
            tier: Optional[str] = None, path: Optional[Path] = None) -> dict:
    """Open an experiment. The prediction is locked here, before anything is measured.

    ``source`` says where the idea came from - a second-brain entry id, a BASELINE row, an AI employee
    proposal, a doctor finding. An experiment with no source is an idea someone had, which is allowed,
    but it should say so rather than look like it came from the evidence.
    """
    if direction not in DIRECTIONS:
        raise ValueError(f"direction must be one of {DIRECTIONS}, got {direction!r}")
    if not str(measured_by).strip():
        raise ValueError("an experiment needs the command that will settle it")
    if not str(question).strip():
        raise ValueError("an experiment needs a question")

    from . import governance

    # A tier this module does not recognise must be a loud error, never a quiet downgrade.
    # governance.tier_for() deliberately ignores a `declared` value outside TIER_ORDER - correct there,
    # because self-declaration must not be trusted - but it means a typo like "T3_CRITICAL" instead of
    # "T3" would leave the action at whatever its wording happened to match. That is precisely the
    # direction a guard must not fail in, so it is rejected at the point the experiment is written.
    if tier is not None and tier not in governance.TIER_ORDER:
        raise ValueError(f"tier must be one of {governance.TIER_ORDER} "
                         f"(governance.T0_READ .. T3_CRITICAL), got {tier!r}")

    prediction = Prediction(metric=metric, direction=direction, threshold=float(threshold),
                            baseline=None if baseline is None else float(baseline),
                            measured_by=str(measured_by).strip())
    rows = experiments(path)
    experiment = Experiment(
        id=_next_id(rows), question=question.strip(), source=str(source).strip() or "unsourced",
        prediction=prediction,
        tier=tier or governance.tier_for(f"run {measured_by}"),
        opened=_utc_text(), lock=prediction_digest(prediction, question))
    _append(experiment.as_dict(), path)
    return experiment.as_dict()


def _number_from(output: str, metric: str):
    """Pull the declared metric out of a command's output, or nothing at all."""
    for match in METRIC_LINE.finditer(output or ""):
        if match.group(1) == metric:
            return float(match.group(2))
    for match in METRIC_JSON.finditer(output or ""):
        try:
            payload = json.loads(match.group(1))
        except ValueError:
            continue
        if isinstance(payload, dict) and metric in payload:
            try:
                return float(payload[metric])
            except (TypeError, ValueError):
                return None
    return None


def run_measurement(row: dict, *, run=subprocess.run, timeout: int = 3600, cwd: Optional[Path] = None) -> dict:
    """Run the command the experiment declared and read its number back.

    No fallback and no inference. If the command fails, or succeeds without printing the metric it
    promised, this returns ``value: None`` with the reason - and `settle()` turns that into
    `inconclusive`. That is the whole defence against a loop that reports progress it did not make.
    """
    prediction = row.get("prediction") or {}
    command = str(prediction.get("measured_by") or "")
    metric = str(prediction.get("metric") or "")
    if not command or not metric:
        return {"value": None, "reason": "the experiment declared no command or no metric"}
    try:
        proc = run(command, shell=True, capture_output=True, text=True, timeout=timeout,
                   cwd=str(cwd) if cwd else None)
    except Exception as exc:
        return {"value": None, "reason": f"the measurement could not run: {type(exc).__name__}: {exc}"}
    output = (getattr(proc, "stdout", "") or "") + "\n" + (getattr(proc, "stderr", "") or "")
    value = _number_from(output, metric)
    if value is None:
        tail = output.strip().splitlines()[-4:]
        return {"value": None, "returncode": getattr(proc, "returncode", None),
                "reason": (f"the command ran but never printed 'METRIC {metric} = <number>'; "
                           f"last lines: {' | '.join(line.strip()[:90] for line in tail)}")}
    return {"value": value, "returncode": getattr(proc, "returncode", None),
            "command": command, "at": _utc_text()}


def settle(experiment_id: str, *, value=None, reason: str = "", raw: str = "",
           path: Optional[Path] = None, **_ignored) -> dict:
    """Record the verdict against the LOCKED prediction. Refuses if the prediction moved.

    ``value`` None means the measurement did not produce a number, which is `inconclusive` - a real
    and useful state, and the one a dishonest loop would be tempted to round up into a result.
    """
    rows = experiments(path)
    row = next((r for r in rows if r.get("id") == experiment_id), None)
    if row is None:
        return {"ok": False, "reason": f"no experiment {experiment_id}"}
    if row.get("state") == "closed":
        return {"ok": False, "reason": f"{experiment_id} is already closed with verdict "
                                      f"{row.get('verdict') or 'none'}"}
    lock = verify_lock(row)
    if not lock["ok"]:
        return {"ok": False, "reason": lock["note"], "lock": lock}

    body = row.get("prediction") or {}
    prediction = Prediction(metric=body.get("metric", ""), direction=body.get("direction", "higher"),
                            threshold=float(body.get("threshold", 0)), baseline=body.get("baseline"),
                            measured_by=body.get("measured_by", ""))
    if value is None:
        verdict = "inconclusive"
    else:
        verdict = "confirmed" if prediction.holds(float(value)) else "refuted"

    row = dict(row)
    row["state"] = "closed"
    row["verdict"] = verdict
    row["closed"] = _utc_text()
    row["result"] = {"value": None if value is None else float(value),
                     "reason": reason, "raw": str(raw)[:2000], "at": _utc_text()}
    _append(row, path)
    return {"ok": True, "id": experiment_id, "verdict": verdict,
            "predicted": prediction.describe(),
            "measured": None if value is None else float(value),
            "note": {"confirmed": "the prediction held; acting on it still needs its tier's permission",
                     "refuted": "the prediction failed - this explanation is now ruled out, which is "
                                "the point of running it",
                     "inconclusive": "no number came back, so nothing is known either way"}[verdict]}


def record_outcome(experiment_id: str, what_changed: str, *, path: Optional[Path] = None) -> dict:
    """What actually changed because of this experiment. Usually nothing, and that must be sayable.

    Checked through governance at the experiment's own tier: a confirmed result that would change
    live behaviour is recorded as a proposal for the owner, never applied here.
    """
    from . import governance

    rows = experiments(path)
    row = next((r for r in rows if r.get("id") == experiment_id), None)
    if row is None:
        return {"ok": False, "reason": f"no experiment {experiment_id}"}
    decision = governance.check_permission(f"apply experiment result: {what_changed}",
                                           declared_tier=row.get("tier"))
    allowed = bool(getattr(decision, "allowed", False))
    # The tier that governance ACTUALLY applied, which can be higher than the one the experiment
    # declared - tier_for raises but never lowers. Reporting the declared tier beside a refusal
    # written for a higher one made the record say T1 while the decision was made at T3.
    applied = str(getattr(decision, "tier", "") or row.get("tier") or "")
    row = dict(row)
    row["acted"] = str(what_changed).strip()
    row["acted_at"] = _utc_text()
    row["acted_allowed"] = allowed
    row["acted_tier"] = applied
    row["acted_reason"] = str(getattr(decision, "reason", ""))
    _append(row, path)
    return {"ok": True, "id": experiment_id, "allowed": allowed,
            "tier": applied, "declared_tier": row.get("tier"), "reason": row["acted_reason"],
            "note": "" if allowed else "recorded as a proposal for the owner, not applied"}


def experiment_report(path: Optional[Path] = None, now: Optional[datetime] = None) -> dict:
    """What the loop has actually learned. Refuted counts as progress, because it is.

    The `honest` flag is the one worth reading: a loop whose predictions nearly all come true is not
    working well, it is being written after the fact or asking questions it already knows.
    """
    rows = experiments(path)
    closed = [r for r in rows if r.get("state") == "closed"]
    counts = {verdict: sum(1 for r in closed if r.get("verdict") == verdict) for verdict in VERDICTS}
    settled = counts["confirmed"] + counts["refuted"]
    rate = round(counts["confirmed"] / settled, 3) if settled else None
    open_rows = [r for r in rows if r.get("state") != "closed"]
    stale = [{"id": r.get("id"), "question": r.get("question"), "age_days": _age_days(r.get("opened"), now or datetime.now(timezone.utc))}
             for r in open_rows if (_age_days(r.get("opened"), now or datetime.now(timezone.utc)) or 0) > STALE_DAYS]
    tampered = [{"id": r.get("id"), **verify_lock(r)} for r in rows if not verify_lock(r)["ok"]]

    if not rows:
        summary = "no experiment has been run yet - the loop exists but has not turned"
    elif settled == 0:
        summary = f"{len(open_rows)} experiment(s) open, none settled yet"
    else:
        summary = (f"{settled} settled: {counts['refuted']} explanation(s) ruled out, "
                   f"{counts['confirmed']} prediction(s) held")

    honest = True
    honest_note = ""
    if settled >= MIN_FOR_RATE and rate is not None and rate >= SUSPICIOUS_CONFIRM_RATE:
        honest = False
        honest_note = (f"{rate:.0%} of predictions came true. Predictions that always hold are not "
                       "predictions - they are being written after the measurement, or they are too "
                       "safe to teach anything.")
    if tampered:
        honest = False
        honest_note = (honest_note + " " if honest_note else "") + \
            f"{len(tampered)} experiment(s) had their prediction changed after it was locked."

    return {"total": len(rows), "closed": len(closed), "open": len(open_rows),
            "counts": counts, "confirm_rate": rate, "summary": summary,
            "stale": stale, "tampered": tampered,
            "honest": honest, "honest_note": honest_note,
            "ruled_out": [{"id": r.get("id"), "question": r.get("question"),
                           "predicted": (r.get("prediction") or {}).get("metric"),
                           "measured": (r.get("result") or {}).get("value")}
                          for r in closed if r.get("verdict") == "refuted"],
            "places_orders": False}


def candidates(limit: int = 8) -> dict:
    """Questions worth an experiment, taken from the system's own record rather than invented.

    Three sources, in the order they deserve attention: claims the second brain can find no
    measurement for, the owner's pending proposals, and whatever the doctor is currently unhappy
    about. Each is only a CANDIDATE - it becomes an experiment when someone states a prediction for
    it, which is the step that cannot be automated away.
    """
    found: list = []
    try:
        from .second_brain import hypotheses

        for entry in (hypotheses().get("entries") or [])[:limit]:
            found.append({"source": f"second_brain:{entry.get('id')}", "kind": "unmeasured claim",
                          "question": entry.get("title") or "", "why": entry.get("why") or ""})
    except Exception as exc:
        found.append({"source": "second_brain", "kind": "unavailable", "question": "",
                      "why": f"{type(exc).__name__}: {exc}"})
    try:
        employee = Path(smartentry_data_dir()) / "ai_employee" / "proposals.json"
        if employee.exists():
            payload = json.loads(employee.read_text(encoding="utf-8"))
            items = payload if isinstance(payload, list) else (payload.get("proposals") or [])
            for item in items:
                if str(item.get("status", "")).lower() == "pending":
                    found.append({"source": f"ai_employee:{item.get('id')}", "kind": "proposal",
                                  "question": item.get("title") or item.get("summary") or "",
                                  "why": item.get("category") or ""})
    except Exception:
        pass                    # a missing or unreadable proposals file is not a failure of the loop
    try:
        latest = Path(smartentry_data_dir()) / "system_health" / "doctor_latest.json"
        if latest.exists():
            payload = json.loads(latest.read_text(encoding="utf-8"))
            for check in (payload.get("checks") or []):
                if check.get("status") in ("warn", "fail"):
                    found.append({"source": f"doctor:{check.get('name')}", "kind": check.get("status"),
                                  "question": str(check.get("summary") or "")[:200], "why": ""})
    except Exception:
        pass
    return {"count": len(found), "candidates": found[:limit * 2],
            "note": ("A candidate is not an experiment. It becomes one only when a prediction is "
                     "stated for it - that is the step that makes the answer mean something.")}


def main(argv=None) -> int:
    """`python -m src.self_improvement status|candidates|list|open|settle|report`. Never trades."""
    _console_safe()
    argv = list(sys.argv[1:] if argv is None else argv)
    command = (argv[0] if argv else "status").lower()

    if command in ("status", "report"):
        rep = experiment_report()
        print(f"self-improvement loop: {rep['summary']}")
        print(f"  open {rep['open']}   closed {rep['closed']}   "
              f"refuted {rep['counts']['refuted']}   confirmed {rep['counts']['confirmed']}   "
              f"inconclusive {rep['counts']['inconclusive']}")
        if rep["confirm_rate"] is not None:
            print(f"  confirm rate {rep['confirm_rate']:.0%}"
                  f"{'' if rep['honest'] else '  <-- SUSPICIOUS'}")
        if rep["honest_note"]:
            print(f"  {rep['honest_note']}")
        for row in rep["ruled_out"]:
            print(f"  ruled out: {row['question'][:88]}  (measured {row['measured']})")
        for row in rep["stale"]:
            print(f"  STALE {row['id']} open {row['age_days']} days: {row['question'][:70]}")
        return 0

    if command == "candidates":
        found = candidates()
        print(f"{found['count']} candidate question(s) from the system's own record:")
        for row in found["candidates"]:
            print(f"  [{row['kind']:16}] {row['question'][:96]}")
            print(f"   {'':18} source {row['source']}")
        print()
        print(found["note"])
        return 0

    if command == "list":
        for row in experiments():
            lock = "" if verify_lock(row)["ok"] else "  LOCK BROKEN"
            print(f"{row['id']}  {row.get('state','?'):8} {row.get('verdict') or '-':13} "
                  f"{row.get('question','')[:70]}{lock}")
        return 0

    print(__doc__.strip().splitlines()[0])
    print("usage: python -m src.self_improvement status|candidates|list")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
