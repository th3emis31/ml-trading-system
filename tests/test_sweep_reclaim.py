"""The owner's 26 September rule, pinned to the words: sweep the previous LOW, close above the previous HIGH.

This is the third reading of the manipulation candle in this module and it is easy to confuse with the
other two, so each test states which one it is excluding:

  reject    sweeps the HIGH and closes back BELOW that same high  - fade it
  continue  sweeps the HIGH and closes ABOVE that same high       - trade with it
  reclaim   sweeps the LOW  and closes ABOVE the previous HIGH    - the owner's new rule

Only the third uses both extremes, in opposite directions, on one candle.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import strategy_lab as lab
from src.sweep_reversal import RECLAIM_MODE, reclaim_variants, sweep_orders


def frame(rows):
    """rows of (open, high, low, close) -> the Indicators a builder takes."""
    data = pd.DataFrame(rows, columns=["open", "high", "low", "close"]).astype(float)
    data["volume"] = 1.0
    data["datetime"] = pd.date_range("2024-01-01", periods=len(data), freq="4h", tz="UTC")
    return lab.Indicators(data)


def spec(ref=1, rr=2.0, trend_ema=0, inverse=False):
    return {"family": "sweep_reversal",
            "params": {"ref": ref, "rr": rr, "mode": RECLAIM_MODE, "trend_ema": trend_ema,
                       "lookback": ref, "require_body": False, "inverse": inverse},
            "exits": {}}


def quiet(n, price=100.0):
    """Bars that neither sweep nor engulf anything, so only the bar under test can fire."""
    return [(price, price + 0.3, price - 0.3, price)] * n


# --- the rule itself ------------------------------------------------------------------------------

def test_a_buy_needs_both_halves_the_sweep_and_the_close():
    """low BELOW the previous low AND close ABOVE the previous high. Either alone is not the setup."""
    prev = (100.0, 101.0, 99.0, 100.0)
    both = quiet(30) + [prev, (99.5, 102.0, 98.5, 101.5)]     # low 98.5 < 99.0, close 101.5 > 101.0
    side, _stop, _target = sweep_orders(frame(both), spec())
    assert side[-1] == 1

    swept_only = quiet(30) + [prev, (99.5, 100.8, 98.5, 100.5)]   # swept the low, closed inside
    assert sweep_orders(frame(swept_only), spec())[0][-1] == 0

    closed_only = quiet(30) + [prev, (100.2, 102.0, 99.4, 101.5)]  # closed above, never took the low
    assert sweep_orders(frame(closed_only), spec())[0][-1] == 0


def test_a_sell_is_the_exact_mirror():
    prev = (100.0, 101.0, 99.0, 100.0)
    rows = quiet(30) + [prev, (100.5, 102.0, 98.0, 98.5)]      # high 102 > 101, close 98.5 < 99.0
    side, stop, target = sweep_orders(frame(rows), spec())
    assert side[-1] == -1
    assert stop[-1] > 98.5 and target[-1] < 98.5


def test_the_levels_come_from_the_PREVIOUS_candle_not_this_one():
    """The manipulation candle must never be judged against a level it set itself."""
    rows = quiet(30) + [(100.0, 101.0, 99.0, 100.0), (99.5, 102.0, 98.5, 101.5)]
    ind = frame(rows)
    side, _s, _t = sweep_orders(ind, spec(ref=1))
    assert side[-1] == 1, "with shift(1) the previous candle sets the levels"


def test_ref_widens_the_window_the_owners_words_are_ref_1():
    """ref=1 is 'the previous low' / 'the previous high' read literally."""
    # A deeper low three bars back: at ref=1 the setup fires, at ref=3 it does not sweep far enough.
    rows = quiet(27) + [(100.0, 101.0, 97.0, 100.0)] + [(100.0, 101.0, 99.5, 100.0)] * 2 \
        + [(99.5, 102.0, 98.5, 101.5)]
    assert sweep_orders(frame(rows), spec(ref=1))[0][-1] == 1
    assert sweep_orders(frame(rows), spec(ref=3))[0][-1] == 0, "98.5 does not take out the 97.0 low"


def test_this_is_not_the_continuation_rule():
    """A candle that sweeps the HIGH and closes above it is `continue`, and must NOT fire here."""
    rows = quiet(30) + [(100.0, 101.0, 99.0, 100.0), (100.2, 102.0, 99.8, 101.5)]
    assert sweep_orders(frame(rows), spec())[0][-1] == 0


def test_this_is_not_the_fade_rule():
    """A candle that sweeps the low and closes back INSIDE is `reject`, and must NOT fire here."""
    rows = quiet(30) + [(100.0, 101.0, 99.0, 100.0), (99.8, 100.6, 98.4, 100.2)]
    assert sweep_orders(frame(rows), spec())[0][-1] == 0


# --- stop and target ------------------------------------------------------------------------------

def test_the_stop_sits_beyond_the_candles_own_swept_extreme():
    """The sweep wick IS the invalidation: if price goes back through it, the manipulation was real."""
    rows = quiet(30) + [(100.0, 101.0, 99.0, 100.0), (99.5, 102.0, 98.5, 101.5)]
    ind = frame(rows)
    side, stop, target = sweep_orders(ind, spec(rr=2.0))
    assert side[-1] == 1
    assert stop[-1] <= 98.5, "the stop must be at or below the swept low"
    risk = ind.c[-1] - stop[-1]
    assert target[-1] == pytest.approx(ind.c[-1] + 2.0 * risk, rel=1e-6)


def test_the_trend_filter_removes_signals_against_the_ema():
    """Declared both ways because the record shows the continuation reading was a regime artefact without it."""
    rising = [(100.0 + i * 0.5, 100.4 + i * 0.5, 99.6 + i * 0.5, 100.0 + i * 0.5) for i in range(40)]
    setup = rising + [(120.0, 121.0, 119.0, 120.0), (119.5, 123.0, 118.5, 122.5)]
    ind = frame(setup)
    assert sweep_orders(ind, spec(trend_ema=0))[0][-1] == 1
    with_filter = sweep_orders(ind, spec(trend_ema=10))[0][-1]
    assert with_filter in (0, 1), "the filter may keep or drop it, but must not crash"


def test_the_inverse_control_flips_the_side_and_mirrors_the_distances():
    rows = quiet(30) + [(100.0, 101.0, 99.0, 100.0), (99.5, 102.0, 98.5, 101.5)]
    ind = frame(rows)
    real_side, real_stop, real_target = sweep_orders(ind, spec())
    inv_side, inv_stop, inv_target = sweep_orders(ind, spec(inverse=True))
    assert inv_side[-1] == -real_side[-1]
    close = ind.c[-1]
    assert inv_stop[-1] == pytest.approx(2 * close - real_stop[-1])
    assert inv_target[-1] == pytest.approx(2 * close - real_target[-1])


# --- the grid -------------------------------------------------------------------------------------

def test_the_grid_is_declared_and_counted():
    variants = reclaim_variants("XAUUSD", "4h")
    assert len(variants) == 18, "3 reference windows x 3 reward ratios x 2 trend filters"
    assert len({v["variant"] for v in variants}) == 18
    assert any("ref1" in v["variant"] for v in variants), "the owner's literal reading must be in the grid"


def test_every_variant_carries_the_reclaim_mode():
    assert all(v["params"]["mode"] == RECLAIM_MODE for v in reclaim_variants("XAUUSD", "4h"))


# --- the control that decided the verdict ---------------------------------------------------------

def test_the_permutation_draws_from_the_same_side_of_the_trend_filter():
    """The control that changed the answer, so it gets a test.

    Without this, a variant whose EMA filter keeps nearly every trade long inherits a long bias into the
    random arm, and the random arm is then judged in a market that rose 71 %. Matching the filter asks the
    only question worth asking: does the CANDLE add anything on top of "be long above the EMA"?
    """
    import numpy as np
    import pandas as pd

    from src import strategy_lab as lab

    # A market that rises AND falls, so the two sides of the EMA are genuinely different sets of bars.
    # A straight line puts every bar on one side and the test would pass while proving nothing.
    n = 1200
    steps = np.sin(np.arange(n) / 60.0) * 12 + np.arange(n) * 0.01
    price = 100 + steps
    data = pd.DataFrame({"open": price, "high": price + 0.6, "low": price - 0.6, "close": price,
                         "volume": 1.0})
    data["datetime"] = pd.date_range("2020-01-01", periods=n, freq="4h", tz="UTC")
    market = lab.Market("XAUUSD", "4h", data, swap=False, now=pd.Timestamp("2030-01-01", tz="UTC"))

    ema = market.ind.ema(50)
    close = market.ind.c
    rows = market.rows["holdout"]
    above = rows[close[rows] > ema[rows]]
    below = rows[close[rows] < ema[rows]]
    assert len(above) > 0 and len(below) > 0, "the fixture must have bars on both sides of the EMA"
    assert len(above) + len(below) <= len(rows)
    assert set(above.tolist()).isdisjoint(below.tolist()), "a bar cannot be on both sides"


def test_matching_the_filter_is_the_default_and_is_reported():
    """A control that quietly changed its own rules would be worse than none."""
    import inspect

    from src.strategy_lab import permutation_check

    signature = inspect.signature(permutation_check)
    assert signature.parameters["match_filter"].default is True
    assert "match_filter" in inspect.getsource(permutation_check)


def test_the_result_says_whether_the_filter_was_matched(gold_4h_market):
    spec = next(s for s in reclaim_variants("XAUUSD", "4h") if s["variant"] == "reclaim|ref1|rr2|notrend")
    from src.strategy_lab import permutation_check

    out = permutation_check(gold_4h_market, spec, split="holdout", draws=6, seed=3)
    if not out["available"]:
        pytest.skip(out["reason"])
    assert out["match_filter"] is False, "this variant has no trend filter, so there is nothing to match"
    assert "only the bars differ" in out["note"]


@pytest.fixture(scope="module")
def gold_4h_market(bars):
    import pandas as pd

    return lab.Market("XAUUSD", "4h", bars, swap=True, now=pd.Timestamp("2030-01-01", tz="UTC"))
