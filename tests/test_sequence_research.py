import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("torch")

from src import sequence_research as sr
from src.data import generate_synthetic_data

TINY = {"hidden": 8, "epochs": 1, "patience": 1, "batch": 256, "seq_len": 16}
CONFIG = [{"name": "balanced", "sl_atr": 1.0, "tp_atr": 2.0, "horizon": 12}]


def _hourly(n=2200):
    frame = generate_synthetic_data("BTCUSD", start_date="2024-01-01", end_date="2024-12-31", n=n)
    frame["datetime"] = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    return frame


@pytest.fixture(scope="module")
def report():
    return sr.run_sequence_research("BTCUSD", "1h", cell="gru", n_folds=4, holdout_folds=1, configs=CONFIG,
                                    params=TINY, data=_hourly())


def test_runs_end_to_end(report):
    assert report["available"] is True
    assert report["model"] == "gru"
    assert report["holdout"]["verdict"]["checks"]


def test_holdout_is_never_used_for_selection(report):
    assert set(report["selection"]["folds_used"]).isdisjoint(report["holdout_folds"])


def test_training_stops_before_each_test_block(report):
    for fold in report["fold_training"]:
        assert fold["train_end_idx"] < fold["test_start_idx"] - CONFIG[0]["horizon"] + 1


def test_scaler_and_early_stopping_never_see_test_bars():
    rng = np.random.default_rng(0)
    X = rng.normal(size=(1500, 5))
    labels = (rng.random(1500) > 0.5).astype(float)
    train_idx = np.arange(100, 1000)
    test_idx = np.arange(1100, 1200)
    first, _, _ = sr._train_predict(X, labels, labels, train_idx, test_idx, 16, 12, "gru", {**sr.DEFAULTS, **TINY})
    shifted = X.copy()
    shifted[1000:] += 1000.0  # huge change after the training span, including the test bars
    second, _, _ = sr._train_predict(shifted, labels, labels, train_idx, test_idx, 16, 12, "gru", {**sr.DEFAULTS, **TINY})
    # Predictions differ (inputs differ) but must be finite: the scaler was fitted on training bars
    # only, and the huge test values are clipped rather than leaking into training statistics.
    assert np.isfinite(first[test_idx]).all() and np.isfinite(second[test_idx]).all()
    # Training-span windows are untouched, so a model trained twice must produce identical predictions
    # for a test window made only of training-span bars.
    inside = np.arange(900, 1000)
    a, _, _ = sr._train_predict(X, labels, labels, np.arange(100, 880), inside, 16, 12, "gru", {**sr.DEFAULTS, **TINY})
    b, _, _ = sr._train_predict(shifted, labels, labels, np.arange(100, 880), inside, 16, 12, "gru", {**sr.DEFAULTS, **TINY})
    np.testing.assert_allclose(a[inside], b[inside], rtol=1e-5, atol=1e-6)


def test_costs_are_charged(report):
    cost = report["cost_round_trip_pct"] * 100
    for trade in report["holdout"]["recent_trades"]:
        assert trade["net_pct"] == pytest.approx(trade["gross_pct"] - cost, abs=1e-3)
