"""Volume profile: POC, VAH, VAL, and POC migration. Step 4 of the owner's SmartEntry architecture.

    python -m src.volume_profile XAUUSD 4h

WHAT A VOLUME PROFILE IS, AND THE CHOICES THIS MAKES
----------------------------------------------------
A price histogram rather than a time histogram: every bar's volume is spread across the prices it
traded at, and the result says WHERE business was done rather than when.

* **POC (point of control)** - the single price with the most volume behind it. The card calls it the
  "price acceptance area", acting as support or resistance: reclaim it and that is bullish, reject it
  and that is bearish.
* **VAH / VAL (value area high and low)** - the band around the POC holding ``value_area`` of all the
  volume, 70 % by convention. The card uses them as targets and reaction zones.

Four things have to be pinned down, because "volume profile" names a family of calculations:

1. **The window.** A rolling lookback of ``lookback`` bars, recomputed on every bar. Session or
   composite profiles are the other common choice; a rolling window is the one that can be evaluated on
   any bar without knowing where a session boundary falls, which matters for a 24-hour market like gold.
2. **How a bar's volume is spread.** Evenly across the bins its HIGH-LOW range touches. A bar is a
   summary of many trades and the profile has no idea where inside the bar they happened, so spreading
   evenly is the assumption that adds the least. Putting it all at the close would be a different, and
   much spikier, instrument.
3. **Bin width.** ``bins`` equal-width price buckets across the window's own range, so the resolution
   follows the market rather than a fixed number of dollars that means something different on gold at
   1,200 and gold at 4,300.
4. **Causality.** The profile for bar ``i`` uses bars ``i-lookback+1 .. i`` and nothing after. That is
   what makes it usable in a backtest; a profile built on the whole history and then read at an early
   bar is the classic way this indicator lies.

WHAT THIS DOES NOT DO
---------------------
It places no orders and takes no view. It reports levels; whether trading them is worth anything is
decided by measurement in ``src/smart_entry_arch.py`` and recorded in ``.claude/memory/BASELINE.md``.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Optional

import numpy as np

DEFAULT_LOOKBACK = 120       # 4H bars: about twenty days of business
DEFAULT_BINS = 50
DEFAULT_VALUE_AREA = 0.70


@dataclass
class Profile:
    """One window's profile."""

    poc: float
    vah: float
    val: float
    total_volume: float
    bins: int
    low: float
    high: float


def profile_window(highs, lows, volumes, *, bins: int = DEFAULT_BINS,
                   value_area: float = DEFAULT_VALUE_AREA) -> Optional[Profile]:
    """The profile of ONE window of bars. None when the window cannot produce one.

    The value area grows out from the POC, each step taking whichever neighbouring bin - above or below -
    holds more volume, until ``value_area`` of the total is inside. That is the standard construction and
    it is deliberately not a symmetric band: value is usually lopsided, and forcing symmetry would move
    VAH and VAL to prices nothing traded at.
    """
    high = np.asarray(highs, dtype=float)
    low = np.asarray(lows, dtype=float)
    volume = np.asarray(volumes, dtype=float)
    if len(high) == 0 or len(high) != len(low) or len(high) != len(volume):
        return None
    good = np.isfinite(high) & np.isfinite(low) & np.isfinite(volume) & (volume >= 0)
    high, low, volume = high[good], low[good], volume[good]
    if len(high) == 0:
        return None

    top, bottom = float(high.max()), float(low.min())
    if not np.isfinite(top) or not np.isfinite(bottom) or top <= bottom:
        return None
    total = float(volume.sum())
    if total <= 0:
        return None

    edges = np.linspace(bottom, top, bins + 1)
    centres = (edges[:-1] + edges[1:]) / 2.0
    buckets = np.zeros(bins, dtype=float)

    # Each bar's volume spread evenly over the bins its range touches.
    lower = np.clip(np.searchsorted(edges, low, side="right") - 1, 0, bins - 1)
    upper = np.clip(np.searchsorted(edges, high, side="left") - 1, 0, bins - 1)
    upper = np.maximum(upper, lower)
    for start, end, size in zip(lower, upper, volume):
        span = end - start + 1
        buckets[start:end + 1] += size / span

    # On a tie, `argmax` returns the LOWEST bin, which biases the POC downward whenever the histogram is
    # flat or nearly flat - and a flat histogram is exactly what a quiet range produces. Among tied bins,
    # take the one nearest the volume-weighted average price, which is the tie-break that adds no
    # direction of its own.
    top_volume = buckets.max()
    tied = np.flatnonzero(buckets >= top_volume - 1e-12)
    if len(tied) == 1:
        peak = int(tied[0])
    else:
        vwap = float((centres * buckets).sum() / buckets.sum()) if buckets.sum() > 0 else float(centres.mean())
        peak = int(tied[np.argmin(np.abs(centres[tied] - vwap))])
    inside = buckets[peak]
    low_index = high_index = peak
    wanted = total * float(value_area)
    while inside < wanted and (low_index > 0 or high_index < bins - 1):
        below = buckets[low_index - 1] if low_index > 0 else -1.0
        above = buckets[high_index + 1] if high_index < bins - 1 else -1.0
        if above >= below:
            high_index += 1
            inside += buckets[high_index]
        else:
            low_index -= 1
            inside += buckets[low_index]

    return Profile(poc=float(centres[peak]), vah=float(edges[high_index + 1]),
                   val=float(edges[low_index]), total_volume=total, bins=bins,
                   low=bottom, high=top)


