"""Layer 3's guarantees, each checked against an answer known before the test runs.

The brain's whole value is that it REFUSES things, so most of these assert a refusal. A planner that
accepts every plan is a text formatter; what makes this layer 3 is that a plan missing its tests, a
goal with no failing case, and a run where nothing was independently adjudicated all come back as
"no", with a reason naming the step.

Nothing here touches the network, the broker or the model provider. Every governance check runs against
a `tmp_path` sandbox so a halt or dry-run file on this machine cannot change a result, and no `run:`
check is ever executed — `allow_run` stays off except where a stub runner is passed in.
"""
import json

import pytest

from src import brain, governance, skill_acceptance


# --------------------------------------------------------------------------- refusals on structure

def test_a_step_with_no_check_is_refused_and_the_step_is_named():
    """The rule the whole layer rests on: success may not rest on the model's own say-so."""
    plan = brain.parse_plan(
        "goal: does the gold model have an edge\n"
        "step: measure the holdout\n"
        "  number: closed_trades >= 100\n"
        "step: decide whether it is good\n")
    assert plan.sound is False
    assert any("step 2" in r and "declares no check" in r for r in plan.refusals), plan.refusals


def test_more_than_seven_steps_is_refused_as_two_goals():
    text = "goal: does the strategy beat its control\n" + "".join(
        f"step: thing {n}\n  number: x >= {n}\n" for n in range(1, 9))
    plan = brain.parse_plan(text)
    assert plan.sound is False
    assert any("8 steps" in r and "ceiling is 7" in r for r in plan.refusals), plan.refusals


def test_a_plan_made_entirely_of_questions_cannot_be_run_unattended():
    """`ask` is adjudicated by the owner rather than the model, so it is a legitimate check — but a
    plan of nothing but asks cannot advance without someone sitting there, and saying so beats
    stalling silently."""
    plan = brain.parse_plan(
        "goal: does the sweep have an edge\n"
        "step: look at it\n  ask: does it look right?\n"
        "step: look again\n  ask: still right?\n")
    assert plan.sound is False
    assert any("without asking someone" in r for r in plan.refusals), plan.refusals


def test_an_empty_plan_is_refused_rather_than_treated_as_nothing_to_do():
    plan = brain.parse_plan("goal: does anything work\n")
    assert plan.sound is False
    assert any("no steps" in r for r in plan.refusals), plan.refusals


def test_a_sound_plan_is_accepted_and_keeps_its_steps_in_order():
    plan = brain.parse_plan(
        "goal: does sweep_reclaim short beat buy-and-hold on the holdout\n"
        "step: measure the holdout and record it\n"
        "  number: closed_trades >= 100\n"
        "step: run the inverse as a control\n"
        "  ask: did the inverse actually TRADE and LOSE?\n")
    assert plan.sound is True and plan.refusals == []
    assert [s.number for s in plan.steps] == [1, 2]
    assert plan.steps[0].checks[0].kind == "number"
    assert plan.steps[1].checks[0].kind == "ask"


# --------------------------------------------------------------------------- the goal itself

@pytest.mark.parametrize("goal", [
    "make the strategy better",
    "improve the gold model",
    "optimise the entry filter",
    "make the entry faster",
])
def test_a_goal_naming_a_direction_rather_than_an_outcome_is_refused(goal):
    """No result could come back false, so there is nothing to plan — only to elaborate."""
    said = brain.restate(goal)
    assert said["answerable"] is False, said
    assert "false" in said["why"] or "outcome" in said["why"]


@pytest.mark.parametrize("goal", [
    "does sweep_reclaim short beat buy-and-hold on the holdout",
    "how many of the 109 trades closed inside their entry bar",
    "verify the ledger entry price equals the simulated fill",
])
def test_a_goal_that_can_come_back_false_is_answerable(goal):
    assert brain.restate(goal)["answerable"] is True


