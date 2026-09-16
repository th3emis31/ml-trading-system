"""Gold session pullback on the Vantage DEMO account 11581419 - the only strategy allowed to trade that account.

Rules: strategies/gold_session_pullback.md, with the setup and exit functions shared with the backtest
(src/gold_session_pullback_lab.py), so the demo trades exactly what was tested:

- H4 EMA50/EMA200 trend, H1 EMA20 pullback + rejection candle on the bar that just closed (signal bars 07:00-19:00 UTC),
  entered at the start of the next bar (the cycle runs at :01; a signal older than ``max_signal_delay_minutes`` is not
  chased).
- Two legs of 0.01 lots each (owner decision 2026-09-16, because 0.01 cannot be split): leg A takes profit at 1.5R,
  leg B at 3R, both with the swing stop (5-bar swing -/+ 1.5 x ATR14, R between 0.5 and 4 ATR). When leg A has filled at
  its target, leg B's stop moves to the entry price on the next cycle (the backtest moves it from the next bar).
- One trade at a time, at most 2 entries per UTC day, everything flat at 21:00 UTC.

Gates before an entry, each refusal logged with its reason: session window, tier-1 event window (FOMC/CPI/NFP, 60 min
before to 90 min after; historical file + live calendar), volatility breaker (H1 range > 3 x ATR14 blocks 3 bars), the
30-minute High-impact USD news window, spread <= 25 % of R, and the kill switches. There is no approval queue for the
demo: every order and every refusal goes to data/paper_trading/demo_session_pullback_log.jsonl instead.

Kill switches (owner decision 2026-09-16: measured on this strategy's own P&L, magic 440502, not on the other experts
sharing the account): -3 % of the day's starting balance stops entries for the rest of the UTC day; a 15 % drawdown of
the strategy's equity halts it; any MT5 error (not connected, unreadable account/positions/deals/bars, a rejected order,
close or stop change) halts it. A halt needs the owner's resume on /demo-trading. The STOP button halts and closes this
strategy's open legs (magic 440502 only).

Account: refused unless the logged-in account is login 11581419, trade_mode demo and a demo server. The login and the
magic number are constants in this file, not config, and every order, close and stop change re-checks the account.

``dry_run`` (default true) runs everything except sending: would-be orders are logged and settled on broker H1 bars
with the backtest's exit function, so the page shows their R. Sending is switched on only by the owner.
The MT5 connection belongs to the app (never a second MetaTrader5 process): the app runs the cycle through
POST /api/demo-trading/cycle, which the hourly task calls (``python -m src.demo_session_pullback cycle``).
"""
from __future__ import annotations

import argparse
import copy
import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import pandas as pd

from . import demo_executor, paper_trader
from .event_defence import (load_historical_events, tier1_events_from_calendar, tier1_window, utc_timestamp,
                            volatility_breaker_mask)
from .gold_session_pullback_lab import RULES, session_pullback_exit, session_pullback_setups
from .runtime_paths import smartentry_data_dir

DEMO_ACCOUNT_LOGIN = 11581419          # hard-coded: any other account is refused, whatever the config says
MAGIC = 440502                         # unused on the account (checked 2026-09-16 against every magic in its history)
SYMBOL = "XAUUSD"
VOLUME_PER_LEG = 0.01
LEGS = (("A", RULES["tp1_r"]), ("B", RULES["tp2_r"]))
STRATEGY = "gold_session_pullback"
H1_BARS = 5000                          # 1,250 H4 bars: EMA200 warm-up well past the lab's 200-bar minimum
CYCLE_URL = "http://127.0.0.1:5000/api/demo-trading/cycle"

DEFAULT_CONFIG = {
    "enabled": False,
    "dry_run": True,
    "max_entries_per_day": RULES["max_entries_per_day"],
    "flat_hour_utc": RULES["flat_hour_utc"],
    "daily_loss_limit": RULES["daily_loss_limit"],
    "halt_drawdown": RULES["halt_drawdown"],
    "max_spread_fraction_of_r": 0.25,
    "max_signal_delay_minutes": 20,
    "high_impact_news_window": True,
    "approval_queue": False,
}


