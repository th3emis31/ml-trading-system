"""The owner's SmartEntry 4H architecture, tested one combination at a time. Research only.

    python -m src.smart_entry_arch run [--symbols XAUUSD BTCUSD]

THE ARCHITECTURE, FROM THE OWNER'S CARD
---------------------------------------
Bullish:  liquidity sweep -> CISD -> POC reclaim -> displacement -> FVG retest
Bearish:  liquidity sweep -> CISD -> POC rejection -> displacement -> FVG retest

The card also says how to test it, and that instruction is followed literally:

    BACKTEST COMBINATIONS - test each combination separately:
      1. POC + Volume
      2. POC + Liquidity Sweep
      3. POC + CISD
      4. POC + CISD + FVG
      5. POC + CISD + FVG + Volume (full model)
    LET THE DATA DECIDE.

That is the right shape for an experiment, and it is why this module exists rather than one more
all-in strategy: each stage either earns its place or it does not, and a full model that beats nothing
is a full model nobody should trade.

WHAT EACH STAGE MEANS HERE
--------------------------
* **POC** - ``src/volume_profile.py``, a rolling 120-bar profile. A *reclaim* is a close crossing from
  below the POC to above it; a *rejection* is the mirror. Every combination contains it, because the
  card puts it at the centre of all five.
* **Liquidity sweep** - a bar trading beyond a confirmed swing low (or high) within ``window`` bars,
  reusing the confirmed-swing machinery from ``src/market_structure.py``.
* **CISD** - ``src/cisd.py``, the owner's own earlier card: a close beyond the open of a run of
  opposite-closing candles that swept a level.
* **FVG** - a real three-candle imbalance: bar i's low above bar i-2's high leaves a gap that bar i-1
  displaced through. ``features.detect_fvg`` is NOT reused - it tests for consecutive rising closes,
  which is a different thing wearing the same name.
* **Volume** - the signal bar trading above ``vol_mult`` times the average of the previous 20 bars,
  which is the card's "higher volume = stronger confirmation".

STOPS AND TARGETS, WHICH THE CARD DOES SPECIFY
----------------------------------------------
"SL: structural invalidation + ATR(14)" and "TP1: 1R | TP2: 2R | TP3: 3R+". So the stop goes beyond the
swing that would invalidate the read, plus half an ATR, and the reward ratio is a declared parameter
rather than something chosen after seeing results.

ONE HONEST LIMIT, STATED UP FRONT
---------------------------------
The card says "use real volume (BTC) or futures volume (Gold)". MetaTrader gives TICK volume for gold -
the number of price changes, not contracts traded. Every gold figure here rests on that proxy, and it is
the reason bitcoin and gold results are reported separately rather than pooled.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from itertools import product

import numpy as np

from . import strategy_lab as lab
from .cisd import cisd_arrays
from .market_structure import structure_swings
from .volume_profile import rolling_levels

PROFILE_LOOKBACK = 120
PROFILE_BINS = 50
SWEEP_WINDOW = 10            # bars a sweep or a CISD stays relevant for
VOLUME_LOOKBACK = 20
VOLUME_MULTIPLE = 1.2
DISPLACEMENT_FACTOR = 1.5    # the card's own figure, against the previous three candles
DISPLACEMENT_BASE = 3
STOP_ATR = 0.5               # "structural invalidation + ATR"
MAX_BARS = 48
REWARD_RATIOS = (1.0, 2.0, 3.0)

# The card's five, in its order. Each is a set of stages switched on; POC is in all of them.
COMBINATIONS = {
    "1_poc_volume": {"sweep": False, "cisd": False, "fvg": False, "volume": True, "displacement": False},
    "2_poc_sweep": {"sweep": True, "cisd": False, "fvg": False, "volume": False, "displacement": False},
    "3_poc_cisd": {"sweep": False, "cisd": True, "fvg": False, "volume": False, "displacement": False},
    "4_poc_cisd_fvg": {"sweep": False, "cisd": True, "fvg": True, "volume": False, "displacement": False},
    "5_full_model": {"sweep": True, "cisd": True, "fvg": True, "volume": True, "displacement": True},
}


def fvg_flags(highs, lows) -> tuple:
    """``(bullish, bearish)`` boolean arrays: a three-candle imbalance completed at this bar.

    Bullish: bar i's LOW is above bar i-2's HIGH, so nothing traded between them and bar i-1 displaced
    through the gap. Bearish is the mirror. Known at bar i's close, so it is causal.
    """
    high = np.asarray(highs, dtype=float)
    low = np.asarray(lows, dtype=float)
    n = len(high)
    bullish = np.zeros(n, dtype=bool)
    bearish = np.zeros(n, dtype=bool)
    if n < 3:
        return bullish, bearish
    bullish[2:] = low[2:] > high[:-2]
    bearish[2:] = high[2:] < low[:-2]
    return bullish, bearish


def displacement_flags(opens, highs, lows, closes, factor: float = DISPLACEMENT_FACTOR,
                       base: int = DISPLACEMENT_BASE) -> tuple:
    """Strong directional candles: a body larger than ``factor`` times the mean of the previous ``base``."""
    o = np.asarray(opens, dtype=float)
    c = np.asarray(closes, dtype=float)
    body = np.abs(c - o)
    n = len(c)
    mean_body = np.full(n, np.nan)
    for i in range(base, n):
        mean_body[i] = body[i - base:i].mean()
    with np.errstate(invalid="ignore"):
        strong = np.isfinite(mean_body) & (mean_body > 0) & (body > factor * mean_body)
    return strong & (c > o), strong & (c < o)


def _recent(flags: np.ndarray, window: int) -> np.ndarray:
    """True where ``flags`` was set on this bar or within the previous ``window`` bars."""
    out = np.zeros(len(flags), dtype=bool)
    hits = np.flatnonzero(flags)
    for index in hits:
        out[index:min(index + window + 1, len(flags))] = True
    return out


def _swept_levels(ind, window: int) -> tuple:
    """``(low_swept, high_swept)``: a bar traded beyond a CONFIRMED swing within the window."""
    from .poi_liquidity import bars_from

    bars = bars_from(ind.o, ind.h, ind.l, ind.c)
    swings = structure_swings(bars, 5)
    n = len(ind.c)
    low_swept = np.zeros(n, dtype=bool)
    high_swept = np.zeros(n, dtype=bool)
    lows = [s for s in swings if s.kind == "low"]
    highs = [s for s in swings if s.kind == "high"]
    for source, out, compare in ((lows, low_swept, "below"), (highs, high_swept, "above")):
        for swing in source:
            start = swing.confirmed_at
            stop = min(start + window * 4, n)
            for i in range(start, stop):
                if compare == "below" and ind.l[i] < swing.price:
                    out[i] = True
                    break
                if compare == "above" and ind.h[i] > swing.price:
                    out[i] = True
                    break
    return _recent(low_swept, window), _recent(high_swept, window)


def arch_orders(ind: lab.Indicators, spec: dict):
    """side / stop / target for one combination of the architecture."""
    p = spec["params"]
    stages = COMBINATIONS[str(p["combination"])]
    rr = float(p.get("rr") or 2.0)
    window = int(p.get("window") or SWEEP_WINDOW)
    n = len(ind.c)

    volumes = ind.df["volume"].to_numpy(dtype=float) if "volume" in ind.df.columns else np.zeros(n)
    poc, _vah, _val = ind._cached(("poc", PROFILE_LOOKBACK, PROFILE_BINS),
                                  lambda: rolling_levels(ind.h, ind.l, volumes,
                                                         lookback=PROFILE_LOOKBACK, bins=PROFILE_BINS))

    # --- the core, present in every combination: a close crossing the POC ---------------------------
    previous_close = np.concatenate(([np.nan], ind.c[:-1]))
    previous_poc = np.concatenate(([np.nan], poc[:-1]))
    with np.errstate(invalid="ignore"):
        reclaim = (previous_close <= previous_poc) & (ind.c > poc)
        reject = (previous_close >= previous_poc) & (ind.c < poc)
    long_ok = reclaim & np.isfinite(poc)
    short_ok = reject & np.isfinite(poc)

    if stages["sweep"]:
        low_swept, high_swept = ind._cached(("arch_sweeps", window), lambda: _swept_levels(ind, window))
        long_ok = long_ok & low_swept
        short_ok = short_ok & high_swept

    if stages["cisd"]:
        cisd_side, _level, _extreme = ind._cached(
            ("arch_cisd",), lambda: cisd_arrays(ind.o, ind.h, ind.l, ind.c, min_run=1,
                                                sweep_lookback=10, max_wait=5))
        long_ok = long_ok & _recent(cisd_side == 1, window)
        short_ok = short_ok & _recent(cisd_side == -1, window)

    if stages["fvg"]:
        bullish_fvg, bearish_fvg = ind._cached(("arch_fvg",), lambda: fvg_flags(ind.h, ind.l))
        long_ok = long_ok & _recent(bullish_fvg, window)
        short_ok = short_ok & _recent(bearish_fvg, window)

    if stages["displacement"]:
        up_push, down_push = ind._cached(("arch_disp",),
                                         lambda: displacement_flags(ind.o, ind.h, ind.l, ind.c))
        long_ok = long_ok & _recent(up_push, window)
        short_ok = short_ok & _recent(down_push, window)

    if stages["volume"]:
        mean_volume = np.full(n, np.nan)
        for i in range(VOLUME_LOOKBACK, n):
            mean_volume[i] = volumes[i - VOLUME_LOOKBACK:i].mean()
        with np.errstate(invalid="ignore"):
            loud = np.isfinite(mean_volume) & (mean_volume > 0) & (volumes > VOLUME_MULTIPLE * mean_volume)
        long_ok = long_ok & loud
        short_ok = short_ok & loud

    side = np.where(long_ok, 1, np.where(short_ok, -1, 0)).astype(int)
    if p.get("inverse"):
        side = -side

    # --- stop at the structural invalidation, plus ATR, exactly as the card specifies ---------------
    atr = ind.atr(14)
    swing_low = ind.lowest(window, shift=1) if hasattr(ind, "lowest") else None
    swing_high = ind.highest(window, shift=1) if hasattr(ind, "highest") else None
    with np.errstate(invalid="ignore"):
        stop = np.where(side == 1, swing_low - STOP_ATR * atr,
                        np.where(side == -1, swing_high + STOP_ATR * atr, np.nan))
        risk = np.abs(ind.c - stop)
        target = np.where(side == 1, ind.c + rr * risk, np.where(side == -1, ind.c - rr * risk, np.nan))
        usable = (np.isfinite(atr) & (atr > 0) & np.isfinite(stop) & np.isfinite(risk) & (risk > 0)
                  & np.where(side == 1, stop < ind.c, np.where(side == -1, stop > ind.c, False)))
    side = np.where(usable, side, 0)
    return side.astype(int), stop, target


lab.ORDER_BUILDERS["smart_entry_arch"] = arch_orders


def arch_variants(symbol: str, timeframe: str) -> list:
    """15 per market: the card's 5 combinations x 3 reward ratios. Declared before any result."""
    out = []
    for combination, rr in product(COMBINATIONS, REWARD_RATIOS):
        out.append({
            "family": "smart_entry_arch",
            "params": {"symbol": symbol, "timeframe": timeframe, "combination": combination,
                       "rr": rr, "window": SWEEP_WINDOW},
            "exits": {"stop": "fixed", "sl_atr": 0.0, "rr": 0.0, "trail_atr": 0.0,
                      "max_bars": MAX_BARS, "swing_lookback": 0},
            "description": f"{combination.replace('_', ' ')} at {rr:.0f}R",
            "variant": f"{combination}|rr{rr:.0f}",
        })
    return out


