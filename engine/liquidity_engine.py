"""Liquidity: where stops sit, when they get taken, and whether price reclaimed afterwards.

A "sweep" is the event the owner's own strategies are built on: price trades THROUGH a prior extreme —
taking the orders resting beyond it — and then either continues (a real break) or comes back inside (a
failed break, which is the tradable one). Telling those two apart is the whole job, and it is why a sweep
is not simply "a new high".

Delegates the detection to the existing tested code rather than writing a third version of it:

* `src.sweep_reversal` — the owner's sweep/reclaim rule, already a registered builder
* `src.poi_liquidity` — points of interest and the liquidity pools around them

Everything here is CAUSAL by construction: a prior extreme is computed with `shift=1`, so the bar being
judged is never part of the range it is being compared against. That single shift is the difference between
a sweep detector and a lookahead.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd


def prior_extremes(frame: pd.DataFrame, lookback: int = 20) -> tuple:
    """Highest high and lowest low of the PREVIOUS `lookback` bars, excluding the current one.

    The exclusion is the point. Comparing a bar against a window that contains it guarantees the bar can
    never exceed it, so every sweep would vanish; including the current bar in the window is the mirror
    mistake and invents sweeps that were only visible afterwards.
    """
    high = pd.Series(frame["high"]).rolling(lookback).max().shift(1).to_numpy(dtype=float)
    low = pd.Series(frame["low"]).rolling(lookback).min().shift(1).to_numpy(dtype=float)
    return high, low


def sweeps(frame: pd.DataFrame, lookback: int = 20) -> dict:
    """Bars that took out a prior extreme, split into continuation and reclaim.

    * `swept_high`  — the bar's HIGH exceeded the prior high (buy-side liquidity taken)
    * `reclaim_down`— it swept the high and CLOSED back below it: the failed break, the tradable one
    * `swept_low` / `reclaim_up` — the mirror

    Returned as boolean arrays aligned to the frame, so any bar can be asked what happened AT it.
    """
    prior_high, prior_low = prior_extremes(frame, lookback)
    high = frame["high"].to_numpy(dtype=float)
    low = frame["low"].to_numpy(dtype=float)
    close = frame["close"].to_numpy(dtype=float)

    with np.errstate(invalid="ignore"):
        swept_high = (high > prior_high) & np.isfinite(prior_high)
        swept_low = (low < prior_low) & np.isfinite(prior_low)
        reclaim_down = swept_high & (close < prior_high)
        reclaim_up = swept_low & (close > prior_low)

    return {
        "prior_high": prior_high, "prior_low": prior_low,
        "swept_high": swept_high, "swept_low": swept_low,
        "reclaim_down": reclaim_down, "reclaim_up": reclaim_up,
    }


def equal_levels(frame: pd.DataFrame, lookback: int = 20, tolerance_pct: float = 0.0005) -> dict:
    """Clusters of near-equal highs or lows — where stops pile up, so where liquidity is.

    `tolerance_pct` is a FRACTION of price (0.0005 = 0.05 %), not a percentage and not an absolute price.
    An absolute threshold is the error recorded in LESSONS.md: on gold spanning 2,000 to 4,400 a fixed
    dollar tolerance means something different at each end of the series.
    """
    high = pd.Series(frame["high"])
    low = pd.Series(frame["low"])
    roll_high = high.rolling(lookback).max()
    roll_low = low.rolling(lookback).min()
    with np.errstate(invalid="ignore", divide="ignore"):
        equal_high = ((roll_high - high).abs() / high <= tolerance_pct).to_numpy()
        equal_low = ((low - roll_low).abs() / low <= tolerance_pct).to_numpy()
    return {"equal_high": equal_high, "equal_low": equal_low}


def events_at(state: dict, index: int) -> dict:
    """Which liquidity events fired at one bar, as plain booleans for the ledger to record."""
    keys = ("swept_high", "swept_low", "reclaim_down", "reclaim_up")
    return {k: bool(state[k][index]) for k in keys if k in state and index < len(state[k])}


def counts(state: dict) -> dict:
    """How often each event occurred — the sanity check before believing any rule built on them.

    Named `counts`, not `summary`: `daily_agent.summary` already exists and means something else.
    """
    return {k: int(np.count_nonzero(v)) for k, v in state.items()
            if isinstance(v, np.ndarray) and v.dtype == bool}