def demo_paths() -> dict:
    """Config, state, order/decision log and trade memory (under the data folder the tests redirect)."""
    data = smartentry_data_dir()
    return {"config": data / "paper_trading" / "demo_session_pullback.json",
            "state": data / "paper_trading" / "demo_session_pullback_state.json",
            "log": data / "paper_trading" / "demo_session_pullback_log.jsonl",
            "trade_memory": data / "trade_memory" / "gold_session_pullback_demo.jsonl"}


INITIAL_STATE = {"halted": None, "day": None, "day_stopped": None, "entries_today": 0, "attempted": [], "trades": {},
                 "equity": 1.0, "peak_equity": 1.0, "day_start_equity": 1.0, "start_balance": None, "closed_money": 0.0}


def read_state() -> dict:
    path = demo_paths()["state"]
    return paper_trader.load_state(path) if path.exists() else copy.deepcopy(INITIAL_STATE)


def write_state(state: dict) -> None:
    state["attempted"] = state.get("attempted", [])[-100:]
    paper_trader.save_state(state, demo_paths()["state"])


def _append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, default=str) + "\n")


def read_jsonl(path: Path, limit: Optional[int] = None) -> list[dict]:
    if not Path(path).exists():
        return []
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows[-limit:] if limit else rows


def log(kind: str, now, reason: str, **details) -> dict:
    record = {"at": demo_executor._stamp(now), "event": kind, "magic": MAGIC, "reason": reason, **details}
    _append_jsonl(demo_paths()["log"], record)
    return record


# ----------------------------------------------------------------------------------------------- account guard
def check_demo_account(account: Optional[dict]) -> tuple[bool, str]:
    """True only for login 11581419 on a demo server with MT5 trade_mode demo. Not configurable."""
    ok, reason = demo_executor.is_demo_account(account, DEMO_ACCOUNT_LOGIN)
    if ok and int((account or {}).get("login") or 0) != DEMO_ACCOUNT_LOGIN:
        return False, f"account {account.get('login')} is not demo account {DEMO_ACCOUNT_LOGIN}"
    return ok, reason


class AccountRefused(RuntimeError):
    pass


class DemoOnlyEngine:
    """Every trading call re-reads the account first and refuses anything but the demo account."""

    def __init__(self, engine):
        self._engine = engine

    def __getattr__(self, name):
        return getattr(self._engine, name)

    def _guard(self):
        ok, reason = check_demo_account(self._engine.account_snapshot())
        if not ok:
            raise AccountRefused(reason)

    def place_market_order(self, **request):
        self._guard()
        if int(request.get("magic") or 0) != MAGIC or float(request.get("volume") or 0) != VOLUME_PER_LEG:
            raise AccountRefused(f"refused order outside this strategy's magic {MAGIC} / {VOLUME_PER_LEG} lot per leg")
        return self._engine.place_market_order(**request)

    def close_position(self, ticket, comment="GSP close"):
        self._guard()
        return self._engine.close_position(ticket, comment=comment)

    def modify_position_sltp(self, ticket, stop_loss=None, take_profit=None):
        self._guard()
        return self._engine.modify_position_sltp(ticket, stop_loss=stop_loss, take_profit=take_profit)


# ----------------------------------------------------------------------------------------------- context
def session_name(hour: int) -> str:
    if 7 <= hour <= 11:
        return "London"
    if 12 <= hour <= 15:
        return "London/New York overlap"
    if 16 <= hour <= 20:
        return "New York"
    return "outside session"


def all_tier1_events(calendar_events: list[dict]) -> list[dict]:
    """Historical tier-1 file plus this week's live calendar, one entry per event and time."""
    seen, merged = set(), []
    for event in load_historical_events() + tier1_events_from_calendar(calendar_events or []):
        key = (event["event"], pd.Timestamp(event["time_utc"]).strftime("%Y-%m-%d %H:%M"))
        if key not in seen:
            seen.add(key)
            merged.append(event)
    merged.sort(key=lambda e: e["time_utc"])
    return merged


def _halt(state: dict, now, kind: str, reason: str, **details) -> dict:
    state["halted"] = {"kind": kind, "reason": reason, "at": demo_executor._stamp(now)}
    return log("halted", now, reason, halt_kind=kind, **details)


def _mt5_error(state: dict, now, what: str, **details) -> dict:
    return _halt(state, now, "mt5_error", f"MT5 error: {what}. Trading halted until the owner resumes on /demo-trading.", **details)


