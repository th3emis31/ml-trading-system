"""Strategy Lab: generate, backtest and rank rule-based strategies on broker candles.

Research only - nothing here places orders. It runs for a time budget (the hourly Windows
task "SmartEntry Strategy Lab" keeps it going around the clock), samples strategies from a
few rule families, and keeps every result in ``data/strategy_lab/registry.json``.

What keeps the search honest
----------------------------
* Each market's history is split by date the first time it is seen: SEARCH (first 60%
  after warm-up), VALIDATION (next 20%) and HOLDOUT (everything after). The boundaries are
  stored, so the holdout keeps growing with genuinely new bars.
* A candidate is ranked on SEARCH and VALIDATION only. It must be profitable in both and in
  at least 60% of the calendar years it traded. Only then is the HOLDOUT simulated.
* The holdout verdict includes the deflated Sharpe ratio (Bailey & Lopez de Prado), which
  raises the bar with the number of strategies tried in that market, so a lucky winner out
  of thousands of tries does not pass.
* Entries at the next bar's open, stop checked before target inside a bar, gaps through a
  stop fill at the open, costs from ``BACKTEST_COSTS``, one position at a time.
* Overnight swap from ``HOLDING_COSTS`` for every broker rollover a trade is held through
  (17:00 New York, Wednesday counts three nights), as MT5 charges it. Without it multi-day
  holds looked far better than on MT5 (gold D1 holdout PF 2.57 here vs 1.48 in MT5).

Run:  python -m src.strategy_lab run --minutes 45
      python -m src.strategy_lab ea          (the SwingTrendPullback expert as coded)
      python -m src.strategy_lab status
"""
from __future__ import annotations

import argparse
import copy
import functools
import hashlib
import json
import math
import os
import random
import time
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import pandas as pd

from .features import compute_atr, compute_ema, compute_rsi
from .walkforward_backtest import BACKTEST_COSTS, _iso, summarize_trades

LAB_DIR = Path("data") / "strategy_lab"
REGISTRY_PATH = LAB_DIR / "registry.json"
STATUS_PATH = LAB_DIR / "status.json"
# 1m is here for research on minute strategies (the Aurum Flow replica runs on it). It is
# deliberately NOT in DEFAULT_MARKETS: the hourly Strategy Lab search does not scan it, because
# the app serves at most 50,000 bars and 50,000 minutes is only about 35 trading days.
TIMEFRAME_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440}
DEFAULT_MARKETS = ("XAUUSD:4h", "XAUUSD:1h", "BTCUSD:4h", "BTCUSD:1h",
                   "XAUUSD:1d", "BTCUSD:1d", "XAUUSD:15m", "BTCUSD:15m")  # every symbol/timeframe the app's data feed serves
WARMUP_BARS = 300
SEARCH_FRACTION, VALIDATION_FRACTION = 0.6, 0.2
GATES = {"search_min_trades": 30, "validation_min_trades": 15, "min_profit_factor": 1.1, "min_year_share": 0.6,
         "min_trades_per_counted_year": 5}
HOLDOUT_CRITERIA = {"min_profit_factor": 1.2, "min_trades": 30, "max_drawdown_pct": 20.0, "min_deflated_sharpe": 0.95}
# expectancy_r is the after-cost R; avg_r beside it is the gross one, kept so the cost of a strategy in R is
# visible rather than hidden. Read expectancy_r when judging a candidate.
SUMMARY_KEYS = ("trades", "long_trades", "short_trades", "win_rate_pct", "profit_factor", "expectancy_pct",
                "expectancy_r", "expectancy_r_bound", "ambiguous_exits", "avg_r",
                "total_return_pct", "max_drawdown_pct", "sharpe", "years", "trades_per_year")
HEARTBEAT_STALE_SECONDS = 300
MAX_SRS_KEPT = 5000
RECENT_REJECTED_KEPT = 300  # rejected candidates keep only their id (never re-tested) plus this short list for the page

SIDES = ["long", "short", "both"]
# Named here rather than imported, because src/candle_patterns.py imports src/features.py and a
# circular import at module load would break every consumer of the lab.
_CANDLE_PATTERNS = ("marubozu", "hammer", "shooting_star", "pin_bar", "bullish_engulfing",
                    "bearish_engulfing", "piercing_line", "dark_cloud_cover", "morning_star",
                    "evening_star", "three_white_soldiers", "three_black_crows")

FAMILIES = {
    "ema_pullback": {"side": SIDES, "ema_fast": [13, 21, 34], "ema_slow": [50, 89, 100], "slope_lookback": [1, 3],
                     "push_lookback": [10, 15, 20], "push_atr": [0.4, 0.6, 0.8, 1.0], "pullback_tol": [0.3, 0.6, 0.9],
                     "bull_close": [True, False], "rsi_min": [0, 40, 50], "trend_ema": [0, 200, 300, 600]},
    "donchian_breakout": {"side": SIDES, "lookback": [20, 40, 55, 100], "trend_ema": [0, 100, 200]},
    "rsi_reversion": {"side": SIDES, "rsi_len": [2, 3, 5, 14], "lower": [10, 20, 30], "upper": [70, 80, 90],
                      "trend_ema": [0, 200]},
    "ema_cross": {"side": SIDES, "fast": [10, 20, 50], "slow": [50, 100, 200]},
    "bollinger_reversion": {"side": SIDES, "length": [20, 50], "k": [2.0, 2.5, 3.0], "trend_ema": [0, 200]},
    # Added 19 Sep 2026 so the hourly search explores new ground instead of re-testing five exhausted
    # families. Both were built and measured first, and both carry their own order builder rather than
    # a SIGNALS entry, so the exit grid does not apply to them - they set their own stop and target.
    #
    # candle_pattern: the 12 signed candlestick detectors (src/candle_patterns.py). Measured alone they
    # are worth about 2 points of win rate at best, but the three-candle reversals were consistently
    # positive across markets, and the search can look for combinations of pattern, risk and reward
    # that a single pre-declared grid could not.
    #
    # trendline_break: the mechanism read out of the owner's Aurum Flow expert, with exits scaled to
    # ATR rather than the fixed point distances that made it meaningless on bitcoin. Its best variant
    # was the closest thing to a pass found all day (deflated Sharpe 0.7504 on BTCUSD 4h).
    "candle_pattern": {"pattern": sorted(_CANDLE_PATTERNS), "rr": [1.0, 1.5, 2.0, 3.0],
                       "sl_atr": [0.75, 1.0, 1.5, 2.0]},
    "trendline_break": {"structure_depth": [100, 200, 400], "spacing": [50, 100],
                        "refresh_bars": [20, 40], "ma_period": [0, 200, 600],
                        "entry_points": [0, 130], "sl_atr_mult": [1.5, 2.5, 4.45],
                        "tp_atr_mult_ratio": [1.0, 1.5, 2.0, 3.0]},
}
EXIT_GRID = {"stop": ["atr", "swing"], "sl_atr": [1.0, 1.5, 2.0, 3.0], "rr": [1.0, 1.5, 2.0, 3.0, 0.0],
             "trail_atr": [0.0, 2.0, 3.5], "max_bars": [12, 24, 50, 150], "swing_lookback": [5]}

# The MT5 expert SwingTrendPullback.mq5 with the inputs on its XAUUSD H4 chart (strategies/swing_trend_pullback.md).
SWING_TREND_PULLBACK_SPEC = {
    "name": "SwingTrendPullback EA (as coded)",
    "family": "ema_pullback",
    "params": {"side": "long", "ema_fast": 21, "ema_slow": 50, "slope_lookback": 1, "push_lookback": 15, "push_atr": 0.6,
               "pullback_tol": 0.6, "bull_close": True, "rsi_min": 40},
    "exits": {"stop": "swing", "sl_atr": 1.5, "rr": 2.0, "trail_atr": 3.5, "max_bars": 150, "swing_lookback": 5},
}

# The user's TradingView "Swing Trend Pullback Strategy (21/50 EMA)" inputs, loaded on the live XAUUSD H4 chart as
# SwingTrendPullback_XAUUSD_H4_tradingview.set (13 Sep 2026).
SWING_TREND_PULLBACK_TRADINGVIEW_SPEC = {
    "name": "SwingTrendPullback EA (TradingView inputs)",
    "family": "ema_pullback",
    "params": {"side": "long", "ema_fast": 21, "ema_slow": 51, "slope_lookback": 1, "push_lookback": 15, "push_atr": 0.5,
               "pullback_tol": 0.55, "bull_close": True, "rsi_min": 40, "atr_len": 12},
    "exits": {"stop": "swing", "sl_atr": 1.5, "rr": 2.0, "trail_atr": 4.5, "max_bars": 150, "swing_lookback": 5},
}
EA_SPECS = {"as_coded": SWING_TREND_PULLBACK_SPEC, "tradingview": SWING_TREND_PULLBACK_TRADINGVIEW_SPEC}

