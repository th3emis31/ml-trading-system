"""Daily trading agent (PAPER): research -> decision -> journal, for the TradingView daily plan.

Three jobs, once per closed H4 candle for each symbol on the watchlist (XAUUSD, BTCUSD):

1. Research  - broker candles through the app (never a second MetaTrader connection), the daily plan with the owner's
               TradingView "Swing Trend Pullback v2" inputs (tradingview_plan.build_daily_plan), daily bias and
               support / resistance, the ForexFactory calendar (news window + next high-impact events) and the
               ATOMIC V85 feed file from MT5 (evidence only).
2. Trade     - rule decision BUY / HOLD / CLOSE / WAIT / NO_TRADE / SKIP_NEWS, executed on the system's own PAPER LEDGER:
               entry at the next H4 candle's open, stop and 3R target from the plan, 3.5 ATR trailing stop from closed
               candles, 150-candle time exit, 1% of the ledger balance at risk, spread and overnight swap charged.
3. Journal   - one structured entry for every decision, including the ones where it did nothing, in
               data/daily_agent/journal.jsonl, a markdown file per day and (when configured) the Obsidian vault.

There is no live mode in this module: it imports no broker service and cannot place real orders. Going live would be a
separate owner decision after the evidence rules (holdout, 100+ trades, deflated Sharpe 0.95) are met.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import pandas as pd

from . import economic_calendar as ec
from . import strategy_lab as lab
from .ea_monitor import _write_json_atomic
from .paper_trader import drop_forming_bars
from .runtime_paths import cycle_health
from .system_doctor import _read_json  # tolerant reader: also opens the ANSI JSON that MetaTrader writes (ATOMIC feed)
from .tradingview_plan import build_daily_plan

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
WATCHLIST = ("XAUUSD", "BTCUSD")
MODE = "paper"                 # the only mode that exists here
START_BALANCE = 10_000.0
RISK_PCT = 1.0
H4_MINUTES = 240
ATOMIC_MAX_AGE_HOURS = 3.0
STALE_BAR_HOURS = 12.0
TIME_FORMAT = "%Y-%m-%d %H:%M"
JOURNAL_KEPT_IN_SUMMARY = 200
MT5_DATA_FOLDER = "D0E8209F77C8CF37AD8BF550E51FF075"

# The owner's TradingView inputs on the XAUUSD 4h chart (14 Sep 2026): EMA 21/50, slope lookback 9, pullback tolerance
# 0.65 ATR, push 15 bars > 0.6 ATR, bullish close, RSI > 40, ATR 14; swing stop 2 ATR, 3R target, 3.5 ATR trail, 150 bars.
AGENT_SPEC = {
    "name": "Swing Trend Pullback v2 (owner TradingView inputs)",
    "family": "ema_pullback",
    "params": {"side": "long", "ema_fast": 21, "ema_slow": 50, "slope_lookback": 9, "push_lookback": 15, "push_atr": 0.6,
               "pullback_tol": 0.65, "bull_close": True, "rsi_min": 40, "atr_len": 14},
    "exits": {"stop": "swing", "sl_atr": 2.0, "rr": 3.0, "trail_atr": 3.5, "max_bars": 150, "swing_lookback": 5},
}


# ---------------------------------------------------------------------------------------------------- helpers
def _ts(value) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")


def _fmt(value) -> str:
    return _ts(value).strftime(TIME_FORMAT)


def _r2(value) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, 2) if np.isfinite(number) else None


def agent_dir(data_dir: Path = DATA_DIR) -> Path:
    return Path(data_dir) / "daily_agent"


def default_atomic_dir() -> Path:
    return Path(os.environ.get("APPDATA", "")) / "MetaQuotes" / "Terminal" / MT5_DATA_FOLDER / "MQL5" / "Files" / "atomic_analyst"


def ledger_state(data_dir: Path, now) -> dict:
    """The paper ledger from data/daily_agent/state.json, with defaults for anything missing (a fresh 10,000 USD ledger)."""
    state = {"mode": MODE, "strategy": AGENT_SPEC["name"], "start_balance": START_BALANCE, "balance": START_BALANCE,
             "risk_pct": RISK_PCT, "positions": {}, "last_decided_bar": {}, "pending_logged": {}, "closed_trades": [],
             "created_at": _fmt(now), "updated_at": _fmt(now)}
    stored = _read_json(agent_dir(data_dir) / "state.json")
    if isinstance(stored, dict):
        state.update(stored)
    state["mode"] = MODE
    return state


def append_journal(data_dir: Path, entry: dict) -> None:
    path = agent_dir(data_dir) / "journal.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_journal(data_dir: Path, limit: Optional[int] = None) -> list[dict]:
    path = agent_dir(data_dir) / "journal.jsonl"
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows[-limit:] if limit else rows


# ---------------------------------------------------------------------------------------------------- research
def atomic_evidence(symbol: str, atomic_dir: Optional[Path], now) -> dict:
    """ATOMIC V85's verdict for the symbol when its feed file is fresh. Evidence only: it never gates a decision."""
    if atomic_dir is None:
        return {"available": False, "reason": "ATOMIC feed folder not set"}
    data = _read_json(Path(atomic_dir) / f"{symbol}.json")
    if not isinstance(data, dict):
        return {"available": False, "reason": "no ATOMIC feed file for this symbol"}
    try:
        age = (_ts(now).timestamp() - float(data.get("generatedAtEpoch"))) / 3600
    except (TypeError, ValueError):
        return {"available": False, "reason": "ATOMIC feed has no timestamp"}
    if age > ATOMIC_MAX_AGE_HOURS:
        return {"available": False, "reason": f"ATOMIC feed is {age:.1f} h old (MT5 chart closed?)"}
    return {"available": True, "verdict": data.get("verdict"), "direction": data.get("direction"),
            "agreement_pct": data.get("confidence"), "timeframe": data.get("timeframe"),
            "trend": (data.get("atomic") or {}).get("trend"), "age_hours": round(age, 1),
            "note": "evidence only - never gates a decision"}


