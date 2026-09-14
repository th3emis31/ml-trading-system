"""Paper trading (forward test) for the gold 4h multi-timeframe research model.

Research only: this module has no broker connection and never sends, modifies or
closes an order. It records what the model *would* do on live broker candles so the
forward result can be compared with the backtest holdout (XAUUSD 4h + daily context,
tight exits, xgb, trailing 90% EV threshold: holdout PF 1.29 on 192 trades,
2024-05-28 to 2026-09-11).

Demo trading: after a cycle, ``main`` hands a fresh BUY or SELL to the running app's
``/api/demo-model/execute`` and asks ``/api/demo-model/sync`` to close positions at the time
limit. Every order guard (enabled flag, demo account only, freshness, one position, stops
attached) lives in ``src/demo_executor.py`` inside the app; this module has no broker code.

Each cycle is safe to run as often as you like; it acts once per closed bar:

1. Reads broker 4h and daily candles through the running app (``/api/data/bars``)
   and drops every bar that has not closed yet.
2. Settles the open paper trade with the research's own ``triple_barrier_outcomes``
   (next-bar-open entry, gap-adjusted stop checked before target, time exit, costs).
3. Decides on the newest closed bar. BUY and SELL models are trained on bars that end
   ``PREDICTION_WINDOW + horizon`` bars before it, so the current bar and the trailing
   EV history are all out-of-sample, as in the walk-forward research. A trade is taken
   only when its EV clears the trailing quantile of the previous EVs and no paper trade
   is open; otherwise the decision is NO_TRADE. Every decision is logged with its numbers.

Run:  python -m src.paper_trader            one cycle on live broker candles
      python -m src.paper_trader --status   summary only
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

os.environ.setdefault("RESEARCH_JOBS", "2")  # small footprint next to the live app and MetaTrader

import numpy as np
import pandas as pd

from .edge_research import (INTERVAL_SPECS, _fit_predict, best_ev, load_research_frame, trailing_quantile,
                            triple_barrier_outcomes)
from .mtf_data import TIMEFRAMES
from .walkforward_backtest import BACKTEST_COSTS, _iso, summarize_trades

PAPER_SETUP = {
    "name": "xauusd_4h_mtf_xgb_tight_q90",
    "symbol": "XAUUSD", "interval": "4h", "htf": "1d", "config": "tight", "model": "xgb",
    "min_ev": 0.0, "quantile": 0.90,
    "places_orders": False,
    "research_reference": {"file": "data/research/combined_xauusd_4h_mtf_20260913-091542.json",
                           "holdout_period": "2024-05-28 to 2026-09-11", "trades": 192, "win_rate_pct": 47.4,
                           "profit_factor": 1.29, "max_drawdown_pct": 6.5, "total_return_pct": 20.3},
}
PAPER_DIR = Path("data") / "paper_trading"
PREDICTION_WINDOW = 240  # trailing EV window (180 bars on 4h) plus margin, all predicted out-of-sample
MIN_TRAIN_ROWS = 200
MAX_DECISIONS_KEPT = 2000


def _now() -> pd.Timestamp:
    return pd.Timestamp(datetime.now(timezone.utc))


def state_path(setup: dict = PAPER_SETUP) -> Path:
    return PAPER_DIR / f"{setup['name']}.json"


def new_state(setup: dict = PAPER_SETUP) -> dict:
    return {"setup": setup, "started_at": _iso(_now()), "last_run": None, "last_error": None, "last_message": None,
            "last_decision_bar": None, "decisions": [], "open_trade": None, "closed_trades": []}


def load_state(path: Optional[Path] = None) -> dict:
    path = Path(path or state_path())
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return new_state()


def save_state(state: dict, path: Optional[Path] = None) -> Path:
    path = Path(path or state_path(state["setup"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1, default=str), encoding="utf-8")
    os.replace(tmp, path)
    return path


def drop_forming_bars(frame: pd.DataFrame, minutes: int, now) -> pd.DataFrame:
    """Only bars whose close (open time + duration) is at or before ``now``."""
    if frame is None or frame.empty:
        return frame
    out = frame.copy()
    out["datetime"] = pd.to_datetime(out["datetime"], utc=True)
    out = out[out["datetime"] + pd.Timedelta(minutes=minutes) <= pd.Timestamp(now)].reset_index(drop=True)
    out.attrs = dict(frame.attrs)
    return out


def _outcome_so_far(df: pd.DataFrame, t: int, side: int, cfg: dict, last: int) -> Optional[dict]:
    """Exit of a trade signalled at bar ``t`` using closed bars up to ``last`` only.

    Runs the research's own ``triple_barrier_outcomes`` on bars t..last padded with flat
    copies of the last close. The pad cannot trigger a stop or target the real bars had not
    already hit, so an exit placed inside the pad simply means "still open".
    """
    window = df.iloc[t:last + 1][["datetime", "open", "high", "low", "close", "atr_14"]].reset_index(drop=True)
    last_close = float(df["close"].iloc[last])
    pad = pd.DataFrame({"datetime": [window["datetime"].iloc[-1]] * cfg["horizon"], "open": last_close,
                        "high": last_close, "low": last_close, "close": last_close, "atr_14": np.nan})
    path = triple_barrier_outcomes(pd.concat([window, pad], ignore_index=True), cfg["sl_atr"], cfg["tp_atr"],
                                   cfg["horizon"])[side]
    exit_rel = int(path["exit_idx"][0])
    if exit_rel < 0 or t + exit_rel > last:
        return None
    return {"exit_at": t + exit_rel, "hit": str(path["hit"][0]), "gross": float(path["gross"][0])}


def _settle_open_trade(state: dict, df: pd.DataFrame, last: int, cfg: dict, cost_pct: float) -> None:
    trade = state.get("open_trade")
    if not trade:
        return
    times = pd.to_datetime(df["datetime"], utc=True)
    matches = np.flatnonzero(times.map(_iso).to_numpy() == trade["signal_bar"])
    if len(matches) == 0:
        state["last_error"] = f"signal bar {trade['signal_bar']} is not in the bars returned"
        return
    t = int(matches[0])
    if t + 1 > last:
        return  # the entry bar has not closed yet
    side = 1 if trade["side"] == "BUY" else -1
    entry = float(df["open"].iloc[t + 1])
    atr = float(df["atr_14"].iloc[t])
    risk_price = cfg["sl_atr"] * atr
    trade.update({"entry_time": _iso(times.iloc[t + 1]), "entry_price": round(entry, 3),
                  "stop": round(entry - side * risk_price, 3), "target": round(entry + side * cfg["tp_atr"] * atr, 3)})
    result = _outcome_so_far(df, t, side, cfg, last)
    if result is None:
        close = float(df["close"].iloc[last])
        trade.update({"bars_held": int(last - t), "mark_price": round(close, 3),
                      "unrealized_net_pct": round((side * (close - entry) / entry - cost_pct) * 100, 4)})
        return
    gross = result["gross"]
    state["closed_trades"].append({
        "signal_bar": trade["signal_bar"], "side": trade["side"], "p_win": trade.get("p_win"),
        "ev_r": trade.get("ev_r"), "threshold_r": trade.get("threshold_r"),
        "entry_time": trade["entry_time"], "entry_price": trade["entry_price"], "stop": trade["stop"],
        "target": trade["target"], "exit_time": _iso(times.iloc[result["exit_at"]]), "outcome": result["hit"],
        "bars_held": int(result["exit_at"] - t), "gross_pct": round(gross * 100, 4),
        "net_pct": round((gross - cost_pct) * 100, 4), "r_multiple": round(gross * entry / risk_price, 3),
        "settled_at": state["last_run"],
    })
    state["open_trade"] = None


def _decide(state: dict, df: pd.DataFrame, feature_names: list, last: int, cfg: dict, spec: dict, setup: dict,
            cost_pct: float) -> dict:
    times = pd.to_datetime(df["datetime"], utc=True)
    bar_time = _iso(times.iloc[last])
    decision = {"bar_time": bar_time, "decided_at": state["last_run"], "close": round(float(df["close"].iloc[last]), 3)}
    X = df[feature_names].to_numpy(dtype=float)
    ok = np.isfinite(X).all(axis=1)
    if not ok[last]:
        return {**decision, "action": "NO_TRADE", "reason": "features not available for this bar"}

    pred_start = last - PREDICTION_WINDOW + 1
    train_end = pred_start - cfg["horizon"]  # purge: labels look `horizon` bars ahead
    pred_rows = np.arange(pred_start, last + 1)
    pred_rows = pred_rows[ok[pred_rows]]
    outcomes = triple_barrier_outcomes(df, cfg["sl_atr"], cfg["tp_atr"], cfg["horizon"])
    proba = {}
    train_count = 0
    for side in (1, -1):
        label = outcomes[side]["label"]
        train_rows = np.arange(0, max(0, train_end))
        train_rows = train_rows[ok[train_rows] & np.isfinite(label[train_rows])]
        if len(train_rows) < MIN_TRAIN_ROWS:
            return {**decision, "action": "NO_TRADE", "reason": f"only {len(train_rows)} training rows"}
        values = np.full(len(df), np.nan)
        values[pred_rows] = _fit_predict(setup["model"], X[train_rows], label[train_rows].astype(int), X[pred_rows])
        proba[side] = values
        train_count = int(len(train_rows))

    # The next bar's open is not known yet, so the newest bar's EV uses its close as the entry estimate.
    ev_history = best_ev(df, proba[1], proba[-1], cfg, cost_pct)
    entry_estimate = float(df["close"].iloc[last])
    atr = float(df["atr_14"].iloc[last])
    reward_risk = cfg["tp_atr"] / cfg["sl_atr"]
    cost_r = cost_pct * entry_estimate / (cfg["sl_atr"] * atr)
    p_buy, p_sell = float(proba[1][last]), float(proba[-1][last])
    ev_buy = p_buy * reward_risk - (1 - p_buy) - cost_r
    ev_sell = p_sell * reward_risk - (1 - p_sell) - cost_r
    ev_history[last] = max(ev_buy, ev_sell)
    side, ev, prob = (1, ev_buy, p_buy) if ev_buy >= ev_sell else (-1, ev_sell, p_sell)
    floor = float(setup.get("min_ev", 0.0))
    threshold = None
    if setup.get("quantile"):
        threshold = float(trailing_quantile(ev_history, spec["trailing_window"], setup["quantile"])[last])
    decision.update({"p_buy": round(p_buy, 4), "p_sell": round(p_sell, 4), "ev_buy_r": round(ev_buy, 3),
                     "ev_sell_r": round(ev_sell, 3),
                     "threshold_r": round(threshold, 3) if threshold is not None and np.isfinite(threshold) else None,
                     "train_rows": train_count, "train_end": _iso(times.iloc[train_end - 1])})
    if threshold is not None:
        if not np.isfinite(threshold):
            return {**decision, "action": "NO_TRADE", "reason": "not enough past EVs for the trailing threshold"}
        floor = max(floor, threshold)
    side_name = "BUY" if side == 1 else "SELL"
    if state.get("open_trade"):
        return {**decision, "action": "NO_TRADE",
                "reason": f"{side_name} EV {ev:.3f}R, but a paper trade is already open (one position at a time)"}
    if ev < floor:
        return {**decision, "action": "NO_TRADE", "reason": f"best EV {ev:.3f}R ({side_name}) is below {floor:.3f}R"}
    state["open_trade"] = {
        "signal_bar": bar_time, "side": side_name, "p_win": round(prob, 4), "ev_r": round(ev, 3),
        "threshold_r": round(floor, 3), "opened_at": state["last_run"],
        "entry_time_expected": _iso(times.iloc[last] + pd.Timedelta(minutes=spec["minutes"])),
        "entry_price_estimate": round(entry_estimate, 3),
        "stop_estimate": round(entry_estimate - side * cfg["sl_atr"] * atr, 3),
        "target_estimate": round(entry_estimate + side * cfg["tp_atr"] * atr, 3),
        "horizon_bars": cfg["horizon"],
        "symbol": setup["symbol"], "atr": round(atr, 4), "sl_atr": cfg["sl_atr"], "tp_atr": cfg["tp_atr"],
        "bar_minutes": spec["minutes"],
    }
    return {**decision, "action": side_name, "reason": f"{side_name} EV {ev:.3f}R >= {floor:.3f}R"}


def run_paper_cycle(state: dict, bars: pd.DataFrame, htf_bars: pd.DataFrame, now=None) -> dict:
    """Settle the open paper trade and decide once on the newest closed bar. No orders, ever."""
    setup = state["setup"]
    now = pd.Timestamp(now) if now is not None else _now()
    if now.tzinfo is None:
        now = now.tz_localize("UTC")
    state.update({"last_run": _iso(now), "last_error": None, "last_message": None})
    spec = INTERVAL_SPECS[setup["interval"]]
    cfg = next(c for c in spec["configs"] if c["name"] == setup["config"])
    cost_pct = BACKTEST_COSTS.get(setup["symbol"], BACKTEST_COSTS["default"])["round_trip_pct"]

    bars = drop_forming_bars(bars, spec["minutes"], now)
    htf = drop_forming_bars(htf_bars, TIMEFRAMES[setup["htf"]]["minutes"], now)
    if bars is None or bars.empty or htf is None or htf.empty:
        state["last_error"] = "no closed 4h or daily bars"
        return state
    df, feature_names, _, htf_used = load_research_frame(setup["symbol"], setup["interval"], mtf=True, data=bars,
                                                         htf_data={setup["htf"]: htf})
    if df is None or not htf_used or len(df) < PREDICTION_WINDOW + MIN_TRAIN_ROWS + cfg["horizon"]:
        state["last_error"] = f"not enough bars with daily context ({0 if df is None else len(df)})"
        return state
    last = len(df) - 1
    _settle_open_trade(state, df, last, cfg, cost_pct)
    bar_time = _iso(pd.Timestamp(df["datetime"].iloc[last]))
    if state.get("last_decision_bar") == bar_time:
        state["last_message"] = f"bar {bar_time} was already decided; waiting for the next 4h close"
        return state
    decision = _decide(state, df, feature_names, last, cfg, spec, setup, cost_pct)
    state["decisions"] = (state.get("decisions") or [])[-(MAX_DECISIONS_KEPT - 1):] + [decision]
    state["last_decision_bar"] = bar_time
    state["last_message"] = f"{bar_time}: {decision['action']} - {decision['reason']}"
    return state


def summarize(state: dict) -> dict:
    closed = state.get("closed_trades") or []
    decisions = state.get("decisions") or []
    metrics = {"trades": 0}
    if closed:
        metrics = summarize_trades(closed, test_start=state["started_at"], test_end=state.get("last_run") or state["started_at"],
                                   test_bars=len(decisions), bars_in_market=int(sum(t["bars_held"] for t in closed)))
    return {
        "setup": state["setup"]["name"], "places_orders": False, "started_at": state.get("started_at"),
        "last_run": state.get("last_run"), "last_error": state.get("last_error"), "last_message": state.get("last_message"),
        "bars_decided": len(decisions), "signals": sum(1 for d in decisions if d.get("action") in ("BUY", "SELL")),
        "open_trade": state.get("open_trade"), "closed_trades": len(closed), "metrics": metrics,
        "research_reference": state["setup"].get("research_reference"),
        "last_decision": decisions[-1] if decisions else None,
        "demo_execution": (state.get("demo_execution") or [])[-5:],
    }


def _post_app(path: str, payload: dict) -> dict:
    """POST JSON to the running app; errors come back as a dict instead of raising."""
    import urllib.error
    import urllib.request

    url = os.getenv("TRADING_APP_URL", "http://127.0.0.1:5000").rstrip("/") + path
    request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read().decode("utf-8"))
        except Exception:
            return {"event": "error", "reason": f"HTTP {exc.code} from {path}"}
    except Exception as exc:
        return {"event": "error", "reason": f"{path}: {exc}"}


def hand_to_demo_executor(state: dict, decided_now: bool) -> None:
    """Let the app close demo positions at the time limit, then pass it a BUY/SELL decided this run.

    The app's demo executor makes every trading decision (enabled, demo account, freshness,
    one position, stops); this only forwards the signal and records the answer.
    """
    log = list(state.get("demo_execution") or [])
    log.extend((_post_app("/api/demo-model/sync", {}) or {}).get("events") or [])
    trade = state.get("open_trade")
    if decided_now and trade and trade.get("signal_bar") == state.get("last_decision_bar") and trade.get("atr"):
        signal = {"symbol": trade.get("symbol") or state["setup"]["symbol"], "side": trade["side"],
                  "bar_time": trade["signal_bar"], "bar_minutes": trade.get("bar_minutes"),
                  "horizon_bars": trade.get("horizon_bars"), "atr": trade["atr"], "sl_atr": trade.get("sl_atr"),
                  "tp_atr": trade.get("tp_atr"), "p_win": trade.get("p_win"), "ev_r": trade.get("ev_r"),
                  "threshold_r": trade.get("threshold_r"), "setup": state["setup"]["name"]}
        event = _post_app("/api/demo-model/execute", signal)
        trade["demo_execution"] = event
        log.append(event)
    state["demo_execution"] = log[-200:]


def fetch_live_frames(setup: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Broker candles through the running app; no Yahoo fallback (GC=F prices do not match fills)."""
    from .mtf_data import APP_MAX_BARS, fetch_app_bars

    return (fetch_app_bars(setup["symbol"], setup["interval"], APP_MAX_BARS),
            fetch_app_bars(setup["symbol"], setup["htf"], APP_MAX_BARS))


def main(argv: Optional[list] = None) -> dict:
    parser = argparse.ArgumentParser(description="Paper-trade the gold 4h MTF research model (never places orders).")
    parser.add_argument("--status", action="store_true", help="print the summary without running a cycle")
    args = parser.parse_args(argv)
    state = load_state()
    if not args.status:
        bars, htf = fetch_live_frames(state["setup"])
        if bars.empty or htf.empty:
            state.update({"last_run": _iso(_now()),
                          "last_error": "broker candles unavailable - is app.py running with MT5 connected?"})
        else:
            decided_before = state.get("last_decision_bar")
            state = run_paper_cycle(state, bars, htf)
            hand_to_demo_executor(state, decided_now=state.get("last_decision_bar") != decided_before)
        save_state(state)
    summary = summarize(state)
    print(json.dumps(summary, indent=1, default=str))
    return summary


if __name__ == "__main__":
    main()
