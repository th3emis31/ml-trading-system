"""Drift watch: the checks that were not being made, and the traps in making them.

Each test here corresponds to something the system was ignoring on 19 September 2026, or to a way
the check itself could quietly mislead.
"""
import json

import pytest

from src import drift_watch as dw


def _decision(symbol="XAUUSD", accuracy=0.54, source="broker", promoted=False, at="2026-09-18 05:30",
              challenger=None, age=1):
    row = {"symbol": symbol, "accuracy": accuracy, "data_source": source, "rf_promoted": promoted,
           "trained_at": at, "champion_age_days": age}
    if challenger is not None:
        row["challenger_accuracy"] = challenger
    return row


# --- the baseline that was missing ------------------------------------------------

def test_an_accuracy_below_the_majority_class_is_called_no_edge():
    base = {"available": True, "majority_class_rate": 0.56, "source": "app:mt5:XAUUSD"}
    out = dw.accuracy_against_baseline([_decision(accuracy=0.54, promoted=True)], "XAUUSD", base)
    assert out["status"] == "no edge"
    assert "always guessing the common class" in out["why"]


def test_a_tiny_edge_is_called_negligible_rather_than_an_edge():
    base = {"available": True, "majority_class_rate": 0.535, "source": "app:mt5:XAUUSD"}
    out = dw.accuracy_against_baseline([_decision(accuracy=0.54, promoted=True)], "XAUUSD", base)
    assert out["status"] == "negligible"


def test_a_real_edge_is_recognised():
    base = {"available": True, "majority_class_rate": 0.50, "source": "app:mt5:XAUUSD"}
    out = dw.accuracy_against_baseline([_decision(accuracy=0.56, promoted=True)], "XAUUSD", base)
    assert out["status"] == "has an edge"
    assert out["edge"] == pytest.approx(0.06)


def test_a_missing_baseline_is_cannot_judge_not_a_pass():
    out = dw.accuracy_against_baseline([_decision(promoted=True)], "XAUUSD",
                                       {"available": False, "reason": "no broker bars"})
    assert out["status"] == "cannot judge"


# --- the cross-source trap, which I walked into once ------------------------------

def test_an_accuracy_from_another_price_source_is_not_comparable():
    """The bug this pins: a Yahoo-trained accuracy against a broker baseline read as a clean edge."""
    base = {"available": True, "majority_class_rate": 0.5242, "source": "app:mt5:XAUUSD"}
    out = dw.accuracy_against_baseline([_decision(accuracy=0.5371, source="yahoo", promoted=True)],
                                       "XAUUSD", base)
    assert out["status"] == "not comparable"
    assert "two different price series" in out["why"]


def test_the_champions_source_is_taken_from_the_run_that_promoted_it():
    """A later run on broker bars does not make a Yahoo-trained champion broker-trained."""
    decisions = [_decision(accuracy=0.5371, source="yahoo", promoted=True, at="2026-09-17 05:30"),
                 _decision(accuracy=0.5371, source="broker", promoted=False, at="2026-09-19 05:30")]
    base = {"available": True, "majority_class_rate": 0.5242, "source": "app:mt5:XAUUSD"}
    out = dw.accuracy_against_baseline(decisions, "XAUUSD", base)
    assert out["status"] == "not comparable"
    assert out["trained_on"] == "yahoo"


# --- the frozen number read as a fresh one ----------------------------------------

def test_an_accuracy_repeated_across_runs_is_flagged_as_the_champions_frozen_figure():
    """The displayed figure repeats because it is frozen at promotion; the re-scored one is current."""
    decisions = [_decision(accuracy=0.5370843989769821) for _ in range(4)]
    decisions[-1]["rf_champion"] = {"accuracy": 0.5345, "rows": 565}
    out = dw.champion_freshness(decisions, "XAUUSD")
    assert out["status"] == "unchanged"
    assert out["identical_records_in_a_row"] == 4
    assert out["displayed_accuracy"] == pytest.approx(0.5370843989769821)
    assert out["champion_accuracy_rescored"] == pytest.approx(0.5345)
    assert "frozen when the champion was promoted" in out["why"]


def test_the_rescored_accuracy_is_preferred_over_the_frozen_one():
    """The bug this pins: bitcoin displayed 0.5639 while its real current accuracy was 0.5312."""
    base = {"available": True, "majority_class_rate": 0.5010, "source": "app:mt5:BTCUSD"}
    decisions = [_decision(symbol="BTCUSD", accuracy=0.5639, source="broker", promoted=False)]
    decisions[-1]["rf_champion"] = {"accuracy": 0.5312, "rows": 576}
    out = dw.accuracy_against_baseline(decisions, "BTCUSD", base)
    assert out["accuracy"] == pytest.approx(0.5312), "must use the re-scored figure, not the displayed one"
    assert out["displayed_accuracy"] == pytest.approx(0.5639)
    assert "re-scored" in out["measured_on"]
    assert out["status"] == "has an edge"
    assert out["edge"] == pytest.approx(0.0302, abs=1e-4)


def test_the_promotion_decision_itself_was_always_fair():
    """Recorded because I claimed otherwise first: both models are scored on the same holdout."""
    from src import model_promotion, daily_learning
    import inspect

    source = inspect.getsource(daily_learning)
    assert "holdout = promotion.challenger_holdout(features)" in source
    assert "evaluate_rf(rf_model, holdout" in source
    assert "evaluate_rf(champion_rf, holdout" in source


