"""Backtest each candlestick pattern as a strategy, with costs, on both markets: research only.

The rules and the pre-declared grid are the "Second stage" section of
``strategies/candle_patterns.md``. Read that first, and in particular why this exists: the
measurement in ``src/candle_patterns.py`` is **gross**. It counts how often a symmetric 1 ATR
barrier resolves the pattern's way and charges nothing for spread or swap. A two-point win-rate
edge on a 1:1 barrier is about +0.04 R per trade before costs, which is small enough that a
genuinely predictive pattern can still lose money. This stage runs each pattern through the same
engine and the same cost model as every other strategy in ``.claude/memory/BASELINE.md``.

Entry is a market order at the next bar's open, which is what the measurement assumed, so
``simulate_orders`` is used without ``entry_prices``. Doji is excluded: it has no direction, and
inventing one for it would be a different hypothesis from the one that was measured.

    python -m src.candle_pattern_lab run [--symbols XAUUSD BTCUSD] [--timeframes 1h 4h]
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from itertools import product

import numpy as np

from . import crt_lab
from . import strategy_lab as lab
from .candle_patterns import DIRECTIONLESS, PATTERNS, pattern_frame

SL_ATR = 1.0                 # fixed, to match the measurement's barrier
REWARD_RATIOS = (1.0, 2.0)
MAX_BARS = 48
TRADED_PATTERNS = tuple(name for name in PATTERNS if name not in DIRECTIONLESS)


def pattern_orders(ind: lab.Indicators, spec: dict):
    """side / stop / target for one pattern, anchored on the signal bar's close and ATR."""
    p = spec["params"]
    n = len(ind.c)
    atr = ind.atr(14)

    # The Strategy Lab's random search builds specs from the FAMILIES grid alone, so symbol and
    # timeframe may be absent. The cache key does not need them: the frame is already this market's.
    signals = ind._cached(("candle_patterns",), lambda: pattern_frame(ind.df))
    side = signals[p["pattern"]].to_numpy().astype(int)
    if p.get("inverse"):
        side = -side

    risk = float(p.get("sl_atr") or SL_ATR) * atr
    with np.errstate(invalid="ignore"):
        stop = np.where(side == 1, ind.c - risk, np.where(side == -1, ind.c + risk, np.nan))
        target = np.where(side == 1, ind.c + p["rr"] * risk,
                          np.where(side == -1, ind.c - p["rr"] * risk, np.nan))
    # A bar without a usable ATR cannot be sized, so it is not a signal.
    side = np.where(np.isfinite(atr) & (atr > 0), side, 0)
    return side, stop, target


lab.ORDER_BUILDERS["candle_pattern"] = pattern_orders


def pattern_variants(symbol: str, timeframe: str) -> list[dict]:
    """24 per market and timeframe: 12 signed patterns x 2 reward ratios."""
    out = []
    for name, rr in product(TRADED_PATTERNS, REWARD_RATIOS):
        out.append({
            "family": "candle_pattern",
            "params": {"symbol": symbol, "timeframe": timeframe, "pattern": name, "rr": rr},
            "exits": {"stop": "fixed", "sl_atr": 0.0, "rr": 0.0, "trail_atr": 0.0,
                      "max_bars": MAX_BARS, "swing_lookback": 0},
            "description": f"{name} at {rr:.0f}R",
            "variant": f"{name}|rr{rr:.0f}",
        })
    return out


def run(symbols=("XAUUSD", "BTCUSD"), timeframes=("1h", "4h")) -> dict:
    from .mtf_data import load_bars

    registry = lab.load_registry()
    n_trials = len(TRADED_PATTERNS) * len(REWARD_RATIOS) * len(symbols) * len(timeframes)
    report = {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
              "n_trials_total": n_trials,
              "note": "the measurement in src/candle_patterns.py is gross; these numbers are after "
                      "spread and swap",
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
        for spec in pattern_variants(symbol, timeframe):
            record = lab.evaluate_candidate(market, spec, with_holdout=True)
            orders = lab.strategy_orders(market.ind, spec)
            record["variant"] = spec["variant"]
            record["signals"] = int(np.count_nonzero(orders[0]))
            # the inverse baseline for the same pattern, same bars
            inverse_spec = {**spec, "params": {**spec["params"], "inverse": True}}
            inverse = market.summary("holdout", market.simulate(inverse_spec, "holdout"))
            record["inverse_holdout"] = {"trades": inverse.get("trades"),
                                         "profit_factor": inverse.get("profit_factor"),
                                         "net_pct": inverse.get("total_return_pct")}
            record["splits_all_positive"] = all(
                (record.get(split) or {}).get("total_return_pct", -1) is not None
                and (record[split]["total_return_pct"] or -1) > 0
                for split in ("search", "validation", "holdout"))
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
    scope = "-".join(sorted(symbols)) + "_" + "-".join(timeframes)
    path = lab.LAB_DIR / f"candle_pattern_backtest_{scope}_{datetime.now(timezone.utc):%Y%m%d}.json"
    path.write_text(json.dumps(report, indent=1, default=lambda v: v.item() if hasattr(v, "item") else str(v)),
                    encoding="utf-8")
    report["path"] = str(path)
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Backtest candlestick patterns with costs (research only)")
    parser.add_argument("command", choices=["run"])
    parser.add_argument("--symbols", nargs="+", default=["XAUUSD", "BTCUSD"])
    parser.add_argument("--timeframes", nargs="+", default=["1h", "4h"])
    args = parser.parse_args(argv)

    report = run(tuple(args.symbols), tuple(args.timeframes))
    print(f"saved {report['path']}   ({report['n_trials_total']} trials counted in the deflated Sharpe)")
    for key, market in report["markets"].items():
        if market.get("error"):
            print(f"\n{key}: {market['error']}")
            continue
        print(f"\n=== {key}  {market['info'].get('bars')} bars")
        rows = sorted(market["variants"], key=lambda r: -((r.get("holdout") or {}).get("profit_factor") or 0))
        for r in rows:
            h = r.get("holdout") or {}
            inv = r.get("inverse_holdout") or {}
            print(f"  {r['variant']:28s} sig {r['signals']:5d} trades {h.get('trades', 0):4d} "
                  f"PF {h.get('profit_factor') or 0:5.3f} net {h.get('total_return_pct') or 0:+7.2f}% "
                  f"| inverse PF {inv.get('profit_factor') or 0:5.3f} "
                  f"| all3 {'YES' if r['splits_all_positive'] else 'no'}")
        print(f"  positive on all three splits: {market['positive_on_all_splits'] or 'none'}")
        print(f"  passed the holdout bar:       {market['passed_holdout'] or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
