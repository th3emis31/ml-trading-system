"""How good is the builder, measured - and the bar it has to clear before its output is trusted.

    python -m src.build_bench            # the whole benchmark, both splits
    python -m src.build_bench --split holdout

WHAT THIS MEASURES, AND WHAT IT DOES NOT
----------------------------------------
It measures **specification quality**: given a request, does the builder produce numbered, testable
rules that cover the boundaries, each carrying a check a machine can decide?

It does **NOT** measure that generated code runs. Saying otherwise would be the flattering version of
this number and the owner has been clear that nothing gets reported flatteringly. Spec quality is worth
measuring on its own evidence: holding the model, the test budget and the repair loop fixed, grounding
the checks in a spec produced correct code +38 percentage points more often (arXiv:2607.06636). So this
measures the input the research identifies as the driver, and names it as that rather than as the
outcome.

THE SPLIT, AND WHY THERE IS ONE
-------------------------------
`calibrate` tasks are for changing things. `holdout` tasks are never tuned against, and the trust bar
is read from the holdout only. This is the same rule the strategy lab already lives under, for the same
reason: a score on tasks you adjusted the prompt against is a measure of the adjusting, not of the
builder. The two sets are fixed in this file so a later run is comparable with an earlier one - adding
a task changes the benchmark and therefore the baseline, which is why `TASKS_VERSION` exists and is
reported with every score.

THE TRUST BAR
-------------
Stated once, here, so it cannot drift to fit a result - the same discipline as the deflated Sharpe bar
that is never lowered to let a near-miss through. See `trust()`.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from .runtime_paths import smartentry_data_dir

# Bump this whenever a task is added, removed or reworded: a score is only comparable with another
# score from the same benchmark, and a silently changed benchmark is how a baseline becomes a lie.
TASKS_VERSION = 1

# Each task names the request and the BOUNDARY CONCEPTS a competent spec has to address. The concepts
# are alternative spellings for one idea, so a spec is credited for meaning it rather than for using a
# particular word - the research finding is that the spec's content is what matters, not its wording.
CALIBRATE = (
    {"key": "session_range",
     "request": ("A Python function returning the London session high and low from a list of OHLC "
                 "bars, given the session start and end hour in broker server time."),
     "must_cover": {"empty": ("empty", "no bars", "none found", "zero bars"),
                    "bad_hour": ("0-23", "0 and 23", "invalid hour", "out of range", "outside"),
                    "missing_data": ("non-numeric", "missing", "invalid", "malformed", "not a number")},
     "min_rules": 4},
    {"key": "position_size",
     "request": ("A function that turns account balance, risk percent and stop distance into a lot "
                 "size, refusing anything that would risk more than the percent given."),
     "must_cover": {"zero_stop": ("zero", "0 ", "divide", "division"),
                    "negative": ("negative", "below zero", "less than zero"),
                    "cap": ("maximum", "cap", "exceed", "more than", "limit")},
     "min_rules": 4},
    {"key": "csv_loader",
     "request": ("A loader that reads a broker CSV of bars into a frame, rejecting rows whose "
                 "timestamps are duplicated or out of order."),
     "must_cover": {"duplicate": ("duplicate", "repeated", "same timestamp"),
                    "order": ("out of order", "unsorted", "descending", "chronological", "ascending"),
                    "missing_file": ("missing", "does not exist", "not found", "absent")},
     "min_rules": 4},
    {"key": "atr",
     "request": "A function computing ATR over N bars, where N may be larger than the data supplied.",
     "must_cover": {"short_data": ("fewer", "less than", "insufficient", "not enough", "shorter"),
                    "n_invalid": ("zero", "negative", "positive integer", "at least 1"),
                    "type": ("float", "numeric", "number", "returns")},
     "min_rules": 3},
)

HOLDOUT = (
    {"key": "spread_filter",
     "request": ("A check that refuses a trade when the broker spread is wider than a given fraction "
                 "of the stop distance."),
     "must_cover": {"zero_stop": ("zero", "0 ", "divide", "division"),
                    "missing_quote": ("missing", "unavailable", "no quote", "stale", "none"),
                    "boundary": ("equal", "exactly", "boundary", "inclusive", "same as")},
     "min_rules": 4},
    {"key": "session_clock",
     "request": ("A converter from broker server time to UTC that works across a daylight-saving "
                 "change rather than assuming a fixed offset."),
     "must_cover": {"dst": ("daylight", "summer", "winter", "dst", "offset changes"),
                    "ambiguous": ("ambiguous", "twice", "repeated hour", "gap", "nonexistent"),
                    "naive": ("naive", "timezone", "aware", "tzinfo")},
     "min_rules": 4},
    {"key": "equity_curve",
     "request": ("A function returning maximum drawdown from a list of equity values, as a positive "
                 "fraction."),
     "must_cover": {"empty": ("empty", "no values", "none", "zero length"),
                    "monotonic": ("rising", "never falls", "no drawdown", "increasing", "zero"),
                    "sign": ("positive", "absolute", "non-negative", "fraction")},
     "min_rules": 3},
    {"key": "json_state",
     "request": ("A reader for a state file that returns a default when the file is missing, empty or "
                 "corrupt, and never raises."),
     "must_cover": {"missing": ("missing", "does not exist", "absent", "not found"),
                    "corrupt": ("corrupt", "invalid json", "malformed", "unparseable", "bad json"),
                    "never_raises": ("never raise", "not raise", "no exception", "does not throw")},
     "min_rules": 4},
)

# --- the bar -----------------------------------------------------------------------------------
# Read from the HOLDOUT only, and fixed here rather than chosen after seeing a score.
MIN_HOLDOUT_TASKS = 4          # every holdout task must have been attempted
MIN_SPEC_SCORE = 0.80          # mean score across the holdout
MIN_MACHINE_FRACTION = 0.70    # most rules must be decidable without asking the owner
MIN_WORST_TASK = 0.40          # and no single task may collapse: an average can hide a total failure

BENCH_FILE = "build_bench.json"


def _check_is_valid(rule) -> bool:
    """Could this check actually be adjudicated, or is it only shaped like a check?

    A `number:` rule must compare a name against a literal; `returned_value >= low` cannot be read by
    anything and so proves nothing. `run`, `file` and `appended` carry a path or command and are taken
    as readable here - whether they PASS is a separate question decided elsewhere.
    """
    from .skill_acceptance import NUMBER_RULE

    if rule.kind == "number":
        body = (rule.check or "").split(":", 1)[-1].strip()
        return bool(NUMBER_RULE.match(body))
    return bool((rule.check or "").split(":", 1)[-1].strip())


def score_spec(spec, task: dict) -> dict:
    """One task's score: did it parse, does it cover the boundaries, can a machine decide it?

    Three components, deliberately equal-weighted and each in 0..1, because there is no evidence for
    a cleverer weighting and inventing one would dress a guess up as a measurement.
    """
    rules = getattr(spec, "rules", []) or []
    text = " ".join((rule.text or "").lower() for rule in rules)
    covered = {name: any(word.lower() in text for word in words)
               for name, words in (task.get("must_cover") or {}).items()}
    coverage = (sum(covered.values()) / len(covered)) if covered else 0.0
    enough = min(1.0, len(rules) / max(1, int(task.get("min_rules") or 1)))
    checked = [rule for rule in rules if rule.check]
    # Machine-decidable means the check can ACTUALLY be adjudicated, not merely that its kind is one of
    # the automatic ones. Found on 25 September 2026: the builder writes `number: returned_value >= low`,
    # which compares against a NAME rather than a value and cannot be read at all. Counting that as
    # machine-decidable inflated this score, and an inflated score is what a trust bar must never rest
    # on - so validity is checked here rather than assumed from the kind.
    machine = [rule for rule in checked if rule.machine_checked and _check_is_valid(rule)]
    machine_fraction = (len(machine) / len(rules)) if rules else 0.0
    return {"task": task["key"], "rules": len(rules), "with_check": len(checked),
            "covered": covered, "coverage": round(coverage, 3),
            "enough_rules": round(enough, 3), "machine_fraction": round(machine_fraction, 3),
            "malformed": [rule.number for rule in checked
                          if rule.machine_checked and not _check_is_valid(rule)],
            "score": round((coverage + enough + machine_fraction) / 3, 3),
            "missed": [name for name, hit in covered.items() if not hit]}


def bench(split: str = "holdout", drafter: Optional[Callable] = None,
          family: str = "software") -> dict:
    """Run one split and score it. A task the provider could not answer scores 0 and says so."""
    if drafter is None:
        from .builder import draft_spec as drafter

    tasks = HOLDOUT if split == "holdout" else CALIBRATE
    rows = []
    for task in tasks:
        drafted = drafter(task["request"], family=family, title=task["key"])
        if not drafted.get("ok") or drafted.get("spec") is None:
            rows.append({"task": task["key"], "rules": 0, "with_check": 0, "coverage": 0.0,
                         "enough_rules": 0.0, "machine_fraction": 0.0, "score": 0.0,
                         "covered": {}, "missed": list((task.get("must_cover") or {})),
                         "error": drafted.get("error") or "no spec"})
            continue
        row = score_spec(drafted["spec"], task)
        row["provider"] = drafted.get("provider")
        rows.append(row)
    scores = [row["score"] for row in rows]
    return {"split": split, "tasks_version": TASKS_VERSION, "tasks": len(rows), "rows": rows,
            "spec_score": round(statistics.fmean(scores), 4) if scores else 0.0,
            "worst_task": round(min(scores), 4) if scores else 0.0,
            "machine_fraction": round(statistics.fmean([r["machine_fraction"] for r in rows]), 4)
                                if rows else 0.0,
            "provider": next((r.get("provider") for r in rows if r.get("provider")), None),
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")}


def trust(holdout: dict) -> dict:
    """May the builder's specs be acted on without reading every line? Answered from the holdout.

    This is the question "when can I trust it", made answerable. Four conditions, all required:

    1. every holdout task attempted - a score over two tasks is not a measurement;
    2. mean spec score at or above 0.80;
    3. at least 70% of rules decidable by machine, because a spec full of `ask` cannot run unattended;
    4. no single task below 0.40 - a mean can hide one complete collapse, and the collapse is exactly
       the case that would be shipped unnoticed.

    A `no` names the condition that failed. And a `yes` is bounded: it says the SPECS can be acted on,
    not that anything built is correct. Only a build whose own checks pass is that, which is what
    `builder.verdict_for` decides - this bar governs whether the spec is worth building from.
    """
    reasons = []
    if holdout.get("tasks", 0) < MIN_HOLDOUT_TASKS:
        reasons.append(f"only {holdout.get('tasks', 0)} holdout task(s), need {MIN_HOLDOUT_TASKS}")
    if holdout.get("spec_score", 0) < MIN_SPEC_SCORE:
        reasons.append(f"spec score {holdout.get('spec_score', 0):.3f} below {MIN_SPEC_SCORE}")
    if holdout.get("machine_fraction", 0) < MIN_MACHINE_FRACTION:
        reasons.append(f"only {holdout.get('machine_fraction', 0):.0%} of rules machine-decidable, "
                       f"need {MIN_MACHINE_FRACTION:.0%}")
    if holdout.get("worst_task", 0) < MIN_WORST_TASK:
        reasons.append(f"worst task scored {holdout.get('worst_task', 0):.3f}, below {MIN_WORST_TASK} "
                       f"- one collapse, hidden by the mean")
    if reasons:
        return {"trusted": False, "why": "; ".join(reasons),
                "means": "read every rule before acting on a spec from this builder"}
    return {"trusted": True,
            "why": (f"holdout spec score {holdout['spec_score']:.3f} over {holdout['tasks']} tasks, "
                    f"{holdout['machine_fraction']:.0%} machine-decidable, worst task "
                    f"{holdout['worst_task']:.3f}"),
            "means": ("its SPECS can be acted on without reading every line. It does NOT mean anything "
                      "it builds is correct - only a build whose own checks pass is that.")}


def run_bench(splits=("calibrate", "holdout"), drafter: Optional[Callable] = None,
              record: bool = True) -> dict:
    results = {split: bench(split, drafter=drafter) for split in splits}
    out = {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
           "tasks_version": TASKS_VERSION, "splits": results, "places_orders": False}
    if "holdout" in results:
        out["trust"] = trust(results["holdout"])
    if record:
        try:
            target = Path(smartentry_data_dir()) / BENCH_FILE
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(out, indent=1), encoding="utf-8")
            out["saved_to"] = str(target)
        except OSError as exc:
            out["record_error"] = f"{type(exc).__name__}: {exc}"
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Measure the builder's specification quality.")
    parser.add_argument("--split", choices=("calibrate", "holdout", "both"), default="both")
    args = parser.parse_args(argv)
    splits = ("calibrate", "holdout") if args.split == "both" else (args.split,)

    out = run_bench(splits)
    for name, result in out["splits"].items():
        print(f"\n{name}  ({result['tasks']} tasks, benchmark v{result['tasks_version']}, "
              f"provider {result.get('provider')})")
        for row in result["rows"]:
            missed = ("missed: " + ", ".join(row["missed"])) if row["missed"] else "all boundaries covered"
            print(f"  {row['task']:<16} score {row['score']:.3f}  rules {row['rules']:>2} "
                  f"({row['with_check']} checked, {row['machine_fraction']:.0%} machine)  {missed}"
                  + (f"  ERROR {row['error'][:40]}" if row.get("error") else ""))
        print(f"  -> spec_score {result['spec_score']:.4f}  worst {result['worst_task']:.3f}  "
              f"machine {result['machine_fraction']:.0%}")

    if "trust" in out:
        verdict = out["trust"]
        print(f"\nTRUSTED: {'yes' if verdict['trusted'] else 'NO'} - {verdict['why']}")
        print(f"  {verdict['means']}")
        # Read by src.self_improvement, which settles a prediction against these lines.
        holdout = out["splits"]["holdout"]
        print(f"\nMETRIC spec_score = {holdout['spec_score']}")
        print(f"METRIC machine_fraction = {holdout['machine_fraction']}")
        print(f"METRIC worst_task = {holdout['worst_task']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