def research(symbol: str, h4: Optional[pd.DataFrame], daily: Optional[pd.DataFrame], now, events: Optional[list],
             atomic_dir: Optional[Path]) -> dict:
    now = _ts(now)
    if h4 is None or h4.empty or daily is None or daily.empty:
        return {"available": False, "symbol": symbol, "reason": "no broker candles from the app"}
    plan = build_daily_plan(h4, daily, symbol, now=now, spec=AGENT_SPEC)
    if not plan.get("available"):
        return {"available": False, "symbol": symbol, "reason": plan.get("reason") or "plan unavailable"}
    raw = h4.copy()
    raw["datetime"] = pd.to_datetime(raw["datetime"], utc=True)
    raw = raw.sort_values("datetime").reset_index(drop=True)
    closed = drop_forming_bars(raw, H4_MINUTES, now)
    last_closed = _ts(plan["last_closed_candle"])
    after = raw[raw["datetime"] > last_closed]
    next_open = {"time": _fmt(after.iloc[0]["datetime"]), "price": float(after.iloc[0]["open"])} if len(after) else None
    news: dict = {"available": False, "reason": "economic calendar unavailable"}
    if events is not None:
        window = ec.news_window(events, symbol, now.to_pydatetime())
        coming = ec.upcoming(events, now.to_pydatetime(), 24, symbol, "High")
        news = {"available": True, "in_window": window["in_window"], "events_in_window": window["events"],
                "next_high_impact": window["next_high_impact"], "high_impact_next_24h": coming}
    return {"available": True, "symbol": symbol, "plan": plan, "closed_bars": closed, "last_closed_bar": plan["last_closed_candle"],
            "signal_high": float(closed.iloc[-1]["high"]) if len(closed) else None, "next_open": next_open,
            "data_fresh": (now - last_closed).total_seconds() / 3600 <= STALE_BAR_HOURS + 4, "news": news,
            "atomic": atomic_evidence(symbol, atomic_dir, now)}


