"""Structure labels must never use a swing before it was confirmed, or they backtest a future they saw.

A pivot at bar i needs `prd` bars either side, so it is only knowable at bar i+prd. A BOS detected at bar
j that references a swing confirmed at j+3 is a label that could not have existed live, and any split
built on it is fiction. That is the property most of these tests exist for.
"""
from __future__ import annotations

from src.market_structure import (DEFAULT_PRD, structure_at, structure_events, structure_summary,
                                  structure_swings)
from src.volatility_trend_breakout import Candle


def _candles_from(pairs):
    """(high, low) pairs -> candles whose close sits mid-range unless given as a triple."""
    out = []
    for i, item in enumerate(pairs):
        if len(item) == 3:
            high, low, close = item
        else:
            high, low = item
            close = (high + low) / 2
        out.append(Candle(ts=f"2026-01-{i + 1:02d} 00:00", open=close, high=high, low=low,
                          close=close, volume=1.0))
    return out


def _leg(start, end, bars):
    return [start + (end - start) * k / bars for k in range(1, bars + 1)]


def _path(*levels_legs):
    """An explicit price path from straight legs, one bar per step.

    Built explicitly rather than generated. The first attempt used a generator that rose and then fell
    only 80 % of the way back, which made a net-rising staircase that could never break a prior swing LOW -
    so the bearish half of this module went untested while the test looked like it passed.
    """
    levels = [levels_legs[0][0]]
    for start, end, bars in levels_legs:
        levels += _leg(start, end, bars)
    return _candles_from([(v + 0.2, v - 0.2, v) for v in levels])


def _rising():
    """Up, pullback, up again: makes a swing high, a swing low, then a bullish BOS through the high."""
    return _path((100, 120, 11), (120, 110, 11), (110, 130, 11))


def _up_then_down():
    """The textbook sequence: a bullish BOS, then the first break the other way, which is a CHOCH."""
    return _path((100, 120, 11), (120, 110, 11), (110, 130, 11), (130, 102, 14))


def _two_up_legs():
    """Two rising cycles, so there are two highs and two lows to label HH and HL against."""
    return _path((100, 120, 11), (120, 110, 11), (110, 135, 11), (135, 118, 11), (118, 150, 11))


# --- the lookahead guard ------------------------------------------------------------------------

def test_no_event_references_a_swing_that_was_not_yet_confirmed():
    candles = _up_then_down()
    swings = {s.index: s for s in structure_swings(candles)}
    for event in structure_events(candles):
        swing = swings[event.swing_index]
        assert swing.confirmed_at <= event.index, (
            f"event at bar {event.index} used a swing only confirmed at {swing.confirmed_at}")


def test_a_swing_carries_the_bar_it_could_first_be_known_on():
    candles = _rising()
    for swing in structure_swings(candles, DEFAULT_PRD):
        assert swing.confirmed_at == swing.index + DEFAULT_PRD


# --- the labels ---------------------------------------------------------------------------------

def test_highs_are_labelled_against_the_previous_high_not_the_previous_swing():
    """Comparing a high to a low would be meaningless, so each kind is labelled against its own kind."""
    candles = _two_up_legs()
    highs = [s for s in structure_swings(candles) if s.kind == "high" and s.label]
    lows = [s for s in structure_swings(candles) if s.kind == "low" and s.label]
    assert all(s.label in ("HH", "LH") for s in highs)
    assert all(s.label in ("HL", "LL") for s in lows)


def test_a_rising_series_makes_higher_highs_and_higher_lows():
    candles = _two_up_legs()
    labels = [s.label for s in structure_swings(candles) if s.label]
    assert "HH" in labels and "HL" in labels


# --- BOS and CHOCH ------------------------------------------------------------------------------

def test_the_trend_starts_unknown_and_is_not_guessed():
    candles = _rising()
    rows = structure_at(candles)
    assert rows[0]["trend"] == "unknown", "structure must not be assumed before a break confirms it"


def test_taking_out_a_swing_high_in_an_uptrend_is_a_bos_not_a_choch():
    candles = _rising()
    events = structure_events(candles)
    bullish = [e for e in events if e.direction == "bullish"]
    assert bullish, "a rising series must produce bullish breaks"
    assert bullish[-1].kind == "BOS"


def test_the_first_break_against_an_established_trend_is_a_choch():
    """The whole point of the distinction: continuation is a BOS, the first turn against it is a CHOCH."""
    events = structure_events(_up_then_down())
    assert [(e.kind, e.direction) for e in events] == [("BOS", "bullish"), ("CHOCH", "bearish")]
    assert events[0].trend_before == "unknown" and events[0].trend_after == "bullish"
    assert events[1].trend_before == "bullish" and events[1].trend_after == "bearish"
    assert events[1].level < events[0].level, "the CHOCH broke a swing LOW, below the high the BOS broke"


def test_a_level_is_only_broken_once():
    """Otherwise a long run above an old swing high fires an event on every single bar."""
    candles = _rising()
    events = structure_events(candles)
    broken = [e.swing_index for e in events]
    assert len(broken) == len(set(broken)), f"a swing was reported broken twice: {broken}"


def test_breaks_are_measured_on_the_close_unless_the_wick_is_asked_for():
    """A wick through a level and back is common on gold; counting it would multiply the event count."""
    candles = _rising()
    on_close = structure_events(candles, wick_break=False)
    on_wick = structure_events(candles, wick_break=True)
    assert len(on_wick) >= len(on_close), "wick breaks can only be as many or more"


# --- the per-bar view ---------------------------------------------------------------------------

def test_every_bar_gets_a_row_so_a_trade_can_ask_what_structure_said_at_entry():
    candles = _rising()
    rows = structure_at(candles)
    assert len(rows) == len(candles)
    assert [r["index"] for r in rows] == list(range(len(candles)))


def test_bars_since_event_counts_up_from_the_event_and_is_none_before_the_first():
    candles = _rising()
    rows = structure_at(candles)
    assert rows[0]["bars_since_event"] is None, "nothing has happened yet on the first bar"
    with_event = [r for r in rows if r["event"]]
    assert with_event, "this fixture must produce at least one event"
    first = with_event[0]["index"]
    assert rows[first]["bars_since_event"] == 0
    if first + 1 < len(rows):
        assert rows[first + 1]["bars_since_event"] == 1


def test_the_summary_counts_what_it_found_and_places_no_orders():
    out = structure_summary(_rising())
    assert out["places_orders"] is False
    assert out["swings"] > 0 and out["events"] > 0
    assert out["break_on"] == "close"
    assert sum(out["event_counts"].values()) == out["events"]


def test_a_flat_series_produces_no_structure_rather_than_noise():
    flat = _candles_from([(100.1, 99.9)] * 60)
    assert structure_events(flat) == []
    assert all(r["trend"] == "unknown" for r in structure_at(flat))