def test_the_learner_records_where_its_accuracy_figure_came_from():
    import inspect
    from src import daily_learning

    source = inspect.getsource(daily_learning)
    assert '"accuracy_basis"' in source
    assert '"champion_rescored_accuracy"' in source


def test_challengers_landing_below_the_champions_claim_are_counted():
    decisions = [_decision(accuracy=0.56, challenger=0.52) for _ in range(5)]
    out = dw.champion_freshness(decisions, "XAUUSD")
    assert out["challengers"]["materially_below_champion"] == 5
    assert out["challengers"]["worst_gap"] == pytest.approx(0.04)


def test_an_old_champion_is_flagged_as_stale():
    out = dw.champion_freshness([_decision(age=dw.STALE_CHAMPION_DAYS + 1)], "XAUUSD")
    assert out["status"] == "stale"


# --- the training source against the feed -----------------------------------------

def test_a_yahoo_trained_champion_against_an_mt5_feed_is_a_mismatch():
    out = dw.champion_source_mismatch([_decision(source="yahoo", promoted=True)], "XAUUSD",
                                      "app:mt5:XAUUSD")
    assert out["status"] == "MISMATCH"
    assert "GC=F" in out["why"], "the gold note must explain which instrument Yahoo actually serves"


def test_the_mismatch_note_is_about_the_symbol_in_hand_not_always_gold():
    """A bug I shipped and caught: the bitcoin line explained the GC=F gold future."""
    out = dw.champion_source_mismatch([_decision(symbol="BTCUSD", source="yahoo", promoted=True)],
                                      "BTCUSD", "app:mt5:BTCUSD")
    assert out["status"] == "MISMATCH"
    assert "GC=F" not in out["why"]
    assert "BTC-USD" in out["why"]


def test_a_broker_trained_champion_on_a_broker_feed_matches():
    out = dw.champion_source_mismatch([_decision(source="broker", promoted=True)], "XAUUSD",
                                      "app:mt5:XAUUSD")
    assert out["status"] == "matched"


def test_no_promoted_champion_is_no_evidence_not_a_pass():
    out = dw.champion_source_mismatch([_decision(promoted=False)], "XAUUSD", "app:mt5:XAUUSD")
    assert out["status"] == "no evidence"


# --- source changes and strategy drift --------------------------------------------

def test_a_price_source_change_in_the_history_is_flagged():
    decisions = [_decision(source="yahoo"), _decision(source="yahoo"), _decision(source="broker")]
    out = dw.source_drift(decisions)
    assert out["status"] == "source changed"
    assert set(out["sources"]) == {"yahoo", "broker"}
    assert "not comparable" in out["why"]


def test_one_source_throughout_is_not_a_concern():
    out = dw.source_drift([_decision(source="broker") for _ in range(3)])
    assert out["status"] == "one source"


def test_strategy_drift_says_no_evidence_when_nothing_has_traded(tmp_path):
    (tmp_path / "strategy_lab").mkdir(parents=True)
    (tmp_path / "strategy_lab" / "shadow_book.json").write_text(
        json.dumps({"strategies": {"a": {"id": "a", "stats": {"trades": 0}}}}), encoding="utf-8")
    out = dw.strategy_drift(tmp_path)
    assert out["status"] == "no evidence"


def test_strategy_drift_will_not_read_a_thin_sample(tmp_path):
    (tmp_path / "strategy_lab").mkdir(parents=True)
    (tmp_path / "strategy_lab" / "shadow_book.json").write_text(json.dumps({"strategies": {"a": {
        "id": "a", "market": "XAUUSD:4h", "description": "x",
        "stats": {"trades": 3, "expectancy_r": 5.0},
        "confidence": {"prior_used_r": 0.1, "interval_spans_zero": True}}}}), encoding="utf-8")
    out = dw.strategy_drift(tmp_path)
    assert out["status"] == "not readable yet"


def test_strategy_drift_reports_delivering_below_promise(tmp_path):
    (tmp_path / "strategy_lab").mkdir(parents=True)
    (tmp_path / "strategy_lab" / "shadow_book.json").write_text(json.dumps({"strategies": {"a": {
        "id": "a", "market": "XAUUSD:4h", "description": "x",
        "stats": {"trades": 40, "expectancy_r": -0.2},
        "confidence": {"prior_used_r": 0.15, "interval_spans_zero": False}}}}), encoding="utf-8")
    out = dw.strategy_drift(tmp_path)
    assert out["status"] == "drifting"
    assert out["delivering_below_promise"] == 1
    assert out["worst"][0]["gap_r"] == pytest.approx(-0.35)


# --- the module's boundaries ------------------------------------------------------

def test_the_module_never_trades_or_retrains():
    text = open(dw.__file__.replace(".pyc", ".py"), encoding="utf-8").read()
    # Call patterns, not bare words: the prose legitimately discusses promotion and training.
    for forbidden in ("order_send(", "OrderSend(", "place_order(", "auto_execute", "MetaTrader5",
                      "train_model(", "save_model(", "promote(", "record_decision("):
        assert forbidden not in text, f"{forbidden} must not appear in a read-only watcher"


def test_the_doctor_runs_the_drift_check():
    """A check nobody runs is not a check; it has to be in the doctor's list."""
    from src import system_doctor

    source = open(system_doctor.__file__.replace(".pyc", ".py"), encoding="utf-8").read()
    assert "def check_model_drift" in source
    assert "check_model_drift," in source, "the check must be registered in the doctor's check list"
