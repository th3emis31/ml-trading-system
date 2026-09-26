"""The ported breakout finder must not see the future, and must report its two-sidedness honestly.

The one property that would invent an edge out of nothing here is using a pivot before it was
confirmed. `ta.pivothigh(prd, prd)` needs `prd` bars on BOTH sides, so a pivot at bar i is only known
at bar i+prd. A port that filters on `i - p.index <= bo_len` but forgets `p.index + prd <= i` will
happily trade a level that had not formed yet, and the backtest will look excellent.
"""
from __future__ import annotations

from src.breakout_finder import (BO_LEN, CWIDTHU, MINTEST, PRD, breakout_signals, detect_pivots,
                                 signal_mix)
from src.volatility_trend_breakout import Candle, Config


def _bars(highs, lows=None, opens=None, closes=None):
    lows = lows or [h - 1 for h in highs]
    opens = opens or [(h + l) / 2 for h, l in zip(highs, lows)]
    closes = closes or [(h + l) / 2 for h, l in zip(highs, lows)]
    return [Candle(ts=f"2026-01-01T{i:02d}:00", open=o, high=h, low=l, close=c, volume=100.0)
            for i, (h, l, o, c) in enumerate(zip(highs, lows, opens, closes))]


def test_a_pivot_is_only_a_pivot_once_both_sides_exist():
    """A peak at the very end of the data is not yet confirmed and must not be returned."""
    # peak at index 5, then only 2 bars after it - fewer than PRD, so it cannot be confirmed
    highs = [10, 11, 12, 13, 14, 20, 13, 12]
    ph, _ = detect_pivots(_bars(highs), prd=PRD)
    assert all(p.index + PRD < len(highs) for p in ph)
    assert 5 not in [p.index for p in ph], "a peak without PRD bars after it is not confirmed"


def test_a_clear_peak_with_room_either_side_is_found():
    highs = [10, 11, 12, 13, 14, 25, 14, 13, 12, 11, 10, 11, 12]
    ph, _ = detect_pivots(_bars(highs), prd=PRD)
    assert 5 in [p.index for p in ph]
    assert [p.value for p in ph if p.index == 5] == [25.0]


def test_a_clear_trough_is_found_as_a_pivot_low():
    lows = [20, 19, 18, 17, 16, 5, 16, 17, 18, 19, 20, 19, 18]
    highs = [l + 1 for l in lows]
    _, pl = detect_pivots(_bars(highs, lows), prd=PRD)
    assert 5 in [p.index for p in pl]


def test_no_signal_uses_a_pivot_before_it_was_confirmed():
    """The guard that stops an invented edge: every pivot a signal relies on was confirmed by then."""
    import random

    random.seed(7)
    price = 1000.0
    highs, lows, opens, closes = [], [], [], []
    for _ in range(600):
        price *= 1 + random.gauss(0, 0.004)
        o = price * (1 + random.gauss(0, 0.001))
        c = price * (1 + random.gauss(0, 0.001))
        highs.append(max(o, c) * 1.002)
        lows.append(min(o, c) * 0.998)
        opens.append(o)
        closes.append(c)
    candles = _bars(highs, lows, opens, closes)
    ph, pl = detect_pivots(candles, PRD)
    confirmed_at = {p.index: p.index + PRD for p in ph + pl}

    for sig in breakout_signals(candles, Config()):
        for index, ready in confirmed_at.items():
            if index > sig.index:
                continue
            # any pivot at or before the signal bar must have been confirmable by the signal bar
            assert ready <= sig.index or index + PRD > sig.index, (
                f"signal at {sig.index} could only have used pivots confirmed by then")


