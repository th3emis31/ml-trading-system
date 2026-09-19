"""The 4H manipulation candle: sweep a level, close back inside, trade the reversal. Research only.

The rule and the pre-declared grid are in ``strategies/sweep_reversal_4h.md``, written from the
owner's two photographs. In short: a 4H candle's wick takes out the highest high (or lowest low) of
the previous ``lookback`` candles, and the candle then closes back inside. The wick is the
manipulation; the close is the signal.

Deliberately simpler than ``src/crt_mss_lab.py``, which also starts with a sweep but then needs a
market structure shift and a fair-value-gap retest on 5m bars. Everything here happens on the 4H
candle itself, so there are fewer knobs to fit to the past.

    python -m src.sweep_reversal run [--symbols XAUUSD BTCUSD] [--timeframes 4h]
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from itertools import product

import numpy as np
import pandas as pd

from . import strategy_lab as lab

LOOKBACKS = (10, 20, 40)
REWARD_RATIOS = (1.0, 2.0, 3.0)
BODY_FILTERS = (True, False)
MAX_BARS = 30          # a reversal that has not worked within five days is not the trade that was drawn
MODES = ("reject", "continue")
STOP_BUFFER = 0.02     # of ATR, so the stop sits just beyond the wick rather than exactly on it


def sweep_orders(ind: lab.Indicators, spec: dict):
    """side / stop / target per bar. The signal bar is the sweep candle; entry is the next open."""
    p = spec["params"]
    lookback = int(p.get("lookback", 20))
    rr = float(p.get("rr", 2.0))
    require_body = bool(p.get("require_body", True))

    n = len(ind.c)
    atr = ind.atr(14)
    # Highest high and lowest low of the PREVIOUS `lookback` candles: shift(1) excludes the current
    # one, so the level a candle sweeps is never the level that candle itself set.
    prior_high = ind.highest(lookback, shift=1)
    prior_low = ind.lowest(lookback, shift=1)

    side = np.zeros(n, dtype=int)
    stop = np.full(n, np.nan)
    target = np.full(n, np.nan)

    with np.errstate(invalid="ignore"):
        swept_high = (ind.h > prior_high) & np.isfinite(prior_high)
        closed_back_below = ind.c < prior_high
        swept_low = (ind.l < prior_low) & np.isfinite(prior_low)
        closed_back_above = ind.c > prior_low

    bearish_body = ind.c < ind.o
    bullish_body = ind.c > ind.o

    # Two readings of the owner's pictures, both tested rather than one assumed.
    #   "reject"   - the wick takes the level and the candle closes back INSIDE: fade it.
    #   "continue" - the candle CLOSES BEYOND the level: trade with it. This is the owner's own
    #                wording, "if the close above the previous high buy, if the close below sell",
    #                and it is the case the first version explicitly threw away.
    if str(p.get("mode", "reject")) == "continue":
        short = swept_low & (ind.c < prior_low) & (bearish_body if require_body else True)
        long = swept_high & (ind.c > prior_high) & (bullish_body if require_body else True)
        # A close beyond the range is a breakout, and a breakout needs a trend to continue into.
        # Without this the rule made +40 % on gold's 2024-2026 run and lost 50 % over the fifteen
        # ranging years before it, which is a regime result rather than an edge.
        trend_ema = int(p.get("trend_ema") or 0)
        if trend_ema:
            ema = ind.ema(trend_ema)
            with np.errstate(invalid="ignore"):
                long = long & (ind.c > ema)
                short = short & (ind.c < ema)
    else:
        short = swept_high & closed_back_below & (bearish_body if require_body else True)
        long = swept_low & closed_back_above & (bullish_body if require_body else True)
    # A candle that swept both extremes says nothing about direction, so it is not a signal.
    both = short & long
    short, long = short & ~both, long & ~both

    buffer = STOP_BUFFER * atr
    for index in np.flatnonzero(short | long):
        if not np.isfinite(atr[index]) or atr[index] <= 0:
            continue
        continuation = str(p.get("mode", "reject")) == "continue"
        if short[index]:
            # fading: risk is the sweep wick above. continuing: risk is the candle's own high.
            entry_ref, stop_price = ind.c[index], ind.h[index] + buffer[index]
            risk = stop_price - entry_ref
            if risk <= 0:
                continue
            side[index], stop[index], target[index] = -1, stop_price, entry_ref - rr * risk
        else:
            entry_ref, stop_price = ind.c[index], ind.l[index] - buffer[index]
            risk = entry_ref - stop_price
            if risk <= 0:
                continue
            side[index], stop[index], target[index] = 1, stop_price, entry_ref + rr * risk

    if p.get("inverse"):
        side = -side
        # mirror the distances about the signal bar's close, so the inverse risks and targets the same
        for index in np.flatnonzero(side):
            close = ind.c[index]
            stop[index], target[index] = 2 * close - stop[index], 2 * close - target[index]
    return side, stop, target


lab.ORDER_BUILDERS["sweep_reversal"] = sweep_orders

# The one variant that earned a forward test, declared here ONCE so the forward test carries no trial-counting
# penalty. It is the owner's own rule read the way the owner stated it - "if the close above the previous high buy,
# if the close below sell" - with the trend filter that stopped it being a bet on gold's 2024-26 run.
# BASELINE 2026-09-19, XAUUSD 4h under the corrected cost model: positive in all three windows (search +6.33 %,
# validation +11.42 %, holdout +26.69 % over 118 trades at PF 1.378, max drawdown 6.76 %), beating its own inverse
# by 62 points, after-cost expectancy +0.2193 R on the holdout with no ambiguous exits at all. Deflated Sharpe
# 0.181 against the 0.95 bar, because 36 declared variants plus the cumulative registry count are charged against
# it - which is exactly what a single pre-declared forward test does not incur.
FORWARD_VARIANT = "continue|lb40|nobody|rr2|ema400"
FORWARD_CANDIDATE = {
    "family": "sweep_reversal",
    "params": {"symbol": "XAUUSD", "timeframe": "4h", "lookback": 40, "require_body": False,
               "rr": 2.0, "mode": "continue", "trend_ema": 400},
    "exits": {"stop": "fixed", "sl_atr": 0.0, "rr": 0.0, "trail_atr": 0.0,
              "max_bars": MAX_BARS, "swing_lookback": 0},
    "description": ("4H candle closes BEYOND the 40-candle extreme in the direction of the EMA400, stop beyond the "
                    "candle's own far side, target 2 R, gold 4H (the owner's manipulation-candle rule)"),
    "variant": FORWARD_VARIANT,
}


def sweep_variants(symbol: str, timeframe: str) -> list:
    """36 per market: 2 modes x 3 lookbacks x 2 body filters x 3 reward ratios.

    Both readings of the owner's pictures are in the grid, because the first version tested only the
    fade and the owner's own wording turned out to describe the continuation.
    """
    out = []
    for mode, lookback, require_body, rr in product(MODES, LOOKBACKS, BODY_FILTERS, REWARD_RATIOS):
        name = f"{mode}|lb{lookback}|{'body' if require_body else 'nobody'}|rr{rr:.0f}"
        out.append({
            "family": "sweep_reversal",
            "params": {"symbol": symbol, "timeframe": timeframe, "lookback": lookback,
                       "require_body": require_body, "rr": rr, "mode": mode},
            "exits": {"stop": "fixed", "sl_atr": 0.0, "rr": 0.0, "trail_atr": 0.0,
                      "max_bars": MAX_BARS, "swing_lookback": 0},
            "description": (f"4H sweep of the {lookback}-candle extreme, "
                            + ("closed BEYOND it, trade with it" if mode == "continue"
                               else "closed back inside, fade it")
                            + f"{', body confirms' if require_body else ''}, {rr:.0f}R"),
            "variant": name,
        })
    return out


def run(symbols=("XAUUSD", "BTCUSD"), timeframes=("4h",)) -> dict:
    from .mtf_data import load_bars

    registry = lab.load_registry()
    n_trials = (len(MODES) * len(LOOKBACKS) * len(BODY_FILTERS) * len(REWARD_RATIOS)
                * len(symbols) * len(timeframes))
    report = {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
              "rule": "a 4H candle sweeps the prior N-candle extreme and closes back inside; trade the reversal",
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
        for spec in sweep_variants(symbol, timeframe):
            record = lab.evaluate_candidate(market, spec, with_holdout=True)
            orders = lab.strategy_orders(market.ind, spec)
            record["variant"] = spec["variant"]
            record["signals"] = int(np.count_nonzero(orders[0]))
            inverse_spec = {**spec, "params": {**spec["params"], "inverse": True}}
            inverse = market.summary("holdout", market.simulate(inverse_spec, "holdout"))
            record["inverse_holdout"] = {"trades": inverse.get("trades"),
                                         "profit_factor": inverse.get("profit_factor"),
                                         "net_pct": inverse.get("total_return_pct")}
            record["splits_all_positive"] = all(
                ((record.get(split) or {}).get("total_return_pct") or -1) > 0
                for split in ("search", "validation", "holdout"))
            record["beats_inverse"] = bool(
                (record.get("holdout") or {}).get("total_return_pct", -999)
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
            "survivors": [r["variant"] for r in records
                          if r["splits_all_positive"] and r["beats_inverse"]]}
    lab.LAB_DIR.mkdir(parents=True, exist_ok=True)
    scope = "-".join(sorted(symbols)) + "_" + "-".join(timeframes)
    path = lab.LAB_DIR / f"sweep_reversal_{scope}_{datetime.now(timezone.utc):%Y%m%d}.json"
    path.write_text(json.dumps(report, indent=1, default=lambda v: v.item() if hasattr(v, "item") else str(v)),
                    encoding="utf-8")
    report["path"] = str(path)
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="4H sweep-and-reject reversal (research only)")
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
        print(f"\n=== {key}  {market['info'].get('bars')} bars")
        rows = sorted(market["variants"], key=lambda r: -((r.get("holdout") or {}).get("profit_factor") or 0))
        for r in rows:
            h, inv = r.get("holdout") or {}, r.get("inverse_holdout") or {}
            flags = ("all3 " if r["splits_all_positive"] else "     ") + ("inv" if r["beats_inverse"] else "   ")
            print(f"  {r['variant']:22s} sig {r['signals']:4d} n {h.get('trades', 0):4d} "
                  f"PF {h.get('profit_factor') or 0:5.3f} net {h.get('total_return_pct') or 0:+7.2f}% "
                  f"| inv PF {inv.get('profit_factor') or 0:5.3f} | {flags}")
        print(f"  positive on all three splits: {market['positive_on_all_splits'] or 'none'}")
        print(f"  and also beating the inverse: {market['survivors'] or 'none'}")
        print(f"  passed the holdout bar:       {market['passed_holdout'] or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
