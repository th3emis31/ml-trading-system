"""Does the LSTM's FEATURE SET explain why it never improves? A paired measurement, not an opinion.

Opened by the self-improvement loop (build map step 10) as the successor to two explanations that
were already measured and ruled out on 25 September 2026: too little data, and too small a training
window. The record of that morning says the champion looks like the best of many draws from a
distribution centred on chance - XAUUSD challengers over 30 recorded runs: mean 0.4909, sd 0.0286,
max 0.5424, and the champion IS that max, promoted once in 30 attempts.

The remaining candidate explanation is the feature set. `LSTM_FEATURE_COLUMNS` holds 21 columns, and
ten of them are clock and session encodings. `build_features` already computes price-structure columns
the LSTM never sees: fair-value gaps, pullback and reversal flags, a second moving-average pair, and
longer return and volatility horizons. If the network is starved of information rather than incapable,
adding those should move it.

## Why this is measured as a PAIRED difference

Comparing a new run against the champion's 0.5424 would be comparing a mean against the best of 30
draws, which is the multiple-comparisons trap this project has been caught by before. So both arms
are trained here, in the same process, on the same bars, with the same seeds, and what is reported is
the DIFFERENCE between them. The only thing that differs between the two arms is the column list.

Prints, for the loop to read:

    METRIC accuracy_gain = <extended mean minus current mean>

SAFETY: models are redirected to a temporary folder through SMARTENTRY_MODELS_DIR before any src
module is imported, exactly as tests/conftest.py does. The live champions are never touched - on 24
September a diagnostic that called a training entry point overwrote them, and that must not repeat.
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import tempfile
from pathlib import Path

SANDBOX = Path(tempfile.mkdtemp(prefix="lstm_features_"))
os.environ["SMARTENTRY_MODELS_DIR"] = str(SANDBOX)      # MUST precede any src import
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import warnings                                          # noqa: E402

warnings.filterwarnings("ignore")

from src import lstm_model                               # noqa: E402
from src.features import build_features                  # noqa: E402
from src.lstm_model import LSTMTrader                    # noqa: E402
from src.mtf_data import fetch_app_bars                  # noqa: E402

# Price structure the network has never been given. Every one of these is already computed by
# build_features on every bar - they are simply absent from the LSTM's column list.
EXTRA_FEATURES = [
    "fvg_up",          # fair-value gap up: an imbalance the owner's own strategies trade
    "fvg_down",
    "pullback",        # the setup the live SwingTrendPullback strategy is built on
    "reversal",
    "sma_5",           # a second moving-average pair, faster than the ema_9/ema_21 already present
    "sma_20",
    "return_5d",       # a longer horizon than the 1d and 3d returns already present
    "volatility_5d",
]

WINDOW = 8_760          # one year of hourly bars: enough to train, small enough to run several arms
SEEDS = (0, 1, 2)
EPOCHS = 50


def _train_once(frame, columns, symbol: str, seed: int):
    """One training run with a given column list, returning its validation accuracy or None."""
    try:
        import numpy as np
        import tensorflow as tf

        np.random.seed(seed)
        tf.random.set_seed(seed)
    except Exception:
        pass
    original = list(lstm_model.LSTM_FEATURE_COLUMNS)
    try:
        # The column list is module-level, so the arm is selected by swapping it and putting it back.
        lstm_model.LSTM_FEATURE_COLUMNS[:] = list(columns)
        trader = LSTMTrader(f"{symbol}_s{seed}_{len(columns)}c")
        result = trader.train(frame, epochs=EPOCHS)
        payload = result
        if isinstance(payload, (tuple, list)):           # train() returns (model, metrics)
            payload = next((item for item in payload if isinstance(item, dict)), {})
        value = (payload or {}).get("accuracy")
        return None if value is None else float(value)
    except Exception as exc:
        print(f"    seed {seed} failed: {type(exc).__name__}: {exc}", flush=True)
        return None
    finally:
        lstm_model.LSTM_FEATURE_COLUMNS[:] = original


def main(symbol: str = "XAUUSD") -> int:
    current = list(lstm_model.LSTM_FEATURE_COLUMNS)
    frame = fetch_app_bars(symbol, "1h", WINDOW)
    frame = frame.sort_values("datetime").reset_index(drop=True)
    enriched = build_features(frame)

    missing = [name for name in EXTRA_FEATURES if name not in enriched.columns]
    if missing:
        # Say so and settle nothing, rather than quietly testing a shorter list than was declared.
        print(f"cannot run: build_features did not produce {missing}")
        return 2
    extended = current + EXTRA_FEATURES

    print(f"{symbol}: {len(enriched):,} rows after features")
    print(f"  arm A: the current {len(current)} columns")
    print(f"  arm B: those plus {len(EXTRA_FEATURES)} price-structure columns = {len(extended)}")
    print(f"  {len(SEEDS)} seeds each, {EPOCHS} epochs, same bars, same seeds", flush=True)

    arms = {}
    for label, columns in (("current", current), ("extended", extended)):
        scores = []
        for seed in SEEDS:
            value = _train_once(enriched, columns, symbol, seed)
            print(f"    {label:9} seed {seed}  accuracy {value}", flush=True)
            if value is not None:
                scores.append(value)
        arms[label] = scores

    if not arms["current"] or not arms["extended"]:
        print("cannot settle: one arm produced no accuracy at all")
        return 3

    mean_current = statistics.mean(arms["current"])
    mean_extended = statistics.mean(arms["extended"])
    gain = mean_extended - mean_current

    print()
    print(f"  current  mean {mean_current:.4f}  from {arms['current']}")
    print(f"  extended mean {mean_extended:.4f}  from {arms['extended']}")
    print()
    print(f"METRIC accuracy_gain = {gain:.4f}")

    out = ROOT / "data" / "lstm_feature_test.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "symbol": symbol, "window": WINDOW, "seeds": list(SEEDS), "epochs": EPOCHS,
        "added": EXTRA_FEATURES,
        "current": {"columns": len(current), "scores": arms["current"], "mean": round(mean_current, 4)},
        "extended": {"columns": len(extended), "scores": arms["extended"], "mean": round(mean_extended, 4)},
        "accuracy_gain": round(gain, 4),
        "note": ("Paired: both arms trained in one process on the same bars with the same seeds, so the "
                 "difference is attributable to the columns. Sandboxed - the live models were not touched."),
    }, indent=1), encoding="utf-8")
    print(f"saved -> {out}")
    print(f"sandbox (safe to delete): {SANDBOX}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1].upper() if len(sys.argv) > 1 else "XAUUSD"))