def compact_research(res: dict) -> dict:
    """What the journal keeps from the research (no candle arrays)."""
    if not res.get("available"):
        return {"available": False, "reason": res.get("reason")}
    plan, news = res["plan"], res.get("news") or {}
    nxt = news.get("next_high_impact") or {}
    support, resistance = plan.get("nearest_support") or {}, plan.get("nearest_resistance") or {}
    return {
        "available": True, "close": plan.get("close"), "status": plan.get("status"), "headline": plan.get("headline"),
        "entry": plan.get("entry"), "stop_loss": plan.get("stop_loss"), "take_profit": plan.get("take_profit"),
        "atr": plan.get("atr"), "rsi": plan.get("rsi"), "fast_ema": plan.get("fast_ema"), "slow_ema": plan.get("slow_ema"),
        "checklist": plan.get("checklist"), "daily_bias": (plan.get("bias") or {}).get("label"),
        "nearest_support": {"name": support.get("name"), "price": support.get("price")} if support else None,
        "nearest_resistance": {"name": resistance.get("name"), "price": resistance.get("price")} if resistance else None,
        "news": {"available": news.get("available", False), "in_window": news.get("in_window"),
                 "next_high_impact": f"{nxt.get('title')} {nxt.get('time_utc')} UTC" if nxt else None,
                 "high_impact_next_24h": len(news.get("high_impact_next_24h") or [])},
        "atomic": res.get("atomic"), "next_open": res.get("next_open"), "data_fresh": res.get("data_fresh"),
    }


# ---------------------------------------------------------------------------------------------------- paper ledger
def manage_position(pos: dict, bars: pd.DataFrame, exits: dict = AGENT_SPEC["exits"]) -> tuple[dict, Optional[dict]]:
    """Walk closed H4 candles not processed yet, like the backtester: time exit at the open after max_bars, trailing stop
    raised from the previous closed candle, gap through the stop at the open, then stop before target inside a candle."""
    if bars is None or bars.empty:
        return pos, None
    frame = bars.sort_values("datetime").reset_index(drop=True)
    ind = lab.Indicators(frame)
    atr = ind.atr(int(AGENT_SPEC["params"].get("atr_len", 14)))
    times = pd.to_datetime(frame["datetime"], utc=True)
    o, h, l = ind.o, ind.h, ind.l
    entry_time = _ts(pos["entry_time"])
    matches = [i for i, stamp in enumerate(times) if stamp == entry_time]  # both tz-aware UTC
    if not matches:
        return pos, None  # the fill candle has not closed yet
    e = int(matches[0])
    done = _ts(pos["last_processed"]) if pos.get("last_processed") else None
    trail = float(exits.get("trail_atr") or 0.0)
    max_bars = int(exits.get("max_bars") or 10 ** 9)
    for t in range(e, len(frame)):
        if done is not None and times.iloc[t] <= done:
            continue
        reason = price = None
        if t > e:
            if t - e >= max_bars:
                reason, price = "time exit", o[t]
            else:
                if trail > 0 and np.isfinite(atr[t - 1]):
                    pos["extreme"] = max(float(pos["extreme"]), float(h[t - 1]))
                    raised = pos["extreme"] - trail * float(atr[t - 1])
                    if raised > pos["stop"]:
                        pos["stop"] = round(float(raised), 5)
                        pos["stop_moves"] = int(pos.get("stop_moves") or 0) + 1
                if o[t] <= pos["stop"]:
                    reason, price = "stop", o[t]
        if reason is None:
            if l[t] <= pos["stop"]:
                reason, price = "stop", pos["stop"]
            elif h[t] >= pos["target"]:
                reason, price = "take profit", pos["target"]
        pos["last_processed"] = _fmt(times.iloc[t])
        pos["bars_held"] = t - e + 1
        pos["last_close"] = float(frame.iloc[t]["close"])
        if reason is not None:
            if reason == "stop" and pos["stop"] > pos["initial_stop"]:
                reason = "trailing stop"
            return pos, {"reason": reason, "price": float(price), "time": _fmt(times.iloc[t])}
    return pos, None


