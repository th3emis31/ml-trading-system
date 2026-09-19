"""Multi-timeframe history for research (15m / 1h / 4h / 1d).

* ``load_bars`` prefers broker candles **fetched through the running app**
  (``/api/data/bars``, which reads them over the app's own MT5 connection): the
  prices the account actually trades, and far deeper 15m history than Yahoo's
  ~60 days. It falls back to Yahoo (4h is resampled from 1h) and caches per day.
* ``fetch_mt5_bars`` opens MetaTrader5 directly. ``auto`` never uses it; it runs only
  when ``source="mt5"`` is requested explicitly. Use it only while app.py is not
  running: a second MT5 connection from another process while the app was
  connected restarted the app once. Never call it inside the app.
* ``add_htf_features`` joins higher-timeframe context to each lower-timeframe
  bar using only higher-timeframe bars that had **closed** by the time the lower
  bar closed - the moment a decision is made - so there is no look-ahead.

MT5 bar times are the broker's server clock; hour-of-day features on MT5 data
therefore follow server time rather than UTC. Every frame records its source in
``attrs["source"]``.
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from .data import fetch_yahoo_history

TIMEFRAMES = {
    "1m": {"minutes": 1, "mt5": "TIMEFRAME_M1", "yahoo": ("7d", "1m"), "resample": None},
    "5m": {"minutes": 5, "mt5": "TIMEFRAME_M5", "yahoo": ("60d", "5m"), "resample": None},
    "15m": {"minutes": 15, "mt5": "TIMEFRAME_M15", "yahoo": ("60d", "15m"), "resample": None},
    "1h": {"minutes": 60, "mt5": "TIMEFRAME_H1", "yahoo": ("729d", "1h"), "resample": None},
    "4h": {"minutes": 240, "mt5": "TIMEFRAME_H4", "yahoo": ("729d", "1h"), "resample": "4h"},
    "1d": {"minutes": 1440, "mt5": "TIMEFRAME_D1", "yahoo": ("max", "1d"), "resample": None},
}
MT5_SYMBOLS = {"XAUUSD": ["XAUUSD", "XAUUSD.crp"], "BTCUSD": ["BTCUSD"]}
MT5_MAX_BARS = 99_000  # the terminal's Max bars setting is 100,000; larger requests fail with "Invalid params"
APP_MAX_BARS = 50_000  # MT5Service.MAX_RATES in the app
APP_BARS_URL = os.getenv("TRADING_APP_URL", "http://127.0.0.1:5000").rstrip("/") + "/api/data/bars"
CACHE_DIR = Path("data") / "research" / "cache"
OHLCV = ["datetime", "open", "high", "low", "close", "volume"]


def resample_bars(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Aggregate OHLCV bars into ``rule`` bars labelled by their open time."""
    if df is None or df.empty:
        return pd.DataFrame(columns=OHLCV)
    frame = df.copy()
    frame["datetime"] = pd.to_datetime(frame["datetime"], utc=True)
    agg = (frame.set_index("datetime")
           .resample(rule, label="left", closed="left")
           .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
           .dropna(subset=["open", "close"])
           .reset_index())
    agg.attrs["source"] = f"{df.attrs.get('source', 'unknown')}+resample:{rule}"
    return agg


def mt5_server_to_utc(seconds) -> pd.Series:
    """MT5 rate times are the broker's server clock, not UTC. Vantage and IC Markets run New York time + 7 h (UTC+3 in
    summer, UTC+2 in winter); verified 15 Sep 2026 against the app's /api/data/bars (exactly 3 h apart on 40 bars).
    Times that do not exist or repeat around the New York clock change become NaT."""
    server = pd.to_datetime(pd.Series(seconds), unit="s")
    new_york = (server - pd.Timedelta(hours=7)).dt.tz_localize("America/New_York", ambiguous="NaT", nonexistent="NaT")
    return new_york.dt.tz_convert("UTC")


