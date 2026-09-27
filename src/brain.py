"""i40 Pilot layer 3 — Brain: plan, then criticise the plan. The last missing piece of layer 3.

    goal -> restate -> retrieve evidence -> plan (<=7 steps, each with an acceptance test)
         -> critique against known failure modes -> carry out -> verify -> record

Layers 0-2 and 4-7 already exist. What did not exist is the thing that walks a goal through them, and
`docs/I40_PILOT_ARCHITECTURE.md` names it exactly: *"the plan-and-execute loop itself"*.

NOT TO BE CONFUSED WITH `jarvis_autonomous_brain.py`
----------------------------------------------------
That module plans TRADES — asset, stop loss, take profit, position size — and queues them for the
owner's approval. This one plans WORK: the steps of a piece of research or engineering, each with a
test that decides whether the step actually succeeded. The names are close and the jobs are unrelated.

WHY THE MODEL WRITES THE PLAN AND THE SYSTEM ENFORCES IT
--------------------------------------------------------
The architecture's one binding constraint is that this must run offline on a small local model, which
reasons worse and forgets instructions. So the thesis is *put the competence in the SYSTEM, not the
model*, and here that splits cleanly:

* **The model supplies** the restatement and the step text — language and judgement.
* **The system supplies** everything that must be correct: the step ceiling, the rule that every step
  declares a test, the critique drawn from a register of what has actually gone wrong here, the tier
  check before anything acts, the adjudication of each test, and the refusal to report success when
  nothing independent passed.

A plan is therefore REFUSED on structure before a single step runs. That is the point: a weak model
producing a sloppy plan gets a specific complaint back, not a half-executed piece of work.

WHY SEVEN
---------
`MAX_STEPS` is 7 because the register's own history is of plans that grew until nobody re-read the
top of them. A goal needing more than seven steps is two goals, and saying so is more useful than
executing eleven.

THE PLAN FORMAT IS DELIBERATELY TINY
------------------------------------
    goal: what we are actually trying to settle
    step: the thing to do
      tier: T1
      run: python -m pytest -q tests/test_thing.py
      number: trades >= 100
    step: the next thing
      appended: .claude/memory/BASELINE.md
      ask: did the inverse control actually trade and lose?

Small enough for a 7B model to emit reliably, and the check lines are the SAME grammar the skills use
(`skill_acceptance.CHECK`) rather than a second one invented here. One grammar means a check that
works in a skill works in a plan, and there is one place to fix when it does not.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from . import failure_modes, governance, skill_acceptance

MAX_STEPS = 7

# A goal is answerable when it names something that can come back FALSE. These are matched as whole
# words (see `restate`), which is why "optimise" is spelled out both ways instead of clipped to
# "optimi" - a prefix is a substring by another name.
DECIDABLE = ("does", "do", "is", "are", "which", "how many", "how much", "what is", "whether",
             "prove", "proves", "measure", "count", "compare", "beat", "beats", "test", "tests",
             "verify", "check", "equals", "matches", "survives")
VAGUE = ("better", "improve", "improved", "optimise", "optimize", "enhance", "maximise", "maximize",
         "tidy", "cleanup", "faster", "stronger", "best")

GOAL_LINE = re.compile(r"^\s*goal\s*:\s*(.+?)\s*$", re.I)
STEP_LINE = re.compile(r"^\s*step\s*:\s*(.+?)\s*$", re.I)
TIER_LINE = re.compile(r"^\s*tier\s*:\s*(T[0-3])\s*$", re.I)

# The format asked for, verbatim, so a refusal can hand the writer the shape instead of a complaint.
FORMAT_HELP = (
    "goal: <what is being settled>\n"
    "step: <what to do>\n"
    "  tier: T0|T1|T2|T3        (optional; the action text is classified anyway)\n"
    "  run: <command>           | file: <path> | number: <name> >= <value>\n"
    "  appended: <path>         | ask: <question only the owner can answer>\n"
    "step: <the next thing>\n"
    "  ...\n"
    f"At most {MAX_STEPS} steps, and EVERY step needs at least one check line."
)


@dataclass
class Step:
    """One step of a plan, and the test that decides whether it worked.

    `checks` are `skill_acceptance.Check` objects — the same type the skills use, so `run_checks` and
    the four automatic kinds are inherited rather than reimplemented.
    """

    number: int
    what: str
    checks: List[skill_acceptance.Check] = field(default_factory=list)
    declared_tier: Optional[str] = None
    decision: Optional[governance.Decision] = None
    state: str = "pending"                 # pending | refused | done | failed | unproven
    detail: str = ""
    # The tier of what the step DESCRIBES, which is not the same as the tier of what it executes.
    prose_tier: str = ""
    needs_owner: bool = False

    @property
    def declared(self) -> bool:
        return bool(self.checks)

    @property
    def has_automatic(self) -> bool:
        """At least one check something other than the model or the owner can settle."""
        return any(c.automatic for c in self.checks)

    @property
    def executes(self) -> List[skill_acceptance.Check]:
        """The checks that actually run a command. Everything else only compares or reads."""
        return [c for c in self.checks if c.kind == "run"]

    def as_dict(self) -> dict:
        return {"number": self.number, "what": self.what, "state": self.state, "detail": self.detail,
                "declared_tier": self.declared_tier, "prose_tier": self.prose_tier,
                "needs_owner": self.needs_owner,
                "decision": self.decision.as_dict() if self.decision else None,
                "checks": [{"kind": c.kind, "spec": c.spec, "passed": c.passed, "detail": c.detail}
                           for c in self.checks]}


@dataclass
class Plan:
    """A goal restated, the steps to settle it, and every reason it might be wrong.

    `refusals` is not an error list to be cleared — it is the plan's own account of why it is not fit
    to run. A plan with refusals is never carried out.
    """

    goal: str
    restated: str = ""
    steps: List[Step] = field(default_factory=list)
    refusals: List[str] = field(default_factory=list)
    critique: dict = field(default_factory=dict)
    evidence: List[str] = field(default_factory=list)
    citations: List[str] = field(default_factory=list)

    @property
    def sound(self) -> bool:
        """Structurally fit to run. Says nothing about whether it will succeed."""
        return not self.refusals and bool(self.steps)

    def text(self) -> str:
        """The whole plan as one string — what the critique pass reads."""
        lines = [f"goal: {self.restated or self.goal}"]
        for step in self.steps:
            lines.append(f"step: {step.what}")
            lines += [f"  {c.kind}: {c.spec}" for c in step.checks]
        return "\n".join(lines)

    def as_dict(self) -> dict:
        return {"goal": self.goal, "restated": self.restated, "sound": self.sound,
                "refusals": list(self.refusals), "steps": [s.as_dict() for s in self.steps],
                "critique": self.critique,
                "evidence": list(self.evidence), "citations": list(self.citations)}


# ----------------------------------------------------------------- restate and retrieve


def restate(goal: str) -> dict:
    """Is this goal answerable, and what would settle it?

    Not a rewording. The question is whether the goal names something that can come back FALSE. "make
    the strategy better" cannot; "does sweep_reclaim short beat buy-and-hold on the holdout" can. The
    register's own history is full of work that ran for a day against a goal that had no failing case,
    and a goal like that cannot be planned — only elaborated.
    """
    text = (goal or "").strip()
    if not text:
        return {"restated": "", "answerable": False,
                "why": "no goal was given, so there is nothing to plan"}

    # Deliberately shallow and readable. A local model can be asked to reword a goal; it must not be
    # trusted to decide whether the goal is falsifiable, because that is the judgement it is worst at
    # and the one that costs a whole day when it is wrong.
    #
    # WHOLE WORDS, not substrings. "improve the gold model" contains "prove", which made the vaguest
    # goal in the set read as the most answerable one. `governance.tier_for` carries the same scar from
    # the other direction - "rm " fired inside "one arm was" and sent a harmless note to T3 - so the
    # rule is written the same way here: a term matches a word, never a fragment of one.
    low = text.lower()
    words = set(re.findall(r"[a-z]+", low))

    def _present(term: str) -> bool:
        parts = term.split()
        return all(w in words for w in parts) if len(parts) > 1 else term in words

    hits = [w for w in DECIDABLE if _present(w)]
    soft = [w for w in VAGUE if _present(w)]

    if not hits and soft:
        return {"restated": text, "answerable": False,
                "why": (f"'{soft[0]}' names a direction, not an outcome, so no result could come back "
                        "false. Restate it as a question with a number or an artefact in it."),
                "matched_vague": soft}
    return {"restated": text, "answerable": True,
            "why": ("names something that can fail" if hits else
                    "no vague direction found; treat as answerable but say what would settle it"),
            "matched_decidable": hits}


def evidence_for(goal: str, budget_tokens: Optional[int] = None,
                 rows: Optional[list] = None) -> dict:
    """What the system already knows about this goal, each line carrying its source.

    Goes through layer 2 (`context_builder`) rather than reading the record directly, so the budget,
    the cite-or-omit rule and the depth-scales-with-the-window behaviour all apply here too. A brain
    that assembled its own context would be a second context layer with its own quiet rules.
    """
    from . import context_builder

    if budget_tokens is None:
        try:
            budget_tokens = context_builder.budget_for_provider() or 32_000
        except Exception:                            # noqa: BLE001 - layer 0 may be absent offline
            budget_tokens = 32_000
    try:
        limit, excerpt = context_builder.depth_for(budget_tokens)
        lines, citations = context_builder.evidence_lines(goal, limit=limit, rows=rows,
                                                          excerpt=excerpt)
    except Exception as exc:                         # noqa: BLE001
        return {"available": False, "reason": f"the record could not be read: {exc}",
                "lines": [], "citations": []}
    return {"available": True, "budget_tokens": budget_tokens,
            "lines": list(lines), "citations": list(citations), "count": len(lines)}


# ----------------------------------------------------------------- plan


def parse_plan(text: str, goal: str = "") -> Plan:
    """Read a plan in the declared format and refuse it on structure before anything runs.

    Every refusal names the step it came from. A plan sent back as "invalid" teaches a weak model
    nothing; "step 3 declares no check" is actionable on the next attempt.
    """
    plan = Plan(goal=goal or "")
    current: Optional[Step] = None

    for raw in (text or "").splitlines():
        if not raw.strip() or raw.strip().startswith("#"):
            continue
        found = GOAL_LINE.match(raw)
        if found and current is None:
            plan.restated = found.group(1)
            continue
        found = STEP_LINE.match(raw)
        if found:
            current = Step(number=len(plan.steps) + 1, what=found.group(1))
            plan.steps.append(current)
            continue
        found = TIER_LINE.match(raw)
        if found and current is not None:
            current.declared_tier = found.group(1).upper()
            continue
        found = skill_acceptance.CHECK.match(raw)
        if found and current is not None:
            current.checks.append(skill_acceptance.Check(kind=found.group(1).lower(),
                                                         spec=found.group(2)))
            continue
        if current is not None:
            current.what = f"{current.what} {raw.strip()}".strip()

    if not plan.goal:
        plan.goal = plan.restated

    if not plan.steps:
        plan.refusals.append("no steps were declared, so there is nothing to carry out")
    if len(plan.steps) > MAX_STEPS:
        plan.refusals.append(
            f"{len(plan.steps)} steps, and the ceiling is {MAX_STEPS}. A goal needing more than "
            f"{MAX_STEPS} steps is two goals; split it rather than running eleven.")
    for step in plan.steps:
        if not step.declared:
            plan.refusals.append(
                f"step {step.number} ({step.what[:60]!r}) declares no check, so its success would "
                "rest on the model's own say-so")
    if plan.steps and not any(s.has_automatic for s in plan.steps):
        plan.refusals.append(
            "no step carries a check that can be settled without asking someone. A plan made "
            "entirely of questions cannot be run unattended.")

    said = restate(plan.restated or plan.goal)
    if not said["answerable"]:
        plan.refusals.append(f"the goal is not answerable: {said['why']}")
    return plan


def critique_plan(plan: Plan, limit: int = 5) -> dict:
    """What would make this plan wrong, asked BEFORE it runs, from the register of what already has.

    Delegates to `failure_modes.critique`, which raises only modes whose trigger words appear — a
    critique that fires on everything gets skimmed and then ignored. The value added here is the
    per-step view: knowing WHICH step a mode lands on is what makes the warning actionable.
    """
    whole = failure_modes.critique(plan.text(), limit=limit)
    per_step = []
    for step in plan.steps:
        found = failure_modes.critique(step.what, limit=2)
        if found["count"]:
            per_step.append({"step": step.number,
                             "modes": [m["id"] for m in found["applies"]],
                             "checks_first": found["checks_first"]})
    return {**whole, "per_step": per_step,
            "steps_with_a_known_failure_mode": len(per_step),
            "steps_total": len(plan.steps)}


def make_plan(goal: str, text: str, *, budget_tokens: Optional[int] = None,
              rows: Optional[list] = None) -> Plan:
    """Restate, retrieve, parse and critique — everything up to the first action, and none of it."""
    plan = parse_plan(text, goal=goal)
    found = evidence_for(plan.restated or goal, budget_tokens=budget_tokens, rows=rows)
    plan.evidence, plan.citations = found.get("lines", []), found.get("citations", [])
    plan.critique = critique_plan(plan)
    return plan


# ----------------------------------------------------------------- carry out and verify


def verify_step(step: Step, *, values: Optional[dict] = None, allow_run: bool = False,
                runner: Optional[Callable] = None) -> Step:
    """Adjudicate one step's checks and set its state from the result, never from the step text.

    `allow_run` is off by default for the same reason `skill_acceptance` has it off: executing a
    command a plan names is a real action, and layer 7 decides whether that is permitted. A check that
    was not run is `None`, which is reported as UNPROVEN — never as a pass.
    """
    stand_in = skill_acceptance.SkillAcceptance(name=f"step {step.number}", path="", checks=step.checks)
    skill_acceptance.run_checks(stand_in, values, allow_run=allow_run, runner=runner)
    verdict = skill_acceptance.may_report_success(stand_in)

    if verdict["may_report_success"]:
        step.state = "done"
    elif any(c.passed is False for c in step.checks):
        step.state = "failed"
    else:
        step.state = "unproven"
    step.detail = verdict["reason"]
    return step


def carry_out(plan: Plan, *, values: Optional[dict] = None, allow_run: bool = False,
              runner: Optional[Callable] = None, approval: Optional[dict] = None,
              capability: str = "brain", base=None) -> dict:
    """Walk the steps: tier check, then adjudicate, and STOP at the first step that fails.

    Two rules that make this different from a loop over a list:

    * **Every step passes through layer 7 first.** `check_permission` classifies the step's own text,
      so a step that reads harmlessly but names an outward action is caught by the tier rules rather
      than by whoever is reading the plan.
    * **A failed step stops the plan.** Carrying on past a failure produces later steps whose results
      were computed on a broken foundation, and those are worse than no results — they look fine.

    An UNPROVEN step does not stop the plan, because `allow_run=False` makes unproven the normal state
    of a dry pass. It does stop the plan being reported as done; `may_report_done` sees to that.
    """
    if not plan.sound:
        return {"ran": False, "reason": "the plan was refused on structure and was not carried out",
                "refusals": list(plan.refusals), "steps": [s.as_dict() for s in plan.steps]}

    done, refused, failed, unproven, needs_owner = [], [], [], [], []
    for step in plan.steps:
        # THE GATE IS ON WHAT EXECUTES, NOT ON WHAT IS DESCRIBED.
        #
        # `carry_out` can do exactly one outward thing: run a `run:` check's command. Everything else
        # compares a number, reads a file's date or asks the owner. So gating the step's PROSE refused
        # work that acts on nothing - "count the closed trades" classifies T3 because "trades" contains
        # "trade" - and a gate that refuses a read teaches the owner to grant blanket approvals, which
        # is strictly more dangerous than the thing it was protecting against.
        #
        # The prose tier is still computed, still recorded and still surfaced: a step DESCRIBING T2+
        # work is flagged `needs_owner`, because a human or a model will carry that description out
        # somewhere this function cannot see. Flagged, not silently allowed, and not refused either.
        step.prose_tier = governance.tier_for(step.what, step.declared_tier)
        step.needs_owner = step.prose_tier in (governance.T2_OUTWARD, governance.T3_CRITICAL)
        if step.needs_owner:
            needs_owner.append(step.number)

        if allow_run and step.executes:
            # The command is what runs, so the command is what is classified - with the prose tier as a
            # floor, so a step that reads as T3 cannot smuggle a command through at T1.
            worst = max((governance.check_permission(
                c.spec, declared_tier=step.prose_tier, approval=approval,
                capability=capability, base=base) for c in step.executes),
                key=lambda d: (d.allowed, governance.TIER_ORDER.index(d.tier)))
            decision = worst if worst.allowed else next(
                governance.check_permission(c.spec, declared_tier=step.prose_tier, approval=approval,
                                            capability=capability, base=base)
                for c in step.executes)
        else:
            decision = governance.Decision(
                True, step.prose_tier,
                "nothing in this step executes; its checks only read, compare or ask"
                + (f" (the step DESCRIBES {step.prose_tier} work, which needs the owner)"
                   if step.needs_owner else ""))

        step.decision = decision
        if not decision.allowed:
            step.state, step.detail = "refused", decision.reason
            refused.append(step.number)
            break                       # a refused step means the plan cannot continue as written

        verify_step(step, values=values, allow_run=allow_run, runner=runner)
        {"done": done, "failed": failed, "unproven": unproven}[step.state].append(step.number)
        if step.state == "failed":
            break

    reached = len(done) + len(failed) + len(unproven) + len(refused)
    return {"ran": True, "steps_total": len(plan.steps), "steps_reached": reached,
            "done": done, "failed": failed, "unproven": unproven, "refused": refused,
            "needs_owner": needs_owner,
            "stopped_early": reached < len(plan.steps),
            "allow_run": allow_run,
            "steps": [s.as_dict() for s in plan.steps]}


def may_report_done(plan: Plan) -> dict:
    """May this plan be reported as done? The plan-level twin of `may_report_success`.

    Only when EVERY step reached `done`. Unproven is not done, a refused step is not done, and a plan
    that stopped early is certainly not done. This function exists because the tempting summary —
    "5 of 7 steps passed" — reads like progress and hides that the goal is unsettled.
    """
    if not plan.sound:
        return {"may_report_done": False, "reason": "the plan was refused on structure",
                "refusals": list(plan.refusals)}
    states = {s.number: s.state for s in plan.steps}
    not_done = {n: st for n, st in states.items() if st != "done"}
    if not_done:
        worst = ", ".join(f"step {n} {st}" for n, st in list(not_done.items())[:4])
        return {"may_report_done": False,
                "reason": f"{len(not_done)} of {len(states)} steps are not done: {worst}",
                "states": states}
    return {"may_report_done": True,
            "reason": f"all {len(states)} steps passed a check adjudicated by something else",
            "states": states}


# ----------------------------------------------------------------- the whole loop


def run_goal(goal: str, plan_text: str, *, values: Optional[dict] = None, allow_run: bool = False,
             runner: Optional[Callable] = None, approval: Optional[dict] = None,
             budget_tokens: Optional[int] = None, rows: Optional[list] = None,
             record: bool = True) -> dict:
    """goal -> restate -> evidence -> plan -> critique -> carry out -> verify -> RECORD.

    The record is not optional in spirit: layer 4's rule is that no loop may finish without writing
    what it measured, and a brain that plans and then leaves no trace is the exact thing the loop
    ledger exists to make visible. `record=False` is for tests, which must not write to the ledger.
    """
    plan = make_plan(goal, plan_text, budget_tokens=budget_tokens, rows=rows)
    outcome = carry_out(plan, values=values, allow_run=allow_run, runner=runner, approval=approval)
    verdict = may_report_done(plan)
    result = {"goal": goal, "plan": plan.as_dict(), "outcome": outcome, "verdict": verdict}

    if not record:
        return result

    from .loop_ledger import record_closure

    measured = {"steps": len(plan.steps), "done": len(outcome.get("done") or []),
                "failed": len(outcome.get("failed") or []),
                "unproven": len(outcome.get("unproven") or []),
                "refused": len(outcome.get("refused") or []),
                "refusals": len(plan.refusals),
                "failure_modes_raised": plan.critique.get("count", 0),
                "evidence_lines": len(plan.evidence)}
    record_closure(
        "brain", kind="goal", observed=f"goal: {goal[:120]}",
        decided=(f"plan of {len(plan.steps)} steps" if plan.sound
                 else f"refused: {plan.refusals[0][:120] if plan.refusals else 'unsound'}"),
        # `acted` is True only if a step actually changed something. A dry pass observes; saying it
        # acted would put a false entry in the one ledger built to answer "did anything change?".
        acted=bool(allow_run and outcome.get("done")),
        measured=measured, acceptance_passed=verdict["may_report_done"],
        note=verdict["reason"][:300])
    return result


if __name__ == "__main__":       # pragma: no cover - a hand check, not a test
    import sys

    the_goal = " ".join(sys.argv[1:]) or "does sweep_reclaim short beat buy-and-hold on the holdout"
    example = (
        f"goal: {the_goal}\n"
        "step: run the engine's known-answer tests before trusting any number it produces\n"
        "  run: python -m pytest -q tests/test_engine_truth.py\n"
        "step: measure the holdout and record it with its date\n"
        "  appended: .claude/memory/BASELINE.md\n"
        "  number: closed_trades >= 100\n"
        "step: run the inverse as a control\n"
        "  ask: did the inverse actually TRADE and LOSE?\n"
    )
    print(json.dumps(run_goal(the_goal, example, record=False), indent=1, default=str))