def test_improve_does_not_count_as_prove():
    """'improve' CONTAINS 'prove', which made the vaguest goal in the set read as the most answerable
    one. `governance.tier_for` carries the same scar from the other direction, where 'rm ' fired inside
    'one arm was'. Substring matching on intent words is a recurring defect in this codebase."""
    assert brain.restate("improve the gold model")["answerable"] is False
    assert brain.restate("prove the gold model has an edge")["answerable"] is True


def test_a_measurable_goal_survives_containing_a_vague_word():
    """Refusing anything that mentions 'improved' would reject a perfectly answerable question."""
    assert brain.restate("measure whether the improved model beats the old one")["answerable"] is True


def test_no_goal_at_all_is_not_quietly_treated_as_an_empty_one():
    assert brain.restate("")["answerable"] is False
    assert brain.restate(None)["answerable"] is False


# --------------------------------------------------------------------------- one grammar, not two

def test_a_plan_check_uses_THE_SAME_grammar_as_a_skill_check():
    """If these ever diverge, a check that works in a skill silently stops working in a plan."""
    lines = ["run: python -m pytest -q", "number: trades >= 100",
             "appended: NOTES.md", "ask: is it right?"]
    fenced = "```acceptance\n" + "\n".join(lines) + "\n```"
    from_skill = skill_acceptance.parse(fenced)
    from_plan = brain.parse_plan(
        "goal: does it hold\nstep: do it\n" + "\n".join("  " + line for line in lines)
    ).steps[0].checks
    assert from_skill, "the skill parser must have found the checks in the fenced block"
    assert [(c.kind, c.spec) for c in from_plan] == [(c.kind, c.spec) for c in from_skill]


# --------------------------------------------------------------------------- critique before acting

def test_the_critique_names_which_STEP_carries_a_known_failure_mode():
    """The register already raises modes for a body of text. Knowing the step makes it act-on-able."""
    plan = brain.parse_plan(
        "goal: does the parameter sweep find a real edge\n"
        "step: sweep the parameters and pick the best cell\n  number: closed_trades >= 100\n"
        "step: write it down\n  appended: .claude/memory/BASELINE.md\n")
    found = brain.critique_plan(plan)
    assert found["count"] > 0, "a parameter sweep must raise something from the register"
    assert found["steps_total"] == 2
    assert any(entry["step"] == 1 for entry in found["per_step"]), found["per_step"]
    assert found["checks_first"], "every raised mode carries the check that would have caught it"


# --------------------------------------------------------------------------- carrying out

def _runner(returncode: int):
    """A stand-in for subprocess.run, so a `run:` check is adjudicated without running anything."""
    class _Proc:
        def __init__(self):
            self.returncode, self.stdout, self.stderr = returncode, "stub", ""
    return lambda *a, **k: _Proc()


def _cleared(tmp_path):
    """A governance sandbox with 'brain' taken out of dry run.

    Layer 7 defaults an unknown capability to dry run — unproven means not acting — so without this
    every `allow_run=True` pass is refused. That is correct behaviour and useless for testing the path
    beyond it, so the tests that need to get past it say so explicitly.
    """
    (tmp_path / governance.DRY_RUN_NAME).write_text(json.dumps({"brain": False}), encoding="utf-8")
    return tmp_path


def test_a_failed_step_stops_the_plan_and_later_steps_stay_pending(tmp_path):
    """Carrying on past a failure produces later results computed on a broken foundation, and those
    are worse than none because they look fine."""
    plan = brain.parse_plan(
        "goal: does the engine agree with the ledger\n"
        "step: run the truth tests\n  run: python -m pytest -q tests/test_engine_truth.py\n"
        "step: measure the holdout\n  number: closed_trades >= 100\n")
    out = brain.carry_out(plan, allow_run=True, runner=_runner(1),
                          values={"closed_trades": 500}, base=_cleared(tmp_path))
    assert out["failed"] == [1], out
    assert out["stopped_early"] is True
    assert plan.steps[1].state == "pending", "step 2 must not have been attempted"


