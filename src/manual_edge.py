"""What separates the owner's WINNING manual trades from their losing ones. Research only, never trades.

    python -m src.manual_edge report

WHY THIS EXISTS, AND THE MISTAKE IT CORRECTS
--------------------------------------------
The owner asked twice what happens on the candles before their manual wins. Both times I answered from
SCREENSHOTS - eyeballing date windows off chart pictures and describing those - and then concluded from
the invented windows that there was no edge. The account holds 102 closed trades with exact entry
timestamps, prices and directions, 79 of them the owner's own. I never opened them.

That is the difference between describing pictures and measuring trades, and it also unlocks the only
comparison that can find an edge: **winners against losers**. Comparing six winners against "all bars"
answers a different question - it asks what a turn looks like, when the real question is what made the
owner take THESE and what made some of them work.

WHAT IT MEASURES
----------------
For every manual trade, the H1 bar the entry fell in, and at that bar:

* Parabolic SAR - side, and whether it had just flipped
* RSI(12), the owner's own setting
* price against the EMA 10 / 21 / 50 fitted from their charts, and the distance in ATR
* whether the previous 20 bars' extreme had just been swept
* body and volume against their own 20-bar averages
* the hour of day, and how long the trade was held

Then it splits those by outcome. A feature that appears in the winners AND the losers explains nothing;
only a gap between the two is a candidate for the thing the owner is actually doing.

WHAT IT CANNOT DO
-----------------
37 winners and 42 losers is a small sample, and any split found here needs holding out before it becomes
a rule. It also cannot see the reason a trade was taken - news, a higher timeframe, a view - only what the
chart showed at that moment.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd

from .turn_anatomy import FAST, MEDIUM, RSI_LENGTH, SLOW, parabolic_sar, wilder_rsi

AVERAGE_OVER = 20
SWEEP_LOOKBACK = 20


def load_manual_trades(path: Optional[str] = None) -> list:
    """The owner's own closed trades, from the attributed endpoint or a saved copy of it."""
    if path:
        payload = json.loads(open(path, encoding="utf-8").read())
    else:
        import urllib.request

        with urllib.request.urlopen("http://127.0.0.1:5000/api/trades/closed?limit=500", timeout=120) as reply:
            payload = json.loads(reply.read().decode("utf-8"))
    rows = payload.get("trades") or []
    return [r for r in rows if (r.get("attribution") or {}).get("owner") != "system"]


def bar_features(frame: pd.DataFrame) -> dict:
    """Every per-bar feature, computed once for the whole frame."""
    close = frame["close"].astype(float)
    high = frame["high"].astype(float)
    low = frame["low"].astype(float)
    open_ = frame["open"].astype(float)
    volume = frame["volume"].astype(float) if "volume" in frame.columns else pd.Series(0.0, index=frame.index)

    previous = close.shift(1)
    true_range = pd.concat([high - low, (high - previous).abs(), (low - previous).abs()], axis=1).max(axis=1)
    atr = true_range.ewm(alpha=1 / 14, adjust=False).mean()
    sar, rising = parabolic_sar(high, low)
    body = (close - open_).abs()

    return {
        "close": close.to_numpy(), "high": high.to_numpy(), "low": low.to_numpy(),
        "atr": atr.to_numpy(),
        "rsi": wilder_rsi(close, RSI_LENGTH),
        "sar_rising": rising,
        "sar_flip": np.concatenate(([False], rising[1:] != rising[:-1])),
        "ema_fast": close.ewm(span=FAST, adjust=False).mean().to_numpy(),
        "ema_medium": close.ewm(span=MEDIUM, adjust=False).mean().to_numpy(),
        "ema_slow": close.ewm(span=SLOW, adjust=False).mean().to_numpy(),
        "body_ratio": (body / body.rolling(AVERAGE_OVER).mean()).to_numpy(),
        "volume_ratio": (volume / volume.rolling(AVERAGE_OVER).mean()).to_numpy(),
        "prior_low": low.rolling(SWEEP_LOOKBACK).min().shift(1).to_numpy(),
        "prior_high": high.rolling(SWEEP_LOOKBACK).max().shift(1).to_numpy(),
    }


def describe_entry(features: dict, index: int, direction: str) -> dict:
    """What the chart showed at the bar this trade was entered on, signed by the trade's direction.

    Signing matters: "price 1 ATR above the EMA50" means the opposite thing for a buy and a sell, and
    pooling them unsigned is how a real split gets averaged into nothing.
    """
    sign = 1 if str(direction).upper() == "BUY" else -1
    atr = features["atr"][index]
    if not np.isfinite(atr) or atr <= 0:
        return {}
    close = features["close"][index]
    rsi = features["rsi"][index]
    return {
        "with_sar": bool(features["sar_rising"][index]) == (sign == 1),
        "sar_just_flipped": bool(features["sar_flip"][index]),
        "rsi": round(float(rsi), 1) if np.isfinite(rsi) else None,
        # RSI signed toward the trade: high means "stretched the way I am trading".
        "rsi_with_trade": round(float(50 + sign * (rsi - 50)), 1) if np.isfinite(rsi) else None,
        "atr_from_slow_ema": round(float(sign * (close - features["ema_slow"][index]) / atr), 2),
        "atr_from_fast_ema": round(float(sign * (close - features["ema_fast"][index]) / atr), 2),
        "fast_above_medium": bool(features["ema_fast"][index] > features["ema_medium"][index]) == (sign == 1),
        "swept_prior_extreme": bool(
            features["low"][index] < features["prior_low"][index] if sign == 1
            else features["high"][index] > features["prior_high"][index]),
        "body_ratio": round(float(features["body_ratio"][index]), 2) if np.isfinite(features["body_ratio"][index]) else None,
        "volume_ratio": round(float(features["volume_ratio"][index]), 2) if np.isfinite(features["volume_ratio"][index]) else None,
        "hour_utc": None,
    }


