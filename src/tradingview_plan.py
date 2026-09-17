"""Daily TradingView plan: the SwingTrendPullback rules on the broker's own candles.

``build_daily_plan`` applies the live expert's inputs (the TradingView settings, ``strategy_lab.EA_SPECS["tradingview"]``)
to closed H4 candles and returns the day's plan: setup status (trade active, new setup, waiting for a pullback, no trade),
entry, stop loss, take profit and trailing stop, the condition checklist, and support / resistance with the daily bias
(the Daily Report's own level and momentum functions). The rules are the same as the TradingView indicator in
``strategies/tradingview/smartentry_daily_plan.pine``, so the chart and this plan agree apart from data-feed differences
(TradingView's OANDA feed vs the broker) and indicator warm-up.

A research plan: it places no orders and no strategy has passed the system's full evidence test yet.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from . import strategy_lab as lab
from .daily_report import analyse_levels, analyse_momentum
from .ea_monitor import _write_json_atomic
from .features import compute_ema
from .paper_trader import drop_forming_bars

ROOT = Path(__file__).resolve().parents[1]
PLAN_DIR = ROOT / "data" / "tradingview_plans"
PINE_PATH = ROOT / "strategies" / "tradingview" / "smartentry_daily_plan.pine"
PLAN_SPEC = lab.EA_SPECS["tradingview"]
H4_MINUTES, DAY_MINUTES = 240, 1440
TIME_FORMAT = "%Y-%m-%d %H:%M"


def _price(value) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, 2) if np.isfinite(number) else None


def follow_trades(ind: lab.Indicators, side, stop, target, atr, exits: dict, lookback_bars: int = 900) -> dict:
    """Walk the recent signals like the backtester (one position, next-bar entry, stop before target, trailing stop
    from closed bars, time exit) and return the open position, the last closed trade and a signal on the last bar."""
    n = len(ind.c)
    o, h, l = ind.o, ind.h, ind.l
    trail = float(exits.get("trail_atr") or 0.0)
    max_bars = int(exits.get("max_bars") or 10 ** 9)
    position, last_closed, pending = None, None, None
    for t in range(max(0, n - lookback_bars), n):
        if position is not None and t >= position["entry_bar"]:
            s, reason, price = position["side"], None, None
            if t > position["entry_bar"]:
                if t - position["entry_bar"] >= max_bars:
                    reason, price = "time exit", o[t]
                else:
                    if trail > 0 and np.isfinite(atr[t - 1]):
                        if s == 1:
                            position["extreme"] = max(position["extreme"], h[t - 1])
                            position["stop"] = max(position["stop"], position["extreme"] - trail * atr[t - 1])
                        else:
                            position["extreme"] = min(position["extreme"], l[t - 1])
                            position["stop"] = min(position["stop"], position["extreme"] + trail * atr[t - 1])
                    if (s == 1 and o[t] <= position["stop"]) or (s == -1 and o[t] >= position["stop"]):
                        reason, price = "stop", o[t]
            if reason is None:
                if (s == 1 and l[t] <= position["stop"]) or (s == -1 and h[t] >= position["stop"]):
                    reason, price = "stop", position["stop"]
                elif np.isfinite(position["target"]) and ((s == 1 and h[t] >= position["target"]) or (s == -1 and l[t] <= position["target"])):
                    reason, price = "take profit", position["target"]
            if reason is not None:
                if reason == "stop" and position["stop"] != position["initial_stop"]:
                    reason = "trailing stop"
                position.update(exit_bar=t, exit_price=float(price), exit_reason=reason)
                last_closed, position = position, None
        if position is None and side[t] != 0:
            s = int(side[t])
            if t + 1 >= n:
                pending = {"signal_bar": t, "side": s, "stop": float(stop[t]), "target": float(target[t])}
            else:
                entry = float(o[t + 1])
                if np.isfinite(entry) and ((s == 1 and entry > stop[t]) or (s == -1 and entry < stop[t])):
                    position = {"side": s, "signal_bar": t, "entry_bar": t + 1, "entry": entry, "stop": float(stop[t]),
                                "initial_stop": float(stop[t]), "target": float(target[t]),
                                "extreme": float(h[t] if s == 1 else l[t])}
    return {"open": position, "last_closed": last_closed, "pending": pending}


def build_daily_plan(h4: pd.DataFrame, daily: pd.DataFrame, symbol: str = "XAUUSD", now=None, spec: dict = PLAN_SPEC) -> dict:
    now = pd.Timestamp(now) if now is not None else pd.Timestamp.now(tz="UTC")
    if now.tzinfo is None:
        now = now.tz_localize("UTC")
    bars = drop_forming_bars(h4, H4_MINUTES, now).sort_values("datetime").reset_index(drop=True)
    days = drop_forming_bars(daily, DAY_MINUTES, now).sort_values("datetime").reset_index(drop=True)
    if len(bars) < 300 or len(days) < 60:
        return {"available": False, "symbol": symbol, "reason": f"not enough closed candles ({len(bars)} H4, {len(days)} daily)"}
    ind = lab.Indicators(bars)
    params, exits = spec["params"], spec["exits"]
    cond = lab.ema_pullback_conditions(ind, params)
    side, stop, target = lab.strategy_orders(ind, spec)
    atr, fast = cond["atr"], cond["fast"]
    last = len(bars) - 1
    times = ind.times
    close = float(ind.c[last])
    trend_ok, _ = lab._trend_masks(ind, params.get("trend_ema", 0))
    trades = follow_trades(ind, side, stop, target, atr, exits)
    rr = float(exits.get("rr") or 0.0)

    open_trade, pending, closed = trades["open"], trades["pending"], trades["last_closed"]
    forming = bool(cond["trend_up"][last] and cond["push_up"][last] and trend_ok[last])
    if open_trade:
        status, headline = "active", "BUY trade active"
        entry, plan_stop, plan_target = open_trade["entry"], open_trade["stop"], open_trade["target"]
        detail = {"since": times.iloc[open_trade["entry_bar"]].strftime(TIME_FORMAT), "bars_held": last - open_trade["entry_bar"] + 1,
                  "initial_stop": _price(open_trade["initial_stop"]), "trailing": open_trade["stop"] != open_trade["initial_stop"]}
    elif pending:
        status, headline = "new_setup", "NEW BUY setup: enter at the next H4 candle's open"
        entry, plan_stop, plan_target = close, pending["stop"], pending["target"]
        detail = {"signal_bar": times.iloc[pending["signal_bar"]].strftime(TIME_FORMAT)}
    elif forming:
        lowest = float(np.nanmin(ind.l[max(0, last - int(exits.get("swing_lookback") or 5) + 1): last + 1]))
        entry = float(fast[last])
        plan_stop = (min(lowest, entry) if exits.get("stop") == "swing" else entry) - float(exits["sl_atr"]) * float(atr[last])
        plan_target = entry + rr * (entry - plan_stop) if rr > 0 and entry > plan_stop else float("nan")
        status, headline = "waiting_pullback", "WAIT: buy only if price pulls back into the zone around the fast EMA"
        detail = {"buy_zone": [_price(entry - cond["tolerance"][last]), _price(entry + cond["tolerance"][last])]}
    else:
        status, headline = "no_trade", "NO TRADE: the H4 uptrend conditions are not in place"
        entry = plan_stop = plan_target = float("nan")
        detail = {}

    risk = entry - plan_stop if np.isfinite(entry) and np.isfinite(plan_stop) else None
    levels = analyse_levels(days)
    # analyse_levels measures from the last daily close; the plan uses the latest H4 close, so pick the nearest support
    # and resistance again from that price (a level between the two closes would otherwise sit on the wrong side).
    day_atr = float(levels.get("atr") or 0.0)
    candidates = [dict(level) for level in (levels.get("levels") or []) + (levels.get("swings") or [])]
    for level in candidates:
        level["distance_atr"] = round((level["price"] - close) / day_atr, 2) if day_atr > 0 else None
        level["distance_pct"] = round((level["price"] / close - 1) * 100, 2)
    reference = round(close, 2)   # level prices are rounded; a level at the close itself is neither support nor resistance
    above = sorted((lv for lv in candidates if lv["price"] > reference), key=lambda lv: lv["price"])
    below = sorted((lv for lv in candidates if lv["price"] < reference), key=lambda lv: lv["price"], reverse=True)
    levels["nearest_resistance"] = above[0] if above else None
    levels["nearest_support"] = below[0] if below else None
    momentum = analyse_momentum(days)
    day_close = days["close"].astype(float)
    ema50 = float(compute_ema(day_close, 50).iloc[-1])
    ema200 = float(compute_ema(day_close, 200).iloc[-1])
    last_day_close = float(day_close.iloc[-1])
    bias = ("bullish" if last_day_close > ema50 > ema200 else "bearish" if last_day_close < ema50 < ema200 else "mixed")
    checklist = [
        {"name": "H4 uptrend: fast EMA above slow EMA and rising", "ok": bool(cond["trend_up"][last])},
        {"name": "Momentum push beyond the slow EMA", "ok": bool(cond["push_up"][last])},
        {"name": "Pullback touches the fast EMA zone", "ok": bool(cond["touch_up"][last])},
        {"name": "Bullish close on the signal candle", "ok": bool(cond["close_up"][last])},
        {"name": f"RSI above {params.get('rsi_min')}", "ok": bool(cond["rsi_up"][last])},
        {"name": "Extra trend EMA filter" if params.get("trend_ema") else "Extra trend EMA filter (off)", "ok": bool(trend_ok[last])},
    ]
    plan = {
        "available": True, "places_orders": False, "symbol": symbol, "timeframe": "H4",
        "generated_at": now.strftime(TIME_FORMAT), "date": now.strftime("%Y-%m-%d"),
        "last_closed_candle": times.iloc[last].strftime(TIME_FORMAT), "close": _price(close),
        "status": status, "headline": headline, "side": "BUY" if status != "no_trade" else None,
        "entry": _price(entry), "stop_loss": _price(plan_stop), "take_profit": _price(plan_target),
        "risk_per_unit": _price(risk), "risk_atr": round(risk / float(atr[last]), 2) if risk and float(atr[last]) > 0 else None,
        "reward_risk": rr, "trailing_stop_atr": exits.get("trail_atr"), "max_bars": exits.get("max_bars"), "detail": detail,
        "atr": _price(atr[last]), "rsi": _price(cond["rsi"][last]), "fast_ema": _price(fast[last]), "slow_ema": _price(cond["slow"][last]),
        "checklist": checklist,
        "bias": {"label": bias, "daily_close": _price(last_day_close), "ema50": _price(ema50), "ema200": _price(ema200),
                 "rsi_14": momentum.get("rsi_14")},
        "levels": levels.get("levels"), "swings": levels.get("swings"),
        "nearest_resistance": levels.get("nearest_resistance"), "nearest_support": levels.get("nearest_support"),
        "last_closed_trade": ({"entry": _price(closed["entry"]), "exit": _price(closed["exit_price"]), "reason": closed["exit_reason"],
                               "opened": times.iloc[closed["entry_bar"]].strftime(TIME_FORMAT),
                               "closed": times.iloc[closed["exit_bar"]].strftime(TIME_FORMAT),
                               "result_pct": round((closed["exit_price"] / closed["entry"] - 1) * 100 * closed["side"], 2)}
                              if closed else None),
        "rules": {"inputs": params, "exits": exits, "source": "SwingTrendPullback EA (TradingView inputs)"},
        "note": "Research plan on broker candles, not financial advice. Demo account first.",
    }
    return plan


def save_daily_plan(plan: dict, plan_dir: Path = PLAN_DIR) -> Optional[Path]:
    """Keep one file per day and symbol (overwritten during the day) plus the latest plan per symbol."""
    if not plan.get("available"):
        return None
    plan_dir = Path(plan_dir)
    dated = plan_dir / f"{plan['date']}_{plan['symbol']}.json"
    _write_json_atomic(dated, plan)
    _write_json_atomic(plan_dir / f"latest_{plan['symbol']}.json", plan)
    return dated


def pine_script_text(path: Path = PINE_PATH) -> Optional[str]:
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        return None
