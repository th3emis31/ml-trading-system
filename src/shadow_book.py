"""Shadow forward tests for every strategy the book keeps: places nothing, blocks nothing.

Why this exists. On 19 September 2026 the owner asked for the system to keep learning and building
confidence without being blocked. Two things were in the way. The 0.95 deflated Sharpe bar was being
used as a universal verdict — fixed in ``src/forward_evidence.py`` by grading instead of gating — and
only **two** candidates had a forward test running while the strategy book was holding **54**
watchlist strategies with no clean forward record at all.

The book already re-checks its entries on the newest bars, but it does so by re-running the whole
backtest, so its holdout grows and old fitted bars sit beside new ones. That is useful and it is not
the same thing as a forward test. Here each strategy gets a **fixed start instant**, and only trades
after it count. Nothing is refitted, nothing is re-selected, and no trial-counting penalty applies
because each strategy is declared once, when it first appears.

Shadow means shadow: this module simulates on real broker candles with the real cost model and
**never places an order**. There is therefore no reason for any strategy to be excluded, which is the
point — evidence is the scarce resource, and anything that can generate it safely should be.

Reads: ``data/strategy_lab/strategy_book.json`` (the book's watchlist).
Writes: ``data/strategy_lab/shadow_book.json`` (one record per strategy).

    python -m src.shadow_book run [--status] [--market XAUUSD:4h]
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd

from . import forward_evidence
from . import strategy_lab as lab
from .walkforward_backtest import _iso

BOOK_PATH = lab.LAB_DIR / "strategy_book.json"
STATE_PATH = lab.LAB_DIR / "shadow_book.json"
# Enough bars for the warm-up plus a usable forward window, per timeframe.
BARS = {"15m": 6000, "1h": 6000, "4h": 4000, "1d": 2000}
TRACKED_STATUSES = ("watchlist", "approved_for_demo")


def book_entries(path: Optional[str] = None) -> list:
    """The strategies the book is keeping, each with the spec that defines it."""
    raw = forward_evidence._read_json_or_none(path or BOOK_PATH) or {}
    entries = raw.get("entries") or {}
    rows = list(entries.values()) if isinstance(entries, dict) else list(entries)
    return [r for r in rows
            if isinstance(r, dict) and r.get("status") in TRACKED_STATUSES and r.get("spec")]


def _market_parts(market: str) -> tuple:
    symbol, _, timeframe = str(market).partition(":")
    return symbol, timeframe


def simulate_since(market: lab.Market, spec: dict, start_at: pd.Timestamp) -> dict:
    """Trades taken strictly after ``start_at``, on the real cost model. Nothing is refitted."""
    ind = market.ind
    orders = lab.strategy_orders(ind, spec)
    side, stop, target = orders[:3]
    entry_prices = orders[3] if len(orders) > 3 else None
    times = ind.times
    first = int(np.searchsorted(times.dt.tz_convert(None).to_numpy(dtype="datetime64[ns]"),
                                start_at.tz_convert(None).to_datetime64()))
    first = max(first, lab.WARMUP_BARS)
    rows = np.arange(first, len(ind.c))
    if len(rows) == 0:
        return {"trades": [], "r_values": [], "bars_watched": 0}
    trades = lab.simulate_orders(ind.o, ind.h, ind.l, ind.c, ind.atr(14), times, side, stop, target,
                                 rows, spec["exits"], market.cost_pct, market.holding, entry_prices)
    values = []
    for t in trades:
        risk = abs(t["entry_price"] - stop[t["entry_idx"]])
        t["net_r"] = round(t["net_pct"] / 100.0 * t["entry_price"] / risk, 3) if risk > 0 else 0.0
        values.append(t["net_r"])
    return {"trades": [{k: v for k, v in t.items() if k != "entry_idx"} for t in trades],
            "r_values": values, "bars_watched": int(len(rows))}


def run(now=None, market_filter: Optional[str] = None, book: Optional[str] = None,
        state_path: Optional[str] = None) -> dict:
    """One pass over the book's watchlist. Bars are fetched once per market, not once per strategy."""
    from .mtf_data import load_bars

    now = pd.Timestamp(now) if now is not None else pd.Timestamp.now(tz="UTC")
    if now.tzinfo is None:
        now = now.tz_localize("UTC")
    path = state_path or STATE_PATH
    previous = (forward_evidence._read_json_or_none(path) or {}).get("strategies") or {}

    entries = book_entries(book)
    if market_filter:
        entries = [e for e in entries if e.get("market") == market_filter]

    wanted = sorted({str(e.get("market")) for e in entries})
    markets: dict = {}
    for key in wanted:
        symbol, timeframe = _market_parts(key)
        bars = load_bars(symbol, timeframe, source="app")
        if bars is None or bars.empty or len(bars) < lab.WARMUP_BARS + 100:
            markets[key] = None
            continue
        bars = bars.tail(BARS.get(timeframe, 4000)).reset_index(drop=True)
        markets[key] = lab.Market(symbol, timeframe, bars, now=now, swap=True)

    report = {"generated_at": _iso(now), "places_orders": False,
              "note": "shadow forward tests: simulated on real broker candles with the real cost "
                      "model, and no order is ever sent",
              "tracked": len(entries), "strategies": {}}

    for entry in entries:
        key = str(entry.get("id") or entry.get("description"))
        market_key = str(entry.get("market"))
        market = markets.get(market_key)
        record = dict(previous.get(key) or {})
        record.update({"id": key, "market": market_key, "family": entry.get("family"),
                       "description": entry.get("description"), "book_status": entry.get("status"),
                       "last_run": _iso(now)})
        if market is None:
            record.update({"available": False, "reason": f"no broker bars for {market_key}"})
            report["strategies"][key] = record
            continue
        # The start instant is fixed the first time a strategy is seen and never moves afterwards.
        start_at = pd.Timestamp(record.get("start_at") or _iso(now), tz="UTC")
        record["start_at"] = _iso(start_at)
        try:
            outcome = simulate_since(market, entry["spec"], start_at)
        except Exception as exc:          # one strategy failing must not stop the rest
            record.update({"available": False, "reason": f"simulation failed: {exc}"})
            report["strategies"][key] = record
            continue

        stats = forward_evidence.score(outcome["r_values"])
        prior = (entry.get("latest") or {}).get("expectancy_r")
        record.update({
            "available": True, "reason": None,
            "bars_watched": outcome["bars_watched"],
            "data_end": _iso(market.ind.times.iloc[-1]),
            "closed_trades": outcome["trades"][-40:],
            "stats": stats,
            "confidence": forward_evidence.confidence(stats, prior),
            "tier": forward_evidence.tier(stats, splits_positive=None, beats_inverse=None),
            # net_r is computed per trade above, so the window total comes straight from it; calling
            # crt_lab.r_stats here would need the entry_idx and stop array that are deliberately
            # stripped before the trades are stored.
            "this_window_r": {"trades": stats["trades"], "total_r": stats["total_r"],
                              "best_r": round(max(outcome["r_values"]), 3) if outcome["r_values"] else None,
                              "worst_r": round(min(outcome["r_values"]), 3) if outcome["r_values"] else None},
            "cost_model": market.info["cost_model"]})
        report["strategies"][key] = record

    with_trades = [r for r in report["strategies"].values() if (r.get("stats") or {}).get("trades")]
    report["totals"] = {
        "tracked": len(entries),
        "with_forward_trades": len(with_trades),
        "closed_forward_trades": sum(r["stats"]["trades"] for r in with_trades),
        "positive_expectancy": sum(1 for r in with_trades if (r["stats"]["expectancy_r"] or 0) > 0),
    }
    lab.LAB_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(report, indent=1, default=lambda v: v.item() if hasattr(v, "item") else str(v))
    (lab.LAB_DIR / "shadow_book.json").write_text(payload, encoding="utf-8") if state_path is None \
        else open(state_path, "w", encoding="utf-8").write(payload)
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Shadow forward tests for the strategy book (never trades)")
    parser.add_argument("command", choices=["run", "status"])
    parser.add_argument("--market", default=None, help="only this market, e.g. XAUUSD:4h")
    args = parser.parse_args(argv)

    if args.command == "status":
        report = forward_evidence._read_json_or_none(STATE_PATH) or {}
        if not report:
            print("no shadow book yet; run it once")
            return 0
    else:
        report = run(market_filter=args.market)

    rows = sorted(report["strategies"].values(),
                  key=lambda r: -((r.get("stats") or {}).get("expectancy_r") or -99))
    print(f"Shadow book as at {report['generated_at']}   places_orders={report['places_orders']}")
    print(f"tracking {report['totals']['tracked']} strategies the book keeps; "
          f"{report['totals']['with_forward_trades']} have forward trades, "
          f"{report['totals']['positive_expectancy']} of those are positive so far\n")
    for r in rows[:25]:
        stats = r.get("stats") or {}
        if not r.get("available"):
            print(f"  {r['market']:12s} {str(r['description'])[:46]:46s} unavailable: {r.get('reason')}")
            continue
        tier_name = (r.get("tier") or {}).get("name", "?")
        conf = r.get("confidence") or {}
        # An expectancy on a handful of trades, with one outlier, reads as a finding when it is not.
        # Anything whose interval still spans zero is marked, so the number is never quoted bare.
        spans = conf.get("interval_spans_zero")
        flag = "  NOT YET READABLE (interval spans zero)" if spans or stats.get("trades", 0) < 5 else ""
        low, high = (conf.get("interval") or [None, None])
        band = f"[{low:+.2f},{high:+.2f}]" if low is not None else "[--]"
        print(f"  {r['market']:12s} {str(r['description'])[:40]:40s} "
              f"n {stats.get('trades', 0):3d} "
              f"exp {stats.get('expectancy_r') if stats.get('expectancy_r') is not None else 0:+.3f} R "
              f"95% {band:>16s} | {tier_name}{flag}")
    print(f"\n  total closed shadow trades: {report['totals']['closed_forward_trades']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
