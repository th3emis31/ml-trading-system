from __future__ import annotations

import argparse

import pandas as pd

from .data import generate_synthetic_data
from .features import build_features
from .train import FEATURE_COLUMNS


def predict(symbol: str):
    df = generate_synthetic_data(symbol, start_date="2024-01-01", end_date="2024-12-31", n=500)
    features = build_features(df)
    import joblib

    model = joblib.load(f"models/{symbol.lower()}_model.joblib")
    X = features[FEATURE_COLUMNS]
    preds = model.predict(X)
    return pd.DataFrame({"datetime": features["datetime"], "prediction": preds})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", choices=["XAUUSD", "BTCUSD"], required=True)
    args = parser.parse_args()
    print(predict(args.symbol).tail())
