"""Every strategy the system owns, one by one, LONG only then SHORT only, on 15m / 1h / 4h.

    python -m src.direction_sweep run --side long
    python -m src.direction_sweep run --side short

WHAT THIS IS FOR
----------------
The owner asked for exactly this: take every strategy in the system, run it on each timeframe, test the
LONG side first, keep whatever is profitable, then repeat for the short side. It is a survey, not a search
for one winner, so nothing here invents a new rule - each family brings its own declared variants and the
only thing this module changes is the DIRECTION it is allowed to trade.

HOW A SIDE IS FORCED
--------------------
Each family's registered builder is wrapped: it produces its usual side/stop/target, and then every signal
in the unwanted direction is set to zero. Nothing else is touched - the stops, targets, exits and filters
stay exactly as that family declared them. A family that is long-only by nature simply reports fewer
trades on the short pass, which is itself worth seeing.

THE HONEST ARITHMETIC OF A SURVEY THIS SIZE
-------------------------------------------
Running hundreds of variants across three timeframes and two directions is a very large number of trials,
and the deflated Sharpe charges every one of them. Almost nothing survives that bar, and quoting it here
would be quoting a number that the survey's own size made unreachable. So this module ranks on what a
survey can honestly establish and says so plainly:

1. **positive after costs on the holdout**, which is the minimum, and
2. **positive on search, validation AND holdout**, which is the one that separates a strategy from a
   window, and
3. **beats its own inverse**, because a signal whose inverse also wins is carrying no direction.

Anything clearing all three is a CANDIDATE and is saved as such. A candidate is not a proven edge, and
promoting one to money still needs the unchanged 0.95 deflated Sharpe bar on its own pre-declared test.
"""
from __future__ import annotations

import argparse
import importlib
import json
import warnings
from datetime import datetime, timezone
from typing import Callable, Optional

import numpy as np

from . import strategy_lab as lab

TIMEFRAMES = ("15m", "1h", "4h")
SYMBOLS = ("XAUUSD", "BTCUSD")

# family -> (module, the function that returns its declared variants)
FAMILY_VARIANTS = {
    "candle_pattern": ("candle_pattern_lab", "pattern_variants"),
    "cisd": ("cisd_lab", "cisd_variants"),
    "crt_displacement": ("crt_displacement", "displacement_variants"),
    "crt_htf": ("crt_htf_lab", "htf_variants"),
    "crt_mss": ("crt_mss_lab", "mss_variants"),
    "poi_liquidity": ("poi_liquidity", "poi_variants"),
    "smart_entry_arch": ("smart_entry_arch", "arch_variants"),
    "sweep_reversal": ("sweep_reversal", "sweep_variants"),
    "sweep_reclaim": ("sweep_reversal", "reclaim_variants"),
}


def load_families() -> dict:
    """Import every lab so its builder registers, and collect the variant generators that worked."""
    found = {}
    for family, (module_name, function_name) in FAMILY_VARIANTS.items():
        try:
            module = importlib.import_module(f".{module_name}", package="src")
            if hasattr(module, "_register"):
                module._register()
            found[family] = getattr(module, function_name)
        except Exception as exc:                 # noqa: BLE001 - a family that will not load is reported
            found[family] = exc
    return found


def one_side_builder(builder: Callable, side_wanted: int) -> Callable:
    """The same builder, with every signal in the other direction removed."""

    def build(ind, spec):
        orders = builder(ind, spec)
        side, stop, target = orders[0].copy(), orders[1], orders[2]
        side = np.where(side == side_wanted, side_wanted, 0).astype(int)
        return (side, stop, target) + tuple(orders[3:])

    return build


