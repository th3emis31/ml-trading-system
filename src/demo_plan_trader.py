"""Execute the daily TradingView plan on the demo account. The plan's missing half.

Until 20 September 2026 ``src/tradingview_plan.py`` had NO execution path at all: build_daily_plan was
read by daily_agent, obsidian_notes and plan_journal, all of which display or journal. The owner asked
repeatedly for the system to open a trade from the plan, and it could not - the levels were drawn on a
chart for them to act on by hand.

WHY THIS ROUTE AND NOT THE BROWSER. TradingView's own paper trading touches no account and produces no
evidence the ladder can use, its webhook cannot reach this machine (the app listens on the LAN only, and
alerts fire from TradingView's cloud), and browser automation has no fill guarantees. The system already
computes the same plan from BROKER candles, which is better than the TradingView copy - no basis error,
no alert latency - and the account's real safety lives on the MT5 side. So the plan decides here and MT5
executes, on demo account 11581419 and nowhere else.

SAFETY, in the order it is applied. The account is checked first and a non-demo login refuses everything.
``dry_run`` defaults to TRUE, so this decides and logs and sends nothing until the owner turns it off.
One position at a time, one entry per session, its own magic number so its trades are never confused with
another strategy's, and its own kill switches on its own profit and loss. Every order is journalled the
moment it is placed, not only when it closes.

WHAT IT DOES NOT CLAIM. The SwingTrendPullback plan has not cleared the evidence bar, and its last closed
plan trade was a stop at -2.36 %. This module gives the plan a way to act; it does not make it good.

    python -m src.demo_plan_trader cycle|status
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Callable, Optional

import pandas as pd

from . import demo_executor
from . import demo_session_pullback as shared
from .runtime_paths import smartentry_data_dir

STRATEGY = "swing_trend_pullback_plan"
MAGIC = 440704                      # its own magic: 440502 is the pullback, 440603 the breakout
SYMBOLS = ("XAUUSD",)
VOLUME = 0.01

DEFAULT_CONFIG = {
    "enabled": True,
    # Sends NOTHING until the owner sets this false. The capability is built and switched off, which is
    # the only honest way to ship an order path that has never been asked to place one.
    "dry_run": True,
    "symbols": list(SYMBOLS),
    "volume": VOLUME,
    "max_entries_per_day": 1,
    "daily_loss_limit": 0.03,
    "halt_drawdown": 0.15,
    "max_entry_drift_atr": 0.5,     # refuse if price has already run past the plan's entry
    "high_impact_news_window": True,
    "note": ("Executes the daily SwingTrendPullback plan on Vantage DEMO 11581419 only. dry_run true means "
             "it decides and logs and places nothing. The plan has NOT cleared the evidence bar."),
}

INITIAL_STATE = {"halted": None, "day": None, "entries_today": 0, "trades": {}, "last_cycle": None,
                 "closed_money": 0.0}


def plan_paths() -> dict:
    data = smartentry_data_dir()
    return {"config": data / "paper_trading" / "demo_plan_trader.json",
            "state": data / "paper_trading" / "demo_plan_trader_state.json",
            "log": data / "paper_trading" / "demo_plan_trader.log",
            "trade_memory": data / "trade_memory" / "swing_trend_pullback_plan_demo.jsonl"}


def _plan_sink() -> dict:
    return {"path": plan_paths()["log"]}


def load_plan_state() -> dict:
    path = plan_paths()["state"]
    if not path.exists():
        return dict(INITIAL_STATE)
    try:
        return {**INITIAL_STATE, **json.loads(path.read_text(encoding="utf-8"))}
    except (OSError, ValueError):
        return dict(INITIAL_STATE)


def save_plan_state(state: dict) -> None:
    path = plan_paths()["state"]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1, default=str), encoding="utf-8")
    tmp.replace(path)


def plan_is_tradable(plan: Optional[dict]) -> tuple[bool, str]:
    """Only a plan that names a side, an entry, a stop and a target is an order.

    The plan reports ``status`` "no_trade" far more often than not - on 20 Sep three of its six checklist
    conditions failed - and a plan without levels must never be turned into a market order by filling the
    gaps with something plausible.
    """
    if not plan:
        return False, "no plan was produced"
    if not plan.get("available", True):
        return False, str(plan.get("reason") or "the plan is unavailable")
    if plan.get("status") != "trade":
        return False, f"plan status is {plan.get('status')!r}: {plan.get('headline') or 'no setup'}"
    missing = [k for k in ("side", "entry", "stop_loss", "take_profit") if plan.get(k) in (None, "")]
    if missing:
        return False, f"the plan is missing {', '.join(missing)}, so it is not an order"
    if str(plan.get("side")).lower() not in ("long", "short", "buy", "sell"):
        return False, f"unrecognised side {plan.get('side')!r}"
    return True, "the plan names a side, an entry, a stop and a target"


def entry_drift_ok(plan: dict, price: float, max_drift_atr: float) -> tuple[bool, str]:
    """Has price already run past the plan's entry? A late fill is a different trade from the one planned."""
    atr = float(plan.get("atr") or 0.0)
    entry = float(plan.get("entry"))
    if atr <= 0:
        return False, "the plan carries no ATR, so drift cannot be judged"
    drift = abs(price - entry) / atr
    if drift > max_drift_atr:
        return False, (f"price {price:.2f} is {drift:.2f} ATR from the plan's entry {entry:.2f}, beyond "
                       f"{max_drift_atr:.2f}; this is no longer the planned trade")
    return True, f"price is {drift:.2f} ATR from the plan entry"


