"""i40 Pilot layer 6 - the orchestration design prompt, rendered from the system's own live state.

    python -m src.orchestration prompt                # print it
    python -m src.orchestration prompt --budget 8192  # fitted to a local model's window
    python -m src.orchestration ask                   # send it to whichever provider answers now
    python -m src.orchestration facts                 # just the measured numbers it carries

WHY THIS IS CODE AND NOT A MARKDOWN FILE
----------------------------------------
Every fact in this prompt is read at render time. A design prompt written once as a document starts
drifting the same afternoon: it would still claim 14.3 % loop coverage after the coverage moved, still
name a provider that has since been installed, still describe a gap that has been closed. A model handed
stale facts designs for a system that no longer exists, and that design looks reasonable - which is worse
than one that is obviously wrong.

It is also the honest lesson of today: several layers of this system turned out to be instruments with
nothing plugged into them. A prompt saved to `docs/` and never loaded would be one more.

WHAT ORCHESTRATION MEANS HERE, STATED PLAINLY
---------------------------------------------
Right now the orchestrator of this system is **Windows Task Scheduler** - a clock. A clock can start
things at a time. It cannot rank two jobs, react to an event, notice that one job is starving another of
memory, or decline to run something whose answer nothing is waiting for. On 26 September two demo trading
loops were killed mid-run while heavy research ran beside them, and a clock has no way to know that
mattered.

So the design question is not "add an agent". It is: what chooses the next action, with what budget, under
whose authority, and who verifies it happened.
"""
from __future__ import annotations

import argparse
import json
from typing import Optional

TASK = ("Design the orchestration layer of the i40 Pilot trading system: the component that decides "
        "WHAT the system does next, with what budget, under which authority tier, and how the result is "
        "verified and recorded.")

# Stated before the work. Each item is checkable by reading the design, not by liking it.
ACCEPTANCE = """The design is accepted only if all of these are true of it:
1. It names the SINGLE place the next action is chosen, and what happens when two actions want the same
   resource (CPU, RAM, the MT5 terminal, the model's tokens).
2. Every action it can dispatch carries a budget with a number on it, and a stated behaviour when that
   budget is exceeded - which is never 'silently do less'.
3. No action it can dispatch places, modifies or closes an order without passing the existing execution
   guard and the tier gate; the design says which tier each action sits in.
4. Every dispatched action ends by writing a closure record through src.loop_ledger, including when it
   crashes or is killed. The design says how a killed action is told apart from one that had nothing to do.
5. It works with NO paid subscription available: it degrades to the local model's window, and says what it
   stops being able to do rather than pretending otherwise.
6. It is additive to what exists. Name the modules it calls; do not propose replacing them.
7. Every claim about the system's current state is cited from the FACTS section, or marked as an
   assumption to check."""

# Deliberately questions rather than a wish list: a design that answers these is a design, and one that
# lists desirable properties is not.
QUESTIONS = [
    "What is the unit of work? A loop, a task, a goal, or something smaller - and why that one.",
    "How is the next unit chosen? Name the ranking and what it ranks on. 'Expected new evidence per "
    "minute' is a candidate; say what would make it the wrong one.",
    "Where does a goal that outlives one run live, and what stops it being retried forever?",
    "What can preempt what? A trading decision has a deadline the market sets; research does not.",
    "How does the orchestrator know a unit of work FINISHED rather than was killed? (See the fact below: "
    "two demo loops were killed mid-run and left no trace until closure records were wired in.)",
    "What does it do when the only available model has an 8,192-token window?",
    "Which actions need the owner's approval, and how is an approval scoped so it does not leak into the "
    "next action?",
    "How is the orchestrator itself measured? If it cannot say what it changed, it is another clock.",
]

OUTPUT_SHAPE = """Answer in this order, with these headings, and nothing else:

1. UNIT OF WORK - one paragraph, and the reason for the choice.
2. THE CHOOSING RULE - the ranking, written as a formula or as explicit ordered rules.
3. BUDGETS - a table: action kind | wall-clock | memory | tokens | what happens when exceeded.
4. AUTHORITY - a table: action kind | tier T0-T3 | what gates it | who may approve.
5. CLOSURE - how each action reports, and how a kill is told apart from a quiet run.
6. DEGRADED MODE - what the orchestrator does with only the local model, and what it stops doing.
7. WHAT IT CALLS - the existing modules, named, with the function or endpoint used.
8. FIRST SLICE - the smallest version worth running, and the one measurement that would show it beat the
   clock it replaces.
9. WHAT WOULD MAKE THIS DESIGN WRONG - two concrete conditions, each cheap to check."""

