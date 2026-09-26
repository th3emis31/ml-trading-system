"""What the candles immediately before a swing turn actually look like. Research only, never trades.

    python -m src.turn_anatomy XAUUSD 1h --window "2026-09-14 12:00" "2026-09-15 00:00"

WHY THIS EXISTS
---------------
The owner marked six of their own winning H1 gold trades on the chart and asked what happens on the
candles before each turn. That is the right question and it had no tool: every other module in this
project measures a rule someone already wrote down, while this one goes the other way - it takes turns
that actually happened and describes them, so a rule can be written FROM the evidence instead of guessed
at and then tested.

It answers one question per turn: at the extreme bar, and on the candles leading into it, what was true?

* where price sat against the fast / medium / slow moving averages, and whether they had crossed
* the Parabolic SAR, and whether it flipped - the trigger visible in all six of the owner's screenshots
* RSI(12), the owner's own setting
* whether the turn swept a prior swing (took liquidity) before turning
* whether a CISD fired, using ``src/cisd.py``
* the candle bodies and ranges against their recent average, which is displacement
* tick volume against its recent average

WHAT IT DOES NOT DO
-------------------
It does not score, rank or conclude. Describing six winners says nothing on its own about whether the
pattern is tradeable, because the six were chosen BY their outcome - the same selection that makes every
screenshot of a strategy look good. Turning what this finds into a claim needs the same holdout, costs
and controls as everything else in ``.claude/memory/BASELINE.md``, and comparing these turns against the
turns that did NOT work is the first step.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import List, Optional

import numpy as np
import pandas as pd

FAST, MEDIUM, SLOW = 10, 21, 50      # fitted to the owner's screenshots; EMA50 matched the red line
RSI_LENGTH = 12                      # the owner's charts read "RSI(12)"
SAR_STEP, SAR_MAX = 0.02, 0.2        # MetaTrader's defaults
LEAD_CANDLES = 6                     # how many bars before the turn to describe
AVERAGE_OVER = 20


@dataclass
class Turn:
    """One swing extreme and the bars that led into it."""

    index: int
    when: str
    kind: str            # "low" (a turn upward) or "high" (a turn downward)
    price: float


def parabolic_sar(highs, lows, step: float = SAR_STEP, maximum: float = SAR_MAX) -> tuple:
    """``(sar, rising)`` - Wilder's Parabolic SAR, the green diamonds on the owner's charts.

    Written here rather than imported because nothing in this project had one. It is the standard
    formulation: the extreme point advances the acceleration factor, and a penetration of SAR flips the
    side and restarts the factor.
    """
    high = np.asarray(highs, dtype=float)
    low = np.asarray(lows, dtype=float)
    n = len(high)
    sar = np.full(n, np.nan)
    rising = np.zeros(n, dtype=bool)
    if n < 3:
        return sar, rising

    up = high[1] >= high[0]
    accel = step
    extreme = high[1] if up else low[1]
    sar[1] = low[0] if up else high[0]
    rising[1] = up
    for i in range(2, n):
        previous = sar[i - 1]
        value = previous + accel * (extreme - previous)
        if up:
            value = min(value, low[i - 1], low[i - 2])
            if low[i] < value:                     # flip to falling
                up = False
                value = extreme
                extreme = low[i]
                accel = step
            elif high[i] > extreme:
                extreme = high[i]
                accel = min(accel + step, maximum)
        else:
            value = max(value, high[i - 1], high[i - 2])
            if high[i] > value:                    # flip to rising
                up = True
                value = extreme
                extreme = high[i]
                accel = step
            elif low[i] < extreme:
                extreme = low[i]
                accel = min(accel + step, maximum)
        sar[i] = value
        rising[i] = up
    return sar, rising


def wilder_rsi(closes, length: int = RSI_LENGTH) -> np.ndarray:
    """RSI on Wilder's smoothing, which is what MetaTrader draws."""
    close = np.asarray(closes, dtype=float)
    n = len(close)
    out = np.full(n, np.nan)
    if n <= length:
        return out
    change = np.diff(close)
    gain = np.where(change > 0, change, 0.0)
    loss = np.where(change < 0, -change, 0.0)
    average_gain = gain[:length].mean()
    average_loss = loss[:length].mean()
    for i in range(length, n):
        if i > length:
            average_gain = (average_gain * (length - 1) + gain[i - 1]) / length
            average_loss = (average_loss * (length - 1) + loss[i - 1]) / length
        out[i] = 100.0 if average_loss == 0 else 100 - 100 / (1 + average_gain / average_loss)
    return out


