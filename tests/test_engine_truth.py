"""Can the backtest engine find an edge that is definitely there? If not, every result it gave is void.

The owner's charge, 26 September 2026: *"your mechanism is making complete wrong calculation, the
backtest is completely wrong, the backtest is useless... you still continue making huge mistakes."*

It deserves a test, not an argument. Every other test in this project checks a STRATEGY against the
engine. These check the ENGINE against arithmetic that is known before it runs:

1. A planted edge it must find. Price is built so that after a signal it always rises before it falls.
   An engine that reports a loss on this is broken, and so is everything it ever reported.
2. A planted LOSS it must not flatter.
3. Buy and hold, where the answer is simply the price change.
4. Costs, which must equal the spread times the number of trades and nothing more.
5. A coin-flip market, where the result must be about zero minus costs - not systematically worse.
6. The fill rule: entries at the NEXT bar's open, never the signal bar's close.

If these pass, a losing backtest is a fact about the strategy. If any fails, it is a fact about the
engine, and the owner is right.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.strategy_lab import simulate_orders, summarize_trades

EXITS = {"stop": "fixed", "sl_atr": 0.0, "rr": 0.0, "trail_atr": 0.0, "max_bars": 50,
         "swing_lookback": 0}


def ramp_market(n=600, start=100.0, step=1.0, wiggle=0.2):
    """A market that rises `step` per bar. Deterministic, so every expectation below is arithmetic."""
    close = start + np.arange(n) * step
    open_ = close - step
    high = close + wiggle
    low = open_ - wiggle
    times = pd.to_datetime(pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC"))
    atr = np.full(n, step * 3)
    return open_, high, low, close, atr, times


def run(side, stop, target, o, h, l, c, atr, times, cost=0.0, exits=None, rows=None):
    rows = np.arange(50, len(c) - 60) if rows is None else rows
    trades = simulate_orders(o, h, l, c, atr, times, side, stop, target, rows,
                             exits or EXITS, cost, None, None)
    return trades, summarize_trades(trades, test_start=times[0], test_end=times[-1],
                                    test_bars=len(c), bars_in_market=sum(t["bars_held"] for t in trades))


# --- 1. the planted edge -------------------------------------------------------------------------

def test_the_engine_finds_an_edge_that_is_definitely_there():
    """Every long is entered into a market that only rises. The target must be hit, every time.

    If this fails, no negative result this engine has ever produced means anything.
    """
    o, h, l, c, atr, times = ramp_market()
    n = len(c)
    side = np.zeros(n, dtype=int)
    side[np.arange(60, n - 80, 20)] = 1                 # a long every 20 bars
    stop = c - 10 * 1.0                                  # 10 points of risk
    target = c + 10 * 1.0                                # 10 points of reward, reached in ~10 bars
    trades, out = run(side, stop, target, o, h, l, c, atr, times)

    assert out["trades"] >= 20, f"only {out['trades']} trades were taken from {len(trades)} signals"
    assert out["win_rate_pct"] == pytest.approx(100.0, abs=0.01), (
        f"a market that only rises produced a {out['win_rate_pct']}% win rate - the engine is wrong")
    assert out["total_return_pct"] > 0
    # profit_factor is None with zero losing trades, which is the honest answer rather than infinity.
    assert out["profit_factor"] is None or out["profit_factor"] > 5


def test_the_engine_reports_a_planted_loss_as_a_loss():
    """The mirror. Shorts in a market that only rises must lose every time - no flattering."""
    o, h, l, c, atr, times = ramp_market()
    n = len(c)
    side = np.zeros(n, dtype=int)
    side[np.arange(60, n - 80, 20)] = -1
    stop = c + 10.0
    target = c - 10.0
    _trades, out = run(side, stop, target, o, h, l, c, atr, times)
    assert out["win_rate_pct"] == pytest.approx(0.0, abs=0.01)
    assert out["total_return_pct"] < 0


# --- 2. buy and hold, where the answer is the price change ----------------------------------------

def test_one_long_held_to_the_end_earns_the_price_change():
    """The simplest possible check that R and % are computed the right way round."""
    o, h, l, c, atr, times = ramp_market(n=300, start=100.0, step=1.0)
    n = len(c)
    side = np.zeros(n, dtype=int)
    side[50] = 1
    stop = np.full(n, 1.0)                               # far away: never hit
    target = np.full(n, 10_000.0)                        # far away: never hit
    exits = {**EXITS, "max_bars": 100}                   # so it closes on the time exit
    trades, _out = run(side, stop, target, o, h, l, c, atr, times, exits=exits, rows=np.array([50]))
    assert len(trades) == 1
    entry, exit_price = trades[0]["entry_price"], trades[0]["exit_price"]
    expected = (exit_price - entry) / entry * 100
    assert trades[0]["net_pct"] == pytest.approx(expected, rel=1e-6), (
        "the trade's return does not equal its own entry-to-exit move")
    assert exit_price > entry, "a rising market held for 100 bars must exit higher"


# --- 3. costs: exactly the spread, once per trade -------------------------------------------------

def test_cost_is_charged_once_per_trade_and_equals_the_spread():
    o, h, l, c, atr, times = ramp_market()
    n = len(c)
    side = np.zeros(n, dtype=int)
    signals = np.arange(60, n - 80, 20)
    side[signals] = 1
    stop, target = c - 10.0, c + 10.0

    # THE CONVENTION, which is a trap worth stating: `cost_pct` is a FRACTION of the entry price, not a
    # percentage, despite the name. 0.0005 means 0.05 %. Reading it as a percentage is how this project
    # once ran a cost model six times too harsh (BASELINE, 19 September), and an earlier version of this
    # very test passed 0.05 meaning "0.05 %" and was charged 5 %.
    cost_fraction = 0.0005
    free_trades, gross = run(side, stop, target, o, h, l, c, atr, times, cost=0.0)
    paid_trades, net = run(side, stop, target, o, h, l, c, atr, times, cost=cost_fraction)

    assert gross["trades"] == net["trades"]
    # Checked PER TRADE, not on the totals. `total_return_pct` compounds, so a fixed cost per trade does
    # not subtract linearly from it - an earlier version of this test compared the compounded totals and
    # "found" a 118x discrepancy that was entirely its own arithmetic.
    for free, paid in zip(free_trades, paid_trades):
        assert paid["gross_pct"] == pytest.approx(free["gross_pct"], rel=1e-9), "cost moved the gross move"
        assert paid["net_pct"] == pytest.approx(free["net_pct"] - cost_fraction * 100, abs=1e-6), (
            f"net {paid['net_pct']} is not gross {free['net_pct']} minus {cost_fraction * 100}%")
    assert net["total_return_pct"] < gross["total_return_pct"], "charging a spread must reduce the return"


def test_zero_cost_means_zero_cost():
    """A free round trip must return exactly the price move, so costs cannot be hiding anywhere."""
    o, h, l, c, atr, times = ramp_market()
    n = len(c)
    side = np.zeros(n, dtype=int)
    side[60] = 1
    stop, target = c - 10.0, c + 10.0
    trades, _out = run(side, stop, target, o, h, l, c, atr, times, cost=0.0, rows=np.array([60]))
    trade = trades[0]
    assert trade["net_pct"] == pytest.approx((trade["exit_price"] - trade["entry_price"]) / trade["entry_price"] * 100,
                                                rel=1e-9)


# --- 4. a coin-flip market must come out near zero, not systematically negative --------------------

def test_random_signals_on_a_random_walk_are_about_flat_before_costs():
    """The charge is that the engine loses money on everything. On a market with no edge and no costs,
    a symmetric strategy must land near zero - if it is deeply negative, the engine has a leak."""
    rng = np.random.default_rng(20260926)
    n = 4000
    steps = rng.normal(0, 1.0, n)
    close = 1000 + np.cumsum(steps)
    open_ = np.concatenate(([close[0]], close[:-1]))
    high = np.maximum(open_, close) + rng.uniform(0.1, 0.6, n)
    low = np.minimum(open_, close) - rng.uniform(0.1, 0.6, n)
    times = pd.to_datetime(pd.date_range("2020-01-01", periods=n, freq="4h", tz="UTC"))
    atr = pd.Series(high - low).rolling(14).mean().bfill().to_numpy()

    side = np.zeros(n, dtype=int)
    picks = rng.choice(np.arange(60, n - 80), size=600, replace=False)
    side[picks] = rng.choice([1, -1], size=len(picks))
    risk = 2.0 * atr
    stop = np.where(side == 1, close - risk, np.where(side == -1, close + risk, np.nan))
    target = np.where(side == 1, close + risk, np.where(side == -1, close - risk, np.nan))

    _trades, out = run(side, stop, target, open_, high, low, close, atr, times, cost=0.0,
                       exits={**EXITS, "max_bars": 80})
    assert out["trades"] > 100
    # A symmetric 1:1 bet on a driftless walk: the win rate must be near a coin flip.
    assert 40 <= out["win_rate_pct"] <= 60, (
        f"a fair 1:1 bet on a random walk won {out['win_rate_pct']}% - the engine is not fair")
    assert abs(out["expectancy_r"]) < 0.15, (
        f"expectancy {out['expectancy_r']} R on a no-edge market means the engine leaks")


# --- 5. the fill rule ------------------------------------------------------------------------------

def test_entry_is_the_next_bars_open_never_the_signal_bars_close():
    """Filling at the signal bar's close is the single most common way a backtest invents money."""
    o, h, l, c, atr, times = ramp_market()
    n = len(c)
    side = np.zeros(n, dtype=int)
    side[100] = 1
    stop, target = c - 10.0, c + 10.0
    trades, _out = run(side, stop, target, o, h, l, c, atr, times, rows=np.array([100]))
    assert trades[0]["entry_price"] == pytest.approx(o[101], abs=0.01), (
        f"entered at {trades[0]['entry_price']}, next open is {o[101]}, signal close was {c[100]}")