def fetch_mt5_bars(symbol: str, timeframe: str, count: int = MT5_MAX_BARS) -> pd.DataFrame:
    """Broker candles from the running MT5 terminal (empty frame when unavailable)."""
    spec = TIMEFRAMES[timeframe]
    try:
        import MetaTrader5 as mt5  # type: ignore
    except Exception:
        return pd.DataFrame(columns=OHLCV)
    if not mt5.initialize():
        return pd.DataFrame(columns=OHLCV)
    try:
        for name in MT5_SYMBOLS.get(symbol.upper(), [symbol.upper()]):
            if not mt5.symbol_select(name, True):
                continue
            rates = mt5.copy_rates_from_pos(name, getattr(mt5, spec["mt5"]), 0, min(int(count), MT5_MAX_BARS))
            if rates is None or len(rates) == 0:
                continue
            frame = pd.DataFrame(rates)
            frame["datetime"] = mt5_server_to_utc(frame["time"])
            frame = frame.dropna(subset=["datetime"]).drop_duplicates(subset=["datetime"])
            frame = frame.rename(columns={"tick_volume": "volume"})[OHLCV]
            frame["symbol"] = symbol.upper()
            frame.attrs["source"] = f"mt5:{name}"
            return frame
    finally:
        mt5.shutdown()
    return pd.DataFrame(columns=OHLCV)


def fetch_app_bars(symbol: str, timeframe: str, count: int = APP_MAX_BARS) -> pd.DataFrame:
    """Broker candles through the running app's MT5 connection (empty frame when unavailable)."""
    query = urllib.parse.urlencode({"symbol": symbol.upper(), "timeframe": timeframe, "count": int(count)})
    try:
        with urllib.request.urlopen(f"{APP_BARS_URL}?{query}", timeout=300) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return pd.DataFrame(columns=OHLCV)
    # Only broker bars count for this provider; the app's own Yahoo fallback is fetched directly instead.
    if not payload.get("available") or not str(payload.get("source", "")).startswith("mt5"):
        return pd.DataFrame(columns=OHLCV)
    frame = pd.DataFrame(payload["bars"])
    frame["datetime"] = pd.to_datetime(frame["datetime"], utc=True)
    frame["symbol"] = symbol.upper()
    frame.attrs["source"] = f"app:{payload['source']}"
    return frame


def fetch_app_history(symbol: str, timeframe: str, start, end=None, chunk_days: int = 15,
                      use_cache: bool = True) -> pd.DataFrame:
    """Every broker bar between ``start`` and ``end``, paged through the app in date windows.

    ``fetch_app_bars`` can only walk back from the newest bar and stops at ``APP_MAX_BARS``, which
    on a one-minute chart is about 35 trading days. This asks ``/api/data/bars`` for one window at a
    time instead, so deep minute history arrives in pieces small enough not to strain the running
    app. Windows with no bars (weekends, or before the broker's history begins) are simply empty.

    The result is cached per symbol/timeframe/range under ``data/research/cache``, because paging
    a few months of minute bars takes a while and research re-runs it often.
    """
    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC") if end is not None else pd.Timestamp.now(tz="UTC")
    cache = CACHE_DIR / f"{symbol.lower()}_{timeframe}_app_{start_ts:%Y%m%d}_{end_ts:%Y%m%d}.csv"
    if use_cache and cache.exists():
        frame = pd.read_csv(cache, parse_dates=["datetime"])
        frame["datetime"] = pd.to_datetime(frame["datetime"], utc=True)
        frame.attrs["source"] = f"app:mt5:{symbol.upper()}:paged"
        return frame

    pieces = []
    window_start = start_ts
    while window_start < end_ts:
        window_end = min(window_start + pd.Timedelta(days=chunk_days), end_ts)
        query = urllib.parse.urlencode({"symbol": symbol.upper(), "timeframe": timeframe,
                                        "start": window_start.isoformat(), "end": window_end.isoformat()})
        try:
            with urllib.request.urlopen(f"{APP_BARS_URL}?{query}", timeout=600) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception:
            payload = {}
        if payload.get("available") and payload.get("bars"):
            pieces.append(pd.DataFrame(payload["bars"]))
        window_start = window_end

    if not pieces:
        return pd.DataFrame(columns=OHLCV)
    frame = pd.concat(pieces, ignore_index=True)
    frame["datetime"] = pd.to_datetime(frame["datetime"], utc=True)
    frame = (frame.drop_duplicates(subset=["datetime"])     # chunk edges overlap by a bar
             .sort_values("datetime").reset_index(drop=True))
    frame["symbol"] = symbol.upper()
    frame.attrs["source"] = f"app:mt5:{symbol.upper()}:paged"
    if use_cache:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        frame.to_csv(cache, index=False)
    return frame


def fetch_yahoo_bars(symbol: str, timeframe: str) -> pd.DataFrame:
    spec = TIMEFRAMES[timeframe]
    period, interval = spec["yahoo"]
    frame = fetch_yahoo_history(symbol, period=period, interval=interval)
    if frame is None or frame.empty:
        return pd.DataFrame(columns=OHLCV)
    frame.attrs["source"] = f"yahoo:{interval}"
    if spec["resample"]:
        frame = resample_bars(frame, spec["resample"])
    frame["symbol"] = symbol.upper()
    return frame