# Overnight financing (swap). MT5 charges a fixed amount per lot per night at the broker rollover (17:00 New York,
# the server's midnight), three nights on Wednesday. Measured from MT5 Strategy Tester deals on Vantage XAUUSD
# (13 Sep 2026): long -82.76 USD per 100 oz lot per night = 0.8276 in price units, the same every year (the tester
# applies today's rate to all history). Every tested trade was long, so the short rate is assumed equal.
# Two ways to apply it to history:
#   "percent" (default): the same share of price per night as today (0.8276 on a 4,348 price = 0.0190%), so a
#                        2008 trade at 800 pays proportionally, not five times today's rate.
#   "price":            the fixed price amount every night, exactly like the MT5 Strategy Tester (for matching it).
HOLDING_COSTS = {
    "XAUUSD": {"long_per_night": 0.8276, "short_per_night": 0.8276, "unit": "price per unit per night",
               "long_pct_per_night": 0.0190, "short_pct_per_night": 0.0190, "calendar": "forex",
               "source": "MT5 Strategy Tester deals, Vantage demo, 2026-09-13 (gold 4,348); short side assumed equal to long"},
    # Vantage charges bitcoin a share of the price every calendar night (the per-lot amount moved with the price while the
    # percentage stayed at 0.0552-0.0564% in 2025 and 2026). 51.76 USD per 1 BTC per night is the median at 2025-26 prices.
    "BTCUSD": {"long_per_night": 51.76, "short_per_night": 51.76, "unit": "price per unit per night",
               "long_pct_per_night": 0.0560, "short_pct_per_night": 0.0560, "calendar": "crypto",
               "source": "MT5 Strategy Tester deals, Vantage demo BTCUSD H4 2025-01..2026-09 (54 overnight trades, 1 BTC per lot), "
                         "2026-09-14; short side assumed equal to long"},
}
SWAP_MODES = ("percent", "price")
ROLLOVER_WEIGHTS = (1, 1, 3, 1, 1, 0, 0)  # rollovers Monday..Sunday; Wednesday's carries the weekend
ROLLOVER_CALENDARS = {"forex": ROLLOVER_WEIGHTS, "crypto": (1, 1, 1, 1, 1, 1, 1)}  # crypto CFDs are financed every night
COST_MODEL = "spread+swap-v2"


def rollover_counts(times, weekday_weights=ROLLOVER_WEIGHTS) -> np.ndarray:
    """Weighted broker rollovers (17:00 New York) passed at each bar time, cumulative.

    Nights charged for a position opened at bar i and closed at bar j = counts[j] - counts[i].
    ``weekday_weights`` are the nights charged by the Monday..Sunday rollovers (see ROLLOVER_CALENDARS).
    """
    stamps = pd.to_datetime(pd.Series(times), utc=True).dt.tz_convert("America/New_York").dt.tz_localize(None)
    # Day index of the latest rollover already passed, counted from Monday 1970-01-05 (so index % 7 == 0 is Monday).
    day = ((stamps - pd.Timedelta(hours=17)).dt.floor("D") - pd.Timestamp("1970-01-05")).dt.days.to_numpy()
    first, last = int(day.min()) - 1, int(day.max())
    weights = np.array([weekday_weights[d % 7] for d in range(first, last + 1)], dtype=float)
    return np.cumsum(weights)[day - first]


# --------------------------------------------------------------------------- indicators and signals
class Indicators:
    """Causal indicator arrays for one bar frame, computed once and cached."""

    def __init__(self, bars: pd.DataFrame):
        self.df = bars.reset_index(drop=True)
        self.o = self.df["open"].to_numpy(dtype=float)
        self.h = self.df["high"].to_numpy(dtype=float)
        self.l = self.df["low"].to_numpy(dtype=float)
        self.c = self.df["close"].to_numpy(dtype=float)
        self.times = pd.to_datetime(self.df["datetime"], utc=True)
        self._cache: dict = {}

    def _cached(self, key, build):
        if key not in self._cache:
            self._cache[key] = build()
        return self._cache[key]

    def ema(self, span: int) -> np.ndarray:
        return self._cached(("ema", span), lambda: compute_ema(self.df["close"].astype(float), span).to_numpy())

    def rsi(self, length: int) -> np.ndarray:
        return self._cached(("rsi", length), lambda: compute_rsi(self.df["close"].astype(float), length).to_numpy())

    def atr(self, length: int = 14) -> np.ndarray:
        return self._cached(("atr", length), lambda: compute_atr(self.df.astype({"high": float, "low": float,
                                                                                    "close": float}), length).to_numpy())

    def sma(self, length: int) -> np.ndarray:
        return self._cached(("sma", length), lambda: self.df["close"].astype(float).rolling(length).mean().to_numpy())

    def std(self, length: int) -> np.ndarray:
        return self._cached(("std", length), lambda: self.df["close"].astype(float).rolling(length).std().to_numpy())

    def highest(self, length: int, shift: int = 0) -> np.ndarray:
        return self._cached(("hh", length, shift), lambda: pd.Series(self.h).rolling(length).max().shift(shift).to_numpy())

    def lowest(self, length: int, shift: int = 0) -> np.ndarray:
        return self._cached(("ll", length, shift), lambda: pd.Series(self.l).rolling(length).min().shift(shift).to_numpy())


def _rolling_max(values: np.ndarray, length: int) -> np.ndarray:
    return pd.Series(values).rolling(length).max().to_numpy()


def ema_pullback_conditions(ind: Indicators, p: dict) -> dict:
    """Each condition of the SwingTrendPullback entry per bar (the one source for the signals and the daily plan)."""
    fast, slow, atr, rsi = ind.ema(p["ema_fast"]), ind.ema(p["ema_slow"]), ind.atr(int(p.get("atr_len", 14))), ind.rsi(14)
    fast_prev = pd.Series(fast).shift(p["slope_lookback"]).to_numpy()
    tol = p["pullback_tol"] * atr
    o, h, l, c = ind.o, ind.h, ind.l, ind.c
    always = np.ones(len(c), dtype=bool)
    return {
        "fast": fast, "slow": slow, "atr": atr, "rsi": rsi, "tolerance": tol,
        "trend_up": (fast > slow) & (fast > fast_prev),
        "push_up": _rolling_max(h - slow, p["push_lookback"]) > p["push_atr"] * atr,
        "touch_up": (l <= fast + tol) & (c >= fast - tol) & (c > slow),
        "close_up": c > o if p["bull_close"] else always,
        "rsi_up": rsi > p["rsi_min"],
        "trend_down": (fast < slow) & (fast < fast_prev),
        "push_down": _rolling_max(slow - l, p["push_lookback"]) > p["push_atr"] * atr,
        "touch_down": (h >= fast - tol) & (c <= fast + tol) & (c < slow),
        "close_down": c < o if p["bull_close"] else always,
        "rsi_down": rsi < 100 - p["rsi_min"],
    }


def _signals_ema_pullback(ind: Indicators, p: dict):
    cond = ema_pullback_conditions(ind, p)
    long_sig = cond["trend_up"] & cond["push_up"] & cond["touch_up"] & cond["close_up"] & cond["rsi_up"]
    short_sig = cond["trend_down"] & cond["push_down"] & cond["touch_down"] & cond["close_down"] & cond["rsi_down"]
    up, down = _trend_masks(ind, p.get("trend_ema", 0))  # optional long-term filter (EA input TrendFilterEmaLen)
    return long_sig & up, short_sig & down


def _trend_masks(ind: Indicators, span: int):
    if not span:
        ones = np.ones(len(ind.c), dtype=bool)
        return ones, ones
    trend = ind.ema(span)
    return ind.c > trend, ind.c < trend


def _signals_donchian_breakout(ind: Indicators, p: dict):
    up, down = _trend_masks(ind, p["trend_ema"])
    return (ind.c > ind.highest(p["lookback"], shift=1)) & up, (ind.c < ind.lowest(p["lookback"], shift=1)) & down


def _signals_rsi_reversion(ind: Indicators, p: dict):
    up, down = _trend_masks(ind, p["trend_ema"])
    rsi = ind.rsi(p["rsi_len"])
    return (rsi < p["lower"]) & up, (rsi > p["upper"]) & down


