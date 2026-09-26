"""Paper forward tests of the gold CRT watchlist candidates: research only, never places orders.

Candidates (BASELINE.md, 15 Sep 2026):
- ``crt_ea_ema50_15m``: the MT4 CRT_Dashboard_EA entry as coded (src/crt_lab.py) on XAUUSD 15m, only when the M15 close
  is on the trade's side of EMA50, fixed 2R, no trailing (CRT round 2; failed later on unseen 2022-24 bars).
- ``crt_mss_d1_5m``: the owner's pictured model (src/crt_mss_lab.py) on XAUUSD 5m: previous server day's range swept,
  market structure shift, limit at the FVG's 50 %, target the other side of the range (passed search + validation,
  holdout -5.5R net).
Their backtest holdouts are spent, so only bars after each candidate's ``start_at`` (fixed on its first run) count.

Each run fetches fresh broker bars (15m through the app, 5m straight from MT5 with server time converted to UTC),
recomputes the trades since ``start_at`` with spread and swap, keeps closed trades that have left the fetched window,
and writes one state file per candidate. No bars -> ``available: false``; nothing is invented. Limit orders that have
not filled yet are not shown.

    python -m src.crt_forward [--status] [--candidate KEY]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import pandas as pd

from . import aurum_flow_lab          # registers the trendline-break order builder
from . import candle_pattern_lab      # registers the candlestick order builder
from . import sweep_reversal          # registers the sweep/manipulation-candle order builder
from . import crt_lab
from . import crt_mss_lab
from . import paper_trader
from . import strategy_lab as lab
from .walkforward_backtest import _iso, summarize_trades

SYMBOL = "XAUUSD"
STATE_PATH = lab.LAB_DIR / "crt_forward.json"
CANDIDATE = {"family": "crt", "params": {"symbol": SYMBOL, "filters": {"m15_trend": True}},
             "exits": dict(crt_lab.exit_variants(SYMBOL))["B_fixed_target"],
             "description": "CRT_Dashboard_EA entry + M15 EMA50 agrees, fixed 2R, no trailing (paper forward test)"}
_MSS_SPEC = next(v for v in crt_mss_lab.mss_variants(SYMBOL, "5m") if v["variant"] == "d1|opposite")
CANDIDATES = {
    "crt_ea_ema50_15m": {"spec": CANDIDATE, "timeframe": "15m", "bars": 3000, "htf_bars": 600, "source": "app",
                         "state": STATE_PATH},
    "crt_mss_d1_5m": {"spec": {**_MSS_SPEC, "description": "Previous-day CRT sweep + MSS + FVG 50% limit, target other side "
                                                           "of the day range, gold 5m (paper forward test)"},
                      "timeframe": "5m", "bars": 4000, "htf_bars": 0, "source": "mt5",
                      "state": lab.LAB_DIR / "crt_forward_mss_d1_5m.json"},
}
# Added 19 Sep 2026: the two candidates the research left at the 0.95 bar's doorstep. Their backtest
# holdouts are spent, so the only honest way for either to clear the bar is forward trades that nobody
# fitted anything to. Both place NOTHING - this module never sends an order - and both are declared
# once here so no trial-counting penalty applies, which is what killed them in the search.
_SCALED_BTC = next(v for v in aurum_flow_lab.scaled_variants("BTCUSD", "4h", 30)
                   if v["variant"] == "atr4.45/8.9|ma600")
_MORNING_STAR = next(v for v in candle_pattern_lab.pattern_variants("XAUUSD", "4h")
                     if v["variant"] == "morning_star|rr1")
CANDIDATES["trendline_break_btc_4h"] = {
    "spec": {**_SCALED_BTC,
             "description": "Trendline break (two closed bars beyond the ray through the two extreme swings of "
                            "200 bars), SMA600 filter, stop 4.45 ATR / target 8.90 ATR, BTCUSD 4h "
                            "(paper forward test; BASELINE 19 Sep: all three splits positive, PF 1.474, "
                            "deflated Sharpe 0.7504 against the 0.95 bar)"},
    "symbol": "BTCUSD", "timeframe": "4h", "bars": 3000, "htf_bars": 0, "source": "app",
    "state": lab.LAB_DIR / "forward_trendline_break_btc_4h.json"}
CANDIDATES["morning_star_xau_4h"] = {
    "spec": {**_MORNING_STAR,
             "description": "Morning star, entry at the next open, stop 1.0 ATR and target 1 R, XAUUSD 4h "
                            "(paper forward test; BASELINE 19 Sep: all three splits positive, PF 1.386, "
                            "60.4 % win rate, beats its own inverse at PF 0.555)"},
    "symbol": "XAUUSD", "timeframe": "4h", "bars": 3000, "htf_bars": 0, "source": "app",
    "state": lab.LAB_DIR / "forward_morning_star_xau_4h.json"}

# Added 19 Sep 2026, after the owner corrected my reading of their own photographs: the manipulation-candle rule
# is a CONTINUATION, not a fade, and read that way it is the first candidate to come out positive in all three
# windows. Declared once in src/sweep_reversal.py, paper only, places nothing.
CANDIDATES["sweep_continue_xau_4h"] = {
    "spec": {**sweep_reversal.FORWARD_CANDIDATE,
             "description": sweep_reversal.FORWARD_CANDIDATE["description"]
                            + " (paper forward test; BASELINE 19 Sep: all three splits positive, holdout PF 1.378 "
                              "over 118 trades, +0.2193 R after costs, beats its own inverse by 62 points, "
                              "deflated Sharpe 0.181 against the 0.95 bar)"},
    "symbol": "XAUUSD", "timeframe": "4h", "bars": 3000, "htf_bars": 0, "source": "app",
    "state": lab.LAB_DIR / "forward_sweep_continue_xau_4h.json"}

CRITERIA = {"min_trades": 30, "min_profit_factor": 1.2, "max_drawdown_pct": 20.0}

Fetch = Callable[[str, str, int], pd.DataFrame]


def _fetch(source: str) -> Fetch:
    from .mtf_data import fetch_app_bars, fetch_mt5_bars

    return fetch_mt5_bars if source == "mt5" else fetch_app_bars


def progress_verdict(summary: dict) -> dict:
    """collecting until CRITERIA['min_trades'] forward trades, then passed / failed on PF, return and drawdown."""
    trades = int(summary.get("trades") or 0)
    if trades < CRITERIA["min_trades"]:
        return {"status": "collecting", "trades": trades, "needed": CRITERIA["min_trades"]}
    pf = summary.get("profit_factor")
    checks = {"profit_factor": pf is not None and float(pf) >= CRITERIA["min_profit_factor"],
              "positive_return": float(summary.get("total_return_pct") or 0) > 0,
              "max_drawdown": float(summary.get("max_drawdown_pct") or 0) <= CRITERIA["max_drawdown_pct"]}
    return {"status": "passed" if all(checks.values()) else "failed", "trades": trades, "checks": checks}


def run_once(fetch: Optional[Fetch] = None, now=None, path: Optional[Path] = None, key: str = "crt_ea_ema50_15m") -> dict:
    candidate = CANDIDATES[key]
    spec, timeframe = candidate["spec"], candidate["timeframe"]
    # Candidates may name their own market; the two CRT ones predate this and stay on XAUUSD.
    symbol = candidate.get("symbol", SYMBOL)
    path = Path(path or candidate["state"])
    fetch = fetch or _fetch(candidate["source"])
    now = pd.Timestamp(now) if now is not None else pd.Timestamp.now(tz="UTC")
    if now.tzinfo is None:
        now = now.tz_localize("UTC")
    state = paper_trader.load_state(path) if path.exists() else {}
    state.update({"candidate": spec["description"], "candidate_key": key, "symbol": symbol, "timeframe": timeframe,
                  "places_orders": False, "last_run": _iso(now)})
    bars = fetch(symbol, timeframe, candidate["bars"])
    bars4h = fetch(symbol, "4h", candidate["htf_bars"]) if candidate["htf_bars"] else None
    missing_htf = candidate["htf_bars"] and (bars4h is None or bars4h.empty)
    if bars is None or bars.empty or missing_htf or len(bars) < lab.WARMUP_BARS + 500:
        state.update({"available": False, "reason": f"no or too few broker bars ({candidate['source']})"})
        paper_trader.save_state(state, path)
        return state

    start_at = pd.Timestamp(state.get("start_at") or _iso(now), tz="UTC")
    state["start_at"] = _iso(start_at)
    market = lab.Market(symbol, timeframe, bars, now=now, swap=True)
    ind = market.ind
    if bars4h is not None:
        ind.htf_bars = bars4h
    orders = lab.strategy_orders(ind, spec)
    side, stop, target = orders[:3]
    entry_prices = orders[3] if len(orders) > 3 else None
    times = ind.times
    stamps = times.dt.tz_convert(None).to_numpy(dtype="datetime64[ns]")
    n = len(ind.c)

    def position(ts: pd.Timestamp) -> int:
        return int(np.searchsorted(stamps, ts.tz_convert(None).to_datetime64()))

    previous = {t["entry_time"]: t for t in state.get("closed_trades") or []}
    window_start = _iso(times.iloc[lab.WARMUP_BARS])
    first_row = max(position(start_at), lab.WARMUP_BARS)
    kept_exits = [t["exit_time"] for t in previous.values() if t["entry_time"] < window_start]
    if kept_exits:   # one position at a time across runs: never open before a kept trade has closed
        first_row = max(first_row, position(pd.Timestamp(max(kept_exits), tz="UTC")))
    rows = np.arange(first_row, n)
    trades = lab.simulate_orders(ind.o, ind.h, ind.l, ind.c, ind.atr(14), times, side, stop, target, rows,
                                 spec["exits"], market.cost_pct, market.holding, entry_prices)
    risk_r = crt_lab.r_stats(trades, stop)
    for t in trades:
        risk = abs(t["entry_price"] - stop[t["entry_idx"]])
        t["net_r"] = round(t["net_pct"] / 100.0 * t["entry_price"] / risk, 3) if risk > 0 else 0.0
        t["stop"], t["target"] = round(float(stop[t["entry_idx"]]), 3), round(float(target[t["entry_idx"]]), 3)
    fresh = {t["entry_time"]: {k: v for k, v in t.items() if k != "entry_idx"} for t in trades}
    merged = {k: v for k, v in previous.items() if k < window_start}
    merged.update(fresh)
    closed = sorted(merged.values(), key=lambda t: t["entry_time"])

    free_from = position(pd.Timestamp(closed[-1]["exit_time"], tz="UTC")) if closed else first_row
    open_position = None
    for t in rows[side[rows] != 0]:
        t = int(t)
        if t < free_from:
            continue
        label = "BUY" if side[t] == 1 else "SELL"
        if t + 1 < n:
            fill = entry_prices[t] if entry_prices is not None and np.isfinite(entry_prices[t]) else ind.o[t + 1]
            open_position = {"side": label, "entry_time": _iso(times.iloc[t + 1]), "entry_price": round(float(fill), 3),
                             "stop": round(float(stop[t]), 3), "target": round(float(target[t]), 3),
                             "last_close": round(float(ind.c[-1]), 3)}
        else:
            open_position = {"side": label, "status": "signal on the last closed bar, entry at the next open",
                             "stop": round(float(stop[t]), 3), "target": round(float(target[t]), 3)}
        break

    if closed:
        span_bars = max(1, int((now - start_at) / pd.Timedelta(minutes=lab.TIMEFRAME_MINUTES[timeframe])))
        metrics = summarize_trades(closed, test_start=start_at, test_end=now, test_bars=span_bars,
                                   bars_in_market=int(sum(t["bars_held"] for t in closed)))
        summary = {k: metrics.get(k) for k in lab.SUMMARY_KEYS}
        summary["net_r"] = round(float(sum(t.get("net_r", 0.0) for t in closed)), 2)
    else:
        summary = {"trades": 0, "net_r": 0.0}
    state.update({"available": True, "reason": None, "data_source": bars.attrs.get("source"), "data_end": _iso(times.iloc[-1]),
                  "closed_trades": closed, "open_position": open_position, "summary": summary,
                  "verdict": progress_verdict(summary), "this_window_r": risk_r, "cost_model": market.info["cost_model"]})
    paper_trader.save_state(state, path)
    return state


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Paper forward tests of the gold CRT candidates (never places orders).")
    parser.add_argument("--status", action="store_true", help="print the saved states without fetching")
    parser.add_argument("--candidate", choices=sorted(CANDIDATES), default=None, help="only this candidate")
    args = parser.parse_args(argv)
    keys = [args.candidate] if args.candidate else list(CANDIDATES)
    out = {}
    for key in keys:
        path = Path(CANDIDATES[key]["state"])
        if args.status:
            state = paper_trader.load_state(path) if path.exists() else {}
        else:
            try:
                state = run_once(key=key)
            except Exception as exc:   # one candidate failing must not stop the other
                state = {"available": False, "reason": f"run failed: {exc}"}
        out[key] = {k: state.get(k) for k in ("last_run", "available", "reason", "start_at", "data_end", "summary",
                                              "verdict", "open_position")}
    print(json.dumps(out, indent=1, default=str))


if __name__ == "__main__":
    # Wrapped so this loop cannot finish without a record - see loop_ledger.run_main.
    from .loop_ledger import run_main

    run_main("crt_forward", main)
