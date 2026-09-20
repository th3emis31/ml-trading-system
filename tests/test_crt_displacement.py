"""The 4H CRT as specified: sweep, displacement, body close - and the wick case it says to reject."""
import numpy as np
import pandas as pd
import pytest

from src import crt_displacement as cd, strategy_lab as lab


def _bars(rows, freq="4h"):
    return pd.DataFrame({"datetime": pd.date_range("2026-01-01", periods=len(rows), freq=freq, tz="UTC"),
                         "open": [r[0] for r in rows], "high": [r[1] for r in rows],
                         "low": [r[2] for r in rows], "close": [r[3] for r in rows],
                         "volume": [1000.0] * len(rows)})


def _spec(**kw):
    params = {"symbol": "XAUUSD", "timeframe": "4h", "mode": "body", "require_displacement": True,
              "require_sweep": True, "rr": 2.0, "swing_lookback": 3, "sweep_window": 20,
              "displacement_factor": 1.5}
    params.update(kw)
    return {"family": "crt_displacement", "params": params,
            "exits": {"stop": "fixed", "sl_atr": 0.0, "rr": 0.0, "trail_atr": 0.0,
                      "max_bars": 30, "swing_lookback": 0}}


def test_a_swing_is_only_known_once_the_bars_after_it_have_closed():
    """A fractal pivot needs bars on BOTH sides. Publishing it earlier would be look-ahead."""
    values = np.array([10, 11, 12, 13, 20, 13, 12, 11, 10, 11, 12], dtype=float)
    levels = cd._confirmed_swings(values, 3, high=True)
    peak = 4                                     # the 20
    assert not np.isfinite(levels[peak]), "the pivot cannot be known on the bar it happens"
    assert not np.isfinite(levels[peak + 2]), "nor before its three right-hand bars have closed"
    assert levels[peak + 3] == 20.0, "known only once confirmed"


def test_no_look_ahead_in_the_whole_signal():
    rng = np.random.default_rng(11)
    close = 4000 + np.cumsum(rng.normal(0, 12, 500))
    rows = [(c, c + 18, c - 18, c + 2) for c in close]
    full = lab.Indicators(_bars(rows))
    cut = 380
    part = lab.Indicators(_bars(rows[:cut]))
    a = cd.displacement_orders(full, _spec())
    b = cd.displacement_orders(part, _spec())
    np.testing.assert_array_equal(a[0][:cut], b[0])
    np.testing.assert_allclose(a[1][:cut], b[1], equal_nan=True)


def test_displacement_measures_the_body_against_the_previous_three():
    """The specification's figure: at least 1.5x. A quiet candle that breaks must be refused."""
    flat = [(4000.0, 4002.0, 3998.0, 4000.0)] * 8
    peak = [(4000.0, 4050.0, 3998.0, 4001.0)]            # sets a swing high at 4050
    settle = [(4001.0, 4003.0, 3999.0, 4001.0)] * 4      # confirms it, and sets the small base body
    rows = flat + peak + settle
    ind_small = lab.Indicators(_bars(rows + [(4001.0, 4060.0, 4000.0, 4055.0)]))
    # body 54 against a base of about 2 -> hugely displaced, so this one is allowed
    assert cd.displacement_orders(ind_small, _spec(require_sweep=False))[0][-1] == 1
    # now make the previous three candles large, so the same break is NOT displacement
    big = [(4001.0, 4060.0, 3940.0, 4051.0), (4051.0, 4110.0, 3990.0, 4001.0), (4001.0, 4060.0, 3940.0, 4051.0)]
    ind_big = lab.Indicators(_bars(flat + peak + settle[:1] + big + [(4051.0, 4120.0, 4050.0, 4056.0)]))
    assert cd.displacement_orders(ind_big, _spec(require_sweep=False))[0][-1] == 0, \
        "a break whose body is small relative to recent candles is not displacement"


def test_a_wick_only_cross_is_not_a_body_break_and_is_the_rejected_case():
    flat = [(4000.0, 4002.0, 3998.0, 4000.0)] * 8
    peak = [(4000.0, 4050.0, 3998.0, 4001.0)]
    settle = [(4001.0, 4003.0, 3999.0, 4001.0)] * 4
    wick = [(4001.0, 4060.0, 4000.0, 4010.0)]            # high clears 4050, close does not
    ind = lab.Indicators(_bars(flat + peak + settle + wick))
    assert cd.displacement_orders(ind, _spec(mode="body", require_sweep=False))[0][-1] == 0, \
        "a wick through the level is not a body close"
    assert cd.displacement_orders(ind, _spec(mode="wick", require_sweep=False))[0][-1] == 1, \
        "the wick mode exists so the rejected case can be measured rather than assumed"


def test_the_declared_grid_separates_every_condition():
    """Each of the three conditions must be switchable, or the result cannot say which one works."""
    specs = cd.displacement_variants("XAUUSD", "4h")
    assert len(specs) == 16 and len({s["variant"] for s in specs}) == 16
    assert {s["params"]["mode"] for s in specs} == {"body", "wick"}
    assert {s["params"]["require_displacement"] for s in specs} == {True, False}
    assert {s["params"]["require_sweep"] for s in specs} == {True, False}
    assert {s["params"]["rr"] for s in specs} == {2.0, 3.0}


def test_the_stop_sits_beyond_the_displacement_candles_own_extreme():
    flat = [(4000.0, 4002.0, 3998.0, 4000.0)] * 8
    peak = [(4000.0, 4050.0, 3998.0, 4001.0)]
    settle = [(4001.0, 4003.0, 3999.0, 4001.0)] * 4
    rows = flat + peak + settle + [(4001.0, 4060.0, 3995.0, 4055.0)]
    ind = lab.Indicators(_bars(rows))
    side, stop, target = cd.displacement_orders(ind, _spec(require_sweep=False))
    assert side[-1] == 1
    assert stop[-1] < 3995.0, "risk is the origin of the move, below the candle's own low"
    assert target[-1] > 4055.0


def test_the_module_cannot_trade():
    text = open(cd.__file__.replace(".pyc", ".py"), encoding="utf-8").read()
    for forbidden in ("order_send", "OrderSend", "place_order", "auto_execute", "MetaTrader5"):
        assert forbidden not in text