def test_a_signal_on_the_last_bar_is_not_traded():
    """There is no next bar to fill on, so taking it would be inventing a price."""
    o, h, l, c, atr, times = ramp_market(n=200)
    n = len(c)
    side = np.zeros(n, dtype=int)
    side[n - 1] = 1
    stop, target = c - 10.0, c + 10.0
    trades, _out = run(side, stop, target, o, h, l, c, atr, times, rows=np.array([n - 1]))
    assert trades == []


def test_one_position_at_a_time():
    """Overlapping trades would multiply the return without multiplying the risk."""
    o, h, l, c, atr, times = ramp_market()
    n = len(c)
    side = np.zeros(n, dtype=int)
    side[60:120] = 1                                    # a signal on every bar
    stop = c - 40.0                                      # wide, so each trade lasts many bars
    target = c + 40.0
    trades, _out = run(side, stop, target, o, h, l, c, atr, times, rows=np.arange(60, 120))
    for earlier, later in zip(trades, trades[1:]):
        assert later["entry_idx"] >= earlier["entry_idx"] + earlier["bars_held"], "two trades overlapped"


def test_the_cost_argument_is_a_fraction_not_a_percentage():
    """Pinned because the name says otherwise and the mistake is expensive in both directions.

    `simulate_orders(..., cost_pct=x)` subtracts x from the trade's FRACTIONAL return before scaling to a
    percentage. So x=0.0001 charges 0.01 %. Reading it as a percentage charges a hundred times too much,
    which turns every real edge negative - exactly the shape of "every strategy loses".
    """
    o, h, l, c, atr, times = ramp_market()
    n = len(c)
    side = np.zeros(n, dtype=int)
    side[60] = 1
    stop, target = c - 10.0, c + 10.0
    free, _ = run(side, stop, target, o, h, l, c, atr, times, cost=0.0, rows=np.array([60]))
    paid, _ = run(side, stop, target, o, h, l, c, atr, times, cost=0.01, rows=np.array([60]))
    charged = free[0]["net_pct"] - paid[0]["net_pct"]
    assert charged == pytest.approx(1.0, abs=1e-6), (
        f"cost=0.01 removed {charged}% - it is a fraction, so it must remove exactly 1%")


