"""04. Market structure: swing highs and lows, BOS, CHOCH, trend, range.

Delegates entirely to `src.market_structure`, which already carries the rule that makes this safe: a pivot
at bar i is only CONFIRMED at bar i+prd, because it needs prd bars on each side. `structure_at` therefore
returns one row PER BAR — "what did structure say at this moment" — rather than a single summary computed
from the whole series, which would let every bar read its own future.

Nothing here recomputes pivots. `breakout_finder.detect_pivots` does that, and `market_structure` is
explicit that it was named around two existing functions that mean different things.
"""
from __future__ import annotations

from typing import List, Optional

import pandas as pd

from src.market_structure import (DEFAULT_PRD, Swing, StructureEvent,
                                  structure_at, structure_events, structure_summary, structure_swings)
from src.volatility_trend_breakout import Candle


def to_candles(frame: pd.DataFrame) -> List[Candle]:
    """The Candle sequence the structure code expects, built once per frame."""
    return [Candle(ts=str(t), open=float(o), high=float(h), low=float(l), close=float(c), volume=float(v))
            for t, o, h, l, c, v in zip(frame["datetime"], frame["open"], frame["high"],
                                        frame["low"], frame["close"], frame["volume"])]


def swings(frame: pd.DataFrame, prd: int = DEFAULT_PRD) -> List[Swing]:
    """Confirmed swings, labelled HH / HL / LH / LL. Each carries `confirmed_at`: use it, not `index`."""
    return structure_swings(to_candles(frame), prd)


def events(frame: pd.DataFrame, prd: int = DEFAULT_PRD, wick_break: bool = False) -> List[StructureEvent]:
    """BOS and CHOCH in time order, each with the trend before and after."""
    return structure_events(to_candles(frame), prd, wick_break)


def per_bar(frame: pd.DataFrame, prd: int = DEFAULT_PRD, wick_break: bool = False) -> List[dict]:
    """Structure state for EVERY bar, so a trade can be asked what structure said when it was entered."""
    return structure_at(to_candles(frame), prd, wick_break)


def overview(frame: pd.DataFrame, prd: int = DEFAULT_PRD, wick_break: bool = False) -> dict:
    """Named `overview`, not `summary`: `daily_agent.summary` already exists and means something else."""
    return structure_summary(to_candles(frame), prd, wick_break)


def state_at(rows: List[dict], index: int) -> Optional[dict]:
    """One bar's structure row, or None when the index is outside the series."""
    return rows[index] if 0 <= index < len(rows) else None
