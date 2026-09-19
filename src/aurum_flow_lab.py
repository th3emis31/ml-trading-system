"""Aurum Flow (Trade Smart FX Tools) entry-rule replica: research only, never trades.

The rules, what the source actually does versus what the vendor advertises, and the pre-declared
variant grid are in ``strategies/aurum_flow.md``. Read that first: the supply/demand zones the
product page leads with are drawn on the chart and never consulted by any trading decision, and
what decides trades is a trendline breakout with an SMA filter and a fixed 2100/1800 point
stop/target.

Replicated here (from ``Aurum Flow.mq4`` v7.05):

* ``TL_HIGH`` through the two highest 3-bar swing highs of the last ``structure_depth`` bars,
  at least ``spacing`` bars apart, rebuilt every ``refresh_bars`` bars; ``TL_LOW`` mirrored.
* Buy when the last two closed bars both reached at or above ``TL_HIGH``; sell mirrored.
* SMA(``ma_period``) filter on the previous bar's close.
* A stop order ``entry_points`` beyond the signal bar's extreme, expiring after
  ``expiry_minutes``.
* Fixed stop and target in points, and one position at a time.

NOT replicated, deliberately: the ``EnableRecoveryMode2`` cascade (up to 60 same-direction
positions with no stop loss) and ``CheckMultiDealBreakeven``. The shared engine holds one
position at a time, so a martingale cannot be expressed in it. This module measures the entry
edge on its own, which is the thing the cascade would otherwise hide.

    python -m src.aurum_flow_lab run [--symbols XAUUSD] [--timeframes 15m 1h]
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from itertools import product

import numpy as np
import pandas as pd

from . import crt_lab
from . import strategy_lab as lab

# The EA's own defaults, kept as named constants so a reader can check them against the source.
STRUCTURE_DEPTH = 200      # StructureDepth
SPACING = 100              # StructureSpacing
REFRESH_BARS = 40          # StructureRefreshBars
ENTRY_POINTS = 130         # PendingOrderDistance
EXPIRY_MINUTES = 60        # PendingExpiryMinutes
SL_POINTS, TP_POINTS = 2100, 1800   # SL, TP
MAX_TRADE_DAYS = 5         # MaxTradeDurationDays
BLOCKED_MONTHS = (11, 12)  # TradeInNovember / TradeInDecember, both false by default
# P() in the EA: Point x 10 when Digits is 3 or 5, else Point. Gold is quoted to 2 or 3 decimals,
# so both branches give 0.01 of a dollar per point. Bitcoin is quoted to 2 decimals, same.
POINT_VALUE = 0.01


def swing_highs(h: np.ndarray) -> np.ndarray:
    """IsSwingHigh: a 3-bar fractal. Index i is a swing when it tops both neighbours."""
    out = np.zeros(len(h), dtype=bool)
    out[1:-1] = (h[1:-1] > h[:-2]) & (h[1:-1] > h[2:])
    return out


def swing_lows(l: np.ndarray) -> np.ndarray:
    out = np.zeros(len(l), dtype=bool)
    out[1:-1] = (l[1:-1] < l[:-2]) & (l[1:-1] < l[2:])
    return out


def _two_extremes(values: np.ndarray, is_swing: np.ndarray, start: int, end: int,
                  spacing: int, want_high: bool):
    """FindTwoHighPeaks / FindTwoLowPeaks over bars ``start..end`` inclusive.

    Picks the most extreme swing, then the most extreme other swing at least ``spacing`` bars
    away from it — the EA's own two-pass fallback. Returns (older_index, newer_index) or None.
    """
    candidates = [i for i in range(start, end + 1) if is_swing[i]]
    if not candidates:
        return None
    best = max(candidates, key=lambda i: values[i]) if want_high else min(candidates, key=lambda i: values[i])
    others = [i for i in candidates if abs(i - best) >= spacing]
    if not others:
        return None
    second = max(others, key=lambda i: values[i]) if want_high else min(others, key=lambda i: values[i])
    return (min(best, second), max(best, second))


def _line(a: int, pa: float, b: int, pb: float, at: np.ndarray) -> np.ndarray:
    """The ray through (a, pa) and (b, pb), valued at bar indices ``at``."""
    if b == a:
        return np.full(len(at), pa, dtype=float)
    slope = (pb - pa) / (b - a)
    return pa + slope * (at - a)


def trendlines(ind: lab.Indicators, depth: int, spacing: int, refresh: int):
    """Per bar, the value of TL_HIGH and TL_LOW as the EA would have had them drawn.

    The EA rebuilds both lines every ``refresh`` bars from the previous ``depth`` closed bars and
    leaves them in place until the next rebuild, so between rebuilds the line is extrapolated
    forward. Only bars strictly before the rebuild are used, so nothing looks ahead.
    """
    def build():
        n = len(ind.h)
        upper, lower = np.full(n, np.nan), np.full(n, np.nan)
        sw_h, sw_l = swing_highs(ind.h), swing_lows(ind.l)
        idx = np.arange(n, dtype=float)
        anchor_high = anchor_low = None
        for bar in range(n):
            if bar >= depth + 2 and (anchor_high is None or bar % refresh == 0):
                window_start, window_end = bar - depth, bar - 2   # candles 2..depth back, as the EA scans
                found_h = _two_extremes(ind.h, sw_h, window_start, window_end, spacing, True)
                found_l = _two_extremes(ind.l, sw_l, window_start, window_end, spacing, False)
                if found_h is not None and found_l is not None:
                    anchor_high, anchor_low = found_h, found_l
            if anchor_high is None or anchor_low is None:
                continue
            a, b = anchor_high
            upper[bar] = _line(a, ind.h[a], b, ind.h[b], idx[bar:bar + 1])[0]
            a, b = anchor_low
            lower[bar] = _line(a, ind.l[a], b, ind.l[b], idx[bar:bar + 1])[0]
        return upper, lower

    return ind._cached(("aurum_trendlines", depth, spacing, refresh), build)


def aurum_orders(ind: lab.Indicators, spec: dict):
    """side / stop / target / entry arrays for the replica. Entry is a resting stop order."""
    p = spec["params"]
    n = len(ind.c)
    side = np.zeros(n, dtype=int)
    stop = np.full(n, np.nan)
    target = np.full(n, np.nan)
    entry = np.full(n, np.nan)

    upper, lower = trendlines(ind, p["structure_depth"], p["spacing"], p["refresh_bars"])
    ma = ind.sma(p["ma_period"]) if p["ma_period"] else None
    months = pd.DatetimeIndex(ind.times).month.to_numpy()
    step = max(1, int(round((ind.times.iloc[1] - ind.times.iloc[0]).total_seconds() / 60))) if n > 1 else 1
    fill_window = max(1, int(p["expiry_minutes"] // step))
    sl_dist = p["sl_points"] * POINT_VALUE
    tp_dist = p["tp_points"] * POINT_VALUE

    # Decision at the open of bar t, from the two bars closed before it (the EA's candle 1 and 2).
    for t in range(2, n - 1):
        one, two = t - 1, t - 2
        if not np.isfinite(upper[one]) or not np.isfinite(upper[two]):
            continue
        buy = ind.h[one] >= upper[one] and ind.h[two] >= upper[two]
        sell = ind.l[one] <= lower[one] and ind.l[two] <= lower[two]
        if buy == sell:               # neither, or both at once: the EA skips
            continue
        if p["block_nov_dec"] and months[t] in BLOCKED_MONTHS:
            continue
        if ma is not None:
            level = ma[one]
            if not np.isfinite(level):
                continue
            if buy and ind.c[one] < level:
                continue
            if sell and ind.c[one] > level:
                continue

        direction = 1 if buy else -1
        level = (ind.h[one] + p["entry_points"] * POINT_VALUE) if buy else (ind.l[one] - p["entry_points"] * POINT_VALUE)
        # The inverse baseline trades the identical signal bars and the identical levels the other
        # way round, with the stop and target mirrored. It answers "is this edge, or is it just
        # gold's direction?", because a long-biased breakout in a rising market flatters itself.
        if p.get("inverse"):
            direction = -direction
        # The stop order rests from bar t and expires after ``expiry_minutes``; it fills on the
        # first bar in that window that trades through the level.
        fill = None
        for j in range(t, min(t + fill_window, n)):
            if (buy and ind.h[j] >= level) or (sell and ind.l[j] <= level):
                fill = j
                break
        if fill is None or fill == 0:
            continue
        row = fill - 1
        if side[row] != 0:
            continue
        side[row] = direction
        entry[row] = level
        stop[row] = level - direction * sl_dist
        target[row] = level + direction * tp_dist
    return side, stop, target, entry


lab.ORDER_BUILDERS["aurum_flow"] = aurum_orders


def aurum_variants(symbol: str, timeframe: str, max_bars: int) -> list[dict]:
    """The pre-declared grid from strategies/aurum_flow.md: 8 per market and timeframe."""
    out = []
    for ma_period, (sl_points, tp_points), block in product((600, 0), ((SL_POINTS, TP_POINTS), (SL_POINTS, 3150)), (True, False)):
        rr = tp_points / sl_points
        name = f"ma{ma_period or 'off'}|rr{rr:.2f}|{'novdec_off' if block else 'all_months'}"
        out.append({
            "family": "aurum_flow",
            "params": {"symbol": symbol, "timeframe": timeframe, "structure_depth": STRUCTURE_DEPTH,
                       "spacing": SPACING, "refresh_bars": REFRESH_BARS, "ma_period": ma_period,
                       "entry_points": ENTRY_POINTS, "expiry_minutes": EXPIRY_MINUTES,
                       "sl_points": sl_points, "tp_points": tp_points, "block_nov_dec": block},
            "exits": {"stop": "fixed", "sl_atr": 0.0, "rr": 0.0, "trail_atr": 0.0,
                      "max_bars": max_bars, "swing_lookback": 0},
            "description": f"Aurum Flow replica {name}",
            "variant": name,
        })
    return out


# The second, wider grid declared in strategies/aurum_flow.md: does ANY version of the mechanism
# clear the bar, or only the vendor's settings fail? Every pair here has reward:risk of 1 or
# better, because the shipped 2100/1800 needs a 53.8 % win rate before costs.
SEARCH_DEPTHS = (100, 200, 400)
SEARCH_MA = (0, 200, 600)
SEARCH_EXITS = ((2100, 1800), (2100, 4200), (1400, 2800), (1000, 3000))
SEARCH_ENTRY = (0, ENTRY_POINTS)


def search_variants(symbol: str, timeframe: str, max_bars: int) -> list[dict]:
    """72 per market and timeframe. November and December are included on purpose."""
    out = []
    for depth, ma_period, (sl_points, tp_points), entry_points in product(
            SEARCH_DEPTHS, SEARCH_MA, SEARCH_EXITS, SEARCH_ENTRY):
        name = f"d{depth}|ma{ma_period or 'off'}|{sl_points}/{tp_points}|e{entry_points}"
        out.append({
            "family": "aurum_flow",
            "params": {"symbol": symbol, "timeframe": timeframe, "structure_depth": depth,
                       "spacing": SPACING, "refresh_bars": REFRESH_BARS, "ma_period": ma_period,
                       "entry_points": entry_points, "expiry_minutes": EXPIRY_MINUTES,
                       "sl_points": sl_points, "tp_points": tp_points, "block_nov_dec": False},
            "exits": {"stop": "fixed", "sl_atr": 0.0, "rr": 0.0, "trail_atr": 0.0,
                      "max_bars": max_bars, "swing_lookback": 0},
            "description": f"Aurum Flow mechanism {name}",
            "variant": name,
        })
    return out


def run(symbols=("XAUUSD",), timeframes=("15m", "1h"), grid: str = "shipped") -> dict:
    from .mtf_data import load_bars

    registry = lab.load_registry()
    report = {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
              "grid": grid,
              "source": "Aurum Flow.mq4 v7.05 (Trade Smart FX Tools), entry rules only",
              "not_replicated": "EnableRecoveryMode2 cascade (up to 60 same-direction positions, no stop loss) "
                                "and CheckMultiDealBreakeven; the engine holds one position at a time",
              "markets": {}}
    for symbol, timeframe in product(symbols, timeframes):
        key = f"{symbol}:{timeframe}"
        bars = load_bars(symbol, timeframe, source="app")
        if bars is None or bars.empty:
            report["markets"][key] = {"error": "no broker bars"}
            continue
        minutes = int(round((pd.to_datetime(bars["datetime"]).iloc[1] - pd.to_datetime(bars["datetime"]).iloc[0]).total_seconds() / 60))
        max_bars = max(8, int(MAX_TRADE_DAYS * 24 * 60 // max(minutes, 1)))
        market = lab.Market(symbol, timeframe, bars,
                            boundaries=(registry["markets"].get(key) or {}).get("boundaries"), swap=True)
        specs = (search_variants if grid == "search" else aurum_variants)(symbol, timeframe, max_bars)
        records = []
        for spec in specs:
            record = lab.evaluate_candidate(market, spec, with_holdout=True)
            orders = lab.strategy_orders(market.ind, spec)
            for split in ("search", "validation", "holdout"):
                record[f"{split}_r"] = crt_lab.r_stats(market.simulate(spec, split, orders), orders[1])
            record["variant"] = spec["variant"]
            record["signals"] = int(np.count_nonzero(orders[0]))
            records.append(record)
        sr_variance = lab._variance([[r["validation_sr"], r["validation"]["trades"]]
                                     for r in records if r["validation_sr"] is not None])
        for record in records:
            record["holdout_verdict"] = lab.holdout_verdict(record, len(specs), sr_variance)
        validated = [r for r in records if r["validated"]]
        report["markets"][key] = {"info": market.info, "n_trials": len(specs), "max_bars": max_bars,
                                 "variants": records,
                                 "validated": [r["variant"] for r in validated],
                                 "passed_holdout": [r["variant"] for r in records
                                                    if (r.get("holdout_verdict") or {}).get("passed")]}
    lab.LAB_DIR.mkdir(parents=True, exist_ok=True)
    # The markets are in the name: two runs on the same day for different timeframes used to
    # overwrite each other, which silently destroyed the first run's evidence.
    scope = "-".join(sorted(symbols)) + "_" + "-".join(timeframes)
    path = lab.LAB_DIR / f"aurum_flow_{grid}_{scope}_{datetime.now(timezone.utc).strftime('%Y%m%d')}.json"
    path.write_text(json.dumps(report, indent=1, default=lambda v: v.item() if hasattr(v, "item") else str(v)),
                    encoding="utf-8")
    report["path"] = str(path)
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Aurum Flow entry-rule replica (research only)")
    parser.add_argument("command", choices=["run"])
    parser.add_argument("--symbols", nargs="+", default=["XAUUSD"])
    parser.add_argument("--timeframes", nargs="+", default=["15m", "1h"])
    parser.add_argument("--grid", choices=["shipped", "search"], default="shipped",
                        help="shipped = the EA's own 8 variants; search = the wider 72-variant grid")
    args = parser.parse_args(argv)
    report = run(tuple(args.symbols), tuple(args.timeframes), grid=args.grid)
    print(f"saved {report['path']}")
    for key, market in report["markets"].items():
        if market.get("error"):
            print(f"{key}: {market['error']}")
            continue
        print(f"\n{key}  ({market['info'].get('bars')} bars, {market['n_trials']} trials)")
        for record in market["variants"]:
            hold = record.get("holdout") or {}
            print(f"  {record['variant']:32s} signals {record['signals']:4d}  "
                  f"holdout trades {hold.get('trades', 0):4d}  PF {hold.get('profit_factor') or 0:.3f}  "
                  f"net {hold.get('total_return_pct') or 0:+7.2f}%  verdict "
                  f"{'PASS' if (record.get('holdout_verdict') or {}).get('passed') else 'fail'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
