"""Execution safety item 5: only real broker fills (demo or live account) move the autonomy confidence threshold."""
from pathlib import Path

import app as app_module
from src import execution_guard as guard


def _simulated(pnl):
    return {"symbol": "XAUUSD", "side": "BUY", "execution_mode": "demo", "status": "win" if pnl > 0 else "loss",
            "pnl_pct": pnl, "outcome_source": "simulated_demo"}


def _real(pnl, platform="mt5"):
    return {"symbol": "XAUUSD", "side": "SELL", "execution_mode": platform, "status": "win" if pnl > 0 else "loss",
            "pnl_pct": pnl, "outcome_source": "broker_fill", "ticket": 123456, platform: {"executed": True}}


def test_only_filled_broker_orders_are_real():
    assert guard.is_real_broker_fill(_real(0.4))
    assert guard.is_real_broker_fill(_real(-0.4, platform="mt4"))
    assert not guard.is_real_broker_fill(_simulated(0.4))
    assert not guard.is_real_broker_fill({**_real(0.4), "ticket": None})
    assert not guard.is_real_broker_fill({**_real(0.4), "mt5": {"executed": False}})
    assert not guard.is_real_broker_fill({**_real(0.4), "outcome_source": "broker_rejected"})
    assert not guard.is_real_broker_fill({**_real(0.4), "execution_mode": "demo"})
    assert not guard.is_real_broker_fill(None)


def test_simulated_outcomes_never_shift_the_threshold():
    state = {}
    for _ in range(12):
        app_module._jarvis_memory_record_execution_result(state, "XAUUSD", _simulated(-0.5))
    bucket = state["jarvis"]["memory"]["symbol_stats"]["XAUUSD"]
    assert "auto_exec_total" not in bucket and bucket["simulated_exec_total"] == 12
    assert app_module._jarvis_symbol_memory_adjustment(state, "XAUUSD")["confidence_shift"] == 0.0


def test_real_fills_shift_the_threshold_and_legacy_counters_are_kept_aside():
    state = {"jarvis": {"memory": {"events": [], "symbol_stats": {"XAUUSD": {
        "auto_exec_total": 20, "auto_exec_wins": 20, "auto_exec_losses": 0, "auto_exec_avg_pnl_pct": 1.5}}}}}
    assert app_module._jarvis_symbol_memory_adjustment(state, "XAUUSD")["confidence_shift"] == 0.0, \
        "unmarked (possibly simulated) counters must not lower the threshold"
    for _ in range(5):
        app_module._jarvis_memory_record_execution_result(state, "XAUUSD", _real(-0.6))
    bucket = state["jarvis"]["memory"]["symbol_stats"]["XAUUSD"]
    assert bucket["legacy_auto_exec_total"] == 20 and bucket["auto_exec_total"] == 5 and bucket["auto_exec_losses"] == 5
    assert app_module._jarvis_symbol_memory_adjustment(state, "XAUUSD")["confidence_shift"] > 0, "real losses raise the bar"


def test_demo_simulation_branch_marks_its_outcome_as_simulated():
    source = Path(app_module.__file__).read_text(encoding="utf-8")
    assert "trade_record['outcome_source'] = 'simulated_demo'" in source
    assert source.count("'broker_fill' if mt") == 2