def evaluate_family(family: str, variants_for: Callable, symbol: str, timeframe: str,
                    side_wanted: int) -> list:
    """Every declared variant of one family, on one market and timeframe, on one side only."""
    from .mtf_data import load_bars

    registry = lab.load_registry()
    key = f"{symbol}:{timeframe}"
    bars = load_bars(symbol, timeframe, source="app")
    if bars is None or bars.empty:
        return [{"family": family, "market": key, "error": "no broker bars"}]
    try:
        market = lab.Market(symbol, timeframe, bars,
                            boundaries=(registry["markets"].get(key) or {}).get("boundaries"), swap=True)
    except Exception as exc:                     # noqa: BLE001 - too few bars, usually
        return [{"family": family, "market": key, "error": f"{type(exc).__name__}: {exc}"[:120]}]

    original = lab.ORDER_BUILDERS.get(family)
    if original is None:
        return [{"family": family, "market": key, "error": "no registered builder"}]

    try:
        specs = variants_for(symbol, timeframe)
    except Exception as exc:                     # noqa: BLE001
        return [{"family": family, "market": key, "error": f"variants failed: {exc}"[:120]}]

    lab.ORDER_BUILDERS[family] = one_side_builder(original, side_wanted)
    out = []
    try:
        for spec in specs:
            spec = {**spec, "family": family}
            try:
                record = lab.evaluate_candidate(market, spec, with_holdout=True)
            except Exception as exc:             # noqa: BLE001 - one bad variant must not stop the survey
                out.append({"family": family, "market": key, "variant": spec.get("variant"),
                            "error": f"{type(exc).__name__}: {exc}"[:120]})
                continue
            holdout = record.get("holdout") or {}
            if not holdout.get("trades"):
                continue
            inverse_spec = {**spec, "params": {**spec["params"], "inverse": True}}
            try:
                inverse = market.summary("holdout", market.simulate(inverse_spec, "holdout"))
            except Exception:                    # noqa: BLE001
                inverse = {}
            splits = [(record.get(s) or {}).get("total_return_pct") for s in
                      ("search", "validation", "holdout")]
            out.append({
                "family": family, "market": key, "variant": spec.get("variant"),
                "trades": holdout.get("trades"),
                "win_rate_pct": holdout.get("win_rate_pct"),
                "profit_factor": holdout.get("profit_factor"),
                "net_pct": holdout.get("total_return_pct"),
                "max_drawdown_pct": holdout.get("max_drawdown_pct"),
                "buy_and_hold_pct": holdout.get("buy_and_hold_pct"),
                # Read before believing any of it: how many outcomes the engine chose rather than measured.
                "ambiguous_exits": holdout.get("ambiguous_exits"),
                "expectancy_r": holdout.get("expectancy_r"),
                "inverse_net_pct": inverse.get("total_return_pct"),
                "inverse_trades": inverse.get("trades"),
                "splits": splits,
                "all_splits_positive": all(v is not None and v > 0 for v in splits),
                # The control only means something if it actually traded AND lost. A bullish-only pattern
                # inverted on a long-only pass produces NO trades, and comparing against nothing passed the
                # check for free - four of the first sixteen candidates cleared it that way. A positive
                # inverse is worse than no control: it says both directions made money, which is drift.
                "beats_inverse": bool((inverse.get("trades") or 0) > 0
                                      and (inverse.get("total_return_pct") is not None)
                                      and inverse["total_return_pct"] < 0
                                      and (holdout.get("total_return_pct") or -999) > inverse["total_return_pct"]),
                "inverse_control": ("none - the inverse produced no trades"
                                    if not (inverse.get("trades") or 0) else
                                    "drift - the inverse also made money"
                                    if (inverse.get("total_return_pct") or 0) > 0 else "real"),
            })
    finally:
        lab.ORDER_BUILDERS[family] = original
    return out


