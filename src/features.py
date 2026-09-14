from __future__ import annotations

import numpy as np
import pandas as pd

PIP_PROFILE = {
    "XAUUSD": {"pip_size": 0.1, "min_pips": 30, "max_pips": 2000},
    "BTCUSD": {"pip_size": 1.0, "min_pips": 50, "max_pips": 3000},
}


def get_pip_profile(symbol: str | None):
    if not symbol:
        return {"pip_size": 0.1, "min_pips": 30, "max_pips": 2000}
    return PIP_PROFILE.get(str(symbol).upper(), {"pip_size": 0.1, "min_pips": 30, "max_pips": 2000})


def get_adaptive_pip_profile(symbol: str | None, volatility_pct: float = 0.0, hour_utc: int | None = None):
    profile = get_pip_profile(symbol)
    min_pips = float(profile["min_pips"])
    max_pips = float(profile["max_pips"])
    volatility_pct = float(volatility_pct or 0.0)

    # Expand pip expectations in high-volatility regimes and tighten them in calm regimes.
    if volatility_pct >= 0.02:
        min_pips *= 1.35
        max_pips *= 1.35
    elif volatility_pct <= 0.006:
        min_pips *= 0.85
        max_pips *= 0.85

    if hour_utc is not None:
        # Session-aware adjustment: London/NY windows allow larger targets.
        if 6 <= hour_utc <= 16:
            min_pips *= 1.1
            max_pips *= 1.12
        else:
            min_pips *= 0.95

    return {
        "pip_size": float(profile["pip_size"]),
        "min_pips": round(max(5.0, min_pips), 1),
        "max_pips": round(max(min_pips + 10.0, max_pips), 1),
    }


def add_quality_targets(df: pd.DataFrame, horizon: int = 3) -> pd.DataFrame:
    out = df.copy()
    symbol = None
    if "symbol" in out.columns and not out["symbol"].empty:
        symbol = str(out["symbol"].iloc[0])
    profile = get_pip_profile(symbol)
    pip_size = float(profile["pip_size"])
    min_pips = float(profile["min_pips"])
    max_pips = float(profile["max_pips"])

    out["future_close"] = out["close"].shift(-horizon)
    out["future_move_pips"] = (out["future_close"] - out["close"]) / max(1e-9, pip_size)
    out["future_abs_pips"] = out["future_move_pips"].abs()
    out["quality_move"] = ((out["future_abs_pips"] >= min_pips) & (out["future_abs_pips"] <= max_pips)).astype(int)
    out["target"] = (out["future_move_pips"] > 0).astype(int)
    out["target_pip_min"] = min_pips
    out["target_pip_max"] = max_pips
    return out


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """Create useful features for machine learning classification."""
    out = df.copy()
    out = out.sort_values("datetime").reset_index(drop=True)
    dt = pd.to_datetime(out.get("datetime", out.index), utc=True, errors="coerce")
    out["hour_utc"] = dt.dt.hour.astype(float)
    out["weekday"] = dt.dt.weekday.astype(float)
    out["month"] = dt.dt.month.astype(float)
    out["quarter"] = dt.dt.quarter.astype(float)

    # Cyclical encodings help models learn seasonal and intraday recurrence.
    out["hour_sin"] = np.sin(2 * np.pi * out["hour_utc"] / 24.0)
    out["hour_cos"] = np.cos(2 * np.pi * out["hour_utc"] / 24.0)
    out["weekday_sin"] = np.sin(2 * np.pi * out["weekday"] / 7.0)
    out["weekday_cos"] = np.cos(2 * np.pi * out["weekday"] / 7.0)
    out["month_sin"] = np.sin(2 * np.pi * out["month"] / 12.0)
    out["month_cos"] = np.cos(2 * np.pi * out["month"] / 12.0)

    out["session_london"] = ((out["hour_utc"] >= 7) & (out["hour_utc"] <= 16)).astype(float)
    out["session_newyork"] = ((out["hour_utc"] >= 12) & (out["hour_utc"] <= 21)).astype(float)
    out["session_asia"] = ((out["hour_utc"] >= 0) & (out["hour_utc"] <= 8)).astype(float)
    out["session_overlap"] = ((out["hour_utc"] >= 12) & (out["hour_utc"] <= 16)).astype(float)
    out["return_1d"] = out["close"].pct_change()
    out["return_3d"] = out["close"].pct_change(3)
    out["return_5d"] = out["close"].pct_change(5)
    out["volatility_3d"] = out["return_1d"].rolling(3).std()
    out["volatility_5d"] = out["return_1d"].rolling(5).std()
    out["sma_5"] = out["close"].rolling(5).mean()
    out["sma_20"] = out["close"].rolling(20).mean()
    out["ema_9"] = compute_ema(out["close"], 9)
    out["ema_21"] = compute_ema(out["close"], 21)
    out["ema_spread"] = (out["ema_9"] - out["ema_21"]) / out["close"].replace(0, np.nan)
    out["rsi_14"] = compute_rsi(out["close"], 14)
    out["atr_14"] = compute_atr(out, 14)
    out["atr_pct"] = out["atr_14"] / out["close"].replace(0, np.nan)
    out["fvg_up"] = detect_fvg(out, direction="up")
    out["fvg_down"] = detect_fvg(out, direction="down")
    out["pullback"] = detect_pullback(out)
    out["reversal"] = detect_reversal(out)
    out["trend_5"] = np.sign(out["close"] - out["sma_5"])
    out["trend_20"] = np.sign(out["close"] - out["sma_20"])
    out = add_quality_targets(out, horizon=3)
    out = out.dropna().reset_index(drop=True)
    return out


def detect_fvg(df: pd.DataFrame, direction: str = "up") -> pd.Series:
    values = [0.0] * len(df)
    high = df["high"].tolist()
    low = df["low"].tolist()
    close = df["close"].tolist()
    for i in range(2, len(df)):
        if direction == "up" and close[i - 2] < low[i - 1] and close[i - 1] < low[i]:
            values[i] = 1.0
        elif direction == "down" and close[i - 2] > high[i - 1] and close[i - 1] > high[i]:
            values[i] = 1.0
    return pd.Series(values, index=df.index)


def detect_pullback(df: pd.DataFrame) -> pd.Series:
    values = [0.0] * len(df)
    close = df["close"].tolist()
    for i in range(3, len(df)):
        if close[i] < close[i - 1] < close[i - 2] and close[i - 1] > close[i - 3]:
            values[i] = 1.0
    return pd.Series(values, index=df.index)


def detect_reversal(df: pd.DataFrame) -> pd.Series:
    values = [0.0] * len(df)
    high = df["high"].tolist()
    low = df["low"].tolist()
    close = df["close"].tolist()
    for i in range(3, len(df)):
        bullish = low[i] < low[i - 1] < low[i - 2] and close[i] > close[i - 1]
        bearish = high[i] > high[i - 1] > high[i - 2] and close[i] < close[i - 1]
        if bullish or bearish:
            values[i] = 1.0
    return pd.Series(values, index=df.index)


def compute_rsi(series: pd.Series, window: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(window=window, min_periods=window).mean()
    avg_loss = loss.rolling(window=window, min_periods=window).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)


def compute_ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def compute_atr(df: pd.DataFrame, window: int = 14) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            (df["high"] - df["low"]).abs(),
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.rolling(window=window, min_periods=window).mean()
