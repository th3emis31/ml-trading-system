from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.exceptions import NotFittedError
from sklearn.preprocessing import StandardScaler
from tensorflow import keras
from tensorflow.keras import layers

from .features import build_features as build_classic_features, build_inference_features
from .runtime_paths import smartentry_models_dir

LSTM_FEATURE_COLUMNS = [
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
    "volatility_3d",
    "ema_9",
    "ema_21",
    "ema_spread",
    "rsi_14",
    "atr_14",
    "atr_pct",
    "trend_5",
    "trend_20",
]


class LSTMTrader:
    def __init__(self, symbol: str, lookback: int = 30):
        self.symbol = symbol
        self.lookback = lookback
        self.model = None
        self.model_path = smartentry_models_dir() / f"{symbol.lower()}_lstm.keras"
        self.metadata_path = smartentry_models_dir() / f"{symbol.lower()}_lstm_meta.json"
        # The scaler has to be persisted next to the network: a prediction made
        # in a fresh process has no fitted scaler otherwise, and every call
        # raises NotFittedError.
        self.scaler_path = smartentry_models_dir() / f"{symbol.lower()}_lstm_scaler.joblib"
        self.scaler = StandardScaler()
        self.scaler_ready = False

    def build_features(self, df: pd.DataFrame) -> pd.DataFrame:
        return build_classic_features(df)

    def save_scaler(self):
        self.scaler_path.parent.mkdir(exist_ok=True)
        joblib.dump(self.scaler, self.scaler_path)
        self.scaler_ready = True

    def load_scaler(self) -> bool:
        """Restore the training-time scaler. False when none was persisted."""
        if self.scaler_ready:
            return True
        if not self.scaler_path.exists():
            return False
        try:
            self.scaler = joblib.load(self.scaler_path)
            self.scaler_ready = True
            return True
        except Exception:
            return False

    def prepare_sequences(self, df: pd.DataFrame, fit_scaler: bool = False):
        values = df[LSTM_FEATURE_COLUMNS].to_numpy(dtype=float)
        if fit_scaler:
            self.scaler.fit(values)
            self.save_scaler()
        elif not self.load_scaler():
            raise NotFittedError(
                f"No persisted scaler for {self.symbol}; retrain the LSTM to regenerate it."
            )
        values_scaled = self.scaler.transform(values)
        
        X = []
        y = []
        targets = df["target"].to_numpy(dtype=int)
        for i in range(self.lookback, len(values_scaled)):
            X.append(values_scaled[i - self.lookback:i])
            y.append(targets[i])
        X = np.array(X, dtype=float)
        y = np.array(y, dtype=int)
        return X, y

    def train(self, df: pd.DataFrame, epochs: int = 50, use_best_model_checkpoint: bool = True):
        prepared = self.build_features(df)
        quality_mask = prepared.get("quality_move", pd.Series([1] * len(prepared), index=prepared.index)).astype(bool)
        quality_prepared = prepared[quality_mask]
        if len(quality_prepared) >= 80:
            prepared = quality_prepared
        X, y = self.prepare_sequences(prepared, fit_scaler=True)
        if len(X) < 60:
            raise ValueError("Not enough data for LSTM training")
        split_index = int(len(X) * 0.8)
        X_train, X_test = X[:split_index], X[split_index:]
        y_train, y_test = y[:split_index], y[split_index:]
        self.model = keras.Sequential(
            [
                layers.Input(shape=(self.lookback, len(LSTM_FEATURE_COLUMNS))),
                layers.LSTM(64, return_sequences=True, kernel_regularizer=keras.regularizers.l2(1e-5)),
                layers.Dropout(0.2),
                layers.BatchNormalization(),
                layers.LSTM(32, kernel_regularizer=keras.regularizers.l2(1e-5)),
                layers.Dropout(0.2),
                layers.BatchNormalization(),
                layers.Dense(16, activation="relu", kernel_regularizer=keras.regularizers.l2(1e-5)),
                layers.Dropout(0.1),
                layers.Dense(1, activation="sigmoid"),
            ]
        )
        self.model.compile(optimizer=keras.optimizers.Adam(learning_rate=0.001), loss="binary_crossentropy", metrics=["accuracy"])
        early_stop = keras.callbacks.EarlyStopping(monitor="val_loss", patience=3, restore_best_weights=True)
        reduce_lr = keras.callbacks.ReduceLROnPlateau(monitor="val_loss", factor=0.5, patience=2, min_lr=1e-6)
        
        # PHASE 2 IMPROVEMENT: Add ModelCheckpoint to save best model automatically
        checkpoint_path = self.model_path.parent / f"{self.symbol.lower()}_lstm_best.keras"
        model_checkpoint = keras.callbacks.ModelCheckpoint(
            str(checkpoint_path),
            monitor="val_loss",
            save_best_only=True,
            mode="min",
            verbose=0
        )
        
        callbacks = [early_stop, reduce_lr, model_checkpoint] if use_best_model_checkpoint else [early_stop, reduce_lr]
        
        self.model.fit(X_train, y_train, epochs=epochs, batch_size=16, validation_split=0.1, callbacks=callbacks, verbose=0)
        self.model_path.parent.mkdir(exist_ok=True)
        self.model.save(self.model_path)
        loss, accuracy = self.model.evaluate(X_test, y_test, verbose=0)
        self.metadata_path.write_text(json.dumps({"lookback": self.lookback, "symbol": self.symbol, "trained_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}), encoding="utf-8")
        
        # PHASE 2 IMPROVEMENT: Compute additional metrics for trading evaluation
        from sklearn.metrics import precision_score, recall_score, f1_score, confusion_matrix
        y_pred = (self.model.predict(X_test, verbose=0) > 0.5).astype(int).flatten()
        y_test_flat = y_test.astype(int).flatten()
        
        precision = precision_score(y_test_flat, y_pred, zero_division=0)
        recall = recall_score(y_test_flat, y_pred, zero_division=0)
        f1 = f1_score(y_test_flat, y_pred, zero_division=0)
        cm = confusion_matrix(y_test_flat, y_pred)
        
        metrics = {
            "symbol": self.symbol,
            "accuracy": float(accuracy),
            "loss": float(loss),
            "precision": float(precision),
            "recall": float(recall),
            "f1_score": float(f1),
            "confusion_matrix": cm.tolist(),
            "trained_rows": len(df),
            "quality_rows": int(len(prepared)),
            "target_pip_min": float(prepared.get("target_pip_min", pd.Series([0])).iloc[0]) if not prepared.empty else None,
            "target_pip_max": float(prepared.get("target_pip_max", pd.Series([0])).iloc[0]) if not prepared.empty else None,
            "feature_count": len(LSTM_FEATURE_COLUMNS),
            "trained_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        return self.model, metrics

    def predict(self, df: pd.DataFrame):
        if self.model is None and self.model_path.exists():
            self.model = keras.models.load_model(self.model_path)
        # Inference keeps the newest bars whose 3-bar look-ahead target is still unknown (training uses build_features).
        prepared = build_inference_features(df)
        X, _ = self.prepare_sequences(prepared, fit_scaler=False)
        if len(X) == 0:
            return None
        probs = self.model.predict(X[-1:][0:1], verbose=0)[0][0]
        return float(probs)