def close_trade(pos: dict, exit_: dict, symbol: str) -> dict:
    units, entry, price = float(pos["units"]), float(pos["entry"]), float(exit_["price"])
    gross = (price - entry) * units
    spread = lab.BACKTEST_COSTS.get(symbol, lab.BACKTEST_COSTS["default"])["round_trip_pct"] * entry * units
    nights, swap = 0.0, 0.0
    holding = lab.HOLDING_COSTS.get(symbol)
    if holding:
        weights = lab.ROLLOVER_CALENDARS[holding.get("calendar", "forex")]
        counts = lab.rollover_counts(pd.Series([_ts(pos["entry_time"]), _ts(exit_["time"])]), weights)
        nights = float(counts[1] - counts[0])
        swap = nights * float(holding["long_pct_per_night"]) / 100.0 * entry * units
    net = gross - spread - swap
    return {**{k: v for k, v in pos.items() if k not in ("last_close",)}, "exit_time": exit_["time"], "exit_price": round(price, 5),
            "exit_reason": exit_["reason"], "gross": round(gross, 2), "spread_cost": round(spread, 2), "swap_cost": round(swap, 2),
            "nights": nights, "net": round(net, 2), "r_multiple": round(net / float(pos["risk_money"]), 2) if pos.get("risk_money") else None}


# ---------------------------------------------------------------------------------------------------- decision
def _context_reasons(res: dict) -> list[str]:
    plan, notes = res["plan"], []
    bias = (plan.get("bias") or {}).get("label")
    if bias:
        notes.append(f"daily bias {bias} (close vs daily EMA50/EMA200)")
    support, resistance = plan.get("nearest_support"), plan.get("nearest_resistance")
    if support or resistance:
        notes.append("nearest support " + (f"{support['name']} {support['price']}" if support else "-") +
                     ", resistance " + (f"{resistance['name']} {resistance['price']}" if resistance else "-"))
    news = res.get("news") or {}
    if not news.get("available"):
        notes.append("economic calendar unavailable: news risk unknown")
    else:
        soon = news.get("high_impact_next_24h") or []
        nxt = news.get("next_high_impact")
        if soon:
            notes.append("high-impact news in the next 24 h: " + "; ".join(f"{e['title']} {e['time_utc']} UTC" for e in soon[:3]))
        elif nxt:
            notes.append(f"next high-impact news {nxt['title']} {nxt['time_utc']} UTC")
    atomic = res.get("atomic") or {}
    if atomic.get("available"):
        notes.append(f"ATOMIC V85 ({atomic.get('timeframe')}) says {atomic.get('verdict')} at {atomic.get('agreement_pct')}% agreement, "
                     f"trend {atomic.get('trend')} - evidence only")
    else:
        notes.append(f"ATOMIC evidence unavailable: {atomic.get('reason')}")
    return notes


