"""Leak-free walk-forward backtest of the RF signal strategy on real history.

What this module guarantees (see the backtesting rules in CLAUDE.md):

- Time ordered. Every fold trains only on bars that end before its test window,
  with a purge gap equal to the label horizon, so no training label overlaps a
  test bar. Features in ``build_features`` use past bars only.
- The live model is never touched. Folds are fitted with the same RandomForest
  helper ``src.train`` uses, but nothing is saved to ``models/``.
- Real data only. History comes from Yahoo; when Yahoo returns nothing the run
  reports ``available: False``. The synthetic generator is never used here
  (tests may inject a frame through ``data=``).
- Costs from ``BACKTEST_COSTS`` are charged on every trade.
- The agreed metric set is reported every time, and fewer than
  ``MIN_TRADES_FOR_EVIDENCE`` trades is labelled insufficient evidence.
- The seed, feature list, thresholds, costs and data range travel with the
  result so a run can be reproduced.

The trade rule mirrors the live path: ensemble-style probability bands decide
BUY / SELL / HOLD, the stop sits 1.2 x ATR-proxy from entry and the first target
2.4 x away (``build_live_plan``), and whichever is touched first settles the trade
(stop checked first, as ``build_shadow_simulation`` does).
"""
from __future__ import annotations

import math
import time
from datetime import datetime, timezone
from typing import Callable, Optional

import numpy as np
import pandas as pd

from .data import YAHOO_TICKERS, fetch_yahoo_history
from .features import build_features
from .train import FEATURE_COLUMNS, _fit_random_forest_with_optional_calibration

RANDOM_SEED = 42  # RandomForestClassifier(random_state=42) inside src.train
MIN_TRADES_FOR_EVIDENCE = 100
LABEL_HORIZON_BARS = 3  # build_features -> add_quality_targets(horizon=3)
STOP_ATR_MULT = 1.2
TARGET_ATR_MULT = 2.4
MIN_TRAIN_ROWS = 200
SIGNAL_MODES = {"live_engine", "rf_proba"}

