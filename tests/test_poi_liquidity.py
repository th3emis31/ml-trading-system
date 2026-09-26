"""The Trader's Grit model: sweep the liquidity, return to the point of interest.

The chart shows a shape, and a shape is only tradeable if it can be recognised WITHOUT seeing what came
after. Structure here comes from `market_structure`, where a swing is not known until `prd` bars after it
formed, and these tests exist mostly to prove that lag survives into this module - a setup built on a swing
that was not yet confirmed is a setup nobody could have taken.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.market_structure import structure_swings
from src.poi_liquidity import DEFAULT_MAX_WAIT, bars_from, poi_arrays, poi_setups, poi_summary


def leg(start, end, count):
    return [start + (end - start) * k / count for k in range(1, count + 1)]


def path(*legs, spread=0.4):
    """A price path from straight legs, one bar per step - explicit rather than generated, because a
    generated staircase once made a fixture that could never break a swing low and hid half a module."""
    values = [legs[0][0]]
    for start, end, count in legs:
        values += leg(start, end, count)
    return bars_from([v for v in values], [v + spread for v in values],
                     [v - spread for v in values], [v for v in values])


def _down_then_return():
    """Up, down (breaking structure), a lower low that sweeps, then a rally back to the old high."""
    return path((100, 120, 12), (120, 104, 12), (104, 112, 12), (112, 99, 12), (99, 121, 16))


# --- the setup ------------------------------------------------------------------------------------

def test_a_setup_needs_a_sweep_and_a_return_to_the_point_of_interest():
    setups = poi_setups(_down_then_return(), prd=5, max_wait=40, require_bos=False)
    assert setups, "this path sweeps a low and then rallies back over the prior high"
    first = setups[0]
    assert first.swept_extreme < first.swept_level or first.swept_extreme > first.swept_level
    assert first.poi_index > first.sweep_index, "the POI is reached AFTER the sweep"


def test_the_poi_is_on_the_other_side_of_the_sweep():
    for setup in poi_setups(_down_then_return(), prd=5, max_wait=40, require_bos=False):
        if setup.direction == 1:
            assert setup.poi_level > setup.swept_extreme, "lows swept, so the POI is above"
        else:
            assert setup.poi_level < setup.swept_extreme, "highs swept, so the POI is below"


def test_the_window_expires():
    """A POI touched fifty bars later claims a setup nobody was watching any more."""
    bars = _down_then_return()
    patient = poi_setups(bars, prd=5, max_wait=40, require_bos=False)
    hasty = poi_setups(bars, prd=5, max_wait=1, require_bos=False)
    assert len(hasty) <= len(patient)


def test_requiring_a_break_of_structure_only_removes_setups():
    bars = _down_then_return()
    loose = poi_setups(bars, prd=5, max_wait=40, require_bos=False)
    strict = poi_setups(bars, prd=5, max_wait=40, require_bos=True)
    assert len(strict) <= len(loose)
    assert all(s.had_bos for s in strict)


def test_a_level_is_only_swept_once():
    """Otherwise a long run below an old low produces a setup on every bar."""
    setups = poi_setups(_down_then_return(), prd=5, max_wait=40, require_bos=False)
    seen = [(s.swept_level, s.direction) for s in setups]
    assert len(seen) == len(set(seen))


# --- causality, which is the whole point ----------------------------------------------------------

def test_no_setup_uses_a_swing_that_was_not_yet_confirmed():
    bars = _down_then_return()
    swings = {(s.kind, s.price): s for s in structure_swings(bars, 5)}
    for setup in poi_setups(bars, prd=5, max_wait=40, require_bos=False):
        kind = "low" if setup.direction == 1 else "high"
        swing = swings.get((kind, setup.swept_level))
        if swing is not None:
            assert swing.confirmed_at <= setup.sweep_index, (
                f"the sweep at {setup.sweep_index} used a swing confirmed at {swing.confirmed_at}")


def test_truncating_after_the_signal_does_not_change_it():
    """The property the backtest rests on: the setup must be knowable at its own signal bar."""
    bars = _down_then_return()
    full = poi_setups(bars, prd=5, max_wait=40, require_bos=False)
    assert full, "fixture must produce a setup"
    cut = full[0].poi_index + 1
    truncated = poi_setups(bars[:cut], prd=5, max_wait=40, require_bos=False)
    assert truncated, "the first setup must survive truncation at its own signal bar"
    assert truncated[0].sweep_index == full[0].sweep_index
    assert truncated[0].poi_level == full[0].poi_level


# --- the two readings -----------------------------------------------------------------------------

def test_fade_and_ride_signal_on_different_bars_and_opposite_sides():
    bars = _down_then_return()
    fade_side, fade_anchor, _ = poi_arrays(bars, mode="fade", prd=5, max_wait=40, require_bos=False)
    ride_side, ride_anchor, _ = poi_arrays(bars, mode="ride", prd=5, max_wait=40, require_bos=False)
    setups = poi_setups(bars, prd=5, max_wait=40, require_bos=False)
    assert setups
    setup = setups[0]
    assert ride_side[setup.sweep_index] == setup.direction, "ride enters at the sweep, with the reversal"
    assert fade_side[setup.poi_index] == -setup.direction, "fade enters at the POI, against the arrival"
    assert ride_anchor[setup.sweep_index] == setup.swept_extreme
    assert fade_anchor[setup.poi_index] == setup.poi_level


def test_nothing_is_marked_on_bars_with_no_setup():
    bars = _down_then_return()
    side, _anchor, _obj = poi_arrays(bars, mode="fade", prd=5, max_wait=40, require_bos=False)
    assert np.count_nonzero(side) == len(poi_setups(bars, prd=5, max_wait=40, require_bos=False))


# --- the summary ----------------------------------------------------------------------------------

def test_the_summary_counts_and_places_no_orders():
    out = poi_summary(_down_then_return(), prd=5, max_wait=40, require_bos=False)
    assert out["setups"] >= 1 and out["places_orders"] is False
    assert out["lows_swept"] + out["highs_swept"] == out["setups"]


def test_a_flat_market_produces_nothing():
    flat = bars_from([100] * 200, [100.2] * 200, [99.8] * 200, [100] * 200)
    assert poi_setups(flat, prd=5, max_wait=DEFAULT_MAX_WAIT, require_bos=False) == []


def test_too_few_bars_is_handled():
    assert poi_setups(bars_from([1, 2], [2, 3], [0, 1], [1, 2]), prd=5) == []


# --- the lookahead that the first run of this module actually had ---------------------------------

@pytest.mark.parametrize("mode", ["fade", "ride"])
def test_every_signal_survives_truncation_at_its_own_bar(mode):
    """THE test this module needed and did not have.

    The first version placed the ride entry at the sweep bar but only recorded the setup once price had
    RETURNED to the POI - so the entry depended on bars after it. Truncating at the signal bar made the
    signal vanish. It produced profit factors of 2.5-2.7 at 1.6 % drawdown, better than anything else in
    this project's record, which is what a lookahead looks like from the outside.

    The earlier truncation test cut at the POI bar, which is AFTER the ride entry, so it passed while the
    defect was live. Cutting at each signal's OWN bar is the check that has teeth.
    """
    bars = _down_then_return()
    side, _anchor, _obj = poi_arrays(bars, mode=mode, prd=5, max_wait=40, require_bos=False)
    fired = [int(i) for i in np.flatnonzero(side)]
    assert fired, f"the {mode} fixture must produce at least one signal"
    for index in fired:
        cut = index + 1
        truncated, _a, _o = poi_arrays(bars[:cut], mode=mode, prd=5, max_wait=40, require_bos=False)
        assert truncated[index] == side[index], (
            f"{mode} signal at bar {index} changed from {side[index]} to {truncated[index]} when the "
            "bars after it were removed, so it was reading the future")


def test_the_ride_entry_does_not_require_the_poi_to_be_reached():
    """The POI is the ride's TARGET. Demanding it before entry is the lookahead above."""
    from src.poi_liquidity import sweep_signals

    # A path that sweeps a low and then keeps falling, so the POI above is never reached.
    bars = path((100, 120, 12), (120, 104, 12), (104, 112, 12), (112, 90, 20), (90, 88, 10))
    sweeps = sweep_signals(bars, prd=5, require_bos=False)
    assert sweeps, "the sweep happened, so the ride reading must have an entry"
    side, _a, _o = poi_arrays(bars, mode="ride", prd=5, max_wait=40, require_bos=False)
    assert np.count_nonzero(side) == len(sweeps)
    # And the fade reading correctly has nothing here: price never came back to the POI.
    fade_side, _a2, _o2 = poi_arrays(bars, mode="fade", prd=5, max_wait=40, require_bos=False)
    assert np.count_nonzero(fade_side) <= np.count_nonzero(side)


def test_a_sweep_is_counted_once_even_if_price_keeps_going():
    from src.poi_liquidity import sweep_signals

    bars = path((100, 120, 12), (120, 104, 12), (104, 112, 12), (112, 90, 20))
    sweeps = sweep_signals(bars, prd=5, require_bos=False)
    seen = [(s["swept_level"], s["direction"]) for s in sweeps]
    assert len(seen) == len(set(seen))
