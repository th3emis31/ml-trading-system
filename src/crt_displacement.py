"""The 4H CRT as an institutional market-structure rule: sweep -> displacement -> body close. Research only.

Declared 20 September 2026 BEFORE any result, from the owner's written specification. This is NOT
``src/crt_lab.py`` (the MT4 CRT_Dashboard_EA replica, anchor/middle/breakout on M15) and NOT
``src/crt_htf_lab.py`` (classic range CRT on higher timeframes, which lost on every market and
timeframe). The difference that makes it worth testing is the pair of conditions neither of those
carried: a DISPLACEMENT test on the breaking candle, and an explicit separation of a body close from
a wick-only break.

THE SPECIFICATION, as given:

1. Liquidity sweep - price must have swept a major previous swing high/low before the reversal.
2. Displacement - the break past the opposing structural swing must be driven by an aggressive,
   large-bodied expansion candle: body at least ``displacement_factor`` times the size of the
   previous three candles.
3. Body close - the 4H candle must close its BODY cleanly past the structural break point. A
   wick-only cross is a Liquidity Purge (fakeout), not a CRT.

HOW IT IS TESTED. Each of the three conditions is switchable, so the result can say which one carries
the edge rather than only whether the bundle works. In particular ``mode="wick"`` deliberately trades
the case the specification says to REJECT: if the specification is right, the body variants must beat
the wick variants. A bundle that works only when all three are on, with each one individually adding
nothing, is a different and weaker claim than a rule whose parts each pull their weight.

Swing points are fractal pivots: a swing high is a bar whose high exceeds the ``swing_lookback`` highs
on each side, confirmed only once those later bars have closed, so nothing is known before it could be.
The structural level is the most recent CONFIRMED opposing swing at the time of the break.

    python -m src.crt_displacement run [--symbols XAUUSD BTCUSD] [--timeframes 4h]
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from itertools import product

import numpy as np
import pandas as pd

from . import strategy_lab as lab

SWING_LOOKBACK = 3          # bars each side of a fractal pivot
SWEEP_WINDOW = 20           # the sweep must have happened within this many bars before the break
DISPLACEMENT_FACTOR = 1.5   # the specification's figure
DISPLACEMENT_BASE = 3       # "the previous 3 candles"
MODES = ("body", "wick")
REWARD_RATIOS = (2.0, 3.0)
MAX_BARS = 30
STOP_BUFFER_ATR = 0.05


def _confirmed_swings(values: np.ndarray, lookback: int, high: bool) -> np.ndarray:
    """For each bar, the most recent swing level CONFIRMED by then (NaN until one exists).

    A pivot at ``i`` needs ``lookback`` bars each side, so it is only knowable at ``i + lookback``.
    Assigning it any earlier would let the rule see the future.
    """
    n = len(values)
    level = np.full(n, np.nan)
    current = np.nan
    for i in range(n):
        pivot = i - lookback
        if pivot - lookback >= 0:
            window = values[pivot - lookback: pivot + lookback + 1]
            centre = values[pivot]
            if high and centre >= window.max():
                current = centre
            elif not high and centre <= window.min():
                current = centre
        level[i] = current
    return level


def displacement_orders(ind: lab.Indicators, spec: dict):
    """side / stop / target per bar. The signal bar is the displacement candle; entry is the next open."""
    p = spec["params"]
    lookback = int(p.get("swing_lookback", SWING_LOOKBACK))
    sweep_window = int(p.get("sweep_window", SWEEP_WINDOW))
    factor = float(p.get("displacement_factor", DISPLACEMENT_FACTOR))
    rr = float(p.get("rr", 2.0))
    mode = str(p.get("mode", "body"))
    require_displacement = bool(p.get("require_displacement", True))
    require_sweep = bool(p.get("require_sweep", True))

    n = len(ind.c)
    atr = ind.atr(14)
    o, h, l, c = ind.o, ind.h, ind.l, ind.c

    swing_high = _confirmed_swings(h, lookback, high=True)
    swing_low = _confirmed_swings(l, lookback, high=False)
    # The level in force when the current bar forms is the one confirmed by the PREVIOUS bar.
    prior_high = np.concatenate([[np.nan], swing_high[:-1]])
    prior_low = np.concatenate([[np.nan], swing_low[:-1]])

    body = np.abs(c - o)
    # Mean body of the previous DISPLACEMENT_BASE candles, excluding the breaking candle itself.
    base = pd.Series(body).shift(1).rolling(DISPLACEMENT_BASE).mean().to_numpy()
    with np.errstate(invalid="ignore"):
        displaced = body >= factor * base
    if not require_displacement:
        displaced = np.ones(n, dtype=bool)

    with np.errstate(invalid="ignore"):
        if mode == "wick":
            # The case the specification says to reject: the wick crosses but the body does not.
            broke_up = (h > prior_high) & (c <= prior_high) & np.isfinite(prior_high)
            broke_down = (l < prior_low) & (c >= prior_low) & np.isfinite(prior_low)
        else:
            broke_up = (c > prior_high) & np.isfinite(prior_high)
            broke_down = (c < prior_low) & np.isfinite(prior_low)
        bullish_body, bearish_body = c > o, c < o

    # Origin sweep: within the preceding window, some bar took out the swing level on the far side and
    # price came back. For a bullish break the sweep is of an old LOW, and vice versa.
    # "some bar in the window took the level out" is a rolling OR, which is what this is. It was
    # written as a Python double loop over every bar and every bar in its window, which cost 1.3 s
    # per candidate on 4H gold and 3.6 s on hourly - against about 0.36 s for the indicator
    # families - and that is the whole reason this mechanism could not be afforded in the hourly
    # search. The window is the same: sweep_window bars back plus the bar itself, with min_periods
    # covering the short window at the start of the series exactly as max(0, i - sweep_window) did.
    with np.errstate(invalid="ignore"):
        took_low = np.isfinite(prior_low) & (l < prior_low)
        took_high = np.isfinite(prior_high) & (h > prior_high)
    window = sweep_window + 1
    swept_low = (pd.Series(took_low, dtype=float).rolling(window, min_periods=1).max()
                 .to_numpy() > 0)
    swept_high = (pd.Series(took_high, dtype=float).rolling(window, min_periods=1).max()
                  .to_numpy() > 0)
    if not require_sweep:
        swept_low = np.ones(n, dtype=bool)
        swept_high = np.ones(n, dtype=bool)

    long = broke_up & displaced & swept_low & bullish_body
    short = broke_down & displaced & swept_high & bearish_body
    both = long & short
    long, short = long & ~both, short & ~both

    side = np.zeros(n, dtype=int)
    stop = np.full(n, np.nan)
    target = np.full(n, np.nan)
    buffer = STOP_BUFFER_ATR * atr
    for index in np.flatnonzero(long | short):
        if not np.isfinite(atr[index]) or atr[index] <= 0:
            continue
        entry_ref = c[index]
        if long[index]:
            # risk sits below the displacement candle's own low: the origin of the move
            stop_price = l[index] - buffer[index]
            risk = entry_ref - stop_price
            if risk <= 0:
                continue
            side[index], stop[index], target[index] = 1, stop_price, entry_ref + rr * risk
        else:
            stop_price = h[index] + buffer[index]
            risk = stop_price - entry_ref
            if risk <= 0:
                continue
            side[index], stop[index], target[index] = -1, stop_price, entry_ref - rr * risk

    if p.get("inverse"):
        side = -side
        for index in np.flatnonzero(side):
            mid = c[index]
            stop[index], target[index] = 2 * mid - stop[index], 2 * mid - target[index]
    return side, stop, target


lab.ORDER_BUILDERS["crt_displacement"] = displacement_orders


def displacement_variants(symbol: str, timeframe: str) -> list:
    """16 per market: 2 modes x 2 displacement x 2 sweep x 2 reward ratios, every one counted.

    The controls exist to attribute the result. body-vs-wick tests the specification's central claim;
    turning displacement or the sweep off says whether that condition earns its place.
    """
    out = []
    for mode, disp, sweep, rr in product(MODES, (True, False), (True, False), REWARD_RATIOS):
        name = (f"{mode}|{'disp' if disp else 'nodisp'}|{'sweep' if sweep else 'nosweep'}|rr{rr:.0f}")
        out.append({
            "family": "crt_displacement",
            "params": {"symbol": symbol, "timeframe": timeframe, "mode": mode,
                       "require_displacement": disp, "require_sweep": sweep, "rr": rr,
                       "swing_lookback": SWING_LOOKBACK, "sweep_window": SWEEP_WINDOW,
                       "displacement_factor": DISPLACEMENT_FACTOR},
            "exits": {"stop": "fixed", "sl_atr": 0.0, "rr": 0.0, "trail_atr": 0.0,
                      "max_bars": MAX_BARS, "swing_lookback": 0},
            "description": (f"4H CRT: {'body close' if mode == 'body' else 'WICK-ONLY cross (the rejected case)'} "
                            f"past the last confirmed opposing swing"
                            + (f", body >= {DISPLACEMENT_FACTOR}x the previous {DISPLACEMENT_BASE}" if disp else "")
                            + (", after a liquidity sweep" if sweep else "") + f", {rr:.0f}R"),
            "variant": name,
        })
    return out


def run(symbols=("XAUUSD", "BTCUSD"), timeframes=("4h",)) -> dict:
    from .mtf_data import load_bars

    registry = lab.load_registry()
    n_trials = len(MODES) * 2 * 2 * len(REWARD_RATIOS) * len(symbols) * len(timeframes)
    report = {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
              "rule": "4H CRT: liquidity sweep, displacement candle, body close past the opposing swing",
              "n_trials_total": n_trials, "places_orders": False, "markets": {}}
    for symbol, timeframe in product(symbols, timeframes):
        key = f"{symbol}:{timeframe}"
        bars = load_bars(symbol, timeframe, source="app")
        if bars is None or bars.empty:
            report["markets"][key] = {"error": "no broker bars"}
            continue
        market = lab.Market(symbol, timeframe, bars,
                            boundaries=(registry["markets"].get(key) or {}).get("boundaries"), swap=True)
        records = []
        for spec in displacement_variants(symbol, timeframe):
            record = lab.evaluate_candidate(market, spec, with_holdout=True)
            record["variant"] = spec["variant"]
            record["signals"] = int(np.count_nonzero(lab.strategy_orders(market.ind, spec)[0]))
            inverse = market.summary("holdout", market.simulate(
                {**spec, "params": {**spec["params"], "inverse": True}}, "holdout"))
            record["inverse_holdout"] = {"trades": inverse.get("trades"),
                                         "profit_factor": inverse.get("profit_factor"),
                                         "net_pct": inverse.get("total_return_pct")}
            record["splits_all_positive"] = all(
                ((record.get(split) or {}).get("total_return_pct") or -1) > 0
                for split in ("search", "validation", "holdout"))
            record["beats_inverse"] = bool((record.get("holdout") or {}).get("total_return_pct", -999)
                                           > (record["inverse_holdout"].get("net_pct") or -999))
            records.append(record)
        sr_variance = lab._variance([[r["validation_sr"], r["validation"]["trades"]]
                                     for r in records if r["validation_sr"] is not None])
        for record in records:
            record["holdout_verdict"] = lab.holdout_verdict(record, n_trials, sr_variance)
        report["markets"][key] = {
            "info": market.info, "variants": records,
            "passed_holdout": [r["variant"] for r in records
                               if (r.get("holdout_verdict") or {}).get("passed")],
            "positive_on_all_splits": [r["variant"] for r in records if r["splits_all_positive"]],
            "survivors": [r["variant"] for r in records if r["splits_all_positive"] and r["beats_inverse"]]}
    lab.LAB_DIR.mkdir(parents=True, exist_ok=True)
    scope = "-".join(sorted(symbols)) + "_" + "-".join(timeframes)
    path = lab.LAB_DIR / f"crt_displacement_{scope}_{datetime.now(timezone.utc):%Y%m%d}.json"
    path.write_text(json.dumps(report, indent=1, default=lambda v: v.item() if hasattr(v, "item") else str(v)),
                    encoding="utf-8")
    report["path"] = str(path)
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="4H CRT: sweep, displacement, body close (research only)")
    parser.add_argument("command", choices=["run"])
    parser.add_argument("--symbols", nargs="+", default=["XAUUSD", "BTCUSD"])
    parser.add_argument("--timeframes", nargs="+", default=["4h"])
    args = parser.parse_args(argv)

    report = run(tuple(args.symbols), tuple(args.timeframes))
    print(f"saved {report['path']}   ({report['n_trials_total']} trials counted)")
    for key, market in report["markets"].items():
        if market.get("error"):
            print(f"\n{key}: {market['error']}")
            continue
        print(f"\n=== {key}  {market['info'].get('bars')} bars  {market['info'].get('data_start')}"
              f" to {market['info'].get('data_end')}")
        for r in sorted(market["variants"], key=lambda x: -((x.get("holdout") or {}).get("profit_factor") or 0)):
            hold, inv = r.get("holdout") or {}, r.get("inverse_holdout") or {}
            flags = ("all3 " if r["splits_all_positive"] else "     ") + ("inv" if r["beats_inverse"] else "   ")
            print(f"  {r['variant']:28s} sig {r['signals']:4d} n {hold.get('trades', 0):4d} "
                  f"PF {hold.get('profit_factor') or 0:5.3f} net {hold.get('total_return_pct') or 0:+8.2f}% "
                  f"expR {str(hold.get('expectancy_r')):>8s} | inv PF {inv.get('profit_factor') or 0:5.3f} | {flags}")
        print(f"  positive on all three splits: {market['positive_on_all_splits'] or 'none'}")
        print(f"  and also beating the inverse: {market['survivors'] or 'none'}")
        print(f"  passed the holdout bar:       {market['passed_holdout'] or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
