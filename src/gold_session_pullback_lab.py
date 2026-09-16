"""Backtest of the gold session pullback (strategies/gold_session_pullback.md), research only - never trades.

Rules are the ones declared in the strategy document before any test, plus the owner's decisions of 16 Sep 2026:
flat at 21:00 UTC, at most one open position, the declared rejection candle, and the news filter run both ways
(without it, and with the tier-1 event window applied on history from data/historical_events.csv).

- Trend: EMA50 vs EMA200 on the last H4 bar (resampled from broker H1) that closed at or before the signal bar's close.
- Signal bar t (H1, opens 07:00-19:00 UTC): pullback to EMA20 plus a rejection candle; fill at the open of bar t+1.
- Stop: 5-bar swing -/+ 1.5 x ATR14; skip when the risk R is below 0.5 or above 4 x ATR14.
- Exits: half at 1.5R and the stop moved to break-even for the rest, the rest at 3R, everything flat at 21:00 UTC.
  The stop is checked before a target when one bar touches both (conservative).
- Risk: 0.5 % of equity per trade, one position at a time, at most 2 entries per UTC day, no new entry once the day's
  closed result is -3 %, stop trading after a 15 % drawdown.
- Costs: the Strategy Lab round-trip spread + slippage for XAUUSD; no swap because every trade is flat by 21:00 UTC.
- Split: the Strategy Lab's locked XAUUSD 1h boundaries; development and holdout are reported separately and nothing
  is chosen on the holdout. Baseline: the same signal bars traded in the opposite direction.

Run: python -m src.gold_session_pullback_lab run
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from .event_defence import entries_blocked_mask, load_historical_events, volatility_breaker_mask
from .features import compute_atr, compute_ema
from .mtf_data import resample_bars
from .runtime_paths import smartentry_data_dir

SYMBOL = "XAUUSD"
RULES = {
    "ema_trend_fast": 50, "ema_trend_slow": 200, "ema_pullback": 20, "atr_len": 14,
    "signal_open_hours_utc": (7, 19), "flat_hour_utc": 21,
    "rejection_close_fraction": 2 / 3, "rejection_wick_fraction": 0.5, "min_range_atr": 0.3,
    "swing_bars": 5, "stop_atr": 1.5, "min_r_atr": 0.5, "max_r_atr": 4.0,
    "tp1_r": 1.5, "tp1_fraction": 0.5, "tp2_r": 3.0,
    "risk_fraction": 0.005, "max_entries_per_day": 2, "daily_loss_limit": 0.03, "halt_drawdown": 0.15,
}
VARIANTS = {
    "no_event_filter": {"tier1": False, "breaker": False},
    "tier1_event_filter": {"tier1": True, "breaker": False},
    "tier1_plus_breaker": {"tier1": True, "breaker": True},
}
SUCCESS = {"min_expectancy_r": 0.2, "min_trades": 100, "max_drawdown_pct": 10.0, "min_deflated_sharpe": 0.95}
MIN_H4_BARS = 200


def results_path() -> Path:
    return smartentry_data_dir() / "strategy_lab" / "gold_session_pullback.json"


def session_pullback_setups(bars: pd.DataFrame, rules: dict = RULES) -> pd.DataFrame:
    """One row per qualifying signal bar: signal_idx, entry_idx, side (+1 long / -1 short), atr, swing_low, swing_high."""
    frame = bars.sort_values("datetime").reset_index(drop=True)
    times = pd.to_datetime(frame["datetime"], utc=True)
    o, h, l, c = (frame[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
    ema20 = compute_ema(frame["close"], rules["ema_pullback"]).to_numpy(dtype=float)
    atr = compute_atr(frame, rules["atr_len"]).to_numpy(dtype=float)

    h4 = resample_bars(frame[["datetime", "open", "high", "low", "close"]].assign(volume=0.0), "4h")
    h4_close_time = pd.to_datetime(h4["datetime"], utc=True) + pd.Timedelta(hours=4)
    fast = compute_ema(h4["close"], rules["ema_trend_fast"]).to_numpy(dtype=float)
    slow = compute_ema(h4["close"], rules["ema_trend_slow"]).to_numpy(dtype=float)
    h4_stamps = h4_close_time.dt.tz_convert(None).to_numpy(dtype="datetime64[ns]")
    bar_close = (times + pd.Timedelta(hours=1)).dt.tz_convert(None).to_numpy(dtype="datetime64[ns]")
    h4_idx = np.searchsorted(h4_stamps, bar_close, side="right") - 1

    first_hour, last_hour = rules["signal_open_hours_utc"]
    swing = int(rules["swing_bars"])
    rows = []
    for t in range(max(swing, rules["atr_len"] + 1), len(frame) - 1):
        k = h4_idx[t]
        if k < MIN_H4_BARS or not np.isfinite(atr[t]) or atr[t] <= 0:
            continue
        if not first_hour <= times.iloc[t].hour <= last_hour:
            continue
        if times.iloc[t + 1] - times.iloc[t] != pd.Timedelta(hours=1):
            continue   # the fill bar must follow directly (no weekend or maintenance gap)
        rng = h[t] - l[t]
        if rng < rules["min_range_atr"] * atr[t]:
            continue
        trend = 1 if fast[k] > slow[k] else (-1 if fast[k] < slow[k] else 0)
        if trend == 1:
            pullback = l[t] <= ema20[t] and c[t] > ema20[t]
            rejection = (c[t] > o[t] and c[t] >= l[t] + rules["rejection_close_fraction"] * rng
                         and min(o[t], c[t]) - l[t] >= rules["rejection_wick_fraction"] * rng)
        elif trend == -1:
            pullback = h[t] >= ema20[t] and c[t] < ema20[t]
            rejection = (c[t] < o[t] and c[t] <= h[t] - rules["rejection_close_fraction"] * rng
                         and h[t] - max(o[t], c[t]) >= rules["rejection_wick_fraction"] * rng)
        else:
            continue
        if pullback and rejection:
            rows.append({"signal_idx": t, "entry_idx": t + 1, "side": trend, "atr": float(atr[t]),
                         "swing_low": float(l[t - swing + 1: t + 1].min()), "swing_high": float(h[t - swing + 1: t + 1].max())})
    return pd.DataFrame(rows, columns=["signal_idx", "entry_idx", "side", "atr", "swing_low", "swing_high"])


def session_pullback_exit(o, h, l, c, times, entry_idx: int, side: int, stop: float, rules: dict = RULES) -> dict:
    """Walk one trade from the fill bar: stop first, half at TP1 then break-even, rest at TP2, flat at 21:00 UTC."""
    entry = float(o[entry_idx])
    risk = side * (entry - stop)
    tp1, tp2 = entry + side * rules["tp1_r"] * risk, entry + side * rules["tp2_r"] * risk
    entry_day = times[entry_idx].normalize()
    flat_at = entry_day + pd.Timedelta(hours=rules["flat_hour_utc"])
    fraction_open, gross_r, current_stop, parts = 1.0, 0.0, stop, []

    def close(fraction, price, reason, j):
        nonlocal fraction_open, gross_r
        gross_r += fraction * side * (price - entry) / risk
        fraction_open -= fraction
        parts.append({"fraction": round(fraction, 3), "price": round(float(price), 3), "reason": reason, "bar": int(j)})

    j = entry_idx
    n = len(o)
    while fraction_open > 1e-9:
        if j >= n:
            close(fraction_open, c[n - 1], "data_end", n - 1)
            break
        if times[j] >= flat_at or times[j].normalize() != entry_day:
            if times[j] == flat_at:
                close(fraction_open, o[j], "flat_21utc", j)
            else:
                close(fraction_open, c[j - 1], "flat_21utc", j - 1)
            break
        stop_hit = l[j] <= current_stop if side == 1 else h[j] >= current_stop
        if stop_hit:
            close(fraction_open, current_stop, "stop" if current_stop == stop else "breakeven", j)
            break
        reached_tp1 = h[j] >= tp1 if side == 1 else l[j] <= tp1
        reached_tp2 = h[j] >= tp2 if side == 1 else l[j] <= tp2
        if fraction_open > 1 - 1e-9 and reached_tp1:
            close(rules["tp1_fraction"], tp1, "tp1", j)
            if reached_tp2:
                close(fraction_open, tp2, "tp2", j)
                break
            current_stop = entry   # break-even for the rest, from the next bar on
        elif fraction_open < 1 - 1e-9 and reached_tp2:
            close(fraction_open, tp2, "tp2", j)
            break
        j += 1
    last_bar = parts[-1]["bar"]
    return {"entry": round(entry, 3), "stop": round(float(stop), 3), "risk": round(float(risk), 3), "gross_r": round(gross_r, 4),
            "exit_bar": last_bar, "parts": parts, "outcome": parts[-1]["reason"]}


def session_pullback_split(bars: pd.DataFrame, setups: pd.DataFrame, signal_range: tuple[int, int], blocked_entry: np.ndarray,
                           cost_fraction: float, invert: bool = False, rules: dict = RULES) -> dict:
    """Trades for signal bars in [start, end) with the risk rules; fresh equity for the split."""
    frame = bars.sort_values("datetime").reset_index(drop=True)
    o, h, l, c = (frame[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
    times = pd.to_datetime(frame["datetime"], utc=True).reset_index(drop=True)
    start, end = signal_range
    equity, peak, max_dd, halted = 1.0, 1.0, 0.0, False
    busy_until, day, entries_today, day_start_equity = -1, None, 0, 1.0
    trades, skipped = [], {"filter": 0, "daily_cap": 0, "daily_loss": 0, "halted": 0, "busy": 0, "risk_size": 0}
    for s in setups[(setups["signal_idx"] >= start) & (setups["signal_idx"] < end)].itertuples(index=False):
        entry_idx = int(s.entry_idx)
        entry_day = times[entry_idx].normalize()
        if entry_day != day:
            day, entries_today, day_start_equity = entry_day, 0, equity
        if entry_idx <= busy_until:
            skipped["busy"] += 1; continue
        if halted:
            skipped["halted"] += 1; continue
        if blocked_entry[entry_idx]:
            skipped["filter"] += 1; continue
        if entries_today >= rules["max_entries_per_day"]:
            skipped["daily_cap"] += 1; continue
        if equity / day_start_equity - 1 <= -rules["daily_loss_limit"]:
            skipped["daily_loss"] += 1; continue
        side = -int(s.side) if invert else int(s.side)
        stop = (s.swing_low - rules["stop_atr"] * s.atr) if side == 1 else (s.swing_high + rules["stop_atr"] * s.atr)
        risk = side * (o[entry_idx] - stop)
        if not rules["min_r_atr"] * s.atr <= risk <= rules["max_r_atr"] * s.atr:
            skipped["risk_size"] += 1; continue
        result = session_pullback_exit(o, h, l, c, times, entry_idx, side, stop, rules)
        cost_r = cost_fraction * result["entry"] / result["risk"]
        net_r = result["gross_r"] - cost_r
        equity *= 1 + rules["risk_fraction"] * net_r
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak)
        if max_dd >= rules["halt_drawdown"]:
            halted = True
        entries_today += 1
        busy_until = result["exit_bar"]
        trades.append({"signal_time": times[int(s.signal_idx)].strftime("%Y-%m-%d %H:%M"),
                       "entry_time": times[entry_idx].strftime("%Y-%m-%d %H:%M"), "side": "BUY" if side == 1 else "SELL",
                       "gross_r": result["gross_r"], "cost_r": round(cost_r, 4), "net_r": round(net_r, 4),
                       "outcome": result["outcome"], "equity": round(equity, 6)})
    return {"trades": trades, "skipped": skipped, "halted": halted, "final_equity": equity, "max_drawdown": max_dd}


def session_pullback_summary(split: dict) -> dict:
    net = np.array([t["net_r"] for t in split["trades"]], dtype=float)
    wins, losses = net[net > 0], net[net <= 0]
    years = {}
    for t in split["trades"]:
        y = years.setdefault(t["entry_time"][:4], {"trades": 0, "net_r": 0.0})
        y["trades"] += 1
        y["net_r"] = round(y["net_r"] + t["net_r"], 3)
    return {
        "trades": int(len(net)),
        "long_trades": sum(1 for t in split["trades"] if t["side"] == "BUY"),
        "short_trades": sum(1 for t in split["trades"] if t["side"] == "SELL"),
        "win_rate_pct": round(len(wins) / len(net) * 100, 1) if len(net) else None,
        "expectancy_r": round(float(net.mean()), 4) if len(net) else None,
        "avg_cost_r": round(float(np.mean([t["cost_r"] for t in split["trades"]])), 4) if len(net) else None,
        "profit_factor_r": round(float(wins.sum() / abs(losses.sum())), 3) if len(net) and losses.sum() < 0 else None,
        "total_return_pct": round((split["final_equity"] - 1) * 100, 2),
        "max_drawdown_pct": round(split["max_drawdown"] * 100, 2),
        "per_trade_sharpe": round(float(net.mean() / net.std(ddof=1)), 4) if len(net) > 2 and net.std(ddof=1) > 0 else None,
        "halted_at_15pct_drawdown": split["halted"],
        "skipped_signals": split["skipped"],
        "outcomes": {k: sum(1 for t in split["trades"] if t["outcome"] == k) for k in ("stop", "breakeven", "tp1", "tp2", "flat_21utc", "data_end")},
        "by_year": years,
    }


def run_session_pullback_backtest(bars: pd.DataFrame, events: list[dict], boundaries: dict, cost_fraction: float,
                                  rules: dict = RULES) -> dict:
    """All variants, each with its inverse-direction baseline, on development (before holdout_start) and holdout."""
    from .strategy_lab import deflated_sharpe

    frame = bars.sort_values("datetime").reset_index(drop=True)
    times = pd.to_datetime(frame["datetime"], utc=True)
    stamps = times.dt.tz_convert(None).to_numpy(dtype="datetime64[ns]")
    holdout_start = int(np.searchsorted(stamps, pd.Timestamp(boundaries["holdout_start"]).to_datetime64()))
    ranges = {"development": (0, holdout_start), "holdout": (holdout_start, len(frame))}
    setups = session_pullback_setups(frame, rules)
    tier1_block = entries_blocked_mask(times, events)
    _, breaker_block = volatility_breaker_mask(frame)
    out = {"symbol": SYMBOL, "timeframe": "1h", "bars": len(frame),
           "data_start": times.iloc[0].strftime("%Y-%m-%d %H:%M"), "data_end": times.iloc[-1].strftime("%Y-%m-%d %H:%M"),
           "boundaries": boundaries, "cost_round_trip_fraction": cost_fraction, "rules": rules, "success_bar": SUCCESS,
           "signal_bars": int(len(setups)), "tier1_events_timed": len(events),
           "entry_bars_in_tier1_window": int(tier1_block.sum()), "entry_bars_blocked_by_breaker": int(breaker_block.sum()),
           "variants": {}}
    for name, variant in VARIANTS.items():
        blocked = np.zeros(len(frame), dtype=bool)
        if variant["tier1"]:
            blocked |= tier1_block
        if variant["breaker"]:
            blocked |= breaker_block
        out["variants"][name] = {
            split: {"strategy": session_pullback_summary(session_pullback_split(frame, setups, rng, blocked, cost_fraction, False, rules)),
                    "inverse_baseline": session_pullback_summary(session_pullback_split(frame, setups, rng, blocked, cost_fraction, True, rules))}
            for split, rng in ranges.items()}
    # Deflated Sharpe on the holdout counts every variant run here as a trial (the inverse baselines are not candidates).
    holdout_sr = [v["holdout"]["strategy"]["per_trade_sharpe"] for v in out["variants"].values() if v["holdout"]["strategy"]["per_trade_sharpe"] is not None]
    sr_variance = float(np.var(holdout_sr, ddof=1)) if len(holdout_sr) > 1 else 1e-6
    for name, variant in out["variants"].items():
        split = session_pullback_split(frame, setups, ranges["holdout"],
                                       (tier1_block if VARIANTS[name]["tier1"] else np.zeros(len(frame), bool))
                                       | (breaker_block if VARIANTS[name]["breaker"] else np.zeros(len(frame), bool)),
                                       cost_fraction, False, rules)
        dsr = deflated_sharpe([t["net_r"] for t in split["trades"]], n_trials=len(VARIANTS), sr_variance=sr_variance)
        hold = variant["holdout"]["strategy"]
        hold["deflated_sharpe"] = dsr
        checks = {
            "trades": hold["trades"] >= SUCCESS["min_trades"],
            "expectancy": (hold["expectancy_r"] or -9) > SUCCESS["min_expectancy_r"],
            "drawdown": hold["max_drawdown_pct"] < SUCCESS["max_drawdown_pct"],
            "deflated_sharpe": dsr is not None and dsr >= SUCCESS["min_deflated_sharpe"],
            "beats_inverse": (hold["expectancy_r"] or -9) > (variant["holdout"]["inverse_baseline"]["expectancy_r"] or -9),
        }
        variant["holdout_verdict"] = {"checks": checks, "passed": all(checks.values()),
                                      "evidence": "sufficient" if checks["trades"] else "insufficient (fewer than 100 holdout trades)"}
    out["deflated_sharpe_trials"] = len(VARIANTS)
    return out


def _load_fresh_bars() -> pd.DataFrame:
    """Closed broker H1 bars through the app's own MT5 connection (never a second MT5 session)."""
    from .mtf_data import fetch_app_bars, load_bars
    from .paper_trader import drop_forming_bars

    frame = fetch_app_bars(SYMBOL, "1h")
    source = "app (fresh)"
    if frame is None or frame.empty:
        frame, source = load_bars(SYMBOL, "1h", source="app"), "app (cache)"
    frame = drop_forming_bars(frame, 60, pd.Timestamp.now(tz="UTC"))
    frame.attrs["source"] = source
    return frame