def decide(symbol: str, res: dict, state: dict, now) -> Optional[dict]:
    """One decision per closed H4 candle per symbol (None when that candle was already decided). Mutates ``state``."""
    now = _ts(now)
    base = {"time_utc": _fmt(now), "symbol": symbol, "mode": MODE, "strategy": AGENT_SPEC["name"]}
    decided, pending = state.setdefault("last_decided_bar", {}), state.setdefault("pending_logged", {})
    if not res.get("available"):
        key = f"NO_DATA:{res.get('reason')}"
        if decided.get(symbol) == key:
            return None
        decided[symbol] = key
        return {**base, "bar": None, "decision": "NO_DATA", "action": "none",
                "reasons": [f"no decision possible: {res.get('reason')}", "open paper positions are left unchanged until data returns"],
                "research": compact_research(res), "position": state["positions"].get(symbol), "balance": round(state["balance"], 2)}
    bar = res["last_closed_bar"]
    if decided.get(symbol) == bar:
        return None
    plan = res["plan"]
    exits = AGENT_SPEC["exits"]
    pos = state["positions"].get(symbol)
    decision, action, reasons, position = None, "none", [], None

    if pos:
        pos, exit_ = manage_position(pos, res["closed_bars"], exits)
        if exit_:
            trade = close_trade(pos, exit_, symbol)
            state["balance"] = round(float(state["balance"]) + trade["net"], 2)
            state["closed_trades"].append(trade)
            state["positions"].pop(symbol, None)
            decision, action, position = "CLOSE", "closed", trade
            reasons = [f"{exit_['reason']} reached at {exit_['price']:.2f} on the {exit_['time']} H4 candle",
                       f"net {trade['net']:+.2f} USD ({trade['r_multiple']:+.2f}R): gross {trade['gross']:+.2f}, spread -{trade['spread_cost']:.2f}, "
                       f"swap -{trade['swap_cost']:.2f} ({trade['nights']:.0f} nights)",
                       f"paper balance now {state['balance']:.2f} USD"]
        else:
            state["positions"][symbol] = pos
            unreal = (float(pos.get("last_close") or plan["close"]) - pos["entry"]) * pos["units"]
            pos["unrealized"] = round(unreal, 2)
            decision, position = "HOLD", dict(pos)
            reasons = [f"BUY open since {pos['entry_time']} at {pos['entry']:.2f}, {pos.get('bars_held', 0)} of {exits['max_bars']} candles held",
                       f"stop {pos['stop']:.2f}" + (f" (trailing, raised {pos.get('stop_moves', 0)}x from {pos['initial_stop']:.2f})" if pos["stop"] > pos["initial_stop"] else "") +
                       f", target {pos['target']:.2f}; unrealized {unreal:+.2f} USD",
                       "no action: neither stop, target nor time exit was reached on the closed candles"]
    else:
        status = plan.get("status")
        failed = [c["name"] for c in plan.get("checklist") or [] if not c.get("ok") and "(off)" not in c["name"]]
        passed = [c["name"] for c in plan.get("checklist") or [] if c.get("ok") and "(off)" not in c["name"]]
        news = res.get("news") or {}
        if status == "new_setup":
            if news.get("in_window"):
                titles = ", ".join(f"{e['title']} ({e['minutes_to']:+d} min)" for e in news.get("events_in_window") or [])
                decision = "SKIP_NEWS"
                reasons = [f"BUY setup confirmed on the {bar} H4 candle, but high-impact news is inside the ±30 min window: {titles}",
                           "entry skipped: the plan never opens a trade into high-impact USD news"]
            elif not res.get("data_fresh"):
                decision = "NO_TRADE"
                reasons = [f"BUY setup on the {bar} candle, but the candles are stale (market closed or feed down): not entered"]
            elif not res.get("next_open"):
                if pending.get(symbol) == bar:
                    return None
                pending[symbol] = bar
                return {**base, "bar": bar, "decision": "WAIT", "action": "none",
                        "reasons": [f"BUY setup confirmed on the {bar} H4 candle; entry is the next candle's open, which has not printed yet",
                                    "the agent will enter at that open on its next run"] + _context_reasons(res),
                        "research": compact_research(res), "position": None, "balance": round(state["balance"], 2)}
            else:
                entry, stop, target = float(res["next_open"]["price"]), float(plan["stop_loss"]), float(plan["take_profit"])
                risk = entry - stop
                if risk <= 0 or entry >= target:
                    decision = "NO_TRADE"
                    reasons = [f"BUY setup on the {bar} candle, but the next open {entry:.2f} is already " +
                               ("at or below the stop" if risk <= 0 else "at or above the target") + ": not entered"]
                else:
                    risk_money = float(state["balance"]) * RISK_PCT / 100.0
                    units = risk_money / risk
                    pos = {"symbol": symbol, "side": "BUY", "signal_bar": bar, "entry_time": res["next_open"]["time"],
                           "entry": round(entry, 5), "stop": round(stop, 5), "initial_stop": round(stop, 5), "target": round(target, 5),
                           "extreme": float(res.get("signal_high") or entry), "units": round(units, 6), "risk_money": round(risk_money, 2),
                           "bars_held": 0, "stop_moves": 0, "last_processed": None, "opened_at": _fmt(now)}
                    state["positions"][symbol] = pos
                    pending.pop(symbol, None)
                    decision, action, position = "BUY", "opened", dict(pos)
                    reasons = [f"Swing Trend Pullback BUY setup confirmed on the {bar} H4 candle: " + "; ".join(passed),
                               f"paper BUY at {entry:.2f} (next candle open {res['next_open']['time']}), stop {stop:.2f} ({risk:.2f} below), "
                               f"target {target:.2f} ({exits['rr']:.0f}R), trailing stop {exits['trail_atr']} ATR, time exit {exits['max_bars']} candles",
                               f"size {units:.4f} units = {RISK_PCT:.0f}% of the paper balance ({risk_money:.2f} USD) at risk"]
        elif status == "active":
            decision = "NO_TRADE"
            reasons = [f"the rules show a BUY that started {plan.get('detail', {}).get('since')} before the agent held it: not chased",
                       "waits for the next fresh setup"]
        elif status == "waiting_pullback":
            zone = (plan.get("detail") or {}).get("buy_zone")
            decision = "WAIT"
            reasons = ["uptrend and momentum push are in place, but no pullback entry yet",
                       f"buy only if an H4 candle dips into {zone[0]}-{zone[1]} and closes bullish with RSI above 40" if zone else "waiting for a pullback",
                       "still missing: " + ("; ".join(failed) if failed else "a closed signal candle")]
        else:
            decision = "NO_TRADE"
            reasons = ["no setup: " + ("; ".join(failed) if failed else "conditions not met")]
        reasons += _context_reasons(res)

    decided[symbol] = bar
    return {**base, "bar": bar, "decision": decision, "action": action, "reasons": reasons, "research": compact_research(res),
            "position": position, "balance": round(float(state["balance"]), 2)}


