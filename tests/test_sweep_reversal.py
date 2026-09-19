"""The 4H manipulation candle: the sweep must be a sweep, and the close must be the signal."""
import numpy as np
import pandas as pd
import pytest

from src import strategy_lab as lab
from src import sweep_reversal as sr


def _bars_from_rows(rows, freq="4h"):
    """rows = (open, high, low, close)."""
    return pd.DataFrame({"datetime": pd.date_range("2026-01-01", periods=len(rows), freq=freq, tz="UTC"),
                         "open": [r[0] for r in rows], "high": [r[1] for r in rows],
                         "low": [r[2] for r in rows], "close": [r[3] for r in rows],
                         "volume": [1000.0] * len(rows)})


def _sweep_spec(**kw):
    params = {"symbol": "XAUUSD", "timeframe": "4h", "lookback": 5, "require_body": True, "rr": 2.0}
    params.update(kw)
    return {"family": "sweep_reversal", "params": params,
            "exits": {"stop": "fixed", "sl_atr": 0.0, "rr": 0.0, "trail_atr": 0.0,
                      "max_bars": 30, "swing_lookback": 0}}


def _flat_bars(n=40, price=4000.0):
    return [(price, price + 2, price - 2, price) for _ in range(n)]


def test_a_candle_that_sweeps_the_prior_high_and_closes_back_below_is_a_short():
    rows = _flat_bars(30)
    rows.append((4000.0, 4050.0, 3998.0, 3995.0))      # wick above everything, closes back below
    ind = lab.Indicators(_bars_from_rows(rows))
    side, stop, target = sr.sweep_orders(ind, _sweep_spec())
    assert side[-1] == -1
    assert stop[-1] > 4050.0, "the stop must sit beyond the sweep wick"
    assert target[-1] < 3995.0


def test_the_mirror_is_a_long():
    rows = _flat_bars(30)
    rows.append((4000.0, 4002.0, 3950.0, 4005.0))      # wick below everything, closes back above
    ind = lab.Indicators(_bars_from_rows(rows))
    side, stop, target = sr.sweep_orders(ind, _sweep_spec())
    assert side[-1] == 1
    assert stop[-1] < 3950.0
    assert target[-1] > 4005.0


def test_a_candle_that_closes_BEYOND_the_level_is_a_breakout_not_a_sweep():
    """The whole point is the rejection: closing outside means the level gave way."""
    rows = _flat_bars(30)
    rows.append((4000.0, 4050.0, 3998.0, 4045.0))      # swept and HELD above
    ind = lab.Indicators(_bars_from_rows(rows))
    assert sr.sweep_orders(ind, _sweep_spec())[0][-1] == 0


def test_a_candle_that_never_reaches_the_level_is_not_a_sweep():
    rows = _flat_bars(30)
    rows.append((4000.0, 4001.0, 3998.0, 3999.0))
    ind = lab.Indicators(_bars_from_rows(rows))
    assert sr.sweep_orders(ind, _sweep_spec())[0][-1] == 0


def test_the_level_excludes_the_candle_being_tested():
    """Without shift(1) a candle would be compared with a maximum it set itself, and never sweep."""
    rows = _flat_bars(30)
    rows.append((4000.0, 4050.0, 3998.0, 3995.0))
    ind = lab.Indicators(_bars_from_rows(rows))
    assert sr.sweep_orders(ind, _sweep_spec())[0][-1] == -1


def test_the_body_filter_rejects_a_candle_that_closed_up():
    rows = _flat_bars(30)
    rows.append((3990.0, 4050.0, 3988.0, 3995.0))      # swept, closed back inside, but closed UP
    ind = lab.Indicators(_bars_from_rows(rows))
    assert sr.sweep_orders(ind, _sweep_spec(require_body=True))[0][-1] == 0
    assert sr.sweep_orders(ind, _sweep_spec(require_body=False))[0][-1] == -1


def test_a_candle_that_sweeps_both_extremes_is_not_a_signal():
    """It took liquidity on both sides, so it says nothing about direction."""
    rows = _flat_bars(30)
    rows.append((4000.0, 4060.0, 3940.0, 3999.0))
    ind = lab.Indicators(_bars_from_rows(rows))
    assert sr.sweep_orders(ind, _sweep_spec(require_body=False))[0][-1] == 0


