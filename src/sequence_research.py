"""LSTM edge research on the same labels, folds and holdout as ``src/edge_research``.

Research only: nothing here writes ``models/`` or touches the live LSTM.

A recurrent network sees the last ``seq_len`` bars of the research features and
predicts two probabilities at once: that a BUY entered at the next open hits its
target before its stop, and the same for a SELL (triple-barrier labels). Trades,
costs, the EV rule, the validation-only selection and the single holdout pass
are exactly those of the tree research, so the two reports compare directly.

Leakage guards specific to sequences:
* the feature scaler is fitted on bars up to the end of each fold's training
  span only;
* early stopping uses the last slice of the training span, separated from the
  fitted part by the label horizon;
* training ends ``horizon`` bars before the test block (same purge as trees).
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd

from .data import fetch_yahoo_history
from .edge_research import (INTERVAL_SPECS, MIN_EV_GRID, MIN_VALIDATION_TRADES, RESEARCH_DIR, RESEARCH_FEATURES,
                            ProgressFn, _iso, _metrics, _report, build_research_features, evaluate_pass, simulate,
                            triple_barrier_outcomes, walk_forward_blocks)
from .walkforward_backtest import BACKTEST_COSTS, RANDOM_SEED

SEQ_LEN = {"1h": 48, "1d": 30}
DEFAULTS = {"hidden": 32, "epochs": 12, "patience": 3, "batch": 256, "lr": 1e-3, "dropout": 0.2, "val_fraction": 0.15}


def _train_predict(X: np.ndarray, y_buy: np.ndarray, y_sell: np.ndarray, train_idx: np.ndarray, test_idx: np.ndarray,
                   seq_len: int, horizon: int, cell: str, params: dict) -> tuple[np.ndarray, np.ndarray, dict]:
    import torch
    from torch import nn

    torch.manual_seed(RANDOM_SEED)
    np.random.seed(RANDOM_SEED)
    fit_end = int(train_idx.max()) + 1
    mean = np.nanmean(X[:fit_end], axis=0)
    std = np.nanstd(X[:fit_end], axis=0)
    std[std == 0] = 1.0
    Xs = np.clip((X - mean) / std, -5, 5).astype(np.float32)
    Xs = np.nan_to_num(Xs, nan=0.0)
    windows = np.lib.stride_tricks.sliding_window_view(Xs, seq_len, axis=0)  # (n-seq+1, F, seq)

    def batch(idx):
        return torch.from_numpy(np.ascontiguousarray(windows[idx - seq_len + 1].transpose(0, 2, 1)))

    labels = np.stack([y_buy, y_sell], axis=1).astype(np.float32)
    train_idx = train_idx[(train_idx >= seq_len - 1) & np.isfinite(labels[train_idx]).all(axis=1)]
    n_val = max(50, int(len(train_idx) * params["val_fraction"]))
    val_idx = train_idx[-n_val:]
    fit_idx = train_idx[: max(0, len(train_idx) - n_val - horizon)]
    test_idx = test_idx[test_idx >= seq_len - 1]
    info = {"fit_rows": int(len(fit_idx)), "val_rows": int(len(val_idx)), "test_rows": int(len(test_idx))}
    p_buy = np.full(len(X), np.nan)
    p_sell = np.full(len(X), np.nan)
    if len(fit_idx) < 300 or len(test_idx) == 0:
        info["skipped"] = "not enough rows"
        return p_buy, p_sell, info

    rnn_cls = nn.LSTM if cell == "lstm" else nn.GRU

    class Net(nn.Module):
        def __init__(self):
            super().__init__()
            self.rnn = rnn_cls(Xs.shape[1], params["hidden"], batch_first=True)
            self.drop = nn.Dropout(params["dropout"])
            self.head = nn.Linear(params["hidden"], 2)

        def forward(self, x):
            out, _ = self.rnn(x)
            return self.head(self.drop(out[:, -1, :]))

    net = Net()
    optimiser = torch.optim.Adam(net.parameters(), lr=params["lr"])
    loss_fn = nn.BCEWithLogitsLoss()
    rng = np.random.default_rng(RANDOM_SEED)
    y_val = torch.from_numpy(labels[val_idx])
    best_loss, best_state, bad_epochs, epochs_run = float("inf"), None, 0, 0
    for _ in range(params["epochs"]):
        epochs_run += 1
        net.train()
        order = rng.permutation(fit_idx)
        for start in range(0, len(order), params["batch"]):
            idx = order[start:start + params["batch"]]
            optimiser.zero_grad()
            loss = loss_fn(net(batch(idx)), torch.from_numpy(labels[idx]))
            loss.backward()
            optimiser.step()
        net.eval()
        with torch.no_grad():
            val_loss = float(loss_fn(net(batch(val_idx)), y_val))
        if val_loss < best_loss - 1e-4:
            best_loss, bad_epochs = val_loss, 0
            best_state = {k: v.detach().clone() for k, v in net.state_dict().items()}
        else:
            bad_epochs += 1
            if bad_epochs >= params["patience"]:
                break
    if best_state is not None:
        net.load_state_dict(best_state)
    net.eval()
    with torch.no_grad():
        probs = []
        for start in range(0, len(test_idx), 2048):
            probs.append(torch.sigmoid(net(batch(test_idx[start:start + 2048]))).numpy())
    probs = np.concatenate(probs)
    p_buy[test_idx] = probs[:, 0]
    p_sell[test_idx] = probs[:, 1]
    info.update({"epochs_run": epochs_run, "best_val_loss": round(best_loss, 5)})
    return p_buy, p_sell, info


def run_sequence_research(symbol: str, interval: str = "1h", *, cell: str = "lstm", n_folds: int = 8,
                          holdout_folds: int = 3, configs: Optional[list] = None, params: Optional[dict] = None,
                          progress: ProgressFn = None, data: Optional[pd.DataFrame] = None) -> dict:
    import torch

    torch.set_num_threads(max(1, min(8, torch.get_num_threads())))
    started = time.monotonic()
    symbol = symbol.upper()
    spec = INTERVAL_SPECS[interval]
    configs = configs or spec["configs"]
    params = {**DEFAULTS, **(params or {})}
    seq_len = params.get("seq_len") or SEQ_LEN[interval]
    cost_pct = BACKTEST_COSTS.get(symbol, BACKTEST_COSTS["default"])["round_trip_pct"]
    base = {"symbol": symbol, "interval": interval, "model": cell, "seq_len": seq_len, "params": params,
            "seed": RANDOM_SEED, "cost_round_trip_pct": cost_pct,
            "ran_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")}

    _report(progress, "data", 3, f"Fetching {symbol} {interval} history")
    raw = data if data is not None else fetch_yahoo_history(symbol, period=spec["period"], interval=interval)
    if raw is None or raw.empty:
        return {**base, "available": False, "reason": "No real history returned; research never uses synthetic prices."}
    if spec.get("max_years") and data is None:
        cutoff = pd.to_datetime(raw["datetime"], utc=True).max() - pd.Timedelta(days=365 * spec["max_years"])
        raw = raw[pd.to_datetime(raw["datetime"], utc=True) >= cutoff]

    df = build_research_features(raw, htf_bars=spec["htf_bars"])
    X = df[RESEARCH_FEATURES].to_numpy(dtype=float)
    feature_ok = np.isfinite(X).all(axis=1)
    valid_positions = np.flatnonzero(feature_ok)
    if len(valid_positions) < 800:
        return {**base, "available": False, "reason": f"only {len(valid_positions)} usable bars after warm-up"}
    first_valid = int(valid_positions[0])
    blocks = walk_forward_blocks(len(df), first_valid, n_folds)
    fold_of_row = np.zeros(len(df), dtype=int)
    for k, block in enumerate(blocks, start=1):
        fold_of_row[block] = k
    validation_ids = list(range(1, n_folds - holdout_folds + 1))
    holdout_ids = list(range(n_folds - holdout_folds + 1, n_folds + 1))

    predictions, outcomes_by_cfg, fold_info = {}, {}, []
    steps, step = len(configs) * len(blocks), 0
    for cfg in configs:
        outcomes = triple_barrier_outcomes(df, cfg["sl_atr"], cfg["tp_atr"], cfg["horizon"])
        outcomes_by_cfg[cfg["name"]] = outcomes
        p_buy_all = np.full(len(df), np.nan)
        p_sell_all = np.full(len(df), np.nan)
        for k, block in enumerate(blocks, start=1):
            step += 1
            _report(progress, "train", 5 + int(80 * step / steps), f"{cell} {cfg['name']} fold {k} of {len(blocks)}")
            train_end = int(block[0]) - cfg["horizon"]
            train_idx = np.arange(first_valid, max(first_valid, train_end))
            train_idx = train_idx[feature_ok[train_idx]]
            test_idx = block[feature_ok[block]]
            if len(train_idx) == 0:
                continue
            p_buy, p_sell, info = _train_predict(X, outcomes[1]["label"], outcomes[-1]["label"], train_idx, test_idx,
                                                 seq_len, cfg["horizon"], cell, params)
            mask = np.isfinite(p_buy)
            p_buy_all[mask] = p_buy[mask]
            p_sell_all[mask] = p_sell[mask]
            fold_info.append({"config": cfg["name"], "fold": k, "role": "validation" if k in validation_ids else "holdout",
                              "train_end_idx": int(train_end - 1), "test_start_idx": int(block[0]), **info})
        predictions[cfg["name"]] = (p_buy_all, p_sell_all)

    validation_rows = np.flatnonzero(np.isin(fold_of_row, validation_ids) & feature_ok)
    holdout_rows = np.flatnonzero(np.isin(fold_of_row, holdout_ids) & feature_ok)
    candidates = []
    for cfg in configs:
        p_buy, p_sell = predictions[cfg["name"]]
        for min_ev in MIN_EV_GRID:
            m = _metrics(df, simulate(df, outcomes_by_cfg[cfg["name"]], p_buy, p_sell, validation_rows, cfg, min_ev, cost_pct),
                         validation_rows)
            candidates.append({"config": cfg["name"], "model": cell, "min_ev": min_ev, "trades": m.get("trades"),
                               "long_trades": m.get("long_trades"), "short_trades": m.get("short_trades"),
                               "win_rate_pct": m.get("win_rate_pct"), "profit_factor": m.get("profit_factor"),
                               "expectancy_pct": m.get("expectancy_pct"), "max_drawdown_pct": m.get("max_drawdown_pct"),
                               "total_return_pct": m.get("total_return_pct")})

    ranked = sorted(candidates, key=lambda c: ((c["trades"] or 0) >= MIN_VALIDATION_TRADES,
                                               (c["total_return_pct"] or -999) > 0, c["profit_factor"] or 0.0,
                                               c["expectancy_pct"] or -999), reverse=True)
    chosen = ranked[0]
    cfg = next(c for c in configs if c["name"] == chosen["config"])
    p_buy, p_sell = predictions[cfg["name"]]
    holdout_trades = simulate(df, outcomes_by_cfg[cfg["name"]], p_buy, p_sell, holdout_rows, cfg, chosen["min_ev"], cost_pct)
    holdout_metrics = _metrics(df, holdout_trades, holdout_rows)
    _report(progress, "done", 100, "Sequence research complete")
    return {
        **base,
        "available": True,
        "bars": int(len(df)),
        "data_start": _iso(df["datetime"].iloc[0]),
        "data_end": _iso(df["datetime"].iloc[-1]),
        "features": list(RESEARCH_FEATURES),
        "configs": configs,
        "configurations_tried": len(candidates),
        "validation_folds": validation_ids,
        "holdout_folds": holdout_ids,
        "fold_training": fold_info,
        "selection": {"chosen": chosen, "top_validation": ranked[:8], "folds_used": validation_ids,
                      "validation_profitable": (chosen["trades"] or 0) >= MIN_VALIDATION_TRADES
                      and (chosen["total_return_pct"] or -1) > 0},
        "holdout": {"metrics": holdout_metrics, "verdict": evaluate_pass(holdout_metrics),
                    "recent_trades": holdout_trades[-20:]},
        "duration_sec": round(time.monotonic() - started, 1),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LSTM/GRU edge research on triple-barrier labels.")
    parser.add_argument("--symbols", nargs="+", default=["XAUUSD", "BTCUSD"])
    parser.add_argument("--interval", default="1h", choices=sorted(INTERVAL_SPECS))
    parser.add_argument("--cell", default="lstm", choices=["lstm", "gru"])
    args = parser.parse_args()
    for sym in args.symbols:
        report = run_sequence_research(sym, args.interval, cell=args.cell,
                                       progress=lambda s, p, m: print(f"[{p:3d}%] {m}", flush=True))
        RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        path = RESEARCH_DIR / f"sequence_{args.cell}_{sym.lower()}_{args.interval}_{stamp}.json"
        path.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
        if not report.get("available"):
            print(sym, "unavailable:", report.get("reason"))
            continue
        hold = report["holdout"]["metrics"]
        print(f"\n=== {sym} {args.interval} {args.cell} | saved {path}")
        print("chosen on validation:", report["selection"]["chosen"])
        print("HOLDOUT:", {k: hold.get(k) for k in ("period", "trades", "long_trades", "short_trades", "win_rate_pct",
                                                    "profit_factor", "expectancy_pct", "max_drawdown_pct",
                                                    "total_return_pct", "buy_and_hold_pct", "sharpe")})
        print("verdict:", report["holdout"]["verdict"])
