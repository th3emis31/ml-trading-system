from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (
    accuracy_score, 
    classification_report, 
    precision_score,
    recall_score,
    f1_score,
    brier_score_loss,
    roc_auc_score,
    roc_curve,
    confusion_matrix
)
from sklearn.calibration import CalibratedClassifierCV
from sklearn.model_selection import TimeSeriesSplit

from .data import fetch_real_data
from .features import build_features
# LSTMTrader is imported inside train_lstm_model: importing it here loaded TensorFlow
# (~1 GB private memory) into every module that only needs FEATURE_COLUMNS or the RF
# helpers, including the research engine, on a machine with ~7 GB of RAM.

FEATURE_COLUMNS = [
    "hour_utc",
    "weekday",
    "month",
    "quarter",
    "hour_sin",
    "hour_cos",
    "weekday_sin",
    "weekday_cos",
    "month_sin",
    "month_cos",
    "session_london",
    "session_newyork",
    "session_asia",
    "session_overlap",
    "return_1d",
    "return_3d",
    "return_5d",
    "volatility_3d",
    "volatility_5d",
    "sma_5",
    "sma_20",
    "ema_9",
    "ema_21",
    "ema_spread",
    "rsi_14",
    "atr_14",
    "atr_pct",
    "fvg_up",
    "fvg_down",
    "pullback",
    "reversal",
    "trend_5",
    "trend_20",
]

def compute_advanced_metrics(y_true, y_pred, y_proba=None):
    """Compute comprehensive classification metrics for better trading signal evaluation."""
    metrics = {
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "precision": float(precision_score(y_true, y_pred, zero_division=0)),
        "recall": float(recall_score(y_true, y_pred, zero_division=0)),
        "f1": float(f1_score(y_true, y_pred, zero_division=0)),
        "f1_score": float(f1_score(y_true, y_pred, zero_division=0)),  # Keep for backward compat
    }
    
    # Add ROC-AUC only if probabilities available and data suitable for it
    if y_proba is not None:
        try:
            if len(np.unique(y_true)) > 1:
                metrics["roc_auc"] = float(roc_auc_score(y_true, y_proba))
            else:
                metrics["roc_auc"] = None
        except:
            metrics["roc_auc"] = None
    
    # Add confusion matrix
    cm = confusion_matrix(y_true, y_pred)
    metrics["confusion_matrix"] = cm.tolist()
    
    # Add sensitivity and specificity
    if cm.shape == (2, 2):
        tn, fp, fn, tp = cm.ravel()
        metrics["sensitivity"] = float(tp / (tp + fn)) if (tp + fn) > 0 else 0.0
        metrics["specificity"] = float(tn / (tn + fp)) if (tn + fp) > 0 else 0.0
    
    return metrics


def compute_expected_calibration_error(y_true, y_proba, bins: int = 10) -> float:
    """Estimate Expected Calibration Error (ECE) over equally-spaced probability bins."""
    y_true_arr = np.asarray(y_true).astype(float)
    y_proba_arr = np.asarray(y_proba).astype(float)
    if y_true_arr.size == 0 or y_proba_arr.size == 0:
        return 0.0

    edges = np.linspace(0.0, 1.0, int(max(2, bins)) + 1)
    ece = 0.0
    total = float(len(y_true_arr))
    for i in range(len(edges) - 1):
        low = edges[i]
        high = edges[i + 1]
        if i == len(edges) - 2:
            mask = (y_proba_arr >= low) & (y_proba_arr <= high)
        else:
            mask = (y_proba_arr >= low) & (y_proba_arr < high)
        if not np.any(mask):
            continue
        bucket_true = y_true_arr[mask]
        bucket_proba = y_proba_arr[mask]
        gap = abs(float(np.mean(bucket_true)) - float(np.mean(bucket_proba)))
        ece += gap * (float(len(bucket_true)) / total)
    return float(ece)


