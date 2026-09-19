"""Aurum Flow replica: the entry rules, and the guarantee that it cannot trade.

The point of these is that the replica is faithful to `Aurum Flow.mq4` where it claims to be, and
honest about where it is not. Every test builds its own bars; none needs the broker feed.
"""
import numpy as np
import pandas as pd

from src import aurum_flow_lab as af
from src import strategy_lab as lab


def _bars_from(highs, lows=None, closes=None, start="2026-01-05 00:00", freq="15min"):
    n = len(highs)
    lows = lows if lows is not None else [h - 10.0 for h in highs]
    closes = closes if closes is not None else [(h + l) / 2 for h, l in zip(highs, lows)]
    return pd.DataFrame({"datetime": pd.date_range(start, periods=n, freq=freq, tz="UTC"),
                         "open": closes, "high": highs, "low": lows, "close": closes,
                         "volume": [1] * n})


def _spec(**overrides):
    params = dict(symbol="XAUUSD", timeframe="15m", structure_depth=20, spacing=5, refresh_bars=5,
                  ma_period=0, entry_points=af.ENTRY_POINTS, expiry_minutes=af.EXPIRY_MINUTES,
                  sl_points=af.SL_POINTS, tp_points=af.TP_POINTS, block_nov_dec=False)
    params.update(overrides)
    return {"family": "aurum_flow", "params": params,
            "exits": {"stop": "fixed", "sl_atr": 0.0, "rr": 0.0, "trail_atr": 0.0,
                      "max_bars": 480, "swing_lookback": 0},
            "description": "test", "variant": "test"}


# --- the pieces the EA is built from -------------------------------------------

def test_swing_high_is_a_three_bar_fractal():
    highs = np.array([1.0, 3.0, 2.0, 5.0, 4.0])
    assert list(af.swing_highs(highs)) == [False, True, False, True, False]


def test_swing_low_is_the_mirror():
    lows = np.array([5.0, 2.0, 4.0, 1.0, 3.0])
    assert list(af.swing_lows(lows)) == [False, True, False, True, False]


def test_the_two_anchors_must_be_spacing_bars_apart():
    """FindTwoHighPeaks rejects a second peak closer than StructureSpacing and looks further back."""
    values = np.zeros(30)
    values[10], values[11], values[25] = 100.0, 99.0, 90.0   # 11 is the tallest neighbour of 10
    is_swing = np.zeros(30, dtype=bool)
    is_swing[[10, 11, 25]] = True
    found = af._two_extremes(values, is_swing, 0, 29, spacing=5, want_high=True)
    assert found == (10, 25)   # not (10, 11)


def test_no_anchor_pair_gives_nothing_rather_than_a_guess():
    values = np.zeros(10)
    values[4] = 5.0
    is_swing = np.zeros(10, dtype=bool)
    is_swing[4] = True
    assert af._two_extremes(values, is_swing, 0, 9, spacing=5, want_high=True) is None


def test_the_line_extrapolates_through_both_anchors():
    at = np.array([0.0, 5.0, 10.0, 20.0])
    out = af._line(0, 100.0, 10, 110.0, at)
    assert list(out) == [100.0, 105.0, 110.0, 120.0]


def test_the_trendline_never_uses_a_later_bar():
    """The value at bar i must not change when bars after i change."""
    rng = np.random.default_rng(7)
    highs = 4000 + np.cumsum(rng.normal(0, 5, 120))
    first = af.trendlines(lab.Indicators(_bars_from(list(highs))), 20, 5, 5)[0]
    tampered = highs.copy()
    tampered[100:] += 500.0
    second = af.trendlines(lab.Indicators(_bars_from(list(tampered))), 20, 5, 5)[0]
    finite = np.isfinite(first[:100]) & np.isfinite(second[:100])
    assert finite.any()
    assert np.allclose(first[:100][finite[:100]], second[:100][finite[:100]])


# --- the entry rule -------------------------------------------------------------

def _breakout_bars(n=60, break_at=40, run_up=60.0):
    """A flat range, then two bars that close above everything, then a bar that runs on."""
    highs = [4000.0 + (i % 5) for i in range(n)]
    highs[8], highs[9], highs[10] = 4010.0, 4009.0, 4008.0
    highs[25] = 4012.0
    for i in range(break_at, n):
        highs[i] = 4100.0 + run_up
    return _bars_from(highs)