# Round-trip cost per trade as a fraction of price: spread + commission + slippage.
BACKTEST_COSTS = {
    # Corrected 19 Sep 2026 from MEASURED broker spreads, after the owner pushed back that every
    # strategy could not be losing. The old constants contradicted their own notes: the note said
    # "about 0.30 spread on a 4,400 price", which is 0.0068 %, while the constant 0.0004 is read as a
    # FRACTION and charges 0.04 % - $1.76 on that price, roughly six times the real cost.
    #
    # Measured over 132,000 broker bars through /api/data/bars, which now serves MT5's per-bar spread:
    #   XAUUSD 4h/1h  median spread 0.16 (0.0062-0.0069 %), 90th percentile 0.20, 99th 0.23
    #   BTCUSD 4h/1h  median spread ~16 (0.0295-0.0394 %), 90th percentile ~36, 99th ~55
    # Confirmed live on 19 Sep by the owner's own terminal: BTCUSD bid 81683.64 / ask 81700.60.
    #
    # These figures are the 90th-percentile spread DOUBLED, so half the charge is spread at a bad
    # moment and the other half is a slippage allowance. That is still well under the old constants
    # and it is deliberately conservative rather than optimistic.
    # Re-measured 22 Sep 2026 on M1 bars exported from the owner's own Vantage terminal, which carry
    # the broker's per-minute <SPREAD> column: 100,850 gold minutes (10 Jun - 22 Sep) and 100,912
    # bitcoin minutes (14 Jul - 22 Sep). That is finer than the 4h/1h bar spreads used on 19 Sep.
    #
    # THE CONVENTION IS UNCHANGED - 90th-percentile spread DOUBLED - so this corrects the measurement
    # and not the method. Changing both at once is how an optimistic cost arrives wearing the label
    # of a correction.
    #
    #   XAUUSD  p50 0.0053 %  p90 0.0057 %  p99 0.0069 %  worst minute 0.0075 %
    #   BTCUSD  p50 0.0257 %  p90 0.0267 %  p99 0.0270 %  worst minute 0.0272 %
    #
    # Gold goes UP. Its spread has widened since September (p90 23 points against the 20 measured
    # then), so the honest figure is dearer than the one it replaces. Bitcoin goes down by 41 %: it
    # was being charged roughly three times what the broker takes, which suppressed real results.
    #
    # Tested for widening under stress, because a calm sample would hide it: in the top 1 % of
    # minutes by range, gold widens 1.04x and bitcoin 0.84x - bitcoin is TIGHTER when active. Gold's
    # first bar after a session break costs 0.0065 % against a 0.0053 % norm, still inside the figure.
    #
    # Caveat that limits this: three months of gold and two of bitcoin, no NFP spike and no stressed
    # Monday gap in the window. The doubling is what carries that risk, which is why it stays.
    "XAUUSD": {"round_trip_pct": 0.000115,
               "note": "0.0057 % spread at the 90th percentile, doubled for slippage; measured "
                       "2026-09-22 over 100,850 M1 bars with the broker's own spread column "
                       "(was 0.00009, measured on coarser 4h/1h bars when the spread was tighter)"},
    "BTCUSD": {"round_trip_pct": 0.000534,
               "note": "0.0267 % spread at the 90th percentile, doubled for slippage; measured "
                       "2026-09-22 over 100,912 M1 bars (was 0.0009, about 3.4x the real spread, "
                       "which was suppressing genuinely profitable bitcoin candidates)"},
    # Added 24 Sep 2026 for the owner's three new markets, measured the SAME way on 60,000 M1 bars
    # each from their own terminal, using the broker's per-minute spread column. The method was
    # validated before it was trusted: re-measuring gold and bitcoin with it reproduced the stored
    # figures almost exactly (XAUUSD 0.00011324 against 0.000115, BTCUSD 0.00053047 against
    # 0.000534), so these three are on the same footing as the two that were already here.
    #
    #   NAS100  p50 0.0027 %  p90 0.0028 %  p99 0.0029 %  worst 0.0037 %
    #   ETHUSD  p50 0.0995 %  p90 0.1293 %  p99 0.1310 %  worst 0.1320 %
    #   XRPUSD  p50 0.5922 %  p90 0.8207 %  p99 0.8308 %  worst 0.8430 %
    #
    # XRPUSD IS THE FINDING, and it is a warning rather than an opportunity: a round trip costs
    # 1.64 % of price, which is 31x bitcoin and 145x gold. A strategy needs an edge larger than that
    # before it earns anything at all, so an XRP result that looks profitable under a generic cost
    # assumption is almost certainly an artefact of the assumption. Charging it honestly is the only
    # way the comparison between markets means anything.
    #
    # Same caveat as the others: two months of minutes, no stressed gap in the window. The doubling
    # is what carries that risk.
    "NAS100": {"round_trip_pct": 0.0000564,
               "note": "0.0028 % spread at the 90th percentile, doubled for slippage; measured "
                       "2026-09-24 over 60,000 M1 bars. The cheapest market here - less than half "
                       "gold's cost"},
    "ETHUSD": {"round_trip_pct": 0.00258555,
               "note": "0.1293 % spread at the 90th percentile, doubled for slippage; measured "
                       "2026-09-24 over 60,000 M1 bars. About 5x bitcoin's cost"},
    "XRPUSD": {"round_trip_pct": 0.01641477,
               "note": "0.8207 % spread at the 90th percentile, doubled for slippage; measured "
                       "2026-09-24 over 60,000 M1 bars. A round trip costs 1.64 % of price - 31x "
                       "bitcoin - so an edge must clear that before anything is earned"},
    "default": {"round_trip_pct": 0.001, "note": "generic assumption for an unmeasured symbol"},
}

# Yahoo serves at most ~730 days of hourly bars, so the long ranges use daily bars.
BACKTEST_RANGES = {
    "2y": {"period": "729d", "interval": "1h", "hold_bars": 24,
           "label": "2 years - hourly bars (same timeframe as the live model)"},
    "5y": {"period": "5y", "interval": "1d", "hold_bars": 10,
           "label": "5 years - daily bars"},
    "10y": {"period": "10y", "interval": "1d", "hold_bars": 10,
            "label": "10 years - daily bars"},
}

ProgressFn = Optional[Callable[[str, int, str], None]]


def _report(progress: ProgressFn, stage: str, pct: int, message: str) -> None:
    if progress is not None:
        try:
            progress(stage, int(pct), message)
        except Exception:
            pass


def _iso(value) -> Optional[str]:
    if value is None:
        return None
    try:
        stamp = pd.Timestamp(value)
        if stamp.tzinfo is None:
            stamp = stamp.tz_localize("UTC")
        return stamp.tz_convert("UTC").strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(value)


