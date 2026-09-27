"""The owner's 1pm UK Tokyo-range breakout continuation. Research only, never trades.

    python -m src.tokyo_breakout run [--symbol XAUUSD] [--timeframe 15m]

THE RULE AS GIVEN
-----------------
"uk time 1pm wait for breakout Tokyo zone high or low and continuation" - so: mark the Tokyo session's
high and low, wait until 13:00 UK, and take the first break of either side in the direction of the break.

TWO THINGS THAT DECIDE WHETHER THE NUMBER MEANS ANYTHING, both handled rather than assumed
------------------------------------------------------------------------------------------
**1pm UK is not a fixed UTC hour.** London runs BST (UTC+1) for about seven months and GMT (UTC) for the
other five, so "1pm UK" is 12:00 UTC in summer and 13:00 UTC in winter. Broker bars arrive stamped UTC, so
the trigger is found by converting each stamp to `Europe/London` and reading the local hour - never by
picking one UTC hour. Getting this wrong would put the entry an hour early for half the year and quietly
average two different strategies together.

**The Tokyo zone has no single definition**, so it is swept rather than chosen: 00:00-06:00 UTC (Tokyo cash
hours) and 00:00-08:00 UTC (the wider Asian session). Both end well before the earliest possible trigger
(12:00 UTC), so the range is always complete before it is used - there is no lookahead in it, and that is a
property of the clock rather than of careful coding. Windows that cross midnight (e.g. 23:00-08:00, adding
the Sydney open) are deliberately NOT included: they would need the previous UTC day's bars and the extra
bookkeeping is a place for a bug rather than an insight.

WHY 15-MINUTE BARS
------------------
Fine enough to place a 13:00 trigger and a session range; coarse enough that the stop - the full width of
the Tokyo range - is many times a single candle. `.claude/memory/LESSONS.md` records the opposite mistake
twice: a stop smaller than one bar makes the engine's stop-first convention decide the trade instead of the
market, and a fair 1:1 bet then measures 37 % instead of 50 %.

THE STRUCTURE
-------------
* trigger window: 13:00 UK until 20:00 UK, so a break has the London afternoon and the New York morning to
  continue. One trade per day at most - the FIRST qualifying break.
* direction: break above the Tokyo high goes long, below the Tokyo low goes short. That is what
  "continuation" means here: with the break, not against it.
* stop: the OPPOSITE end of the Tokyo range. The range is the structure being broken, so price travelling
  all the way back through it is the breakout having failed. This also makes the risk unit unambiguous.
* target: R multiples of that same range.
* exit: stop, target, or the end of the trigger day.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from itertools import product

import numpy as np
import pandas as pd

from . import strategy_lab as lab

# Swept, because "the Tokyo zone" is genuinely ambiguous. Both are (start_hour, end_hour) in UTC and both
# close before the earliest trigger at 12:00 UTC.
TOKYO_WINDOWS = {"tokyo_0_6": (0, 6), "asia_0_8": (0, 8)}
TRIGGER_FROM_UK_HOUR = 13        # the owner's 1pm, read in Europe/London so BST and GMT both land right
TRIGGER_UNTIL_UK_HOUR = 20       # stop looking for a break after this London hour
REWARD_RATIOS = (1.0, 2.0, 3.0)  # R multiples of the Tokyo range
MAX_BARS = 32                    # 32 x 15m = 8 h, so a trade cannot outlive its own day


def _tokyo_levels(times: pd.Series, high: np.ndarray, low: np.ndarray, window: tuple) -> tuple:
    """That day's Tokyo high and low, carried forward onto every bar of the same UTC date.

    The Tokyo window closes at or before 08:00 UTC and the trigger cannot fire before 12:00 UTC, so a bar
    that uses these levels is always looking at a range that finished hours earlier. Causality here comes
    from the clock, not from a shift().
    """
    start_hour, end_hour = window
    utc_date = times.dt.strftime("%Y-%m-%d").to_numpy()
    hour = times.dt.hour.to_numpy()
    in_tokyo = (hour >= start_hour) & (hour < end_hour)

    frame = pd.DataFrame({"date": utc_date,
                          "high": np.where(in_tokyo, high, np.nan),
                          "low": np.where(in_tokyo, low, np.nan)})
    grouped = frame.groupby("date", sort=False)
    return (grouped["high"].transform("max").to_numpy(dtype=float),
            grouped["low"].transform("min").to_numpy(dtype=float))


def tokyo_breakout_orders(ind: lab.Indicators, spec: dict):
    """side / stop / target. Long on a close above the Tokyo high, short on a close below its low."""
    p = spec["params"]
    window = TOKYO_WINDOWS.get(str(p.get("tokyo_window") or "asia_0_8"), (0, 8))
    rr = float(p.get("rr") or 2.0)
    n = len(ind.c)

    times = ind.times
    if getattr(times.dt, "tz", None) is None:
        times = times.dt.tz_localize("UTC")
    london_hour = times.dt.tz_convert("Europe/London").dt.hour.to_numpy()
    utc_date = times.dt.strftime("%Y-%m-%d").to_numpy()

    tokyo_high, tokyo_low = _tokyo_levels(times, ind.h, ind.l, window)

    in_trigger = (london_hour >= TRIGGER_FROM_UK_HOUR) & (london_hour < TRIGGER_UNTIL_UK_HOUR)
    with np.errstate(invalid="ignore"):
        broke_up = in_trigger & np.isfinite(tokyo_high) & (ind.c > tokyo_high)
        broke_down = in_trigger & np.isfinite(tokyo_low) & (ind.c < tokyo_low)

    # ONE trade per day: keep only the first qualifying bar of each UTC date.
    side = np.zeros(n, dtype=int)
    candidate = broke_up | broke_down
    seen: set = set()
    for i in np.flatnonzero(candidate):
        day = utc_date[i]
        if day in seen:
            continue
        seen.add(day)
        side[i] = 1 if broke_up[i] else -1

    wanted = str(p.get("side") or "")
    if wanted in ("long", "short"):
        keep = 1 if wanted == "long" else -1
        side = np.where(side == keep, keep, 0).astype(int)

    # The risk unit comes from the BASE signal: a long that broke the high risks back to the Tokyo low.
    with np.errstate(invalid="ignore"):
        base_stop = np.where(side == 1, tokyo_low, np.where(side == -1, tokyo_high, np.nan))
        risk = np.abs(ind.c - base_stop)

    # The inverse control MIRRORS the stop at the same distance instead of reusing the range edge.
    # Flipping the side first and then reading the edge is what made this control worthless: an inverted
    # long becomes a short whose stop would sit at the Tokyo high, which price has ALREADY broken above, so
    # the geometry check below rejected it and the control took ZERO trades on all six variants - a free
    # pass, and the same vacuous-control bug already recorded in direction_sweep on 27 Sep 2026. Mirroring
    # keeps the timing, the count and the risk size identical and changes only the direction, which is what
    # a control has to be.
    if p.get("inverse"):
        side = -side
        with np.errstate(invalid="ignore"):
            stop = np.where(side == 1, ind.c - risk, np.where(side == -1, ind.c + risk, np.nan))
    else:
        stop = base_stop

    with np.errstate(invalid="ignore"):
        target = np.where(side == 1, ind.c + rr * risk,
                          np.where(side == -1, ind.c - rr * risk, np.nan))
        usable = (np.isfinite(stop) & np.isfinite(risk) & (risk > 0)
                  & np.where(side == 1, stop < ind.c, np.where(side == -1, stop > ind.c, False)))
    side = np.where(usable, side, 0).astype(int)
    return side, stop, target


def _register() -> None:
    lab.ORDER_BUILDERS["tokyo_breakout"] = tokyo_breakout_orders


_register()


def breakout_variants(symbol: str = "XAUUSD", timeframe: str = "15m") -> list:
    """Six: two Tokyo window definitions x three reward ratios. Declared before any result."""
    out = []
    for window, rr in product(TOKYO_WINDOWS, REWARD_RATIOS):
        out.append({
            "family": "tokyo_breakout",
            "params": {"symbol": symbol, "timeframe": timeframe, "tokyo_window": window, "rr": rr},
            "exits": {"stop": "fixed", "sl_atr": 0.0, "rr": 0.0, "trail_atr": 0.0,
                      "max_bars": MAX_BARS, "swing_lookback": 0},
            "description": (f"from 13:00 UK, first break of the {window} Tokyo range, with the break, "
                            f"stop at the far end of the range, target {rr:g}R"),
            "variant": f"{window}|rr{rr:g}",
        })
    return out


def run(symbol: str = "XAUUSD", timeframe: str = "15m", side: str = "both") -> dict:
    from .mtf_data import load_bars

    bars = load_bars(symbol, timeframe, source="app")
    if bars is None or bars.empty:
        return {"available": False, "reason": "no broker bars"}

    registry = lab.load_registry()
    key = f"{symbol}:{timeframe}"
    market = lab.Market(symbol, timeframe, bars,
                        boundaries=(registry["markets"].get(key) or {}).get("boundaries"), swap=True)

    rows = []
    for spec in breakout_variants(symbol, timeframe):
        if side in ("long", "short"):
            spec = {**spec, "params": {**spec["params"], "side": side}}
        try:
            trades = market.simulate(spec, "holdout")
        except Exception as exc:                          # noqa: BLE001
            rows.append({"variant": spec["variant"], "error": f"{type(exc).__name__}: {exc}"[:110]})
            continue
        summary = market.summary("holdout", trades)
        inverse_spec = {**spec, "params": {**spec["params"], "inverse": True}}
        try:
            inverse = market.summary("holdout", market.simulate(inverse_spec, "holdout"))
        except Exception:                                 # noqa: BLE001
            inverse = {}
        longs = sum(1 for t in trades if t["side"] == "BUY")
        rows.append({
            "variant": spec["variant"], "trades": summary.get("trades"),
            "long_trades": longs, "short_trades": (summary.get("trades") or 0) - longs,
            "win_rate_pct": summary.get("win_rate_pct"),
            "profit_factor": summary.get("profit_factor"),
            "expectancy_r": summary.get("expectancy_r"),
            "expectancy_r_bound": summary.get("expectancy_r_bound"),
            "net_pct": summary.get("total_return_pct"),
            "max_drawdown_pct": summary.get("max_drawdown_pct"),
            "ambiguous_exits": summary.get("ambiguous_exits"),
            "buy_and_hold_pct": summary.get("buy_and_hold_pct"),
            "inverse_net_pct": inverse.get("total_return_pct"),
            "inverse_trades": inverse.get("trades"),
            "insufficient_evidence": (summary.get("trades") or 0) < 100,
            "beats_inverse": bool((inverse.get("trades") or 0) > 0
                                  and inverse.get("total_return_pct") is not None
                                  and inverse["total_return_pct"] < 0
                                  and (summary.get("total_return_pct") or -999) > inverse["total_return_pct"]),
        })

    report = {"available": True, "market": key, "side": side,
              "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
              "configurations_tried": len(rows), "info": market.info, "rows": rows}
    lab.LAB_DIR.mkdir(parents=True, exist_ok=True)
    path = lab.LAB_DIR / f"tokyo_breakout_{symbol}_{timeframe}_{datetime.now(timezone.utc):%Y%m%d}.json"
    path.write_text(json.dumps(report, indent=1, default=lambda v: v.item() if hasattr(v, "item") else str(v)),
                    encoding="utf-8")
    report["path"] = str(path)
    return report


def print_breakout_report(report: dict) -> None:
    info = report.get("info") or {}
    print("PRE-FLIGHT (.claude/skills/measure) - read before any number below")
    print(f"  units       {report['market'].split(':')[0]} quotes to 0.01; round-trip cost "
          f"{info.get('cost_round_trip_pct')} as a FRACTION of price")
    print( "  clock       1pm UK is read in Europe/London, so BST (12:00 UTC) and GMT (13:00 UTC) both land on "
           "the owner's 1pm. Trigger 13:00-20:00 London, one trade per day.")
    print( "  scale       stop is the FULL width of the Tokyo range, many times a 15m candle, so the engine's "
           "stop-first convention is not deciding these trades")
    print( "  causality   the Tokyo window closes by 08:00 UTC and the trigger cannot fire before 12:00 UTC, so "
           "the range is complete hours before it is used; fill is the NEXT bar's open")
    print( "  control     every variant is run inverted on the same bars; an inverse that made money or took no "
           "trades is not a control")
    print(f"  provenance  broker bars via the app, {info.get('bars')} bars, {info.get('data_start')} -> "
          f"{info.get('data_end')}; holdout {(info.get('periods') or {}).get('holdout')}")
    print(f"  trials      {report.get('configurations_tried')} configurations. Any row under 100 closed trades "
          f"is INSUFFICIENT EVIDENCE whatever its profit factor.")
    print()
    print(f"  {'variant':18} {'trades':>7} {'L/S':>9} {'win%':>6} {'PF':>6} {'exp_r':>8} {'net%':>8} "
          f"{'dd%':>7} {'inv%':>8} {'amb':>4} evidence")
    for r in sorted(report.get("rows") or [], key=lambda x: -(x.get("net_pct") or -999)):
        if r.get("error"):
            print(f"  {r['variant']:18} {'-':>7}  {r['error']}")
            continue
        ev = "INSUFFICIENT" if r.get("insufficient_evidence") else ("ok" if r.get("beats_inverse") else "no control")
        print(f"  {r['variant']:18} {r.get('trades'):>7} {str(r.get('long_trades')) + '/' + str(r.get('short_trades')):>9} "
              f"{r.get('win_rate_pct'):>6} {str(r.get('profit_factor')):>6} {str(r.get('expectancy_r')):>8} "
              f"{r.get('net_pct'):>8} {r.get('max_drawdown_pct'):>7} {str(r.get('inverse_net_pct')):>8} "
              f"{str(r.get('ambiguous_exits')):>4} {ev}")
    if report.get("path"):
        print(f"\n  written to {report['path']}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="1pm UK Tokyo-range breakout. Research only, never trades.")
    sub = parser.add_subparsers(dest="command", required=True)
    runner = sub.add_parser("run", help="run the declared grid on the locked holdout")
    runner.add_argument("--symbol", default="XAUUSD")
    runner.add_argument("--timeframe", default="15m")
    runner.add_argument("--side", default="both", choices=("both", "long", "short"))
    args = parser.parse_args(argv)

    report = run(args.symbol, args.timeframe, args.side)
    if not report.get("available"):
        print(report.get("reason"))
        return 1
    print_breakout_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
