import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd
import pytest

from src import edge_research as er
from src import meta_research as mr
from src.data import generate_synthetic_data

CONFIG = [{"name": "balanced", "sl_atr": 1.0, "tp_atr": 2.0, "horizon": 24}]


def _hourly(n=2600):
    frame = generate_synthetic_data("XAUUSD", start_date="2024-01-01", end_date="2024-12-31", n=n)
    frame["datetime"] = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    return frame


@pytest.mark.parametrize("rule", mr.PRIMARY_RULES)
def test_primary_rules_never_use_future_bars(rule):
    frame = _hourly()
    columns = ["open", "high", "low", "close", "volume"]
    base = mr.primary_side(er.build_research_features(frame), rule)
    changed = frame.copy()
    changed[columns] = changed[columns].astype(float)
    changed.loc[changed.index[-150:], columns] *= 1.7
    after = mr.primary_side(er.build_research_features(changed), rule)
    cutoff = len(frame) - 150
    np.testing.assert_array_equal(base[:cutoff], after[:cutoff])


@pytest.fixture(scope="module")
def report():
    return mr.run_meta_research("XAUUSD", "1h", rules=("breakout",), models=("logit",), configs=CONFIG,
                                n_folds=5, holdout_folds=2, data=_hourly())


def test_runs_and_reports_the_unfiltered_rule(report):
    assert report["available"] is True
    assert set(report["unfiltered_rule_holdout"]) == set(mr.SUMMARY_KEYS)
    assert isinstance(report["filter_adds_value"], bool)


def test_training_uses_only_bars_where_the_rule_fired(report):
    frame = er.build_research_features(_hourly())
    fired = int((mr.primary_side(frame, "breakout") != 0).sum())
    for fold in report["fold_training"]:
        assert fold["train_rows"] <= fired
        assert fold["train_end_idx"] < fold["test_start_idx"] - CONFIG[0]["horizon"] + 1


def test_holdout_is_never_used_for_selection(report):
    assert set(report["selection"]["folds_used"]).isdisjoint(report["holdout_folds"])


def test_costs_are_charged(report):
    cost = report["cost_round_trip_pct"] * 100
    for trade in report["holdout"]["recent_trades"]:
        assert trade["net_pct"] == pytest.approx(trade["gross_pct"] - cost, abs=1e-3)