def _fit_random_forest_with_optional_calibration(
    X_train,
    y_train,
    *,
    calibrate: bool = True,
    calibration_method: str = "sigmoid",
):
    base_model = RandomForestClassifier(n_estimators=120, random_state=42, n_jobs=-1)
    base_model.fit(X_train, y_train)

    unique_classes = np.unique(np.asarray(y_train))
    if not calibrate or len(unique_classes) < 2:
        return base_model, {"enabled": False, "method": None, "reason": "insufficient_classes"}

    n_rows = int(len(X_train))
    # Require enough rows for stable calibration folds and preserve temporal order.
    max_splits = max(2, min(5, n_rows // 30))
    if max_splits < 2:
        return base_model, {"enabled": False, "method": None, "reason": "insufficient_rows"}

    calibration_cv = TimeSeriesSplit(n_splits=max_splits, gap=1)
    calibrated_model = CalibratedClassifierCV(
        estimator=RandomForestClassifier(n_estimators=120, random_state=42, n_jobs=-1),
        method=calibration_method,
        cv=calibration_cv,
    )
    try:
        calibrated_model.fit(X_train, y_train)
        return calibrated_model, {"enabled": True, "method": calibration_method, "cv_splits": max_splits, "gap": 1}
    except ValueError:
        return base_model, {"enabled": False, "method": None, "reason": "calibration_failed"}


def train_model_with_timeseries_cv(df: pd.DataFrame, symbol: str, n_splits: int = 5, gap: int = 1):
    """Train with time-series cross-validation for proper temporal data handling."""
    if df.empty:
        raise ValueError("No training data available")
    
    enriched = build_features(df)
    if enriched.empty:
        raise ValueError("No enriched data after feature build")
    
    quality_mask = enriched.get("quality_move", pd.Series([1] * len(enriched), index=enriched.index)).astype(bool)
    quality_rows = enriched[quality_mask]
    if len(quality_rows) < 80:
        quality_rows = enriched
    
    X = quality_rows[FEATURE_COLUMNS]
    y = quality_rows["target"]
    
    # Time-series cross-validation: walk-forward approach with gap to reduce leakage.
    safe_gap = max(0, int(gap))
    try:
        tscv = TimeSeriesSplit(n_splits=n_splits, gap=safe_gap)
        # Validate split configuration early.
        _ = next(tscv.split(X))
    except ValueError:
        safe_gap = 0
        tscv = TimeSeriesSplit(n_splits=n_splits, gap=safe_gap)
    cv_scores = []
    cv_metrics_list = []
    
    for fold, (train_idx, test_idx) in enumerate(tscv.split(X)):
        X_train_fold = X.iloc[train_idx]
        y_train_fold = y.iloc[train_idx]
        X_test_fold = X.iloc[test_idx]
        y_test_fold = y.iloc[test_idx]
        
        clf, _ = _fit_random_forest_with_optional_calibration(X_train_fold, y_train_fold, calibrate=True)
        
        preds = clf.predict(X_test_fold)
        fold_score = accuracy_score(y_test_fold, preds)
        cv_scores.append(fold_score)
        
        # Get probabilities for advanced metrics
        proba = clf.predict_proba(X_test_fold)[:, 1] if len(clf.classes_) > 1 else None
        fold_metrics = compute_advanced_metrics(y_test_fold, preds, proba)
        cv_metrics_list.append(fold_metrics)
    
    # Final training on all data for production model
    clf, calibration_meta = _fit_random_forest_with_optional_calibration(X, y, calibrate=True)
    
    # Prepare final metrics combining CV results
    metrics = {
        "symbol": symbol,
        "cv_folds": n_splits,
        "cv_gap": safe_gap,
        "cv_accuracy_mean": float(np.mean(cv_scores)),
        "cv_accuracy_std": float(np.std(cv_scores)),
        "cv_accuracies": [float(s) for s in cv_scores],
        "cv_metrics_per_fold": cv_metrics_list,
        "classification_report": classification_report(y, clf.predict(X), output_dict=True, zero_division=0),
        "quality_rows": int(len(quality_rows)),
        "total_rows": int(len(enriched)),
        "target_pip_min": float(quality_rows.get("target_pip_min", pd.Series([0])).iloc[0]) if not quality_rows.empty else None,
        "target_pip_max": float(quality_rows.get("target_pip_max", pd.Series([0])).iloc[0]) if not quality_rows.empty else None,
        "calibration_enabled": bool(calibration_meta.get("enabled")),
        "calibration_method": calibration_meta.get("method"),
        "calibration_cv_splits": calibration_meta.get("cv_splits"),
        "calibration_gap": calibration_meta.get("gap"),
    }
    
    # Add advanced metrics for final model
    final_proba = clf.predict_proba(X)[:, 1] if len(np.unique(y)) > 1 else None
    final_advanced = compute_advanced_metrics(y, clf.predict(X), final_proba)
    metrics.update(final_advanced)
    if final_proba is not None:
        metrics["brier_score"] = float(brier_score_loss(y, final_proba))
        metrics["ece_10"] = float(compute_expected_calibration_error(y, final_proba, bins=10))
    
    save_model(clf, symbol)
    save_metrics(metrics, symbol)
    return clf, metrics


def train_model(df: pd.DataFrame, symbol: str):
    """Original training function - kept for backward compatibility."""
    if df.empty:
        raise ValueError("No training data available")
    enriched = build_features(df)
    if enriched.empty:
        raise ValueError("No enriched data after feature build")

    quality_mask = enriched.get("quality_move", pd.Series([1] * len(enriched), index=enriched.index)).astype(bool)
    quality_rows = enriched[quality_mask]
    if len(quality_rows) < 80:
        quality_rows = enriched

    X = quality_rows[FEATURE_COLUMNS]
    y = quality_rows["target"]

    split_index = int(len(X) * 0.8)
    X_train, X_test = X.iloc[:split_index], X.iloc[split_index:]
    y_train, y_test = y.iloc[:split_index], y.iloc[split_index:]

    clf, calibration_meta = _fit_random_forest_with_optional_calibration(X_train, y_train, calibrate=True)

    preds = clf.predict(X_test)
    proba = clf.predict_proba(X_test)[:, 1] if len(np.unique(y_test)) > 1 else None
    accuracy = accuracy_score(y_test, preds)
    report = classification_report(y_test, preds, output_dict=True, zero_division=0)
    
    # Add advanced metrics from Phase 2
    advanced_metrics = compute_advanced_metrics(y_test, preds, proba)

    metrics = {
        "symbol": symbol,
        "accuracy": float(accuracy),
        "precision": advanced_metrics.get("precision"),
        "recall": advanced_metrics.get("recall"),
        "f1": advanced_metrics.get("f1"),
        "confusion_matrix": advanced_metrics.get("confusion_matrix"),
        "classification_report": report,
        "quality_rows": int(len(quality_rows)),
        "total_rows": int(len(enriched)),
        "target_pip_min": float(quality_rows.get("target_pip_min", pd.Series([0])).iloc[0]) if not quality_rows.empty else None,
        "target_pip_max": float(quality_rows.get("target_pip_max", pd.Series([0])).iloc[0]) if not quality_rows.empty else None,
        "calibration_enabled": bool(calibration_meta.get("enabled")),
        "calibration_method": calibration_meta.get("method"),
        "calibration_cv_splits": calibration_meta.get("cv_splits"),
        "calibration_gap": calibration_meta.get("gap"),
    }
    if proba is not None:
        metrics["brier_score"] = float(brier_score_loss(y_test, proba))
        metrics["ece_10"] = float(compute_expected_calibration_error(y_test, proba, bins=10))
    save_model(clf, symbol)
    save_metrics(metrics, symbol)
    return clf, metrics


def save_model(model, symbol: str):
    out_dir = Path("models")
    out_dir.mkdir(exist_ok=True)
    path = out_dir / f"{symbol.lower()}_model.joblib"
    import joblib

    joblib.dump(model, path)


def load_model(symbol: str):
    path = Path("models") / f"{symbol.lower()}_model.joblib"
    if not path.exists():
        return None
    import joblib

    return joblib.load(path)


def save_metrics(metrics: dict, symbol: str):
    out_dir = Path("models")
    out_dir.mkdir(exist_ok=True)
    path = out_dir / f"{symbol.lower()}_metrics.json"
    with path.open("w", encoding="utf-8") as fh:
        json.dump(metrics, fh, indent=2)


def load_metrics(symbol: str):
    path = Path("models") / f"{symbol.lower()}_metrics.json"
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def save_lstm_metrics(metrics: dict, symbol: str):
    out_dir = Path("models")
    out_dir.mkdir(exist_ok=True)
    path = out_dir / f"{symbol.lower()}_lstm_metrics.json"
    with path.open("w", encoding="utf-8") as fh:
        json.dump(metrics, fh, indent=2)


def load_lstm_metrics(symbol: str):
    path = Path("models") / f"{symbol.lower()}_lstm_metrics.json"
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def train_lstm_model(df: pd.DataFrame, symbol: str):
    from .lstm_model import LSTMTrader

    trader = LSTMTrader(symbol)
    model, metrics = trader.train(df)
    save_lstm_metrics(metrics, symbol)
    return model, metrics


# PHASE 3.2 IMPROVEMENT: Ensemble Voting
def ensemble_predict(lstm_prob: float, rf_prob: float, lstm_weight: float = 0.6, rf_weight: float = 0.4) -> dict:
    """
    Combine LSTM and RandomForest predictions using weighted voting.
    
    PHASE 3.2 IMPROVEMENT - Ensemble Voting for better trading signals
    
    Args:
        lstm_prob: LSTM model probability (0-1)
        rf_prob: RandomForest model probability (0-1)
        lstm_weight: Weight for LSTM (default 0.6 = 60%)
        rf_weight: Weight for RandomForest (default 0.4 = 40%)
    
    Returns:
        dict with keys:
            - probability: Ensemble probability (weighted average)
            - confidence: Confidence level (0-100%, how much models agree)
            - lstm_prob: Original LSTM probability
            - rf_prob: Original RandomForest probability
            - agreement: Agreement level (0-1)
            - signal: Trading signal (BUY/HOLD/SELL)
    
    Example:
        >>> result = ensemble_predict(lstm_prob=0.72, rf_prob=0.68)
        >>> print(f"Signal: {result['signal']}, Confidence: {result['confidence']:.1f}%")
        Signal: BUY, Confidence: 98.0%
    """
    # Validate inputs
    if not (0 <= lstm_prob <= 1) or not (0 <= rf_prob <= 1):
        raise ValueError("Probabilities must be between 0 and 1")
    
    if not (lstm_weight > 0) or not (rf_weight > 0):
        raise ValueError("Weights must be positive numbers")
    
    # Normalize weights
    total_weight = lstm_weight + rf_weight
    lstm_weight = lstm_weight / total_weight
    rf_weight = rf_weight / total_weight
    
    # Ensemble probability = weighted average
    ensemble_prob = (lstm_prob * lstm_weight) + (rf_prob * rf_weight)
    
    # Confidence = how much models agree (inverse of distance between them)
    disagreement = abs(lstm_prob - rf_prob)
    agreement = 1 - disagreement  # 0 = total disagreement, 1 = perfect agreement
    confidence = agreement * 100  # Convert to percentage (0-100%)
    
    # Generate signal based on probability threshold
    if ensemble_prob > 0.55:
        signal = "BUY"
    elif ensemble_prob < 0.45:
        signal = "SELL"
    else:
        signal = "HOLD"
    
    return {
        "probability": float(ensemble_prob),
        "confidence": float(confidence),
        "lstm_prob": float(lstm_prob),
        "rf_prob": float(rf_prob),
        "agreement": float(agreement),
        "signal": signal,
        "lstm_weight": float(lstm_weight),
        "rf_weight": float(rf_weight),
    }


if __name__ == "__main__":
    for symbol in ["XAUUSD", "BTCUSD"]:
        data = fetch_real_data(symbol, period="120d", interval="1h")
        if data.empty:
            print(f"Could not fetch live data for {symbol}, falling back to synthetic")
            from .data import generate_synthetic_data
            data = generate_synthetic_data(symbol, n=600)
        enriched = build_features(data)
        train_model(enriched, symbol)
        print(f"trained {symbol}")