# ----------------------------------------------------------------------------------------------- trade settlement
def _closed_bars(bars: pd.DataFrame, now) -> tuple[pd.DataFrame, bool]:
    """Bars without the still-forming one; the second value says whether the last row of ``bars`` is forming."""
    frame = bars.sort_values("datetime").reset_index(drop=True)
    last_open = pd.Timestamp(frame["datetime"].iloc[-1])
    forming = utc_timestamp(now) < last_open + pd.Timedelta(hours=1)
    return (frame.iloc[:-1].reset_index(drop=True) if forming else frame), forming


def _finish_trade(state: dict, trade: dict, now, r_result: float, net_money: Optional[float], legs: list[dict]) -> dict:
    trade.update({"status": "closed", "closed_at": demo_executor._stamp(now), "r_result": round(float(r_result), 4),
                  "net_money": None if net_money is None else round(float(net_money), 2), "legs": legs})
    state["equity"] = float(state.get("equity", 1.0)) * (1 + RULES["risk_fraction"] * float(r_result)) if trade["dry_run"] \
        else float(state.get("equity", 1.0))
    if not trade["dry_run"] and net_money is not None:
        state["closed_money"] = float(state.get("closed_money") or 0.0) + float(net_money)
        state["day_closed_money"] = float(state.get("day_closed_money") or 0.0) + float(net_money)
    memory = {
        "strategy": STRATEGY, "magic": MAGIC, "account": DEMO_ACCOUNT_LOGIN, "symbol": SYMBOL,
        "mode": "dry_run_simulated" if trade["dry_run"] else "demo_broker_fill",
        "trade_id": trade["id"], "side": trade["side"], "opened_at": trade["opened_at"], "closed_at": trade["closed_at"],
        "setup": trade["setup"], "session": trade["session"], "regime": trade["regime"],
        "next_event": trade["next_event"], "minutes_to_next_tier1_event": trade["minutes_to_next_tier1_event"],
        "legs": legs, "r_result": trade["r_result"], "net_money": trade["net_money"],
    }
    _append_jsonl(demo_paths()["trade_memory"], memory)
    return log("trade_closed", now, f"trade {trade['id']} closed at {trade['r_result']:+.2f}R", trade_id=trade["id"],
               r_result=trade["r_result"], net_money=trade["net_money"], dry_run=trade["dry_run"])


