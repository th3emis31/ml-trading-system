"""Ask whether a symbol's model entries beat a permutation of its own signals.

One symbol per process, because this machine's memory ceiling has killed multi-symbol jobs twice.
Bars come from the APP's MT5 connection over HTTP, never a second mt5.initialize() - on 24 September
my own repeated MT5 connections halted two live strategies for two hours.

    python scripts/run_random_entry_check.py XAUUSD 500 [rf_proba|live_engine]

Default mode is rf_proba. live_engine re-predicts every out-of-sample bar through the full ensemble
and takes over an hour on 12,000 bars, which buys little here: the walk-forward has no per-fold LSTM
either way, so both modes are testing the same random forest. rf_proba keeps the whole history, and
more trades is what gives a permutation test its power.
"""
import json
import sys
import urllib.request
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.walkforward_backtest import run_walkforward_backtest

OUT = Path(__file__).resolve().parents[1] / "data" / "random_entry_benchmark.json"


def app_bars(symbol: str, timeframe: str = "4h", count: int = 12000) -> pd.DataFrame:
    url = f"http://localhost:5000/api/data/bars?symbol={symbol}&timeframe={timeframe}&count={count}"
    with urllib.request.urlopen(url, timeout=300) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not payload.get("available"):
        raise SystemExit(f"{symbol}: {payload.get('reason')}")
    frame = pd.DataFrame(payload["bars"])
    frame["datetime"] = pd.to_datetime(frame["datetime"], utc=True)
    print(f"{symbol}: {len(frame)} {timeframe} bars from {payload.get('source')}, "
          f"{frame['datetime'].iloc[0]:%Y-%m-%d} to {frame['datetime'].iloc[-1]:%Y-%m-%d}", flush=True)
    return frame[["datetime", "open", "high", "low", "close", "volume"]]


if __name__ == "__main__":
    symbol = (sys.argv[1] if len(sys.argv) > 1 else "XAUUSD").upper()
    draws = int(sys.argv[2]) if len(sys.argv) > 2 else 500
    mode = sys.argv[3] if len(sys.argv) > 3 else "rf_proba"
    frame = app_bars(symbol)
    buy_hold = (frame["close"].iloc[-1] / frame["close"].iloc[0] - 1) * 100

    def progress(stage, pct, message):
        print(f"  [{pct:3d}%] {message}", flush=True)

    out = run_walkforward_backtest(symbol, "5y", data=frame, n_folds=6, signal_mode=mode,
                                   random_draws=draws, progress=progress)
    if not out.get("available"):
        raise SystemExit(f"{symbol}: {out.get('reason')}")
    bench = out.get("random_entry_benchmark") or {}
    record = {"symbol": symbol, "bars": len(frame), "buy_and_hold_pct": round(buy_hold, 2),
              "signal_mode": mode, "metrics": out.get("metrics"),
              "inverse": (out.get("inverse_baseline") or {}).get("metrics"),
              "benchmark": bench}
    saved = {}
    if OUT.exists():
        try:
            saved = json.loads(OUT.read_text(encoding="utf-8"))
        except ValueError:
            saved = {}
    saved[symbol] = record
    OUT.write_text(json.dumps(saved, indent=1), encoding="utf-8")

    print(f"\n=== {symbol} ===", flush=True)
    print(f"  buy and hold over the window : {buy_hold:+.2f}%")
    print(f"  model, out of sample         : {bench.get('model_total_return_pct')}% "
          f"over {bench.get('model_trades')} trades")
    print(f"  random arrangements of its own signals ({bench.get('draws')} draws):")
    print(f"     mean {bench.get('random_mean_return_pct')}%   median {bench.get('random_median_return_pct')}%"
          f"   5th {bench.get('random_5th_pct')}%   95th {bench.get('random_95th_pct')}%")
    print(f"  percentile {bench.get('percentile')}   p-value {bench.get('p_value')}   "
          f"beats chance: {bench.get('beats_chance')}")
    print(f"  {bench.get('verdict')}")
