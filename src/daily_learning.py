from __future__ import annotations

from pathlib import Path
import json
from datetime import datetime

from .data import fetch_real_data
from .features import build_features
from .train import train_model, save_lstm_metrics, ensemble_predict
from .lstm_model import LSTMTrader
from . import model_promotion as promotion
from .runtime_paths import live_training_allowed, smartentry_data_dir


def training_bars(symbol: str, period: str, interval: str):
    """Candles to learn from: the broker's own, falling back to Yahoo only when the broker cannot be read.

    Until 2026-09-18 this trained on Yahoo. Yahoo proxies XAUUSD with the GC=F futures contract, which carries roughly
    1.4 % basis against the broker's spot, so the models were learning a price series they would never trade at. The
    broker's candles come through the running app (never a second MetaTrader connection), and the frame keeps its
    ``source`` tag so a run always records which prices it learned from.
    """
    from .mtf_data import fetch_app_bars

    bars_needed = {"1h": 24 * int(str(period).rstrip("d") or 120), "1d": int(str(period).rstrip("d") or 240)}
    try:
        frame = fetch_app_bars(symbol, interval, bars_needed.get(interval, 3000))
    except Exception:
        frame = None
    if frame is not None and not frame.empty and str(frame.attrs.get("source", "")).startswith("app:mt5"):
        frame = frame.sort_values("datetime").reset_index(drop=True)
        frame.attrs["source"] = "broker"
        return frame
    fallback = fetch_real_data(symbol, period=period, interval=interval)
    if str(fallback.attrs.get("source")) == "yahoo":
        fallback.attrs["source"] = "yahoo_fallback"     # the broker could not be read; say so in the record
    return fallback


