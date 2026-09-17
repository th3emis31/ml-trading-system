import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import pytest

from src.data import generate_synthetic_data
from src import walkforward_backtest as wf


@pytest.fixture(scope="module")
def run():
    """One walk-forward run on injected data, with the model savers watched."""
    import src.train as train_module

    calls = []
    original_save_model = train_module.save_model
    original_save_metrics = train_module.save_metrics
    train_module.save_model = lambda *a, **k: calls.append("save_model")
    train_module.save_metrics = lambda *a, **k: calls.append("save_metrics")
    try:
        data = generate_synthetic_data("XAUUSD", start_date="2020-01-01", end_date="2024-12-31", n=1400)
        result = wf.run_walkforward_backtest("XAUUSD", "5y", data=data, n_folds=4)
    finally:
        train_module.save_model = original_save_model
        train_module.save_metrics = original_save_metrics
    return result, calls


def test_reports_the_agreed_metric_set(run):
    result, _ = run
    assert result["available"] is True
    for key in ("trades", "win_rate_pct", "profit_factor", "expectancy_pct", "max_drawdown_pct",
                "sharpe", "sortino", "cagr_pct", "avg_r", "exposure_pct"):
        assert key in result["metrics"]
    assert result["seed"] == wf.RANDOM_SEED
    assert result["feature_columns"]
    assert result["test_start"] and result["test_end"]


def test_expectancy_in_r_losing_streak_and_inverse_baseline_are_reported(run):
    result, _ = run
    for key in ("expectancy_r", "longest_losing_streak"):
        assert key in result["metrics"] and key in result["inverse_baseline"]["metrics"]
    assert result["inverse_baseline"]["evidence"] in ("sufficient", "insufficient")
    for trade in result["recent_trades"]:
        assert trade["net_r"] < trade["r_multiple"], "costs lower the R of every trade"


def test_invert_flips_the_side_and_mirrors_stop_and_target():
    n = 30
    closes = [100.0] * n
    features = pd.DataFrame({"datetime": pd.date_range("2024-01-01", periods=n, freq="D", tz="UTC"),
                             "close": closes, "high": [100.5] * n, "low": [99.5] * n, "volatility_5d": [0.01] * n})
    features.loc[1, "low"] = 97.0                                   # 1.2 x ATR(1.0) below 100 = 98.8: a long is stopped
    import numpy as np
    proba = np.full(n, np.nan)
    proba[0] = 0.9
    directions = np.zeros(n, dtype=int)
    directions[0] = 1
    folds = np.ones(n, dtype=int)
    kwargs = dict(buy_threshold=0.55, sell_threshold=0.45, hold_bars=5, cost_pct=0.0004, directions=directions)
    long_trade = wf._simulate_trades(features, proba, folds, **kwargs)[0]
    short_trade = wf._simulate_trades(features, proba, folds, invert=True, **kwargs)[0]
    assert long_trade["side"] == "BUY" and long_trade["outcome"] == "SL" and long_trade["r_multiple"] == -1.0
    assert short_trade["side"] == "SELL" and short_trade["outcome"] != "SL"
    assert long_trade["net_r"] == pytest.approx(-1.0 - 0.0004 * 100 / 1.2, abs=1e-4)
    streak = wf.summarize_trades([{"net_pct": x, "side": "BUY", "r_multiple": 0.0, "net_r": 0.0, "bars_held": 1}
                                  for x in (0.1, -0.1, -0.2, 0.0, 0.3, -0.1)],
                                 test_start="2024-01-01", test_end="2025-01-01", test_bars=100, bars_in_market=6)
    assert streak["longest_losing_streak"] == 3


def test_folds_are_time_ordered_with_purge_gap(run):
    result, _ = run
    assert result["purge_bars"] == wf.LABEL_HORIZON_BARS
    for fold in result["by_fold"]:
        assert fold["train_end"] < fold["test_start"]
    starts = [fold["test_start"] for fold in result["by_fold"]]
    assert starts == sorted(starts)


def test_live_model_files_are_never_written(run):
    _, calls = run
    assert calls == []


def test_costs_are_charged_on_every_trade(run):
    result, _ = run
    cost = result["costs"]["round_trip_pct"] * 100
    for trade in result["recent_trades"]:
        assert trade["net_pct"] == pytest.approx(trade["gross_pct"] - cost, abs=1e-3)


def test_evidence_flag_follows_trade_count(run):
    result, _ = run
    expected = "sufficient" if result["metrics"]["trades"] >= wf.MIN_TRADES_FOR_EVIDENCE else "insufficient"
    assert result["evidence"] == expected


def test_missing_history_is_reported_not_faked(monkeypatch):
    monkeypatch.setattr(wf, "fetch_yahoo_history", lambda *a, **k: pd.DataFrame())
    result = wf.run_walkforward_backtest("BTCUSD", "10y")
    assert result["available"] is False
    assert "synthetic" in result["reason"]


def test_unknown_range_is_rejected():
    assert wf.run_walkforward_backtest("XAUUSD", "3y")["available"] is False
