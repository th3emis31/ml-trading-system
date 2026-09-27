"""06. Volume and auction: POC, value area, VWAP, relative volume.

Delegates to `src.volume_profile`, which already builds the profile CAUSALLY — a rolling window that ends
at the bar being judged, never a profile of the whole series projected backwards. A value area computed
over all history and then read at an early bar is one of the easiest lookaheads to write by accident and
one of the hardest to see afterwards.

One honest limit, carried over from `smart_entry_arch` and worth repeating wherever gold volume is used:
MetaTrader gives TICK volume for gold — the number of price changes, not contracts traded. Every gold
figure here rests on that proxy, which is why gold and bitcoin results are reported separately rather than
pooled.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.volume_profile import DEFAULT_BINS, DEFAULT_LOOKBACK, profile_window, rolling_levels


def levels(frame: pd.DataFrame, lookback: int = DEFAULT_LOOKBACK, bins: int = DEFAULT_BINS) -> dict:
    """Rolling POC / VAH / VAL aligned to the frame, each from bars up to and including its own."""
    poc, vah, val = rolling_levels(frame["high"].to_numpy(dtype=float),
                                   frame["low"].to_numpy(dtype=float),
                                   frame["volume"].to_numpy(dtype=float),
                                   lookback=lookback, bins=bins)
    return {"poc": poc, "vah": vah, "val": val}


def window_profile(frame: pd.DataFrame, start: int, end: int, bins: int = DEFAULT_BINS):
    """The auction profile for one explicit slice — for when a session or range needs its own profile."""
    sl = slice(start, end + 1)
    return profile_window(frame["high"].to_numpy(dtype=float)[sl],
                          frame["low"].to_numpy(dtype=float)[sl],
                          frame["volume"].to_numpy(dtype=float)[sl], bins=bins)


def relative_volume(frame: pd.DataFrame, lookback: int = 20) -> np.ndarray:
    """This bar's volume against the mean of the PREVIOUS `lookback` bars.

    The shift matters: including the current bar in its own average dilutes exactly the spike being
    measured, so a genuinely heavy bar reads as ordinary.
    """
    vol = pd.Series(frame["volume"].to_numpy(dtype=float))
    baseline = vol.rolling(lookback).mean().shift(1)
    with np.errstate(invalid="ignore", divide="ignore"):
        return (vol / baseline).to_numpy(dtype=float)


def vwap(frame: pd.DataFrame, reset_daily: bool = True) -> np.ndarray:
    """Volume-weighted average price, reset each UTC day by default so it means the session's VWAP."""
    typical = (frame["high"].to_numpy(dtype=float) + frame["low"].to_numpy(dtype=float)
               + frame["close"].to_numpy(dtype=float)) / 3.0
    vol = frame["volume"].to_numpy(dtype=float)
    times = pd.to_datetime(frame["datetime"], utc=True)
    group = times.dt.strftime("%Y-%m-%d").to_numpy() if reset_daily else np.array(["all"] * len(frame))
    table = pd.DataFrame({"g": group, "pv": typical * vol, "v": vol})
    cum_pv = table.groupby("g")["pv"].cumsum().to_numpy(dtype=float)
    cum_v = table.groupby("g")["v"].cumsum().to_numpy(dtype=float)
    out = np.full(len(frame), np.nan, dtype=float)
    with np.errstate(invalid="ignore", divide="ignore"):
        np.divide(cum_pv, cum_v, out=out, where=cum_v > 0)
    return out


def state_at(state: dict, index: int) -> dict:
    """POC / VAH / VAL at one bar, for the ledger to record alongside the decision."""
    return {k: (float(v[index]) if index < len(v) and np.isfinite(v[index]) else None)
            for k, v in state.items() if isinstance(v, np.ndarray)}