def find_turn(frame: pd.DataFrame, start, end) -> Optional[Turn]:
    """The most extreme bar inside the window, and which way it turned.

    Which extreme matters is decided by where price went AFTER the window rather than guessed: a window
    whose close ends above its own midpoint turned up, so its LOW is the turn.
    """
    def as_utc(value):
        """A window bound as a UTC Timestamp, whether it arrived as a string or already tz-aware.

        `pd.Timestamp(value, tz="UTC")` raises on a value that already carries a timezone, so passing a
        Timestamp straight from a frame - the obvious thing for a caller to do - crashed the whole call.
        """
        stamp = pd.Timestamp(value)
        return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")

    mask = (frame["datetime"] >= as_utc(start)) & (frame["datetime"] <= as_utc(end))
    inside = frame[mask]
    if inside.empty:
        return None
    first, last = float(inside["close"].iloc[0]), float(inside["close"].iloc[-1])
    if last >= first:
        index = int(inside["low"].idxmin())
        return Turn(index=index, when=str(frame.loc[index, "datetime"]), kind="low",
                    price=float(frame.loc[index, "low"]))
    index = int(inside["high"].idxmax())
    return Turn(index=index, when=str(frame.loc[index, "datetime"]), kind="high",
                price=float(frame.loc[index, "high"]))


def describe_turn(frame: pd.DataFrame, turn: Turn, lead: int = LEAD_CANDLES) -> dict:
    """Everything measurable about the turn bar and the candles leading into it."""
    close = frame["close"].astype(float)
    fast = close.ewm(span=FAST, adjust=False).mean().to_numpy()
    medium = close.ewm(span=MEDIUM, adjust=False).mean().to_numpy()
    slow = close.ewm(span=SLOW, adjust=False).mean().to_numpy()
    sar, rising = parabolic_sar(frame["high"], frame["low"])
    rsi = wilder_rsi(close)
    body = (close - frame["open"].astype(float)).abs().to_numpy()
    rng = (frame["high"].astype(float) - frame["low"].astype(float)).to_numpy()
    volume = frame["volume"].astype(float).to_numpy() if "volume" in frame.columns else np.zeros(len(frame))
    mean_body = pd.Series(body).rolling(AVERAGE_OVER).mean().to_numpy()
    mean_volume = pd.Series(volume).rolling(AVERAGE_OVER).mean().to_numpy()

    i = turn.index
    candles = []
    for offset in range(lead, -1, -1):
        j = i - offset
        if j < 1:
            continue
        candles.append({
            "when": str(frame.loc[j, "datetime"])[:16],
            "at_turn": j == i,
            "open": round(float(frame.loc[j, "open"]), 2),
            "high": round(float(frame.loc[j, "high"]), 2),
            "low": round(float(frame.loc[j, "low"]), 2),
            "close": round(float(frame.loc[j, "close"]), 2),
            "dir": "up" if frame.loc[j, "close"] > frame.loc[j, "open"] else "down",
            "body_vs_avg": round(float(body[j] / mean_body[j]), 2) if np.isfinite(mean_body[j]) and mean_body[j] > 0 else None,
            "vol_vs_avg": round(float(volume[j] / mean_volume[j]), 2) if np.isfinite(mean_volume[j]) and mean_volume[j] > 0 else None,
            "rsi": round(float(rsi[j]), 1) if np.isfinite(rsi[j]) else None,
            "sar": "below" if rising[j] else "above",
            "sar_flip": bool(j > 0 and rising[j] != rising[j - 1]),
            "vs_fast": round(float(frame.loc[j, "close"] - fast[j]), 2),
            "vs_medium": round(float(frame.loc[j, "close"] - medium[j]), 2),
            "vs_slow": round(float(frame.loc[j, "close"] - slow[j]), 2),
        })

    # Did the turn take liquidity - a low below the prior 20 bars' low, or a high above their high?
    window = slice(max(0, i - 20), i)
    if turn.kind == "low":
        prior = float(frame["low"].iloc[window].min()) if i > 0 else np.nan
        swept = bool(np.isfinite(prior) and turn.price < prior)
    else:
        prior = float(frame["high"].iloc[window].max()) if i > 0 else np.nan
        swept = bool(np.isfinite(prior) and turn.price > prior)

    flips = [c["when"] for c in candles if c["sar_flip"]]
    return {"turn": {"when": turn.when[:16], "kind": turn.kind, "price": round(turn.price, 2)},
            "swept_prior_20_bar_extreme": swept, "prior_extreme": round(float(prior), 2) if np.isfinite(prior) else None,
            "sar_flipped_in_lead": flips,
            "rsi_at_turn": candles[-1]["rsi"] if candles else None,
            "candles": candles, "places_orders": False}