def test_a_passing_run_check_lets_the_plan_continue(tmp_path):
    plan = brain.parse_plan(
        "goal: does the engine agree with the ledger\n"
        "step: run the truth tests\n  run: python -m pytest -q tests/test_engine_truth.py\n"
        "step: measure the holdout\n  number: closed_trades >= 100\n")
    out = brain.carry_out(plan, allow_run=True, runner=_runner(0),
                          values={"closed_trades": 500}, base=_cleared(tmp_path))
    assert out["done"] == [1, 2], out
    assert out["stopped_early"] is False


def test_an_unproven_step_does_not_stop_the_plan(tmp_path):
    """With allow_run off, unproven is the normal state of a dry pass — it must not read as failure."""
    plan = brain.parse_plan(
        "goal: does the engine agree with the ledger\n"
        "step: run the truth tests\n  run: python -m pytest -q tests/test_engine_truth.py\n"
        "step: check the closed trade count\n  number: closed_trades >= 100\n")
    out = brain.carry_out(plan, allow_run=False, values={"closed_trades": 500}, base=tmp_path)
    assert out["unproven"] == [1] and out["done"] == [2], out
    assert out["stopped_early"] is False


def test_a_number_check_is_settled_by_the_value_not_by_the_step_text(tmp_path):
    text = ("goal: does it have enough closed trades\n"
            "step: count the closed trades\n  number: closed_trades >= 100\n")
    assert brain.carry_out(brain.parse_plan(text), values={"closed_trades": 99},
                           base=tmp_path)["failed"] == [1]
    assert brain.carry_out(brain.parse_plan(text), values={"closed_trades": 100},
                           base=tmp_path)["done"] == [1]


# --------------------------------------------------------------------------- governance

def test_a_step_that_executes_nothing_is_not_refused_for_what_it_DESCRIBES(tmp_path):
    """"count the closed trades" classifies T3, because "trades" contains "trade". The step compares a
    number and acts on nothing, and refusing it would teach the owner to grant blanket approvals —
    strictly more dangerous than the thing the refusal was protecting against. So it runs, and the
    prose tier is recorded and flagged instead."""
    plan = brain.parse_plan("goal: does it have enough closed trades\n"
                            "step: count the closed trades\n  number: closed_trades >= 100\n")
    out = brain.carry_out(plan, values={"closed_trades": 500}, base=tmp_path)
    assert out["done"] == [1] and out["refused"] == []
    assert plan.steps[0].prose_tier == governance.T3_CRITICAL
    assert plan.steps[0].needs_owner is True
    assert out["needs_owner"] == [1]
    assert "executes" in plan.steps[0].decision.reason


def test_a_step_whose_COMMAND_is_outward_is_refused(tmp_path):
    """The gate is on what runs. A command that leaves this machine is refused without approval, even
    though the step's own wording is mild."""
    plan = brain.parse_plan("goal: does the branch reach the remote\n"
                            "step: tidy up the branch\n  run: git push origin main\n")
    out = brain.carry_out(plan, allow_run=True, runner=_runner(0), base=_cleared(tmp_path))
    assert out["refused"] == [1], out
    assert plan.steps[0].decision.tier in (governance.T2_OUTWARD, governance.T3_CRITICAL)


def test_a_read_only_step_needs_no_approval(tmp_path):
    plan = brain.parse_plan("goal: does CLAUDE.md exist\nstep: read CLAUDE.md\n  file: CLAUDE.md\n")
    out = brain.carry_out(plan, base=tmp_path)
    assert out["refused"] == [] and out["done"] == [1]
    assert plan.steps[0].prose_tier == governance.T0_READ
    assert plan.steps[0].needs_owner is False