IDENTITY = """You are designing one layer of a system that already exists, runs on one Windows PC, and
trades real and demo MetaTrader accounts with the owner's money. It is not a greenfield project and it has
no team: every part must be operable and debuggable by one person reading a log.

HARD RULES, which the design may not trade away:
- Nothing places, modifies or closes an order except through the existing execution guard, and nothing
  bypasses the tier gates or the owner's approval.
- Evidence gates money, never learning: a strategy may always keep trading and recording, and only the
  decision to give it money waits on proof.
- The system must keep working with no paid AI subscription. The local model's window is the floor to
  design against, not an afterthought.
- Never invent a number. A fact that cannot be read is reported as unavailable."""


# One render probes the network (is Claude reachable?), asks Ollama for its models and rebuilds the whole
# AI-employee context from disk. Doing that twice in the same minute answers the same question twice, so the
# reads are cached for FRESH_SECONDS and `refresh=True` forces them. Short, because these facts change while
# the system runs - the coverage number moved three times in one afternoon while this was being written.
FRESH_SECONDS = 60
_CACHE: dict = {"at": 0.0, "facts": None}


def facts(refresh: bool = False) -> dict:
    """The measured state this prompt carries. Read live; nothing here is written down by hand."""
    import time

    if not refresh and _CACHE["facts"] is not None and time.monotonic() - _CACHE["at"] < FRESH_SECONDS:
        return _CACHE["facts"]
    out: dict = {}

    from .loop_ledger import KNOWN_LOOPS, closure_report
    report = closure_report()
    out["loops"] = {
        "known": len(KNOWN_LOOPS),
        "reporting": report["reporting"],
        "coverage_pct": report["coverage_pct"],
        "states": {row["loop"]: row["state"] for row in report["loops"]},
        "rule": report["rule"],
    }

    from . import ai_provider
    found = []
    for provider in ai_provider.providers():
        ok, why = provider.available()
        found.append({"name": provider.describe().get("name"), "available": ok,
                      "context_tokens": provider.context_tokens(), "note": why})
    out["providers"] = found
    chosen = ai_provider.choose()
    out["provider_now"] = {"name": chosen.describe().get("name") if chosen else None,
                           "context_tokens": chosen.context_tokens() if chosen else 0}

    from .ai_employee import CLAUDE_CONTEXT_TOKENS, build_employee_context, prompt_for_budget
    try:
        context = build_employee_context()
        budget = out["provider_now"]["context_tokens"] or CLAUDE_CONTEXT_TOKENS
        _, shape = prompt_for_budget(context, budget)
        out["daily_review_prompt"] = {k: shape[k] for k in ("shape", "budget_tokens", "prompt_tokens")}
    except Exception as exc:                      # noqa: BLE001 - a fact that cannot be read is reported
        out["daily_review_prompt"] = {"unavailable": f"{type(exc).__name__}: {exc}"[:200]}

    try:
        from .strategy_confirm import confirm_status
        status = confirm_status()
        out["confirmation"] = {key: status[key] for key in ("slots", "registered", "confirmed", "floor")
                               if key in status}
    except Exception as exc:                      # noqa: BLE001
        out["confirmation"] = {"unavailable": f"{type(exc).__name__}: {exc}"[:200]}

    import time

    _CACHE.update({"at": time.monotonic(), "facts": out})
    return out


