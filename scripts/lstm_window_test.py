"""Does the LSTM stop improving because of the model, or because of how little data it is given?

The daily learning run trains on `120d` of hourly bars - 2,880 rows - while the broker holds 50,000.
That is 5.8% of what exists, and each daily run adds 24 new bars: **0.83% new data**. A challenger
trained on a window 99.2% identical to yesterday's is the same dice re-rolled with a different random
seed, which is exactly the pattern in the record - challengers scattered between 0.46 and 0.54 with no
trend, and a champion frozen for weeks.

This measures the one thing that would settle it: hold everything else fixed and vary only the
training window.

SAFETY: models are redirected to a temporary folder through SMARTENTRY_MODELS_DIR before any src
module is imported, exactly as the test suite does. The live champions are never touched - on
24 September a diagnostic that called a training entry point overwrote them, and that must not repeat.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

SANDBOX = Path(tempfile.mkdtemp(prefix="lstm_window_"))
os.environ["SMARTENTRY_MODELS_DIR"] = str(SANDBOX)      # MUST be set before importing src
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import warnings                                          # noqa: E402

warnings.filterwarnings("ignore")

from src.features import build_features                  # noqa: E402
from src.lstm_model import LSTMTrader                     # noqa: E402
from src.mtf_data import fetch_app_bars                   # noqa: E402

WINDOWS = (2_880, 8_760, 20_000, 40_000)                 # 120d, 1y, 2.3y, 4.6y of hourly bars


def run(symbol: str = "XAUUSD", seeds: int = 2) -> list:
    rows = []
    biggest = max(WINDOWS)
    frame = fetch_app_bars(symbol, "1h", biggest)
    frame = frame.sort_values("datetime").reset_index(drop=True)
    print(f"{symbol}: {len(frame):,} hourly bars available for the test", flush=True)

    for window in WINDOWS:
        if window > len(frame):
            print(f"  skip {window:,}: only {len(frame):,} bars exist")
            continue
        slice_ = frame.tail(window).reset_index(drop=True)
        enriched = build_features(slice_)
        accuracies = []
        for seed in range(seeds):
            # Several seeds per window, because a single run's accuracy moves by more than the effect
            # being measured - reporting one would be reporting noise.
            try:
                import numpy as np
                import tensorflow as tf

                np.random.seed(seed)
                tf.random.set_seed(seed)
            except Exception:
                pass
            trader = LSTMTrader(f"{symbol}_w{window}_s{seed}")
            result = trader.train(enriched, epochs=50)
            # train() returns a tuple in some paths and a dict in others; read either rather than
            # assuming, because guessing here would silently record None for every run.
            # train() returns (model, metrics_dict). Read the dict out of whatever shape comes back
            # rather than assuming a position - looked at, not guessed.
            payload = result
            if isinstance(payload, (tuple, list)):
                payload = next((v for v in payload if isinstance(v, dict)), {})
            accuracy = (payload or {}).get("accuracy") if isinstance(payload, dict) else None
            if accuracy is not None:
                accuracies.append(float(accuracy))
            print(f"  window {window:>6,}  seed {seed}  accuracy {accuracy}", flush=True)
        if accuracies:
            rows.append({"window": window, "runs": len(accuracies),
                         "mean_accuracy": round(sum(accuracies) / len(accuracies), 4),
                         "best": round(max(accuracies), 4), "worst": round(min(accuracies), 4),
                         "spread": round(max(accuracies) - min(accuracies), 4),
                         "rows_after_features": len(enriched)})
    return rows


if __name__ == "__main__":
    symbol = (sys.argv[1] if len(sys.argv) > 1 else "XAUUSD").upper()
    results = run(symbol)
    print()
    print(f"{'window':>8} {'rows':>8} {'mean acc':>9} {'best':>7} {'worst':>7} {'spread':>7}")
    for row in results:
        print(f"{row['window']:>8,} {row['rows_after_features']:>8,} {row['mean_accuracy']:>9.4f} "
              f"{row['best']:>7.4f} {row['worst']:>7.4f} {row['spread']:>7.4f}")
    out = ROOT / "data" / "lstm_window_test.json"
    out.write_text(json.dumps({"symbol": symbol, "results": results,
                               "note": "Trained in a sandbox; the live models were not touched."},
                              indent=1), encoding="utf-8")
    print(f"\nsaved -> {out}")
    print(f"sandbox (safe to delete): {SANDBOX}")
