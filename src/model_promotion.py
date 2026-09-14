"""Champion / challenger gate for self-learning.

Retraining used to overwrite the live model files unconditionally, whether or
not the new model was any better. This module lets learning keep running while
protecting the live model:

1. ``archive_champion`` copies the current (champion) model files to
   ``models/archive/<symbol>/<UTC stamp>/`` before a retrain. Archives are never
   deleted, so no model is ever lost.
2. After the retrain writes the challenger, ``evaluate_rf`` scores champion and
   challenger on the same bars - only bars that come after the challenger's own
   training rows, with a purge gap - so the challenger is never graded on data it
   learned from.
3. ``decide_rf`` / ``decide_lstm`` keep the challenger only when it is not worse;
   otherwise ``restore_files`` puts the champion back.
4. ``record_decision`` appends every outcome to ``data/learning_decisions.json``.

The champion may have been trained on part of the evaluation window (it was
trained earlier on overlapping history). That head start can only make
promotion harder, never easier, so the gate errs on the side of keeping a
known model. A champion older than ``STALE_CHAMPION_DAYS`` is allowed to be
replaced by a challenger within ``ACCURACY_TOLERANCE`` so the models keep up
with changing markets.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from .train import FEATURE_COLUMNS
from .walkforward_backtest import BACKTEST_COSTS, LABEL_HORIZON_BARS, _simulate_trades, summarize_trades

MODELS_DIR = Path("models")
ARCHIVE_DIR = MODELS_DIR / "archive"
DECISIONS_PATH = Path("data") / "learning_decisions.json"

RF_FILES = ("{s}_model.joblib", "{s}_metrics.json")
LSTM_FILES = ("{s}_lstm.keras", "{s}_lstm_best.keras", "{s}_lstm_meta.json", "{s}_lstm_metrics.json", "{s}_lstm_scaler.joblib")

ACCURACY_TOLERANCE = 0.01
STALE_CHAMPION_DAYS = 14
MIN_HOLDOUT_ROWS = 60
DRAWDOWN_TOLERANCE_PCT = 2.0
MIN_TRADES_FOR_DRAWDOWN_CHECK = 5
EVAL_HOLD_BARS = 24


def _names(symbol: str, patterns) -> list[str]:
    return [p.format(s=symbol.lower()) for p in patterns]


def archive_champion(symbol: str) -> dict:
    """Copy the current model files aside before they are overwritten."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    dest = ARCHIVE_DIR / symbol.lower() / stamp
    copied = []
    for name in _names(symbol, RF_FILES + LSTM_FILES):
        src = MODELS_DIR / name
        if src.exists():
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest / name)
            copied.append(name)
    rf_path = MODELS_DIR / RF_FILES[0].format(s=symbol.lower())
    saved_at = None
    if rf_path.exists():
        saved_at = datetime.fromtimestamp(rf_path.stat().st_mtime, tz=timezone.utc)
    return {
        "path": str(dest) if copied else None,
        "files": copied,
        "rf_saved_at": saved_at.strftime("%Y-%m-%d %H:%M:%S") if saved_at else None,
        "rf_age_days": round((datetime.now(timezone.utc) - saved_at).total_seconds() / 86400, 2) if saved_at else None,
    }


def restore_files(archive: dict, symbol: str, patterns) -> list[str]:
    """Put archived champion files back in place of the challenger."""
    restored = []
    if not archive or not archive.get("path"):
        return restored
    folder = Path(archive["path"])
    for name in _names(symbol, patterns):
        src = folder / name
        if src.exists():
            shutil.copy2(src, MODELS_DIR / name)
            restored.append(name)
    return restored


def load_archived_json(archive: dict, name: str) -> dict:
    if not archive or not archive.get("path"):
        return {}
    path = Path(archive["path"]) / name
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {}


def load_archived_rf(archive: dict, symbol: str):
    if not archive or not archive.get("path"):
        return None
    path = Path(archive["path"]) / RF_FILES[0].format(s=symbol.lower())
    if not path.exists():
        return None
    import joblib

    return joblib.load(path)


def challenger_holdout(features: pd.DataFrame, split_fraction: float = 0.8) -> pd.DataFrame:
    """Bars the challenger never trained on.

    Mirrors ``train_model``: it trains on the first 80% of the quality rows
    (or of all rows when fewer than 80 are quality rows). Everything after that
    cut, plus the label-horizon purge, is unseen by the challenger.
    """
    if features is None or features.empty:
        return pd.DataFrame()
    mask = features["quality_move"].astype(bool) if "quality_move" in features.columns else pd.Series(True, index=features.index)
    rows = features[mask] if int(mask.sum()) >= 80 else features
    split = int(len(rows) * split_fraction)
    if split <= 0 or split >= len(rows):
        return pd.DataFrame()
    last_train_pos = features.index.get_loc(rows.index[split - 1])
    start = last_train_pos + 1 + LABEL_HORIZON_BARS
    return features.iloc[start:].copy()


def _positive_proba(model, X: pd.DataFrame) -> np.ndarray:
    classes = list(getattr(model, "classes_", []))
    if hasattr(model, "predict_proba") and 1 in classes and len(classes) >= 2:
        return model.predict_proba(X)[:, classes.index(1)]
    return np.asarray(model.predict(X), dtype=float)


