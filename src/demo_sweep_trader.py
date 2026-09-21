"""The owner's manipulation-candle rule, executed on the demo account. Research evidence, not proven edge.

This is the one candidate that survived a week of testing: the 4H sweep-continuation read the owner's own
way - "if the close above the previous high buy, if the close below sell" - with an EMA400 trend filter.
Under the corrected cost model on XAUUSD 4H it is positive in ALL THREE windows (search +6.33 %,
validation +11.42 %, holdout +26.69 % over 118 trades at PF 1.378), beats its own inverse by 62.3 points,
and has ZERO ambiguous exits in 473 trades, so the sign of every window is read off the data rather than
assigned by the engine's pessimism.

WHAT IT IS NOT. Deflated Sharpe 0.181 against the owner's standing 0.95 bar, which does not move. It does
not beat buy-and-hold over its holdout (+74.0 %), at a far smaller drawdown. The paper forward test
``sweep_continue_xau_4h`` has run since 19 Sep and closed NO trades yet - the rule fires about once a
week, so two days is nothing. This module gives it a way to act on the demo account so the evidence it
accumulates is real fills rather than simulated ones; it does not make the rule good.

SAFETY, in the order applied: demo account 11581419 or nothing (hard-coded in demo_session_pullback, any
other login refuses and halts), ``dry_run`` TRUE by default so it decides and places nothing until the
owner says otherwise, one position at a time, its own magic so its trades are never confused with another
strategy's, a stop and a target attached to every order, and the signal must come from the bar that has
just closed rather than a stale one.

    python -m src.demo_sweep_trader cycle|status
"""
from __future__ import annotations

import argparse
import json
from typing import Callable, Optional

import numpy as np
import pandas as pd

from . import demo_executor
from . import demo_session_pullback as shared
from . import strategy_lab as lab
from . import sweep_reversal
from .runtime_paths import smartentry_data_dir

STRATEGY = "sweep_continuation"
MAGIC = 440805                      # its own: 440502 pullback, 440603 breakout, 440704 daily plan
SYMBOL = "XAUUSD"
TIMEFRAME = "4h"
BAR_MINUTES = 240
VOLUME = 0.01

DEFAULT_CONFIG = {
    "enabled": True,
    # Places NOTHING until the owner sets this false, exactly as the daily plan executor shipped.
    "dry_run": True,
    "symbol": SYMBOL,
    "volume": VOLUME,
    "max_signal_age_minutes": 60,   # the 4H bar must have closed recently; a stale signal is not the trade
    "max_entry_drift_atr": 0.5,
    "max_spread_fraction_of_r": 0.10,
    "note": ("The owner's 4H manipulation-candle rule (continue|lb40|nobody|rr2|ema400) on Vantage DEMO "
             "11581419 only. Deflated Sharpe 0.181 against the unchanged 0.95 bar: this is forward "
             "evidence gathering, not a proven edge."),
}

INITIAL_STATE = {"halted": None, "trades": {}, "last_cycle": None, "last_signal_bar": None}


def sweep_paths() -> dict:
    data = smartentry_data_dir()
    return {"config": data / "paper_trading" / "demo_sweep_trader.json",
            "state": data / "paper_trading" / "demo_sweep_trader_state.json",
            "log": data / "paper_trading" / "demo_sweep_trader.log",
            "trade_memory": data / "trade_memory" / "sweep_continuation_demo.jsonl"}


def _sweep_sink() -> dict:
    return {"path": sweep_paths()["log"]}


def load_sweep_state() -> dict:
    path = sweep_paths()["state"]
    if not path.exists():
        return dict(INITIAL_STATE)
    try:
        return {**INITIAL_STATE, **json.loads(path.read_text(encoding="utf-8"))}
    except (OSError, ValueError):
        return dict(INITIAL_STATE)


def save_sweep_state(state: dict) -> None:
    path = sweep_paths()["state"]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1, default=str), encoding="utf-8")
    tmp.replace(path)


def latest_signal(bars: pd.DataFrame, now) -> Optional[dict]:
    """The rule's verdict on the bar that has just CLOSED, or None.

    Uses sweep_reversal.FORWARD_CANDIDATE unchanged - the spec frozen in code on 19 September and pinned
    by tests - so what trades here is exactly what was measured, not a re-typed copy of it.
    """
    closed = shared.drop_forming_bars(bars, BAR_MINUTES, now) if hasattr(shared, "drop_forming_bars") \
        else _drop_forming(bars, now)
    if closed is None or len(closed) < lab.WARMUP_BARS:
        return None
    ind = lab.Indicators(closed)
    side, stop, target = sweep_reversal.sweep_orders(ind, sweep_reversal.FORWARD_CANDIDATE)
    i = len(side) - 1
    if int(side[i]) == 0 or not np.isfinite(stop[i]) or not np.isfinite(target[i]):
        return None
    return {"side": "BUY" if int(side[i]) == 1 else "SELL",
            "bar_time": lab._iso(ind.times.iloc[i]),
            "close": float(ind.c[i]), "stop": round(float(stop[i]), 2),
            "target": round(float(target[i]), 2), "atr": float(ind.atr(14)[i])}