def _positive_class_proba(model, X: pd.DataFrame) -> np.ndarray:
    classes = list(getattr(model, "classes_", []))
    if 1 not in classes or len(classes) < 2:
        return np.full(len(X), 0.5)
    return model.predict_proba(X)[:, classes.index(1)]


def _direction_for_probability(probability: float, buy_threshold: float, sell_threshold: float) -> int:
    """+1 / -1 / 0 from src/signal_engine.py's live rule (the dashboard's rule) with this run's thresholds."""
    from .signal_engine import BUY, SELL, classify_probability

    side = classify_probability(probability, {"buy_threshold": buy_threshold, "sell_threshold": sell_threshold})
    return 1 if side == BUY else (-1 if side == SELL else 0)


def backtest_signal_series(bars: pd.DataFrame, models: Optional[dict] = None, start: int = 0) -> pd.DataFrame:
    """Evaluation-path entry: the live engine (src/signal_engine.py, config "live") on every closed bar from ``start``.

    Bar i sees only bars 0..i, exactly what the dashboard sees when bar i is the newest closed bar, so a backtest
    built on this series trades the same signals as live for the same models.
    """
    from .signal_engine import predict_signal_series

    return predict_signal_series(bars, models, "live", start=start)


def _simulate_trades(features: pd.DataFrame, proba: np.ndarray, fold_of_row: np.ndarray, *,
                     buy_threshold: float, sell_threshold: float, hold_bars: int, cost_pct: float,
                     directions: Optional[np.ndarray] = None, invert: bool = False) -> list[dict]:
    """One position at a time, entered at the signal bar close, settled on the path.

    ``directions`` (+1 / -1 / 0 per row) are the engine's own signals; without them the side comes from ``proba``.
    ``invert`` trades every signal in the opposite direction with the same stop/target distances and costs: the
    inverse-direction baseline, which shows whether a result comes from the rules or from the market's direction.
    """
    closes = features["close"].to_numpy(dtype=float)
    highs = features["high"].to_numpy(dtype=float)
    lows = features["low"].to_numpy(dtype=float)
    vols = features["volatility_5d"].to_numpy(dtype=float)
    times = features["datetime"].to_numpy()
    n = len(features)
    trades: list[dict] = []
    busy_until = -1
    for i in range(n - 1):
        if i < busy_until or np.isnan(proba[i]):
            continue
        if directions is not None:
            direction = int(directions[i])
        else:
            direction = _direction_for_probability(proba[i], buy_threshold, sell_threshold)
        if direction == 0:
            continue
        if invert:
            direction = -direction
        entry = closes[i]
        if not np.isfinite(entry) or entry <= 0:
            continue
        atr = max(0.5, float(vols[i]) * entry) if np.isfinite(vols[i]) else max(0.5, entry * 0.005)
        stop = entry - direction * atr * STOP_ATR_MULT
        target = entry + direction * atr * TARGET_ATR_MULT
        exit_price = None
        outcome = "TIME"
        last = min(i + hold_bars, n - 1)
        j = i
        for j in range(i + 1, last + 1):
            if direction == 1:
                if lows[j] <= stop:
                    exit_price, outcome = stop, "SL"
                    break
                if highs[j] >= target:
                    exit_price, outcome = target, "TP"
                    break
            else:
                if highs[j] >= stop:
                    exit_price, outcome = stop, "SL"
                    break
                if lows[j] <= target:
                    exit_price, outcome = target, "TP"
                    break
        if exit_price is None:
            exit_price = closes[j]
        gross = direction * (exit_price - entry) / entry
        trades.append({
            "fold": int(fold_of_row[i]),
            "side": "BUY" if direction == 1 else "SELL",
            "entry_time": _iso(times[i]),
            "exit_time": _iso(times[j]),
            "entry": round(float(entry), 4),
            "exit": round(float(exit_price), 4),
            "outcome": outcome,
            "bars_held": int(j - i),
            "probability": round(float(proba[i]), 4),
            "gross_pct": round(gross * 100, 4),
            "net_pct": round((gross - cost_pct) * 100, 4),
            "r_multiple": round(direction * (exit_price - entry) / (atr * STOP_ATR_MULT), 3),
            # R after the round-trip cost, the unit the expectancy is reported in
            "net_r": round((direction * (exit_price - entry) - cost_pct * entry) / (atr * STOP_ATR_MULT), 4),
        })
        busy_until = j
    return trades