# ---------------------------------------------------------------------------------------------------- run
def day_markdown(entries: list[dict], date: str) -> str:
    lines = [f"# Daily Agent Journal {date}", "",
             "> Paper ledger only. Research, decision and reasons for every closed H4 candle, including the ones where the agent did nothing.", ""]
    if not entries:
        lines.append("No decisions yet today.")
    for e in entries:
        r = e.get("research") or {}
        lines.append(f"## {e['time_utc'][11:]} UTC - {e['symbol']} - {e['decision']}")
        lines.append(f"Candle {e.get('bar') or '-'} · close {r.get('close', '-')} · plan status {r.get('status', '-')} · balance {e.get('balance')} USD")
        lines += [f"- {reason}" for reason in e.get("reasons") or []]
        lines.append("")
    return "\n".join(lines)


def write_markdown(data_dir: Path, date: str, vault: Optional[Path] = None) -> dict:
    entries = [e for e in read_journal(data_dir) if str(e.get("time_utc", "")).startswith(date)]
    text = day_markdown(entries, date)
    path = agent_dir(data_dir) / "journal" / f"{date}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    result = {"markdown": str(path)}
    if vault:
        from .obsidian_notes import write_generated

        result["obsidian"] = write_generated(Path(vault) / "Agent" / f"{date} Agent Journal.md",
                                             {"date": date, "tags": "[smartentry, agent]"}, text)
    return result


def run_agent(now=None, data_dir: Path = DATA_DIR, loader: Optional[Callable] = None, calendar: Optional[dict] = None,
              atomic_dir: Optional[Path] = None, vault: Optional[Path] = None) -> dict:
    now = _ts(now if now is not None else pd.Timestamp.now(tz="UTC"))
    if loader is None:
        from .mtf_data import load_bars

        # broker candles through the app's own MT5 connection, fresh every run (no daily cache)
        loader = lambda symbol, timeframe: load_bars(symbol, timeframe, source="app", use_cache=False)  # noqa: E731
    atomic_dir = atomic_dir if atomic_dir is not None else default_atomic_dir()
    if calendar is None:
        try:
            calendar = ec.load_calendar(now.to_pydatetime())
        except Exception as exc:  # calendar is research, never a reason to stop the agent
            calendar = {"available": False, "reason": str(exc)}
    events = calendar.get("events") if calendar.get("available") else None
    state = ledger_state(data_dir, now)
    decisions = []
    for symbol in WATCHLIST:
        try:
            res = research(symbol, loader(symbol, "4h"), loader(symbol, "1d"), now, events, atomic_dir)
        except Exception as exc:
            res = {"available": False, "symbol": symbol, "reason": f"{type(exc).__name__}: {exc}"}
        if res.get("available"):
            _write_json_atomic(agent_dir(data_dir) / "plans" / f"{now.strftime('%Y-%m-%d')}_{symbol}.json", res["plan"])
        entry = decide(symbol, res, state, now)
        if entry:
            append_journal(data_dir, entry)
            decisions.append(f"{symbol}: {entry['decision']}")
    equity = float(state["balance"]) + sum(float(p.get("unrealized") or 0.0) for p in state["positions"].values())
    state["equity"] = round(equity, 2)
    state["updated_at"] = _fmt(now)
    _write_json_atomic(agent_dir(data_dir) / "state.json", state)
    markdown = write_markdown(data_dir, now.strftime("%Y-%m-%d"), vault)
    return {"generated_at": _fmt(now), "mode": MODE, "places_orders": False, "decisions": decisions or ["no new closed H4 candle"],
            "balance": state["balance"], "equity": state["equity"], "open_positions": sorted(state["positions"]), **markdown}