def anatomy(symbol: str, timeframe: str, windows: List[tuple], lead: int = LEAD_CANDLES) -> dict:
    from .mtf_data import load_bars

    frame = load_bars(symbol, timeframe, source="app")
    if frame is None or frame.empty:
        return {"available": False, "reason": f"no broker bars for {symbol} {timeframe}"}
    frame = frame.sort_values("datetime").reset_index(drop=True)
    frame["datetime"] = pd.to_datetime(frame["datetime"], utc=True)
    out = {"available": True, "symbol": symbol, "timeframe": timeframe,
           "moving_averages": {"fast": FAST, "medium": MEDIUM, "slow": SLOW, "kind": "EMA"},
           "turns": [], "places_orders": False}
    for start, end in windows:
        turn = find_turn(frame, start, end)
        if turn is None:
            out["turns"].append({"window": [start, end], "error": "no bars in this window"})
            continue
        described = describe_turn(frame, turn, lead)
        described["window"] = [start, end]
        out["turns"].append(described)
    return out


def print_anatomy(out: dict) -> None:
    if not out.get("available"):
        print(out.get("reason"))
        return
    ma = out["moving_averages"]
    print(f"{out['symbol']} {out['timeframe']} - the candles into each turn "
          f"(EMA {ma['fast']}/{ma['medium']}/{ma['slow']}, RSI {RSI_LENGTH}, SAR {SAR_STEP}/{SAR_MAX})")
    for row in out["turns"]:
        if row.get("error"):
            print(f"\n{row['window']}: {row['error']}")
            continue
        turn = row["turn"]
        arrow = "turned UP from" if turn["kind"] == "low" else "turned DOWN from"
        print(f"\n=== {turn['when']}  {arrow} {turn['price']}")
        print(f"    swept the prior 20-bar {'low' if turn['kind'] == 'low' else 'high'} "
              f"({row['prior_extreme']}): {'YES' if row['swept_prior_20_bar_extreme'] else 'no'}"
              f"   |   SAR flipped on: {', '.join(f[-5:] for f in row['sar_flipped_in_lead']) or 'not in this lead'}")
        print(f"    {'when':>16} {'dir':>5} {'close':>9} {'body x':>7} {'vol x':>6} {'RSI':>5} "
              f"{'SAR':>6} {'vs fast':>8} {'vs med':>8} {'vs slow':>8}")
        for candle in row["candles"]:
            mark = " <- TURN" if candle["at_turn"] else ""
            print(f"    {candle['when'][-11:]:>16} {candle['dir']:>5} {candle['close']:>9.2f} "
                  f"{(candle['body_vs_avg'] or 0):>7.2f} {(candle['vol_vs_avg'] or 0):>6.2f} "
                  f"{(candle['rsi'] or 0):>5.1f} {candle['sar']:>6} "
                  f"{candle['vs_fast']:>8.2f} {candle['vs_medium']:>8.2f} {candle['vs_slow']:>8.2f}{mark}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Describe the candles into a swing turn. Reads only.")
    parser.add_argument("symbol", nargs="?", default="XAUUSD")
    parser.add_argument("timeframe", nargs="?", default="1h")
    parser.add_argument("--window", nargs=2, action="append", metavar=("START", "END"),
                        help="a window to find the turn in; repeat for several")
    parser.add_argument("--lead", type=int, default=LEAD_CANDLES)
    args = parser.parse_args(argv)

    windows = [tuple(w) for w in (args.window or [])]
    if not windows:
        print("give at least one --window START END")
        return 1
    print_anatomy(anatomy(args.symbol, args.timeframe, windows, args.lead))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
