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
import math
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from .runtime_paths import smartentry_data_dir, smartentry_models_dir
from .train import FEATURE_COLUMNS
from .walkforward_backtest import BACKTEST_COSTS, LABEL_HORIZON_BARS, _simulate_trades, summarize_trades

MODELS_DIR = smartentry_models_dir()
ARCHIVE_DIR = MODELS_DIR / "archive"
DECISIONS_PATH = smartentry_data_dir() / "learning_decisions.json"

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


def accuracy_chance_band(rows: Optional[int]) -> float:
    """The 95 % band inside which an accuracy DIFFERENCE on ``rows`` bars is indistinguishable from luck.

    Two coin-flippers scored on the same n bars differ by about 1.96 x 0.5 x sqrt(2/n) by chance alone. On the
    565-bar holdouts these models are scored on, that is roughly 5.8 points - far wider than the gaps the gate
    was rejecting on. Measured 20 Sep 2026: every rejection in the four preceding runs was made on a gap INSIDE
    this band, so the gate was resolving noise.
    """
    n = int(rows or 0)
    if n <= 0:
        return 1.0                      # nothing to compare: treat every difference as noise
    return 1.96 * 0.5 * math.sqrt(2.0 / n)


TRADED_SOURCES = ("broker", "mt5")


def _trades_the_live_series(source: Optional[str]) -> bool:
    """True when a model was trained on the prices the system actually trades."""
    text = str(source or "").strip().lower()
    return text.startswith(TRADED_SOURCES)


def decide_rf(champion: Optional[dict], challenger: Optional[dict], champion_age_days: Optional[float],
              *, champion_source: Optional[str] = None, challenger_source: Optional[str] = None) -> tuple[bool, str]:
    """Which model serves live, decided on MONEY rather than on accuracy.

    Changed 20 Sep 2026 after measuring the old gate against its own record. It compared accuracy first and
    only reached the money checks if accuracy passed. Across 41 head-to-head runs the accuracy winner and the
    money winner agreed 17 times and disagreed 17 - a coin flip - and IN 17 OF 41 RUNS THE GATE KEPT THE MODEL
    THAT MADE LESS MONEY. On 20 Sep it preferred -5.653 % over -2.239 % for gold and -11.613 % over -10.518 %
    for bitcoin, taking the worse drawdown in both cases too.

    So the order is now: reject only on a REAL accuracy collapse (outside the chance band), then decide on
    after-cost return over the same bars, then keep the drawdown guard. Accuracy is a sanity check, not the
    verdict. Nothing here claims the promoted model is profitable - it is the better of two, and
    ``promoted_model_is_profitable`` in the decision record says whether it makes money at all.
    """
    if challenger is None:
        if champion is None:
            return True, "No champion to compare with and too few unseen bars to score the challenger; keeping the new model."
        return False, "Too few unseen bars to score the challenger fairly; the champion stays live."
    if champion is None:
        return True, "No previous model to compare with; the new model goes live."
    stale = champion_age_days is not None and champion_age_days > STALE_CHAMPION_DAYS
    band = accuracy_chance_band(challenger.get("rows"))
    gap = challenger["accuracy"] - champion["accuracy"]

    # 1. Sanity only: reject a challenger whose accuracy has genuinely collapsed, meaning the gap is worse
    #    than chance can explain. A gap inside the band carries no information and must not decide anything.
    allowance = band + (ACCURACY_TOLERANCE if stale else 0.0)
    if gap + 1e-9 < -allowance:
        return False, (f"Challenger accuracy {challenger['accuracy']:.3f} is {abs(gap) * 100:.1f} points below the "
                       f"champion's {champion['accuracy']:.3f} on {challenger['rows']} unseen bars, beyond the "
                       f"{band * 100:.1f}-point chance band{' plus the stale-model tolerance' if stale else ''}; "
                       f"champion restored.")

    # 2. The drawdown guard, unchanged, and applied BEFORE anything can promote. An earlier version of this
    #    rewrite put the no-return fallback above it, which let a challenger with double the drawdown through
    #    whenever the return figures were missing - caught by test_drawdown_guard_blocks_riskier_challenger.
    if (champion.get("trades", 0) >= MIN_TRADES_FOR_DRAWDOWN_CHECK
            and challenger.get("trades", 0) >= MIN_TRADES_FOR_DRAWDOWN_CHECK
            and challenger["max_drawdown_pct"] > champion["max_drawdown_pct"] + DRAWDOWN_TOLERANCE_PCT):
        return False, (f"Challenger drawdown {challenger['max_drawdown_pct']:.1f}% is worse than the champion's "
                       f"{champion['max_drawdown_pct']:.1f}% on the same bars; champion restored.")

    # 2b. SOURCE MATCH. A champion trained on a different price series than the system trades is not
    #     measuring the instrument. On 22 Sep 2026 the BTCUSD champion, trained on Yahoo's BTC-USD,
    #     was kept over a broker-trained challenger because it returned -13.069 % against -14.115 %
    #     on 578 unseen bars - a one-point difference on a sample swinging fourteen, which is noise -
    #     while Yahoo serves its own exchange mix rather than this broker's book. Keeping it means
    #     knowingly trading a model fitted to prices that do not exist on the account.
    #
    #     This does NOT lower the bar: the accuracy-collapse check and the drawdown guard above both
    #     still apply, so a genuinely broken challenger is still refused. It resolves a comparison the
    #     return figures cannot settle, in favour of the model that at least describes the right
    #     series. It only fires when the champion's source is known to be wrong AND the challenger's
    #     is known to be right; unknown sources change nothing.
    if _trades_the_live_series(challenger_source) and champion_source and not _trades_the_live_series(champion_source):
        return True, (f"Champion was trained on {champion_source} while the system trades broker prices, so its "
                      f"accuracy and return describe another series; the challenger is trained on "
                      f"{challenger_source} and cleared the accuracy and drawdown checks, so it is promoted on "
                      f"source match rather than on a return comparison that cannot settle it.")

    # 3. With no money to compare, fall back to the original contract: equal or better accuracy is promoted,
    #    with the stale-champion tolerance. This path exists for scored-but-untraded holdouts.
    champion_return = champion.get("total_return_pct")
    challenger_return = challenger.get("total_return_pct")
    if champion_return is None or challenger_return is None:
        tolerance = ACCURACY_TOLERANCE if stale else 0.0
        if challenger["accuracy"] + tolerance + 1e-9 >= champion["accuracy"]:
            return True, (f"No after-cost return to compare on {challenger['rows']} bars; on accuracy "
                          f"{challenger['accuracy']:.3f} vs {champion['accuracy']:.3f}"
                          f"{' (champion was stale)' if stale else ''}; new model promoted.")
        return False, (f"No after-cost return to compare, and challenger accuracy {challenger['accuracy']:.3f} is "
                       f"below the champion's {champion['accuracy']:.3f} on {challenger['rows']} bars; "
                       f"champion restored.")

    # 4. The verdict: after-cost return over the same bars.
    noise = "inside" if abs(gap) <= band else "outside"
    if challenger_return > champion_return + 1e-9:
        return True, (f"Challenger returns {challenger_return:+.3f}% against the champion's {champion_return:+.3f}% "
                      f"after costs on {challenger['rows']} unseen bars (drawdown {challenger['max_drawdown_pct']:.1f}% "
                      f"vs {champion['max_drawdown_pct']:.1f}%); accuracy gap {gap * 100:+.1f} points is {noise} the "
                      f"{band * 100:.1f}-point chance band{' and the champion was stale' if stale else ''}; "
                      f"new model promoted on after-cost return.")
    return False, (f"Challenger returns {challenger_return:+.3f}% against the champion's {champion_return:+.3f}% after "
                   f"costs on {challenger['rows']} unseen bars; accuracy gap {gap * 100:+.1f} points is {noise} the "
                   f"{band * 100:.1f}-point chance band, so it cannot decide; champion stays live on return.")


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