def _signals_ema_cross(ind: Indicators, p: dict):
    fast, slow = ind.ema(p["fast"]), ind.ema(p["slow"])
    above = fast > slow
    was_above = pd.Series(above).shift(1, fill_value=False).to_numpy(dtype=bool)
    below = fast < slow
    was_below = pd.Series(below).shift(1, fill_value=False).to_numpy(dtype=bool)
    return above & ~was_above, below & ~was_below


def _signals_bollinger_reversion(ind: Indicators, p: dict):
    up, down = _trend_masks(ind, p["trend_ema"])
    mid, sd = ind.sma(p["length"]), ind.std(p["length"])
    return (ind.c < mid - p["k"] * sd) & up, (ind.c > mid + p["k"] * sd) & down


SIGNALS: dict[str, Callable] = {
    "ema_pullback": _signals_ema_pullback,
    "donchian_breakout": _signals_donchian_breakout,
    "rsi_reversion": _signals_rsi_reversion,
    "ema_cross": _signals_ema_cross,
    "bollinger_reversion": _signals_bollinger_reversion,
}


# Families whose stops and targets come from their own pattern (for example the MT4 CRT expert in src/crt_lab.py)
# register a builder here: builder(ind, spec) -> (side, stop, target), the same arrays strategy_orders returns.
ORDER_BUILDERS: dict = {}


