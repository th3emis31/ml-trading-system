"""CISD must match the owner's card, and must not be able to see the future.

The card gives the idea; four things had to be pinned down to measure it (where the line sits, what
"swept" means, which closure counts, how long the level lives). Each of those choices gets a test, because
a detector that quietly means something else than the picture would make every number downstream answer a
question nobody asked.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.cisd import CISDEvent, cisd_arrays, cisd_events, cisd_summary


def ohlc(rows):
    """rows of (open, high, low, close) -> the four arrays the detector takes.

    Not named `bars`: that is a module-scoped fixture in conftest.py holding 3,000 synthetic gold
    candles, and shadowing it here would quietly break any test in this file that asked for it.
    """
    o = [r[0] for r in rows]
    h = [r[1] for r in rows]
    l = [r[2] for r in rows]
    c = [r[3] for r in rows]
    return o, h, l, c


def flat(n, price=100.0, span=0.5):
    """n quiet candles that close where they open, so they start no run and sweep nothing."""
    return [(price, price + span, price - span, price)] * n


def down(open_, close, low=None, high=None):
    return (open_, high if high is not None else open_ + 0.2, low if low is not None else close - 0.2, close)


def up(open_, close, low=None, high=None):
    return (open_, high if high is not None else close + 0.2, low if low is not None else open_ - 0.2, close)


# --- the bullish case from the card ---------------------------------------------------------------

def _bullish_setup(sweep=True, closure=True, run_len=3):
    """Quiet bars, then a run of down-closes that takes out their low, then a close back above the run's
    opening price. That is the card's left-hand picture."""
    rows = flat(12, 100.0)                       # lows sit at 99.5
    price = 100.0
    for step in range(run_len):                  # each candle closes lower than it opened
        low = (99.0 - step) if sweep else (99.8 - step * 0.01)
        rows.append(down(price, price - 0.8, low=low))
        price -= 0.8
    # the closure: back above the open of the FIRST down candle (100.0)
    rows.append(up(price, 100.4 if closure else 99.9))
    return ohlc(rows)


def test_a_bullish_cisd_is_the_close_above_the_run_that_swept_lows():
    events = cisd_events(*_bullish_setup(), sweep_lookback=10, max_wait=5)
    assert len(events) == 1, [e.index for e in events]
    event = events[0]
    assert event.direction == 1
    assert event.index == 15, "the signal is the bar that closed through, not the run"
    assert event.run_start == 12 and event.run_end == 14


def test_the_level_is_the_open_of_the_first_candle_of_the_run():
    """The choice that makes a CISD an orderblock. The last candle's close would be a different idea."""
    event = cisd_events(*_bullish_setup(), sweep_lookback=10, max_wait=5)[0]
    assert event.level == 100.0


def test_a_run_that_swept_nothing_is_not_a_cisd():
    """'that swept lows' is part of the pattern. Without the sweep this is just a pullback."""
    assert cisd_events(*_bullish_setup(sweep=False), sweep_lookback=10, max_wait=5) == []


def test_no_closure_through_the_level_means_no_signal():
    assert cisd_events(*_bullish_setup(closure=False), sweep_lookback=10, max_wait=5) == []


def test_only_the_first_closure_fires():
    """Without this every later bar above the line re-fires, and the trade count counts bars, not setups."""
    o, h, l, c = _bullish_setup()
    for _ in range(4):                           # four more bars, all closing well above the level
        o.append(101.0); h.append(101.6); l.append(100.8); c.append(101.4)
    events = cisd_events(o, h, l, c, sweep_lookback=10, max_wait=8)
    assert len([e for e in events if e.direction == 1]) == 1


def test_the_level_expires():
    """A closure long after the run claims a sweep nobody is watching any more."""
    o, h, l, c = ohlc(flat(12, 100.0)
                      + [down(100.0, 99.2, low=99.0), down(99.2, 98.4, low=98.2)]
                      + flat(6, 98.6)            # six quiet bars below the level
                      + [up(98.6, 100.9)])       # then a close above it
    assert cisd_events(o, h, l, c, sweep_lookback=10, max_wait=3) == []
    assert len(cisd_events(o, h, l, c, sweep_lookback=10, max_wait=9)) == 1


def test_min_run_filters_short_series():
    setup = _bullish_setup(run_len=2)
    assert len(cisd_events(*setup, min_run=2, sweep_lookback=10, max_wait=5)) == 1
    assert cisd_events(*setup, min_run=3, sweep_lookback=10, max_wait=5) == []


# --- the bearish mirror ---------------------------------------------------------------------------