def facts_lines(found: dict) -> list:
    """One fact per line, each naming where it came from, so the design can cite it."""
    lines = []
    loops = found.get("loops") or {}
    if loops:
        lines.append(f"loop_ledger: {loops['reporting']} of {loops['known']} known loops write a closure "
                     f"record ({loops['coverage_pct']}% coverage)")
        silent = [name for name, state in (loops.get("states") or {}).items() if state == "SILENT"]
        if silent:
            lines.append("loop_ledger: still SILENT (wired, not yet run since): " + ", ".join(sorted(silent)))
        lines.append("loop_ledger rule: " + str(loops.get("rule")))
    for row in found.get("providers") or []:
        state = "available" if row["available"] else "NOT available"
        lines.append(f"ai_provider: {row['name']} - {state}, window {row['context_tokens']:,} tokens "
                     f"({row['note']})")
    now = found.get("provider_now") or {}
    if now.get("name"):
        lines.append(f"ai_provider.choose(): {now['name']} would answer right now, window "
                     f"{now['context_tokens']:,} tokens")
    review = found.get("daily_review_prompt") or {}
    if "shape" in review:
        lines.append(f"ai_employee: today's review prompt is {review['prompt_tokens']:,} tokens, sent as "
                     f"'{review['shape']}' into a {review['budget_tokens']:,}-token budget")
    elif review.get("unavailable"):
        lines.append(f"ai_employee: prompt shape could not be read ({review['unavailable']})")
    confirmation = found.get("confirmation") or {}
    if confirmation and "unavailable" not in confirmation:
        lines.append("strategy_confirm: " + ", ".join(f"{k} {v}" for k, v in confirmation.items()))
    lines.append("scheduler: the current orchestrator is Windows Task Scheduler - a clock, with no ranking, "
                 "no preemption and no resource accounting")
    lines.append("history, 26 Sep 2026: two demo trading loops were killed mid-run (exit 0xC000013A) while "
                 "heavy research ran beside them, and left no trace at all until closure records were wired")
    return lines


def design_prompt(budget_tokens: Optional[int] = None, refresh: bool = False) -> dict:
    """The orchestration design prompt, fitted to ``budget_tokens`` if one is given.

    Returns ``{"prompt": str|None, "shape": dict}``. With a budget the assembly goes through layer 2
    (`context_builder`), so what had to be left out is stated rather than quietly trimmed - and if the
    rules and the task alone will not fit, it is REFUSED rather than shortened.
    """
    found = facts(refresh=refresh)
    fact_lines = facts_lines(found)
    if budget_tokens is None:
        from .ai_provider import estimate_tokens
        body = "\n".join([
            IDENTITY, "",
            "TASK", TASK, "",
            "ANSWER THESE", *[f"- {q}" for q in QUESTIONS], "",
            "FACTS (measured from the running system just now; cite these)",
            *[f"- {line}" for line in fact_lines], "",
            "DONE WHEN", ACCEPTANCE, "",
            OUTPUT_SHAPE,
        ])
        return {"prompt": body, "facts": found,
                "shape": {"shape": "full", "budget_tokens": None, "ok": True, "omitted": [],
                          "prompt_tokens": estimate_tokens(body)}}

    from .context_builder import brief_for_task
    brief = brief_for_task(TASK, budget_tokens=int(budget_tokens), identity=IDENTITY, acceptance=ACCEPTANCE,
                           working=[f"ANSWER: {q}" for q in QUESTIONS] + OUTPUT_SHAPE.splitlines(),
                           episodic=fact_lines)
    shape = {"shape": "brief", "budget_tokens": int(budget_tokens), "prompt_tokens": brief.prompt_tokens,
             "ok": bool(brief.ok), "omitted": brief.omitted, "note": brief.reason}
    return {"prompt": brief.prompt if brief.ok else None, "facts": found, "shape": shape}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="The orchestration design prompt, rendered from live state.")
    sub = parser.add_subparsers(dest="command", required=False)
    show = sub.add_parser("prompt", help="print the prompt")
    show.add_argument("--budget", type=int, default=None, help="tokens the answering model has")
    ask = sub.add_parser("ask", help="send it to whichever provider answers now and print the reply")
    ask.add_argument("--budget", type=int, default=None)
    sub.add_parser("facts", help="print only the measured facts it carries")
    args = parser.parse_args(argv)
    command = args.command or "prompt"

    if command == "facts":
        print(json.dumps(facts(refresh=True), indent=1, default=str))
        return 0

    built = design_prompt(getattr(args, "budget", None), refresh=True)
    if built["prompt"] is None:
        print("REFUSED: " + str(built["shape"].get("note")))
        return 1

    if command == "prompt":
        print(built["prompt"])
        for row in built["shape"]["omitted"]:
            print(f"\n[left out of {row['slot']}: kept {row['kept']} of {row['available']}]")
        return 0

    from . import ai_provider
    result = ai_provider.complete(built["prompt"], task="orchestration design")
    if not result.get("ok"):
        print("no answer: " + str(result.get("error"))[:300])
        return 1
    print(result.get("text") or "")
    return 0


if __name__ == "__main__":
    # No closure record here on purpose. This renders a prompt on demand; it is a tool, not a loop, and
    # `loop_ledger.KNOWN_LOOPS` is what the closure report reads - a record under a name that is not in it
    # would be written and never read, which is the exact "instrument with nothing plugged in" failure this
    # module's docstring is about.
    raise SystemExit(main())