def strategy_orders(ind: Indicators, spec: dict) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per bar: side (+1/-1/0), stop and target computed from that closed bar (NaN without a signal)."""
    builder = ORDER_BUILDERS.get(spec["family"])
    if builder is not None:
        return builder(ind, spec)
    params, exits = spec["params"], spec["exits"]
    long_sig, short_sig = SIGNALS[spec["family"]](ind, params)
    with np.errstate(invalid="ignore"):
        side = long_sig.astype(int) - short_sig.astype(int)
        if params.get("side") == "long":
            side[side < 0] = 0
        elif params.get("side") == "short":
            side[side > 0] = 0
        atr, close = ind.atr(int(params.get("atr_len", 14))), ind.c
        if exits["stop"] == "swing":
            low_anchor, high_anchor = ind.lowest(exits["swing_lookback"]), ind.highest(exits["swing_lookback"])
            if spec["family"] == "ema_pullback":  # the expert anchors on min(swing low, EMA fast)
                fast = ind.ema(params["ema_fast"])
                low_anchor, high_anchor = np.fmin(low_anchor, fast), np.fmax(high_anchor, fast)
            long_stop, short_stop = low_anchor - exits["sl_atr"] * atr, high_anchor + exits["sl_atr"] * atr
        else:
            long_stop, short_stop = close - exits["sl_atr"] * atr, close + exits["sl_atr"] * atr
        stop = np.where(side == 1, long_stop, np.where(side == -1, short_stop, np.nan))
        risk = np.where(side == 1, close - stop, np.where(side == -1, stop - close, np.nan))
        valid = (side != 0) & np.isfinite(stop) & np.isfinite(atr) & (atr > 0) & (risk > 0)
        side = np.where(valid, side, 0)
        stop = np.where(valid, stop, np.nan)
        target = np.full(len(close), np.nan)
        if exits["rr"] and exits["rr"] > 0:
            target = np.where(side == 1, close + exits["rr"] * risk, np.where(side == -1, close - exits["rr"] * risk, np.nan))
    return side.astype(int), stop, target


# --------------------------------------------------------------------------- simulation
def simulate_orders(o, h, l, c, atr, times, side, stop, target, rows, exits: dict, cost_pct: float,
                    holding: Optional[dict] = None, entry_prices=None) -> list[dict]:
    """One position at a time over signal bars in ``rows``; entry at the next bar's open.

    ``entry_prices`` (optional, per signal bar): a resting limit order filled on the next bar at that price. The
    builder must only set it when that bar trades through the limit. A target is not credited on the fill bar
    (the bar may have reached it before the fill); a stop touched on the fill bar still counts.

    ``holding`` charges overnight swap: {"roll": rollover_counts(times), "mode": "percent" | "price", "long": ..,
    "short": ..} per night (percent of the entry price, or price units); None keeps the spread-only cost model.
    """
    n = len(o)
    trail_atr = float(exits.get("trail_atr") or 0.0)
    max_bars = int(exits.get("max_bars") or 10 ** 9)
    # Optional stop management like the MT4 CRT expert (absent keys change nothing): move the stop to entry +
    # be_lock_price once the best price reaches be_trigger_r x the initial risk; trail trail_dist_price behind the
    # best price once it is trail_start_price in profit, or trail_dist_r x risk once it is trail_start_r x risk in
    # profit. Checked on the previous bar's extreme, so the stop moves from the next bar on.
    be_trigger_r = float(exits.get("be_trigger_r") or 0.0)
    be_lock = float(exits.get("be_lock_price") or 0.0)
    trail_start_price = float(exits.get("trail_start_price") or 0.0)
    trail_dist_price = float(exits.get("trail_dist_price") or 0.0)
    trail_start_r = float(exits.get("trail_start_r") or 0.0)
    trail_dist_r = float(exits.get("trail_dist_r") or 0.0)
    managed = be_trigger_r > 0 or trail_dist_price > 0 or trail_dist_r > 0
    # Optional swap avoidance (needs ``holding``): close at the close of the last bar before a rollover that would
    # charge three nights ("exit_before_triple_swap") or any night ("exit_before_rollover"), so that night is not paid.
    roll = holding["roll"] if holding is not None else None
    exit_triple = bool(exits.get("exit_before_triple_swap")) and roll is not None
    exit_any = bool(exits.get("exit_before_rollover")) and roll is not None
    rows = np.asarray(rows)
    trades: list[dict] = []
    free_from = -1
    for t in rows[side[rows] != 0]:
        t = int(t)
        if t < free_from or t + 1 >= n:
            continue
        s = int(side[t])
        stop0, tgt, entry_i = float(stop[t]), float(target[t]), t + 1
        limit_fill = entry_prices is not None and np.isfinite(entry_prices[t])
        entry = float(entry_prices[t]) if limit_fill else o[entry_i]
        if not np.isfinite(entry) or entry <= 0 or (s == 1 and entry <= stop0) or (s == -1 and entry >= stop0):
            continue
        if np.isfinite(tgt) and ((s == 1 and entry >= tgt) or (s == -1 and entry <= tgt)):
            continue
        current_stop, extreme = stop0, (h[t] if s == 1 else l[t])
        best, risk0 = entry, abs(entry - stop0)
        ambiguous = False
        exit_price = outcome = None
        j = entry_i
        while j < n:
            if j - entry_i >= max_bars:
                exit_price, outcome = o[j], "TIME"
                break
            if j > entry_i and (exit_triple or exit_any):
                nights_ahead = roll[j] - roll[j - 1]
                if (exit_any and nights_ahead > 0) or (exit_triple and nights_ahead >= 3):
                    j -= 1
                    exit_price, outcome = c[j], "SWAP_EXIT"
                    break
            if j > entry_i:
                if trail_atr > 0 and np.isfinite(atr[j - 1]):
                    if s == 1:
                        extreme = max(extreme, h[j - 1])
                        current_stop = max(current_stop, extreme - trail_atr * atr[j - 1])
                    else:
                        extreme = min(extreme, l[j - 1])
                        current_stop = min(current_stop, extreme + trail_atr * atr[j - 1])
                if managed:
                    best = max(best, h[j - 1]) if s == 1 else min(best, l[j - 1])
                    profit = s * (best - entry)
                    moves = []
                    if be_trigger_r > 0 and profit >= be_trigger_r * risk0:
                        moves.append(entry + s * be_lock)
                    if trail_dist_price > 0 and profit >= trail_start_price:
                        moves.append(best - s * trail_dist_price)
                    if trail_dist_r > 0 and profit >= trail_start_r * risk0:
                        moves.append(best - s * trail_dist_r * risk0)
                    for level in moves:
                        current_stop = max(current_stop, level) if s == 1 else min(current_stop, level)
                if (s == 1 and o[j] <= current_stop) or (s == -1 and o[j] >= current_stop):
                    exit_price, outcome = o[j], "STOP"
                    break
            if (s == 1 and l[j] <= current_stop) or (s == -1 and h[j] >= current_stop):
                # This bar touched the stop. If it ALSO reached the target, a single bar cannot say which came
                # first, and booking the stop is a choice, not a measurement. The choice stays (pessimism is the
                # safe default) but it is now counted, because a strategy whose result rests on many such bars
                # has not been measured at all - the weekly-structure test moved two of four variants from
                # losing to winning once the order of touches was resolved on finer bars.
                if np.isfinite(tgt) and not (limit_fill and j == entry_i) and (
                        (s == 1 and h[j] >= tgt) or (s == -1 and l[j] <= tgt)):
                    ambiguous = True
                exit_price, outcome = current_stop, "STOP"
                break
            if np.isfinite(tgt) and not (limit_fill and j == entry_i) and ((s == 1 and h[j] >= tgt) or (s == -1 and l[j] <= tgt)):
                exit_price, outcome = tgt, "TARGET"
                break
            j += 1
        if exit_price is None:
            break  # still open when the data ends: not counted
        if outcome == "STOP" and current_stop != stop0:
            outcome = "TRAIL"
        gross = s * (exit_price - entry) / entry
        nights, swap_frac = 0.0, 0.0
        if holding is not None:
            nights = float(holding["roll"][j] - holding["roll"][entry_i])
            rate = float(holding["long"] if s == 1 else holding["short"])
            swap_frac = nights * (rate / entry if holding.get("mode") == "price" else rate / 100.0)
        trades.append({
            "entry_idx": t, "side": "BUY" if s == 1 else "SELL",
            "entry_time": _iso(times[entry_i]), "exit_time": _iso(times[j]), "outcome": outcome,
            "entry_price": round(float(entry), 3), "exit_price": round(float(exit_price), 3),
            "bars_held": int(j - entry_i), "gross_pct": round(gross * 100, 4),
            "net_pct": round((gross - cost_pct - swap_frac) * 100, 4),
            "nights": nights, "swap_pct": round(swap_frac * 100, 4),
            "r_multiple": round(gross * entry / abs(entry - stop0), 3),
            # R AFTER costs. r_multiple above is gross, which flattered every expectancy this lab has ever
            # reported: spread and swap are a fixed slice of the entry price, so on a tight stop they eat a
            # large fraction of one R. summarize_trades reports expectancy_r from this field, never from the
            # gross one. Same convention as walkforward_backtest, which had it from the start.
            "net_r": round((gross - cost_pct - swap_frac) * entry / abs(entry - stop0), 4),
            # True when the exit bar reached both the stop and the target, so this trade's outcome was assigned
            # by the engine's pessimism rather than read off the data.
            "ambiguous_exit": bool(ambiguous),
            # The R this trade would have returned at its target, net of costs: what an ambiguous exit is worth
            # if the target came first. Read by summarize_trades for the pessimism bound.
            "target_r": (round((s * (tgt - entry) / entry - cost_pct - swap_frac) * entry / abs(entry - stop0), 4)
                         if np.isfinite(tgt) else None),
        })
        free_from = j
    return trades


# --------------------------------------------------------------------------- markets and evaluation
class Market:
    """Closed bars, cached indicators and date-fixed search / validation / holdout rows for one symbol+timeframe."""

    def __init__(self, symbol: str, timeframe: str, bars: pd.DataFrame, boundaries: Optional[dict] = None, now=None,
                 swap: bool = True, swap_mode: str = "percent"):
        from .paper_trader import drop_forming_bars

        minutes = TIMEFRAME_MINUTES[timeframe]
        now = pd.Timestamp(now) if now is not None else pd.Timestamp.now(tz="UTC")
        frame = drop_forming_bars(bars, minutes, now).sort_values("datetime").reset_index(drop=True)
        self.symbol, self.timeframe, self.key = symbol.upper(), timeframe, f"{symbol.upper()}:{timeframe}"
        n = len(frame)
        if n < WARMUP_BARS + 500:
            raise ValueError(f"{self.key}: only {n} closed bars")
        self.ind = Indicators(frame)
        times = self.ind.times
        if boundaries:
            stamps = times.dt.tz_convert(None).to_numpy(dtype="datetime64[ns]")

            def _position(text: str) -> int:
                return int(np.searchsorted(stamps, pd.Timestamp(text, tz="UTC").tz_convert(None).to_datetime64()))

            validation_start = _position(boundaries["validation_start"])
            holdout_start = _position(boundaries["holdout_start"])
        else:
            usable = n - WARMUP_BARS
            validation_start = WARMUP_BARS + int(usable * SEARCH_FRACTION)
            holdout_start = WARMUP_BARS + int(usable * (SEARCH_FRACTION + VALIDATION_FRACTION))
        validation_start = max(WARMUP_BARS + 1, min(validation_start, n - 2))
        holdout_start = max(validation_start + 1, min(holdout_start, n - 1))
        self.rows = {"search": np.arange(WARMUP_BARS, validation_start), "validation": np.arange(validation_start, holdout_start),
                     "holdout": np.arange(holdout_start, n)}
        self.boundaries = {"validation_start": _iso(times.iloc[validation_start]), "holdout_start": _iso(times.iloc[holdout_start])}
        self.cost_pct = BACKTEST_COSTS.get(self.symbol, BACKTEST_COSTS["default"])["round_trip_pct"]
        if swap_mode not in SWAP_MODES:
            raise ValueError(f"swap_mode must be one of {SWAP_MODES}")
        holding_spec = HOLDING_COSTS.get(self.symbol) if swap else None
        self.holding = None
        if holding_spec:
            suffix = "_per_night" if swap_mode == "price" else "_pct_per_night"
            calendar = ROLLOVER_CALENDARS[holding_spec.get("calendar", "forex")]
            self.holding = {"roll": rollover_counts(times, calendar), "mode": swap_mode,
                            "long": holding_spec["long" + suffix], "short": holding_spec["short" + suffix]}
        self.info = {"symbol": self.symbol, "timeframe": timeframe, "bars": n, "data_start": _iso(times.iloc[0]),
                     "data_end": _iso(times.iloc[-1]), "boundaries": self.boundaries, "cost_round_trip_pct": self.cost_pct,
                     "cost_model": f"{COST_MODEL}-{swap_mode}" if self.holding else "spread-only", "holding_costs": holding_spec,
                     "periods": {name: f"{_iso(times.iloc[int(r[0])])} to {_iso(times.iloc[int(r[-1])])}"
                                 for name, r in self.rows.items() if len(r)}}

    def simulate(self, spec: dict, split: str, orders=None) -> list[dict]:
        orders = orders if orders is not None else strategy_orders(self.ind, spec)
        side, stop, target = orders[:3]
        entry_prices = orders[3] if len(orders) > 3 else None   # builders with limit entries return a fourth array
        ind = self.ind
        atr = ind.atr(int(spec["params"].get("atr_len", 14)))
        return simulate_orders(ind.o, ind.h, ind.l, ind.c, atr, ind.times, side, stop, target, self.rows[split],
                               spec["exits"], self.cost_pct, self.holding, entry_prices)

    def summary(self, split: str, trades: list[dict]) -> dict:
        rows, times, close = self.rows[split], self.ind.times, self.ind.c
        start, end = times.iloc[int(rows[0])], times.iloc[int(rows[-1])]
        metrics = summarize_trades(trades, test_start=start, test_end=end, test_bars=int(len(rows)),
                                   bars_in_market=int(sum(t["bars_held"] for t in trades)))
        out = {key: metrics.get(key) for key in SUMMARY_KEYS}
        out["period"] = f"{_iso(start)} to {_iso(end)}"
        first, last = float(close[int(rows[0])]), float(close[int(rows[-1])])
        out["buy_and_hold_pct"] = round((last / first - 1) * 100, 3) if first else None
        return out


def spec_id(spec: dict) -> str:
    canonical = json.dumps({"family": spec["family"], "params": spec["params"], "exits": spec["exits"]}, sort_keys=True)
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:12]


def _profit_factor(summary: dict) -> float:
    pf = summary.get("profit_factor")
    if pf is None:
        return 3.0 if (summary.get("trades") or 0) > 0 and (summary.get("total_return_pct") or 0) > 0 else 0.0
    return float(pf)


def year_consistency(trades: list[dict], min_trades: int) -> tuple[float, int]:
    by_year: dict[str, list[float]] = {}
    for trade in trades:
        by_year.setdefault(trade["entry_time"][:4], []).append(trade["net_pct"] / 100.0)
    counted = [float(np.prod([1 + r for r in rets]) - 1) for rets in by_year.values() if len(rets) >= min_trades]
    if not counted:
        return 0.0, 0
    return round(sum(1 for value in counted if value > 0) / len(counted), 3), len(counted)


def candidate_score(search: dict, validation: dict) -> float:
    trades = (search.get("trades") or 0) + (validation.get("trades") or 0)
    drawdown = max(search.get("max_drawdown_pct") or 0.0, validation.get("max_drawdown_pct") or 0.0)
    return round((min(_profit_factor(search), _profit_factor(validation)) - 1.0) * math.sqrt(trades) / (1.0 + drawdown / 20.0), 4)


@functools.lru_cache(maxsize=4096)
def _expected_max_factor(trials: int) -> float:
    """(1 - gamma) * z(1 - 1/N) + gamma * z(1 - 1/(N e)): depends only on the trial count, so it is cached."""
    from scipy.stats import norm

    euler = 0.5772156649
    return float((1 - euler) * norm.ppf(1 - 1.0 / trials) + euler * norm.ppf(1 - 1.0 / (trials * math.e)))


def deflated_sharpe(returns, n_trials: int, sr_variance: float) -> Optional[float]:
    """Probability that the true per-trade Sharpe exceeds what the best of ``n_trials`` random tries would show.

    Skewness and kurtosis are the plain moment estimates (scipy's skew() and kurtosis(fisher=False) defaults),
    computed with numpy: the summaries call this thousands of times and scipy's per-call overhead made the
    Strategy Lab page take 17 s to load.
    """
    values = np.asarray(returns, dtype=float)
    if len(values) < 10:
        return None
    sd = float(values.std(ddof=1))
    if sd <= 0:
        return None
    sr = float(values.mean()) / sd
    trials = max(int(n_trials), 2)
    sr0 = math.sqrt(max(float(sr_variance), 1e-6)) * _expected_max_factor(trials)
    centered = values - values.mean()
    m2 = float(np.mean(centered ** 2))
    if m2 <= 0:
        return None
    skewness = float(np.mean(centered ** 3)) / m2 ** 1.5
    kurt = float(np.mean(centered ** 4)) / m2 ** 2
    denominator = 1 - skewness * sr + (kurt - 1) / 4 * sr ** 2
    if denominator <= 0:
        return None
    z = (sr - sr0) * math.sqrt(len(values) - 1) / math.sqrt(denominator)
    return round(0.5 * (1.0 + math.erf(z / math.sqrt(2.0))), 4)


def _variance(values: list) -> float:
    """Variance of the true per-trade Sharpe across trials: observed spread minus sampling noise.

    Entries are [sharpe, trades] pairs (older registries stored bare values). An estimate from T trades
    carries noise of about (1 + SR^2 / 2) / T; left in, small-sample candidates inflate the deflation
    bar until even a consistent strategy with hundreds of trades scores zero.
    """
    pairs = [(float(v[0]), int(v[1])) if isinstance(v, (list, tuple)) else (float(v), 0) for v in values]
    if len(pairs) < 2:
        return 0.01
    observed = float(np.var([sr for sr, _ in pairs], ddof=1))
    noise = [(1 + sr * sr / 2) / trades for sr, trades in pairs if trades > 0]
    if not noise:
        return max(observed, 1e-4)
    mean_noise = float(np.mean(noise))
    # Hundreds of near-identical variants make "observed minus noise" collapse to zero, which would make the
    # deflation bar vanish; assume the true spread is at least a quarter of the sampling noise.
    return max(observed - mean_noise, 0.25 * mean_noise)


def near_misses(registry: Optional[dict] = None, limit: int = 12) -> list:
    """Candidates that fail exactly one holdout check, and by how much.

    Added 19 Sep 2026. "9,500 candidates, none passed" was true and useless: it hid the fact that on
    XAUUSD:1d a Donchian breakout reaches a deflated Sharpe of 0.963, clearing the 0.95 bar, and fails
    only because its holdout has 19 trades against the 30 minimum - a number that grows on its own as
    the holdout does. Naming what each candidate still needs is the difference between a wall and a
    target, and needs no threshold to move.
    """
    registry = registry if registry is not None else load_registry()
    records = registry.get("candidates") or registry.get("records") or {}
    rows = list(records.values()) if isinstance(records, dict) else list(records)
    out = []
    for record in rows:
        if not isinstance(record, dict):
            continue
        verdict = record.get("holdout_verdict") or {}
        checks = verdict.get("checks") or {}
        if not checks or verdict.get("passed"):
            continue
        failing = [name for name, ok in checks.items() if not ok]
        if len(failing) != 1:
            continue
        holdout = record.get("holdout") or {}
        missing = failing[0]
        if missing == "trades":
            short = f"{HOLDOUT_CRITERIA['min_trades'] - (holdout.get('trades') or 0)} more holdout trades"
        elif missing == "deflated_sharpe":
            dsr = verdict.get("deflated_sharpe")
            short = (f"deflated Sharpe {dsr} against {HOLDOUT_CRITERIA['min_deflated_sharpe']}"
                     if dsr is not None else "a measurable deflated Sharpe")
        elif missing == "max_drawdown":
            short = (f"drawdown {holdout.get('max_drawdown_pct')}% against "
                     f"{HOLDOUT_CRITERIA['max_drawdown_pct']}%")
        elif missing == "profit_factor":
            short = (f"profit factor {holdout.get('profit_factor')} against "
                     f"{HOLDOUT_CRITERIA['min_profit_factor']}")
        else:
            short = f"a positive holdout return (it is {holdout.get('total_return_pct')}%)"
        out.append({"id": record.get("id"), "market": record.get("market"),
                    "description": record.get("description"),
                    "only_missing": missing, "needs": short,
                    "deflated_sharpe": verdict.get("deflated_sharpe"),
                    "per_trade_sharpe": verdict.get("per_trade_sharpe"),
                    "per_trade_sharpe_needed": verdict.get("per_trade_sharpe_needed"),
                    "holdout": {k: holdout.get(k) for k in
                                ("trades", "profit_factor", "total_return_pct", "max_drawdown_pct")}})
    # closest first: a candidate needing a few more trades is nearer than one needing a better Sharpe
    order = {"trades": 0, "deflated_sharpe": 1, "max_drawdown": 2, "profit_factor": 3, "positive_return": 4}
    out.sort(key=lambda r: (order.get(r["only_missing"], 9), -(r["deflated_sharpe"] or 0)))
    return out[:limit]


def holdout_verdict(record: dict, n_trials: int, sr_variance: float) -> Optional[dict]:
    holdout = record.get("holdout")
    if not holdout:
        return None
    dsr = deflated_sharpe(np.asarray(record.get("holdout_returns") or [], dtype=float) / 100.0, n_trials, sr_variance)
    checks = {
        "profit_factor": _profit_factor(holdout) >= HOLDOUT_CRITERIA["min_profit_factor"],
        "trades": (holdout.get("trades") or 0) >= HOLDOUT_CRITERIA["min_trades"],
        "max_drawdown": (holdout.get("max_drawdown_pct") if holdout.get("max_drawdown_pct") is not None else 999)
        <= HOLDOUT_CRITERIA["max_drawdown_pct"],
        "positive_return": (holdout.get("total_return_pct") or -1) > 0,
        "deflated_sharpe": dsr is not None and dsr >= HOLDOUT_CRITERIA["min_deflated_sharpe"],
    }
    # summarize_trades returns numpy floats, so comparisons give numpy bools that JSON cannot encode.
    checks = {name: bool(value) for name, value in checks.items()}
    # "failed the deflated Sharpe" on its own tells a reader nothing they can act on. These two numbers
    # turn the bar into a target: what per-trade Sharpe this holdout achieved, and what it would have
    # needed against this many trials. Measured 19 Sep 2026 on XAUUSD:4h, the requirement is about 0.41
    # per-trade Sharpe at 18,230 cumulative trials, which a real strategy can reach - nothing in 18,000
    # candidates has, and that is a finding about the families rather than about the bar.
    target = sharpe_target(n_trials, sr_variance)
    achieved = per_trade_sharpe(record.get("holdout_returns") or [])
    return {"passed": all(checks.values()), "checks": checks, "deflated_sharpe": dsr, "n_trials": int(n_trials),
            "per_trade_sharpe": achieved, "per_trade_sharpe_needed": target,
            "shortfall": (round(target - achieved, 4) if achieved is not None and target is not None else None),
            "deflation_note": _deflation_note(achieved, target, dsr),
            # Informational, never a blocker: whether this holdout's sign survives the engine's own pessimism about
            # bars that touched the stop and the target together. If the measured expectancy and its bound sit on
            # opposite sides of zero, the candidate has not been measured and needs finer bars before it is judged.
            "outcome_is_resolved": _outcome_is_resolved(holdout),
            "beats_buy_and_hold": bool((holdout.get("total_return_pct") or -999) > (holdout.get("buy_and_hold_pct") or 0))}


def _outcome_is_resolved(summary: dict) -> Optional[bool]:
    """False when the engine's stop-before-target choice, not the data, decides whether this split made money.

    ``expectancy_r_bound`` is what the split would have returned had every bar that touched both levels resolved
    in the strategy's favour instead. None (no ambiguous exits) means nothing was assumed, so it is resolved.
    """
    measured, bound = summary.get("expectancy_r"), summary.get("expectancy_r_bound")
    if measured is None:
        return None
    if bound is None:
        return True
    return bool((measured > 0) == (bound > 0))


def per_trade_sharpe(returns_pct) -> Optional[float]:
    """Mean over standard deviation of the holdout's per-trade returns. The quantity the bar is about."""
    values = np.asarray(returns_pct or [], dtype=float)
    if len(values) < 2:
        return None
    spread = float(values.std(ddof=1))
    if spread <= 0:
        return None
    return round(float(values.mean()) / spread, 4)


def sharpe_target(n_trials: int, sr_variance: float) -> Optional[float]:
    """The per-trade Sharpe a candidate must beat to start clearing the deflation, at this trial count.

    This is sr0 from the deflated Sharpe: sqrt(variance of true Sharpes across trials) times the
    expected maximum of that many draws. Reporting it does not change the bar; it names it.
    """
    try:
        return round(math.sqrt(max(float(sr_variance), 1e-6)) * _expected_max_factor(max(int(n_trials), 2)), 4)
    except Exception:
        return None


def _deflation_note(achieved: Optional[float], target: Optional[float], dsr: Optional[float]) -> str:
    if achieved is None or target is None:
        return "too few holdout trades to measure a per-trade Sharpe"
    if dsr is not None and dsr >= HOLDOUT_CRITERIA["min_deflated_sharpe"]:
        return f"per-trade Sharpe {achieved} clears the {target} needed against this many trials"
    return (f"per-trade Sharpe {achieved} against the {target} needed at this trial count; "
            f"short by {round(target - achieved, 4)}")


def evaluate_candidate(market: Market, spec: dict, with_holdout: bool = False) -> dict:
    """Search and validation always; the holdout only for candidates that pass the gates (or when asked)."""
    orders = strategy_orders(market.ind, spec)
    trades = {split: market.simulate(spec, split, orders) for split in ("search", "validation")}
    search, validation = market.summary("search", trades["search"]), market.summary("validation", trades["validation"])
    year_share, years_counted = year_consistency(trades["search"] + trades["validation"], GATES["min_trades_per_counted_year"])
    reasons = []
    if (search["trades"] or 0) < GATES["search_min_trades"]:
        reasons.append(f"search: only {search['trades']} trades")
    if (validation["trades"] or 0) < GATES["validation_min_trades"]:
        reasons.append(f"validation: only {validation['trades']} trades")
    for name, summary in (("search", search), ("validation", validation)):
        if (summary["trades"] or 0) and (_profit_factor(summary) < GATES["min_profit_factor"] or (summary["total_return_pct"] or 0) <= 0):
            reasons.append(f"{name}: not profitable (PF {summary['profit_factor']}, {summary['total_return_pct']}%)")
    if years_counted < 2 or year_share < GATES["min_year_share"]:
        reasons.append(f"profitable in {year_share:.0%} of {years_counted} years")
    val_returns = np.array([t["net_pct"] / 100.0 for t in trades["validation"]], dtype=float)
    val_sr = float(val_returns.mean() / val_returns.std(ddof=1)) if len(val_returns) >= 2 and val_returns.std(ddof=1) > 0 else None
    record = {
        "id": spec_id(spec), "market": market.key, "family": spec["family"], "spec": spec, "description": describe_spec(spec),
        "search": search, "validation": validation, "year_share": year_share, "years_counted": years_counted,
        "validated": not reasons, "gate_reasons": reasons, "score": candidate_score(search, validation),
        "validation_sr": round(val_sr, 5) if val_sr is not None else None,
        "evaluated_at": _iso(pd.Timestamp.now(tz="UTC")), "data_end": market.info["data_end"],
        "cost_model": market.info["cost_model"],
        "status": "validated" if not reasons else "rejected",
    }
    if not reasons or with_holdout:
        holdout_trades = market.simulate(spec, "holdout", orders)
        record["holdout"] = market.summary("holdout", holdout_trades)
        record["holdout_returns"] = [t["net_pct"] for t in holdout_trades]
        record["holdout_recent_trades"] = [{k: v for k, v in t.items() if k != "entry_idx"} for t in holdout_trades[-10:]]
    return record


def describe_spec(spec: dict) -> str:
    if spec.get("description"):
        return str(spec["description"])
    p, x = spec["params"], spec["exits"]
    family = spec["family"]
    if family == "ema_pullback":
        entry = (f"EMA{p['ema_fast']}/{p['ema_slow']} pullback, push {p['push_lookback']} bars > {p['push_atr']} ATR, "
                 f"tolerance {p['pullback_tol']} ATR{', candle close in direction' if p['bull_close'] else ''}"
                 f"{f', RSI filter {p[chr(114) + chr(115) + chr(105) + chr(95) + chr(109) + chr(105) + chr(110)]}' if p['rsi_min'] else ''}")
    elif family == "donchian_breakout":
        entry = f"close beyond {p['lookback']}-bar high/low" + (f", EMA{p['trend_ema']} trend filter" if p["trend_ema"] else "")
    elif family == "rsi_reversion":
        entry = f"RSI{p['rsi_len']} below {p['lower']} / above {p['upper']}" + (f", EMA{p['trend_ema']} trend filter" if p["trend_ema"] else "")
    elif family == "ema_cross":
        entry = f"EMA{p['fast']} crosses EMA{p['slow']}"
    elif family == "bollinger_reversion":
        entry = f"close outside {p['length']}-bar Bollinger {p['k']} SD" + (f", EMA{p['trend_ema']} trend filter" if p["trend_ema"] else "")
    elif family == "candle_pattern":
        # These families set their own stop and target, so the exit grid below does not describe them.
        return (f"{p['pattern'].replace('_', ' ')} at {p.get('rr', 1)}R, "
                f"stop {p.get('sl_atr', 1.0)} ATR, entry at the next open")
    elif family == "trendline_break":
        ma = f"SMA{p['ma_period']} filter" if p.get("ma_period") else "no trend filter"
        offset = f"{p.get('entry_points', 0)} point offset" if p.get("entry_points") else "at the bar's extreme"
        return (f"trendline break, {p.get('structure_depth')} bars / {p.get('spacing')} spacing, {ma}, "
                f"{offset}, stop {p.get('sl_atr_mult')} ATR, target "
                f"{p.get('tp_atr_mult_ratio')}R")
    else:
        entry = f"{family} with {sorted(p)}"
    stop = f"swing({x['swing_lookback']}) - {x['sl_atr']} ATR" if x["stop"] == "swing" else f"{x['sl_atr']} ATR"
    target = f"{x['rr']}R target" if x["rr"] else "no fixed target"
    trail = f", trail {x['trail_atr']} ATR" if x["trail_atr"] else ""
    return f"{p.get('side', 'both')} · {entry} | stop {stop}, {target}{trail}, max {x['max_bars']} bars"


# --------------------------------------------------------------------------- search
def _valid_spec(spec: dict) -> bool:
    p = spec["params"]
    if spec["family"] == "ema_pullback" and p["ema_fast"] >= p["ema_slow"]:
        return False
    if spec["family"] == "ema_cross" and p["fast"] >= p["slow"]:
        return False
    return True


def _register_extra_builders() -> None:
    """Import the modules that own the newer families, so their order builders exist.

    Imported lazily and tolerantly: if either module is unavailable the lab still runs on the five
    original families rather than failing to start, which matters because it runs hourly.
    """
    for module in ("candle_pattern_lab", "aurum_flow_lab"):
        if module in _EXTRA_LOADED:
            continue
        try:
            __import__(f"{__package__}.{module}")
            _EXTRA_LOADED.add(module)
        except Exception:
            continue


_EXTRA_LOADED: set = set()


def random_spec(rng: random.Random, families: Optional[list] = None) -> dict:
    _register_extra_builders()
    while True:
        family = rng.choice(sorted(families or FAMILIES))
        spec = {"family": family, "params": {k: rng.choice(v) for k, v in FAMILIES[family].items()},
                "exits": {k: rng.choice(v) for k, v in EXIT_GRID.items()}}
        if _valid_spec(spec):
            return spec


def mutate_spec(spec: dict, rng: random.Random) -> dict:
    for _ in range(50):
        child = copy.deepcopy({k: spec[k] for k in ("family", "params", "exits")})
        group = rng.choice(["params", "exits"])
        grid = FAMILIES[child["family"]] if group == "params" else EXIT_GRID
        key = rng.choice(sorted(k for k, v in grid.items() if len(v) > 1))
        child[group][key] = rng.choice([v for v in grid[key] if v != child[group].get(key)])
        if _valid_spec(child):
            return child
    return random_spec(rng)


def load_registry(path: Optional[Path] = None) -> dict:
    path = Path(path or REGISTRY_PATH)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"version": 1, "markets": {}, "candidates": {}}