def plan_cycle(engine, plan_fn: Callable[[str], dict], now=None, config: Optional[dict] = None) -> dict:
    """One decision per run. Places at most one order, on the demo account, and only when dry_run is false."""
    now = shared.utc_timestamp(now)
    config = config or demo_executor.load_config(plan_paths()["config"], DEFAULT_CONFIG)
    state = load_plan_state()
    events: list[dict] = []
    summary = {"at": demo_executor._stamp(now), "strategy": STRATEGY, "magic": MAGIC,
               "dry_run": bool(config.get("dry_run", True)), "events": events}
    sink = _plan_sink()

    def decide(decision, reason, **details):
        summary.update(decision=decision, reason=reason)
        events.append(shared.log(decision, now, reason, sink=sink, magic=MAGIC, **details))
        return summary

    try:
        if not config.get("enabled"):
            return decide("disabled", "plan trading is disabled in data/paper_trading/demo_plan_trader.json")
        if state.get("halted"):
            return decide("halted_skip", f"halted ({state['halted']['kind']}): {state['halted']['reason']}")

        guarded = shared.DemoOnlyEngine(engine, magic=MAGIC, volume=float(config.get("volume", VOLUME)))
        if not (guarded.status() or {}).get("connected"):
            events.append(shared._mt5_error(state, now, "not connected", sink=sink))
            return summary

        day = now.strftime("%Y-%m-%d")
        if state.get("day") != day:
            state.update(day=day, entries_today=0)
        if state["entries_today"] >= int(config.get("max_entries_per_day", 1)):
            return decide("done_today", f"{state['entries_today']} entry already taken today")

        open_here = [p for p in (guarded.positions() or []) if int(p.get("magic") or 0) == MAGIC]
        if open_here:
            return decide("hold", f"one position at a time: {len(open_here)} already open on magic {MAGIC}")

        symbol = (config.get("symbols") or list(SYMBOLS))[0]
        plan = plan_fn(symbol)
        tradable, why = plan_is_tradable(plan)
        if not tradable:
            return decide("no_setup", why)

        side = "BUY" if str(plan["side"]).lower() in ("long", "buy") else "SELL"
        quote = guarded.quote(symbol) or {}
        if not quote.get("ok"):
            events.append(shared._mt5_error(state, now, f"no live quote ({quote.get('message')})", sink=sink))
            return summary
        price = float(quote["ask" if side == "BUY" else "bid"] or 0.0)
        if price <= 0:
            return decide("refused", f"no tradable price for {symbol}")
        ok, drift_why = entry_drift_ok(plan, price, float(config.get("max_entry_drift_atr", 0.5)))
        if not ok:
            return decide("refused", drift_why)

        stop, target = float(plan["stop_loss"]), float(plan["take_profit"])
        if (side == "BUY" and not stop < price < target) or (side == "SELL" and not target < price < stop):
            return decide("refused", f"levels do not bracket the price: stop {stop}, price {price}, target {target}")

        request = {"symbol": symbol, "side": side, "volume": float(config.get("volume", VOLUME)),
                   "stop_loss": stop, "take_profit": target, "magic": MAGIC,
                   "comment": f"STP {day[2:].replace('-', '')}", "allow_retry_without_stops": False}

        if config.get("dry_run", True):
            return decide("dry_run_order", f"plan passed every gate; dry run: {side} {symbol} "
                                           f"{request['volume']} lot at {price:.2f}, stop {stop:.2f}, "
                                           f"target {target:.2f} logged, NOT sent", request=request)

        result = guarded.place_market_order(**request) or {}
        if not result.get("executed"):
            events.append(shared._mt5_error(state, now, f"order rejected ({result.get('message')})",
                                            sink=sink, request=request))
            return summary
        order = result.get("result") or {}
        ticket = int(order.get("order") or order.get("deal") or 0)
        fill = float(order.get("price") or price)
        state["entries_today"] += 1
        state["trades"][str(ticket)] = {"ticket": ticket, "symbol": symbol, "side": side, "fill": fill,
                                        "stop": stop, "target": target, "opened_at": demo_executor._stamp(now),
                                        "plan_date": plan.get("date"), "status": "open"}
        shared._append_jsonl(plan_paths()["trade_memory"], {
            "strategy": STRATEGY, "magic": MAGIC, "account": shared.DEMO_ACCOUNT_LOGIN, "event": "opened",
            "symbol": symbol, "side": side, "ticket": ticket, "fill": fill, "stop": stop, "target": target,
            "volume": request["volume"], "opened_at": demo_executor._stamp(now),
            "plan_date": plan.get("date"), "plan_headline": plan.get("headline"), "mode": "demo_broker_fill"})
        return decide("opened", f"{side} {symbol} {request['volume']} lot at {fill:.2f}, stop {stop:.2f}, "
                                f"target {target:.2f}, ticket {ticket}", ticket=ticket)
    except shared.AccountRefused as exc:
        events.append(shared._halt(state, now, "account_refused",
                                   f"Refused: {exc}. Only demo account {shared.DEMO_ACCOUNT_LOGIN} may trade.",
                                   sink=sink))
        return summary
    finally:
        state["last_cycle"] = {"at": demo_executor._stamp(now), "decision": summary.get("decision"),
                               "reason": summary.get("reason")}
        save_plan_state(state)


