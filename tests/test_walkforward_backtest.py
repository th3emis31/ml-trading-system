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
