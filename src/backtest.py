from __future__ import annotations

import pandas as pd

from .data import generate_synthetic_data
from .features import build_features
from .train import FEATURE_COLUMNS, train_model


def run_backtest(symbol: str):
    df = generate_synthetic_data(symbol, start_date="2024-01-01", end_date="2024-12-31", n=500)
    features = build_features(df)
    model, metrics = train_model(features, symbol)

    signal = model.predict(features[FEATURE_COLUMNS])
    features = features.copy()
    features["signal"] = signal
    features["strategy_return"] = features["signal"].shift(1) * features["return_1d"]
    cumulative = (1 + features["strategy_return"].fillna(0)).cumprod()
    return metrics, cumulative.iloc[-1] - 1


if __name__ == "__main__":
    for symbol in ["XAUUSD", "BTCUSD"]:
        metrics, equity = run_backtest(symbol)
        print(symbol, metrics["accuracy"], round(equity, 4))
