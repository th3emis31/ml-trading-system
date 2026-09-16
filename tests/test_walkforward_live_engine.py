"""Execution safety item 6: the walk-forward backtest trades the shared live engine (signal_engine.predict_signal).

A spy wraps predict_signal: every out-of-sample bar must be predicted by it with the live config, and every simulated
trade must enter on a bar where it returned that side. The legacy RF-probability path stays available by flag.
"""
import pytest

from src import signal_engine
from src import walkforward_backtest as wf
from src.data import generate_synthetic_data


@pytest.fixture(scope="module")
def data():
    return generate_synthetic_data("XAUUSD", start_date="2020-01-01", end_date="2024-12-31", n=1400)


def test_every_trade_comes_from_predict_signal_in_live_mode(data, monkeypatch):
    calls = {}
    original = signal_engine.predict_signal

    def spy(bars, models=None, config=None, features=None):
        result = original(bars, models, config, features=features)
        calls[wf._iso(result["signal_time"])] = (result["signal"], result["config"])
        return result

    monkeypatch.setattr(signal_engine, "predict_signal", spy)
    result = wf.run_walkforward_backtest("XAUUSD", "5y", data=data, n_folds=4)
    assert result["available"] and result["signal_mode"] == "live_engine"
    assert len(calls) == result["test_bars"], "every out-of-sample bar goes through predict_signal"
    live = signal_engine.get_config("live")
    assert all(cfg["weights"] == live["weights"] and cfg["buy_threshold"] == 0.55 and cfg["sell_threshold"] == 0.45
               for _, cfg in calls.values())
    assert result["metrics"]["trades"] > 0
    for trade in result["recent_trades"]:
        assert calls[trade["entry_time"]][0] == trade["side"]


def test_legacy_rf_probability_mode_is_kept(data):
    result = wf.run_walkforward_backtest("XAUUSD", "5y", data=data, n_folds=4, signal_mode="rf_proba")
    assert result["available"] and result["signal_mode"] == "rf_proba"
    assert wf.run_walkforward_backtest("XAUUSD", "5y", data=data, signal_mode="other")["available"] is False
