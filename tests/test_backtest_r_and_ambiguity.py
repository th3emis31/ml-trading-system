"""Two corrections to the engine's arithmetic, both found on 19 September 2026.

1. Every R this system reported was GROSS. ``r_multiple`` divided the pre-cost return by the risk, so a
   strategy that lost money after spread and swap could still show a positive average R - and one did:
   a 1R sweep variant on gold 4H reported avg_r +0.02 while its true after-cost expectancy was -0.0284.
2. When a single bar reaches both the stop and the target, the engine books the stop. That is the safe
   choice, but it is a choice, and nothing used to say how often it was being made.
"""
import numpy as np
import pandas as pd
import pytest

from src import strategy_lab as lab
from src.walkforward_backtest import summarize_trades


def _bars(rows, freq="4h"):
    return pd.DataFrame({"datetime": pd.date_range("2026-01-01", periods=len(rows), freq=freq, tz="UTC"),
                         "open": [r[0] for r in rows], "high": [r[1] for r in rows],
                         "low": [r[2] for r in rows], "close": [r[3] for r in rows],
                         "volume": [1000.0] * len(rows)})


def _run(rows, *, cost_pct=0.0, side_at=0, stop=None, target=None, exits=None):
    ind = lab.Indicators(_bars(rows))
    n = len(rows)
    side = np.zeros(n, dtype=int); side[side_at] = 1
    stops = np.full(n, np.nan); stops[side_at] = stop
    targets = np.full(n, np.nan); targets[side_at] = target
    return lab.simulate_orders(ind.o, ind.h, ind.l, ind.c, ind.atr(14), ind.times, side, stops, targets,
                               np.arange(n), exits or {"max_bars": 50}, cost_pct, None, None)


# --- 1. R must be net of costs ----------------------------------------------------

def test_the_trade_carries_both_a_gross_and_an_after_cost_r():
    rows = [(100.0, 100.5, 99.5, 100.0)] * 2 + [(100.0, 102.0, 99.9, 101.9)] + [(101.9, 102.0, 101.8, 101.9)] * 2
    trades = _run(rows, cost_pct=0.001, stop=99.0, target=102.0)
    assert len(trades) == 1
    t = trades[0]
    assert t["outcome"] == "TARGET"
    assert t["net_r"] < t["r_multiple"], "costs must lower the R of every trade"


def test_the_cost_in_r_is_the_cost_in_price_over_the_risk():
    """The whole bug in one assertion: a cost is a slice of the entry PRICE, so its size in R depends on
    how tight the stop is. A 0.1 % cost on a 1 % stop is 0.1 R; on a 0.1 % stop it is a whole R."""
    rows = [(100.0, 100.5, 99.5, 100.0)] * 2 + [(100.0, 102.0, 99.9, 101.9)] + [(101.9, 102.0, 101.8, 101.9)] * 2
    wide = _run(rows, cost_pct=0.001, stop=99.0, target=102.0)[0]      # risk 1 % of price
    tight = _run(rows, cost_pct=0.001, stop=99.9, target=102.0)[0]     # risk 0.1 % of price
    assert wide["r_multiple"] - wide["net_r"] == pytest.approx(0.1, abs=0.01)
    assert tight["r_multiple"] - tight["net_r"] == pytest.approx(1.0, abs=0.05)


def test_a_strategy_can_win_in_gross_r_and_lose_in_net_r():
    """This is what the old reporting hid, not a hypothetical: it happened on gold."""
    summary = summarize_trades([{"side": "BUY", "net_pct": -0.05, "bars_held": 1, "r_multiple": 0.02, "net_r": -0.03}],
                               test_start="2026-01-01", test_end="2026-07-01", test_bars=100, bars_in_market=1)
    assert summary["avg_r"] > 0 and summary["expectancy_r"] < 0
    assert summary["avg_r_basis"] == "gross R, before spread and swap"


# --- 2. the same-bar coin flip ----------------------------------------------------

def test_a_bar_that_touches_only_the_stop_is_not_ambiguous():
    rows = [(100.0, 100.5, 99.5, 100.0)] * 2 + [(100.0, 100.2, 98.5, 98.6)] + [(98.6, 98.7, 98.5, 98.6)] * 2
    t = _run(rows, stop=99.0, target=102.0)[0]
    assert t["outcome"] == "STOP" and t["ambiguous_exit"] is False


def test_a_bar_that_touches_both_is_flagged_and_still_booked_as_the_stop():
    """Pessimism stays the default - but it is now visible instead of silent."""
    rows = [(100.0, 100.5, 99.5, 100.0)] * 2 + [(100.0, 102.5, 98.5, 100.0)] + [(100.0, 100.1, 99.9, 100.0)] * 2
    t = _run(rows, stop=99.0, target=102.0)[0]
    assert t["outcome"] == "STOP", "the safe assumption is unchanged"
    assert t["ambiguous_exit"] is True, "but the engine must admit it assumed"


def test_the_bound_says_what_the_split_would_be_if_the_coin_flips_landed_the_other_way():
    trades = [{"side": "BUY", "net_pct": -1.0, "bars_held": 1, "r_multiple": -1.0, "net_r": -1.0,
               "target_r": 2.0, "ambiguous_exit": True},
              {"side": "BUY", "net_pct": 2.0, "bars_held": 1, "r_multiple": 2.0, "net_r": 2.0,
               "target_r": 2.0, "ambiguous_exit": False}]
    out = summarize_trades(trades, test_start="2026-01-01", test_end="2026-07-01", test_bars=100, bars_in_market=2)
    assert out["ambiguous_exits"] == 1 and out["ambiguous_exit_pct"] == 50.0
    assert out["expectancy_r"] == pytest.approx(0.5)      # (-1 + 2) / 2 as measured
    assert out["expectancy_r_bound"] == pytest.approx(2.0)  # (+2 + 2) / 2 if the target had come first


def test_no_ambiguous_exits_means_no_bound_to_report():
    out = summarize_trades([{"side": "BUY", "net_pct": 1.0, "bars_held": 1, "r_multiple": 1.0, "net_r": 1.0}],
                           test_start="2026-01-01", test_end="2026-07-01", test_bars=10, bars_in_market=1)
    assert out["ambiguous_exits"] == 0 and out["expectancy_r_bound"] is None


def test_a_split_whose_sign_depends_on_the_assumption_is_reported_as_unresolved():
    assert lab._outcome_is_resolved({"expectancy_r": -0.028, "expectancy_r_bound": 0.017}) is False
    assert lab._outcome_is_resolved({"expectancy_r": 0.05, "expectancy_r_bound": 0.06}) is True
    assert lab._outcome_is_resolved({"expectancy_r": 0.05, "expectancy_r_bound": None}) is True
    assert lab._outcome_is_resolved({"expectancy_r": None}) is None