def test_the_reward_ratio_sets_the_target_distance():
    rows = _flat_bars(30)
    rows.append((4000.0, 4050.0, 3998.0, 3995.0))
    ind = lab.Indicators(_bars_from_rows(rows))
    out = {}
    for rr in (1.0, 3.0):
        side, stop, target = sr.sweep_orders(ind, _sweep_spec(rr=rr))
        risk = stop[-1] - ind.c[-1]
        out[rr] = (ind.c[-1] - target[-1]) / risk
    assert out[1.0] == pytest.approx(1.0, abs=0.01)
    assert out[3.0] == pytest.approx(3.0, abs=0.01)


def test_the_inverse_flips_the_side_and_mirrors_the_levels():
    rows = _flat_bars(30)
    rows.append((4000.0, 4050.0, 3998.0, 3995.0))
    ind = lab.Indicators(_bars_from_rows(rows))
    side, stop, target = sr.sweep_orders(ind, _sweep_spec())
    inv_side, inv_stop, inv_target = sr.sweep_orders(ind, _sweep_spec(inverse=True))
    assert inv_side[-1] == -side[-1]
    close = ind.c[-1]
    assert inv_stop[-1] == pytest.approx(2 * close - stop[-1])
    assert inv_target[-1] == pytest.approx(2 * close - target[-1])
    assert inv_stop[-1] < close < inv_target[-1], "the inverse long must have its stop below the close"


def test_no_look_ahead():
    rng = np.random.default_rng(5)
    close = 4000 + np.cumsum(rng.normal(0, 10, 500))
    rows = [(c, c + 15, c - 15, c + 1) for c in close]
    full = lab.Indicators(_bars_from_rows(rows))
    cut = 400
    part = lab.Indicators(_bars_from_rows(rows[:cut]))
    a = sr.sweep_orders(full, _sweep_spec())
    b = sr.sweep_orders(part, _sweep_spec())
    np.testing.assert_array_equal(a[0][:cut], b[0])
    np.testing.assert_allclose(a[1][:cut], b[1], equal_nan=True)


def test_the_declared_grid_covers_both_readings_of_the_pictures():
    """The owner corrected my reading; both the fade and the continuation are now tested, not one."""
    specs = sr.sweep_variants("XAUUSD", "4h")
    assert len(specs) == 36
    assert len({s["variant"] for s in specs}) == 36
    modes = {s["params"]["mode"] for s in specs}
    assert modes == {"reject", "continue"}


def test_the_continuation_mode_trades_the_way_the_close_points():
    """'If the close above the previous high buy, if the close below sell' - the owner's own words."""
    rows = _flat_bars(30)
    rows.append((4000.0, 4050.0, 3998.0, 4045.0))      # closed BEYOND the prior high
    ind = lab.Indicators(_bars_from_rows(rows))
    assert sr.sweep_orders(ind, _sweep_spec(mode="continue", require_body=False))[0][-1] == 1
    assert sr.sweep_orders(ind, _sweep_spec(mode="reject", require_body=False))[0][-1] == 0


def test_the_trend_filter_only_applies_to_the_continuation():
    """A breakout needs a trend; without the filter the rule lost 50 % over the ranging years."""
    rng = np.random.default_rng(3)
    close = 4000 - np.cumsum(np.abs(rng.normal(2, 1, 400)))     # a steady downtrend
    rows = [(c, c + 12, c - 12, c - 1) for c in close]
    ind = lab.Indicators(_bars_from_rows(rows))
    unfiltered = np.count_nonzero(sr.sweep_orders(ind, _sweep_spec(mode="continue", lookback=20,
                                                                   require_body=False))[0] == 1)
    filtered = np.count_nonzero(sr.sweep_orders(ind, _sweep_spec(mode="continue", lookback=20,
                                                                 require_body=False, trend_ema=200))[0] == 1)
    assert filtered <= unfiltered, "the filter must remove longs made below the trend line"


def test_the_module_cannot_trade():
    text = open(sr.__file__.replace(".pyc", ".py"), encoding="utf-8").read()
    for forbidden in ("order_send", "OrderSend", "place_order", "auto_execute", "MetaTrader5"):
        assert forbidden not in text
