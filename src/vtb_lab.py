"""The owner's Volatility Trend Breakout, made searchable by the Strategy Lab. Research only.

Written 21 September 2026, after the owner pointed out that I had spent an afternoon adding a CRT
mechanism to the hourly search while **their own running strategies were not in it at all**:
"Is not only the CRT is where iis, the Swing trend pullback, Where is Volatility Trend Breakout,
you stack for CRT."

They were right. ``src/volatility_trend_breakout.py`` is the Python port of their TradingView Pine v5
script. It is the strategy behind magic 440603, the only one in this system sending orders, and the
only one with a closed forward trade (+2.18 R). It had a module, a demo executor and a forward
record - and no order builder, so the Strategy Lab could not explore a single variant of it.

This file is that builder. It does not touch ``volatility_trend_breakout.py``, which is a verbatim
copy of the owner's repository file (commit c9fffc2) and whose value is precisely that it is
unmodified: it reproduces their TradingView tester at profit factor 2.023 against 2.027.

WHAT THE SEARCH CAN NOW VARY, and why these and not others. The grid moves the rule's own
thresholds - trend filter, breakout buffer, RSI floor, stop distance, targets, trailing and the time
exit - because those are the knobs the owner tunes on the chart. It does **not** vary the execution
model (commission, slippage, tick size, leverage cap, ``tp1_model``): those describe the broker, not
the strategy, and changing them would make results incomparable with the TradingView row.

ONE DELIBERATE DIFFERENCE FROM THE PINE SCRIPT. Pine enters at the signal bar's close; the shared
engine in ``strategy_lab.simulate_orders`` enters at the **next bar's open**. That is the more
conservative of the two and it is the same assumption every other family in the Lab is measured
under, which is the point - a number here is comparable with the rest of BASELINE.md. It also means
these results will not match ``src/volatility_trend_breakout.backtest`` exactly, and they are not
meant to: that function stays the authority on TradingView parity.

READ THE SPLITS BACKWARDS FOR THIS STRATEGY. THIS IS THE IMPORTANT PART.

The Lab's split boundaries belong to the MARKET, not to a strategy: search 2008-2022, validation
2022-2024, holdout 2024-2026. They are a valid holdout for candidates the Lab searched, because the
search never saw those bars. They are NOT a valid holdout for this one. The owner built and tuned
this script on TradingView over 2023-2026, so the Lab's "holdout" sits INSIDE its tuning window and
its "search" window is the only genuinely out-of-sample period it has.

So for this strategy the rows inverT: the SEARCH row is the out-of-sample evidence and the HOLDOUT
row is in-sample. Measured 21 Sep 2026 on XAUUSD 4H:

    search     2008-2022   169 trades  PF 1.002   -1.67 %    <- the honest out-of-sample number
    validation 2022-2024    67 trades  PF 1.250   +5.59 %
    holdout    2024-2026    70 trades  PF 2.423  +35.18 %    <- inside the tuning window

BASELINE.md reached the same conclusion on 17 September from a different code path ("2007-2022: 251
legs, PF 0.974, -8.82 %... the whole profit is in 2023-2026, the window the script was built and
viewed on"), and the agreement between 0.974 there and 1.002 here is evidence the port is faithful.

A deflated Sharpe computed on the holdout row is therefore meaningless for this strategy, and a
trial count of 1 would be wrong regardless: BASELINE.md records at least six declared trials on the
atr_regime filter alone, plus the parity and inverse runs.

WHAT THIS MODULE IS ACTUALLY FOR, then: letting the search explore VARIANTS of the owner's rule and
scoring them the same way as every other family, and giving the strategy a forward record from now
through the shadow book. Its past cannot settle it. Only bars it has never seen can.

    python -m src.vtb_lab run [--symbols XAUUSD BTCUSD] [--timeframes 4h]
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from itertools import product

import numpy as np

from . import strategy_lab as lab
from . import volatility_trend_breakout as vtb

FAMILY = "volatility_breakout"


def _bar_candles(ind: lab.Indicators) -> list:
    """The port's Candle list for these bars, built by the demo executor's own converter.

    ``src/demo_volatility_breakout.py`` already turns this exact frame shape into Candles, and the
    live strategy uses it, so reusing it keeps research and execution reading the same bars the same
    way. Imported inside the function: it pulls in the executor module, which the hourly search
    should not pay for unless this family is actually drawn. It opens no MT5 connection on import.
    """
    from .demo_volatility_breakout import _candles
    return _candles(ind.df)


def config_from(params: dict) -> "vtb.Config":
    """A Config from a spec's params, leaving every execution-model field at the port's default."""
    cfg = vtb.Config()
    for key in ("ema_len", "use_ema_filter", "donchian_len", "atr_len", "atr_mult", "use_rsi",
                "rsi_len", "rsi_min", "use_volume", "vol_ma_len", "sl_atr_mult", "tp1_r", "tp2_r",
                "use_trail", "trail_atr", "use_time_exit", "max_bars", "be_atr", "inverse",
                "atr_regime", "atr_median_len"):
        if key in params and params[key] is not None:
            setattr(cfg, key, params[key])
    return cfg


def breakout_orders(ind: lab.Indicators, spec: dict):
    """side / stop / target per bar, straight from the port's own ``generate_signals``.

    The signals are generated by the owner's rule, unmodified. All this does is place them on the
    Lab's per-bar arrays so the shared simulator - and therefore the shared cost model, the split
    boundaries and the deflated Sharpe - apply to them exactly as to every other family.
    """
    params = spec.get("params") or {}
    cfg = config_from(params)
    n = len(ind.c)
    side = np.zeros(n, dtype=int)
    stop = np.full(n, np.nan)
    target = np.full(n, np.nan)

    candles = ind._cached(("vtb_candles",), lambda: _bar_candles(ind))
    for signal in vtb.generate_signals(candles, cfg):
        i = signal.index
        if not 0 <= i < n:
            continue
        risk = abs(signal.entry - signal.stop)
        if risk <= 0:
            continue
        side[i] = 1 if signal.direction == "long" else -1
        stop[i] = signal.stop
        target[i] = signal.tp2
    return side, stop, target


lab.ORDER_BUILDERS[FAMILY] = breakout_orders


def live_spec(symbol: str = "XAUUSD", timeframe: str = "4h") -> dict:
    """The strategy exactly as the demo executor runs it (magic 440603), for a like-for-like row."""
    cfg = vtb.Config()
    return {"name": "Volatility Trend Breakout (as running on demo, magic 440603)",
            "family": FAMILY,
            "params": {"symbol": symbol, "timeframe": timeframe, "ema_len": cfg.ema_len,
                       "use_ema_filter": cfg.use_ema_filter, "donchian_len": cfg.donchian_len,
                       "atr_len": cfg.atr_len, "atr_mult": cfg.atr_mult, "use_rsi": cfg.use_rsi,
                       "rsi_len": cfg.rsi_len, "rsi_min": cfg.rsi_min, "use_volume": cfg.use_volume,
                       "vol_ma_len": cfg.vol_ma_len, "sl_atr_mult": cfg.sl_atr_mult,
                       "tp1_r": cfg.tp1_r, "tp2_r": cfg.tp2_r, "use_trail": cfg.use_trail,
                       "trail_atr": cfg.trail_atr, "use_time_exit": cfg.use_time_exit,
                       "max_bars": cfg.max_bars, "be_atr": cfg.be_atr, "inverse": cfg.inverse,
                       "atr_regime": cfg.atr_regime, "atr_median_len": cfg.atr_median_len},
            "exits": {"stop": "builder", "sl_atr": cfg.sl_atr_mult, "rr": cfg.tp2_r,
                      "trail_atr": cfg.trail_atr if cfg.use_trail else 0.0,
                      "max_bars": cfg.max_bars if cfg.use_time_exit else 0,
                      "partial_at_r": cfg.tp1_r, "partial_pct": 50.0, "be_lock_r": cfg.tp1_r,
                      "swing_lookback": 5}}


def run(symbols=("XAUUSD", "BTCUSD"), timeframes=("4h",)) -> dict:
    from .mtf_data import load_bars

    registry = lab.load_registry()
    report = {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
              "strategy": "Volatility Trend Breakout (the owner's Pine v5, magic 440603)",
              "entry_model": "next bar open (the Lab's shared assumption, not Pine's close)",
              "places_orders": False, "markets": {}}
    for symbol, timeframe in product(symbols, timeframes):
        key = f"{symbol}:{timeframe}"
        bars = load_bars(symbol, timeframe, source="app")
        if bars is None or bars.empty:
            report["markets"][key] = {"error": "no broker bars"}
            continue
        market = lab.Market(symbol, timeframe, bars,
                            boundaries=(registry["markets"].get(key) or {}).get("boundaries"), swap=True)
        spec = live_spec(symbol, timeframe)
        side, _s, _t = breakout_orders(market.ind, spec)
        entry = {"symbol": symbol, "timeframe": timeframe, "bars": int(len(market.ind.c)),
                 "signals": int(np.count_nonzero(side)), "cost_round_trip_pct": market.cost_pct,
                 "period": market.info["periods"], "splits": {}}
        for split in market.rows:
            trades = market.simulate(spec, split)
            entry["splits"][split] = market.summary(split, trades) if trades else {"trades": 0}
        report["markets"][key] = entry
    lab.LAB_DIR.mkdir(parents=True, exist_ok=True)
    path = lab.LAB_DIR / f"vtb_lab_{datetime.now(timezone.utc):%Y%m%d}.json"
    path.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    report["path"] = str(path)
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Backtest the Volatility Trend Breakout in the Lab (research only)")
    parser.add_argument("command", choices=["run"])
    parser.add_argument("--symbols", nargs="+", default=["XAUUSD", "BTCUSD"])
    parser.add_argument("--timeframes", nargs="+", default=["4h"])
    args = parser.parse_args(argv)

    report = run(tuple(args.symbols), tuple(args.timeframes))
    print(f"{report['strategy']}\nentry: {report['entry_model']}   places_orders={report['places_orders']}\n")
    for key, market in report["markets"].items():
        if market.get("error"):
            print(f"{key}: {market['error']}")
            continue
        print(f"=== {key}  {market['bars']} bars, {market['signals']} signals, "
              f"costs {market['cost_round_trip_pct'] * 100:.4f}% round trip")
        for split, summary in market["splits"].items():
            if not summary.get("trades"):
                print(f"   {split:11s} no trades")
                continue
            print(f"   {split:11s} n {summary['trades']:4d}  PF {summary.get('profit_factor') or 0:5.3f}  "
                  f"net {summary.get('total_return_pct'):+9.2f}%  win {summary.get('win_rate_pct') or 0:5.1f}%  "
                  f"maxDD {summary.get('max_drawdown_pct'):6.2f}%")
        print()
    print(f"saved {report['path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
