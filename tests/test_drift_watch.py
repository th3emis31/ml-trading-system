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
    decisions = [_decision(accuracy=0.5370843989769821) for _ in range(4)]
    out = dw.champion_freshness(decisions, "XAUUSD")
    assert out["status"] == "unchanged"
    assert out["identical_records_in_a_row"] == 4
    assert "rather than a fresh measurement" in out["why"]


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
