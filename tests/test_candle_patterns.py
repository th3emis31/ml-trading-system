"""Candlestick detectors: each one fires on a hand-built textbook example and not otherwise.

The measurement is only worth reading if the detectors are right, so these build the candles by
hand rather than trusting market data to contain a clean case.
"""
import numpy as np
import pandas as pd
import pytest

from src import candle_patterns as cp


def _frame(rows, atr=10.0):
    """rows = (open, high, low, close). A flat ATR keeps the size thresholds predictable."""
    frame = pd.DataFrame(rows, columns=["open", "high", "low", "close"])
    frame["datetime"] = pd.date_range("2026-01-01", periods=len(frame), freq="1h", tz="UTC")
    frame["volume"] = 1.0
    frame["atr_14"] = atr
    return frame


def _pattern_of(rows, name, atr=10.0):
    return cp.pattern_frame(_frame(rows, atr))[name].to_numpy()


# --- one-candle -----------------------------------------------------------------

def test_marubozu_is_signed_by_direction():
    bull = _pattern_of([(100, 110, 99.5, 109.5)], "marubozu")
    bear = _pattern_of([(109.5, 110, 99.5, 100)], "marubozu")
    assert bull[0] == 1
    assert bear[0] == -1


def test_a_small_body_is_not_a_marubozu():
    assert _pattern_of([(100, 110, 100, 102)], "marubozu")[0] == 0


def test_a_marubozu_must_also_be_big_enough_to_matter():
    """body/range can be perfect on a bar that is tiny against ATR; that is noise, not a marubozu."""
    assert _pattern_of([(100, 100.9, 100, 100.85)], "marubozu", atr=10.0)[0] == 0


def test_doji_is_a_tiny_body_and_carries_no_direction():
    out = _pattern_of([(100, 105, 95, 100.2)], "doji")
    assert out[0] == 1                       # presence flag
    assert cp.DIRECTIONLESS == ("doji",)     # measured on both sides, never signed


def test_hammer_needs_a_long_lower_wick_and_a_short_upper_one():
    assert _pattern_of([(105, 106, 95, 105.5)], "hammer")[0] == 1
    assert _pattern_of([(105, 115, 104.5, 105.5)], "hammer")[0] == 0   # wick on the wrong side


def test_shooting_star_is_the_hammer_mirrored_and_signed_short():
    assert _pattern_of([(105, 115, 104.5, 104.8)], "shooting_star")[0] == -1
    assert _pattern_of([(105, 106, 95, 105.5)], "shooting_star")[0] == 0


def test_pin_bar_is_signed_by_which_side_the_wick_is_on():
    assert _pattern_of([(108, 109, 95, 108.5)], "pin_bar")[0] == 1     # long lower wick
    assert _pattern_of([(101, 115, 100, 101.5)], "pin_bar")[0] == -1    # long upper wick


# --- two-candle -----------------------------------------------------------------

def test_bullish_engulfing_needs_the_previous_bar_down_and_a_bigger_body():
    rows = [(110, 111, 104, 105), (104, 112, 103, 111)]
    assert _pattern_of(rows, "bullish_engulfing")[1] == 1


def test_bullish_engulfing_does_not_fire_after_an_up_bar():
    rows = [(105, 112, 104, 111), (104, 112, 103, 111)]
    assert _pattern_of(rows, "bullish_engulfing")[1] == 0


def test_bearish_engulfing_is_the_mirror():
    rows = [(105, 111, 104, 110), (111, 112, 103, 104)]
    assert _pattern_of(rows, "bearish_engulfing")[1] == -1


def test_piercing_line_closes_inside_the_previous_body_not_beyond_it():
    """Above the midpoint but below the previous open — beyond the open would be engulfing."""
    rows = [(110, 111, 100, 100.5), (99, 107, 98.5, 106)]
    assert _pattern_of(rows, "piercing_line")[1] == 1
    beyond = [(110, 111, 100, 100.5), (99, 112, 98.5, 111)]
    assert _pattern_of(beyond, "piercing_line")[1] == 0


def test_dark_cloud_cover_is_the_mirror():
    rows = [(100, 110.5, 99.5, 110), (111, 111.5, 103, 104)]
    assert _pattern_of(rows, "dark_cloud_cover")[1] == -1