def run(side: str = "long", symbols=SYMBOLS, timeframes=TIMEFRAMES) -> dict:
    warnings.filterwarnings("ignore")
    side_wanted = 1 if side == "long" else -1
    families = load_families()
    rows, failures = [], []
    for family, variants_for in families.items():
        if isinstance(variants_for, Exception):
            failures.append({"family": family, "error": f"{type(variants_for).__name__}: {variants_for}"[:140]})
            continue
        for symbol in symbols:
            for timeframe in timeframes:
                for row in evaluate_family(family, variants_for, symbol, timeframe, side_wanted):
                    (failures if row.get("error") else rows).append(row)

    profitable = [r for r in rows if (r["net_pct"] or 0) > 0]
    candidates = [r for r in profitable if r["all_splits_positive"] and r["beats_inverse"]]
    candidates.sort(key=lambda r: -(r["net_pct"] or 0))

    report = {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
              "side": side, "timeframes": list(timeframes), "symbols": list(symbols),
              "variants_tested": len(rows), "profitable_on_holdout": len(profitable),
              "candidates": candidates, "all_rows": rows, "failures": failures,
              "note": ("a survey this size makes the deflated Sharpe unreachable, so candidates are ranked "
                       "on: profitable after costs, positive on all three splits, and beating their own "
                       "inverse. A candidate is not a proven edge."),
              "places_orders": False}
    lab.LAB_DIR.mkdir(parents=True, exist_ok=True)
    path = lab.LAB_DIR / f"direction_sweep_{side}_{datetime.now(timezone.utc):%Y%m%d}.json"
    path.write_text(json.dumps(report, indent=1, default=lambda v: v.item() if hasattr(v, "item") else str(v)),
                    encoding="utf-8")
    report["path"] = str(path)
    return report