def save_registry(registry: dict, path: Optional[Path] = None) -> None:
    path = Path(path or REGISTRY_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(registry, default=str), encoding="utf-8")
    os.replace(tmp, path)


def read_status(path: Optional[Path] = None) -> dict:
    path = Path(path or STATUS_PATH)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"state": "never_run"}


def write_status(status: dict, path: Optional[Path] = None) -> None:
    path = Path(path or STATUS_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(status, indent=1, default=str), encoding="utf-8")
    os.replace(tmp, path)


def _run_is_active(status: dict) -> bool:
    if status.get("state") != "running" or not status.get("heartbeat"):
        return False
    age = (pd.Timestamp.now(tz="UTC") - pd.Timestamp(status["heartbeat"], tz="UTC")).total_seconds()
    return age < HEARTBEAT_STALE_SECONDS


def _default_loader(symbol: str, timeframe: str) -> pd.DataFrame:
    from .mtf_data import load_bars

    return load_bars(symbol, timeframe, source="app")


def _store(registry: dict, market: Market, spec: dict, tag: Optional[str] = None, count_trial: bool = True) -> dict:
    meta = registry["markets"][market.key]
    record = evaluate_candidate(market, spec, with_holdout=tag is not None)
    if count_trial:
        meta["candidates_tried"] = int(meta.get("candidates_tried") or 0) + 1
        family_counts = meta.setdefault("families", {}).setdefault(spec["family"], {"evaluated": 0, "validated": 0})
        family_counts["evaluated"] += 1
        family_counts["validated"] += int(bool(record.get("validated")))
        if record["validation_sr"] is not None and (record["validation"]["trades"] or 0) >= GATES["validation_min_trades"]:
            meta["validation_srs"] = ((meta.get("validation_srs") or [])[-(MAX_SRS_KEPT - 1):]
                                      + [[record["validation_sr"], int(record["validation"]["trades"] or 0)]])
    if tag:
        record["tag"] = tag
        record["name"] = spec.get("name")
    if record.get("holdout"):
        trials = 1 if tag else int(meta.get("candidates_tried") or 1)
        record["holdout_verdict"] = holdout_verdict(record, trials, _variance(meta.get("validation_srs") or []))
        if not tag:
            record["status"] = "holdout_passed" if record["holdout_verdict"]["passed"] else "holdout_failed"
    if not tag:
        meta.setdefault("seen", []).append(record["id"])
    if tag or record.get("validated"):
        registry["candidates"][f"{market.key}|{record['id']}"] = record
    else:
        recent = meta.setdefault("recent_rejected", [])
        recent.append({key: record[key] for key in ("id", "family", "description", "score", "gate_reasons", "evaluated_at")}
                      | {split: {k: record[split].get(k) for k in ("trades", "profit_factor", "total_return_pct")}
                         for split in ("search", "validation")})
        del recent[:-RECENT_REJECTED_KEPT]
    return record


