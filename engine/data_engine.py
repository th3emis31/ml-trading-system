"""01. Data ingestion, and the provenance gate the whole engine rests on.

BTCUSD / XAUUSD, 1m -> 5m -> 15m -> 1H -> 4H, OHLCV + spread + volume.

Delegates to `src.mtf_data.load_bars(source="app")`, which reaches the broker through the running app and
**refuses anything that is not MT5** - `fetch_app_bars` returns an empty frame unless the payload's source
starts with `mt5`. This module makes that refusal loud instead of silent: `load` returns the source string
it actually received, and `require_mt5` raises rather than hand back bars of unknown origin.

That gate is here because of a real failure: Yahoo proxies XAUUSD with the GC=F future at roughly 1.4 %
basis against broker spot, so gaps, sweeps, levels and stop distances measured on it belong to a different
instrument from the one the account trades. The owner's own `demo_sweep_trader` already halts rather than
act on non-MT5 bars; this engine holds the same line.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from src.mtf_data import load_bars

TIMEFRAMES = ("1m", "5m", "15m", "1h", "4h")
OHLCV = ("datetime", "open", "high", "low", "close", "volume")


@dataclass
class BarSet:
    """One symbol/timeframe of bars, carrying WHERE they came from rather than an assumption about it."""

    symbol: str
    timeframe: str
    frame: pd.DataFrame
    source: str

    @property
    def is_broker(self) -> bool:
        """True only for MT5 bars. A cache entry counts: it is only written after an mt5-only fetch."""
        s = str(self.source or "")
        return s.startswith("mt5") or s.startswith("app:mt5") or s.startswith("app(cache")

    @property
    def bars(self) -> int:
        return 0 if self.frame is None else len(self.frame)

    def describe(self) -> str:
        if not self.bars:
            return f"{self.symbol}:{self.timeframe} NO BARS (source {self.source!r})"
        start, end = self.frame["datetime"].iloc[0], self.frame["datetime"].iloc[-1]
        return (f"{self.symbol}:{self.timeframe} {self.bars} bars {start} -> {end} "
                f"source={self.source!r} {'BROKER' if self.is_broker else 'NOT BROKER'}")


def load(symbol: str, timeframe: str, use_cache: bool = True) -> BarSet:
    """Bars plus the source string actually returned. Never substitutes a different feed."""
    frame = load_bars(symbol.upper(), timeframe, source="app", use_cache=use_cache)
    source = "" if frame is None else str(frame.attrs.get("source") or "")
    return BarSet(symbol.upper(), timeframe, frame, source)


def require_mt5(symbol: str, timeframe: str, use_cache: bool = True) -> BarSet:
    """Same as `load`, but refuses to continue on anything that is not broker data.

    Raising here is deliberate. A backtest that silently ran on the wrong instrument is worse than one that
    did not run: it produces a number that looks like every other number.
    """
    bars = load(symbol, timeframe, use_cache=use_cache)
    if not bars.bars:
        raise RuntimeError(f"no bars for {symbol}:{timeframe} (source {bars.source!r}) - is the app running?")
    if not bars.is_broker:
        raise RuntimeError(f"{symbol}:{timeframe} came from {bars.source!r}, not MT5; refusing to backtest on it")
    return bars


def load_stack(symbol: str, timeframes=TIMEFRAMES, use_cache: bool = True) -> dict:
    """Every timeframe the cascade needs, each checked for provenance independently."""
    return {tf: require_mt5(symbol, tf, use_cache=use_cache) for tf in timeframes}


def spread_points(bars: BarSet) -> Optional[float]:
    """Median per-bar spread as the broker reported it, in points. None when the column is absent."""
    if not bars.bars or "spread" not in bars.frame.columns:
        return None
    return float(bars.frame["spread"].median())