def rolling_levels(highs, lows, volumes, *, lookback: int = DEFAULT_LOOKBACK,
                   bins: int = DEFAULT_BINS, value_area: float = DEFAULT_VALUE_AREA) -> tuple:
    """``(poc, vah, val)`` per bar, each from the ``lookback`` bars ending at that bar.

    Causal by construction: bar ``i`` sees ``i-lookback+1 .. i``. Bars before the first full window get
    NaN rather than a profile built from fewer bars, because a profile of twelve candles and a profile of
    a hundred and twenty are not the same measurement and blending them silently changes what the level
    means early in the series.
    """
    high = np.asarray(highs, dtype=float)
    low = np.asarray(lows, dtype=float)
    volume = np.asarray(volumes, dtype=float)
    n = len(high)
    poc = np.full(n, np.nan)
    vah = np.full(n, np.nan)
    val = np.full(n, np.nan)
    if n == 0 or lookback < 2:
        return poc, vah, val
    for i in range(lookback - 1, n):
        window = slice(i - lookback + 1, i + 1)
        found = profile_window(high[window], low[window], volume[window], bins=bins,
                               value_area=value_area)
        if found is not None:
            poc[i], vah[i], val[i] = found.poc, found.vah, found.val
    return poc, vah, val


def poc_migration(poc: np.ndarray, span: int = 10) -> np.ndarray:
    """+1 where the POC is higher than it was ``span`` bars ago, -1 lower, 0 unchanged or unknown.

    The card reads this directly: "POC moving up = bullish auction, POC moving down = bearish auction".
    """
    values = np.asarray(poc, dtype=float)
    out = np.zeros(len(values), dtype=int)
    if len(values) <= span:
        return out
    previous = np.full(len(values), np.nan)
    previous[span:] = values[:-span]
    with np.errstate(invalid="ignore"):
        out = np.where(np.isfinite(values) & np.isfinite(previous) & (values > previous), 1,
                       np.where(np.isfinite(values) & np.isfinite(previous) & (values < previous), -1, 0))
    return out.astype(int)


def profile_summary(highs, lows, volumes, **kwargs) -> dict:
    found = profile_window(highs, lows, volumes, **kwargs)
    if found is None:
        return {"available": False, "reason": "no usable bars or no volume", "places_orders": False}
    return {"available": True, "poc": round(found.poc, 4), "vah": round(found.vah, 4),
            "val": round(found.val, 4), "range_low": round(found.low, 4),
            "range_high": round(found.high, 4), "total_volume": found.total_volume,
            "places_orders": False}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Volume profile levels from broker bars. Reads only.")
    parser.add_argument("symbol", nargs="?", default="XAUUSD")
    parser.add_argument("timeframe", nargs="?", default="4h")
    parser.add_argument("--lookback", type=int, default=DEFAULT_LOOKBACK)
    parser.add_argument("--bins", type=int, default=DEFAULT_BINS)
    args = parser.parse_args(argv)

    from .mtf_data import load_bars

    frame = load_bars(args.symbol, args.timeframe, source="app")
    if frame is None or frame.empty:
        print(f"no broker bars for {args.symbol} {args.timeframe}")
        return 1
    frame = frame.sort_values("datetime").reset_index(drop=True)
    if "volume" not in frame.columns or float(frame["volume"].fillna(0).sum()) <= 0:
        print(f"{args.symbol} {args.timeframe}: the bars carry no volume, so no profile can be built")
        return 1

    recent = frame.tail(args.lookback)
    out = profile_summary(recent["high"], recent["low"], recent["volume"], bins=args.bins)
    last = float(frame["close"].iloc[-1])
    print(f"{args.symbol} {args.timeframe}: profile of the last {args.lookback} bars")
    print(f"  VAH {out['vah']:.2f}")
    print(f"  POC {out['poc']:.2f}   <- price acceptance")
    print(f"  VAL {out['val']:.2f}")
    print(f"  last close {last:.2f} -> {'ABOVE' if last > out['poc'] else 'BELOW'} the POC")
    poc, _vah, _val = rolling_levels(frame["high"], frame["low"], frame["volume"],
                                     lookback=args.lookback, bins=args.bins)
    drift = poc_migration(poc)
    recent_drift = drift[-1]
    print(f"  POC migration: {'rising (bullish auction)' if recent_drift > 0 else 'falling (bearish auction)' if recent_drift < 0 else 'flat'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
