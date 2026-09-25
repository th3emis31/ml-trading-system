"""The self-improvement loop must not be able to flatter itself.

Build map step 10. A loop that proposes its own changes and measures its own results is exactly where
a system starts reporting progress it did not make, so these tests aim at the three ways that happens
rather than at the happy path:

1. moving the prediction after seeing the answer;
2. writing a result the measurement never produced;
3. counting a ruled-out explanation as a failure, which discourages the experiments worth running.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from src import governance, self_improvement as si

PRINTS_061 = 'python -c "print(\'METRIC accuracy = 0.61\')"'
OPEN = dict(metric="accuracy", direction="higher", threshold=0.52, baseline=0.50,
            measured_by=PRINTS_061, source="test")


def _propose(tmp_path, question="Does X help?", **over):
    kwargs = {**OPEN, **over}
    return si.propose(question, path=tmp_path / "ledger.jsonl", **kwargs)


def test_a_prediction_is_locked_when_it_is_opened(tmp_path):
    row = _propose(tmp_path)
    assert row["state"] == "open" and row["verdict"] == ""
    assert len(row["lock"]) == 32
    assert si.verify_lock(row)["ok"]


def test_moving_the_threshold_after_the_fact_breaks_the_lock(tmp_path):
    row = _propose(tmp_path)
    moved = dict(row)
    moved["prediction"] = {**row["prediction"], "threshold": 0.40}     # easier to clear
    check = si.verify_lock(moved)
    assert not check["ok"]
    assert "changed after it was locked" in check["note"]


def test_a_verdict_is_refused_when_the_prediction_moved(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    row = _propose(tmp_path)
    tampered = dict(row)
    tampered["prediction"] = {**row["prediction"], "direction": "lower"}
    with ledger.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(tampered) + "\n")
    out = si.settle(row["id"], value=0.99, path=ledger)
    assert out["ok"] is False and "changed after it was locked" in out["reason"]


def test_the_baseline_may_be_corrected_without_breaking_the_lock(tmp_path):
    """Re-measuring today's figure more precisely is honest; the CLAIM is what must not move."""
    row = _propose(tmp_path)
    corrected = dict(row)
    corrected["prediction"] = {**row["prediction"], "baseline": 0.4977}
    assert si.verify_lock(corrected)["ok"]