def _drop_forming(frame: pd.DataFrame, now) -> pd.DataFrame:
    from .paper_trader import drop_forming_bars

    return drop_forming_bars(frame, BAR_MINUTES, now)


def signal_is_fresh(signal: dict, now, max_age_minutes: int) -> tuple[bool, str]:
    """A 4H rule that fires on a bar closed six hours ago is not the trade that was measured."""
    closed_at = pd.Timestamp(signal["bar_time"], tz="UTC") + pd.Timedelta(minutes=BAR_MINUTES)
    age = (pd.Timestamp(now) - closed_at).total_seconds() / 60.0
    if age < 0:
        return False, "the signal bar has not closed yet"
    if age > max_age_minutes:
        return False, (f"the signal bar closed {age:.0f} min ago, beyond {max_age_minutes}; "
                       f"this is a stale signal, not a fresh one")
    return True, f"signal bar closed {age:.0f} min ago"


def sweep_cycle(engine, bars_fn: Callable, now=None, config: Optional[dict] = None) -> dict:
    """One decision. At most one order, demo only, and only when dry_run is false."""
    now = shared.utc_timestamp(now)
    config = config or demo_executor.load_config(sweep_paths()["config"], DEFAULT_CONFIG)
    state = load_sweep_state()
    events: list[dict] = []
    summary = {"at": demo_executor._stamp(now), "strategy": STRATEGY, "magic": MAGIC,
               "dry_run": bool(config.get("dry_run", True)), "events": events}
    sink = _sweep_sink()

    def decide(decision, reason, **details):
        summary.update(decision=decision, reason=reason)
        events.append(shared.log(decision, now, reason, sink=sink, magic=MAGIC, **details))
        return summary

    try:
        if not config.get("enabled"):
            return decide("disabled", "sweep trading is disabled in data/paper_trading/demo_sweep_trader.json")
        if state.get("halted"):
            return decide("halted_skip", f"halted ({state['halted']['kind']}): {state['halted']['reason']}")

        guarded = shared.DemoOnlyEngine(engine, magic=MAGIC, volume=float(config.get("volume", VOLUME)))
        if not (guarded.status() or {}).get("connected"):
            events.append(shared._mt5_error(state, now, "not connected", sink=sink))
            return summary

        symbol = str(config.get("symbol") or SYMBOL)
        open_here = [p for p in (guarded.positions() or []) if int(p.get("magic") or 0) == MAGIC]
        if open_here:
            return decide("hold", f"one position at a time: {len(open_here)} open on magic {MAGIC}")

        frame, source = bars_fn(symbol, TIMEFRAME, 3000)
        if frame is None or getattr(frame, "empty", True) or not str(source or "").startswith("mt5"):
            events.append(shared._mt5_error(state, now, f"no broker bars ({source})", sink=sink))
            return summary

        signal = latest_signal(frame, now)
        if signal is None:
            return decide("no_setup", "no manipulation candle on the last closed 4H bar")
        if signal["bar_time"] == state.get("last_signal_bar"):
            return decide("hold", f"already handled the signal on {signal['bar_time']}")
        fresh, why = signal_is_fresh(signal, now, int(config.get("max_signal_age_minutes", 60)))
        if not fresh:
            state["last_signal_bar"] = signal["bar_time"]
            return decide("refused", why)

        quote = guarded.quote(symbol) or {}
        if not quote.get("ok"):
            events.append(shared._mt5_error(state, now, f"no live quote ({quote.get('message')})", sink=sink))
            return summary
        price = float(quote["ask" if signal["side"] == "BUY" else "bid"])
        risk = abs(signal["close"] - signal["stop"])
        if risk <= 0:
            return decide("refused", "the signal has no risk distance")
        drift = abs(price - signal["close"]) / max(1e-9, signal["atr"])
        if drift > float(config.get("max_entry_drift_atr", 0.5)):
            return decide("refused", f"price has moved {drift:.2f} ATR from the signal close; "
                                     f"this is no longer the measured trade")
        spread = float(quote["ask"]) - float(quote["bid"])
        if spread > float(config.get("max_spread_fraction_of_r", 0.10)) * risk:
            return decide("refused", f"spread {spread:.2f} is above "
                                     f"{config['max_spread_fraction_of_r']:.0%} of the {risk:.2f} risk")

        request = {"symbol": symbol, "side": signal["side"], "volume": float(config.get("volume", VOLUME)),
                   "stop_loss": signal["stop"], "take_profit": signal["target"], "magic": MAGIC,
                   "comment": f"SWP {signal['bar_time'][2:10].replace('-', '')}",
                   "allow_retry_without_stops": False}
        state["last_signal_bar"] = signal["bar_time"]

        if config.get("dry_run", True):
            return decide("dry_run_order", f"rule fired and passed every gate; dry run: {signal['side']} "
                                           f"{symbol} at {price:.2f}, stop {signal['stop']:.2f}, target "
                                           f"{signal['target']:.2f} logged, NOT sent", request=request)

        result = guarded.place_market_order(**request) or {}
        if not result.get("executed"):
            events.append(shared._mt5_error(state, now, f"order rejected ({result.get('message')})",
                                            sink=sink, request=request))
            return summary
        order = result.get("result") or {}
        ticket = int(order.get("order") or order.get("deal") or 0)
        fill = float(order.get("price") or price)
        state["trades"][str(ticket)] = {"ticket": ticket, "symbol": symbol, "side": signal["side"],
                                        "fill": fill, "stop": signal["stop"], "target": signal["target"],
                                        "signal_bar": signal["bar_time"], "status": "open",
                                        "opened_at": demo_executor._stamp(now)}
        shared._append_jsonl(sweep_paths()["trade_memory"], {
            "strategy": STRATEGY, "magic": MAGIC, "account": shared.DEMO_ACCOUNT_LOGIN, "event": "opened",
            "symbol": symbol, "side": signal["side"], "ticket": ticket, "fill": fill,
            "stop": signal["stop"], "target": signal["target"], "volume": request["volume"],
            "signal_bar": signal["bar_time"], "opened_at": demo_executor._stamp(now),
            "mode": "demo_broker_fill"})
        return decide("opened", f"{signal['side']} {symbol} {request['volume']} lot at {fill:.2f}, "
                                f"stop {signal['stop']:.2f}, target {signal['target']:.2f}, "
                                f"ticket {ticket}", ticket=ticket)
    except shared.AccountRefused as exc:
        events.append(shared._halt(state, now, "account_refused",
                                   f"Refused: {exc}. Only demo account {shared.DEMO_ACCOUNT_LOGIN} may trade.",
                                   sink=sink))
        return summary
    finally:
        state["last_cycle"] = {"at": demo_executor._stamp(now), "decision": summary.get("decision"),
                               "reason": summary.get("reason")}
        save_sweep_state(state)


