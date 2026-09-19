"""Candlestick pattern detectors, and a measurement of whether each one predicts anything.

Definitions, thresholds and the pass rule are in ``strategies/candle_patterns.md`` and were written
down before this was run. Read that first: the thresholds here are conventional textbook values and
are **never** searched, because a pattern whose threshold is tuned after seeing the result is not a
pattern any more.

Two things this module does differently from ``src/features.detect_reversal``, which is the nearest
thing the system had:

* **Every pattern is direction-signed** (+1 bullish, -1 bearish, 0 absent). ``detect_reversal``
  returns 1.0 for a bullish *or* a bearish reversal, so a model cannot tell which way it pointed.
* **Sizes are normalised** by ATR(14) or by the bar's own range, so "long wick" means the same at
  gold 1,200 and gold 4,300.

Nothing here is wired into any feature list, model or strategy. It measures, and it prints.

    python -m src.candle_patterns measure [--symbols XAUUSD BTCUSD] [--timeframes 15m 1h 4h]
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from typing import Callable

import numpy as np
import pandas as pd

# The project has one ATR implementation and this uses it rather than adding a second.
from .features import compute_atr as _atr

# --- thresholds, fixed by strategies/candle_patterns.md -----------------------------
MARUBOZU_BODY = 0.80        # body / range
MARUBOZU_RANGE_ATR = 0.5
DOJI_BODY = 0.10
DOJI_RANGE_ATR = 0.3
WICK_MULTIPLE = 2.0         # hammer / shooting star: long wick vs body
WICK_OPPOSITE = 0.25        # the other wick, as a share of range
SMALL_BODY = 0.35           # body / range for hammer and shooting star
SHADOW_RANGE_ATR = 0.5
PIN_WICK = 0.66             # pin bar: long wick as a share of range
PIN_RANGE_ATR = 0.75
STAR_BODY_ATR = 0.5         # the first bar of a morning / evening star
STAR_SMALL = 0.35           # the middle bar's body vs the first bar's body
SOLDIER_BODY = 0.60         # body / range for three soldiers / crows

# --- measurement settings, also fixed in advance ------------------------------------
BARRIER_ATR = 1.0           # symmetric: target +1 ATR, stop -1 ATR
HORIZON = 12
MIN_OCCURRENCES = 200       # the pass rule's minimum


def _geometry(df: pd.DataFrame) -> dict:
    """Body, range and wick arrays, plus ATR(14). Pure per-bar geometry, no look-ahead."""
    o = df["open"].to_numpy(dtype=float)
    h = df["high"].to_numpy(dtype=float)
    l = df["low"].to_numpy(dtype=float)
    c = df["close"].to_numpy(dtype=float)
    body = np.abs(c - o)
    rng = h - l
    with np.errstate(divide="ignore", invalid="ignore"):
        body_ratio = np.where(rng > 0, body / rng, 0.0)
    upper = h - np.maximum(o, c)
    lower = np.minimum(o, c) - l
    atr = df["atr_14"].to_numpy(dtype=float) if "atr_14" in df else _atr(df).to_numpy(dtype=float)
    return {"o": o, "h": h, "l": l, "c": c, "body": body, "range": rng,
            "body_ratio": body_ratio, "upper": upper, "lower": lower, "atr": atr,
            "up": c > o, "down": c < o}


# --- one-candle -------------------------------------------------------------------

def marubozu(g: dict) -> np.ndarray:
    big = (g["body_ratio"] >= MARUBOZU_BODY) & (g["range"] >= MARUBOZU_RANGE_ATR * g["atr"])
    return np.where(big & g["up"], 1, np.where(big & g["down"], -1, 0))


def doji(g: dict) -> np.ndarray:
    """Indecision has no direction, so this is measured as a flag with sign 0 handled by the caller."""
    flat = (g["body_ratio"] <= DOJI_BODY) & (g["range"] >= DOJI_RANGE_ATR * g["atr"])
    return np.where(flat, 1, 0)      # presence only; the measurement treats it as both sides


def hammer(g: dict) -> np.ndarray:
    ok = ((g["lower"] >= WICK_MULTIPLE * g["body"]) & (g["upper"] <= WICK_OPPOSITE * g["range"])
          & (g["body_ratio"] <= SMALL_BODY) & (g["range"] >= SHADOW_RANGE_ATR * g["atr"]))
    return np.where(ok, 1, 0)


def shooting_star(g: dict) -> np.ndarray:
    ok = ((g["upper"] >= WICK_MULTIPLE * g["body"]) & (g["lower"] <= WICK_OPPOSITE * g["range"])
          & (g["body_ratio"] <= SMALL_BODY) & (g["range"] >= SHADOW_RANGE_ATR * g["atr"]))
    return np.where(ok, -1, 0)


def pin_bar(g: dict) -> np.ndarray:
    big_enough = g["range"] >= PIN_RANGE_ATR * g["atr"]
    with np.errstate(divide="ignore", invalid="ignore"):
        lower_share = np.where(g["range"] > 0, g["lower"] / g["range"], 0.0)
        upper_share = np.where(g["range"] > 0, g["upper"] / g["range"], 0.0)
    return np.where(big_enough & (lower_share >= PIN_WICK), 1,
                    np.where(big_enough & (upper_share >= PIN_WICK), -1, 0))


# --- two-candle -------------------------------------------------------------------

def _shift(values: np.ndarray, by: int = 1):
    out = np.full(len(values), np.nan if values.dtype.kind == "f" else False, dtype=values.dtype)
    if by < len(values):
        out[by:] = values[:-by]
    return out


def bullish_engulfing(g: dict) -> np.ndarray:
    prev_down, prev_o, prev_c = _shift(g["down"]), _shift(g["o"]), _shift(g["c"])
    prev_body = _shift(g["body"])
    ok = (prev_down & g["up"] & (g["o"] <= prev_c) & (g["c"] >= prev_o) & (g["body"] >= prev_body))
    return np.where(ok, 1, 0)


def bearish_engulfing(g: dict) -> np.ndarray:
    prev_up, prev_o, prev_c = _shift(g["up"]), _shift(g["o"]), _shift(g["c"])
    prev_body = _shift(g["body"])
    ok = (prev_up & g["down"] & (g["o"] >= prev_c) & (g["c"] <= prev_o) & (g["body"] >= prev_body))
    return np.where(ok, -1, 0)


def piercing_line(g: dict) -> np.ndarray:
    prev_down, prev_o, prev_c = _shift(g["down"]), _shift(g["o"]), _shift(g["c"])
    midpoint = (prev_o + prev_c) / 2.0
    ok = prev_down & g["up"] & (g["o"] < prev_c) & (g["c"] > midpoint) & (g["c"] < prev_o)
    return np.where(ok, 1, 0)


def dark_cloud_cover(g: dict) -> np.ndarray:
    prev_up, prev_o, prev_c = _shift(g["up"]), _shift(g["o"]), _shift(g["c"])
    midpoint = (prev_o + prev_c) / 2.0
    ok = prev_up & g["down"] & (g["o"] > prev_c) & (g["c"] < midpoint) & (g["c"] > prev_o)
    return np.where(ok, -1, 0)


# --- three-candle -----------------------------------------------------------------

def morning_star(g: dict) -> np.ndarray:
    first_down, first_body = _shift(g["down"], 2), _shift(g["body"], 2)
    first_o, first_c = _shift(g["o"], 2), _shift(g["c"], 2)
    mid_body = _shift(g["body"], 1)
    first_big = first_body >= STAR_BODY_ATR * g["atr"]
    midpoint = (first_o + first_c) / 2.0
    ok = first_down & first_big & (mid_body <= STAR_SMALL * first_body) & g["up"] & (g["c"] > midpoint)
    return np.where(ok, 1, 0)


def evening_star(g: dict) -> np.ndarray:
    first_up, first_body = _shift(g["up"], 2), _shift(g["body"], 2)
    first_o, first_c = _shift(g["o"], 2), _shift(g["c"], 2)
    mid_body = _shift(g["body"], 1)
    first_big = first_body >= STAR_BODY_ATR * g["atr"]
    midpoint = (first_o + first_c) / 2.0
    ok = first_up & first_big & (mid_body <= STAR_SMALL * first_body) & g["down"] & (g["c"] < midpoint)
    return np.where(ok, -1, 0)


def three_white_soldiers(g: dict) -> np.ndarray:
    up1, up2 = _shift(g["up"], 1), _shift(g["up"], 2)
    c1, c2 = _shift(g["c"], 1), _shift(g["c"], 2)
    r0, r1, r2 = g["body_ratio"], _shift(g["body_ratio"], 1), _shift(g["body_ratio"], 2)
    ok = (g["up"] & up1 & up2 & (g["c"] > c1) & (c1 > c2)
          & (r0 >= SOLDIER_BODY) & (r1 >= SOLDIER_BODY) & (r2 >= SOLDIER_BODY))
    return np.where(ok, 1, 0)


def three_black_crows(g: dict) -> np.ndarray:
    dn1, dn2 = _shift(g["down"], 1), _shift(g["down"], 2)
    c1, c2 = _shift(g["c"], 1), _shift(g["c"], 2)
    r0, r1, r2 = g["body_ratio"], _shift(g["body_ratio"], 1), _shift(g["body_ratio"], 2)
    ok = (g["down"] & dn1 & dn2 & (g["c"] < c1) & (c1 < c2)
          & (r0 >= SOLDIER_BODY) & (r1 >= SOLDIER_BODY) & (r2 >= SOLDIER_BODY))
    return np.where(ok, -1, 0)


PATTERNS: dict[str, Callable[[dict], np.ndarray]] = {
    "marubozu": marubozu,
    "doji": doji,
    "hammer": hammer,
    "shooting_star": shooting_star,
    "pin_bar": pin_bar,
    "bullish_engulfing": bullish_engulfing,
    "bearish_engulfing": bearish_engulfing,
    "piercing_line": piercing_line,
    "dark_cloud_cover": dark_cloud_cover,
    "morning_star": morning_star,
    "evening_star": evening_star,
    "three_white_soldiers": three_white_soldiers,
    "three_black_crows": three_black_crows,
}
DIRECTIONLESS = ("doji",)   # measured on both sides, because indecision points nowhere


def pattern_frame(bars: pd.DataFrame) -> pd.DataFrame:
    """One signed column per pattern, aligned to ``bars``. Read only the bar and its predecessors."""
    frame = bars.reset_index(drop=True).copy()
    if "atr_14" not in frame:
        frame["atr_14"] = _atr(frame)
    g = _geometry(frame)
    out = pd.DataFrame({"datetime": frame["datetime"]})
    for name, detector in PATTERNS.items():
        out[name] = detector(g).astype(int)
    return out


# --- measurement ------------------------------------------------------------------

def _wilson(wins: int, n: int) -> tuple:
    """Wilson 95 % interval for a proportion — honest at small n, unlike the normal approximation."""
    if n == 0:
        return (float("nan"), float("nan"))
    z = 1.96
    p = wins / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (centre - half, centre + half)


def measure_market(bars: pd.DataFrame) -> dict:
    """Win rate of each pattern's own direction against the base rate for that direction."""
    from .edge_research import triple_barrier_outcomes

    frame = bars.reset_index(drop=True).copy()
    if "atr_14" not in frame:
        frame["atr_14"] = _atr(frame)
    outcomes = triple_barrier_outcomes(frame, sl_atr=BARRIER_ATR, tp_atr=BARRIER_ATR, horizon=HORIZON)
    labels = {side: np.asarray(outcomes[side]["label"], dtype=float) for side in (1, -1)}
    patterns = pattern_frame(frame)

    base = {}
    for side in (1, -1):
        defined = np.isfinite(labels[side])
        base[side] = {"n": int(defined.sum()),
                      "win_rate": float(np.nanmean(labels[side][defined])) if defined.any() else float("nan")}

    rows = []
    for name in PATTERNS:
        signal = patterns[name].to_numpy()
        sides = (1, -1) if name in DIRECTIONLESS else (1, -1)
        for side in sides:
            if name in DIRECTIONLESS:
                hit = signal != 0            # presence, measured both ways
            else:
                hit = signal == side
            usable = hit & np.isfinite(labels[side])
            n = int(usable.sum())
            if n == 0:
                continue
            wins = float(np.nansum(labels[side][usable]))
            win_rate = wins / n
            low, high = _wilson(int(round(wins)), n)
            rows.append({"pattern": name, "side": "long" if side == 1 else "short",
                         "occurrences": n, "win_rate": round(win_rate, 4),
                         "base_rate": round(base[side]["win_rate"], 4),
                         "edge": round(win_rate - base[side]["win_rate"], 4),
                         "ci_low": round(low, 4), "ci_high": round(high, 4),
                         "beats_base_with_confidence": bool(low > base[side]["win_rate"]),
                         "enough_occurrences": n >= MIN_OCCURRENCES})
    return {"bars": int(len(frame)), "base_rates": base, "results": rows}


