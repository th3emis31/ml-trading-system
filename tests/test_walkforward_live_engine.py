"""Execution safety item 6: the walk-forward backtest trades the shared live engine (signal_engine.predict_signal).

A spy wraps predict_signal: every out-of-sample bar must be predicted by it with the live config, and every simulated
trade must enter on a bar where it returned that side. The legacy RF-probability path stays available by flag.
"""
import threading

import pytest

from src import signal_engine
from src import walkforward_backtest as wf
from src.data import generate_synthetic_data


@pytest.fixture(scope="module")
def data():
    return generate_synthetic_data("XAUUSD", start_date="2020-01-01", end_date="2024-12-31", n=1400)


def test_every_trade_comes_from_predict_signal_in_live_mode(data, monkeypatch):
    """The backtest must reach the live engine on every out-of-sample bar, and with the live config.

    The spy records calls made on THIS thread only. It has to: tests/test_system_doctor.py requests
    /system-doctor through the test client, which fires @app.before_request and starts
    _jarvis_autonomy_loop as a daemon for the rest of the pytest process. That loop wakes every 180 s
    and computes a signal, this test takes about 167 s, and the spy patches a module attribute that
    the loop reads too - so it was catching one stray call from another thread and asserting
    690 == 689. It only began failing on 22 September, the first deep run after the owner armed
    autonomy: before that the loop woke and returned immediately because it was disabled.

    Filtering by thread keeps the guard exactly as strict for the thing it guards - every test bar
    going through predict_signal with the live config - while ignoring activity that is not this
    test's.
    """
    calls = {}
    original = signal_engine.predict_signal
    test_thread = threading.get_ident()

    def spy(bars, models=None, config=None, features=None):
        result = original(bars, models, config, features=features)
        if threading.get_ident() == test_thread:
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