def learning_caller_context() -> dict:
    """Who asked for a learning cycle: the process and, inside the web app, the HTTP request.

    Added after the 16 Sep 2026 incident, where two off-schedule retrains could not be traced to their caller.
    """
    context = {"pid": os.getpid(), "parent_pid": os.getppid(), "argv": " ".join(sys.argv)[:240]}
    try:
        from flask import has_request_context, request
        if has_request_context():
            context["http"] = {"method": request.method, "path": request.path, "remote_addr": request.remote_addr,
                               "user_agent": str(request.user_agent)[:160], "referrer": request.referrer}
    except Exception:  # attribution is best effort and must never stop a decision being recorded
        pass
    return context


def promoted_model_is_profitable(entry: dict) -> Optional[bool]:
    """Does the model that will now serve live actually make money on the unseen bars?

    The gate picks the BETTER of two models; that is not the same as picking a good one. Measured
    20 Sep 2026, every gated run on record had the live model losing money on unseen bars, so a
    promotion can mean "loses less". Recording this separately keeps the two questions apart instead
    of letting a promotion read as good news. None when there is nothing to judge.
    """
    served = entry.get("rf_challenger") if entry.get("rf_promoted") else entry.get("rf_champion")
    if not isinstance(served, dict):
        return None
    value = served.get("total_return_pct")
    return None if value is None else bool(value > 0)


def record_decision(entry: dict) -> None:
    DECISIONS_PATH.parent.mkdir(exist_ok=True)
    decisions = load_decisions(limit=None)
    entry = {**entry, "caller": entry.get("caller") or learning_caller_context(),
             "promoted_model_is_profitable": promoted_model_is_profitable(entry)}
    decisions.append(entry)
    tmp = DECISIONS_PATH.with_name(DECISIONS_PATH.name + ".tmp")
    tmp.write_text(json.dumps(decisions, indent=1), encoding="utf-8")
    tmp.replace(DECISIONS_PATH)


def champion_training_source(symbol: str) -> Optional[str]:
    """The price source the live champion was TRAINED on: the source of the run that promoted it.

    Not the most recent run's source. src/drift_watch.py makes the same distinction and its comment
    says why: reading the latest row would call a Yahoo-trained accuracy "broker-measured" and report
    an edge that does not exist. Returns None when nothing has promoted yet, which means "unknown"
    and must not be treated as a mismatch.
    """
    rows = [r for r in load_decisions(limit=None)
            if r.get("symbol") == symbol and r.get("rf_promoted") and r.get("data_source")]
    return str(rows[-1]["data_source"]) if rows else None


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
