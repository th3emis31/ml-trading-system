"""03. The 4H -> 1H -> 15M -> 5M/1M cascade, aligned so no lower bar ever sees a higher bar early.

Delegates to `src.mtf_data.htf_context` / `add_htf_features`, which already carry the alignment rule this
engine exists to protect: a 15-minute bar at 10:05 may only use the 4-hour candle that CLOSED at 08:00,
never the one forming around it. Getting that wrong is the classic multi-timeframe lookahead - the higher
frame appears to predict the lower one because it contains its future.

`align_index` is the primitive: for each row of the base frame it returns the index of the most recent
HIGHER-timeframe bar that had already closed. Everything else in the cascade is built on it.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.mtf_data import add_htf_features, htf_context, resample_bars

# Minutes per timeframe, so "which higher bar had closed" is arithmetic rather than a lookup table.
MINUTES = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440}
CASCADE = ("4h", "1h", "15m")          # macro -> context -> setup
EXECUTION = ("5m", "1m")               # where the fill is simulated


def minutes_of(timeframe: str) -> int:
    tf = str(timeframe).lower()
    if tf not in MINUTES:
        raise KeyError(f"unknown timeframe {timeframe!r}; known: {', '.join(MINUTES)}")
    return MINUTES[tf]


def align_index(base_times: pd.Series, higher_times: pd.Series, higher_minutes: int) -> np.ndarray:
    """For each base bar, the index of the last HIGHER bar that had already CLOSED. -1 before any exist.

    A higher bar stamped 08:00 on a 4h frame closes at 12:00, so a base bar at 10:05 must still be using
    the 04:00 candle. searchsorted on the close time is what enforces that; using the open time is the bug
    this function exists to prevent.
    """
    base = pd.to_datetime(pd.Series(base_times).values, utc=True)
    high = pd.to_datetime(pd.Series(higher_times).values, utc=True)
    closes = high + pd.to_timedelta(higher_minutes, unit="m")
    return np.searchsorted(closes.values, base.values, side="right") - 1


def cascade(stack: dict, base: str = "15m") -> pd.DataFrame:
    """The base frame with every higher timeframe's CLOSED context attached, and nothing forming.

    `stack` is {timeframe: BarSet} from data_engine.load_stack.
    """
    base_set = stack[base]
    frame = base_set.frame.copy()
    higher = {tf: stack[tf].frame for tf in CASCADE if tf in stack and minutes_of(tf) > minutes_of(base)}
    if not higher:
        return frame
    enriched, _added = add_htf_features(frame, minutes_of(base), higher)
    return enriched


def context_frame(higher_frame: pd.DataFrame, timeframe: str, prefix: str) -> pd.DataFrame:
    """One higher timeframe's context columns, shifted so only closed bars are readable."""
    return htf_context(higher_frame, minutes_of(timeframe), prefix)


def downsample(frame: pd.DataFrame, to_timeframe: str) -> pd.DataFrame:
    """Build a higher timeframe from a lower one when the broker does not serve it directly."""
    rule = {"5m": "5min", "15m": "15min", "30m": "30min", "1h": "1h", "4h": "4h", "1d": "1D"}[to_timeframe]
    return resample_bars(frame, rule)