def test_a_planning_pass_is_never_gated_but_an_acting_one_is(tmp_path):
    """Dry run means "produces output but does not act", and a planning pass IS that output. A new
    capability must still prove itself before anything runs."""
    text = "goal: does the engine agree\nstep: run the tests\n  run: python -m pytest -q\n"

    dry = brain.carry_out(brain.parse_plan(text), allow_run=False, base=tmp_path)
    assert dry["refused"] == [], "a pass that runs nothing must not be gated"

    acting = brain.parse_plan(text)
    out = brain.carry_out(acting, allow_run=True, runner=_runner(0), base=tmp_path)
    assert out["refused"] == [1], "'brain' is unproven in this sandbox, so it must not act"
    assert "dry run" in acting.steps[0].detail


# --------------------------------------------------------------------------- may it be reported done

def test_unproven_is_not_done(tmp_path):
    """The tempting summary — "1 of 2 steps passed" — reads like progress and hides an unsettled goal."""
    plan = brain.parse_plan(
        "goal: does the engine agree\n"
        "step: run the tests\n  run: python -m pytest -q\n"
        "step: count the closed trades\n  number: closed_trades >= 100\n")
    brain.carry_out(plan, allow_run=False, values={"closed_trades": 500}, base=tmp_path)
    verdict = brain.may_report_done(plan)
    assert verdict["may_report_done"] is False
    assert "step 1 unproven" in verdict["reason"]


def test_a_plan_is_done_only_when_every_step_passed_something_independent(tmp_path):
    plan = brain.parse_plan(
        "goal: does it have enough closed trades and a readable config\n"
        "step: count the closed trades\n  number: closed_trades >= 100\n"
        "step: confirm the project file is there\n  file: CLAUDE.md\n")
    brain.carry_out(plan, values={"closed_trades": 500}, base=tmp_path)
    verdict = brain.may_report_done(plan)
    assert verdict["may_report_done"] is True, verdict["reason"]
    assert set(verdict["states"].values()) == {"done"}


def test_a_refused_plan_can_never_be_reported_done():
    plan = brain.parse_plan("goal: does it work\nstep: just do it\n")
    assert brain.may_report_done(plan)["may_report_done"] is False
    assert brain.carry_out(plan)["ran"] is False


# --------------------------------------------------------------------------- the whole loop

def test_run_goal_returns_the_plan_the_outcome_and_the_verdict_without_writing_a_ledger_entry():
    out = brain.run_goal("does it have enough closed trades",
                         "goal: does it have enough closed trades\n"
                         "step: count them\n  number: closed_trades >= 100\n",
                         values={"closed_trades": 500}, record=False)
    assert out["plan"]["sound"] is True
    assert out["outcome"]["done"] == [1]
    assert out["verdict"]["may_report_done"] is True


def test_a_dry_pass_is_never_recorded_as_having_acted(monkeypatch):
    """Layer 4's ledger exists to answer "did anything change?". A dry pass that claimed to have acted
    would put a false entry in the one record built to make that question answerable."""
    written = {}
    from src import loop_ledger

    def _record(loop, **kw):
        written.update({"loop": loop, **kw})
        return {}

    monkeypatch.setattr(loop_ledger, "record_closure", _record)

    brain.run_goal("does it have enough closed trades",
                   "goal: does it have enough closed trades\n"
                   "step: count them\n  number: closed_trades >= 100\n",
                   values={"closed_trades": 500}, allow_run=False)
    assert written["loop"] == "brain"
    assert written["acted"] is False, "nothing ran, so nothing acted"
    assert written["acceptance_passed"] is True
    assert written["measured"]["done"] == 1


def test_a_refused_plan_is_still_recorded_with_its_reason(monkeypatch):
    """A loop that refuses and leaves no trace is indistinguishable from one that never ran."""
    written = {}
    from src import loop_ledger
    monkeypatch.setattr(loop_ledger, "record_closure",
                        lambda loop, **kw: written.update({"loop": loop, **kw}) or {})

    brain.run_goal("make the strategy better", "goal: make the strategy better\nstep: try things\n")
    assert written["acted"] is False
    assert written["acceptance_passed"] is False
    assert "refused" in written["decided"]
    assert written["measured"]["refusals"] >= 1