def run_cli(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="Gold session pullback backtest (research only, never trades).")
    parser.add_argument("command", choices=["run"])
    args = parser.parse_args(argv)
    from . import strategy_lab as lab

    registry = lab.load_registry()
    boundaries = ((registry.get("markets") or {}).get(f"{SYMBOL}:1h") or {}).get("boundaries")
    if not boundaries:
        print("No locked XAUUSD:1h boundaries in the Strategy Lab registry; refusing to invent a holdout.")
        return 2
    bars = _load_fresh_bars()
    events = load_historical_events()
    cost = lab.BACKTEST_COSTS.get(SYMBOL, lab.BACKTEST_COSTS["default"])["round_trip_pct"]
    result = run_session_pullback_backtest(bars, events, boundaries, cost)
    result["bars_source"] = bars.attrs.get("source")
    result["generated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    path = results_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("bars", "data_start", "data_end", "boundaries", "signal_bars", "tier1_events_timed",
                                              "entry_bars_in_tier1_window", "entry_bars_blocked_by_breaker")}, indent=1))
    for name, variant in result["variants"].items():
        for split in ("development", "holdout"):
            s, inv = variant[split]["strategy"], variant[split]["inverse_baseline"]
            print(f"{name:20s} {split:11s} trades {s['trades']:4d} win {s['win_rate_pct']} exp {s['expectancy_r']}R "
                  f"PF {s['profit_factor_r']} ret {s['total_return_pct']}% DD {s['max_drawdown_pct']}% | inverse exp {inv['expectancy_r']}R "
                  f"PF {inv['profit_factor_r']} ret {inv['total_return_pct']}%")
        print(f"{'':20s} holdout verdict {variant['holdout_verdict']} DSR {variant['holdout']['strategy'].get('deflated_sharpe')}")
    print("saved", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(run_cli())
