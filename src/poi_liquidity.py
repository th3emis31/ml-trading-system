"""The owner's liquidity / point-of-interest model from the Trader's Grit chart. Detection only.

    python -m src.poi_liquidity XAUUSD 30m

WHAT THE CHART SHOWS, READ LEFT TO RIGHT
----------------------------------------
1. An early high, with the buy-side liquidity above it marked **$$$**.
2. Structure breaks DOWN - two **BOS** lines - so the trend is bearish.
3. A final low that takes out the previous low: **SSL**, the sell-side liquidity, swept.
4. Price rallies back up into the **POINT OF INTEREST** at the prior swing high, where the $$$ sits.
5. The boxes mark the trade taken there.

TWO READINGS, BOTH TESTED
-------------------------
The chart draws the setup but not the direction, and the two readings are opposite trades:

* **fade** - the POI is resistance. Price sweeps the lows, rallies into the POI, and you SELL there,
  targeting back down. The small box above the POI is the stop, the large box below is the target.
* **ride** - the sweep is the entry. You BUY when the lows are swept and TARGET the POI.

Both are in the grid because guessing cost a whole backtest once already: on 19 September this project
tested the fade reading of the owner's previous chart, wrote a test asserting the owner's own case was
NOT a signal, and had to be corrected. The measurement decides, not me.

WHAT IS PINNED DOWN, SINCE A CHART CANNOT STATE IT
--------------------------------------------------
* **Structure** comes from ``src/market_structure.py`` - confirmed swings only, and a swing is not known
  until ``prd`` bars after it formed. That lag is enforced, which is what stops this backtesting a shape
  only visible in hindsight.
* **The sweep** is a bar trading beyond the most recently CONFIRMED swing in the trend's direction.
* **The POI** is the most recently confirmed swing on the OTHER side at the moment of the sweep - the
  high price rallies back to after sweeping the lows.
* **The window**: the POI stays live for ``max_wait`` bars after the sweep. Without that, a touch fifty
  bars later claims a setup nobody was watching.

Nothing here places an order. Whether it is worth trading is decided in ``poi_variants`` through the
Strategy Lab and recorded in ``.claude/memory/BASELINE.md``.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import List, Optional

import numpy as np

from .market_structure import DEFAULT_PRD, structure_events, structure_swings

DEFAULT_MAX_WAIT = 20


@dataclass
class Bar:
    """The minimum a structure reader needs. Kept local so this module does not drag in the executor."""

    ts: str
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass
class POISetup:
    """One completed setup: the sweep, the point of interest, and the bar that reached it."""

    sweep_index: int          # the bar that took the liquidity
    poi_index: int            # the bar that traded back into the point of interest
    direction: int            # +1 the lows were swept (bullish reading), -1 the highs were swept
    swept_level: float        # the swing level that was taken out
    swept_extreme: float      # how far beyond it price actually went
    poi_level: float          # the opposite swing price is returning to
    had_bos: bool             # had structure already broken this way before the sweep
    bars_to_poi: int


def bars_from(opens, highs, lows, closes, stamps=None) -> List[Bar]:
    n = len(closes)
    stamps = stamps if stamps is not None else range(n)
    return [Bar(ts=str(stamps[i]), open=float(opens[i]), high=float(highs[i]),
                low=float(lows[i]), close=float(closes[i])) for i in range(n)]


def poi_setups(bars: List[Bar], *, prd: int = DEFAULT_PRD, max_wait: int = DEFAULT_MAX_WAIT,
               require_bos: bool = True) -> List[POISetup]:
    """Every sweep-then-return-to-POI in the frame, using only information available at each bar."""
    n = len(bars)
    if n < prd * 4:
        return []
    swings = structure_swings(bars, prd)
    events = structure_events(bars, prd)
    # Which way structure had broken by each bar, from events that were themselves causal.
    broken = np.zeros(n, dtype=int)
    for event in events:
        direction = 1 if event.direction == "bullish" else -1
        broken[event.index:] = direction

    highs = [s for s in swings if s.kind == "high"]
    lows = [s for s in swings if s.kind == "low"]

    def latest(source, before: int):
        """The most recent swing CONFIRMED at or before `before`. None when there is not one yet."""
        found = None
        for swing in source:
            if swing.confirmed_at <= before:
                found = swing
            else:
                break
        return found

    setups: List[POISetup] = []
    used_levels: set = set()
    for i in range(prd * 2, n):
        bar = bars[i]
        # --- the lows are swept: the bullish reading's trigger --------------------------------------
        low_swing = latest(lows, i - 1)
        if low_swing is not None and bar.low < low_swing.price and (low_swing.index, 1) not in used_levels:
            if not require_bos or broken[i] == -1:
                poi = latest(highs, i - 1)
                if poi is not None and poi.price > bar.close:
                    for j in range(i + 1, min(i + 1 + max_wait, n)):
                        if bars[j].high >= poi.price:
                            setups.append(POISetup(sweep_index=i, poi_index=j, direction=1,
                                                   swept_level=low_swing.price, swept_extreme=bar.low,
                                                   poi_level=poi.price, had_bos=broken[i] == -1,
                                                   bars_to_poi=j - i))
                            break
                    used_levels.add((low_swing.index, 1))

        # --- the highs are swept: the mirror --------------------------------------------------------
        high_swing = latest(highs, i - 1)
        if high_swing is not None and bar.high > high_swing.price and (high_swing.index, -1) not in used_levels:
            if not require_bos or broken[i] == 1:
                poi = latest(lows, i - 1)
                if poi is not None and poi.price < bar.close:
                    for j in range(i + 1, min(i + 1 + max_wait, n)):
                        if bars[j].low <= poi.price:
                            setups.append(POISetup(sweep_index=i, poi_index=j, direction=-1,
                                                   swept_level=high_swing.price, swept_extreme=bar.high,
                                                   poi_level=poi.price, had_bos=broken[i] == 1,
                                                   bars_to_poi=j - i))
                            break
                    used_levels.add((high_swing.index, -1))
    setups.sort(key=lambda s: s.poi_index)
    return setups


def sweep_signals(bars: List[Bar], *, prd: int = DEFAULT_PRD, require_bos: bool = True) -> List[dict]:
    """Every liquidity sweep, knowable AT THE SWEEP BAR. The ride reading's entries.

    Separated from `poi_setups` after the first run of this module produced profit factors of 2.5-2.7 at
    1.6 % drawdown - numbers better than anything else in this project's record, which is a bug signature
    rather than a discovery. It was: `poi_setups` only records a setup once price has RETURNED to the POI,
    and the ride entry sits at the sweep, before that return. Truncating the data at the sweep bar made the
    signal disappear, which is the definition of reading the future.

    Here the POI is the TARGET, not a precondition. Whether price reaches it is the outcome being measured.
    """
    n = len(bars)
    if n < prd * 4:
        return []
    swings = structure_swings(bars, prd)
    events = structure_events(bars, prd)
    broken = np.zeros(n, dtype=int)
    for event in events:
        broken[event.index:] = 1 if event.direction == "bullish" else -1

    highs = [s for s in swings if s.kind == "high"]
    lows = [s for s in swings if s.kind == "low"]

    def latest(source, before: int):
        found = None
        for swing in source:
            if swing.confirmed_at <= before:
                found = swing
            else:
                break
        return found

    out: List[dict] = []
    used: set = set()
    for i in range(prd * 2, n):
        bar = bars[i]
        low_swing = latest(lows, i - 1)
        if low_swing is not None and bar.low < low_swing.price and (low_swing.index, 1) not in used:
            if not require_bos or broken[i] == -1:
                poi = latest(highs, i - 1)
                if poi is not None and poi.price > bar.close:
                    out.append({"index": i, "direction": 1, "anchor": bar.low, "poi": poi.price,
                                "swept_level": low_swing.price, "had_bos": broken[i] == -1})
                used.add((low_swing.index, 1))
        high_swing = latest(highs, i - 1)
        if high_swing is not None and bar.high > high_swing.price and (high_swing.index, -1) not in used:
            if not require_bos or broken[i] == 1:
                poi = latest(lows, i - 1)
                if poi is not None and poi.price < bar.close:
                    out.append({"index": i, "direction": -1, "anchor": bar.high, "poi": poi.price,
                                "swept_level": high_swing.price, "had_bos": broken[i] == 1})
                used.add((high_swing.index, -1))
    return out


def poi_arrays(bars: List[Bar], *, mode: str = "fade", prd: int = DEFAULT_PRD,
               max_wait: int = DEFAULT_MAX_WAIT, require_bos: bool = True) -> tuple:
    """``(side, entry_bar_reference, stop_anchor)`` per bar for the two readings.

    * ``fade`` signals on the bar that reaches the POI, against the move into it.
    * ``ride`` signals on the sweep bar itself, in the direction of the reversal, targeting the POI.
    """
    n = len(bars)
    side = np.zeros(n, dtype=int)
    anchor = np.full(n, np.nan)          # the price the stop is placed beyond
    objective = np.full(n, np.nan)       # the POI (ride) or the swept extreme (fade)
    if mode == "ride":
        # From the sweeps alone. Whether the POI is ever reached is the OUTCOME, not an entry condition -
        # requiring it here was the lookahead that made the first run of this module look extraordinary.
        for signal in sweep_signals(bars, prd=prd, require_bos=require_bos):
            index = signal["index"]
            side[index] = signal["direction"]
            anchor[index] = signal["anchor"]
            objective[index] = signal["poi"]
    else:
        for setup in poi_setups(bars, prd=prd, max_wait=max_wait, require_bos=require_bos):
            # At the POI the trade is AGAINST the leg that arrived there. This one IS causal: by the bar
            # that reaches the POI, the sweep and the level are both already history.
            index = setup.poi_index
            side[index] = -setup.direction
            anchor[index] = setup.poi_level
            objective[index] = setup.swept_extreme
    return side, anchor, objective


def poi_summary(bars: List[Bar], **kwargs) -> dict:
    setups = poi_setups(bars, **kwargs)
    bullish = [s for s in setups if s.direction == 1]
    return {"bars": len(bars), "setups": len(setups),
            "lows_swept": len(bullish), "highs_swept": len(setups) - len(bullish),
            "with_prior_bos": sum(1 for s in setups if s.had_bos),
            "mean_bars_to_poi": round(float(np.mean([s.bars_to_poi for s in setups])), 2) if setups else None,
            "places_orders": False}


# --------------------------------------------------------------------------- the backtest side
STOP_BUFFER_ATR = 0.25       # beyond the level, so the exact price is not the stop
REWARD_RATIOS = (1.0, 2.0, 3.0)
MODES = ("fade", "ride")
BOS_FILTERS = (True, False)
MAX_BARS = 60                # 30 hours on 30m: a setup that has not worked by then is not this trade


def poi_orders(ind, spec: dict):
    """side / stop / target for one variant. The signal bar is the POI touch (fade) or the sweep (ride)."""
    import numpy as _np

    p = spec["params"]
    mode = str(p.get("mode") or "fade")
    prd = int(p.get("prd") or DEFAULT_PRD)
    max_wait = int(p.get("max_wait") or DEFAULT_MAX_WAIT)
    require_bos = bool(p.get("require_bos", True))
    rr = float(p.get("rr") or 2.0)

    key = ("poi_liquidity", mode, prd, max_wait, require_bos)
    side, anchor, _objective = ind._cached(
        key, lambda: poi_arrays(bars_from(ind.o, ind.h, ind.l, ind.c), mode=mode, prd=prd,
                                max_wait=max_wait, require_bos=require_bos))
    side = side.copy()
    if p.get("inverse"):
        side = -side

    atr = ind.atr(14)
    buffer = STOP_BUFFER_ATR * atr
    with _np.errstate(invalid="ignore"):
        # The stop always sits beyond the ANCHOR - the POI for a fade, the swept extreme for a ride -
        # because that is the price which, if traded through, says the read was wrong.
        stop = _np.where(side == 1, anchor - buffer, _np.where(side == -1, anchor + buffer, _np.nan))
        risk = _np.abs(ind.c - stop)
        target = _np.where(side == 1, ind.c + rr * risk, _np.where(side == -1, ind.c - rr * risk, _np.nan))

    usable = (_np.isfinite(atr) & (atr > 0) & _np.isfinite(stop) & _np.isfinite(risk) & (risk > 0)
              & _np.isfinite(anchor))
    # A stop on the wrong side of the close is not a trade; it means the anchor was already breached.
    with _np.errstate(invalid="ignore"):
        usable = usable & _np.where(side == 1, stop < ind.c, _np.where(side == -1, stop > ind.c, False))
    side = _np.where(usable, side, 0)
    return side.astype(int), stop, target


def poi_variants(symbol: str, timeframe: str) -> list:
    """12 per market: 2 readings x 2 structure filters x 3 reward ratios. Declared before any result."""
    from itertools import product

    out = []
    for mode, require_bos, rr in product(MODES, BOS_FILTERS, REWARD_RATIOS):
        name = f"{mode}|{'bos' if require_bos else 'nobos'}|rr{rr:.0f}"
        out.append({
            "family": "poi_liquidity",
            "params": {"symbol": symbol, "timeframe": timeframe, "mode": mode,
                       "require_bos": require_bos, "rr": rr, "prd": DEFAULT_PRD,
                       "max_wait": DEFAULT_MAX_WAIT},
            "exits": {"stop": "fixed", "sl_atr": 0.0, "rr": 0.0, "trail_atr": 0.0,
                      "max_bars": MAX_BARS, "swing_lookback": 0},
            "description": (("sell into the point of interest after the lows were swept"
                             if mode == "fade" else
                             "buy the liquidity sweep and target the point of interest")
                            + (", structure must have broken that way" if require_bos else "")
                            + f", {rr:.0f}R"),
            "variant": name,
        })
    return out


def _register() -> None:
    """Registered late so importing this module for detection alone does not pull in the lab."""
    from . import strategy_lab as lab

    lab.ORDER_BUILDERS["poi_liquidity"] = poi_orders
    # Registered in the same place for the same reason: this module imports the lab lazily, so a
    # module-level registration would raise NameError on import.
    lab.NEIGHBOUR_GRIDS["poi_liquidity"] = neighbour_grid


def neighbour_grid(spec: dict) -> dict:
    """Settings only. `mode` is deliberately excluded: fade and ride are two different rules, so
    stepping it would score a ride candidate against a fade strategy."""
    return {"require_bos": list(BOS_FILTERS), "rr": list(REWARD_RATIOS)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Find sweep -> POI setups in broker bars. Reads only.")
    parser.add_argument("symbol", nargs="?", default="XAUUSD")
    parser.add_argument("timeframe", nargs="?", default="30m")
    parser.add_argument("--prd", type=int, default=DEFAULT_PRD)
    parser.add_argument("--max-wait", type=int, default=DEFAULT_MAX_WAIT)
    parser.add_argument("--no-bos", action="store_true", help="do not require structure to have broken")
    args = parser.parse_args(argv)

    from .mtf_data import load_bars

    frame = load_bars(args.symbol, args.timeframe, source="app")
    if frame is None or frame.empty:
        print(f"no broker bars for {args.symbol} {args.timeframe}")
        return 1
    frame = frame.sort_values("datetime").reset_index(drop=True)
    bars = bars_from(frame["open"], frame["high"], frame["low"], frame["close"], frame["datetime"])
    out = poi_summary(bars, prd=args.prd, max_wait=args.max_wait, require_bos=not args.no_bos)
    print(f"{args.symbol} {args.timeframe}: {out['bars']:,} bars -> {out['setups']} setups "
          f"({out['lows_swept']} after a low sweep, {out['highs_swept']} after a high sweep)")
    print(f"  {out['with_prior_bos']} had structure already broken that way; "
          f"POI reached {out['mean_bars_to_poi']} bars after the sweep on average")
    for setup in poi_setups(bars, prd=args.prd, max_wait=args.max_wait, require_bos=not args.no_bos)[-5:]:
        when = str(frame.loc[setup.poi_index, "datetime"])[:16]
        which = "lows swept" if setup.direction == 1 else "highs swept"
        print(f"    {when}  {which}: took {setup.swept_level:.2f} to {setup.swept_extreme:.2f}, "
              f"POI {setup.poi_level:.2f} reached {setup.bars_to_poi} bars later")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