def load_bars(symbol: str, timeframe: str, source: str = "auto", use_cache: bool = True) -> pd.DataFrame:
    """Bars for one symbol/timeframe from ``source`` = ``app`` | ``yahoo`` | ``mt5`` | ``auto`` (app, then Yahoo)."""
    symbol = symbol.upper()
    order = {"auto": ["app", "yahoo"], "app": ["app"], "mt5": ["mt5"], "yahoo": ["yahoo"]}[source]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    for provider in order:
        cache = CACHE_DIR / f"{symbol.lower()}_{timeframe}_{provider}_{stamp}.csv"
        if use_cache and cache.exists():
            frame = pd.read_csv(cache)
            frame["datetime"] = pd.to_datetime(frame["datetime"], utc=True)
            frame.attrs["source"] = f"{provider}(cache {stamp})"
            return frame
        if provider == "app":
            frame = fetch_app_bars(symbol, timeframe)
        elif provider == "mt5":
            frame = fetch_mt5_bars(symbol, timeframe)
        else:
            frame = fetch_yahoo_bars(symbol, timeframe)
        if frame is not None and not frame.empty:
            if use_cache:
                CACHE_DIR.mkdir(parents=True, exist_ok=True)
                frame.to_csv(cache, index=False)
            return frame
    empty = pd.DataFrame(columns=OHLCV)
    empty.attrs["source"] = "none"
    return empty


def htf_context(htf: pd.DataFrame, minutes: int, prefix: str) -> pd.DataFrame:
    """Context features for each higher-timeframe bar, stamped with the time the bar closed."""
    frame = htf.copy().sort_values("datetime").reset_index(drop=True)
    frame["datetime"] = pd.to_datetime(frame["datetime"], utc=True)
    close = frame["close"].astype(float)
    high = frame["high"].astype(float)
    low = frame["low"].astype(float)
    ema50 = close.ewm(span=50, adjust=False).mean()
    ema200 = close.ewm(span=200, adjust=False).mean()
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    prev_close = close.shift(1)
    true_range = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    highest = high.rolling(20).max()
    lowest = low.rolling(20).min()
    out = pd.DataFrame({
        "available_at": frame["datetime"] + pd.Timedelta(minutes=minutes),
        f"{prefix}_bar_time": frame["datetime"],
        f"{prefix}_trend_up": (ema50 > ema200).astype(float),
        f"{prefix}_dist_ema_50": close / ema50 - 1,
        f"{prefix}_dist_ema_200": close / ema200 - 1,
        f"{prefix}_rsi_14": 100 - 100 / (1 + gain / loss.replace(0, np.nan)),
        f"{prefix}_atr_pct": true_range.rolling(14).mean() / close,
        f"{prefix}_range_pos_20": (close - lowest) / (highest - lowest).replace(0, np.nan),
        f"{prefix}_ret_1": close.pct_change(),
        f"{prefix}_ret_5": close.pct_change(5),
    })
    return out.replace([np.inf, -np.inf], np.nan)


def add_htf_features(base: pd.DataFrame, base_minutes: int, htf_frames: dict) -> tuple[pd.DataFrame, list[str]]:
    """Join higher-timeframe context to ``base`` without look-ahead.

    ``htf_frames`` maps a prefix (e.g. ``"h4"``) to ``(bars, minutes)``. A base bar is
    decided at its close, so it may only see higher-timeframe bars whose own close
    (open time + duration) is at or before that moment.
    """
    frame = base.copy()
    frame["datetime"] = pd.to_datetime(frame["datetime"], utc=True)
    frame = frame.sort_values("datetime").reset_index(drop=True)
    frame["_decision_time"] = frame["datetime"] + pd.Timedelta(minutes=base_minutes)
    feature_columns: list[str] = []
    for prefix, (htf, minutes) in htf_frames.items():
        context = htf_context(htf, minutes, prefix).sort_values("available_at")
        frame = pd.merge_asof(frame, context, left_on="_decision_time", right_on="available_at", direction="backward")
        frame = frame.drop(columns=["available_at"])
        feature_columns += [c for c in context.columns if c not in ("available_at", f"{prefix}_bar_time")]
    frame = frame.drop(columns=["_decision_time"])
    return frame, feature_columns