# ---------------------------------------------------------------------------------------------------- summary
def summary(data_dir: Path = DATA_DIR) -> dict:
    state = _read_json(agent_dir(data_dir) / "state.json")
    journal = read_journal(data_dir)
    if not isinstance(state, dict):
        return {"available": False, "reason": "the daily agent has not run yet", "mode": MODE, "places_orders": False,
                "watchlist": list(WATCHLIST), "strategy": AGENT_SPEC}
    closed = state.get("closed_trades") or []
    wins = [t for t in closed if (t.get("net") or 0) > 0]
    gross_win = sum(t["net"] for t in wins)
    gross_loss = -sum(t["net"] for t in closed if (t.get("net") or 0) <= 0)
    balance_curve, peak, max_dd = [float(state.get("start_balance") or START_BALANCE)], 0.0, 0.0
    for t in closed:
        balance_curve.append(balance_curve[-1] + float(t.get("net") or 0))
    for value in balance_curve:
        peak = max(peak, value)
        max_dd = max(max_dd, (peak - value) / peak * 100 if peak else 0.0)
    latest = {}
    for e in journal:
        latest[e.get("symbol")] = e
    plans = {}
    for symbol in WATCHLIST:
        files = sorted((agent_dir(data_dir) / "plans").glob(f"*_{symbol}.json"))
        plans[symbol] = _read_json(files[-1]) if files else None
    counts: dict = {}
    for e in journal:
        counts[e.get("decision")] = counts.get(e.get("decision"), 0) + 1
    return {
        "available": True, "mode": MODE, "places_orders": False, "strategy": AGENT_SPEC, "watchlist": list(WATCHLIST),
        "risk_pct": RISK_PCT, "start_balance": state.get("start_balance"), "balance": state.get("balance"),
        "equity": state.get("equity", state.get("balance")), "updated_at": state.get("updated_at"), "created_at": state.get("created_at"),
        "positions": state.get("positions") or {}, "closed_trades": closed[-100:][::-1],
        "stats": {"closed": len(closed), "wins": len(wins), "win_rate_pct": round(100 * len(wins) / len(closed), 1) if closed else None,
                  "net": round(sum(float(t.get("net") or 0) for t in closed), 2),
                  "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
                  "avg_r": round(float(np.mean([t.get("r_multiple") or 0 for t in closed])), 2) if closed else None,
                  "max_drawdown_pct": round(max_dd, 2), "needed_for_evidence": 100},
        "decision_counts": counts, "latest": latest, "plans": plans,
        # Two different clocks, and confusing them hides a stopped agent: the task runs hourly, but a decision is only
        # taken when an H4 candle closes, so a four-hour-old decision is normal while a four-hour-old run is not.
        "run_health": cycle_health({"at": state.get("updated_at")}, 60, pd.Timestamp.now(tz="UTC")),
        "decision_health": cycle_health({"at": journal[-1].get("time_utc")} if journal else None,
                                                      240, pd.Timestamp.now(tz="UTC")),
        "journal": journal[-JOURNAL_KEPT_IN_SUMMARY:][::-1],
    }


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="Daily trading agent (paper ledger): research -> decision -> journal.")
    parser.add_argument("command", nargs="?", default="run", choices=["run", "status"])
    args = parser.parse_args(argv)
    if args.command == "status":
        s = summary()
        print(json.dumps({k: v for k, v in s.items() if k not in ("journal", "plans", "latest", "closed_trades")}, indent=1, default=str))
        return 0
    config = _read_json(DATA_DIR / "obsidian.json") or {}
    vault = Path(config["vault_path"]) if config.get("vault_path") else None
    print(json.dumps(run_agent(vault=vault), indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
