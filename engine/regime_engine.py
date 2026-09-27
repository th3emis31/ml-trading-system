"""07. Regime: trending, ranging, volatile, quiet, expansion, compression.

A regime label is only useful if it is computed from bars that had already closed, so every measure here is
backward-looking by construction. The classification is deliberately plain — ATR against its own history for
volatility, directional travel against total travel for trend — because an elaborate regime model is one
more thing to overfit, and the owner's standing rule is that a filter earns its place with evidence rather
than being assumed into the stack.

Regime is recorded per bar so the analytics engine can answer "how did this strategy do in each regime"
without re-deriving it, which is exactly what the owner's diagram asks of it.
"""
from __future__ import annotations

from collections import Counter

import numpy as np
import pandas as pd

TRENDING, RANGING = "trending", "ranging"
VOLATILE, QUIET = "volatile", "quiet"
EXPANSION, COMPRESSION = "expansion", "compression"


def average_true_range(frame: pd.DataFrame, length: int = 14) -> np.ndarray:
    """ATR over a frame, reusing the project's existing true-range rather than writing a second one.

    `src.volatility_trend_breakout.true_range` already computes exactly this over a Candle sequence, and
    the duplicate guard was right to flag a private copy. Named in full because
    `src.strategy_lab.Indicators.atr` already owns the short name and means the Wilder version.
    """
    from src.volatility_trend_breakout import true_range as candle_true_range
    from engine.structure_engine import to_candles
    tr = np.asarray(candle_true_range(to_candles(frame)), dtype=float)
    return pd.Series(tr).rolling(length).mean().to_numpy(dtype=float)


def efficiency(frame: pd.DataFrame, lookback: int = 20) -> np.ndarray:
    """Directional travel over total travel across the last `lookback` bars, in [0, 1].

    Near 1 the market went somewhere in a straight line; near 0 it went nowhere by a long route. It is the
    cleanest trending-versus-ranging measure that needs no threshold fitted to one particular market.
    """
    close = pd.Series(frame["close"].to_numpy(dtype=float))
    net = (close - close.shift(lookback)).abs()
    path = close.diff().abs().rolling(lookback).sum()
    with np.errstate(invalid="ignore", divide="ignore"):
        return (net / path).to_numpy(dtype=float)


def classify_regime(frame: pd.DataFrame, lookback: int = 20, atr_length: int = 14,
                    trend_threshold: float = 0.35) -> dict:
    """Per-bar regime labels. Every input is rolling or shifted, so no bar reads its own future.

    Named `classify_regime`, not `classify`: `strategy_book.classify` already exists and decides what
    status a candidate earns, which is a different question entirely.

    `trend_threshold` is declared here rather than tuned per run: a threshold chosen after seeing results
    is a fitted parameter wearing the name of a constant.
    """
    eff = efficiency(frame, lookback)
    atr = average_true_range(frame, atr_length)
    atr_median = pd.Series(atr).rolling(lookback * 5).median().to_numpy(dtype=float)
    atr_prev = pd.Series(atr).shift(lookback).to_numpy(dtype=float)

    with np.errstate(invalid="ignore"):
        trend = np.where(np.isfinite(eff), np.where(eff >= trend_threshold, TRENDING, RANGING), None)
        volatility = np.where(np.isfinite(atr) & np.isfinite(atr_median),
                              np.where(atr >= atr_median, VOLATILE, QUIET), None)
        breathing = np.where(np.isfinite(atr) & np.isfinite(atr_prev),
                             np.where(atr >= atr_prev, EXPANSION, COMPRESSION), None)
    return {"efficiency": eff, "atr": atr, "trend": trend,
            "volatility": volatility, "breathing": breathing}


def label_at(state: dict, index: int) -> dict:
    """The three labels at one bar, as plain strings for the ledger."""
    return {k: (state[k][index] if index < len(state[k]) else None)
            for k in ("trend", "volatility", "breathing")}


def distribution(state: dict) -> dict:
    """How the run divided between regimes — read before trusting any per-regime performance split."""
    out = {}
    for key in ("trend", "volatility", "breathing"):
        out[key] = dict(Counter(v for v in state[key] if v is not None))
    return out