def test_two_bars_beyond_the_line_are_required():
    """One bar through the trendline is not a signal; the EA needs candle 1 and candle 2."""
    ind = lab.Indicators(_breakout_bars())
    upper, _ = af.trendlines(ind, 20, 5, 5)
    side, _, _, _ = af.aurum_orders(ind, _spec())
    for row in np.flatnonzero(side):
        fill = row + 1
        one, two = fill - 2, fill - 3   # the decision bar was fill-1, so candle 1 and 2 are these
        if side[row] == 1 and np.isfinite(upper[one]) and np.isfinite(upper[two]):
            assert ind.h[one] >= upper[one] and ind.h[two] >= upper[two]


def test_entry_sits_the_pending_distance_beyond_the_signal_bar():
    ind = lab.Indicators(_breakout_bars())
    side, stop, target, entry = af.aurum_orders(ind, _spec())
    rows = np.flatnonzero(side)
    assert len(rows) > 0, "the fixture must produce at least one signal"
    row = rows[0]
    one = row - 1                      # candle 1 relative to the decision bar (row + 1 is the fill)
    expected = ind.h[one] + af.ENTRY_POINTS * af.POINT_VALUE if side[row] == 1 else \
        ind.l[one] - af.ENTRY_POINTS * af.POINT_VALUE
    assert entry[row] == expected


def test_the_stop_is_larger_than_the_target_as_shipped():
    """SL 2100 / TP 1800 points: the EA risks $21 to make $18, so 0.857 R."""
    ind = lab.Indicators(_breakout_bars())
    side, stop, target, entry = af.aurum_orders(ind, _spec())
    row = np.flatnonzero(side)[0]
    risk = abs(entry[row] - stop[row])
    reward = abs(target[row] - entry[row])
    assert round(risk, 2) == 21.00
    assert round(reward, 2) == 18.00
    assert reward < risk


def test_a_wider_target_is_honoured():
    ind = lab.Indicators(_breakout_bars())
    side, stop, target, entry = af.aurum_orders(ind, _spec(tp_points=3150))
    row = np.flatnonzero(side)[0]
    assert round(abs(target[row] - entry[row]) / abs(entry[row] - stop[row]), 2) == 1.50


def test_the_moving_average_filter_removes_signals_it_disagrees_with():
    ind = lab.Indicators(_breakout_bars())
    unfiltered = np.count_nonzero(af.aurum_orders(ind, _spec(ma_period=0))[0])
    filtered = np.count_nonzero(af.aurum_orders(ind, _spec(ma_period=30))[0])
    assert unfiltered > 0
    assert filtered <= unfiltered


def test_blocking_november_and_december_drops_those_signals():
    """TradeInNovember and TradeInDecember are both false by default in the EA."""
    bars = _breakout_bars()
    bars["datetime"] = pd.date_range("2026-11-20 00:00", periods=len(bars), freq="15min", tz="UTC")
    ind = lab.Indicators(bars)
    assert np.count_nonzero(af.aurum_orders(ind, _spec(block_nov_dec=True))[0]) == 0
    assert np.count_nonzero(af.aurum_orders(ind, _spec(block_nov_dec=False))[0]) > 0


def test_the_inverse_baseline_flips_the_side_and_keeps_the_levels():
    ind = lab.Indicators(_breakout_bars())
    side, stop, target, entry = af.aurum_orders(ind, _spec())
    inv_side, inv_stop, inv_target, inv_entry = af.aurum_orders(ind, _spec(inverse=True))
    rows = np.flatnonzero(side)
    assert len(rows) > 0
    for row in rows:
        assert inv_side[row] == -side[row]
        assert inv_entry[row] == entry[row]          # same level, same fill bar
        assert round(abs(inv_entry[row] - inv_stop[row]), 2) == 21.00


def test_the_declared_grid_is_eight_variants():
    """strategies/aurum_flow.md declares 8 per market and timeframe; the deflated Sharpe counts them."""
    specs = af.aurum_variants("XAUUSD", "15m", 480)
    assert len(specs) == 8
    assert len({s["variant"] for s in specs}) == 8
    assert all(s["family"] == "aurum_flow" for s in specs)


# --- the safety guarantee -------------------------------------------------------

def test_the_replica_has_no_order_path():
    """Research only. If this file ever grows a way to trade, this test is the alarm."""
    text = open(af.__file__.replace(".pyc", ".py"), encoding="utf-8").read()
    for forbidden in ("order_send", "OrderSend", "place_order", "auto_execute", "MetaTrader5", "demo_executor"):
        assert forbidden not in text, f"{forbidden} must not appear in the replica"


def test_the_recovery_cascade_is_not_replicated_and_says_so():
    """The martingale is the part that makes the vendor's curve; leaving it out must stay explicit."""
    text = open(af.__file__.replace(".pyc", ".py"), encoding="utf-8").read()
    assert "NOT replicated" in text
    assert "RecoveryMode2" in text