def summarize_trades(trades: list[dict], *, test_start, test_end, test_bars: int, bars_in_market: int) -> dict:
    """The agreed metric set, computed from net (after-cost) trade returns."""
    net = np.array([t["net_pct"] / 100.0 for t in trades], dtype=float)
    count = int(len(net))
    try:
        years = max(0.0, (pd.Timestamp(test_end) - pd.Timestamp(test_start)).days / 365.25)
    except Exception:
        years = 0.0
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for r in net:
        equity *= (1.0 + r)
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak if peak > 0 else 0.0)
    wins = net[net > 0]
    losses = net[net <= 0]
    trades_per_year = (count / years) if years > 0 else None
    longest_losing_streak = streak = 0
    for r in net:
        streak = streak + 1 if r <= 0 else 0
        longest_losing_streak = max(longest_losing_streak, streak)
    net_r = [t["net_r"] for t in trades if t.get("net_r") is not None]
    # A strategy with NO STOP has no risk unit, so it has no R - the MetaQuotes Moving Average sample
    # is exactly that (OrderSend(..., 0, 0, ...), exit only on the opposite signal). Demanding
    # r_multiple raised a KeyError on it; inventing an R for it would be worse. Absent means absent.
    gross_r = [t["r_multiple"] for t in trades if t.get("r_multiple") is not None]
    # Trades whose exit bar reached BOTH the stop and the target. A single bar cannot say which came first, so the
    # engine books the stop; that is the safe choice but it is a choice. Reported here with the expectancy the
    # strategy would have if every one of them had gone the other way instead - not a result, a bound. When the
    # bound and the measured expectancy sit on opposite sides of zero, the candidate has not been measured and
    # needs finer bars before it is judged. Measured 2026-09-19: about 2 % of trades at a 1R target, under 1 % at
    # 2R and above, on both gold and bitcoin at 15m/1h/4h.
    ambiguous = [t for t in trades if t.get("ambiguous_exit")]
    expectancy_r_bound = None
    if net_r and ambiguous:
        flipped = sum((t.get("target_r") or 0.0) - t["net_r"] for t in ambiguous
                      if t.get("net_r") is not None)
        expectancy_r_bound = round((float(np.sum(net_r)) + flipped) / len(net_r), 4)
    sharpe = sortino = None
    if count >= 2 and trades_per_year:
        std = float(np.std(net, ddof=1))
        if std > 0:
            sharpe = float(np.mean(net) / std * math.sqrt(trades_per_year))
        downside = net[net < 0]
        if len(downside) >= 2 and float(np.std(downside, ddof=1)) > 0:
            sortino = float(np.mean(net) / float(np.std(downside, ddof=1)) * math.sqrt(trades_per_year))
    cagr = None
    if years > 0 and equity > 0:
        cagr = (equity ** (1.0 / years) - 1.0) * 100
    return {
        "trades": count,
        "wins": int(len(wins)),
        "losses": int(len(losses)),
        "long_trades": sum(1 for t in trades if t["side"] == "BUY"),
        "short_trades": sum(1 for t in trades if t["side"] == "SELL"),
        "win_rate_pct": round(len(wins) / count * 100, 2) if count else None,
        "profit_factor": round(float(wins.sum()) / abs(float(losses.sum())), 3) if count and losses.sum() < 0 else None,
        "expectancy_pct": round(float(np.mean(net)) * 100, 4) if count else None,
        "avg_r": round(float(np.mean(gross_r)), 3) if gross_r else None,
        "expectancy_r": round(float(np.mean(net_r)), 4) if net_r else None,
        "longest_losing_streak": int(longest_losing_streak),
        "total_return_pct": round((equity - 1.0) * 100, 3),
        "cagr_pct": round(cagr, 3) if cagr is not None else None,
        "max_drawdown_pct": round(max_dd * 100, 3),
        "sharpe": round(sharpe, 3) if sharpe is not None else None,
        "sortino": round(sortino, 3) if sortino is not None else None,
        "exposure_pct": round(bars_in_market / test_bars * 100, 2) if test_bars else None,
        "years": round(years, 2),
        "trades_per_year": round(trades_per_year, 1) if trades_per_year else None,
        "sharpe_basis": "per-trade net returns annualised by trade frequency",
        # Spelled out because reading avg_r as an after-cost number overstated every strategy in this system
        # until 2026-09-19: it is the GROSS R. expectancy_r is the one to judge a candidate on.
        "ambiguous_exits": len(ambiguous),
        "ambiguous_exit_pct": round(len(ambiguous) / count * 100, 2) if count else None,
        "expectancy_r_bound": expectancy_r_bound,
        "avg_r_basis": "gross R, before spread and swap",
        "expectancy_r_basis": "R after spread and swap (None when trades carry no net_r)",
    }


