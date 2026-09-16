"""One signal engine for the live dashboard and for evaluation (backtests, research, parity checks).

Before this module the RF+LSTM signal was computed in two places with different rules:
  * live  app.py _build_signal_payload_uncached: RF and LSTM averaged 50/50, BUY if p >= 0.55, SELL if p <= 0.45
  * train src/train.py ensemble_predict:        LSTM 60 / RF 40, BUY if p > 0.55, SELL if p < 0.45 (strict)
Both now read their weights and thresholds from SIGNAL_CONFIGS below and go through the same functions, so a
backtest and the live dashboard give the same signal for the same closed bars and models.

Nothing here places, modifies or closes orders. HOLD is a real state and carries no trade levels.
"""
from __future__ import annotations

import copy
from typing import Any, Callable, Optional

import numpy as np
import pandas as pd

BUY, SELL, HOLD = "BUY", "SELL", "HOLD"

# The single source of the ensemble weights and decision thresholds.
#   live            what the dashboard, signal history and execution gates use (app.py)
#   legacy_ensemble src.train.ensemble_predict defaults (DailyLearner); strict bounds kept so its tests hold
SIGNAL_CONFIGS: dict[str, dict[str, Any]] = {
    "live": {
        "weights": {"rf": 0.5, "lstm": 0.5},
        "buy_threshold": 0.55,
        "sell_threshold": 0.45,
        "inclusive": True,          # p == 0.55 is BUY, p == 0.45 is SELL
        "rf_fallback": 0.5,         # RF probability when the model cannot predict
        "classify_decimals": 4,     # the live payload classified the 4-decimal ensemble value
    },
    "legacy_ensemble": {
        "weights": {"rf": 0.4, "lstm": 0.6},
        "buy_threshold": 0.55,
        "sell_threshold": 0.45,
        "inclusive": False,         # p must be strictly beyond the threshold
        "rf_fallback": 0.5,
        "classify_decimals": None,  # ensemble_predict compared the unrounded value
    },
}
DEFAULT_CONFIG = "live"


def get_config(config: Optional[Any] = None) -> dict[str, Any]:
    """A copy of a named config, or of a dict that overrides the live config key by key."""
    if config is None or isinstance(config, str):
        name = config or DEFAULT_CONFIG
        if name not in SIGNAL_CONFIGS:
            raise KeyError(f"unknown signal config {name!r}; known: {sorted(SIGNAL_CONFIGS)}")
        return copy.deepcopy(SIGNAL_CONFIGS[name])
    merged = copy.deepcopy(SIGNAL_CONFIGS[DEFAULT_CONFIG])
    for key, value in dict(config).items():
        merged[key] = copy.deepcopy(value)
    return merged


def blend_probabilities(rf_prob: Optional[float], lstm_prob: Optional[float], config: Optional[Any] = None) -> float:
    """Weighted average of the available model probabilities (a missing model is left out, not guessed)."""
    cfg = get_config(config)
    weights = cfg["weights"]
    parts = [(float(p), float(weights.get(name, 0.0))) for name, p in (("rf", rf_prob), ("lstm", lstm_prob))
             if p is not None and np.isfinite(float(p))]
    total = sum(w for _, w in parts)
    if not parts or total <= 0:
        return float(cfg.get("rf_fallback", 0.5))
    return float(sum(p * w for p, w in parts) / total)


def classify_probability(probability: Any, config: Optional[Any] = None) -> str:
    """BUY / SELL / HOLD from a probability; anything unreadable is HOLD."""
    cfg = get_config(config)
    try:
        value = float(probability)
    except (TypeError, ValueError):
        return HOLD
    if not np.isfinite(value):
        return HOLD
    if cfg.get("classify_decimals") is not None:
        value = round(value, int(cfg["classify_decimals"]))
    buy, sell = float(cfg["buy_threshold"]), float(cfg["sell_threshold"])
    if cfg.get("inclusive", True):
        if value >= buy:
            return BUY
        if value <= sell:
            return SELL
    else:
        if value > buy:
            return BUY
        if value < sell:
            return SELL
    return HOLD


def _rf_probability(model: Any, features_row: pd.DataFrame, feature_columns, fallback: float) -> float:
    if model is None:
        return fallback
    try:
        x = features_row[list(feature_columns)]
        if hasattr(model, "predict_proba"):
            return float(model.predict_proba(x)[0, 1])
        return float(model.predict(x)[0])
    except Exception:
        return fallback


def _lstm_probability(model: Any, bars: pd.DataFrame) -> Optional[float]:
    if model is None:
        return None
    try:
        value = model.predict(bars)
        return None if value is None else float(value)
    except Exception:
        return None


def predict_signal(bars: pd.DataFrame, models: Optional[dict] = None, config: Optional[Any] = None,
                   features: Optional[pd.DataFrame] = None) -> dict[str, Any]:
    """Signal for the LAST row of ``bars`` (pass closed bars only).

    ``models``: {"rf": estimator with predict_proba/predict (or None), "lstm": object with predict(bars) -> probability
    (or None), "feature_columns": optional list, default src.train.FEATURE_COLUMNS}.
    ``features``: an already built inference feature frame for ``bars`` (build_inference_features; passing it only saves time).
    Returns {"signal", "probability", "rf_probability", "lstm_probability", "config", "signal_time"}; signal_time is the
    open time of the bar the prediction was made on (the last row of ``bars``, not 3 bars earlier).
    """
    from .features import build_inference_features
    from .train import FEATURE_COLUMNS

    cfg = get_config(config)
    models = models or {}
    frame = features if features is not None else build_inference_features(bars)
    feature_columns = models.get("feature_columns") or FEATURE_COLUMNS
    fallback = float(cfg.get("rf_fallback", 0.5))
    rf_prob = _rf_probability(models.get("rf"), frame.iloc[-1:], feature_columns, fallback) if len(frame) else fallback
    lstm_prob = _lstm_probability(models.get("lstm"), bars)
    probability = blend_probabilities(rf_prob, lstm_prob, cfg)
    return {
        "signal": classify_probability(probability, cfg),
        "probability": round(probability, 4),
        "rf_probability": rf_prob,
        "lstm_probability": lstm_prob,
        "config": cfg,
        "signal_time": frame["datetime"].iloc[-1] if len(frame) and "datetime" in frame.columns else None,
    }


def live_signal(bars: pd.DataFrame, models: Optional[dict] = None, features: Optional[pd.DataFrame] = None) -> dict[str, Any]:
    """The live dashboard's entry (app.py _build_signal_payload_uncached): predict_signal with the live config."""
    return predict_signal(bars, models, "live", features=features)


def predict_signal_series(bars: pd.DataFrame, models: Optional[dict] = None, config: Optional[Any] = None,
                          start: int = 0, step: int = 1,
                          on_bar: Optional[Callable[[int, dict], None]] = None) -> pd.DataFrame:
    """predict_signal on every prefix bars[:i+1] for i >= start (walk-forward: bar i only sees bars 0..i).

    This is the evaluation-path entry: each row equals what predict_signal returns live on the same closed bars.
    """
    rows = []
    for i in range(max(0, int(start)), len(bars), max(1, int(step))):
        window = bars.iloc[: i + 1]
        result = predict_signal(window, models, config)
        record = {"index": i, "signal": result["signal"], "probability": result["probability"],
                  "rf_probability": result["rf_probability"], "lstm_probability": result["lstm_probability"]}
        if "datetime" in bars.columns:
            record["datetime"] = bars["datetime"].iloc[i]
        rows.append(record)
        if on_bar is not None:
            on_bar(i, result)
    return pd.DataFrame(rows)
