"""The MetaQuotes "Moving Average" sample expert, backtested. Research only; it never trades.

Written 21 September 2026 after this expert was found trading a live account at 9.8-10.2 lots with no
stop loss. It is the file MetaQuotes ships with every MT4 terminal as a TEACHING EXAMPLE, magic
20131111, and the question is simply whether its rule makes money.

THE RULE, transcribed from Moving Average.mq4 line by line:

    ma = iMA(NULL, 0, MovingPeriod, MovingShift, MODE_SMA, PRICE_CLOSE, 0)
    sell when Open[1] > ma and Close[1] < ma
    buy  when Open[1] < ma and Close[1] > ma

``MovingShift`` shifts the average FORWARD, so the value displayed at bar 0 is the simple average of
the twelve closes ending six bars earlier. The comparison is the last CLOSED bar's open and close
against that value, which is why entries land on the first tick of a new bar and nowhere else.

Position handling is equally simple and is what makes it dangerous: one position per symbol
(``CalculateCurrentOrders``), no stop and no target at all (``OrderSend(..., 3, 0, 0, ...)``), and the
only exit is ``CheckForClose`` waiting for the average to be crossed back the other way. A position
that goes against it is held, without limit, until that happens.

WHY A SEPARATE SIMULATOR. The shared engine in ``strategy_lab`` exits on a stop, a target or a bar
count; this expert exits on an opposing signal and has no stop at all, which that engine cannot
express. The COSTS and the SPLIT BOUNDARIES are taken from the lab so the numbers stay comparable
with everything else in BASELINE.md.

    python -m src.ma_sample_lab run [--symbols XAUUSD BTCUSD] [--timeframes 15m 1h 4h]
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from itertools import product
from typing import Optional

import numpy as np
import pandas as pd

from . import strategy_lab as lab

MOVING_PERIOD = 12          # the expert's default, unchanged in the copy found on this machine
MOVING_SHIFT = 6
LOT_FLOOR = 0.1             # if(lot<0.1) lot=0.1 - it can never trade smaller than this
DEFAULT_MAX_RISK = 0.02     # MaximumRisk, the input that actually decides size


def sample_ma(close: np.ndarray, period: int = MOVING_PERIOD, shift: int = MOVING_SHIFT) -> np.ndarray:
    """iMA(..., period, shift, MODE_SMA, PRICE_CLOSE, 0): the average ending ``shift`` bars back."""
    series = pd.Series(close, dtype=float)
    return series.rolling(period).mean().shift(shift).to_numpy()


def sample_signals(o: np.ndarray, c: np.ndarray, period: int = MOVING_PERIOD,
                   shift: int = MOVING_SHIFT) -> np.ndarray:
    """+1 buy, -1 sell, 0 nothing - decided on the LAST CLOSED bar, acted on the next open."""
    ma = sample_ma(c, period, shift)
    n = len(c)
    side = np.zeros(n, dtype=int)
    prev_open = np.concatenate([[np.nan], o[:-1]])
    prev_close = np.concatenate([[np.nan], c[:-1]])
    with np.errstate(invalid="ignore"):
        side[(prev_open > ma) & (prev_close < ma)] = -1
        side[(prev_open < ma) & (prev_close > ma)] = 1
    side[~np.isfinite(ma)] = 0
    return side


def simulate_sample(o, h, l, c, times, side, cost_pct: float) -> list[dict]:
    """One position at a time, no stop, no target, exits only when the opposite signal appears.

    Entry is the next bar's open, as the expert's market order fills on the first tick of the new bar.
    """
    n = len(c)
    trades: list[dict] = []
    position = None
    for i in range(n - 1):
        signal = int(side[i])
        if signal == 0:
            continue
        entry_price = float(o[i + 1])
        if not np.isfinite(entry_price) or entry_price <= 0:
            continue
        if position is None:
            position = {"side": signal, "entry_i": i + 1, "entry": entry_price}
            continue
        if signal == position["side"]:
            continue                                  # same direction: the expert holds
        gross = position["side"] * (entry_price - position["entry"]) / position["entry"]
        trades.append({
            "side": "BUY" if position["side"] == 1 else "SELL",
            "entry_time": lab._iso(times.iloc[position["entry_i"]]),
            "exit_time": lab._iso(times.iloc[i + 1]),
            "entry_price": round(position["entry"], 3), "exit_price": round(entry_price, 3),
            "bars_held": int(i + 1 - position["entry_i"]),
            "gross_pct": round(gross * 100, 4),
            "net_pct": round((gross - cost_pct) * 100, 4),
            "outcome": "REVERSE",
        })
        position = {"side": signal, "entry_i": i + 1, "entry": entry_price}
    return trades


def worst_excursion(h, l, trades: list[dict], times) -> dict:
    """How far underwater these trades went while being held, since nothing would have closed them."""
    worst = 0.0
    worst_trade = None
    stamps = list(times)
    index = {lab._iso(t): i for i, t in enumerate(stamps)}
    for trade in trades:
        a, b = index.get(trade["entry_time"]), index.get(trade["exit_time"])
        if a is None or b is None or b <= a:
            continue
        if trade["side"] == "BUY":
            drawdown = (float(np.min(l[a:b + 1])) - trade["entry_price"]) / trade["entry_price"]
        else:
            drawdown = (trade["entry_price"] - float(np.max(h[a:b + 1]))) / trade["entry_price"]
        if drawdown < worst:
            worst, worst_trade = drawdown, trade
    return {"worst_open_drawdown_pct": round(worst * 100, 3),
            "on_trade": {k: worst_trade.get(k) for k in ("side", "entry_time", "exit_time", "net_pct")}
            if worst_trade else None}


def lot_for(free_margin: float, max_risk: float = DEFAULT_MAX_RISK) -> float:
    """The expert's own sizing: AccountFreeMargin() * MaximumRisk / 1000, floored at 0.1."""
    lot = round(free_margin * max_risk / 1000.0, 1)
    return max(LOT_FLOOR, lot)


