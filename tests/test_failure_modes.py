"""Criticise the plan before running it - i40 Pilot build map step 7.

The pinned lesson in this project is: run the cheap check that would falsify a claim BEFORE stating
it. A register of failures that have actually happened here turns that from a good intention into a
checklist, which is what a smaller offline model can follow - it cannot be relied on to invent the
right doubt, but it can read one that has already been earned.

The acceptance test for the step: each past failure is represented by a line that would have caught it.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import failure_modes as fm


def test_every_mode_cites_a_real_occasion_and_a_runnable_check():
    """A mode without an occasion is a general caution, and general cautions get skimmed."""
    out = fm.register_is_sound()
    assert out["sound"] is True, out["problems"]
    assert out["modes"] >= 12


def test_the_expensive_failures_of_this_project_are_all_represented():
    """Each of these cost real work or real credibility here."""
    ids = {m.id for m in fm.REGISTER}
    for required in ("in-sample", "spike-not-plateau", "outlier-window", "foreign-attribution",
                     "invented-data", "self-inflicted-outage", "counter-not-reality",
                     "duplicate-build", "uncheckable-check"):
        assert required in ids, f"no line would have caught: {required}"


def test_a_parameter_sweep_is_warned_about_in_sample_choice_and_spikes():
    """The two that would have caught the 12x artefact and the +417 gold spike."""
    out = fm.critique("sweep the EA parameters on 2024 and pick the best profit factor")
    raised = {a["id"] for a in out["applies"]}
    assert "in-sample" in raised and "spike-not-plateau" in raised


def test_a_diagnostic_is_warned_about_changing_the_live_system():
    """train_model() persists to models/ and overwrote the live champions from inside a probe."""
    out = fm.critique("run a quick diagnostic comparing the raw and calibrated model output")
    assert "self-inflicted-outage" in {a["id"] for a in out["applies"]}


def test_a_performance_claim_is_warned_about_attribution():
    out = fm.critique("report how much profit the system made on the account")
    assert "foreign-attribution" in {a["id"] for a in out["applies"]}


def test_building_something_new_is_warned_about_duplication():
    out = fm.critique("build a new module to implement the provider layer")
    assert "duplicate-build" in {a["id"] for a in out["applies"]}


def test_an_unrelated_plan_raises_nothing():
    """A critique that fires on everything gets skimmed and then ignored."""
    assert fm.critique("say hello to the owner")["count"] == 0


def test_each_check_says_what_to_do_not_merely_what_to_fear():
    for mode in fm.REGISTER:
        assert len(mode.check) >= 40
        assert any(verb in mode.check.lower() for verb in
                   ("measure", "report", "run", "check", "test", "search", "name", "print",
                    "query", "return", "state", "vary", "look", "attribute", "refuse")), mode.id


def test_the_critique_reaches_the_actual_brief():
    """A register nobody calls is a library, not a safeguard."""
    from src import context_builder as cb

    brief = cb.brief_for_task("sweep the parameters and pick the best",
                              budget_tokens=8_000, identity="i40 Pilot")
    assert "CHECK FIRST" in brief.prompt
    assert "Test the neighbours" in brief.prompt


def test_the_critique_cannot_be_trimmed_away_by_the_budget():
    """It rides with the task, because pressure is exactly when the shortcut gets taken."""
    from src import context_builder as cb

    brief = cb.assemble("tune the parameters", budget_tokens=3_000, identity="i40",
                        critique_checks=["Test the neighbours before calling anything a winner."],
                        evidence=[f"- line {i}" for i in range(2_000)])
    assert "CHECK FIRST" in brief.prompt
    task_slot = next(s for s in brief.slots if s.name == "task")
    assert "Test the neighbours" in task_slot.text
