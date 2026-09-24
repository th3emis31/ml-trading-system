"""Permutation benchmark for the Volatility Trend Breakout - the strategy that is actually earning.

It is long-only and gold rose 242 % over the window, so "it made money" cannot say whether the
breakout condition picks good moments or merely picks moments. Each draw fires the same number of
entries at random bars, through the strategy's own exits.

Bars come from the app's MT5 connection over HTTP, never a second mt5.initialize().
"""
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.volatility_trend_breakout import Candle, Config, backtest, metrics, permutation_benchmark

OUT = Path(__file__).resolve().parents[1] / "data" / "strategy_permutation.json"


def app_candles(symbol="XAUUSD", timeframe="4h", count=12000):
    url = f"http://localhost:5000/api/data/bars?symbol={symbol}&timeframe={timeframe}&count={count}"
    with urllib.request.urlopen(url, timeout=300) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not payload.get("available"):
        raise SystemExit(f"{symbol}: {payload.get('reason')}")
    bars = payload["bars"]
    print(f"{symbol}: {len(bars)} {timeframe} bars from {payload.get('source')}, "
          f"{bars[0]['datetime'][:10]} to {bars[-1]['datetime'][:10]}", flush=True)
    return [Candle(ts=b["datetime"], open=b["open"], high=b["high"], low=b["low"],
                   close=b["close"], volume=b.get("volume") or 0.0) for b in bars]


def slice_years(candles, start=None, end=None):
    """A date window, so the benchmark can be run on years the parameter tuning never saw.

    The permutation controls for DRIFT - it cannot control for PARAMETER SELECTION. These inputs were
    tuned by the owner on gold, so on the tuned period a good result is partly the tuning showing up.
    Only an untouched window answers the question cleanly.
    """
    out = [c for c in candles
           if (start is None or c.ts[:10] >= start) and (end is None or c.ts[:10] < end)]
    return out


if __name__ == "__main__":
    draws = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    start = sys.argv[2] if len(sys.argv) > 2 else None
    end = sys.argv[3] if len(sys.argv) > 3 else None
    candles = app_candles()
    if start or end:
        candles = slice_years(candles, start, end)
        print(f"  window {start or 'start'} -> {end or 'end'}: {len(candles)} bars", flush=True)
    buy_hold = (candles[-1].close / candles[0].close - 1) * 100
    cfg = Config()
    real = metrics(backtest(candles, cfg), cfg)
    print(f"  strategy: {real['net_pct']:+.2f}% over {real['closed_legs']} closed legs "
          f"({real['positions']} positions), PF {real['profit_factor']:.3f}, "
          f"expectancy {real['expectancy_r_per_position']:.4f} R/position", flush=True)
    print(f"  permuting {draws} times...", flush=True)
    bench = permutation_benchmark(candles, cfg, draws=draws)
    OUT.write_text(json.dumps({"symbol": "XAUUSD", "buy_and_hold_pct": round(buy_hold, 2),
                               "strategy_metrics": real, "benchmark": bench}, indent=1, default=str),
                   encoding="utf-8")
    print()
    if not bench.get("available"):
        raise SystemExit(f"  not measurable: {bench.get('reason')}")
    print(f"=== Volatility Trend Breakout, XAUUSD 4h ===")
    print(f"  buy and hold                 : {buy_hold:+.2f}%")
    print(f"  strategy                     : {bench['strategy_return_pct']:+.2f}%  "
          f"expectancy {bench['strategy_expectancy_r']:+.4f} R/position over {bench['entries_permuted']} entries")
    print(f"  random entry sets ({bench['draws']} draws):")
    print(f"     return    mean {bench['random_mean_return_pct']:+.2f}%  "
          f"[5th {bench['random_5th_return_pct']:+.2f}%, 95th {bench['random_95th_return_pct']:+.2f}%]")
    print(f"     expectancy mean {bench['random_mean_expectancy_r']:+.4f} R")
    print(f"  expectancy percentile {bench['expectancy_percentile']}   p {bench['expectancy_p_value']}   "
          f"(return percentile {bench['return_percentile']})")
    print(f"  beats chance: {bench['beats_chance']}")
    print(f"  {bench['verdict']}")