def study(symbol: str = "XAUUSD", timeframe: str = "1h", trades_path: Optional[str] = None) -> dict:
    from .mtf_data import load_bars

    trades = load_manual_trades(trades_path)
    frame = load_bars(symbol, timeframe, source="app")
    if frame is None or frame.empty:
        return {"available": False, "reason": f"no broker bars for {symbol} {timeframe}"}
    frame = frame.sort_values("datetime").reset_index(drop=True)
    frame["datetime"] = pd.to_datetime(frame["datetime"], utc=True)
    features = bar_features(frame)
    # tz-naive int64 nanoseconds. `.to_numpy()` on a tz-aware column yields Timestamp objects, and
    # comparing those against a naive datetime64 raises - so both sides are reduced to plain integers.
    stamps = frame["datetime"].astype("int64").to_numpy()

    rows = []
    for trade in trades:
        if str(trade.get("symbol", "")).upper() != symbol.upper():
            continue
        opened = trade.get("opened_at")
        if not opened:
            continue
        when = pd.Timestamp(int(opened), unit="s", tz="UTC")
        index = int(np.searchsorted(stamps, int(when.value), side="right") - 1)
        if index < AVERAGE_OVER + SLOW or index >= len(frame):
            continue
        described = describe_entry(features, index, trade.get("direction", "BUY"))
        if not described:
            continue
        described["hour_utc"] = int(when.hour)
        held_hours = (int(trade.get("time", 0)) - int(opened)) / 3600.0
        rows.append({**described, "net": float(trade.get("net") or 0.0),
                     "won": float(trade.get("net") or 0.0) > 0,
                     "direction": trade.get("direction"), "volume": float(trade.get("volume") or 0),
                     "held_hours": round(held_hours, 1), "entered": str(when)[:16]})

    if not rows:
        return {"available": False, "reason": "no manual trades fell inside the available bars"}

    winners = [r for r in rows if r["won"]]
    losers = [r for r in rows if not r["won"]]

    def split(key):
        def value(source):
            values = [r[key] for r in source if r.get(key) is not None]
            if not values:
                return None
            if isinstance(values[0], bool):
                return round(100.0 * sum(values) / len(values), 1)
            return round(float(np.mean(values)), 2)
        return {"winners": value(winners), "losers": value(losers), "all": value(rows)}

    keys = ["with_sar", "sar_just_flipped", "rsi_with_trade", "atr_from_slow_ema", "atr_from_fast_ema",
            "fast_above_medium", "swept_prior_extreme", "body_ratio", "volume_ratio", "held_hours",
            "hour_utc", "volume"]
    return {"available": True, "symbol": symbol, "timeframe": timeframe,
            "trades": len(rows), "winners": len(winners), "losers": len(losers),
            "net_winners": round(sum(r["net"] for r in winners), 2),
            "net_losers": round(sum(r["net"] for r in losers), 2),
            "features": {key: split(key) for key in keys},
            "rows": rows, "places_orders": False}


def print_study(out: dict) -> None:
    if not out.get("available"):
        print(out.get("reason"))
        return
    print(f"{out['symbol']} {out['timeframe']} - the owner's OWN manual trades, from the account")
    print(f"  {out['trades']} trades matched to bars: {out['winners']} winners (+{out['net_winners']}) "
          f"vs {out['losers']} losers ({out['net_losers']})")
    print()
    print(f"  {'feature':24} {'winners':>10} {'losers':>10} {'gap':>10}")
    for key, values in out["features"].items():
        win, lose = values["winners"], values["losers"]
        if win is None or lose is None:
            continue
        gap = round(win - lose, 2)
        flag = "  <---" if abs(gap) >= (10 if abs(win) > 5 else 0.3) else ""
        print(f"  {key:24} {win:>10} {lose:>10} {gap:>10}{flag}")
    print()
    print("  booleans are percentages; a gap only means something if it survives a holdout.")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Compare the owner's winning manual trades with their "
                                                 "losing ones. Reads only, never trades.")
    sub = parser.add_subparsers(dest="command", required=True)
    runner = sub.add_parser("report", help="the winners-versus-losers table")
    runner.add_argument("--symbol", default="XAUUSD")
    runner.add_argument("--timeframe", default="1h")
    runner.add_argument("--trades", default=None, help="a saved copy of /api/trades/closed")
    args = parser.parse_args(argv)

    print_study(study(args.symbol, args.timeframe, args.trades))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