def measure(symbols=("XAUUSD", "BTCUSD"), timeframes=("15m", "1h", "4h")) -> dict:
    from .mtf_data import load_bars

    report = {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
              "barrier": f"+{BARRIER_ATR} / -{BARRIER_ATR} ATR, horizon {HORIZON} bars, entry next open",
              "rule": "an edge counts only with >=200 occurrences, same direction on both markets, "
                      "and on at least two of the three timeframes",
              "markets": {}}
    for symbol in symbols:
        for timeframe in timeframes:
            bars = load_bars(symbol, timeframe, source="app")
            if bars is None or bars.empty:
                report["markets"][f"{symbol}:{timeframe}"] = {"error": "no broker bars"}
                continue
            report["markets"][f"{symbol}:{timeframe}"] = measure_market(bars)
    return report


def survivors(report: dict) -> list:
    """The pre-declared rule applied: >=200 occurrences, both markets, >=2 of 3 timeframes."""
    passing = {}
    for key, market in report["markets"].items():
        if market.get("error"):
            continue
        symbol, timeframe = key.split(":")
        for row in market["results"]:
            if row["enough_occurrences"] and row["beats_base_with_confidence"]:
                passing.setdefault((row["pattern"], row["side"]), []).append((symbol, timeframe))
    out = []
    for (pattern, side), hits in sorted(passing.items()):
        markets = {symbol for symbol, _ in hits}
        frames = {timeframe for _, timeframe in hits}
        if len(markets) >= 2 and len(frames) >= 2:
            out.append({"pattern": pattern, "side": side, "on": sorted(f"{s}:{t}" for s, t in hits)})
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Measure whether candlestick patterns predict anything")
    parser.add_argument("command", choices=["measure"])
    parser.add_argument("--symbols", nargs="+", default=["XAUUSD", "BTCUSD"])
    parser.add_argument("--timeframes", nargs="+", default=["15m", "1h", "4h"])
    parser.add_argument("--out", default="data/research/candle_patterns.json")
    args = parser.parse_args(argv)

    report = measure(tuple(args.symbols), tuple(args.timeframes))
    from pathlib import Path
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")

    for key, market in report["markets"].items():
        if market.get("error"):
            print(f"\n{key}: {market['error']}")
            continue
        base_long = market["base_rates"]["1"]["win_rate"] if "1" in market["base_rates"] else market["base_rates"][1]["win_rate"]
        base_short = market["base_rates"]["-1"]["win_rate"] if "-1" in market["base_rates"] else market["base_rates"][-1]["win_rate"]
        print(f"\n=== {key}  {market['bars']:,} bars   base rate long {base_long:.3f} / short {base_short:.3f}")
        rows = sorted(market["results"], key=lambda r: -r["edge"])
        for r in rows:
            flag = "PASS" if (r["enough_occurrences"] and r["beats_base_with_confidence"]) else \
                   ("thin" if not r["enough_occurrences"] else "    ")
            print(f"  {r['pattern']:22s} {r['side']:5s} n {r['occurrences']:5d}  win {r['win_rate']:.3f}  "
                  f"base {r['base_rate']:.3f}  edge {r['edge']:+.4f}  95% [{r['ci_low']:.3f},{r['ci_high']:.3f}]  {flag}")

    kept = survivors(report)
    print(f"\n{'='*92}\nPATTERNS SATISFYING THE PRE-DECLARED RULE: {len(kept)}")
    for row in kept:
        print(f"  {row['pattern']} ({row['side']}) on {', '.join(row['on'])}")
    print(f"\nsaved {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
