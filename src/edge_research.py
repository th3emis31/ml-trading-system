"""Edge research: can a model find profitable BUY *and* SELL trades out of sample?

Research only. Nothing here writes ``models/`` or touches the live signal path.

Why this exists
---------------
The live model learns "is the close higher 3 bars from now?" but trades are
judged by whether a target is hit before a stop over up to 24 bars. On gold
that mismatch produced 1,311 long-only trades and -60.9% over two years. This
module trains on what a trade actually experiences:

* **Triple-barrier labels, per side.** For each bar, would a BUY entered at the
  next bar's open hit ``tp_atr x ATR`` before ``sl_atr x ATR`` within
  ``horizon`` bars? And separately, would a SELL? Stops that gap through are
  filled at the gap open. Time exits count as not-a-win.
* **Stationary, leak-free features.** Returns, volatility and its regime,
  position in the recent range, distance from trend EMAs, higher-timeframe
  trend, candle shape, breakouts, RSI, volume and session. Every feature at bar
  *t* uses bars up to *t* only; there are no raw price levels.
* **Expected value in R.** ``EV = p x (tp/sl) - (1 - p) - cost_R``. A trade is
  taken on the side with the larger EV, only when it clears ``min_ev``.

How it avoids fooling itself
----------------------------
* Walk-forward folds; each trains only on bars ending ``horizon`` bars before
  its test window (purge), so no training label overlaps a test bar.
* Configuration (barrier set x model x ``min_ev``) is chosen on the
  **validation folds only**. The chosen configuration is then applied once to
  the **holdout folds**, which play no part in selection. Only the holdout is a
  result.
* Costs from ``BACKTEST_COSTS`` on every trade; fixed seeds; the full list of
  tried configurations is reported so the amount of searching is visible.
* The holdout is compared with buy & hold, a trend-following rule and
  always-long using the same exits and costs, against ``PASS_CRITERIA``.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import pandas as pd

from .data import fetch_yahoo_history
from .walkforward_backtest import (BACKTEST_COSTS, MIN_TRADES_FOR_EVIDENCE, RANDOM_SEED, _iso, _report,
                                   summarize_trades)

RESEARCH_DIR = Path("data") / "research"

INTERVAL_SPECS = {
    # 15m and 4h come from broker candles through the app (src.mtf_data); Yahoo has no 4h
    # bars and only ~60 days of 15m. "htf" lists the higher timeframes joined with --mtf.
    "15m": {"period": None, "minutes": 15, "htf_bars": 1920, "trailing_window": 2880, "htf": ["1h", "4h", "1d"], "configs": [
        {"name": "tight", "sl_atr": 1.0, "tp_atr": 1.5, "horizon": 24},
        {"name": "balanced", "sl_atr": 1.0, "tp_atr": 2.0, "horizon": 48},
        {"name": "wide", "sl_atr": 1.5, "tp_atr": 3.0, "horizon": 96},
    ]},
    "4h": {"period": None, "minutes": 240, "htf_bars": 120, "trailing_window": 180, "htf": ["1d"], "configs": [
        {"name": "tight", "sl_atr": 1.0, "tp_atr": 1.5, "horizon": 6},
        {"name": "balanced", "sl_atr": 1.0, "tp_atr": 2.0, "horizon": 12},
        {"name": "wide", "sl_atr": 1.5, "tp_atr": 3.0, "horizon": 24},
    ]},
    "1h": {"period": "729d", "minutes": 60, "htf": ["4h", "1d"], "htf_bars": 480, "trailing_window": 720, "configs": [
        {"name": "tight", "sl_atr": 1.0, "tp_atr": 1.5, "horizon": 24},
        {"name": "balanced", "sl_atr": 1.0, "tp_atr": 2.0, "horizon": 48},
        {"name": "wide", "sl_atr": 1.5, "tp_atr": 3.0, "horizon": 72},
    ]},
    "1d": {"period": "max", "minutes": 1440, "htf": [], "htf_bars": 60, "max_years": 12, "trailing_window": 250, "configs": [
        {"name": "tight", "sl_atr": 1.0, "tp_atr": 1.5, "horizon": 5},
        {"name": "balanced", "sl_atr": 1.0, "tp_atr": 2.0, "horizon": 10},
        {"name": "wide", "sl_atr": 1.5, "tp_atr": 3.0, "horizon": 20},
    ]},
}
MIN_EV_GRID = (0.05, 0.10, 0.20, 0.30)
# This machine has ~7.4 GB RAM shared with the live app, MetaTrader terminals and a
# browser; a full queue using every core was stopped by the system for low memory.
# Override with the RESEARCH_JOBS environment variable (e.g. 1 for random-forest pieces:
# each training thread holds its own working buffers).
RESEARCH_JOBS = max(1, int(os.getenv("RESEARCH_JOBS", "4")))
# R7a: take a trade only when its EV is in the top share of the EVs seen over the
# previous `trailing_window` bars. A fixed EV floor stopped firing when predicted
# probabilities drifted in the holdout (10 and 6 hourly trades); a trailing quantile
# adapts using past bars only.
QUANTILE_GRID = (0.90, 0.95, 0.98)
MODEL_NAMES = ("logit", "rf", "xgb")
MIN_VALIDATION_TRADES = 30
HOLDOUT_KEYS = ("period", "trades", "long_trades", "short_trades", "win_rate_pct", "profit_factor", "expectancy_pct",
                "avg_r", "max_drawdown_pct", "sharpe", "total_return_pct", "buy_and_hold_pct")
PASS_CRITERIA = {"min_profit_factor": 1.2, "min_trades": MIN_TRADES_FOR_EVIDENCE, "max_drawdown_pct": 20.0,
                 "must_beat_buy_and_hold": True}

RESEARCH_FEATURES = [
    "ret_1", "ret_3", "ret_6", "ret_12", "ret_24", "ret_72",
    "vol_24", "vol_120", "vol_ratio", "atr_pct", "atr_z_500",
    "range_pos_50", "range_pos_200", "dist_ema_50", "dist_ema_200", "ema50_slope_10", "trend_up", "htf_ret",
    "rsi_14", "rsi_14_chg_5", "body_frac", "upper_wick", "lower_wick",
    "breakout_up_20", "breakout_dn_20", "volume_z_120",
    "hour_sin", "hour_cos", "weekday_sin", "weekday_cos", "session_london", "session_newyork", "session_asia",
]

ProgressFn = Optional[Callable[[str, int, str], None]]
# _report and _iso come from src.walkforward_backtest (one definition for both engines).


# --------------------------------------------------------------------------- features
def build_research_features(raw: pd.DataFrame, htf_bars: int = 480) -> pd.DataFrame:
    """Stationary features; the value at bar t depends only on bars 0..t."""
    df = raw.copy().sort_values("datetime").reset_index(drop=True)
    open_, high, low, close = (df[c].astype(float) for c in ("open", "high", "low", "close"))
    log_close = np.log(close.where(close > 0))
    for n in (1, 3, 6, 12, 24, 72):
        df[f"ret_{n}"] = log_close.diff(n)
    df["vol_24"] = df["ret_1"].rolling(24).std()
    df["vol_120"] = df["ret_1"].rolling(120).std()
    df["vol_ratio"] = df["vol_24"] / df["vol_120"].replace(0, np.nan)

    prev_close = close.shift(1)
    true_range = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    df["atr_14"] = true_range.rolling(14).mean()
    df["atr_pct"] = df["atr_14"] / close
    atr_mean = df["atr_pct"].rolling(500, min_periods=200).mean()
    atr_std = df["atr_pct"].rolling(500, min_periods=200).std()
    df["atr_z_500"] = (df["atr_pct"] - atr_mean) / atr_std.replace(0, np.nan)

    for n in (50, 200):
        highest = high.rolling(n).max()
        lowest = low.rolling(n).min()
        df[f"range_pos_{n}"] = (close - lowest) / (highest - lowest).replace(0, np.nan)
    ema50 = close.ewm(span=50, adjust=False).mean()
    ema200 = close.ewm(span=200, adjust=False).mean()
    df["dist_ema_50"] = close / ema50 - 1
    df["dist_ema_200"] = close / ema200 - 1
    df["ema50_slope_10"] = ema50 / ema50.shift(10) - 1
    df["trend_up"] = (ema50 > ema200).astype(float)
    df["htf_ret"] = close / close.shift(htf_bars) - 1

    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    df["rsi_14"] = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
    df["rsi_14_chg_5"] = df["rsi_14"].diff(5)

    bar_range = (high - low).replace(0, np.nan)
    df["body_frac"] = (close - open_) / bar_range
    df["upper_wick"] = (high - np.maximum(close, open_)) / bar_range
    df["lower_wick"] = (np.minimum(close, open_) - low) / bar_range
    df["breakout_up_20"] = (close > high.shift(1).rolling(20).max()).astype(float)
    df["breakout_dn_20"] = (close < low.shift(1).rolling(20).min()).astype(float)

    volume = df["volume"].astype(float) if "volume" in df.columns else pd.Series(0.0, index=df.index)
    vol_mean = volume.rolling(120).mean()
    vol_std = volume.rolling(120).std()
    df["volume_z_120"] = ((volume - vol_mean) / vol_std.replace(0, np.nan)).fillna(0.0)

    stamps = pd.to_datetime(df["datetime"], utc=True, errors="coerce")
    hour = stamps.dt.hour.astype(float)
    weekday = stamps.dt.weekday.astype(float)
    df["hour_sin"] = np.sin(2 * np.pi * hour / 24.0)
    df["hour_cos"] = np.cos(2 * np.pi * hour / 24.0)
    df["weekday_sin"] = np.sin(2 * np.pi * weekday / 7.0)
    df["weekday_cos"] = np.cos(2 * np.pi * weekday / 7.0)
    df["session_london"] = ((hour >= 7) & (hour <= 16)).astype(float)
    df["session_newyork"] = ((hour >= 12) & (hour <= 21)).astype(float)
    df["session_asia"] = ((hour >= 0) & (hour <= 8)).astype(float)
    df = df.replace([np.inf, -np.inf], np.nan)
    return df


# --------------------------------------------------------------------------- labels
def triple_barrier_outcomes(df: pd.DataFrame, sl_atr: float, tp_atr: float, horizon: int) -> dict:
    """For each bar t and each side: outcome of a trade entered at open[t+1].

    Uses bars t+1..t+horizon only. Rows without a full horizon ahead are left
    undefined (label NaN) rather than being guessed.
    """
    o = df["open"].to_numpy(dtype=float)
    h = df["high"].to_numpy(dtype=float)
    lo = df["low"].to_numpy(dtype=float)
    c = df["close"].to_numpy(dtype=float)
    atr = df["atr_14"].to_numpy(dtype=float)
    n = len(df)
    result = {}
    for side in (1, -1):
        label = np.full(n, np.nan)
        gross = np.full(n, np.nan)
        exit_idx = np.full(n, -1, dtype=int)
        hit = np.full(n, "", dtype=object)
        for t in range(n - 1):
            if t + horizon > n - 1 or not np.isfinite(atr[t]) or atr[t] <= 0:
                continue
            entry = o[t + 1]
            if not np.isfinite(entry) or entry <= 0:
                continue
            stop = entry - side * sl_atr * atr[t]
            target = entry + side * tp_atr * atr[t]
            price, idx, outcome = c[t + horizon], t + horizon, "TIME"
            for j in range(t + 1, t + horizon + 1):
                if side == 1:
                    if j > t + 1 and o[j] <= stop:
                        price, idx, outcome = o[j], j, "SL"
                        break
                    if lo[j] <= stop:
                        price, idx, outcome = stop, j, "SL"
                        break
                    if h[j] >= target:
                        price, idx, outcome = target, j, "TP"
                        break
                else:
                    if j > t + 1 and o[j] >= stop:
                        price, idx, outcome = o[j], j, "SL"
                        break
                    if h[j] >= stop:
                        price, idx, outcome = stop, j, "SL"
                        break
                    if lo[j] <= target:
                        price, idx, outcome = target, j, "TP"
                        break
            label[t] = 1.0 if outcome == "TP" else 0.0
            gross[t] = side * (price - entry) / entry
            exit_idx[t] = idx
            hit[t] = outcome
        result[side] = {"label": label, "gross": gross, "exit_idx": exit_idx, "hit": hit}
    return result


# --------------------------------------------------------------------------- models
def _make_model(name: str):
    from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    if name == "logit":
        return make_pipeline(StandardScaler(), LogisticRegression(C=0.1, max_iter=1000))
    if name == "rf":
        return RandomForestClassifier(n_estimators=300, min_samples_leaf=40, max_features="sqrt",
                                      n_jobs=RESEARCH_JOBS, random_state=RANDOM_SEED)
    if name == "xgb":
        try:
            from xgboost import XGBClassifier

            return XGBClassifier(n_estimators=300, max_depth=4, learning_rate=0.05, subsample=0.8,
                                 colsample_bytree=0.8, min_child_weight=20, reg_lambda=1.0, tree_method="hist",
                                 n_jobs=RESEARCH_JOBS, random_state=RANDOM_SEED, eval_metric="logloss", verbosity=0)
        except Exception:
            return HistGradientBoostingClassifier(max_depth=4, learning_rate=0.05, max_iter=300,
                                                  l2_regularization=1.0, random_state=RANDOM_SEED)
    raise ValueError(f"unknown model {name}")


def _fit_predict(name: str, X_train, y_train, X_test) -> np.ndarray:
    if len(np.unique(y_train)) < 2:
        return np.full(len(X_test), float(np.mean(y_train)) if len(y_train) else 0.0)
    model = _make_model(name)
    model.fit(X_train, y_train)
    classes = list(getattr(model, "classes_", [0, 1]))
    return model.predict_proba(X_test)[:, classes.index(1)]


# --------------------------------------------------------------------------- simulation
def simulate(df: pd.DataFrame, outcomes: dict, p_buy: np.ndarray, p_sell: np.ndarray, rows: np.ndarray, cfg: dict,
             min_ev: float, cost_pct: float, side_rule: Optional[Callable[[int], int]] = None,
             threshold: Optional[np.ndarray] = None) -> list[dict]:
    """One position at a time over ``rows`` (ascending bar indices).

    ``side_rule`` replaces the model for baselines: it returns +1 / -1 / 0 for a bar.
    """
    o = df["open"].to_numpy(dtype=float)
    atr = df["atr_14"].to_numpy(dtype=float)
    times = df["datetime"].to_numpy()
    reward_risk = cfg["tp_atr"] / cfg["sl_atr"]
    trades: list[dict] = []
    busy_until = -1
    for t in rows:
        if t < busy_until or t + 1 >= len(df):
            continue
        entry = o[t + 1]
        if not np.isfinite(entry) or not np.isfinite(atr[t]) or atr[t] <= 0:
            continue
        risk_price = cfg["sl_atr"] * atr[t]
        cost_r = cost_pct * entry / risk_price
        if side_rule is None:
            if not (np.isfinite(p_buy[t]) and np.isfinite(p_sell[t])):
                continue
            ev_buy = p_buy[t] * reward_risk - (1 - p_buy[t]) - cost_r
            ev_sell = p_sell[t] * reward_risk - (1 - p_sell[t]) - cost_r
            side, ev, prob = (1, ev_buy, p_buy[t]) if ev_buy >= ev_sell else (-1, ev_sell, p_sell[t])
            floor = min_ev
            if threshold is not None:
                if not np.isfinite(threshold[t]):
                    continue  # not enough past EVs yet to know what "top share" means
                floor = max(min_ev, float(threshold[t]))
            if ev < floor:
                continue
        else:
            side = side_rule(t)
            if side == 0:
                continue
            ev, prob = None, None
        path = outcomes[side]
        exit_at = int(path["exit_idx"][t])
        if exit_at < 0 or not np.isfinite(path["gross"][t]):
            continue
        gross = float(path["gross"][t])
        trades.append({
            "entry_idx": int(t),
            "side": "BUY" if side == 1 else "SELL",
            "entry_time": _iso(times[t + 1]),
            "exit_time": _iso(times[exit_at]),
            "outcome": path["hit"][t],
            "bars_held": int(exit_at - t),
            "p_win": round(float(prob), 4) if prob is not None else None,
            "ev_r": round(float(ev), 3) if ev is not None else None,
            "gross_pct": round(gross * 100, 4),
            "net_pct": round((gross - cost_pct) * 100, 4),
            "r_multiple": round(gross * entry / risk_price, 3),
        })
        busy_until = exit_at
    return trades


def fold_net_returns(trades: list[dict], fold_of_row: np.ndarray, fold_ids: list) -> dict:
    """Compounded net return (%) per fold, keyed by fold id; folds without trades are 0."""
    growth = {k: 1.0 for k in fold_ids}
    for trade in trades:
        fold = int(fold_of_row[trade["entry_idx"]])
        if fold in growth:
            growth[fold] *= 1 + trade["net_pct"] / 100.0
    return {k: round((v - 1) * 100, 3) for k, v in growth.items()}


def best_ev(df: pd.DataFrame, p_buy: np.ndarray, p_sell: np.ndarray, cfg: dict, cost_pct: float) -> np.ndarray:
    """Larger of the BUY and SELL expected values per bar, in R after costs (NaN without predictions)."""
    entry = df["open"].shift(-1).to_numpy(dtype=float)
    atr = df["atr_14"].to_numpy(dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        cost_r = cost_pct * entry / (cfg["sl_atr"] * atr)
    reward_risk = cfg["tp_atr"] / cfg["sl_atr"]
    ev_buy = p_buy * reward_risk - (1 - p_buy) - cost_r
    ev_sell = p_sell * reward_risk - (1 - p_sell) - cost_r
    return np.fmax(ev_buy, ev_sell)


def trailing_quantile(values: np.ndarray, window: int, quantile: float) -> np.ndarray:
    """Quantile of the previous ``window`` values, excluding the current bar (no look-ahead)."""
    series = pd.Series(values, dtype=float).shift(1)
    return series.rolling(window, min_periods=max(20, window // 4)).quantile(quantile).to_numpy()


def _metrics(df: pd.DataFrame, trades: list[dict], rows: np.ndarray) -> dict:
    if len(rows) == 0:
        return {"trades": 0}
    start, end = df["datetime"].iloc[int(rows[0])], df["datetime"].iloc[int(rows[-1])]
    metrics = summarize_trades(trades, test_start=start, test_end=end, test_bars=int(len(rows)),
                               bars_in_market=int(sum(t["bars_held"] for t in trades)))
    first, last = float(df["close"].iloc[int(rows[0])]), float(df["close"].iloc[int(rows[-1])])
    metrics["buy_and_hold_pct"] = round((last / first - 1) * 100, 3) if first else None
    metrics["period"] = f"{_iso(start)} to {_iso(end)}"
    return metrics


def evaluate_pass(metrics: dict) -> dict:
    checks = {
        "profit_factor": (metrics.get("profit_factor") or 0) >= PASS_CRITERIA["min_profit_factor"],
        "trades": (metrics.get("trades") or 0) >= PASS_CRITERIA["min_trades"],
        "max_drawdown": (metrics.get("max_drawdown_pct") if metrics.get("max_drawdown_pct") is not None else 999)
        <= PASS_CRITERIA["max_drawdown_pct"],
        "beats_buy_and_hold": (metrics.get("total_return_pct") or -999) > (metrics.get("buy_and_hold_pct") or 0),
    }
    # numpy comparisons yield numpy bools, which the JSON report wrote as the string "True".
    checks = {name: bool(value) for name, value in checks.items()}
    return {"passed": all(checks.values()), "checks": checks, "criteria": PASS_CRITERIA}


def rank_candidates(candidates: list[dict], validation_ids: list, selection: str = "best") -> list[dict]:
    """Order configurations by validation results only (holdout never enters here).

    ``best``: enough trades, positive return, profit factor, expectancy.
    ``robust``: additionally requires profit in at least 60% of validation folds.
    Shared by full runs and by ``src.research_combine`` so both pick identically.
    """
    def score(c):
        return ((c["trades"] or 0) >= MIN_VALIDATION_TRADES, (c["total_return_pct"] or -999) > 0,
                c["profit_factor"] or 0.0, c["expectancy_pct"] or -999)

    def robust_score(c):
        needed = int(np.ceil(0.6 * len(validation_ids)))
        return ((c["trades"] or 0) >= MIN_VALIDATION_TRADES, (c.get("folds_profitable") or 0) >= needed,
                c.get("folds_profitable") or 0, (c["total_return_pct"] or -999) > 0, c["profit_factor"] or 0.0)

    return sorted(candidates, key=robust_score if selection == "robust" else score, reverse=True)


def candidate_key(candidate: dict) -> str:
    return f"{candidate['config']}|{candidate['model']}|{candidate['min_ev']}|{candidate.get('quantile')}"


def walk_forward_blocks(n_rows: int, first_valid: int, n_folds: int, initial_fraction: float = 0.4) -> list:
    """Test blocks after an initial training span. Shared by the tree and sequence
    research so both are scored on exactly the same periods."""
    initial = first_valid + int((n_rows - first_valid) * initial_fraction)
    return np.array_split(np.arange(initial, n_rows), n_folds)


# --------------------------------------------------------------------------- main entry
def load_research_frame(symbol: str, interval: str, *, source: str = "yahoo", mtf: bool = False,
                        data: Optional[pd.DataFrame] = None, htf_data: Optional[dict] = None,
                        progress: ProgressFn = None, rocket: bool = False):
    """History plus leak-free research features, and closed-bar higher-timeframe context with ``mtf``.

    Returns ``(df, feature_names, data_source, htf_used)``; ``df`` is None when no real history came
    back. Shared by edge research and meta-labelling so both see identical bars and features.
    """
    spec = INTERVAL_SPECS[interval]
    if interval in ("15m", "4h") and source == "yahoo":
        source = "auto"  # broker candles through the app first; Yahoo has no 4h and little 15m history
    if data is not None:
        raw = data
        data_source = data.attrs.get("source", "injected")
    elif source == "yahoo":
        raw = fetch_yahoo_history(symbol, period=spec["period"], interval=interval)
        data_source = f"yahoo:{interval}"
    else:
        from .mtf_data import load_bars

        raw = load_bars(symbol, interval, source=source)
        data_source = raw.attrs.get("source", source) if raw is not None else source
    if raw is None or raw.empty:
        return None, [], data_source, []
    if spec.get("max_years") and data is None:
        cutoff = pd.to_datetime(raw["datetime"], utc=True).max() - pd.Timedelta(days=365 * spec["max_years"])
        raw = raw[pd.to_datetime(raw["datetime"], utc=True) >= cutoff]

    _report(progress, "features", 8, "Building leak-free research features")
    df = build_research_features(raw, htf_bars=spec["htf_bars"])
    feature_names = list(RESEARCH_FEATURES)
    htf_used = []
    if mtf and spec.get("htf"):
        # Higher-timeframe context from bars that had closed when each bar closed, plus
        # "does this timeframe's trend agree with the higher one" flags.
        from .mtf_data import TIMEFRAMES as TF_SPECS, add_htf_features, load_bars as load_htf_bars

        frames = {}
        for htf in spec["htf"]:
            htf_frame = (htf_data or {}).get(htf)
            if htf_frame is None and data is None:
                htf_frame = load_htf_bars(symbol, htf, source=source)
            if htf_frame is not None and not htf_frame.empty:
                frames[f"h_{htf}"] = (htf_frame, TF_SPECS[htf]["minutes"])
                htf_used.append({"timeframe": htf, "source": htf_frame.attrs.get("source"), "bars": int(len(htf_frame))})
        if frames:
            df, htf_columns = add_htf_features(df, spec["minutes"], frames)
            feature_names += htf_columns
            for prefix in frames:
                align = f"align_{prefix}"
                df[align] = (df["trend_up"] == df[f"{prefix}_trend_up"]).astype(float)
                feature_names.append(align)
    if rocket:
        # Random convolution kernels over past bars: the sequence model that replaces the LSTM (src/rocket_features.py).
        from .rocket_features import rocket_features

        rocket_frame, rocket_columns = rocket_features(df)
        df = pd.concat([df, rocket_frame], axis=1)
        feature_names += rocket_columns
    return df, feature_names, data_source, htf_used


def run_edge_research(symbol: str, interval: str = "1h", *, n_folds: int = 8, holdout_folds: int = 3,
                      models: tuple = MODEL_NAMES, configs: Optional[list] = None, progress: ProgressFn = None,
                      data: Optional[pd.DataFrame] = None, selection: str = "best", source: str = "yahoo",
                      mtf: bool = False, htf_data: Optional[dict] = None, rocket: bool = False) -> dict:
    """``selection="best"`` ranks by aggregate validation result (R1/R7a behaviour).
    ``selection="robust"`` first requires profit in at least 60% of the validation folds
    separately, so a configuration carried by one lucky fold is not chosen."""
    started = time.monotonic()
    symbol = symbol.upper()
    spec = INTERVAL_SPECS[interval]
    configs = configs or spec["configs"]
    cost_pct = BACKTEST_COSTS.get(symbol, BACKTEST_COSTS["default"])["round_trip_pct"]
    base = {"symbol": symbol, "interval": interval, "ran_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "seed": RANDOM_SEED, "cost_round_trip_pct": cost_pct}

    _report(progress, "data", 3, f"Fetching {symbol} {interval} history")
    df, feature_names, data_source, htf_used = load_research_frame(symbol, interval, source=source, mtf=mtf, data=data,
                                                                   htf_data=htf_data, progress=progress, rocket=rocket)
    if df is None:
        return {**base, "available": False, "reason": "No real history returned; research never uses synthetic prices."}
    base["data_source"] = data_source
    X_all = df[feature_names].to_numpy(dtype=float)
    feature_ok = np.isfinite(X_all).all(axis=1)
    valid_positions = np.flatnonzero(feature_ok)
    if len(valid_positions) < 800:
        return {**base, "available": False, "reason": f"only {len(valid_positions)} usable bars after warm-up"}
    first_valid = int(valid_positions[0])
    blocks = walk_forward_blocks(len(df), first_valid, n_folds)
    validation_ids = list(range(1, n_folds - holdout_folds + 1))
    holdout_ids = list(range(n_folds - holdout_folds + 1, n_folds + 1))

    folds_meta = []
    predictions = {}  # (config_name, model, side) -> proba array
    fold_of_row = np.zeros(len(df), dtype=int)
    for k, block in enumerate(blocks, start=1):
        fold_of_row[block] = k

    total_steps = len(configs) * len(blocks) * len(models)
    step = 0
    outcomes_by_cfg = {}
    for cfg in configs:
        _report(progress, "labels", 10, f"Labelling trades: {cfg['name']} (SL {cfg['sl_atr']} ATR, TP {cfg['tp_atr']} ATR, {cfg['horizon']} bars)")
        outcomes = triple_barrier_outcomes(df, cfg["sl_atr"], cfg["tp_atr"], cfg["horizon"])
        outcomes_by_cfg[cfg["name"]] = outcomes
        for model_name in models:
            for side in (1, -1):
                predictions[(cfg["name"], model_name, side)] = np.full(len(df), np.nan)
        for k, block in enumerate(blocks, start=1):
            test_start = int(block[0])
            train_end = test_start - cfg["horizon"]  # purge: labels look `horizon` bars ahead
            train_rows = np.arange(first_valid, max(first_valid, train_end))
            train_rows = train_rows[feature_ok[train_rows]]
            test_rows = block[feature_ok[block]]
            if cfg is configs[0]:
                folds_meta.append({
                    "fold": k, "role": "validation" if k in validation_ids else "holdout",
                    "train_end": _iso(df["datetime"].iloc[train_end - 1]) if train_end - 1 >= 0 else None,
                    "test_start": _iso(df["datetime"].iloc[test_start]),
                    "test_end": _iso(df["datetime"].iloc[int(block[-1])]),
                    "train_end_idx": int(train_end - 1), "test_start_idx": test_start, "test_rows": int(len(test_rows)),
                })
            for model_name in models:
                step += 1
                _report(progress, "train", 12 + int(70 * step / max(1, total_steps)),
                        f"{cfg['name']} / {model_name} / fold {k} of {len(blocks)}")
                for side in (1, -1):
                    y = outcomes[side]["label"]
                    usable = train_rows[np.isfinite(y[train_rows])]
                    if len(usable) < 200 or len(test_rows) == 0:
                        continue
                    proba = _fit_predict(model_name, X_all[usable], y[usable].astype(int), X_all[test_rows])
                    predictions[(cfg["name"], model_name, side)][test_rows] = proba

    _report(progress, "select", 85, "Choosing the configuration on validation folds only")
    validation_rows = np.flatnonzero(np.isin(fold_of_row, validation_ids) & feature_ok)
    holdout_rows = np.flatnonzero(np.isin(fold_of_row, holdout_ids) & feature_ok)
    candidates = []
    candidate_holdouts: dict[str, dict] = {}
    for cfg in configs:
        outcomes = outcomes_by_cfg[cfg["name"]]
        for model_name in models:
            p_buy = predictions[(cfg["name"], model_name, 1)]
            p_sell = predictions[(cfg["name"], model_name, -1)]
            ev_best = best_ev(df, p_buy, p_sell, cfg, cost_pct)
            rules = [(min_ev, None) for min_ev in MIN_EV_GRID] + [(0.0, q) for q in QUANTILE_GRID]
            for min_ev, quantile in rules:
                threshold = trailing_quantile(ev_best, spec["trailing_window"], quantile) if quantile else None
                trades = simulate(df, outcomes, p_buy, p_sell, validation_rows, cfg, min_ev, cost_pct, threshold=threshold)
                m = _metrics(df, trades, validation_rows)
                fold_returns = fold_net_returns(trades, fold_of_row, validation_ids)
                # Holdout result per candidate, kept apart from selection so that runs done one
                # model/config at a time can be combined later without re-running anything.
                holdout_candidate = _metrics(df, simulate(df, outcomes, p_buy, p_sell, holdout_rows, cfg, min_ev, cost_pct,
                                                          threshold=threshold), holdout_rows)
                candidate_holdouts[f"{cfg['name']}|{model_name}|{min_ev}|{quantile}"] = {
                    key: holdout_candidate.get(key) for key in HOLDOUT_KEYS}
                candidates.append({"config": cfg["name"], "model": model_name, "min_ev": min_ev, "quantile": quantile,
                                   "folds_profitable": sum(1 for value in fold_returns.values() if value > 0),
                                   "fold_returns": fold_returns,
                                   "trades": m.get("trades"), "long_trades": m.get("long_trades"),
                                   "short_trades": m.get("short_trades"), "win_rate_pct": m.get("win_rate_pct"),
                                   "profit_factor": m.get("profit_factor"), "expectancy_pct": m.get("expectancy_pct"),
                                   "max_drawdown_pct": m.get("max_drawdown_pct"), "total_return_pct": m.get("total_return_pct")})

    ranked = rank_candidates(candidates, validation_ids, selection)
    chosen = ranked[0]
    chosen_cfg = next(cfg for cfg in configs if cfg["name"] == chosen["config"])
    validation_profitable = (chosen["trades"] or 0) >= MIN_VALIDATION_TRADES and (chosen["total_return_pct"] or -1) > 0

    _report(progress, "holdout", 92, "Applying the chosen configuration once to the untouched holdout")
    outcomes = outcomes_by_cfg[chosen_cfg["name"]]
    chosen_p_buy = predictions[(chosen_cfg["name"], chosen["model"], 1)]
    chosen_p_sell = predictions[(chosen_cfg["name"], chosen["model"], -1)]
    chosen_threshold = None
    if chosen.get("quantile"):
        chosen_threshold = trailing_quantile(best_ev(df, chosen_p_buy, chosen_p_sell, chosen_cfg, cost_pct),
                                             spec["trailing_window"], chosen["quantile"])
    holdout_trades = simulate(df, outcomes, chosen_p_buy, chosen_p_sell, holdout_rows, chosen_cfg,
                              chosen["min_ev"], cost_pct, threshold=chosen_threshold)
    holdout_metrics = _metrics(df, holdout_trades, holdout_rows)

    trend_up = df["trend_up"].to_numpy(dtype=float)
    baselines = {
        "trend_following": _metrics(df, simulate(df, outcomes, None, None, holdout_rows, chosen_cfg, 0.0, cost_pct,
                                                 side_rule=lambda t: 1 if trend_up[t] > 0.5 else -1), holdout_rows),
        "always_long": _metrics(df, simulate(df, outcomes, None, None, holdout_rows, chosen_cfg, 0.0, cost_pct,
                                             side_rule=lambda t: 1), holdout_rows),
    }
    for name in baselines:
        baselines[name] = {key: baselines[name].get(key) for key in
                           ("trades", "win_rate_pct", "profit_factor", "total_return_pct", "max_drawdown_pct")}
    baseline_keys = ("trades", "win_rate_pct", "profit_factor", "total_return_pct", "max_drawdown_pct")
    baselines_by_config = {}
    for cfg_item in configs:
        cfg_outcomes = outcomes_by_cfg[cfg_item["name"]]
        trend = _metrics(df, simulate(df, cfg_outcomes, None, None, holdout_rows, cfg_item, 0.0, cost_pct,
                                      side_rule=lambda t: 1 if trend_up[t] > 0.5 else -1), holdout_rows)
        long_only = _metrics(df, simulate(df, cfg_outcomes, None, None, holdout_rows, cfg_item, 0.0, cost_pct,
                                          side_rule=lambda t: 1), holdout_rows)
        baselines_by_config[cfg_item["name"]] = {
            "trend_following": {key: trend.get(key) for key in baseline_keys},
            "always_long": {key: long_only.get(key) for key in baseline_keys},
        }

    verdict = evaluate_pass(holdout_metrics)
    _report(progress, "done", 100, "Research run complete")
    return {
        **base,
        "available": True,
        "bars": int(len(df)),
        "data_start": _iso(df["datetime"].iloc[0]),
        "data_end": _iso(df["datetime"].iloc[-1]),
        "features": feature_names,
        "mtf": bool(mtf),
        "rocket": bool(rocket),
        "htf_used": htf_used,
        "configs": configs,
        "models": list(models),
        "min_ev_grid": list(MIN_EV_GRID),
        "quantile_grid": list(QUANTILE_GRID),
        "trailing_window": spec["trailing_window"],
        "configurations_tried": len(candidates),
        "selection_method": selection,
        "folds": folds_meta,
        "validation_folds": validation_ids,
        "holdout_folds": holdout_ids,
        "selection": {"chosen": chosen, "validation_profitable": validation_profitable,
                      "top_validation": ranked[:8], "folds_used": validation_ids},
        "holdout": {"metrics": holdout_metrics, "verdict": verdict, "recent_trades": holdout_trades[-20:]},
        "baselines_holdout": baselines,
        "baselines_by_config": baselines_by_config,
        "candidates_all": candidates,
        "candidate_holdouts": candidate_holdouts,
        "duration_sec": round(time.monotonic() - started, 1),
    }


def save_report(report: dict, suffix: str = "") -> Path:
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = RESEARCH_DIR / f"edge_{report['symbol'].lower()}_{report['interval']}{suffix}_{stamp}.json"
    path.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    return path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Walk-forward edge research with a final holdout.")
    parser.add_argument("--symbols", nargs="+", default=["XAUUSD", "BTCUSD"])
    parser.add_argument("--interval", default="1h", choices=sorted(INTERVAL_SPECS))
    parser.add_argument("--selection", default="best", choices=["best", "robust"])
    parser.add_argument("--source", default="yahoo", choices=["yahoo", "auto", "app"],
                        help="history source; 15m and 4h always use auto (broker bars via the app, then Yahoo)")
    parser.add_argument("--mtf", action="store_true", help="join higher-timeframe context and alignment features")
    parser.add_argument("--rocket", action="store_true",
                        help="add ROCKET random-convolution sequence features (the LSTM replacement, src/rocket_features.py)")
    parser.add_argument("--models", nargs="+", choices=list(MODEL_NAMES), default=list(MODEL_NAMES),
                        help="run a subset (one at a time saves memory; combine with python -m src.research_combine)")
    parser.add_argument("--configs", nargs="+", default=None, help="subset of label setups, e.g. tight balanced wide")
    args = parser.parse_args()
    chosen_configs = None
    if args.configs:
        chosen_configs = [c for c in INTERVAL_SPECS[args.interval]["configs"] if c["name"] in args.configs]
    piece = ""
    if chosen_configs or tuple(args.models) != MODEL_NAMES:
        piece = f"_piece-{'-'.join(args.models)}-{'-'.join(c['name'] for c in (chosen_configs or INTERVAL_SPECS[args.interval]['configs']))}"
        piece += "-mtf" if args.mtf else ""
        piece += "-rocket" if args.rocket else ""
    elif args.rocket:
        piece = "_rocket"
    for sym in args.symbols:
        report = run_edge_research(sym, args.interval, selection=args.selection, source=args.source, mtf=args.mtf,
                                   models=tuple(args.models), configs=chosen_configs, rocket=args.rocket,
                                   progress=lambda s, p, m: print(f"[{p:3d}%] {m}", flush=True))
        path = save_report(report, suffix=piece)
        if not report.get("available"):
            print(sym, "unavailable:", report.get("reason"))
            continue
        chosen = report["selection"]["chosen"]
        hold = report["holdout"]["metrics"]
        print(f"\n=== {sym} {args.interval} | saved {path}")
        print("chosen on validation:", chosen)
        print("HOLDOUT:", {k: hold.get(k) for k in ("period", "trades", "long_trades", "short_trades", "win_rate_pct",
                                                    "profit_factor", "expectancy_pct", "max_drawdown_pct",
                                                    "total_return_pct", "buy_and_hold_pct", "sharpe")})
        print("verdict:", report["holdout"]["verdict"])
        print("baselines:", report["baselines_holdout"])