def sweep_status() -> dict:
    config = demo_executor.load_config(sweep_paths()["config"], DEFAULT_CONFIG)
    state = load_sweep_state()
    memory = shared.read_jsonl(sweep_paths()["trade_memory"])
    return {"strategy": STRATEGY, "magic": MAGIC, "demo_account": shared.DEMO_ACCOUNT_LOGIN,
            "symbol": config.get("symbol", SYMBOL), "timeframe": TIMEFRAME,
            "rule": sweep_reversal.FORWARD_VARIANT, "config": config,
            "dry_run": bool(config.get("dry_run", True)),
            "places_orders": not bool(config.get("dry_run", True)) and bool(config.get("enabled")),
            "halted": state.get("halted"), "last_cycle": state.get("last_cycle"),
            "open_trades": [t for t in (state.get("trades") or {}).values() if t.get("status") == "open"],
            "journalled": len(memory),
            "evidence": ("Positive in all three windows on XAUUSD 4H (+6.33 / +11.42 / +26.69 %), PF 1.378 "
                         "on 118 holdout trades, beats its own inverse by 62.3 points, zero ambiguous "
                         "exits. Deflated Sharpe 0.181 against the unchanged 0.95 bar - NOT a proven edge.")}


CYCLE_URL = "http://127.0.0.1:5000/api/demo-sweep/cycle"


def main(argv=None) -> int:
    """The APP runs the cycle: it holds the only MT5 connection."""
    import urllib.request
    from datetime import datetime, timezone

    from .execution_guard import SECRET_HEADER, load_or_create_secret

    parser = argparse.ArgumentParser(description="Manipulation-candle demo trading (the app runs the cycle).")
    parser.add_argument("command", choices=["cycle", "status"])
    args = parser.parse_args(argv)

    if args.command == "status":
        print(json.dumps(sweep_status(), indent=1, default=str)[:4000])
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
    raise SystemExit(main())
