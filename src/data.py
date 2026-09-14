from __future__ import annotations

import numpy as np
import pandas as pd
import yfinance as yf

YAHOO_TICKERS = {
    "XAUUSD": "GC=F",
    "BTCUSD": "BTC-USD",
}


def generate_synthetic_data(symbol: str, start_date: str = "2024-01-01", end_date: str = "2024-12-31", n: int = 500):
    """Generate synthetic OHLCV-style data for a symbol."""
    dates = pd.date_range(start_date, end_date, periods=n)
    rng = np.random.default_rng(42)

    base = 2000.0 if symbol.upper() == "XAUUSD" else 50000.0
    drift = rng.normal(0, 0.002, size=n)
    price = np.cumprod(1 + drift) * base
    price = np.maximum(price, 1e-6)

    df = pd.DataFrame(
        {
            "symbol": symbol.upper(),
            "datetime": dates,
            "open": price * (1 + rng.normal(0, 0.0008, size=n)),
            "high": price * (1 + rng.normal(0, 0.0012, size=n)),
            "low": price * (1 - rng.normal(0, 0.0012, size=n)),
            "close": price,
            "volume": rng.integers(1000, 5000, size=n),
        }
    )
    df = df.sort_values("datetime").reset_index(drop=True)
    df["ret"] = df["close"].pct_change()
    return df


def fetch_yahoo_history(symbol: str, period: str = "90d", interval: str = "1h") -> pd.DataFrame:
    ticker_key = YAHOO_TICKERS.get(symbol.upper(), symbol)
    try:
        ticker = yf.Ticker(ticker_key)
        history = ticker.history(period=period, interval=interval, auto_adjust=False, actions=False)
        if history is None or history.empty:
            # fallback to alternate resolutions if Yahoo returns no data for the requested interval
            fallback_options = [
                ("30d", "1h"),
                ("14d", "1h"),
                ("7d", "1h"),
                ("14d", "30m"),
                ("7d", "30m"),
            ]
            for fb_period, fb_interval in fallback_options:
                history = ticker.history(period=fb_period, interval=fb_interval, auto_adjust=False, actions=False)
                if history is not None and not history.empty:
                    break
        if history is None or history.empty:
            return pd.DataFrame()
        if "Adj Close" in history.columns:
            history = history.drop(columns=[col for col in history.columns if col.startswith("Adj")])
        history = history.reset_index()
        history = history.rename(columns={
            "Date": "datetime",
            "Datetime": "datetime",
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
        })
        if "datetime" not in history.columns:
            return pd.DataFrame()
        history = history[["datetime", "open", "high", "low", "close", "volume"]].copy()
        history["symbol"] = symbol.upper()
        history = history.sort_values("datetime").reset_index(drop=True)
        history["ret"] = history["close"].pct_change()
        return history
    except Exception:
        return pd.DataFrame()


def fetch_real_data(symbol: str, period: str = "90d", interval: str = "1h") -> pd.DataFrame:
    df = fetch_yahoo_history(symbol, period=period, interval=interval)
    if df.empty:
        # Never raises, but callers must be able to tell generated prices from real
        # ones: the frame is tagged so the pipeline page can flag a synthetic run.
        synthetic = generate_synthetic_data(symbol, n=500)
        synthetic.attrs["source"] = "synthetic"
        return synthetic
    df.attrs["source"] = "yahoo"
    return df
