"""SwingTrendPullback on XAUUSD H4 with swap-avoiding exits (research only, never trades or changes the live EA).

Swap on long gold is the largest measured cost of every SwingTrendPullback preset (MT5 Strategy Tester: -61k on the
steady preset). Pre-stated 15 Sep 2026, before any result, for three specs (the live EA's TradingView inputs and the
saved "steady" and "trend rider" presets, loaded from the Strategy Lab registry):
  none    as is
  triple  close at the close of the last bar before Wednesday's 17:00 New York rollover (charged three nights)
  any     close before every rollover (no overnight holds)
  day     at most 6 H4 bars (one day) in the trade
Judged like every Strategy Lab candidate on the locked XAUUSD:4h boundaries; the deflated Sharpe counts all trials
already tried on this market plus these 9 new ones (the standing 0.95 bar is not lowered).

    python -m src.stp_swap_lab run
"""
from __future__ import annotations

import argparse
import copy
import json
from datetime import datetime, timezone

from . import crt_lab
from . import strategy_lab as lab

MARKET = "XAUUSD:4h"
PRESET_IDS = {"steady": "bf0f48e8cb55", "trend_rider": "6e54ea0bf2ce"}
RULES = ("none", "triple", "any", "day")


def base_specs(registry: dict) -> dict:
    records = registry.get("candidates") or {}
    items = records.values() if isinstance(records, dict) else records
    by_id = {r["id"]: r for r in items if r.get("market") == MARKET}
    specs = {"tradingview_live": copy.deepcopy(lab.EA_SPECS["tradingview"])}
    for name, cid in PRESET_IDS.items():
        if cid not in by_id:
            raise KeyError(f"registry has no {MARKET} candidate {cid} ({name})")
        specs[name] = copy.deepcopy(by_id[cid]["spec"])
    return specs


def apply_rule(spec: dict, rule: str) -> dict:
    out = copy.deepcopy(spec)
    exits = out["exits"]
    if rule == "triple":
        exits["exit_before_triple_swap"] = True
    elif rule == "any":
        exits["exit_before_rollover"] = True
    elif rule == "day":
        exits["max_bars"] = min(int(exits.get("max_bars") or 6), 6)
    return out


def run() -> dict:
    from .mtf_data import load_bars

    registry = lab.load_registry()
    meta = registry["markets"][MARKET]
    symbol, timeframe = MARKET.split(":")
    market = lab.Market(symbol, timeframe, load_bars(symbol, timeframe, source="app"), boundaries=meta.get("boundaries"), swap=True)
    new_trials = len(base_specs(registry)) * (len(RULES) - 1)
    n_trials = int(meta.get("candidates_tried") or 0) + new_trials
    sr_variance = lab._market_variance(meta)
    rows = []
    for name, spec in base_specs(registry).items():
        for rule in RULES:
            candidate = apply_rule(spec, rule)
            record = lab.evaluate_candidate(market, candidate, with_holdout=True)
            orders = lab.strategy_orders(market.ind, candidate)
            record["holdout_r"] = crt_lab.r_stats(market.simulate(candidate, "holdout", orders), orders[1])
            swap_paid = {split: round(sum(t["swap_pct"] for t in market.simulate(candidate, split, orders)), 2)
                         for split in ("search", "validation", "holdout")}
            record.update({"base": name, "rule": rule, "swap_pct_paid": swap_paid,
                           "holdout_verdict": lab.holdout_verdict(record, n_trials, sr_variance),
                           "holdout_verdict_local": lab.holdout_verdict(record, new_trials + len(base_specs(registry)), sr_variance)})
            rows.append(record)
    report = {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "market": MARKET, "info": market.info,
              "n_trials_official": n_trials, "rows": rows}
    path = lab.LAB_DIR / f"stp_swap_backtest_{datetime.now(timezone.utc).strftime('%Y%m%d')}.json"
    path.write_text(json.dumps(report, indent=1, default=lambda v: v.item() if hasattr(v, "item") else str(v)), encoding="utf-8")
    report["path"] = str(path)
    return report


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="SwingTrendPullback gold H4 with swap-avoiding exits (research only).")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run")
    parser.parse_args(argv)
    report = run()
    print(f"=== {report['market']} {report['info']['periods']} | official trials {report['n_trials_official']}")
    for r in report["rows"]:
        s, v, h = r["search"], r["validation"], r["holdout"]
        ver, loc = r["holdout_verdict"] or {}, r["holdout_verdict_local"] or {}
        print(f"{r['base']:17s} {r['rule']:6s} S {s['trades']:>4} PF {s['profit_factor']} {s['total_return_pct']}% DD {s['max_drawdown_pct']} | "
              f"V {v['trades']:>3} PF {v['profit_factor']} {v['total_return_pct']}% | {'PASS' if r['validated'] else 'fail'} | "
              f"H {h['trades']:>3} PF {h['profit_factor']} {h['total_return_pct']}% DD {h['max_drawdown_pct']} R {r['holdout_r']['net_r']} "
              f"swap% {r['swap_pct_paid']} | DSR official {ver.get('deflated_sharpe')} local {loc.get('deflated_sharpe')} "
              f"{'PASS' if ver.get('passed') else 'fail'}")
    print("\nsaved:", report["path"])


if __name__ == "__main__":
    main()