def _top_validated_specs(registry: dict, market_key: str, limit: int = 20) -> list[dict]:
    """Parents for mutation: the strategy book's approved and watchlist strategies first (the search keeps learning
    around what survived the holdout and the evidence checks), then the best-scoring validated candidates."""
    try:
        from .strategy_book import book_specs
        parents = book_specs(market_key)
    except Exception:  # the book is optional; a missing or unreadable file must not stop the search
        parents = []
    rows = [rec for rec in registry["candidates"].values() if rec["market"] == market_key and rec.get("validated") and not rec.get("tag")]
    rows.sort(key=lambda rec: rec.get("score") or -999, reverse=True)
    known = {spec_id(spec) for spec in parents}
    parents += [rec["spec"] for rec in rows if spec_id(rec["spec"]) not in known][:max(0, limit - len(parents))]
    return parents[:max(limit, len(known))]


def run_search(markets=DEFAULT_MARKETS, minutes: float = 45.0, max_candidates: Optional[int] = None,
               seed: Optional[int] = None, loader: Optional[Callable] = None, registry_path: Optional[Path] = None,
               status_path: Optional[Path] = None, now=None, families: Optional[list] = None) -> dict:
    started = time.monotonic()
    status = read_status(status_path)
    if _run_is_active(status):
        return {"skipped": True, "reason": f"another Strategy Lab run is active (heartbeat {status.get('heartbeat')})"}
    rng = random.Random(seed if seed is not None else time.time_ns())
    registry = load_registry(registry_path)
    loader = loader or _default_loader
    contexts, problems = {}, {}
    for key in markets:
        symbol, timeframe = key.split(":")
        try:
            known = (registry["markets"].get(f"{symbol.upper()}:{timeframe}") or {}).get("boundaries")
            bars = loader(symbol, timeframe)
            if bars is None or bars.empty:
                raise ValueError("no bars returned")
            market = Market(symbol, timeframe, bars, boundaries=known, now=now)
            contexts[market.key] = market
            meta = registry["markets"].setdefault(market.key, {"candidates_tried": 0, "validation_srs": []})
            meta.update(market.info)
        except Exception as exc:
            problems[key] = str(exc)
    run = {"state": "running", "pid": os.getpid(), "started_at": _iso(pd.Timestamp.now(tz="UTC")),
           "heartbeat": _iso(pd.Timestamp.now(tz="UTC")), "markets": sorted(contexts), "problems": problems,
           "evaluated_this_run": 0, "budget_minutes": minutes}
    write_status(run, status_path)
    evaluated = 0
    try:
        for market in contexts.values():  # the expert as coded, re-scored every run as the holdout grows
            if market.symbol == "XAUUSD":
                _store(registry, market, SWING_TREND_PULLBACK_SPEC, tag="ea_baseline", count_trial=False)
        keys = sorted(contexts)
        tops = {key: _top_validated_specs(registry, key) for key in keys}
        seen_ids = {}
        for key in keys:
            meta = registry["markets"][key]
            known = set(meta.get("seen") or [])
            known.update(rec["id"] for rec in registry["candidates"].values() if rec["market"] == key and not rec.get("tag"))
            meta["seen"] = sorted(known)
            seen_ids[key] = known
        attempts, index = 0, 0
        limit_attempts = (max_candidates or 100_000) * 20 + 1000
        while keys and attempts < limit_attempts:
            if max_candidates is not None and evaluated >= max_candidates:
                break
            if max_candidates is None and time.monotonic() - started > minutes * 60:
                break
            attempts += 1
            market = contexts[keys[index % len(keys)]]
            index += 1
            parents = tops[market.key]
            if families:
                parents = [parent for parent in parents if parent["family"] in families]
            spec = mutate_spec(rng.choice(parents), rng) if parents and rng.random() < 0.5 else random_spec(rng, families)
            candidate_id = spec_id(spec)
            if candidate_id in seen_ids[market.key]:
                continue
            _store(registry, market, spec)
            seen_ids[market.key].add(candidate_id)
            evaluated += 1
            if evaluated % 25 == 0:
                tops = {key: _top_validated_specs(registry, key) for key in keys}
                run.update({"heartbeat": _iso(pd.Timestamp.now(tz="UTC")), "evaluated_this_run": evaluated})
                write_status(run, status_path)
                save_registry(registry, registry_path)
    finally:
        save_registry(registry, registry_path)
        summary = {"state": "idle", "last_started_at": run["started_at"], "last_finished_at": _iso(pd.Timestamp.now(tz="UTC")),
                   "evaluated_last_run": evaluated, "markets": sorted(contexts), "problems": problems,
                   "duration_minutes": round((time.monotonic() - started) / 60, 1)}
        write_status(summary, status_path)
    return summary


