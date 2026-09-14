import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

from src import strategy_lab as lab
from src import tradingview_plan as tp

TIMES = pd.date_range("2024-01-01", periods=10, freq="4h", tz="UTC")
EXITS = {"trail_atr": 0.0, "max_bars": 50}


class _Bars:
    def __init__(self, o, h, l, c):
        self.o, self.h, self.l, self.c = (np.array(v, dtype=float) for v in (o, h, l, c))


def _one_signal(n=10, at=1, stop=95.0, target=110.0):
    side, stops, targets = np.zeros(n, dtype=int), np.full(n, np.nan), np.full(n, np.nan)
    side[at], stops[at], targets[at] = 1, stop, target
    return side, stops, targets


def test_follow_trades_open_closed_and_pending():
    flat = [100.0] * 10
    atr = np.full(10, 2.0)
    side, stops, targets = _one_signal()
    open_state = tp.follow_trades(_Bars(flat, [101.0] * 10, [99.0] * 10, flat), side, stops, targets, atr, EXITS)
    assert open_state["open"]["entry"] == 100.0 and open_state["last_closed"] is None
    hit = [101.0] * 10
    hit[5] = 111.0
    closed = tp.follow_trades(_Bars(flat, hit, [99.0] * 10, flat), side, stops, targets, atr, EXITS)
    assert closed["open"] is None and closed["last_closed"]["exit_reason"] == "take profit" and closed["last_closed"]["exit_bar"] == 5
    # Same case as the backtester's test: bar 2 makes a 106 high, so a 1 ATR (2.0) trail lifts the stop to 104 for bar 3;
    # bar 3 opens above it (105) and trades down through it (103): out at 104 on the trailing stop.
    trailed_high = [101.0] * 10
    trailed_high[2] = trailed_high[3] = 106.0
    lows = [99.0] * 10
    lows[3] = 103.0
    opens = flat[:]
    opens[3] = 105.0
    trailing = tp.follow_trades(_Bars(opens, trailed_high, lows, flat), side, stops, targets, atr,
                                {"trail_atr": 1.0, "max_bars": 50})
    assert trailing["last_closed"]["exit_reason"] == "trailing stop" and trailing["last_closed"]["exit_price"] == 104.0
    late_side, late_stops, late_targets = _one_signal(at=9)
    pending = tp.follow_trades(_Bars(flat, [101.0] * 10, [99.0] * 10, flat), late_side, late_stops, late_targets, atr, EXITS)
    assert pending["pending"]["signal_bar"] == 9 and pending["open"] is None


def test_shared_conditions_reproduce_the_signals(bars):
    ind = lab.Indicators(bars)
    params = lab.EA_SPECS["tradingview"]["params"]
    cond = lab.ema_pullback_conditions(ind, params)
    long_sig, _ = lab._signals_ema_pullback(ind, params)
    rebuilt = cond["trend_up"] & cond["push_up"] & cond["touch_up"] & cond["close_up"] & cond["rsi_up"]
    np.testing.assert_array_equal(long_sig, rebuilt)


def test_build_daily_plan_on_synthetic_candles(bars, tmp_path):
    h4 = bars[["datetime", "open", "high", "low", "close", "volume"]].copy()
    daily = (h4.set_index("datetime").resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
             .dropna().reset_index())
    now = h4["datetime"].iloc[-1] + pd.Timedelta(hours=8)
    plan = tp.build_daily_plan(h4, daily, "XAUUSD", now=now)
    assert plan["available"] and plan["places_orders"] is False
    assert plan["status"] in ("active", "new_setup", "waiting_pullback", "no_trade")
    assert len(plan["checklist"]) == 6 and plan["bias"]["label"] in ("bullish", "bearish", "mixed")
    assert plan["levels"] and "nearest_support" in plan and plan["rules"]["inputs"]["ema_slow"] == 51
    if plan["nearest_support"]:
        assert plan["nearest_support"]["price"] < plan["close"] and plan["nearest_support"]["distance_atr"] < 0
    if plan["nearest_resistance"]:
        assert plan["nearest_resistance"]["price"] > plan["close"] and plan["nearest_resistance"]["distance_atr"] > 0
    if plan["status"] != "no_trade":
        assert plan["entry"] and plan["stop_loss"] and plan["stop_loss"] < plan["entry"]
        if plan["take_profit"]:
            assert plan["take_profit"] > plan["entry"]
    json.dumps(plan)  # must be plain JSON for the API
    path = tp.save_daily_plan(plan, tmp_path)
    assert path.exists() and (tmp_path / "latest_XAUUSD.json").exists()
    assert tp.build_daily_plan(h4.iloc[:100], daily.iloc[:20], "XAUUSD", now=now)["available"] is False
    assert "SmartEntry Daily Plan" in (tp.pine_script_text() or "")