def test_the_real_gold_cost_is_the_size_the_record_says_it_is():
    """A guard on the number itself: gold's round trip is about 0.009 %, so the stored constant must be
    near 0.00009 as a fraction. A constant an order of magnitude out is invisible until every backtest
    comes back negative."""
    from src.strategy_lab import BACKTEST_COSTS

    gold = BACKTEST_COSTS["XAUUSD"]["round_trip_pct"]
    assert 0.00002 <= gold <= 0.0005, (
        f"gold round-trip cost is {gold} as a fraction, i.e. {gold * 100:.4f}% - outside the measured range")


# --- the hazard that broke a real study: levels smaller than one bar ---------------------------------

def test_levels_smaller_than_one_bar_bias_the_win_rate_and_the_engine_says_so():
    """Not an engine fault - an engine LIMIT, and one that silently destroyed a real measurement.

    A stop and target both inside a single candle's range means the bar touched both, and the engine books
    the stop. On 27 September 2026 a scalp study sized its levels at 0.3 x the 15m ATR - about 2.1 points
    against a 7.2-point average 15m bar - and a fair 1:1 bet measured 37.3 % instead of 50 %. The missing
    thirteen points were the stop-first convention, applied to a quarter of all trades.

    The cause was a unit error: the owner's trades were measured as 0.35 ATR on HOURLY bars (6.4 points)
    and that fraction was then applied to a 15m frame, making the levels a third of their real size.

    So: `ambiguous_exits` is the number to read before believing any tight-stop result, and the fix is
    finer bars or wider levels - never a different convention.
    """
    rng = np.random.default_rng(4)
    n = 6000
    close = 2000 + np.cumsum(rng.normal(0, 1.2, n))
    open_ = np.concatenate(([close[0]], close[:-1]))
    # Deliberately wide bars: each candle's range dwarfs the levels below.
    high = np.maximum(open_, close) + rng.uniform(2.0, 4.0, n)
    low = np.minimum(open_, close) - rng.uniform(2.0, 4.0, n)
    times = pd.to_datetime(pd.date_range("2024-01-01", periods=n, freq="15min", tz="UTC"))
    atr = np.full(n, 6.0)

    rows = np.arange(100, n - 40, 5)
    side = np.zeros(n, dtype=int)
    side[rows] = -1
    exits = {**EXITS, "max_bars": 8}

    tight = simulate_orders(open_, high, low, close, atr, times, side, close + 1.0, close - 1.0,
                            rows, exits, 0.0, None, None)
    wide = simulate_orders(open_, high, low, close, atr, times, side, close + 12.0, close - 12.0,
                           rows, exits, 0.0, None, None)

    def summarise(trades):
        return summarize_trades(trades, test_start=times[0], test_end=times[-1], test_bars=n,
                                bars_in_market=sum(t["bars_held"] for t in trades))

    tight_out, wide_out = summarise(tight), summarise(wide)
    tight_ambiguous = tight_out.get("ambiguous_exits") or 0
    assert tight_ambiguous > 0, "levels inside one bar must produce ambiguous exits"
    assert tight_out["win_rate_pct"] < wide_out["win_rate_pct"], (
        "levels inside a single bar must measure WORSE than levels the bar cannot straddle - "
        "that gap is the convention, not the market")
    # And with levels the bar cannot straddle, the same fair bet is near the coin flip.
    assert 42 <= wide_out["win_rate_pct"] <= 58, (
        f"a fair 1:1 with levels wider than the bars measured {wide_out['win_rate_pct']}%")