def run(symbols=("XAUUSD", "BTCUSD"), timeframes=("15m", "1h", "4h")) -> dict:
    from .mtf_data import load_bars

    registry = lab.load_registry()
    report = {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
              "expert": "MetaQuotes Moving Average sample, magic 20131111",
              "rule": f"SMA({MOVING_PERIOD}) shifted {MOVING_SHIFT}; last closed bar crossing it; "
                      f"no stop, no target; exit only on the opposite cross",
              "places_orders": False, "markets": {}}
    for symbol, timeframe in product(symbols, timeframes):
        key = f"{symbol}:{timeframe}"
        bars = load_bars(symbol, timeframe, source="app")
        if bars is None or bars.empty:
            report["markets"][key] = {"error": "no broker bars"}
            continue
        market = lab.Market(symbol, timeframe, bars,
                            boundaries=(registry["markets"].get(key) or {}).get("boundaries"), swap=True)
        ind = market.ind
        side = sample_signals(ind.o, ind.c)
        entry = {"symbol": symbol, "timeframe": timeframe, "bars": int(len(ind.c)),
                 "signals": int(np.count_nonzero(side)), "cost_round_trip_pct": market.cost_pct,
                 "period": market.info["periods"], "splits": {}}
        for split, rows in market.rows.items():
            if len(rows) == 0:
                continue
            masked = np.zeros_like(side)
            masked[rows] = side[rows]
            trades = simulate_sample(ind.o, ind.h, ind.l, ind.c, ind.times, masked, market.cost_pct)
            if not trades:
                entry["splits"][split] = {"trades": 0}
                continue
            summary = market.summary(split, trades)
            summary.update(worst_excursion(ind.h, ind.l, trades, ind.times))
            summary["avg_bars_held"] = round(float(np.mean([t["bars_held"] for t in trades])), 1)
            entry["splits"][split] = summary
        report["markets"][key] = entry
    lab.LAB_DIR.mkdir(parents=True, exist_ok=True)
    path = lab.LAB_DIR / f"ma_sample_{datetime.now(timezone.utc):%Y%m%d}.json"
    path.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    report["path"] = str(path)
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Backtest the MetaQuotes Moving Average sample (research only)")
    parser.add_argument("command", choices=["run"])
    parser.add_argument("--symbols", nargs="+", default=["XAUUSD", "BTCUSD"])
    parser.add_argument("--timeframes", nargs="+", default=["15m", "1h", "4h"])
    args = parser.parse_args(argv)

    report = run(tuple(args.symbols), tuple(args.timeframes))
    print(f"{report['expert']}\n{report['rule']}\n")
    for key, market in report["markets"].items():
        if market.get("error"):
            print(f"{key}: {market['error']}")
            continue
        print(f"=== {key}  {market['bars']} bars, {market['signals']} signals, "
              f"costs {market['cost_round_trip_pct'] * 100:.4f}% round trip")
        for split, summary in market["splits"].items():
            if not summary.get("trades"):
                print(f"   {split:11s} no trades")
                continue
            print(f"   {split:11s} n {summary['trades']:4d}  PF {summary.get('profit_factor') or 0:5.3f}  "
                  f"net {summary.get('total_return_pct'):+9.2f}%  win {summary.get('win_rate_pct') or 0:5.1f}%  "
                  f"maxDD {summary.get('max_drawdown_pct'):6.2f}%  held {summary['avg_bars_held']:6.1f} bars  "
                  f"worst open DD {summary['worst_open_drawdown_pct']:+8.2f}%")
        print()
    print(f"saved {report['path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