def evaluate_rf(model, holdout: pd.DataFrame, symbol: str, *, buy_threshold: float = 0.55,
                sell_threshold: float = 0.45) -> Optional[dict]:
    """Direction accuracy, Brier score and a band-trading simulation with costs."""
    if model is None or holdout is None or len(holdout) < MIN_HOLDOUT_ROWS:
        return None
    X = holdout[FEATURE_COLUMNS]
    y = holdout["target"].to_numpy()
    proba = _positive_proba(model, X)
    frame = holdout.reset_index(drop=True)
    cost = BACKTEST_COSTS.get(symbol.upper(), BACKTEST_COSTS["default"])["round_trip_pct"]
    trades = _simulate_trades(frame, np.asarray(proba, dtype=float), np.ones(len(frame), dtype=int),
                              buy_threshold=buy_threshold, sell_threshold=sell_threshold,
                              hold_bars=EVAL_HOLD_BARS, cost_pct=cost)
    metrics = summarize_trades(trades, test_start=frame["datetime"].iloc[0], test_end=frame["datetime"].iloc[-1],
                               test_bars=len(frame), bars_in_market=int(sum(t["bars_held"] for t in trades)))
    return {
        "rows": int(len(holdout)),
        "accuracy": round(float(np.mean((proba >= 0.5).astype(int) == y)), 4),
        "brier": round(float(np.mean((proba - y) ** 2)), 4),
        "trades": metrics["trades"],
        "expectancy_pct": metrics["expectancy_pct"],
        "max_drawdown_pct": metrics["max_drawdown_pct"],
        "total_return_pct": metrics["total_return_pct"],
        "window": f"{str(frame['datetime'].iloc[0])[:16]} to {str(frame['datetime'].iloc[-1])[:16]}",
    }


def decide_rf(champion: Optional[dict], challenger: Optional[dict], champion_age_days: Optional[float]) -> tuple[bool, str]:
    if challenger is None:
        if champion is None:
            return True, "No champion to compare with and too few unseen bars to score the challenger; keeping the new model."
        return False, "Too few unseen bars to score the challenger fairly; the champion stays live."
    if champion is None:
        return True, "No previous model to compare with; the new model goes live."
    stale = champion_age_days is not None and champion_age_days > STALE_CHAMPION_DAYS
    required = champion["accuracy"] - (ACCURACY_TOLERANCE if stale else 0.0)
    if challenger["accuracy"] + 1e-9 < required:
        return False, (f"Challenger accuracy {challenger['accuracy']:.3f} is below the champion's {champion['accuracy']:.3f}"
                       f"{' less the stale-model tolerance' if stale else ''} on {challenger['rows']} unseen bars; champion restored.")
    if (champion.get("trades", 0) >= MIN_TRADES_FOR_DRAWDOWN_CHECK and challenger.get("trades", 0) >= MIN_TRADES_FOR_DRAWDOWN_CHECK
            and challenger["max_drawdown_pct"] > champion["max_drawdown_pct"] + DRAWDOWN_TOLERANCE_PCT):
        return False, (f"Challenger drawdown {challenger['max_drawdown_pct']:.1f}% is worse than the champion's "
                       f"{champion['max_drawdown_pct']:.1f}% on the same bars; champion restored.")
    return True, (f"Challenger accuracy {challenger['accuracy']:.3f} vs champion {champion['accuracy']:.3f} on "
                  f"{challenger['rows']} unseen bars{' (champion was stale)' if stale else ''}; new model promoted.")


def decide_lstm(champion_accuracy: Optional[float], challenger_accuracy: Optional[float],
                champion_age_days: Optional[float]) -> tuple[bool, str]:
    """Weaker check: each LSTM is scored on its own validation split."""
    if challenger_accuracy is None:
        return False, "The LSTM retrain produced no accuracy; the previous LSTM stays live."
    if champion_accuracy is None:
        return True, "No previous LSTM accuracy to compare with; the new LSTM goes live."
    stale = champion_age_days is not None and champion_age_days > STALE_CHAMPION_DAYS
    tolerance = ACCURACY_TOLERANCE if stale else 0.0
    if challenger_accuracy + tolerance + 1e-9 < champion_accuracy:
        return False, (f"New LSTM validation accuracy {challenger_accuracy:.3f} is below the previous "
                       f"{champion_accuracy:.3f}; previous LSTM restored.")
    return True, f"New LSTM validation accuracy {challenger_accuracy:.3f} vs previous {champion_accuracy:.3f}; promoted."


def record_decision(entry: dict) -> None:
    DECISIONS_PATH.parent.mkdir(exist_ok=True)
    decisions = load_decisions(limit=None)
    decisions.append(entry)
    tmp = DECISIONS_PATH.with_name(DECISIONS_PATH.name + ".tmp")
    tmp.write_text(json.dumps(decisions, indent=1), encoding="utf-8")
    tmp.replace(DECISIONS_PATH)


def load_decisions(limit: Optional[int] = 20) -> list:
    if not DECISIONS_PATH.exists():
        return []
    try:
        data = json.loads(DECISIONS_PATH.read_text(encoding="utf-8"))
    except ValueError:
        return []
    if not isinstance(data, list):
        return []
    return data if limit is None else data[-limit:]
