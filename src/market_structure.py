"""Market structure: swings, HH/HL/LH/LL, BOS and CHOCH — layer 3 of the owner's engine diagram.

    python -m src.market_structure XAUUSD 4h

WHAT THESE MEAN, AND THE CHOICES THIS MODULE MAKES
--------------------------------------------------
A **swing** is a confirmed pivot: a bar whose high is the highest of the `prd` bars either side of it
(or whose low is the lowest). It is only *known* `prd` bars after it happened, and that lag is enforced
throughout — a structure event at bar j may only reference swings confirmed at or before j. Forgetting
that is the classic way this pattern backtests beautifully and cannot be traded.

**BOS (break of structure)** is continuation: in an uptrend, price takes out the most recent confirmed
swing HIGH. In a downtrend, it takes out the most recent swing LOW.

**CHOCH (change of character)** is the first warning of a reversal: in an uptrend, price takes out the
most recent confirmed swing LOW instead. In a downtrend, the most recent swing HIGH.

Three choices are made explicitly, because each changes what the labels mean:

1. **Breaks are measured on the CLOSE, not the wick.** A wick through a level and back is common on gold
   and would double or triple the event count. Close-based is the conservative reading; `wick_break=True`
   switches it for comparison rather than hiding the choice.
2. **Trend state starts as `unknown`** and only becomes bullish or bearish once a BOS confirms it. It is
   never guessed from the first two swings.
3. **HH/HL/LH/LL label each swing against the previous swing of the SAME kind** — a high against the
   previous high, a low against the previous low. Comparing a high to a low would be meaningless.

WHAT THIS MODULE DOES NOT DO
----------------------------
It does not trade, gate an entry, or change any signal. It labels bars so the journal and the analysis
can ask whether structure carries information — and that question is answered by measurement in
`tests/` and `BASELINE.md`, not by the existence of the labels. On this system every such descriptive
split so far (session, regime, RSI band) has turned out to be a hypothesis rather than a filter, and
this one starts in exactly that position.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import List, Optional, Sequence

from .breakout_finder import detect_pivots

# Named `structure_swings` and `structure_summary` rather than the obvious `swing_points`/`summarise`
# because both of those already exist and mean something else: `daily_report.swing_points` takes a pandas
# frame and returns the last three as display strings for the support/resistance panel, with no
# confirmation lag and no HH/HL labels, and `plan_journal.summarise` summarises plans. The pivot detection
# itself IS reused - `detect_pivots` above - rather than written a second time.

DEFAULT_PRD = 5


@dataclass
class Swing:
    """One confirmed pivot. `confirmed_at` is the first bar on which it could have been known."""

    index: int
    price: float
    kind: str                 # "high" or "low"
    confirmed_at: int
    label: Optional[str] = None      # HH / HL / LH / LL, against the previous swing of the same kind


@dataclass
class StructureEvent:
    index: int
    ts: str
    kind: str                 # "BOS" or "CHOCH"
    direction: str            # "bullish" or "bearish"
    level: float              # the swing price that was taken out
    swing_index: int          # which swing was broken
    trend_before: str
    trend_after: str


def structure_swings(candles: Sequence, prd: int = DEFAULT_PRD) -> List[Swing]:
    """Confirmed swings in time order, labelled HH/HL/LH/LL against the previous swing of the same kind."""
    highs, lows = detect_pivots(candles, prd)
    out = [Swing(index=p.index, price=p.value, kind="high", confirmed_at=p.index + prd) for p in highs]
    out += [Swing(index=p.index, price=p.value, kind="low", confirmed_at=p.index + prd) for p in lows]
    out.sort(key=lambda s: s.index)

    last = {"high": None, "low": None}
    for swing in out:
        previous = last[swing.kind]
        if previous is not None:
            if swing.kind == "high":
                swing.label = "HH" if swing.price > previous.price else "LH"
            else:
                swing.label = "HL" if swing.price > previous.price else "LL"
        last[swing.kind] = swing
    return out


def structure_events(candles: Sequence, prd: int = DEFAULT_PRD,
                     wick_break: bool = False) -> List[StructureEvent]:
    """Every BOS and CHOCH, in order, using only swings confirmed by the bar that breaks them."""
    swings = structure_swings(candles, prd)
    events: List[StructureEvent] = []
    trend = "unknown"
    # The last swing of each kind that was already CONFIRMED, and not yet broken.
    pending_high: Optional[Swing] = None
    pending_low: Optional[Swing] = None
    cursor = 0

    for i, bar in enumerate(candles):
        # Bring in every swing whose confirmation bar has now passed.
        while cursor < len(swings) and swings[cursor].confirmed_at <= i:
            swing = swings[cursor]
            if swing.kind == "high":
                pending_high = swing
            else:
                pending_low = swing
            cursor += 1

        up_level = bar.high if wick_break else bar.close
        down_level = bar.low if wick_break else bar.close

        if pending_high is not None and up_level > pending_high.price:
            kind = "BOS" if trend in ("bullish", "unknown") else "CHOCH"
            before, trend = trend, "bullish"
            events.append(StructureEvent(index=i, ts=bar.ts, kind=kind, direction="bullish",
                                         level=pending_high.price, swing_index=pending_high.index,
                                         trend_before=before, trend_after=trend))
            pending_high = None          # a level is broken once; the next swing supersedes it
            continue

        if pending_low is not None and down_level < pending_low.price:
            kind = "BOS" if trend in ("bearish", "unknown") else "CHOCH"
            before, trend = trend, "bearish"
            events.append(StructureEvent(index=i, ts=bar.ts, kind=kind, direction="bearish",
                                         level=pending_low.price, swing_index=pending_low.index,
                                         trend_before=before, trend_after=trend))
            pending_low = None
    return events


def structure_at(candles: Sequence, prd: int = DEFAULT_PRD,
                 wick_break: bool = False) -> List[dict]:
    """Per-bar structure state, so any bar can be asked what the structure was AT THAT MOMENT.

    Returned as one row per bar rather than a single summary, because the useful question is always
    "what did structure say when this trade was entered", and answering that from a final summary would
    read the future.
    """
    events = structure_events(candles, prd, wick_break)
    by_index = {e.index: e for e in events}
    rows, trend, last_event = [], "unknown", None
    bars_since = None
    for i, bar in enumerate(candles):
        event = by_index.get(i)
        if event is not None:
            trend, last_event, bars_since = event.trend_after, event, 0
        elif bars_since is not None:
            bars_since += 1
        rows.append({
            "index": i, "ts": bar.ts, "trend": trend,
            "event": event.kind if event else None,
            "event_direction": event.direction if event else None,
            "last_event": last_event.kind if last_event else None,
            "last_event_direction": last_event.direction if last_event else None,
            "bars_since_event": bars_since,
        })
    return rows


def structure_summary(candles: Sequence, prd: int = DEFAULT_PRD, wick_break: bool = False) -> dict:
    swings = structure_swings(candles, prd)
    events = structure_events(candles, prd, wick_break)
    labels: dict = {}
    for swing in swings:
        if swing.label:
            labels[swing.label] = labels.get(swing.label, 0) + 1
    counts: dict = {}
    for event in events:
        key = f"{event.kind} {event.direction}"
        counts[key] = counts.get(key, 0) + 1
    return {"bars": len(candles), "prd": prd, "break_on": "wick" if wick_break else "close",
            "swings": len(swings), "swing_labels": labels,
            "events": len(events), "event_counts": counts,
            "final_trend": (structure_at(candles, prd, wick_break) or [{}])[-1].get("trend"),
            "places_orders": False}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Swings, HH/HL/LH/LL, BOS and CHOCH. Reads only.")
    parser.add_argument("symbol", nargs="?", default="XAUUSD")
    parser.add_argument("timeframe", nargs="?", default="4h")
    parser.add_argument("--bars", type=int, default=4000)
    parser.add_argument("--prd", type=int, default=DEFAULT_PRD)
    parser.add_argument("--wick", action="store_true", help="break on the wick instead of the close")
    args = parser.parse_args(argv)

    from .mtf_data import fetch_app_bars
    from .volatility_trend_breakout import Candle

    frame = fetch_app_bars(args.symbol, args.timeframe, args.bars).sort_values("datetime").reset_index(drop=True)
    candles = [Candle(ts=str(r["datetime"]), open=float(r["open"]), high=float(r["high"]),
                      low=float(r["low"]), close=float(r["close"]), volume=float(r.get("volume") or 0))
               for _, r in frame.iterrows()]
    out = structure_summary(candles, args.prd, args.wick)
    print(f"{args.symbol} {args.timeframe}: {out['bars']:,} bars, pivot period {out['prd']}, "
          f"breaks on the {out['break_on']}")
    print(f"  swings {out['swings']:,}   " + "  ".join(f"{k} {v}" for k, v in sorted(out["swing_labels"].items())))
    print(f"  events {out['events']:,}   " + "  ".join(f"{k} {v}" for k, v in sorted(out["event_counts"].items())))
    print(f"  structure now: {out['final_trend']}")
    for event in structure_events(candles, args.prd, args.wick)[-6:]:
        print(f"    {event.ts[:16]}  {event.kind:5} {event.direction:8} broke {event.level:.2f} "
              f"({event.trend_before} -> {event.trend_after})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
