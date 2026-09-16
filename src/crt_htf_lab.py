"""CRT on 1h / 4h / 1d candles (classic sweep-and-reclaim and the CRT_Dashboard_EA pattern scaled to ATR).

Research only, never trades. Rules and the pre-stated grid: strategies/crt_higher_timeframes.md.

    python -m src.crt_htf_lab run [--markets XAUUSD:4h ...]
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from itertools import product

import numpy as np

from . import crt_lab
from . import strategy_lab as lab

MARKETS = ("XAUUSD:1h", "XAUUSD:4h", "XAUUSD:1d", "BTCUSD:1h", "BTCUSD:4h", "BTCUSD:1d")
MAX_BARS = {"1h": 48, "4h": 60, "1d": 20}
SCALED = {"body_atr": 1.0, "min_mid": 1, "max_mid": 5, "mid_factor": 0.9, "manip_atr": 1.5, "sweep_atr": 0.1, "buffer_atr": 0.8}
CLASSIC_BUFFER_ATR = 0.1


def classic_crt_orders(ind: lab.Indicators, target_mode: str, trend: str):
    o, h, l, c = ind.o, ind.h, ind.l, ind.c
    atr = ind.atr(14)
    ph, pl = np.roll(h, 1), np.roll(l, 1)
    ph[0], pl[0] = np.nan, np.nan
    with np.errstate(invalid="ignore"):
        bull = (l < pl) & (c > pl) & (c < ph)
        bear = (h > ph) & (c < ph) & (c > pl)
        side = np.where(bull & ~bear, 1, np.where(bear & ~bull, -1, 0))
        stop = np.where(side == 1, l - CLASSIC_BUFFER_ATR * atr, np.where(side == -1, h + CLASSIC_BUFFER_ATR * atr, np.nan))
        risk = side * (c - stop)
        if target_mode == "range":
            target = np.where(side == 1, ph, np.where(side == -1, pl, np.nan))
            side = np.where(side * (target - c) >= risk, side, 0)
        else:
            target = c + side * 2.0 * risk
    return _finish(ind, side, stop, target, trend)


def scaled_crt_orders(ind: lab.Indicators, target_mode: str, trend: str):
    def build():
        o, h, l, c = ind.o, ind.h, ind.l, ind.c
        atr = ind.atr(14)
        n = len(c)
        direction, ref = np.zeros(n, dtype=int), np.full(n, np.nan)
        best_score = np.full(n, -1.0)
        for i in range(SCALED["max_mid"] + 2, n):
            a_atr = atr[i]
            if not np.isfinite(a_atr) or a_atr <= 0:
                continue
            for a_off in range(SCALED["min_mid"] + 1, SCALED["max_mid"] + 2):
                a = i - a_off
                body = abs(c[a] - o[a])
                if body < SCALED["body_atr"] * a_atr:
                    continue
                ah, al = h[a], l[a]
                for sign in (1, -1):
                    if sign == 1 and not (c[a] < o[a] and c[i] > o[i] and c[i] > ah):
                        continue
                    if sign == -1 and not (c[a] > o[a] and c[i] < o[i] and c[i] < al):
                        continue
                    ok, swept = True, False
                    for k in range(a + 1, i):
                        if abs(o[k] - c[k]) > body * SCALED["mid_factor"] or h[k] > ah + SCALED["manip_atr"] * a_atr \
                                or l[k] < al - SCALED["manip_atr"] * a_atr or c[k] > ah or c[k] < al:
                            ok = False
                            break
                        if (sign == 1 and l[k] < al - SCALED["sweep_atr"] * a_atr) or (sign == -1 and h[k] > ah + SCALED["sweep_atr"] * a_atr):
                            swept = True
                    if not ok:
                        continue
                    if not (swept or (sign == 1 and l[i] < al - SCALED["sweep_atr"] * a_atr)
                            or (sign == -1 and h[i] > ah + SCALED["sweep_atr"] * a_atr)):
                        continue
                    strength = ((c[i] - ah) if sign == 1 else (al - c[i])) / body
                    if strength > best_score[i]:
                        best_score[i], direction[i] = strength, sign
                        ref[i] = min(al, l[i]) if sign == 1 else max(ah, h[i])
        return direction, ref

    direction, ref = ind._cached(("scaled_crt",), build)
    atr, c = ind.atr(14), ind.c
    with np.errstate(invalid="ignore"):
        stop = np.where(direction == 1, ref - SCALED["buffer_atr"] * atr, np.where(direction == -1, ref + SCALED["buffer_atr"] * atr, np.nan))
        risk = direction * (c - stop)
        rr = 3.0 if target_mode == "range" else 2.0
        target = c + direction * rr * risk
    return _finish(ind, direction.copy(), stop, target, trend)


def _finish(ind: lab.Indicators, side, stop, target, trend: str):
    c = ind.c
    with np.errstate(invalid="ignore"):
        if trend == "ema200":
            ema = ind.ema(200)
            side = np.where((side == 1) & (c > ema) | (side == -1) & (c < ema), side, 0)
        risk = side * (c - stop)
        valid = (side != 0) & np.isfinite(stop) & np.isfinite(target) & (risk > 0)
    side = np.where(valid, side, 0).astype(int)
    return side, np.where(valid, stop, np.nan), np.where(valid, target, np.nan)


def htf_orders(ind: lab.Indicators, spec: dict):
    p = spec["params"]
    builder = classic_crt_orders if p["model"] == "classic" else scaled_crt_orders
    return builder(ind, p["target"], p["trend"])


lab.ORDER_BUILDERS["crt_htf"] = htf_orders


def htf_variants(symbol: str, timeframe: str) -> list[dict]:
    exits = {"stop": "crt", "sl_atr": 0.0, "rr": 0.0, "trail_atr": 0.0, "max_bars": MAX_BARS[timeframe], "swing_lookback": 0}
    out = []
    for model, target, trend in product(("classic", "ea_scaled"), ("rr2", "range"), ("none", "ema200")):
        label = f"{model}|{'3R' if model == 'ea_scaled' and target == 'range' else target}|{trend}"
        out.append({"family": "crt_htf", "params": {"symbol": symbol, "model": model, "target": target, "trend": trend},
                    "exits": dict(exits), "description": f"CRT {timeframe} {label}", "variant": label})
    return out


def run(markets=MARKETS) -> dict:
    from .mtf_data import load_bars

    registry = lab.load_registry()
    n_trials = 8 * len(markets)
    report = {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "n_trials": n_trials, "markets": {}}
    for key in markets:
        symbol, timeframe = key.split(":")
        market = lab.Market(symbol, timeframe, load_bars(symbol, timeframe, source="app"),
                            boundaries=(registry["markets"].get(key) or {}).get("boundaries"), swap=True)
        records = []
        for spec in htf_variants(symbol, timeframe):
            record = lab.evaluate_candidate(market, spec, with_holdout=True)
            orders = lab.strategy_orders(market.ind, spec)
            for split in ("search", "validation", "holdout"):
                record[f"{split}_r"] = crt_lab.r_stats(market.simulate(spec, split, orders), orders[1])
            record["variant"] = spec["variant"]
            records.append(record)
        sr_variance = lab._variance([[r["validation_sr"], r["validation"]["trades"]] for r in records if r["validation_sr"] is not None])
        for record in records:
            record["holdout_verdict"] = lab.holdout_verdict(record, n_trials, sr_variance)
        validated = [r for r in records if r["validated"]]
        chosen = max(validated, key=lambda r: r["score"]) if validated else None
        report["markets"][key] = {"info": market.info, "variants": records, "validated": [r["variant"] for r in validated],
                                  "chosen_on_search_validation": chosen["variant"] if chosen else None}
    path = lab.LAB_DIR / f"crt_htf_backtest_{datetime.now(timezone.utc).strftime('%Y%m%d')}.json"
    path.write_text(json.dumps(report, indent=1, default=lambda v: v.item() if hasattr(v, "item") else str(v)), encoding="utf-8")
    report["path"] = str(path)
    return report


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="CRT on 1h/4h/1d candles (research only, never trades).")
    sub = parser.add_subparsers(dest="command", required=True)
    go = sub.add_parser("run")
    go.add_argument("--markets", nargs="+", default=list(MARKETS))
    args = parser.parse_args(argv)
    report = run(args.markets)
    for key, market in report["markets"].items():
        print(f"\n=== {key} {market['info']['data_start']} -> {market['info']['data_end']} | {market['info']['periods']}")
        for r in market["variants"]:
            v, s, va, ho = r.get("holdout_verdict") or {}, r["search"], r["validation"], r["holdout"]
            print(f"{r['variant']:26s} S {s['trades']:>4} PF {s['profit_factor']} {s['total_return_pct']}% R {r['search_r']['net_r']:>7} | "
                  f"V {va['trades']:>3} PF {va['profit_factor']} R {r['validation_r']['net_r']:>6} | {'PASS' if r['validated'] else 'fail'} "
                  f"{r['score']:>7} || H {ho['trades']:>3} PF {ho['profit_factor']} {ho['total_return_pct']}% R {r['holdout_r']['net_r']:>6} "
                  f"DD {ho['max_drawdown_pct']} B&H {ho.get('buy_and_hold_pct')} DSR {v.get('deflated_sharpe')} {'PASS' if v.get('passed') else 'fail'}")
        print("validated:", market["validated"], "| chosen:", market["chosen_on_search_validation"])
    print("\nsaved:", report["path"], "| trials counted:", report["n_trials"])


if __name__ == "__main__":
    main()
