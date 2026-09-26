"""The volume profile must find where business was actually done, and must never see the future.

POC is the foundation of all five combinations the owner's architecture card asks to be tested, so an
error here would quietly move every result downstream. The causality test is the important one: a profile
built over a whole history and then read at an early bar is the classic way this indicator lies.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.volume_profile import (DEFAULT_BINS, poc_migration, profile_summary, profile_window,
                                rolling_levels)


def bars_at(prices, volumes, spread=0.5):
    """Bars centred on each price, so the volume lands near that price."""
    highs = [p + spread for p in prices]
    lows = [p - spread for p in prices]
    return highs, lows, list(volumes)


# --- the point of control -------------------------------------------------------------------------

def test_the_poc_is_the_price_with_the_most_volume_behind_it():
    prices = [100.0] * 5 + [110.0] * 40 + [120.0] * 5
    highs, lows, volumes = bars_at(prices, [1.0] * len(prices))
    found = profile_window(highs, lows, volumes, bins=40)
    assert found is not None
    assert found.poc == pytest.approx(110.0, abs=1.0), f"POC {found.poc} should sit at the busy price"


def test_volume_not_bar_count_decides_the_poc():
    """The whole point of a volume profile: one enormous bar outweighs many small ones."""
    prices = [100.0] * 30 + [130.0]
    volumes = [1.0] * 30 + [5000.0]
    highs, lows, vols = bars_at(prices, volumes)
    found = profile_window(highs, lows, vols, bins=40)
    assert found.poc == pytest.approx(130.0, abs=1.5)


def test_a_bars_volume_is_spread_across_the_prices_it_traded_at():
    """A wide bar has no idea where inside itself the trading happened, so it contributes everywhere:
    the histogram is uniform, so the value area is simply the middle 70 % of the bar's own range."""
    wide = profile_window([120.0], [100.0], [1000.0], bins=20)
    assert wide is not None
    assert wide.val >= 100.0 and wide.vah <= 120.0
    covered = (wide.vah - wide.val) / (wide.high - wide.low)
    assert covered == pytest.approx(0.70, abs=0.08), f"value area covered {covered:.0%} of the bar"


def test_a_tied_histogram_does_not_bias_the_poc_downward():
    """np.argmax returns the lowest tied bin, and a flat histogram is what a quiet range produces - so
    without a tie-break the POC drifts systematically to the bottom of every quiet window."""
    wide = profile_window([120.0], [100.0], [1000.0], bins=20)
    middle = (wide.high + wide.low) / 2
    assert abs(wide.poc - middle) < 3.0, f"POC {wide.poc} should sit near the middle {middle}, not the low"


# --- the value area -------------------------------------------------------------------------------

def test_the_value_area_brackets_the_poc_and_holds_the_asked_for_share():
    prices = list(np.linspace(90, 110, 200))
    highs, lows, volumes = bars_at(prices, [1.0] * 200)
    found = profile_window(highs, lows, volumes, bins=50, value_area=0.70)
    assert found.val <= found.poc <= found.vah
    assert found.val > found.low - 1 and found.vah < found.high + 1


def test_a_wider_value_area_is_never_narrower():
    prices = list(np.linspace(90, 110, 200))
    highs, lows, volumes = bars_at(prices, [1.0] * 200)
    narrow = profile_window(highs, lows, volumes, bins=50, value_area=0.50)
    wide = profile_window(highs, lows, volumes, bins=50, value_area=0.90)
    assert (wide.vah - wide.val) >= (narrow.vah - narrow.val)


def test_the_value_area_is_allowed_to_be_lopsided():
    """Value is usually one-sided; forcing symmetry would put VAH and VAL at prices nothing traded at."""
    prices = [100.0] * 60 + [101.0] * 40 + [130.0] * 2
    highs, lows, volumes = bars_at(prices, [1.0] * len(prices))
    found = profile_window(highs, lows, volumes, bins=60)
    above = found.vah - found.poc
    below = found.poc - found.val
    assert above != pytest.approx(below, abs=1e-9)


# --- causality ------------------------------------------------------------------------------------

def test_the_level_at_each_bar_uses_only_that_bar_and_the_ones_before():
    n = 300
    prices = list(np.linspace(100, 100, 200)) + list(np.linspace(100, 200, 100))
    highs, lows, volumes = bars_at(prices, [1.0] * n)
    poc, _vah, _val = rolling_levels(highs, lows, volumes, lookback=50, bins=40)
    # At bar 150 the market has only ever traded near 100, so a later run to 200 must not show up.
    assert np.isfinite(poc[150])
    assert poc[150] == pytest.approx(100.0, abs=2.0), f"bar 150 POC {poc[150]} saw the future"


def test_truncating_the_series_does_not_change_earlier_levels():
    n = 400
    rng = np.random.default_rng(11)
    prices = list(100 + np.cumsum(rng.normal(0, 1, n)))
    highs, lows, volumes = bars_at(prices, rng.uniform(1, 10, n))
    full, _a, _b = rolling_levels(highs, lows, volumes, lookback=60, bins=40)
    cut = 250
    part, _c, _d = rolling_levels(highs[:cut], lows[:cut], volumes[:cut], lookback=60, bins=40)
    np.testing.assert_allclose(full[:cut], part, rtol=0, atol=1e-9)


def test_bars_before_the_first_full_window_have_no_level():
    """A profile of twelve candles and one of a hundred and twenty are not the same measurement."""
    highs, lows, volumes = bars_at(list(np.linspace(100, 110, 100)), [1.0] * 100)
    poc, vah, val = rolling_levels(highs, lows, volumes, lookback=30, bins=20)
    assert np.all(np.isnan(poc[:29])) and np.isfinite(poc[29])
    assert np.all(np.isnan(vah[:29])) and np.all(np.isnan(val[:29]))


# --- POC migration --------------------------------------------------------------------------------

def test_poc_migration_reads_rising_and_falling():
    rising = np.linspace(100, 120, 50)
    assert poc_migration(rising, span=5)[-1] == 1
    assert poc_migration(rising[::-1], span=5)[-1] == -1
    assert poc_migration(np.full(50, 100.0), span=5)[-1] == 0


def test_poc_migration_is_zero_where_the_level_is_unknown():
    values = np.array([np.nan] * 10 + [100.0] * 10)
    assert poc_migration(values, span=5)[5] == 0


# --- refusing rather than inventing -----------------------------------------------------------------

def test_no_volume_means_no_profile_rather_than_a_guess():
    highs, lows, _v = bars_at([100.0] * 20, [0.0] * 20)
    assert profile_window(highs, lows, [0.0] * 20) is None
    assert profile_summary(highs, lows, [0.0] * 20)["available"] is False


def test_no_bars_is_handled():
    assert profile_window([], [], []) is None


def test_a_flat_market_with_no_range_is_refused():
    """Every bar at one price gives no histogram to speak of; better none than a fake level."""
    assert profile_window([100.0] * 10, [100.0] * 10, [1.0] * 10) is None


def test_the_summary_places_no_orders():
    highs, lows, volumes = bars_at(list(np.linspace(100, 110, 50)), [1.0] * 50)
    assert profile_summary(highs, lows, volumes)["places_orders"] is False
