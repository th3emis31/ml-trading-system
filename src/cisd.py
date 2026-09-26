"""Change In State Of Delivery (CISD), exactly as the owner's diagram defines it. Detection only.

    python -m src.cisd XAUUSD 4h

THE DEFINITION BEING IMPLEMENTED
--------------------------------
From the owner's card, word for word:

* **Bullish CISD** - "Candle closure ABOVE the series of Downclose candles that swept lows."
* **Bearish CISD** - "Candle closure BELOW the series of Upclose candles that swept highs."
* "A CISD is essentially an Orderblock."

Four things have to be pinned down before that can be measured, because the card states the idea and not
the arithmetic. Each choice is written here rather than buried in the backtest, so it can be argued with:

1. **Where the line sits.** At the OPEN of the FIRST candle of the series. That is what makes a CISD "an
   orderblock": the line is the price the delivery run began from, so closing back through it says the
   run has been undone. Using the close of the last candle instead would fire far more often and mean
   something else.
2. **What "swept lows" means.** The run's own lowest low must be BELOW the lowest low of the
   ``sweep_lookback`` bars immediately before the run began. A run that simply falls without taking out
   prior lows has swept nothing, and the card is explicit that the sweep is part of the pattern.
3. **What counts as the closure.** The FIRST bar after the run whose close is beyond the line. Without
   "first", every later bar above the line re-fires the same event and the trade count becomes a count of
   bars, not of setups.
4. **How long the level stays live.** ``max_wait`` bars after the run ends. A level that never expires
   would let a closure forty bars later claim a sweep nobody was watching any more.

NO LOOKAHEAD
------------
Every value used to decide bar ``i`` comes from bars at or before ``i``: the run, its sweep, the level and
the risk are all known by the close of ``i``. The backtest that consumes this enters at the NEXT bar's
open, so the signal bar's own close is never a fill price.

This module places no orders and calls no broker. It reports what it found; whether it is worth trading is
decided by measurement in ``src/cisd_lab.py`` and recorded in ``.claude/memory/BASELINE.md``.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import List, Optional, Sequence

import numpy as np

DEFAULT_MIN_RUN = 1          # the card draws several, but says "series" - one candle is the loosest reading
DEFAULT_SWEEP_LOOKBACK = 10  # bars before the run whose extreme must be taken out
DEFAULT_MAX_WAIT = 5         # bars the level stays live after the run ends


@dataclass
class CISDEvent:
    """One completed CISD: the bar whose close broke the line, and everything that defined the line."""

    index: int                # the bar that closed through the level - the signal bar
    direction: int            # +1 bullish, -1 bearish
    level: float              # the open of the first candle of the run
    run_start: int
    run_end: int
    run_extreme: float        # the run's lowest low (bullish) or highest high (bearish) - the swept price
    swept_beyond: float       # the prior extreme the run took out
    bars_waited: int          # signal index - run_end


def _runs(o: np.ndarray, c: np.ndarray, direction: int) -> List[tuple]:
    """Maximal runs of consecutive candles closing that way, as (start, end) inclusive.

    Down-close is ``close < open`` and up-close is ``close > open``; a doji (close == open) closes neither
    way and therefore ends a run without starting one. The card's diagram shows solid bodies throughout,
    and treating a doji as a continuation would silently extend runs through bars that delivered nothing.
    """
    matches = (c < o) if direction < 0 else (c > o)
    out, start = [], None
    for i, hit in enumerate(matches):
        if hit and start is None:
            start = i
        elif not hit and start is not None:
            out.append((start, i - 1))
            start = None
    if start is not None:
        out.append((start, len(matches) - 1))
    return out


def cisd_events(opens: Sequence, highs: Sequence, lows: Sequence, closes: Sequence, *,
                min_run: int = DEFAULT_MIN_RUN, sweep_lookback: int = DEFAULT_SWEEP_LOOKBACK,
                max_wait: int = DEFAULT_MAX_WAIT) -> List[CISDEvent]:
    """Every CISD in the frame, in bar order. See the module docstring for each choice made."""
    o = np.asarray(opens, dtype=float)
    h = np.asarray(highs, dtype=float)
    low = np.asarray(lows, dtype=float)
    c = np.asarray(closes, dtype=float)
    n = len(c)
    if n == 0 or not (len(o) == len(h) == len(low) == n):
        return []

    events: List[CISDEvent] = []
    for direction in (1, -1):
        # A bullish CISD is built on a run of DOWN-close candles, and vice versa.
        for start, end in _runs(o, c, -direction):
            if end - start + 1 < min_run:
                continue
            before_from = start - sweep_lookback
            if before_from < 0:
                continue                      # not enough history to say anything was swept
            before = slice(before_from, start)
            if direction == 1:
                run_extreme = float(low[start:end + 1].min())
                prior = float(low[before].min())
                swept = run_extreme < prior
            else:
                run_extreme = float(h[start:end + 1].max())
                prior = float(h[before].max())
                swept = run_extreme > prior
            if not swept:
                continue                      # "that swept lows" is part of the pattern, not a garnish

            level = float(o[start])
            # The FIRST close beyond the line, within the window. Bars inside the run cannot qualify:
            # the closure is what ends the state of delivery, so it comes after the run.
            for i in range(end + 1, min(end + 1 + max_wait, n)):
                beyond = c[i] > level if direction == 1 else c[i] < level
                if beyond:
                    events.append(CISDEvent(index=i, direction=direction, level=level,
                                            run_start=start, run_end=end, run_extreme=run_extreme,
                                            swept_beyond=prior, bars_waited=i - end))
                    break
    events.sort(key=lambda e: (e.index, -e.direction))
    return events


def cisd_arrays(opens, highs, lows, closes, *, min_run: int = DEFAULT_MIN_RUN,
                sweep_lookback: int = DEFAULT_SWEEP_LOOKBACK,
                max_wait: int = DEFAULT_MAX_WAIT) -> tuple:
    """``(side, level, extreme)`` per bar, for the backtest. 0 where no CISD closed on that bar.

    Where a bullish and a bearish CISD both complete on the same bar the bar is left flat rather than
    guessed at: two opposite readings of one candle is not a signal, it is an ambiguity, and taking either
    one would be a coin toss recorded as an edge.
    """
    n = len(closes)
    side = np.zeros(n, dtype=int)
    level = np.full(n, np.nan)
    extreme = np.full(n, np.nan)
    for event in cisd_events(opens, highs, lows, closes, min_run=min_run,
                             sweep_lookback=sweep_lookback, max_wait=max_wait):
        i = event.index
        if side[i] != 0 and side[i] != event.direction:
            side[i] = 0
            level[i] = np.nan
            extreme[i] = np.nan
            continue
        side[i] = event.direction
        level[i] = event.level
        extreme[i] = event.run_extreme
    return side, level, extreme


def cisd_summary(opens, highs, lows, closes, **kwargs) -> dict:
    events = cisd_events(opens, highs, lows, closes, **kwargs)
    bullish = [e for e in events if e.direction == 1]
    runs = [e.run_end - e.run_start + 1 for e in events]
    return {"bars": len(closes), "events": len(events),
            "bullish": len(bullish), "bearish": len(events) - len(bullish),
            "mean_run_length": round(float(np.mean(runs)), 2) if runs else None,
            "mean_bars_waited": round(float(np.mean([e.bars_waited for e in events])), 2) if events else None,
            "places_orders": False}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Find CISDs in broker bars. Reads only, never trades.")
    parser.add_argument("symbol", nargs="?", default="XAUUSD")
    parser.add_argument("timeframe", nargs="?", default="4h")
    parser.add_argument("--min-run", type=int, default=DEFAULT_MIN_RUN)
    parser.add_argument("--sweep-lookback", type=int, default=DEFAULT_SWEEP_LOOKBACK)
    parser.add_argument("--max-wait", type=int, default=DEFAULT_MAX_WAIT)
    args = parser.parse_args(argv)

    from .mtf_data import load_bars

    bars = load_bars(args.symbol, args.timeframe, source="app")
    if bars is None or bars.empty:
        print(f"no broker bars for {args.symbol} {args.timeframe}")
        return 1
    bars = bars.sort_values("datetime").reset_index(drop=True)
    kwargs = {"min_run": args.min_run, "sweep_lookback": args.sweep_lookback, "max_wait": args.max_wait}
    out = cisd_summary(bars["open"], bars["high"], bars["low"], bars["close"], **kwargs)
    print(f"{args.symbol} {args.timeframe}: {out['bars']:,} bars -> {out['events']} CISD "
          f"({out['bullish']} bullish, {out['bearish']} bearish)")
    print(f"  run length {out['mean_run_length']} candles on average, closure {out['mean_bars_waited']} "
          f"bars after the run")
    events = cisd_events(bars["open"], bars["high"], bars["low"], bars["close"], **kwargs)
    for event in events[-6:]:
        when = str(bars.loc[event.index, "datetime"])[:16]
        print(f"    {when}  {'bullish' if event.direction == 1 else 'bearish'}  level {event.level:.2f}  "
              f"swept {event.run_extreme:.2f} through {event.swept_beyond:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