def save_candidates(report: dict, limit: int = 12) -> dict:
    """Put the survivors into the system's own strategy book, at whatever status they EARN.

    Not a new store and not a new status: `strategy_book.gather_evidence` and `classify` decide, using the
    same Monte Carlo, neighbour and rolling-consistency rules every other entry was judged by. Most will
    land as `watchlist` and some as nothing at all, and that is the point - saving a survey result as
    though it were proven is how a book of 1,087 entries stops meaning anything.

    The spec stored carries `params["side"]`, so re-running it later reproduces the ONE-SIDED result that
    was measured rather than the family's usual two-sided behaviour.
    """
    from . import strategy_book as book_module
    from .mtf_data import load_bars

    families = load_families()
    book = book_module.load_book()
    entries = book.setdefault("entries", {})
    saved, skipped = [], []
    side = report.get("side", "long")

    real = [r for r in report.get("candidates", [])
            if r.get("inverse_net_pct") is not None and r["inverse_net_pct"] < 0]
    for row in real[:limit]:
        family, market_key, variant = row["family"], row["market"], row["variant"]
        variants_for = families.get(family)
        if not callable(variants_for):
            skipped.append({"variant": variant, "why": "family would not load"})
            continue
        symbol, timeframe = market_key.split(":")
        spec = next((s for s in variants_for(symbol, timeframe) if s.get("variant") == variant), None)
        if spec is None:
            skipped.append({"variant": variant, "why": "variant no longer generated"})
            continue
        spec = {**spec, "family": family, "params": {**spec["params"], "side": side}}

        bars = load_bars(symbol, timeframe, source="app")
        registry = lab.load_registry()
        market = lab.Market(symbol, timeframe, bars,
                            boundaries=(registry["markets"].get(market_key) or {}).get("boundaries"),
                            swap=True)
        record = lab.evaluate_candidate(market, spec, with_holdout=True)
        evidence = book_module.gather_evidence(market, spec, report.get("variants_tested") or 1,
                                               lab._variance([[record.get("validation_sr"),
                                                               (record.get("validation") or {}).get("trades")]]))
        status, missing = book_module.classify(evidence)
        if status is None:
            skipped.append({"variant": variant, "market": market_key, "why": "; ".join(missing)[:160]})
            continue
        entry_id = f"{market_key}|{side}|{family}|{variant}"
        entries[entry_id] = {
            "id": entry_id, "market": market_key, "family": family, "status": status,
            "description": f"{side} only - {spec.get('description', variant)}",
            "spec": spec, "first_added": entries.get(entry_id, {}).get("first_added")
            or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
            "latest": {"checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
                       "search": record.get("search"), "validation": record.get("validation"),
                       "holdout": record.get("holdout"),
                       "deflated_sharpe": (evidence.get("verdict") or {}).get("deflated_sharpe"),
                       "monte_carlo": evidence.get("monte_carlo"),
                       "neighbours": evidence.get("neighbours"), "rolling": evidence.get("rolling"),
                       "n_trials": report.get("variants_tested"), "missing": missing,
                       "inverse_net_pct": row.get("inverse_net_pct"), "status": status},
            "source": f"direction_sweep {side} {report.get('generated_at')}",
        }
        saved.append({"id": entry_id, "status": status, "net_pct": row["net_pct"],
                      "trades": row["trades"], "missing": missing})

    book["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    book_module.save_book(book)
    return {"saved": saved, "skipped": skipped, "book_entries": len(entries)}


def print_sweep_report(report: dict) -> None:
    """Not `strategy_lab.print_variant_table`: that one prints one MARKET's variants with their splits,
    and these rows are flat and span every family and market at once. Reshaping the survey to fit it would
    contort the data to suit a printer, so this is a second table with a different job, not a copy."""
    print(f"{report['side'].upper()} ONLY - every family, {', '.join(report['timeframes'])}, "
          f"after spread and swap")
    print(f"  {report['variants_tested']} variants produced trades; "
          f"{report['profitable_on_holdout']} profitable on the holdout; "
          f"{len(report['candidates'])} survive all three splits AND beat their inverse")
    if report["failures"]:
        print(f"  {len(report['failures'])} family/market combinations could not run "
              f"({', '.join(sorted({f['family'] for f in report['failures']}))})")
    print()
    if not report["candidates"]:
        print("  no candidate cleared all three checks.")
        return
    print(f"  {'family':18} {'market':14} {'variant':26} {'trades':>7} {'win%':>6} {'PF':>6} "
          f"{'net%':>8} {'inv%':>8} {'amb':>5} {'control':>8}")
    for row in report["candidates"][:25]:
        print(f"  {row['family']:18} {row['market']:14} {str(row['variant'])[:26]:26} "
              f"{row['trades']:>7} {(row['win_rate_pct'] or 0):>6.1f} {(row['profit_factor'] or 0):>6.2f} "
              f"{(row['net_pct'] or 0):>8.2f} {(row['inverse_net_pct'] or 0):>8.2f} "
              f"{(row['ambiguous_exits'] or 0):>5} {row.get('inverse_control', '?'):>8}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Every strategy, one side at a time. Never trades.")
    sub = parser.add_subparsers(dest="command", required=True)
    runner = sub.add_parser("run", help="survey one side")
    runner.add_argument("--side", choices=("long", "short"), default="long")
    runner.add_argument("--symbols", nargs="+", default=list(SYMBOLS))
    runner.add_argument("--timeframes", nargs="+", default=list(TIMEFRAMES))
    runner.add_argument("--save", action="store_true", help="store the survivors in the strategy book")
    args = parser.parse_args(argv)

    report = run(args.side, tuple(args.symbols), tuple(args.timeframes))
    print_sweep_report(report)
    print(f"\n  written to {report['path']}")
    if args.save:
        out = save_candidates(report)
        print(f"\n  saved to the strategy book: {len(out['saved'])} "
              f"(the book now holds {out['book_entries']} entries)")
        for row in out["saved"]:
            print(f"    {row['status']:12} {row['id']}")
            print(f"      net {row['net_pct']:.2f}% on {row['trades']} trades")
            for reason in row["missing"][:2]:
                print(f"      still missing: {reason[:110]}")
        for row in out["skipped"][:8]:
            print(f"    NOT saved: {row.get('variant')} - {row['why'][:110]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