# --------------------------------------------------------------------------- reporting
def leaderboard(registry: dict, market: Optional[str] = None, limit: int = 50) -> list[dict]:
    """Validated candidates ranked by search+validation score; holdout verdicts use today's trial counts."""
    rows = []
    for record in registry["candidates"].values():
        if record.get("tag") or not record.get("validated") or (market and record["market"] != market):
            continue
        rows.append(_public_record(registry, record))
    # Strategies that passed the final holdout first, then by search+validation score.
    rows.sort(key=lambda rec: (bool((rec.get("holdout_verdict") or {}).get("passed")), rec.get("score") or -999), reverse=True)
    return rows[:limit]


_VARIANCE_CACHE: dict = {}


def _market_variance(meta: dict) -> float:
    """_variance of a market's stored validation Sharpes, cached until that list changes (summaries call this per record)."""
    srs = meta.get("validation_srs") or []
    key = (id(srs), len(srs), tuple(srs[-1]) if srs and isinstance(srs[-1], list) else (srs[-1] if srs else None))
    if key not in _VARIANCE_CACHE:
        if len(_VARIANCE_CACHE) > 64:
            _VARIANCE_CACHE.clear()
        _VARIANCE_CACHE[key] = _variance(srs)
    return _VARIANCE_CACHE[key]