# --- three-candle ---------------------------------------------------------------

def test_morning_star_needs_a_big_down_bar_a_small_bar_and_a_recovery():
    rows = [(120, 120.5, 100, 100.5), (100, 101, 99, 100.2), (100.5, 115, 100, 114)]
    assert _pattern_of(rows, "morning_star")[2] == 1


def test_morning_star_refuses_a_large_middle_bar():
    rows = [(120, 120.5, 100, 100.5), (100, 118, 99, 117), (117, 125, 116, 124)]
    assert _pattern_of(rows, "morning_star")[2] == 0


def test_evening_star_is_the_mirror():
    rows = [(100, 120, 99.5, 119.5), (119.5, 120.5, 118.5, 119.7), (119, 120, 104, 105)]
    assert _pattern_of(rows, "evening_star")[2] == -1


def test_three_white_soldiers_need_three_rising_closes_with_full_bodies():
    rows = [(100, 105.2, 99.8, 105), (105, 110.2, 104.8, 110), (110, 115.2, 109.8, 115)]
    assert _pattern_of(rows, "three_white_soldiers")[2] == 1


def test_three_white_soldiers_refuse_a_wicky_bar():
    rows = [(100, 105.2, 99.8, 105), (105, 120, 104.8, 110), (110, 115.2, 109.8, 115)]
    assert _pattern_of(rows, "three_white_soldiers")[2] == 0


def test_three_black_crows_is_the_mirror():
    rows = [(115, 115.2, 109.8, 110), (110, 110.2, 104.8, 105), (105, 105.2, 99.8, 100)]
    assert _pattern_of(rows, "three_black_crows")[2] == -1


# --- the properties that make the measurement trustworthy ------------------------

def test_every_pattern_is_signed_or_explicitly_directionless():
    """The flaw in features.detect_reversal is that it gives bullish and bearish the same value."""
    rng = np.random.default_rng(11)
    close = 4000 + np.cumsum(rng.normal(0, 8, 400))
    rows = [(c - 1, c + 4, c - 4, c + 1) for c in close]
    frame = cp.pattern_frame(_frame(rows))
    for name in cp.PATTERNS:
        values = set(frame[name].unique()) - {0}
        if name in cp.DIRECTIONLESS:
            assert values <= {1}, f"{name} should be a presence flag"
        else:
            assert values <= {1} or values <= {-1} or values <= {1, -1}, name


def test_a_detector_only_looks_at_the_bar_and_its_predecessors():
    """Changing a later bar must not change an earlier bar's pattern value."""
    rng = np.random.default_rng(5)
    close = 4000 + np.cumsum(rng.normal(0, 8, 300))
    rows = [(c - 1, c + 5, c - 5, c + 1) for c in close]
    first = cp.pattern_frame(_frame(rows))
    tampered = list(rows)
    tampered[250:] = [(c + 500, c + 520, c + 480, c + 510) for c, *_ in [(r[0],) for r in rows[250:]]]
    second = cp.pattern_frame(_frame(tampered))
    for name in cp.PATTERNS:
        assert first[name].to_numpy()[:249].tolist() == second[name].to_numpy()[:249].tolist(), name


def test_the_wilson_interval_is_wide_at_small_n_and_narrow_at_large_n():
    narrow = cp._wilson(5000, 10000)
    wide = cp._wilson(15, 30)
    assert (wide[1] - wide[0]) > (narrow[1] - narrow[0]) * 5


def test_thresholds_are_module_constants_so_they_cannot_be_tuned_per_run():
    """strategies/candle_patterns.md fixes these; a per-call override would let a result pick them."""
    import inspect
    for detector in cp.PATTERNS.values():
        params = inspect.signature(detector).parameters
        assert list(params) == ["g"], f"{detector.__name__} must take only the geometry dict"


def test_the_module_cannot_trade():
    text = open(cp.__file__.replace(".pyc", ".py"), encoding="utf-8").read()
    for forbidden in ("order_send", "OrderSend", "place_order", "auto_execute", "MetaTrader5", "demo_executor"):
        assert forbidden not in text, f"{forbidden} must not appear in a measurement module"