def run_walkforward_backtest(symbol: str, range_key: str = "5y", *, buy_threshold: float = 0.55,
                             sell_threshold: float = 0.45, n_folds: int = 6, progress: ProgressFn = None,
                             data: Optional[pd.DataFrame] = None, signal_mode: str = "live_engine") -> dict:
    """``signal_mode``: "live_engine" (default) predicts every test bar with src.signal_engine.predict_signal in the
    live config, exactly as the dashboard does; "rf_proba" is the earlier direct RF-probability path, kept by flag."""
    from . import signal_engine

    started = time.monotonic()
    symbol = symbol.upper()
    if signal_mode not in SIGNAL_MODES:
        return {"available": False, "symbol": symbol, "range": range_key,
                "reason": f"unknown signal_mode '{signal_mode}'; choose one of {sorted(SIGNAL_MODES)}"}
    if range_key not in BACKTEST_RANGES:
        return {"available": False, "symbol": symbol, "range": range_key,
                "reason": f"unknown range '{range_key}'; choose one of {sorted(BACKTEST_RANGES)}"}
    spec = BACKTEST_RANGES[range_key]
    costs = BACKTEST_COSTS.get(symbol, BACKTEST_COSTS["default"])
    base = {
        "symbol": symbol,
        "range": range_key,
        "range_label": spec["label"],
        "interval": spec["interval"],
        "ticker": YAHOO_TICKERS.get(symbol, symbol),
        "ran_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
    }

    _report(progress, "data", 5, f"Fetching {spec['period']} of {spec['interval']} bars for {symbol}")
    source = "injected" if data is not None else "yahoo"
    raw = data if data is not None else fetch_yahoo_history(symbol, period=spec["period"], interval=spec["interval"])
    if raw is None or raw.empty:
        return {**base, "available": False, "data_source": source,
                "reason": "Yahoo returned no history for this range. Backtests never fall back to synthetic prices."}

    _report(progress, "features", 15, f"Building features on {len(raw)} bars")
    features = build_features(raw)
    missing = [c for c in FEATURE_COLUMNS if c not in features.columns]
    if missing:
        return {**base, "available": False, "data_source": source, "reason": f"feature columns missing: {missing}"}
    n = len(features)
    initial_train = max(MIN_TRAIN_ROWS, int(n * 0.5))
    if n - initial_train < n_folds * 20:
        return {**base, "available": False, "data_source": source, "bars": n,
                "reason": f"only {n} usable bars; need at least {MIN_TRAIN_ROWS + n_folds * 20} for {n_folds} folds"}

    test_positions = np.array_split(np.arange(initial_train, n), n_folds)
    proba = np.full(n, np.nan)
    directions = np.zeros(n, dtype=int) if signal_mode == "live_engine" else None
    engine_config = {"buy_threshold": buy_threshold, "sell_threshold": sell_threshold}
    fold_of_row = np.full(n, -1)
    by_fold = []
    for k, block in enumerate(test_positions):
        test_start_i, test_end_i = int(block[0]), int(block[-1])
        train_end_i = test_start_i - LABEL_HORIZON_BARS  # purge: labels look 3 bars ahead
        _report(progress, "train", 20 + int(60 * k / n_folds),
                f"Fold {k + 1}/{n_folds}: training on bars before {_iso(features['datetime'].iloc[test_start_i])}")
        train = features.iloc[:train_end_i]
        mask = train["quality_move"].astype(bool) if "quality_move" in train.columns else pd.Series(True, index=train.index)
        train_rows = train[mask] if int(mask.sum()) >= 80 else train
        fold_note = None
        model = None
        if train_rows["target"].nunique() < 2:
            fold_note = "training labels had one class; fold stood aside"
            proba_block = np.full(len(block), 0.5)
        else:
            model, _ = _fit_random_forest_with_optional_calibration(
                train_rows[FEATURE_COLUMNS], train_rows["target"], calibrate=True)
            proba_block = _positive_class_proba(model, features.iloc[test_start_i:test_end_i + 1][FEATURE_COLUMNS])
        if directions is not None:
            # The shared live engine on each closed test bar: bar i sees feature rows 0..i only.
            engine_models = {"rf": model, "lstm": None, "feature_columns": FEATURE_COLUMNS}
            for i in range(test_start_i, test_end_i + 1):
                prefix = features.iloc[: i + 1]
                result = signal_engine.predict_signal(prefix, engine_models, engine_config, features=prefix)
                proba_block[i - test_start_i] = result["probability"]
                directions[i] = 1 if result["signal"] == signal_engine.BUY else (-1 if result["signal"] == signal_engine.SELL else 0)
        proba[test_start_i:test_end_i + 1] = proba_block
        fold_of_row[test_start_i:test_end_i + 1] = k + 1
        test_targets = features["target"].iloc[test_start_i:test_end_i + 1].to_numpy()
        accuracy = float(np.mean((proba_block >= 0.5).astype(int) == test_targets)) if len(test_targets) else None
        by_fold.append({
            "fold": k + 1,
            "train_rows": int(len(train_rows)),
            "train_end": _iso(features["datetime"].iloc[train_end_i - 1]),
            "test_start": _iso(features["datetime"].iloc[test_start_i]),
            "test_end": _iso(features["datetime"].iloc[test_end_i]),
            "test_bars": int(len(block)),
            "oos_accuracy": round(accuracy, 4) if accuracy is not None else None,
            "note": fold_note,
        })

    _report(progress, "simulate", 85, "Simulating trades with stop/target path and costs")
    trades = _simulate_trades(features, proba, fold_of_row, buy_threshold=buy_threshold,
                              sell_threshold=sell_threshold, hold_bars=spec["hold_bars"],
                              cost_pct=costs["round_trip_pct"], directions=directions)
    inverse_trades = _simulate_trades(features, proba, fold_of_row, buy_threshold=buy_threshold,
                                      sell_threshold=sell_threshold, hold_bars=spec["hold_bars"],
                                      cost_pct=costs["round_trip_pct"], directions=directions, invert=True)
    for fold in by_fold:
        fold_trades = [t for t in trades if t["fold"] == fold["fold"]]
        compounded = 1.0
        for t in fold_trades:
            compounded *= 1 + t["net_pct"] / 100.0
        fold["trades"] = len(fold_trades)
        fold["net_return_pct"] = round((compounded - 1) * 100, 3)

    _report(progress, "metrics", 95, "Computing out-of-sample metrics")
    test_start = features["datetime"].iloc[initial_train]
    test_end = features["datetime"].iloc[-1]
    test_bars = n - initial_train
    bars_in_market = int(sum(t["bars_held"] for t in trades))
    metrics = summarize_trades(trades, test_start=test_start, test_end=test_end,
                               test_bars=test_bars, bars_in_market=bars_in_market)
    inverse_metrics = summarize_trades(inverse_trades, test_start=test_start, test_end=test_end, test_bars=test_bars,
                                       bars_in_market=int(sum(t["bars_held"] for t in inverse_trades)))
    test_targets = features["target"].iloc[initial_train:].to_numpy()
    metrics["oos_direction_accuracy"] = round(float(np.mean((proba[initial_train:] >= 0.5).astype(int) == test_targets)), 4)

    equity_curve = []
    equity = 1.0
    for t in trades:
        equity *= 1 + t["net_pct"] / 100.0
        equity_curve.append({"time": t["exit_time"], "equity": round(equity, 5)})
    if len(equity_curve) > 300:
        step = len(equity_curve) / 300.0
        equity_curve = [equity_curve[int(i * step)] for i in range(300)] + [equity_curve[-1]]

    first_close = float(features["close"].iloc[initial_train])
    last_close = float(features["close"].iloc[-1])
    trade_count = metrics["trades"]
    notes = [
        "Out-of-sample only: every probability comes from a model trained on earlier bars.",
        "Random forest only; the LSTM half of the live ensemble is not retrained per fold.",
    ]
    if signal_mode == "live_engine":
        notes.append("Signals come from src.signal_engine.predict_signal (live config) on every closed test bar, the same "
                     "function the dashboard uses; with no per-fold LSTM the blend is the RF probability alone.")
    if spec["interval"] != "1h":
        notes.append("Daily-bar ranges retrain the same features on daily bars, so they test the strategy logic rather than the exact hourly live model.")
    if symbol == "XAUUSD":
        notes.append("XAUUSD history uses the GC=F gold future, which runs about 1.4% away from broker spot.")

    # Same out-of-sample probabilities, different bands. Picking the best row from
    # this table is in-sample selection and must be confirmed on a later period.
    threshold_sensitivity = []
    for band in (0.52, 0.55, 0.58, 0.60, 0.65):
        band_trades = _simulate_trades(features, proba, fold_of_row, buy_threshold=band,
                                       sell_threshold=round(1 - band, 2), hold_bars=spec["hold_bars"],
                                       cost_pct=costs["round_trip_pct"])
        band_metrics = summarize_trades(band_trades, test_start=test_start, test_end=test_end, test_bars=test_bars,
                                        bars_in_market=int(sum(t["bars_held"] for t in band_trades)))
        threshold_sensitivity.append({
            "buy_at_or_above": band,
            "sell_at_or_below": round(1 - band, 2),
            "is_live_setting": abs(band - buy_threshold) < 1e-9,
            **{key: band_metrics[key] for key in ("trades", "long_trades", "short_trades", "win_rate_pct",
                                                  "profit_factor", "expectancy_pct", "max_drawdown_pct",
                                                  "total_return_pct")},
        })

    by_year: dict[str, dict] = {}
    for t in trades:
        year = str(t["exit_time"])[:4]
        bucket = by_year.setdefault(year, {"year": year, "trades": 0, "wins": 0, "equity": 1.0})
        bucket["trades"] += 1
        bucket["wins"] += 1 if t["net_pct"] > 0 else 0
        bucket["equity"] *= 1 + t["net_pct"] / 100.0
    yearly = [{
        "year": b["year"],
        "trades": b["trades"],
        "win_rate_pct": round(b["wins"] / b["trades"] * 100, 1) if b["trades"] else None,
        "net_return_pct": round((b["equity"] - 1) * 100, 2),
    } for b in sorted(by_year.values(), key=lambda item: item["year"])]

    _report(progress, "done", 100, "Backtest complete")
    return {
        **base,
        "available": True,
        "data_source": source,
        "signal_mode": signal_mode,
        "data_start": _iso(raw["datetime"].iloc[0]) if "datetime" in raw.columns else None,
        "data_end": _iso(raw["datetime"].iloc[-1]) if "datetime" in raw.columns else None,
        "bars": n,
        "test_start": _iso(test_start),
        "test_end": _iso(test_end),
        "test_bars": int(test_bars),
        "folds": n_folds,
        "purge_bars": LABEL_HORIZON_BARS,
        "hold_bars": spec["hold_bars"],
        "thresholds": {"buy": buy_threshold, "sell": sell_threshold},
        "costs": {"round_trip_pct": costs["round_trip_pct"], "note": costs["note"]},
        "seed": RANDOM_SEED,
        "feature_columns": list(FEATURE_COLUMNS),
        "model": "RandomForest fitted with src.train's calibrated helper, retrained per fold; never saved",
        "metrics": metrics,
        "benchmark": {"buy_and_hold_pct": round((last_close / first_close - 1) * 100, 3) if first_close else None},
        "evidence": "sufficient" if trade_count >= MIN_TRADES_FOR_EVIDENCE else "insufficient",
        "evidence_note": (f"{trade_count} trades in the test period"
                          + ("" if trade_count >= MIN_TRADES_FOR_EVIDENCE else f"; under {MIN_TRADES_FOR_EVIDENCE} is insufficient evidence, not a result")),
        "by_fold": by_fold,
        "inverse_baseline": {
            "metrics": inverse_metrics,
            "evidence": "sufficient" if inverse_metrics["trades"] >= MIN_TRADES_FOR_EVIDENCE else "insufficient",
            "note": "The same out-of-sample signals traded in the opposite direction, same stop/target distances and costs.",
        },
        "threshold_sensitivity": threshold_sensitivity,
        "by_year": yearly,
        "equity_curve": equity_curve,
        "recent_trades": trades[-25:],
        "notes": notes,
        "duration_sec": round(time.monotonic() - started, 1),
    }