def test_a_prediction_that_holds_is_confirmed_and_one_that_fails_is_refuted(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    a = _propose(tmp_path, question="A?")
    b = _propose(tmp_path, question="B?")
    assert si.settle(a["id"], value=0.61, path=ledger)["verdict"] == "confirmed"
    assert si.settle(b["id"], value=0.49, path=ledger)["verdict"] == "refuted"


def test_a_lower_is_better_prediction_is_judged_the_right_way_round(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    row = _propose(tmp_path, metric="drawdown", direction="lower", threshold=10.0, baseline=14.0)
    assert si.settle(row["id"], value=8.0, path=ledger)["verdict"] == "confirmed"
    row = _propose(tmp_path, question="again?", metric="drawdown", direction="lower",
                   threshold=10.0, baseline=14.0)
    assert si.settle(row["id"], value=12.0, path=ledger)["verdict"] == "refuted"


def test_no_number_means_inconclusive_never_a_result(tmp_path):
    """The failure this exists to prevent: an unmeasured experiment counted as an improvement."""
    ledger = tmp_path / "ledger.jsonl"
    row = _propose(tmp_path)
    out = si.settle(row["id"], value=None, reason="the trainer crashed", path=ledger)
    assert out["verdict"] == "inconclusive"
    assert out["measured"] is None
    stored = [r for r in si.experiments(ledger) if r["id"] == row["id"]][0]
    assert stored["result"]["value"] is None


def test_an_experiment_cannot_be_settled_twice(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    row = _propose(tmp_path)
    assert si.settle(row["id"], value=0.49, path=ledger)["verdict"] == "refuted"
    again = si.settle(row["id"], value=0.99, path=ledger)
    assert again["ok"] is False and "already closed" in again["reason"]


def test_the_measurement_must_print_the_metric_it_promised(tmp_path):
    row = _propose(tmp_path, measured_by='python -c "print(\'all done, looks great\')"')
    out = si.run_measurement(row)
    assert out["value"] is None
    assert "never printed" in out["reason"]


def test_a_real_command_hands_its_number_back(tmp_path):
    assert si.run_measurement(_propose(tmp_path))["value"] == pytest.approx(0.61)


def test_a_json_line_is_also_accepted(tmp_path):
    command = 'python -c "import json; print(\'@@ \' + json.dumps({\'accuracy\': 0.55, \'rows\': 900}))"'
    assert si.run_measurement(_propose(tmp_path, measured_by=command))["value"] == pytest.approx(0.55)


def test_a_row_count_in_the_output_is_not_mistaken_for_the_result(tmp_path):
    row = _propose(tmp_path, measured_by='python -c "print(\'processed 4821 bars\')"')
    assert si.run_measurement(row)["value"] is None


def test_the_wrong_metric_name_does_not_count(tmp_path):
    """A command that prints SOME number but not the promised one settles nothing."""
    row = _propose(tmp_path, measured_by='python -c "print(\'METRIC sharpe = 1.9\')"')
    assert si.run_measurement(row)["value"] is None


def test_a_command_that_cannot_run_says_so_rather_than_raising(tmp_path):
    def boom(*_a, **_k):
        raise OSError("no shell here")

    assert "could not run" in si.run_measurement(_propose(tmp_path), run=boom)["reason"]


def test_ruled_out_explanations_are_reported_as_progress(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    for number, value in enumerate((0.49, 0.48, 0.61)):
        row = _propose(tmp_path, question=f"Does idea {number} help?")
        si.settle(row["id"], value=value, path=ledger)
    rep = si.experiment_report(ledger)
    assert rep["counts"] == {"confirmed": 1, "refuted": 2, "inconclusive": 0}
    assert "2 explanation(s) ruled out" in rep["summary"]
    assert len(rep["ruled_out"]) == 2
    assert rep["honest"] is True


def test_predictions_that_always_come_true_are_flagged(tmp_path):
    """Not a bug in the code - a warning about whoever is writing the predictions."""
    ledger = tmp_path / "ledger.jsonl"
    for number in range(6):
        row = _propose(tmp_path, question=f"Safe bet {number}?")
        si.settle(row["id"], value=0.99, path=ledger)
    rep = si.experiment_report(ledger)
    assert rep["confirm_rate"] == 1.0
    assert rep["honest"] is False
    assert "always hold are not" in rep["honest_note"]


def test_a_forgotten_experiment_is_reported_as_stale(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    _propose(tmp_path, question="Opened and never measured?")
    later = datetime.now(timezone.utc) + timedelta(days=si.STALE_DAYS + 3)
    rep = si.experiment_report(ledger, now=later)
    assert len(rep["stale"]) == 1 and rep["stale"][0]["age_days"] > si.STALE_DAYS


def test_settling_appends_rather_than_editing(tmp_path):
    """History must survive: the open row and the closed row are both on disk."""
    ledger = tmp_path / "ledger.jsonl"
    row = _propose(tmp_path)
    si.settle(row["id"], value=0.49, path=ledger)
    lines = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 2
    assert lines[0]["state"] == "open" and lines[1]["state"] == "closed"
    assert len(si.experiments(ledger)) == 1            # the reader shows the latest only


def test_a_truncated_line_is_skipped_not_guessed_at(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    row = _propose(tmp_path)
    with ledger.open("a", encoding="utf-8") as handle:
        handle.write('{"id": "exp_broken", "questi\n')
    assert [r["id"] for r in si.experiments(ledger)] == [row["id"]]


def test_an_experiment_needs_a_question_a_command_and_a_real_direction(tmp_path):
    with pytest.raises(ValueError):
        _propose(tmp_path, direction="sideways")
    with pytest.raises(ValueError):
        _propose(tmp_path, measured_by="   ")
    with pytest.raises(ValueError):
        _propose(tmp_path, question="   ")


def test_an_unsourced_idea_is_labelled_as_one(tmp_path):
    assert _propose(tmp_path, source="")["source"] == "unsourced"


def test_acting_on_a_result_is_checked_by_governance(tmp_path):
    """A confirmed result that would move money is recorded as a proposal, never applied."""
    ledger = tmp_path / "ledger.jsonl"
    row = si.propose("Should the live stop move?", metric="expectancy", direction="higher",
                     threshold=0.1, measured_by='python -c "print(\'METRIC expectancy = 0.2\')"',
                     source="test", tier=governance.T3_CRITICAL, path=ledger)
    si.settle(row["id"], value=0.2, path=ledger)
    out = si.record_outcome(row["id"], "change the live SwingTrendPullback stop", path=ledger)
    assert out["ok"] is True
    assert out["allowed"] is False
    assert "proposal for the owner" in out["note"]


def test_a_tier_typo_is_rejected_rather_than_quietly_downgraded(tmp_path):
    """governance.tier_for ignores a `declared` tier it does not know - correctly, because a caller
    must not be able to declare something DOWN. The cost is that a typo would leave a money-moving
    action at whatever its wording matched, so it is refused where the experiment is written."""
    with pytest.raises(ValueError, match="tier must be one of"):
        _propose(tmp_path, tier="T3_CRITICAL")
    with pytest.raises(ValueError):
        _propose(tmp_path, tier="critical")
    assert _propose(tmp_path, tier=governance.T3_CRITICAL)["tier"] == "T3"


def test_nothing_in_the_loop_places_orders(tmp_path):
    assert si.experiment_report(tmp_path / "none.jsonl")["places_orders"] is False