def _public_record(registry: dict, record: dict) -> dict:
    meta = registry["markets"].get(record["market"]) or {}
    out = {k: v for k, v in record.items() if k != "holdout_returns"}
    if record.get("holdout"):
        trials = 1 if record.get("tag") else int(meta.get("candidates_tried") or 1)
        out["holdout_verdict"] = holdout_verdict(record, trials, _market_variance(meta))
    return out


def deflation_toll(registry: dict) -> dict:
    """What the deflated-Sharpe bar actually rejected, and how good those rejects looked.

    "0 passed the holdout" reads as a broken lab unless you can see the step before it. Thousands of candidates clear
    every money test on unseen data - profit factor, trade count, drawdown, positive return - and are then refused
    because, once the number of candidates tried on that market is counted, their result is what chance produces
    anyway. This reports that step so the bar can be judged on its own evidence.
    """
    survivors, best = 0, None
    for record in (registry.get("candidates") or {}).values():
        if record.get("tag") or not record.get("validated"):
            continue
        verdict = record.get("holdout_verdict") or {}
        checks = verdict.get("checks") or {}
        if not checks or verdict.get("passed"):
            continue
        money = [k for k in ("profit_factor", "trades", "max_drawdown", "positive_return")]
        if not all(checks.get(k) for k in money) or checks.get("deflated_sharpe"):
            continue
        survivors += 1
        holdout = record.get("holdout") or {}
        if best is None or (holdout.get("sharpe") or -9) > (best["sharpe"] or -9):
            best = {"market": record.get("market"), "family": record.get("family"),
                    "trades": holdout.get("trades"), "profit_factor": holdout.get("profit_factor"),
                    "total_return_pct": holdout.get("total_return_pct"),
                    "max_drawdown_pct": holdout.get("max_drawdown_pct"), "sharpe": holdout.get("sharpe"),
                    "deflated_sharpe": verdict.get("deflated_sharpe"), "n_trials": verdict.get("n_trials"),
                    "years": holdout.get("years")}
    return {"rejected_only_by_deflation": survivors, "best_rejected": best,
            "bar": HOLDOUT_CRITERIA.get("min_deflated_sharpe"),
            "note": ("These cleared every money test on bars the search never saw, and were still refused: counting "
                     "how many candidates were tried on that market, results this good turn up by chance."
                     if survivors else "No candidate has reached the deflated-Sharpe step yet.")}


def lab_summary(registry_path: Optional[Path] = None, status_path: Optional[Path] = None, limit: int = 50) -> dict:
    registry = load_registry(registry_path)
    counts: dict[str, dict] = {}
    for record in registry["candidates"].values():
        if record.get("tag"):
            continue
        bucket = counts.setdefault(record["market"], {"validated": 0, "holdout_passed": 0})
        bucket["validated"] += int(bool(record.get("validated")))
        passed = (_public_record(registry, record).get("holdout_verdict") or {}).get("passed")
        bucket["holdout_passed"] += int(bool(passed))
    families: dict[str, dict] = {}
    for meta in registry["markets"].values():
        for family, family_counts in (meta.get("families") or {}).items():
            fam = families.setdefault(family, {"evaluated": 0, "validated": 0})
            fam["evaluated"] += int(family_counts.get("evaluated") or 0)
            fam["validated"] += int(family_counts.get("validated") or 0)
    hidden = ("validation_srs", "seen", "recent_rejected", "families")
    markets = {key: {k: v for k, v in meta.items() if k not in hidden}
               | {"counts": {"validated": 0, "holdout_passed": 0, **counts.get(key, {}),
                             "evaluated": int(meta.get("candidates_tried") or 0)}}
               for key, meta in registry["markets"].items()}
    recent_rejected = {key: list(reversed((meta.get("recent_rejected") or [])[-30:]))
                       for key, meta in registry["markets"].items()}
    return {
        "status": read_status(status_path),
        "markets": markets,
        "families": families,
        "baselines": [_public_record(registry, rec) for rec in registry["candidates"].values() if rec.get("tag")],
        "leaderboard": leaderboard(registry, limit=limit),
        "recent_rejected": recent_rejected,
        "rules": {"gates": GATES, "holdout": HOLDOUT_CRITERIA, "search_fraction": SEARCH_FRACTION,
                  "validation_fraction": VALIDATION_FRACTION},
        "deflation": deflation_toll(registry),
    }


def main(argv: Optional[list] = None) -> None:
    parser = argparse.ArgumentParser(description="Strategy Lab: search rule strategies honestly (research only).")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="search for a time budget")
    run.add_argument("--minutes", type=float, default=45.0)
    run.add_argument("--markets", nargs="+", default=list(DEFAULT_MARKETS))
    run.add_argument("--max-candidates", type=int, default=None)
    run.add_argument("--families", nargs="+", choices=sorted(FAMILIES), default=None,
                     help="only search these rule families (e.g. ema_pullback for the SwingTrendPullback expert)")
    ea = sub.add_parser("ea", help="evaluate the SwingTrendPullback expert as coded")
    ea.add_argument("--markets", nargs="+", default=["XAUUSD:4h", "XAUUSD:1h"])
    ea.add_argument("--spec", choices=sorted(EA_SPECS), default="as_coded",
                    help="as_coded = original inputs, tradingview = the inputs on the live H4 chart")
    ea.add_argument("--no-swap", action="store_true", help="spread costs only (the earlier model), for comparison")
    ea.add_argument("--swap-mode", choices=SWAP_MODES, default="percent",
                    help="percent = today's share of price per night (default), price = fixed amount like the MT5 tester")
    sub.add_parser("status", help="print the lab summary")
    args = parser.parse_args(argv)

    if args.command == "run":
        print(json.dumps(run_search(args.markets, minutes=args.minutes, max_candidates=args.max_candidates,
                                    families=args.families), indent=1))
    elif args.command == "ea":
        registry = load_registry()
        for key in args.markets:
            symbol, timeframe = key.split(":")
            market = Market(symbol, timeframe, _default_loader(symbol, timeframe),
                            boundaries=(registry["markets"].get(key.upper()) or {}).get("boundaries"), swap=not args.no_swap,
                            swap_mode=args.swap_mode)
            spec = EA_SPECS[args.spec]
            record = evaluate_candidate(market, spec, with_holdout=True)
            record["holdout_verdict"] = holdout_verdict(record, 1, 0.01)
            print(f"\n=== {spec['name']} on {market.key} ({market.info['data_start']} to {market.info['data_end']}),"
                  f" costs: {market.info['cost_model']}")
            for split in ("search", "validation", "holdout"):
                print(f"{split:10s}", json.dumps(record[split]))
            print("gates:", record["gate_reasons"] or "passed", "| years profitable:", record["year_share"], "of", record["years_counted"])
            print("holdout verdict:", json.dumps(record["holdout_verdict"]))
    else:
        summary = lab_summary(limit=10)
        print(json.dumps({"status": summary["status"], "markets": summary["markets"], "families": summary["families"],
                          "top": [(r["market"], r["description"], r["score"], (r.get("holdout_verdict") or {}).get("passed"))
                                  for r in summary["leaderboard"]]}, indent=1, default=str))


if __name__ == "__main__":
    main()