def settle_dry_run_trade(state: dict, trade: dict, bars: pd.DataFrame, now) -> Optional[dict]:
    """Walk broker H1 bars with the backtest's exit function; settles once stop/targets/21:00 flat decide it."""
    closed, forming = _closed_bars(bars, now)
    frame = bars.sort_values("datetime").reset_index(drop=True)
    flat_at = utc_timestamp(trade["entry_bar"]).normalize() + pd.Timedelta(hours=int(RULES["flat_hour_utc"]))
    # the forming bar may be used only as the 21:00 bar, for its open
    use = frame if forming and utc_timestamp(frame["datetime"].iloc[-1]) >= flat_at else closed
    times = pd.to_datetime(use["datetime"], utc=True).reset_index(drop=True)
    matches = np.flatnonzero(times == utc_timestamp(trade["entry_bar"]))
    if not len(matches):
        return None
    o, h, l, c = (use[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
    side = 1 if trade["side"] == "BUY" else -1
    result = session_pullback_exit(o, h, l, c, times, int(matches[0]), side, float(trade["stop"]))
    if result["outcome"] == "data_end":
        return None
    legs = [{"leg": p_["reason"], "fraction": p_["fraction"], "price": p_["price"],
             "bar_utc": times[p_["bar"]].strftime("%Y-%m-%d %H:%M")} for p_ in result["parts"]]
    return _finish_trade(state, trade, now, result["gross_r"], None, legs)


def manage_live_trade(engine: DemoOnlyEngine, state: dict, trade: dict, config: dict, now) -> list[dict]:
    """Break-even for leg B after leg A's target, flat at 21:00 UTC, and settlement from broker deals."""
    events = []
    positions = engine.positions(symbol=SYMBOL, magic=MAGIC)
    if positions is None:
        return [_mt5_error(state, now, "could not read open positions", trade_id=trade["id"])]
    live = {int(p["ticket"]): p for p in positions}
    open_legs = [leg for leg in trade["legs"] if int(leg["ticket"]) in live]
    closed_legs = [leg for leg in trade["legs"] if int(leg["ticket"]) not in live]
    side = 1 if trade["side"] == "BUY" else -1

    deals = {}
    if closed_legs:
        history = engine.deal_history(days=5) or {}
        if not history.get("ok"):
            return [_mt5_error(state, now, f"deal history unreadable ({history.get('reason')})", trade_id=trade["id"])]
        for deal in history.get("deals") or []:
            deals.setdefault(int(deal["position_id"]), []).append(deal)

    leg_a = next(leg for leg in trade["legs"] if leg["leg"] == "A")
    leg_b = next(leg for leg in trade["legs"] if leg["leg"] == "B")
    a_deals = deals.get(int(leg_a["ticket"])) or []
    a_hit_target = bool(a_deals) and side * (a_deals[-1]["price"] - trade["entry"]) > 0
    if a_hit_target and int(leg_b["ticket"]) in live and not trade.get("breakeven_moved"):
        result = engine.modify_position_sltp(int(leg_b["ticket"]), stop_loss=round(float(trade["entry"]), 2)) or {}
        if not result.get("executed"):
            return [_mt5_error(state, now, f"break-even stop change refused ({result.get('message')})", trade_id=trade["id"])]
        trade["breakeven_moved"] = demo_executor._stamp(now)
        events.append(log("breakeven", now, f"leg A reached 1.5R; leg B stop moved to entry {trade['entry']}",
                          trade_id=trade["id"], ticket=leg_b["ticket"]))

    flat_at = utc_timestamp(trade["entry_bar"]).normalize() + pd.Timedelta(hours=int(config["flat_hour_utc"]))
    if open_legs and utc_timestamp(now) >= flat_at:
        for leg in open_legs:
            result = engine.close_position(int(leg["ticket"]), comment=f"GSP flat 21UTC {leg['leg']}") or {}
            if not result.get("executed"):
                return events + [_mt5_error(state, now, f"21:00 flat close refused for ticket {leg['ticket']} "
                                                        f"({result.get('message')})", trade_id=trade["id"])]
            events.append(log("flat_21utc", now, "time stop: flat at 21:00 UTC", trade_id=trade["id"], ticket=leg["ticket"]))
        return events   # settled from the deals on the next cycle

    if not open_legs:
        legs, r_total, money = [], 0.0, 0.0
        for leg in trade["legs"]:
            leg_deals = deals.get(int(leg["ticket"])) or []
            if not leg_deals:
                return events   # the broker has not reported the exit deal yet
            exit_price = float(leg_deals[-1]["price"])
            leg_r = side * (exit_price - float(leg["fill"])) / float(trade["r_price"])
            net = sum(float(d["net"]) for d in leg_deals)
            r_total += 0.5 * leg_r
            money += net
            legs.append({"leg": leg["leg"], "ticket": leg["ticket"], "fill": leg["fill"], "exit_price": exit_price,
                         "r": round(leg_r, 4), "net_money": round(net, 2), "comment": leg_deals[-1].get("comment")})
        events.append(_finish_trade(state, trade, now, r_total, money, legs))
    return events


# ----------------------------------------------------------------------------------------------- kill switches
def update_kill_switches(state: dict, config: dict, account: dict, floating_money: float, now) -> list[dict]:
    events = []
    if state.get("start_balance") in (None, 0) and account.get("balance"):
        state["start_balance"] = float(account["balance"])
    if not any(t.get("dry_run") is False for t in (state.get("trades") or {}).values()):
        equity = float(state.get("equity", 1.0))            # simulated: 0.5 % risk per R, as in the backtest
    else:
        base = float(state.get("start_balance") or account.get("balance") or 1.0)
        equity = (base + float(state.get("closed_money") or 0.0) + floating_money) / base
    state["live_equity"] = round(equity, 6)
    state["peak_equity"] = max(float(state.get("peak_equity", 1.0)), equity)
    drawdown = (state["peak_equity"] - equity) / state["peak_equity"]
    day_change = equity / float(state.get("day_start_equity") or 1.0) - 1
    state["drawdown"], state["day_change"] = round(drawdown, 6), round(day_change, 6)
    if drawdown >= float(config["halt_drawdown"]) and not state.get("halted"):
        events.append(_halt(state, now, "drawdown", f"strategy drawdown {drawdown:.1%} reached the "
                                                    f"{config['halt_drawdown']:.0%} halt"))
    if day_change <= -float(config["daily_loss_limit"]) and not state.get("day_stopped"):
        state["day_stopped"] = f"day loss {day_change:.2%} reached -{config['daily_loss_limit']:.0%}"
        events.append(log("day_stopped", now, state["day_stopped"] + ": no new entries until the next UTC day"))
    return events


# ----------------------------------------------------------------------------------------------- the cycle
def run_cycle(engine, bars_fn: Callable, calendar_events: list[dict], now=None, config: Optional[dict] = None) -> dict:
    """One hourly decision. ``bars_fn(symbol, timeframe, count) -> (frame, source)``; only MT5 bars are accepted."""
    now = utc_timestamp(now)
    config = config or demo_executor.load_config(demo_paths()["config"], DEFAULT_CONFIG)
    state = read_state()
    events: list[dict] = []
    summary = {"at": demo_executor._stamp(now), "dry_run": bool(config.get("dry_run", True)), "events": events}
    try:
        return _run_cycle(DemoOnlyEngine(engine), bars_fn, calendar_events, now, config, state, events, summary)
    except AccountRefused as exc:
        events.append(_halt(state, now, "account_refused", f"Refused: {exc}. Only demo account {DEMO_ACCOUNT_LOGIN} may trade."))
        return summary
    finally:
        state["last_cycle"] = {"at": demo_executor._stamp(now), "decision": summary.get("decision"), "reason": summary.get("reason")}
        write_state(state)


def _run_cycle(engine, bars_fn, calendar_events, now, config, state, events, summary) -> dict:
    def decide(decision, reason, **details):
        summary.update(decision=decision, reason=reason)
        events.append(log(decision, now, reason, **details))
        return summary

    if not config.get("enabled"):
        return decide("disabled", "demo pullback trading is disabled in data/paper_trading/demo_session_pullback.json")
    if state.get("halted"):
        return decide("halted_skip", f"halted ({state['halted']['kind']}): {state['halted']['reason']}")
    if not (engine.status() or {}).get("connected"):
        events.append(_mt5_error(state, now, "not connected"))
        return summary
    account = engine.account_snapshot()
    if not account:
        events.append(_mt5_error(state, now, "account details unreadable"))
        return summary
    ok, account_reason = check_demo_account(account)
    if not ok:
        raise AccountRefused(account_reason)

    today = now.strftime("%Y-%m-%d")
    if state.get("day") != today:
        state.update(day=today, entries_today=0, day_stopped=None, day_closed_money=0.0,
                     day_start_equity=float(state.get("live_equity") or state.get("equity") or 1.0))

    frame, source = bars_fn(SYMBOL, "1h", H1_BARS)
    if frame is None or frame.empty or not str(source or "").startswith("mt5"):
        events.append(_mt5_error(state, now, f"no broker H1 bars (source {source})"))
        return summary
    bars = frame.sort_values("datetime").reset_index(drop=True)
    bars["datetime"] = pd.to_datetime(bars["datetime"], utc=True)

    # manage the open trade
    floating = 0.0
    open_trades = [t for t in state["trades"].values() if t["status"] == "open"]
    for trade in open_trades:
        if trade["dry_run"]:
            settled = settle_dry_run_trade(state, trade, bars, now)
            if settled:
                events.append(settled)
        else:
            events.extend(manage_live_trade(engine, state, trade, config, now))
            if state.get("halted"):
                return summary
    if any(not t["dry_run"] for t in state["trades"].values() if t["status"] == "open"):
        positions = engine.positions(symbol=SYMBOL, magic=MAGIC)
        if positions is None:
            events.append(_mt5_error(state, now, "could not read open positions"))
            return summary
        floating = sum(float(p.get("profit") or 0.0) for p in positions)
    events.extend(update_kill_switches(state, config, account, floating, now))
    if state.get("halted"):
        return summary

    # entry
    if any(t["status"] == "open" for t in state["trades"].values()):
        return decide("hold", "one trade at a time: a trade is already open")
    if state.get("day_stopped"):
        return decide("no_entry", f"daily loss stop: {state['day_stopped']}")
    if state.get("entries_today", 0) >= int(config["max_entries_per_day"]):
        return decide("no_entry", f"max {config['max_entries_per_day']} entries per UTC day reached")
    forming_open = utc_timestamp(bars["datetime"].iloc[-1])
    if forming_open != now.floor("h"):
        return decide("no_entry", f"no forming H1 bar at {now.floor('h'):%H:%M} UTC (market closed or bars late)")
    if now.hour >= int(config["flat_hour_utc"]):
        return decide("no_entry", "after 21:00 UTC")
    delay = (now - forming_open).total_seconds() / 60
    if delay > float(config["max_signal_delay_minutes"]):
        return decide("no_entry", f"{delay:.0f} min into the bar; entries are only taken within "
                                  f"{config['max_signal_delay_minutes']} min of the bar open")

    setups = session_pullback_setups(bars)
    signal_idx = len(bars) - 2
    row = setups[setups["signal_idx"] == signal_idx]
    signal_bar = utc_timestamp(bars["datetime"].iloc[signal_idx])
    if row.empty:
        return decide("no_setup", f"no pullback + rejection setup on the {signal_bar:%H:%M} UTC bar")
    s = row.iloc[0]
    side = "BUY" if int(s["side"]) == 1 else "SELL"
    trade_id = signal_bar.strftime("%Y-%m-%d %H:%M")
    base = {"trade_id": trade_id, "side": side, "signal_bar": trade_id}
    if trade_id in state.get("attempted", []):
        return decide("refused", f"signal bar {trade_id} was already handled", **base)
    state.setdefault("attempted", []).append(trade_id)

    tier1_events = all_tier1_events(calendar_events)
    window = tier1_window(tier1_events, now.to_pydatetime(), SYMBOL)
    next_event = window.get("next_event")
    minutes_to_next = None
    if next_event:
        minutes_to_next = round((utc_timestamp(next_event["time_utc"]) - now).total_seconds() / 60)
    context = {
        "session": session_name(forming_open.hour),
        "regime": {"trend": "up" if s["h4_ema_fast"] > s["h4_ema_slow"] else "down",
                   "h4_ema50": round(float(s["h4_ema_fast"]), 2), "h4_ema200": round(float(s["h4_ema_slow"]), 2),
                   "h4_ema_gap_pct": round((s["h4_ema_fast"] / s["h4_ema_slow"] - 1) * 100, 3)},
        "next_event": next_event, "minutes_to_next_tier1_event": minutes_to_next,
    }
    if window["entries_blocked"]:
        return decide("refused", "tier-1 event window: " + ", ".join(f"{e['event']} {e['time_utc']}" for e in window["blocking_events"]),
                      **base, **context)
    _, breaker_blocked = volatility_breaker_mask(bars)
    if breaker_blocked[len(bars) - 1]:
        return decide("refused", "volatility breaker: an H1 range above 3 x ATR14 in the last 3 bars", **base, **context)
    if config.get("high_impact_news_window", True):
        from .economic_calendar import news_window
        news = news_window(calendar_events or [], SYMBOL, now.to_pydatetime())
        if news.get("in_window"):
            return decide("refused", "High-impact news window: " + ", ".join(str(e.get("title")) for e in news["events"]),
                          **base, **context)

    quote = engine.quote(SYMBOL) or {}
    if not quote.get("ok"):
        events.append(_mt5_error(state, now, f"no live quote ({quote.get('message')})", **base))
        return summary
    bid, ask = float(quote["bid"]), float(quote["ask"])
    entry = ask if side == "BUY" else bid
    atr = float(s["atr"])
    stop = float(s["swing_low"]) - RULES["stop_atr"] * atr if side == "BUY" else float(s["swing_high"]) + RULES["stop_atr"] * atr
    direction = 1 if side == "BUY" else -1
    r_price = direction * (entry - stop)
    setup = {"signal_bar": trade_id, "entry_bar": demo_executor._stamp(forming_open), "atr14": round(atr, 3), "ema20": round(float(s["ema20"]), 2),
             "swing_low": s["swing_low"], "swing_high": s["swing_high"], "quote_entry": entry, "stop": round(stop, 2),
             "r_price": round(r_price, 3), "tp1": round(entry + direction * RULES["tp1_r"] * r_price, 2),
             "tp2": round(entry + direction * RULES["tp2_r"] * r_price, 2), "spread": round(ask - bid, 3)}
    if not RULES["min_r_atr"] * atr <= r_price <= RULES["max_r_atr"] * atr:
        return decide("refused", f"stop distance {r_price:.2f} is outside 0.5-4 x ATR14 ({atr:.2f})", **base, setup=setup, **context)
    if ask - bid > float(config["max_spread_fraction_of_r"]) * r_price:
        return decide("refused", f"spread {ask - bid:.2f} is above 25 % of R {r_price:.2f}", **base, setup=setup, **context)

    trade = {"id": trade_id, "side": side, "status": "open", "dry_run": bool(config.get("dry_run", True)),
             "opened_at": demo_executor._stamp(now), "entry_bar": demo_executor._stamp(forming_open), "entry": round(entry, 2), "stop": setup["stop"],
             "r_price": setup["r_price"], "setup": setup, **context, "legs": []}
    if trade["dry_run"]:
        trade["entry"] = round(float(bars["open"].iloc[-1]), 2)     # settled like the backtest: fill at the bar open
        trade["r_price"] = round(direction * (trade["entry"] - stop), 3)
        state["trades"][trade_id] = trade
        state["entries_today"] = int(state.get("entries_today", 0)) + 1
        return decide("dry_run_order", f"{side} setup passed every gate; dry run: 2 x {VOLUME_PER_LEG} lot orders logged, "
                                       "not sent", **base, setup=setup, **context)

    placed = []
    for leg, target_r in LEGS:
        request = {"symbol": SYMBOL, "side": side, "volume": VOLUME_PER_LEG, "stop_loss": setup["stop"],
                   "take_profit": setup["tp1"] if leg == "A" else setup["tp2"],
                   "comment": f"GSP {signal_bar:%y%m%d%H} {leg}", "magic": MAGIC, "allow_retry_without_stops": False}
        result = engine.place_market_order(**request) or {}
        if not result.get("executed"):
            for done in placed:   # never leave half a setup open
                engine.close_position(int(done["ticket"]), comment=f"GSP undo {done['leg']}")
            events.append(_mt5_error(state, now, f"order for leg {leg} rejected ({result.get('message')})", **base,
                                     request=request, undone=[d["ticket"] for d in placed]))
            return summary
        order = result.get("result") or {}
        placed.append({"leg": leg, "ticket": int(order.get("order") or order.get("deal") or 0), "target_r": target_r,
                       "fill": float(order.get("price") or entry), "request": request})
        events.append(log("order_sent", now, f"{side} leg {leg} 0.01 lot sent: all gates passed", **base, request=request,
                          ticket=placed[-1]["ticket"], **context))
    trade["legs"] = placed
    trade["entry"] = round(sum(p["fill"] for p in placed) / len(placed), 2)
    state["trades"][trade_id] = trade
    state["entries_today"] = int(state.get("entries_today", 0)) + 1
    summary.update(decision="opened", reason=f"{side} opened: 2 legs")
    return summary


# ----------------------------------------------------------------------------------------------- owner controls
def owner_stop(engine, now=None) -> dict:
    """STOP: halt, and close this strategy's open legs (magic 440502 only). Dry-run trades are cancelled without R."""
    now = utc_timestamp(now)
    state = read_state()
    closed, failures = [], []
    positions = None
    if engine is not None and (engine.status() or {}).get("connected"):
        positions = engine.positions(symbol=SYMBOL, magic=MAGIC)
    guarded = DemoOnlyEngine(engine) if engine is not None else None
    for position in positions or []:
        try:
            result = guarded.close_position(int(position["ticket"]), comment="GSP owner STOP") or {}
        except AccountRefused as exc:
            result = {"executed": False, "message": str(exc)}
        (closed if result.get("executed") else failures).append({"ticket": position["ticket"], "message": result.get("message")})
    for trade in state["trades"].values():
        if trade["status"] == "open" and trade["dry_run"]:
            trade.update(status="cancelled_by_stop", closed_at=demo_executor._stamp(now))
    event = _halt(state, now, "owner_stop", "Owner pressed STOP on /demo-trading.", closed=closed, close_failures=failures,
                  positions_readable=positions is not None)
    write_state(state)
    return event


def owner_resume(now=None) -> dict:
    now = utc_timestamp(now)
    state = read_state()
    previous = state.get("halted")
    state["halted"] = None
    state["peak_equity"] = float(state.get("live_equity") or state.get("equity") or 1.0) if (previous or {}).get("kind") == "drawdown" \
        else state.get("peak_equity", 1.0)
    event = log("resumed", now, "Owner resumed trading on /demo-trading.", previous_halt=previous)
    write_state(state)
    return event


def status_payload(engine=None, now=None) -> dict:
    now = utc_timestamp(now)
    config, state = demo_executor.load_config(demo_paths()["config"], DEFAULT_CONFIG), read_state()
    memory = read_jsonl(demo_paths()["trade_memory"])
    today = now.strftime("%Y-%m-%d")

    def expectancy(rows):
        r = [float(x["r_result"]) for x in rows if x.get("r_result") is not None]
        return {"trades": len(r), "expectancy_r": round(sum(r) / len(r), 4) if r else None,
                "total_r": round(sum(r), 3), "wins": sum(1 for x in r if x > 0)}

    positions = account = None
    if engine is not None and (engine.status() or {}).get("connected"):
        positions = engine.positions(symbol=SYMBOL, magic=MAGIC)
        account = engine.account_snapshot()
    account_ok, account_reason = check_demo_account(account) if account else (False, "MT5 not connected or account unreadable")
    trades = state.get("trades") or {}
    return {
        "strategy": STRATEGY, "magic": MAGIC, "demo_account": DEMO_ACCOUNT_LOGIN, "symbol": SYMBOL,
        "volume_per_leg": VOLUME_PER_LEG, "legs": [{"leg": leg, "target_r": r} for leg, r in LEGS],
        "config": config, "sending_orders": bool(config.get("enabled")) and not config.get("dry_run", True),
        "account_ok": account_ok, "account_reason": account_reason,
        "halted": state.get("halted"), "day_stopped": state.get("day_stopped"), "entries_today": state.get("entries_today", 0)
        if state.get("day") == today else 0,
        "kill_switches": {"daily_loss_limit": config["daily_loss_limit"], "halt_drawdown": config["halt_drawdown"],
                          "day_change": state.get("day_change"), "drawdown": state.get("drawdown"),
                          "measured_on": f"this strategy's P&L only (magic {MAGIC})"},
        "open_trade": next((t for t in trades.values() if t["status"] == "open"), None),
        "open_positions": positions,
        "today_trades": [t for t in trades.values() if str(t.get("opened_at", "")).startswith(today)],
        "expectancy": {"demo_broker_fills": expectancy([m for m in memory if m.get("mode") == "demo_broker_fill"]),
                       "dry_run_simulated": expectancy([m for m in memory if m.get("mode") == "dry_run_simulated"])},
        "recent_trades": list(reversed(memory))[:30],
        "log": list(reversed(read_jsonl(demo_paths()["log"], limit=60))),
        "last_cycle": state.get("last_cycle"),
        "backtest_verdict": "Backtest 2026-09-16 (BASELINE.md): FAIL - holdout +0.03 to +0.04R over 73-79 trades, "
                            "deflated Sharpe 0.60-0.65 (bar 0.95). Demo results are forward evidence only.",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Gold session pullback demo trading (the app runs the cycle).")
    parser.add_argument("command", choices=["cycle", "status"])
    args = parser.parse_args()
    if args.command == "status":
        print(json.dumps(status_payload(), indent=1, default=str)[:4000])
        raise SystemExit(0)
    from .execution_guard import SECRET_HEADER, load_or_create_secret
    cycle_request = urllib.request.Request(CYCLE_URL, data=b"{}", method="POST",
                                           headers={"Content-Type": "application/json", SECRET_HEADER: load_or_create_secret()})
    try:
        with urllib.request.urlopen(cycle_request, timeout=300) as response:
            body = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        print(f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} cycle call failed: {exc}")
        raise SystemExit(1)
    print(f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} {body.get('decision')}: {body.get('reason')}")
    raise SystemExit(0)
