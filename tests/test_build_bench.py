"""The trust bar must not be able to drift to fit a result, and it must read the holdout only.

The bar exists for the same reason the deflated Sharpe bar does: so that "can I trust this" has an
answer that was fixed before the number arrived. These tests pin the four conditions and, above all,
that a good mean cannot carry one collapsed task through.
"""
from __future__ import annotations

from src import build_bench as bb
from src.builder import parse_spec


def _parsed(text):
    return parse_spec(text)


def test_a_perfect_spec_scores_one():
    task = {"key": "t", "min_rules": 3,
            "must_cover": {"empty": ("empty",), "bad": ("invalid",)}}
    spec = _parsed("R1 | rejects an empty list | number: x >= 1\n"
                 "R2 | rejects an invalid hour | number: y >= 1\n"
                 "R3 | returns a float | number: z >= 1\n")
    out = bb.score_spec(spec, task)
    assert out["coverage"] == 1.0 and out["enough_rules"] == 1.0 and out["machine_fraction"] == 1.0
    assert out["score"] == 1.0 and out["missed"] == []


def test_a_missed_boundary_is_named_not_just_counted():
    task = {"key": "t", "min_rules": 1, "must_cover": {"empty": ("empty",), "dst": ("daylight",)}}
    out = bb.score_spec(_parsed("R1 | rejects an empty list | number: x >= 1\n"), task)
    assert out["missed"] == ["dst"] and out["coverage"] == 0.5


def test_an_ask_check_lowers_the_machine_fraction():
    """A spec full of `ask` cannot run unattended, so it must not score as though it could."""
    task = {"key": "t", "min_rules": 2, "must_cover": {"empty": ("empty",)}}
    out = bb.score_spec(_parsed("R1 | rejects an empty list | ask: does it look right?\n"
                              "R2 | returns a float | number: z >= 1\n"), task)
    assert out["machine_fraction"] == 0.5


def test_too_few_rules_is_penalised_against_the_task_floor():
    task = {"key": "t", "min_rules": 4, "must_cover": {"empty": ("empty",)}}
    out = bb.score_spec(_parsed("R1 | rejects an empty list | number: x >= 1\n"), task)
    assert out["enough_rules"] == 0.25


def test_one_collapsed_task_cannot_be_carried_by_a_good_mean():
    """The case this condition exists for: three strong tasks and one total failure averages above the
    bar, and the failure is exactly what would ship unnoticed."""
    holdout = {"tasks": 4, "spec_score": 0.85, "machine_fraction": 1.0, "worst_task": 0.10}
    out = bb.trust(holdout)
    assert out["trusted"] is False and "worst task" in out["why"]


def test_too_few_tasks_is_not_a_measurement():
    out = bb.trust({"tasks": 2, "spec_score": 0.99, "machine_fraction": 1.0, "worst_task": 0.99})
    assert out["trusted"] is False and "holdout task" in out["why"]


def test_a_spec_of_only_ask_checks_is_not_trusted_however_high_it_scores():
    out = bb.trust({"tasks": 4, "spec_score": 0.95, "machine_fraction": 0.2, "worst_task": 0.9})
    assert out["trusted"] is False and "machine-decidable" in out["why"]


def test_passing_all_four_conditions_is_trusted_and_says_what_that_does_not_mean():
    out = bb.trust({"tasks": 4, "spec_score": 0.889, "machine_fraction": 1.0, "worst_task": 0.778})
    assert out["trusted"] is True
    # The bound matters as much as the pass: trusted specs are not correct builds.
    assert "NOT mean anything it builds is correct" in out["means"]


def test_the_bar_is_the_one_that_was_measured_against():
    """If these constants move, a recorded baseline stops being comparable - so a change here is a
    deliberate act, not a quiet adjustment to let a near-miss through."""
    assert bb.MIN_SPEC_SCORE == 0.80
    assert bb.MIN_MACHINE_FRACTION == 0.70
    assert bb.MIN_WORST_TASK == 0.40
    assert bb.MIN_HOLDOUT_TASKS == 4


def test_the_two_splits_do_not_share_a_task():
    """A holdout that overlaps the calibrate set measures the tuning, not the builder."""
    calibrate = {task["key"] for task in bb.CALIBRATE}
    holdout = {task["key"] for task in bb.HOLDOUT}
    assert not (calibrate & holdout)
    assert len(holdout) >= bb.MIN_HOLDOUT_TASKS


def test_a_provider_that_cannot_answer_scores_zero_rather_than_being_skipped():
    """A skipped task would quietly raise the average; a zero is the honest score for no answer."""
    def broken(request, family="software", title=""):
        return {"ok": False, "spec": None, "error": "no provider"}

    out = bb.bench("holdout", drafter=broken)
    assert out["spec_score"] == 0.0 and out["tasks"] == len(bb.HOLDOUT)
    assert all(row["score"] == 0.0 and row["error"] for row in out["rows"])
    assert bb.trust(out)["trusted"] is False