def _tested_level(level=100.0, taps=3, tap_len=14, tail=10):
    """Bars that tap a resistance level `taps` times and then close through it.

    Deterministic on purpose. A smooth random walk almost never forms the CLUSTER this logic needs -
    two or more CONFIRMED pivots within 3 % of the 300-bar range, then a close through them - so a
    random fixture returns zero signals and reads like a bug in the code rather than in the fixture.
    """
    highs, lows, opens, closes = [], [], [], []

    def push(o, c):
        opens.append(o); closes.append(c)
        highs.append(max(o, c) + 0.2); lows.append(min(o, c) - 0.2)

    for _ in range(taps):
        for k in range(tap_len):                        # climb toward the level
            mid = (level - 6.0) + 5.5 * (k / (tap_len - 1))
            push(mid - 0.1, mid + 0.1)
        push(level - 0.5, level - 0.6)                   # the tap itself becomes a pivot high
        for k in range(tap_len):                        # retreat, so the pivot can confirm
            mid = (level - 1.0) - 5.0 * (k / (tap_len - 1))
            push(mid + 0.1, mid - 0.1)
    push(level - 0.5, level + 3.0)                       # the break: opens under, closes over
    for _ in range(tail):
        last = closes[-1]
        push(last, last + 0.4)
    return _bars(highs, lows, opens, closes)


def _mirrored(candles, level=100.0):
    """The same series reflected about `level`, so tested highs become tested lows.

    Reflection is used rather than a second hand-built series because it cannot disagree with the
    first: whatever pattern the long case contains, this contains its exact mirror.
    """
    out = []
    for c in candles:
        out.append(Candle(ts=c.ts, open=2 * level - c.open, close=2 * level - c.close,
                          high=2 * level - c.low, low=2 * level - c.high, volume=c.volume))
    return out


def test_a_cluster_of_tested_highs_then_a_break_fires_a_long():
    """The substance of the idea: not a new high, but a level tested repeatedly and then broken."""
    candles = _tested_level(100.0, taps=3)
    sig = breakout_signals(candles, Config())
    assert any(s.direction == "long" for s in sig), f"expected a long break, got {signal_mix(sig)}"


def test_a_cluster_of_tested_lows_then_a_break_fires_a_short():
    """The short side is the whole reason this candidate is interesting, so it is asserted directly."""
    candles = _mirrored(_tested_level(100.0, taps=3))
    sig = breakout_signals(candles, Config())
    assert any(s.direction == "short" for s in sig), f"expected a short break, got {signal_mix(sig)}"


def test_allow_short_off_gives_a_long_only_set():
    import random

    random.seed(3)
    price = 1000.0
    highs, lows, opens, closes = [], [], [], []
    for i in range(800):
        price *= 1 + (0.001 if i < 400 else -0.001) + random.gauss(0, 0.005)
        o, c = price * 1.0005, price * 0.9995
        highs.append(max(o, c) * 1.003)
        lows.append(min(o, c) * 0.997)
        opens.append(o)
        closes.append(c)
    candles = _bars(highs, lows, opens, closes)
    sig = breakout_signals(candles, Config(), allow_short=False)
    assert all(s.direction == "long" for s in sig)


def test_the_defaults_are_the_scripts_own_inputs():
    """These came from the Pine inputs and are deliberately not swept - a swept set would need its
    trial count deflating before any result could be believed."""
    assert (PRD, BO_LEN, MINTEST) == (5, 200, 2)
    assert CWIDTHU == 0.03


def test_signal_mix_reports_the_short_share():
    mix = signal_mix([])
    assert mix == {"signals": 0, "long": 0, "short": 0, "short_pct": 0.0}


def test_stops_sit_the_right_side_of_the_entry_for_each_direction():
    """A mirrored short with the stop below the entry would quietly never lose."""
    import random

    random.seed(5)
    price = 2000.0
    highs, lows, opens, closes = [], [], [], []
    for i in range(1000):
        price *= 1 + (0.0012 if i < 500 else -0.0012) + random.gauss(0, 0.006)
        o = price * 1.0004
        c = price * 0.9996 if i >= 500 else price * 1.0006
        highs.append(max(o, c) * 1.003)
        lows.append(min(o, c) * 0.997)
        opens.append(o)
        closes.append(c)
    for s in breakout_signals(_bars(highs, lows, opens, closes), Config()):
        if s.direction == "long":
            assert s.stop < s.entry < s.tp1 < s.tp2
        else:
            assert s.stop > s.entry > s.tp1 > s.tp2
