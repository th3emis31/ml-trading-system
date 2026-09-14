"""Meta-labelling research (R6): a simple rule picks the side, ML decides take or skip.

Research only: nothing here writes ``models/`` or changes live signals.

Why
---
R1 (trees) and R3 (LSTM) asked the model to find direction by itself and no
configuration passed the holdout. Meta-labelling asks an easier question: a
fixed, untuned rule proposes a trade (trend: EMA50 vs EMA200; breakout: close
beyond the prior 20-bar high/low) and the model only estimates whether *that*
trade reaches its target before its stop. Trades are kept when their expected
value clears a fixed floor or a trailing quantile of past EVs (R7a).

What keeps it honest
--------------------
* Same leak-free features, triple-barrier outcomes, walk-forward folds, purge,
  costs, next-open entries and gap-adjusted stops as ``src/edge_research``.
* The model trains only on bars where the rule fired, before each fold's purge.
* Selection (rule x barrier set x model x threshold rule) uses validation folds
  only; the choice is applied once to the holdout folds.
* The report always includes the **same rule without the ML filter** on the
  holdout, so it is clear whether the filter adds anything.
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd

from .data import fetch_yahoo_history
from .edge_research import (INTERVAL_SPECS, MIN_EV_GRID, MIN_VALIDATION_TRADES, QUANTILE_GRID, RESEARCH_DIR,
                            RESEARCH_FEATURES, ProgressFn, _fit_predict, _metrics, best_ev, build_research_features,
                            evaluate_pass, load_research_frame, simulate, trailing_quantile, triple_barrier_outcomes,
                            walk_forward_blocks)
from .walkforward_backtest import BACKTEST_COSTS, RANDOM_SEED, _iso, _report

PRIMARY_RULES = ("trend", "breakout")
META_MODELS = ("logit", "rf")
SUMMARY_KEYS = ("trades", "long_trades", "short_trades", "win_rate_pct", "profit_factor", "expectancy_pct",
                "max_drawdown_pct", "total_return_pct")


def primary_side(df: pd.DataFrame, rule: str) -> np.ndarray:
    """+1 / -1 / 0 per bar from a fixed rule that uses bars up to t only."""
    if rule == "trend":
        return np.where(df["trend_up"].to_numpy(dtype=float) > 0.5, 1, -1).astype(int)
    if rule == "breakout":
        up = df["breakout_up_20"].to_numpy(dtype=float) > 0.5
        down = df["breakout_dn_20"].to_numpy(dtype=float) > 0.5
        side = np.zeros(len(df), dtype=int)
        side[up] = 1
        side[down & ~up] = -1
        return side
    raise ValueError(f"unknown primary rule {rule}")


def _summary(metrics: dict) -> dict:
    return {key: metrics.get(key) for key in SUMMARY_KEYS}


def run_meta_research(symbol: str, interval: str = "1h", *, rules: tuple = PRIMARY_RULES, models: tuple = META_MODELS,
                      configs: Optional[list] = None, n_folds: int = 8, holdout_folds: int = 3,
                      progress: ProgressFn = None, data: Optional[pd.DataFrame] = None, source: str = "yahoo",
                      mtf: bool = False, htf_data: Optional[dict] = None) -> dict:
    started = time.monotonic()
    symbol = symbol.upper()
    spec = INTERVAL_SPECS[interval]
    configs = configs or spec["configs"]
    cost_pct = BACKTEST_COSTS.get(symbol, BACKTEST_COSTS["default"])["round_trip_pct"]
    base = {"symbol": symbol, "interval": interval, "method": "meta-labelling", "seed": RANDOM_SEED,
            "cost_round_trip_pct": cost_pct, "ran_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")}

    _report(progress, "data", 3, f"Fetching {symbol} {interval} history")
    # Same bars and features as edge research (broker candles via the app for 15m/4h, closed-bar HTF with mtf).
    df, feature_names, data_source, htf_used = load_research_frame(symbol, interval, source=source, mtf=mtf, data=data,
                                                                   htf_data=htf_data, progress=progress)
    if df is None:
        return {**base, "available": False, "reason": "No real history returned; research never uses synthetic prices."}
    base.update({"data_source": data_source, "mtf": bool(mtf), "htf_used": htf_used, "features": feature_names})
    X = df[feature_names].to_numpy(dtype=float)
    feature_ok = np.isfinite(X).all(axis=1)
    valid_positions = np.flatnonzero(feature_ok)
    if len(valid_positions) < 800:
        return {**base, "available": False, "reason": f"only {len(valid_positions)} usable bars after warm-up"}
    first_valid = int(valid_positions[0])
    blocks = walk_forward_blocks(len(df), first_valid, n_folds)
    fold_of_row = np.zeros(len(df), dtype=int)
    for k, block in enumerate(blocks, start=1):
        fold_of_row[block] = k
    validation_ids = list(range(1, n_folds - holdout_folds + 1))
    holdout_ids = list(range(n_folds - holdout_folds + 1, n_folds + 1))
    validation_rows = np.flatnonzero(np.isin(fold_of_row, validation_ids) & feature_ok)
    holdout_rows = np.flatnonzero(np.isin(fold_of_row, holdout_ids) & feature_ok)

    outcomes_by_cfg = {cfg["name"]: triple_barrier_outcomes(df, cfg["sl_atr"], cfg["tp_atr"], cfg["horizon"])
                       for cfg in configs}
    candidates, fold_info, cache = [], [], {}
    steps = len(rules) * len(configs) * len(models) * len(blocks)
    step = 0
    for rule in rules:
        side = primary_side(df, rule)
        for cfg in configs:
            outcomes = outcomes_by_cfg[cfg["name"]]
            label = np.full(len(df), np.nan)
            label[side == 1] = outcomes[1]["label"][side == 1]
            label[side == -1] = outcomes[-1]["label"][side == -1]
            for model_name in models:
                proba = np.full(len(df), np.nan)
                for k, block in enumerate(blocks, start=1):
                    step += 1
                    _report(progress, "train", 5 + int(80 * step / max(1, steps)),
                            f"{rule} / {cfg['name']} / {model_name} / fold {k} of {len(blocks)}")
                    train_end = int(block[0]) - cfg["horizon"]
                    train_rows = np.arange(first_valid, max(first_valid, train_end))
                    train_rows = train_rows[feature_ok[train_rows] & (side[train_rows] != 0) & np.isfinite(label[train_rows])]
                    test_rows = block[feature_ok[block] & (side[block] != 0)]
                    if model_name == models[0] and cfg is configs[0]:
                        fold_info.append({"rule": rule, "fold": k, "role": "validation" if k in validation_ids else "holdout",
                                          "train_end_idx": int(train_end - 1), "test_start_idx": int(block[0]),
                                          "train_rows": int(len(train_rows)), "test_rows": int(len(test_rows))})
                    if len(train_rows) < 150 or len(test_rows) == 0:
                        continue
                    proba[test_rows] = _fit_predict(model_name, X[train_rows], label[train_rows].astype(int), X[test_rows])
                # Only the rule's side gets the model probability; the other side's EV is negative by construction.
                predicted = np.isfinite(proba)
                p_buy = np.full(len(df), np.nan)
                p_sell = np.full(len(df), np.nan)
                p_buy[predicted] = np.where(side[predicted] == 1, proba[predicted], 0.0)
                p_sell[predicted] = np.where(side[predicted] == -1, proba[predicted], 0.0)
                cache[(rule, cfg["name"], model_name)] = (p_buy, p_sell)
                ev = best_ev(df, p_buy, p_sell, cfg, cost_pct)
                for min_ev, quantile in [(v, None) for v in MIN_EV_GRID] + [(0.0, q) for q in QUANTILE_GRID]:
                    threshold = trailing_quantile(ev, spec["trailing_window"], quantile) if quantile else None
                    m = _metrics(df, simulate(df, outcomes, p_buy, p_sell, validation_rows, cfg, min_ev, cost_pct,
                                              threshold=threshold), validation_rows)
                    candidates.append({"rule": rule, "config": cfg["name"], "model": model_name, "min_ev": min_ev,
                                       "quantile": quantile, **_summary(m)})

    ranked = sorted(candidates, key=lambda c: ((c["trades"] or 0) >= MIN_VALIDATION_TRADES,
                                               (c["total_return_pct"] or -999) > 0, c["profit_factor"] or 0.0,
                                               c["expectancy_pct"] or -999), reverse=True)
    chosen = ranked[0]
    cfg = next(c for c in configs if c["name"] == chosen["config"])
    outcomes = outcomes_by_cfg[cfg["name"]]
    side = primary_side(df, chosen["rule"])
    p_buy, p_sell = cache[(chosen["rule"], cfg["name"], chosen["model"])]
    threshold = None
    if chosen.get("quantile"):
        threshold = trailing_quantile(best_ev(df, p_buy, p_sell, cfg, cost_pct), spec["trailing_window"], chosen["quantile"])
    holdout_trades = simulate(df, outcomes, p_buy, p_sell, holdout_rows, cfg, chosen["min_ev"], cost_pct, threshold=threshold)
    holdout_metrics = _metrics(df, holdout_trades, holdout_rows)
    unfiltered_holdout = _metrics(df, simulate(df, outcomes, None, None, holdout_rows, cfg, 0.0, cost_pct,
                                               side_rule=lambda t: int(side[t])), holdout_rows)
    filtered_pf = holdout_metrics.get("profit_factor") or 0.0
    unfiltered_pf = unfiltered_holdout.get("profit_factor") or 0.0
    filter_adds_value = bool(filtered_pf > unfiltered_pf and
                             (holdout_metrics.get("total_return_pct") or -999) > (unfiltered_holdout.get("total_return_pct") or -999))

    _report(progress, "done", 100, "Meta-labelling research complete")
    return {
        **base,
        "available": True,
        "bars": int(len(df)),
        "data_start": _iso(df["datetime"].iloc[0]),
        "data_end": _iso(df["datetime"].iloc[-1]),
        "rules": list(rules),
        "models": list(models),
        "configs": configs,
        "configurations_tried": len(candidates),
        "validation_folds": validation_ids,
        "holdout_folds": holdout_ids,
        "fold_training": fold_info,
        "selection": {"chosen": chosen, "top_validation": ranked[:8], "folds_used": validation_ids,
                      "validation_profitable": (chosen["trades"] or 0) >= MIN_VALIDATION_TRADES
                      and (chosen["total_return_pct"] or -1) > 0},
        "holdout": {"metrics": holdout_metrics, "verdict": evaluate_pass(holdout_metrics),
                    "recent_trades": holdout_trades[-20:]},
        "unfiltered_rule_holdout": _summary(unfiltered_holdout),
        "filter_adds_value": filter_adds_value,
        "duration_sec": round(time.monotonic() - started, 1),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Meta-labelling research: rule proposes, ML filters.")
    parser.add_argument("--symbols", nargs="+", default=["XAUUSD", "BTCUSD"])
    parser.add_argument("--interval", default="1h", choices=sorted(INTERVAL_SPECS))
    parser.add_argument("--source", default="yahoo", choices=["yahoo", "auto", "app"],
                        help="history source; 15m and 4h always use auto (broker bars via the app, then Yahoo)")
    parser.add_argument("--mtf", action="store_true", help="join closed higher-timeframe context features")
    parser.add_argument("--rules", nargs="+", default=list(PRIMARY_RULES), choices=list(PRIMARY_RULES))
    parser.add_argument("--models", nargs="+", default=list(META_MODELS), help="e.g. --models logit (less memory)")
    args = parser.parse_args()
    for sym in args.symbols:
        report = run_meta_research(sym, args.interval, rules=tuple(args.rules), models=tuple(args.models),
                                   source=args.source, mtf=args.mtf,
                                   progress=lambda s, p, m: print(f"[{p:3d}%] {m}", flush=True))
        RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        suffix = ("_mtf" if args.mtf else "") + ("" if tuple(args.models) == META_MODELS else "_" + "-".join(args.models))
        path = RESEARCH_DIR / f"meta_{sym.lower()}_{args.interval}{suffix}_{stamp}.json"
        path.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
        if not report.get("available"):
            print(sym, "unavailable:", report.get("reason"))
            continue
        hold = report["holdout"]["metrics"]
        print(f"\n=== {sym} {args.interval} meta | saved {path}")
        print("chosen on validation:", report["selection"]["chosen"])
        print("HOLDOUT (filtered):", {k: hold.get(k) for k in ("period",) + SUMMARY_KEYS + ("buy_and_hold_pct", "sharpe")})
        print("HOLDOUT (rule without filter):", report["unfiltered_rule_holdout"])
        print("filter adds value:", report["filter_adds_value"], "| verdict:", report["holdout"]["verdict"])