# --- one-sided specs must mean the same thing in both code paths ------------------------------------

def test_a_side_restricted_spec_is_honoured_by_a_registered_builder():
    """`params["side"]` constrained the built-in signal path and was ignored by registered builders, so
    the same key meant two different things. A long-only result stored as a spec would then be re-run
    two-sided by the strategy book and stop matching its own recorded evidence."""
    from src.strategy_lab import ORDER_BUILDERS, strategy_orders

    o, h, l, c, atr, times = ramp_market(n=120)
    n = len(c)

    def both_ways(ind, spec):
        side = np.zeros(n, dtype=int)
        side[40] = 1
        side[60] = -1
        return side, c - 5.0, c + 5.0

    ORDER_BUILDERS["_side_probe"] = both_ways
    try:
        frame = pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": 1.0})
        frame["datetime"] = times
        from src.strategy_lab import Indicators

        ind = Indicators(frame)
        base = strategy_orders(ind, {"family": "_side_probe", "params": {}, "exits": {}})[0]
        assert set(base.tolist()) == {0, 1, -1}

        longs = strategy_orders(ind, {"family": "_side_probe", "params": {"side": "long"}, "exits": {}})[0]
        shorts = strategy_orders(ind, {"family": "_side_probe", "params": {"side": "short"}, "exits": {}})[0]
        assert longs[40] == 1 and longs[60] == 0, "a long-only spec must drop the short"
        assert shorts[60] == -1 and shorts[40] == 0, "a short-only spec must drop the long"
    finally:
        ORDER_BUILDERS.pop("_side_probe", None)