def run(symbols=("XAUUSD", "BTCUSD"), timeframes=("4h",)) -> dict:
    from .mtf_data import load_bars

    registry = lab.load_registry()
    n_trials = len(COMBINATIONS) * len(REWARD_RATIOS) * len(symbols) * len(timeframes)
    report = {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
              "n_trials_total": n_trials,
              "note": ("each of the card's five combinations tested separately; gold volume is MetaTrader "
                       "TICK volume, a proxy for the futures volume the card asks for"),
              "markets": {}}
    for symbol, timeframe in product(symbols, timeframes):
        key = f"{symbol}:{timeframe}"
        bars = load_bars(symbol, timeframe, source="app")
        if bars is None or bars.empty:
            report["markets"][key] = {"error": "no broker bars"}
            continue
        market = lab.Market(symbol, timeframe, bars,
                            boundaries=(registry["markets"].get(key) or {}).get("boundaries"), swap=True)
        records = []
        for spec in arch_variants(symbol, timeframe):
            record = lab.evaluate_candidate(market, spec, with_holdout=True)
            record["variant"] = spec["variant"]
            record["signals"] = int(np.count_nonzero(lab.strategy_orders(market.ind, spec)[0]))
            inverse = {**spec, "params": {**spec["params"], "inverse": True}}
            inv = market.summary("holdout", market.simulate(inverse, "holdout"))
            record["inverse_holdout"] = {"trades": inv.get("trades"), "net_pct": inv.get("total_return_pct")}
            record["splits_all_positive"] = all(
                (record.get(s) or {}).get("total_return_pct") is not None
                and record[s]["total_return_pct"] > 0 for s in ("search", "validation", "holdout"))
            records.append(record)
        sr_variance = lab._variance([[r["validation_sr"], r["validation"]["trades"]]
                                     for r in records if r["validation_sr"] is not None])
        for record in records:
            record["holdout_verdict"] = lab.holdout_verdict(record, n_trials, sr_variance)
        report["markets"][key] = {
            "info": market.info, "variants": records,
            "passed_holdout": [r["variant"] for r in records
                               if (r.get("holdout_verdict") or {}).get("passed")],
            "positive_on_all_splits": [r["variant"] for r in records if r["splits_all_positive"]]}
    lab.LAB_DIR.mkdir(parents=True, exist_ok=True)
    path = lab.LAB_DIR / f"smart_entry_arch_{datetime.now(timezone.utc):%Y%m%d}.json"
    path.write_text(json.dumps(report, indent=1, default=lambda v: v.item() if hasattr(v, "item") else str(v)),
                    encoding="utf-8")
    report["path"] = str(path)
    return report


def print_arch_report(report: dict) -> None:
    """Sorted by COMBINATION, not by return: the card asks whether each stage adds
    anything, and ranking by profit would hide exactly that."""
    lab.print_variant_table(report, title="SmartEntry architecture - the card's 5 combinations",
                            sort="variant", show_signals=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Backtest the SmartEntry architecture. Never trades.")
    sub = parser.add_subparsers(dest="command", required=True)
    runner = sub.add_parser("run", help="run the card's five combinations")
    runner.add_argument("--symbols", nargs="+", default=["XAUUSD", "BTCUSD"])
    runner.add_argument("--timeframes", nargs="+", default=["4h"])
    args = parser.parse_args(argv)

    report = run(tuple(args.symbols), tuple(args.timeframes))
    print_arch_report(report)
    print(f"\nwritten to {report['path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