def plan_status() -> dict:
    config = demo_executor.load_config(plan_paths()["config"], DEFAULT_CONFIG)
    state = load_plan_state()
    memory = shared.read_jsonl(plan_paths()["trade_memory"])
    closed = [m for m in memory if m.get("event") == "closed"]
    return {"strategy": STRATEGY, "magic": MAGIC, "demo_account": shared.DEMO_ACCOUNT_LOGIN,
            "config": config, "dry_run": bool(config.get("dry_run", True)),
            "places_orders": not bool(config.get("dry_run", True)) and bool(config.get("enabled")),
            "halted": state.get("halted"), "entries_today": state.get("entries_today"),
            "open_trades": [t for t in (state.get("trades") or {}).values() if t.get("status") == "open"],
            "journalled": len(memory), "closed_trades": len(closed),
            "last_cycle": state.get("last_cycle"),
            "evidence": ("The SwingTrendPullback plan has NOT cleared the 0.95 deflated Sharpe bar and its "
                         "last closed plan trade was a stop at -2.36 %. This executes the plan; it does not "
                         "vouch for it.")}


CYCLE_URL = "http://127.0.0.1:5000/api/demo-plan/cycle"


def main(argv=None) -> int:
    """The APP runs the cycle. Only the app holds the MT5 connection, and a second connection from another
    process restarted the app once - so this asks over HTTP rather than opening its own."""
    import urllib.request
    from datetime import datetime, timezone

    from .execution_guard import SECRET_HEADER, load_or_create_secret

    parser = argparse.ArgumentParser(description="Daily plan demo trading (the app runs the cycle).")
    parser.add_argument("command", choices=["cycle", "status"])
    args = parser.parse_args(argv)

    if args.command == "status":
        print(json.dumps(plan_status(), indent=1, default=str)[:4000])
        return 0

    request = urllib.request.Request(CYCLE_URL, data=b"{}", method="POST",
                                     headers={"Content-Type": "application/json",
                                              SECRET_HEADER: load_or_create_secret()})
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            body = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        print(f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} cycle call failed: {exc}")
        return 1
    print(f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} {body.get('decision')}: {body.get('reason')}")
    return 0


if __name__ == "__main__":
    # Wrapped so this loop cannot finish without a record - see loop_ledger.run_main.
    from .loop_ledger import run_main

    raise SystemExit(run_main("demo_plan", main))
