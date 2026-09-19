"""The owner's Thursday/Friday/Monday rule, measured and then backtested. Research only, never trades.

The rule and the pre-declared method are in ``strategies/weekly_friday_thursday.md``. In short: when
Friday fails to take Thursday's high, does Monday come down to Friday's low? And the mirror.

The one thing that is easy to get wrong here is the calendar. This broker's daily candle opens at
21:00 UTC, so every bar is stamped the day *before* the session it covers. ``session_frame`` handles
that once, and everything else works in sessions rather than stamps.

    python -m src.weekly_structure report [--symbols XAUUSD BTCUSD]
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd

from .candle_patterns import _wilson

BEARISH, BULLISH = "bearish", "bullish"


def session_frame(bars: pd.DataFrame) -> pd.DataFrame:
    """Daily bars labelled by the session they actually cover, not by their opening stamp."""
    frame = bars.reset_index(drop=True).copy()
    stamps = pd.to_datetime(frame["datetime"], utc=True)
    frame["session_date"] = (stamps + pd.Timedelta(days=1)).dt.date
    frame["weekday"] = (stamps + pd.Timedelta(days=1)).dt.day_name()
    return frame


def weeks(frame: pd.DataFrame) -> list:
    """Consecutive Thursday, Friday, Monday triplets. Weeks missing any of the three are skipped."""
    rows = frame.to_dict("records")
    by_date = {r["session_date"]: i for i, r in enumerate(rows)}
    out = []
    for i, row in enumerate(rows):
        if row["weekday"] != "Thursday":
            continue
        friday = rows[i + 1] if i + 1 < len(rows) else None
        monday = rows[i + 2] if i + 2 < len(rows) else None
        if not friday or not monday:
            continue
        if friday["weekday"] != "Friday" or monday["weekday"] != "Monday":
            continue          # a holiday broke the week; skipped rather than patched
        out.append({"thursday": row, "friday": friday, "monday": monday})
    return out


def classify_week(week: dict) -> dict:
    """Which conditions this week meets, and whether Monday did what the rule says."""
    th, fr, mo = week["thursday"], week["friday"], week["monday"]
    return {
        "session": str(mo["session_date"]),
        # The Monday session's exact bounds, taken from the daily bar's own stamp. Selecting intraday
        # bars by a time window needs no assumption about the broker's UTC offset, which shifts with
        # daylight saving; grouping them by a date arithmetic instead silently misfiled them.
        "session_start": pd.Timestamp(mo["datetime"]),
        # Friday failed to take Thursday's high -> the rule says Monday visits Friday's low
        "bearish_setup": bool(fr["high"] <= th["high"]),
        "bearish_hit": bool(mo["low"] <= fr["low"]),
        # Friday failed to take Thursday's low -> the rule says Monday visits Friday's high
        "bullish_setup": bool(fr["low"] >= th["low"]),
        "bullish_hit": bool(mo["high"] >= fr["high"]),
        "friday_low": float(fr["low"]), "friday_high": float(fr["high"]),
        "monday_open": float(mo["open"]), "monday_low": float(mo["low"]), "monday_high": float(mo["high"]),
        "monday_close": float(mo["close"]),
    }


def tendency(rows: list, direction: str) -> dict:
    """Hit rate when the condition holds, against the rate on every Monday regardless."""
    setup_key, hit_key = f"{direction}_setup", f"{direction}_hit"
    hits_all = [r[hit_key] for r in rows]
    base = float(np.mean(hits_all)) if hits_all else float("nan")
    conditioned = [r[hit_key] for r in rows if r[setup_key]]
    n = len(conditioned)
    if n == 0:
        return {"direction": direction, "weeks": 0, "why": "the condition never occurred"}
    rate = float(np.mean(conditioned))
    low, high = _wilson(int(round(rate * n)), n)
    return {"direction": direction, "weeks": n, "all_weeks": len(rows),
            "hit_rate": round(rate, 4), "base_rate": round(base, 4),
            "edge": round(rate - base, 4),
            "ci_low": round(low, 4), "ci_high": round(high, 4),
            "beats_base_with_confidence": bool(low > base),
            "why": (f"Monday reached Friday's {'low' if direction == BEARISH else 'high'} in "
                    f"{rate:.1%} of the {n} weeks where the condition held, against {base:.1%} of all "
                    f"{len(rows)} weeks; the 95 % interval is [{low:.3f}, {high:.3f}] and the base rate "
                    f"is {'outside' if low > base else 'inside'} it")}


def simulate_rule(rows: list, direction: str, stop_mode: str, cost_pct: float, atr_by_session: dict) -> dict:
    """Enter at Monday's open, target Friday's extreme, with one of two pre-declared stops."""
    setup_key = f"{direction}_setup"
    side = -1 if direction == BEARISH else 1
    trades = []
    for row in rows:
        if not row[setup_key]:
            continue
        entry = row["monday_open"]
        target = row["friday_low"] if direction == BEARISH else row["friday_high"]
        if stop_mode == "structure":
            stop = row["friday_high"] if direction == BEARISH else row["friday_low"]
        else:
            atr = atr_by_session.get(row["session"])
            if atr is None or not math.isfinite(atr) or atr <= 0:
                continue
            stop = entry + atr if direction == BEARISH else entry - atr
        risk = abs(entry - stop)
        if risk <= 0 or side * (target - entry) <= 0:
            continue          # the target is already behind the entry, or the stop is at it
        # Within Monday alone, both target and stop can be touched; the pessimistic reading is that
        # the stop went first, because the bar does not say which came first.
        hit_target = (row["monday_low"] <= target) if side < 0 else (row["monday_high"] >= target)
        hit_stop = (row["monday_high"] >= stop) if side < 0 else (row["monday_low"] <= stop)
        if hit_stop:
            exit_price, outcome = stop, "stop"
        elif hit_target:
            exit_price, outcome = target, "target"
        else:
            exit_price, outcome = row["monday_close"], "close"
        gross = side * (exit_price - entry) / entry * 100.0
        net = gross - cost_pct
        trades.append({"session": row["session"], "outcome": outcome,
                       "net_pct": round(net, 4), "net_r": round(net / 100.0 * entry / risk, 4)})
    if not trades:
        return {"trades": 0, "why": "no tradeable setups"}
    rs = [t["net_r"] for t in trades]
    wins = [r for r in rs if r > 0]
    losses = [-r for r in rs if r < 0]
    equity, peak, worst = 0.0, 0.0, 0.0
    for r in rs:
        equity += r
        peak = max(peak, equity)
        worst = max(worst, peak - equity)
    return {"trades": len(trades), "win_rate_pct": round(100.0 * len(wins) / len(rs), 2),
            "profit_factor": round(sum(wins) / sum(losses), 3) if losses else None,
            "expectancy_r": round(float(np.mean(rs)), 4), "total_r": round(float(np.sum(rs)), 2),
            "max_drawdown_r": round(worst, 2),
            "outcomes": {k: sum(1 for t in trades if t["outcome"] == k) for k in ("target", "stop", "close")}}


def resolve_with_intraday(rows: list, direction: str, stop_mode: str, cost_pct: float,
                          atr_by_session: dict, intraday: pd.DataFrame, inverse: bool = False) -> dict:
    """The same trades, but with the order of touches settled by finer bars instead of assumed.

    The daily backtest counts a Monday that touched both the target and the stop as a loss, because
    a daily bar cannot say which came first. That is the honest assumption but it is pessimistic, and
    on the bearish/ATR variant it was deciding 193 targets against only 30 stops. Here the Monday
    session is walked in order on hourly bars: whichever level is touched first wins. Hours that
    touch both are still genuinely ambiguous and are counted and reported rather than guessed.
    """
    setup_key = f"{direction}_setup"
    base_side = -1 if direction == BEARISH else 1
    frame = intraday.reset_index(drop=True).copy()
    frame["_stamp"] = pd.to_datetime(frame["datetime"], utc=True)
    frame = frame.sort_values("_stamp").reset_index(drop=True)
    # Compare as naive UTC on both sides: a tz-aware Series and a tz-naive datetime64 will not compare.
    stamp_values = frame["_stamp"].dt.tz_convert(None).to_numpy(dtype="datetime64[ns]")
    lows = frame["low"].to_numpy(dtype=float)
    highs = frame["high"].to_numpy(dtype=float)

    trades, ambiguous, uncovered = [], 0, 0
    for row in rows:
        if not row[setup_key]:
            continue
        side = base_side
        entry = row["monday_open"]
        target = row["friday_low"] if direction == BEARISH else row["friday_high"]
        if stop_mode == "structure":
            stop = row["friday_high"] if direction == BEARISH else row["friday_low"]
        else:
            atr = atr_by_session.get(row["session"])
            if atr is None or not math.isfinite(atr) or atr <= 0:
                continue
            stop = entry + atr if direction == BEARISH else entry - atr
        risk = abs(entry - stop)
        if risk <= 0 or side * (target - entry) <= 0:
            continue
        if inverse:
            # The same setups traded the other way, with the distances mirrored about the entry. Gold
            # rose through this whole window, so a long-biased rule is flattered by direction alone;
            # this is what separates a structure from that.
            reward = abs(target - entry)
            side = -side
            target = entry + side * reward
            stop = entry - side * risk
        start = pd.Timestamp(row["session_start"])
        if start.tzinfo is None:
            start = start.tz_localize("UTC")
        begin = int(np.searchsorted(stamp_values, start.tz_convert(None).to_datetime64()))
        end = int(np.searchsorted(stamp_values,
                                  (start + pd.Timedelta(days=1)).tz_convert(None).to_datetime64()))
        if end <= begin:
            uncovered += 1
            continue

        outcome, exit_price = "close", row["monday_close"]
        for index in range(begin, end):
            low, high = lows[index], highs[index]
            hit_t = (low <= target) if side < 0 else (high >= target)
            hit_s = (high >= stop) if side < 0 else (low <= stop)
            if hit_t and hit_s:
                ambiguous += 1                      # both inside one hour: still unresolved
                outcome, exit_price = "stop", stop  # keep the pessimistic reading for these
                break
            if hit_t:
                outcome, exit_price = "target", target
                break
            if hit_s:
                outcome, exit_price = "stop", stop
                break
        gross = side * (exit_price - entry) / entry * 100.0
        net = gross - cost_pct
        trades.append({"outcome": outcome, "net_r": round(net / 100.0 * entry / risk, 4)})

    if not trades:
        return {"trades": 0, "why": "no setups covered by the intraday history"}
    rs = [x["net_r"] for x in trades]
    wins = [r for r in rs if r > 0]
    losses = [-r for r in rs if r < 0]
    equity = peak = worst = 0.0
    for r in rs:
        equity += r
        peak = max(peak, equity)
        worst = max(worst, peak - equity)
    return {"trades": len(trades), "win_rate_pct": round(100.0 * len(wins) / len(rs), 2),
            "profit_factor": round(sum(wins) / sum(losses), 3) if losses else None,
            "expectancy_r": round(float(np.mean(rs)), 4), "total_r": round(float(np.sum(rs)), 2),
            "max_drawdown_r": round(worst, 2),
            "outcomes": {k: sum(1 for x in trades if x["outcome"] == k) for k in ("target", "stop", "close")},
            "ambiguous_within_one_bar": ambiguous,
            "setups_without_intraday_history": uncovered,
            "r_values": rs}


def analyse(symbol: str, bars: pd.DataFrame, cost_pct: float) -> dict:
    from .features import compute_atr

    frame = session_frame(bars)
    frame["atr_14"] = compute_atr(frame)
    atr_by_session = {str(r["session_date"]): r["atr_14"] for _, r in frame.iterrows()}
    triplets = weeks(frame)
    rows = [classify_week(w) for w in triplets]
    out = {"symbol": symbol, "daily_bars": int(len(frame)), "weeks_usable": len(rows),
           "period": (f"{rows[0]['session']} to {rows[-1]['session']}" if rows else None),
           "cost_pct_round_trip": cost_pct, "tendency": {}, "backtest": {}}
    for direction in (BEARISH, BULLISH):
        out["tendency"][direction] = tendency(rows, direction)
        out["backtest"][direction] = {
            mode: simulate_rule(rows, direction, mode, cost_pct, atr_by_session)
            for mode in ("structure", "atr")}
    return out


def run(symbols=("XAUUSD", "BTCUSD")) -> dict:
    from .mtf_data import load_bars
    from .walkforward_backtest import BACKTEST_COSTS

    report = {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
              "rule": "Friday fails to take Thursday's high -> Monday visits Friday's low, and the mirror",
              "places_orders": False, "markets": {}}
    for symbol in symbols:
        bars = load_bars(symbol, "1d", source="app")
        if bars is None or bars.empty:
            report["markets"][symbol] = {"error": "no broker daily bars"}
            continue
        cost = BACKTEST_COSTS.get(symbol.upper(), BACKTEST_COSTS["default"])["round_trip_pct"]
        report["markets"][symbol] = analyse(symbol, bars, cost)
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Thursday/Friday/Monday rule (research only)")
    parser.add_argument("command", choices=["report"])
    parser.add_argument("--symbols", nargs="+", default=["XAUUSD", "BTCUSD"])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    report = run(tuple(args.symbols))
    if args.json:
        print(json.dumps(report, indent=1, default=str))
        return 0
    for symbol, market in report["markets"].items():
        if market.get("error"):
            print(f"\n{symbol}: {market['error']}")
            continue
        print(f"\n=== {symbol}   {market['weeks_usable']} complete Thu/Fri/Mon weeks "
              f"({market['period']}), costs {market['cost_pct_round_trip']}% round trip")
        for direction, t in market["tendency"].items():
            if not t.get("weeks"):
                print(f"  {direction}: {t.get('why')}")
                continue
            mark = "EDGE" if t["beats_base_with_confidence"] else "no edge"
            print(f"  {direction:8s} setup in {t['weeks']:4d} of {t['all_weeks']} weeks | "
                  f"hit {t['hit_rate']:.3f} vs base {t['base_rate']:.3f} | edge {t['edge']:+.4f} "
                  f"95% [{t['ci_low']:.3f},{t['ci_high']:.3f}]  {mark}")
        for direction, modes in market["backtest"].items():
            for mode, b in modes.items():
                if not b.get("trades"):
                    print(f"     {direction}/{mode}: {b.get('why')}")
                    continue
                print(f"     {direction:8s}/{mode:9s} n {b['trades']:4d} win {b['win_rate_pct']:5.2f}% "
                      f"PF {b['profit_factor']} exp {b['expectancy_r']:+.4f} R total {b['total_r']:+.1f} R "
                      f"maxDD {b['max_drawdown_r']} R  {b['outcomes']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