class DailyLearner:
    def __init__(self, symbol: str):
        self.symbol = symbol
        self.history_dir = smartentry_data_dir()
        self.history_dir.mkdir(exist_ok=True)

    def _history_path(self, frequency: str) -> Path:
        suffix = "daily" if frequency == "daily" else "weekly"
        return self.history_dir / f"{self.symbol.lower()}_{suffix}_history.json"

    def _append_history(self, frequency: str, entry: dict) -> None:
        history_path = self._history_path(frequency)
        history = []
        if history_path.exists():
            try:
                history = json.loads(history_path.read_text(encoding="utf-8"))
            except ValueError:
                history = []
        history.append(entry)
        # 14 Sep 2026: kept 52 runs, which cut the self-learning curve to under two months of daily runs.
        history_path.write_text(json.dumps(history[-1000:], indent=2), encoding="utf-8")

    def run_cycle(self, frequency: str = "daily", use_ensemble: bool = False, gate: bool = True):
        """Retrain the RF and LSTM for this symbol.

        With ``gate`` on (the default) the live models are only replaced when the
        retrained ones are not worse: the current models are archived first, the
        new random forest is scored against the archived one on bars it never
        trained on, and the previous files are restored when the new model loses.
        Every outcome is recorded in data/learning_decisions.json. Training never
        runs on synthetic prices.
        """
        # Training writes the challenger straight over the live files, so the learning-window gate must refuse here,
        # before any data is fetched or any file is touched (see src/runtime_paths.py).
        allowed, gate_reason = live_training_allowed()
        if not allowed:
            blocked = {
                "symbol": self.symbol,
                "frequency": frequency,
                "status": "blocked_outside_learning_window",
                "reason": gate_reason,
                "rf_promoted": False,
                "lstm_promoted": False,
                "trained_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            promotion.record_decision(blocked)
            return blocked
        interval = "1h" if frequency == "daily" else "1d"
        period = "120d" if frequency == "daily" else "240d"
        data = training_bars(self.symbol, period, interval)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if data.empty:
            return {"symbol": self.symbol, "status": "no_data", "frequency": frequency}

        if data.attrs.get("source") == "synthetic":
            # fetch_real_data falls back to generated prices when Yahoo is empty;
            # training on those would overwrite a real model with noise.
            entry = {
                "symbol": self.symbol,
                "frequency": frequency,
                "status": "skipped_synthetic_data",
                "rows": len(data),
                "reason": "Yahoo returned no history, so only generated prices were available. The live models were left untouched.",
                "trained_at": now,
            }
            promotion.record_decision({**entry, "rf_promoted": False, "lstm_promoted": False})
            self._append_history(frequency, entry)
            return entry

        archive = promotion.archive_champion(self.symbol) if gate else {}
        champion_rf = promotion.load_archived_rf(archive, self.symbol) if gate else None
        champion_rf_metrics = promotion.load_archived_json(archive, f"{self.symbol.lower()}_metrics.json") if gate else {}
        champion_lstm_metrics = promotion.load_archived_json(archive, f"{self.symbol.lower()}_lstm_metrics.json") if gate else {}

        rf_model, rf_metrics = train_model(data, self.symbol)  # writes the challenger files

        rf_promoted, rf_reason = True, "Promotion gate disabled; the new model was kept."
        champion_eval = challenger_eval = None
        if gate:
            try:
                features = build_features(data)
                holdout = promotion.challenger_holdout(features)
                challenger_eval = promotion.evaluate_rf(rf_model, holdout, self.symbol)
                champion_eval = promotion.evaluate_rf(champion_rf, holdout, self.symbol) if champion_rf is not None else None
                rf_promoted, rf_reason = promotion.decide_rf(champion_eval, challenger_eval, archive.get("rf_age_days"))
            except Exception as exc:
                rf_promoted = champion_rf is None
                rf_reason = f"Evaluation failed ({exc}); " + (
                    "no previous model, so the new one was kept." if rf_promoted else "the previous model was restored.")
            if not rf_promoted:
                promotion.restore_files(archive, self.symbol, promotion.RF_FILES)

        lstm_metrics = None
        try:
            lstm_trainer = LSTMTrader(self.symbol)
            _, lstm_metrics = lstm_trainer.train(data)
            save_lstm_metrics(lstm_metrics, self.symbol)
        except Exception as exc:
            lstm_metrics = {
                "symbol": self.symbol,
                "accuracy": None,
                "loss": None,
                "trained_rows": len(data),
                "status": "lstm_failed",
                "error": str(exc),
                "trained_at": now,
            }

        lstm_promoted, lstm_reason = True, "Promotion gate disabled; the new LSTM was kept."
        if gate:
            lstm_promoted, lstm_reason = promotion.decide_lstm(
                champion_lstm_metrics.get("accuracy"), lstm_metrics.get("accuracy"), archive.get("rf_age_days"))
            if not lstm_promoted:
                promotion.restore_files(archive, self.symbol, promotion.LSTM_FILES)

        # The live model's accuracy must describe the bars it is being judged on TODAY. When the champion
        # is kept, its stored metrics figure is whatever it scored when it was trained - for gold that was
        # Yahoo's GC=F proxy, and on 19 Sep 2026 it displayed 0.5639 for bitcoin whose re-scored accuracy
        # on current broker bars was 0.5312, overstating it by 3.3 points. champion_eval is that re-scored
        # figure, computed a few lines above on the same holdout the challenger was scored on, so it is
        # both current and comparable. The stored figure is only a fallback.
        if rf_promoted:
            live_rf_accuracy = rf_metrics.get("accuracy")
        else:
            live_rf_accuracy = ((champion_eval or {}).get("accuracy")
                                or champion_rf_metrics.get("accuracy", rf_metrics.get("accuracy")))
        live_lstm_accuracy = lstm_metrics.get("accuracy") if lstm_promoted else champion_lstm_metrics.get("accuracy")
        entry = {
            "symbol": self.symbol,
            "frequency": frequency,
            "status": "trained",
            "rows": len(data),
            "accuracy": live_rf_accuracy,
            "lstm_accuracy": live_lstm_accuracy,
            "lstm_status": lstm_metrics.get("status"),
            "challenger_accuracy": rf_metrics.get("accuracy"),
            # Where the "accuracy" above came from, so nobody has to guess whether it is current.
            "accuracy_basis": ("challenger metrics, just trained" if rf_promoted else
                               "champion re-scored on this run's holdout" if (champion_eval or {}).get("accuracy")
                               else "champion's stored metrics from when it was trained"),
            "champion_rescored_accuracy": (champion_eval or {}).get("accuracy"),
            "challenger_lstm_accuracy": lstm_metrics.get("accuracy"),
            "rf_promoted": rf_promoted,
            "rf_decision": rf_reason,
            "lstm_promoted": lstm_promoted,
            "lstm_decision": lstm_reason,
            "trained_at": now,
            # Which prices this run learned from. Until 2026-09-18 every run used Yahoo, whose XAUUSD is the GC=F
            # futures proxy about 1 % from the broker's spot; accuracy either side of that change is not the same
            # measurement, so the learning curve needs to know where the source changed.
            "data_source": data.attrs.get("source"),
        }

        # PHASE 3.2 IMPROVEMENT: Add ensemble voting results (optional)
        if use_ensemble and lstm_metrics and lstm_metrics.get("accuracy"):
            try:
                # Get ensemble combination of the accuracies as probabilities
                lstm_prob = lstm_metrics.get("accuracy", 0.5)
                rf_prob = rf_metrics.get("accuracy", 0.5)

                ensemble_result = ensemble_predict(lstm_prob, rf_prob)

                entry["ensemble_probability"] = ensemble_result["probability"]
                entry["ensemble_confidence"] = ensemble_result["confidence"]
                entry["ensemble_signal"] = ensemble_result["signal"]
                entry["model_agreement"] = ensemble_result["agreement"]
            except Exception as e:
                # Graceful fallback: if ensemble fails, continue without it
                entry["ensemble_error"] = str(e)

        if gate:
            promotion.record_decision({
                **entry,
                "data_source": data.attrs.get("source"),
                "rf_champion": champion_eval,
                "rf_challenger": challenger_eval,
                "champion_age_days": archive.get("rf_age_days"),
                "archive": archive.get("path"),
            })
        self._append_history(frequency, entry)
        return entry


def main(argv=None) -> int:
    """Daily self-learning run (14 Sep 2026), scheduled by the "SmartEntry Daily Learning" task.

    Retrains each symbol with the promotion gate on: a new model replaces the live one only when it is not
    worse on bars it never trained on. Learning only; it never places orders.
    """
    import argparse
    parser = argparse.ArgumentParser(description="Gated daily retraining for the RF and LSTM models.")
    parser.add_argument("--symbols", nargs="+", default=["XAUUSD", "BTCUSD"])
    parser.add_argument("--frequency", choices=["daily", "weekly"], default="daily")
    args = parser.parse_args(argv)
    failures = 0
    for symbol in args.symbols:
        started = datetime.now()
        try:
            result = DailyLearner(symbol.upper()).run_cycle(args.frequency)
        except Exception as exc:  # one symbol failing must not stop the other
            failures += 1
            result = {"symbol": symbol.upper(), "frequency": args.frequency, "status": "failed", "error": str(exc),
                      "trained_at": started.strftime("%Y-%m-%d %H:%M:%S")}
            promotion.record_decision({**result, "rf_promoted": False, "lstm_promoted": False})
        result["duration_seconds"] = round((datetime.now() - started).total_seconds(), 1)
        print(json.dumps(result, default=str), flush=True)
    return 1 if failures == len(args.symbols) else 0


if __name__ == "__main__":
    raise SystemExit(main())