def test_a_bearish_cisd_is_the_mirror():
    rows = flat(12, 100.0)                       # highs sit at 100.5
    price = 100.0
    for step in range(3):
        high = 101.0 + step
        rows.append(up(price, price + 0.8, high=high))
        price += 0.8
    rows.append(down(price, 99.6))               # closes below the open of the first up candle
    events = cisd_events(*ohlc(rows), sweep_lookback=10, max_wait=5)
    assert len(events) == 1 and events[0].direction == -1
    assert events[0].level == 100.0
    assert events[0].run_extreme > events[0].swept_beyond, "a bearish run must take out prior HIGHS"


# --- no lookahead ---------------------------------------------------------------------------------

def test_a_signal_never_depends_on_a_later_bar():
    """The property the whole backtest rests on: truncating the data after the signal must not change it."""
    o, h, l, c = _bullish_setup()
    full = cisd_events(o, h, l, c, sweep_lookback=10, max_wait=5)
    assert full, "fixture must produce a signal"
    cut = full[0].index + 1
    truncated = cisd_events(o[:cut], h[:cut], l[:cut], c[:cut], sweep_lookback=10, max_wait=5)
    assert [(e.index, e.direction, e.level) for e in truncated] == \
           [(e.index, e.direction, e.level) for e in full]


def test_the_level_and_the_swept_extreme_are_known_by_the_signal_bar():
    event = cisd_events(*_bullish_setup(), sweep_lookback=10, max_wait=5)[0]
    assert event.run_end < event.index and event.run_start <= event.run_end
    assert event.bars_waited == event.index - event.run_end


# --- the arrays the backtest consumes -------------------------------------------------------------

def test_the_arrays_mark_only_the_signal_bar():
    side, level, extreme = cisd_arrays(*_bullish_setup(), sweep_lookback=10, max_wait=5)
    assert side.tolist().count(1) == 1
    i = side.tolist().index(1)
    assert i == 15 and level[i] == 100.0 and extreme[i] < 100.0


def test_a_bar_that_is_both_bullish_and_bearish_is_left_flat():
    """Two opposite readings of one candle is an ambiguity, not a signal; taking either is a coin toss."""
    side = [0, 0]
    events = [CISDEvent(index=1, direction=1, level=100.0, run_start=0, run_end=0, run_extreme=99.0,
                        swept_beyond=99.5, bars_waited=1),
              CISDEvent(index=1, direction=-1, level=101.0, run_start=0, run_end=0, run_extreme=102.0,
                        swept_beyond=101.5, bars_waited=1)]
    # exercised through the public path: build a frame where both complete on the same bar
    import numpy as np

    from src import cisd as module

    original = module.cisd_events
    module.cisd_events = lambda *a, **k: events
    try:
        out_side, out_level, _ = module.cisd_arrays([1, 1], [1, 1], [1, 1], [1, 1])
    finally:
        module.cisd_events = original
    assert out_side[1] == 0 and np.isnan(out_level[1])


def test_a_doji_ends_a_run_rather_than_extending_it():
    """close == open delivered nothing; counting it as continuation would stretch runs through flat bars."""
    rows = flat(12, 100.0) + [down(100.0, 99.2, low=99.0)] + flat(1, 99.2) + [down(99.2, 98.4, low=98.2)]
    rows.append((98.4, 100.6, 98.3, 100.4))
    events = cisd_events(*ohlc(rows), min_run=2, sweep_lookback=10, max_wait=5)
    assert events == [], "the doji split the run, so neither half reaches two candles"


# --- the summary ----------------------------------------------------------------------------------

def test_the_summary_counts_and_places_no_orders():
    out = cisd_summary(*_bullish_setup(), sweep_lookback=10, max_wait=5)
    assert out["events"] == 1 and out["bullish"] == 1 and out["bearish"] == 0
    assert out["places_orders"] is False


def test_an_empty_frame_is_handled():
    assert cisd_events([], [], [], []) == []


# --- how a near-miss is reported ------------------------------------------------------------------

def test_a_candidate_above_sr0_is_not_described_as_short_of_it():
    """sr0 is where confidence reaches 50%, not 95%. A candidate above it that still fails the bar was
    described as 'short by -0.03', which reads as a shortfall when it is a surplus - and the difference
    between "no edge" and "a real but unproven edge" is not a wording nicety, it decides what happens next.
    """
    from src.strategy_lab import _deflation_note

    note = _deflation_note(achieved=0.1627, target=0.1304, dsr=0.6052)
    assert "short by -" not in note
    assert "ABOVE" in note and "0.0323" in note


def test_a_candidate_genuinely_below_sr0_still_says_short_by():
    from src.strategy_lab import _deflation_note

    note = _deflation_note(achieved=0.05, target=0.13, dsr=0.2)
    assert "short by 0.08" in note


def test_a_passing_candidate_says_it_cleared():
    from src.strategy_lab import _deflation_note

    assert "clears" in _deflation_note(achieved=0.5, target=0.13, dsr=0.97)
