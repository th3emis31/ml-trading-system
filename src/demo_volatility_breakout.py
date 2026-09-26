"""Volatility Trend Breakout on the Vantage DEMO account 11581419, beside the gold session pullback.

Owner decisions, 2026-09-17: send demo orders now, run side by side with the pullback (each strategy has its own magic
number, position limit and kill switches), fixed 0.01-lot legs.

Rules: the owner's Pine v5 "Volatility Trend Breakout - Best Version", through the validated port
src/volatility_trend_breakout.py (same signal function, default inputs):
- 4H XAUUSD, long only. A signal on a closed 4H candle: close > previous 20-bar high + 0.35 x ATR14, close > EMA50,
  RSI14 > 52, volume > SMA20 of volume.
- Stop = signal close - 1.5 x ATR; TP1 = close + 1.3R; TP2 = close + 2.8R.
- Two 0.01-lot legs (the script closes 50 % at TP1): leg A with its take-profit at TP1, leg B at TP2, both with the stop
  attached at entry. After leg A fills, leg B's stop goes to entry + 0.15 x ATR and then trails 2.2 x ATR below the
  highest high since entry (current ATR, as the script). Everything closes after 65 closed 4H candles.
Two deliberate differences from the chart, both measured or safer: TP1 is a real limit order at TP1 (the script fills
it at the bar close; the port measured PF 1.926 vs 1.952, immaterial), and the stop is on the broker from the first
second (the script's first protective exit only exists one bar after entry).

Gates before an entry, each refusal logged: the signal candle closed at most ``max_signal_delay_minutes`` ago, price has
not run more than ``max_drift_atr`` x ATR from the signal close, spread <= ``max_spread_fraction_of_r`` x R, tier-1 event
window, H1 volatility breaker, 30-minute High-impact news window, one position at a time, kill switches.
Kill switches measure this strategy's own P&L (magic 440603): -3 % of the day's start stops entries for the UTC day,
15 % drawdown halts; any MT5 error halts. The account guard and logging are the pullback's (src/demo_session_pullback.py).

The app runs the cycle (POST /api/demo-breakout/cycle, control secret); the hourly task SmartEntry Demo Breakout calls it.
"""
from __future__ import annotations

import copy
import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import pandas as pd

from . import demo_executor, paper_trader
from . import demo_session_pullback as shared
from . import volatility_trend_breakout as vtb
from .event_defence import breaker_status, tier1_window, utc_timestamp
from .runtime_paths import smartentry_data_dir

MAGIC = 440603                     # unused on the account (its 32 magics checked 2026-09-16; 440502 is the pullback)
SYMBOL = "XAUUSD"                 # the first market this strategy traded; kept for callers that ask for one symbol
# Bitcoin was added on 2026-09-18 after an out-of-sample test of the owner's own settings on BTCUSD 4H broker candles:
# 248 trades over 8.7 years, PF 1.232, +26.7 %, max drawdown 12.7 % - thinner than gold's recent window but positive on
# a market these settings were never fitted to, and it fires 28.5 times a year against gold's 14.9 (BASELINE.md).
SYMBOLS = ("XAUUSD", "BTCUSD")
VOLUME_PER_LEG = 0.01
STRATEGY = "volatility_trend_breakout"
H4_BARS = 1500
H4_MINUTES = 240
RULES = vtb.Config()               # the script's default inputs
CYCLE_URL = "http://127.0.0.1:5000/api/demo-breakout/cycle"

DEFAULT_CONFIG = {
    "enabled": False,
    "dry_run": True,
    "daily_loss_limit": 0.03,
    "halt_drawdown": 0.15,
    "max_signal_delay_minutes": 45,
    "max_drift_atr": 0.5,
    "max_spread_fraction_of_r": 0.10,
    "high_impact_news_window": True,
}

INITIAL_STATE = {"halted": None, "day": None, "day_stopped": None, "attempted": [], "trades": {},
                 "equity": 1.0, "peak_equity": 1.0, "day_start_equity": 1.0, "start_balance": None, "closed_money": 0.0}


def breakout_paths() -> dict:
    data = smartentry_data_dir()
    return {"config": data / "paper_trading" / "demo_volatility_breakout.json",
            "state": data / "paper_trading" / "demo_volatility_breakout_state.json",
            "log": data / "paper_trading" / "demo_volatility_breakout_log.jsonl",
            "trade_memory": data / "trade_memory" / "volatility_breakout_demo.jsonl"}


def _sink() -> dict:
    return {"log": breakout_paths()["log"], "magic": MAGIC}


def load_breakout_state() -> dict:
    path = breakout_paths()["state"]
    return paper_trader.load_state(path) if path.exists() else copy.deepcopy(INITIAL_STATE)


def save_breakout_state(state: dict) -> None:
    state["attempted"] = state.get("attempted", [])[-100:]
    paper_trader.save_state(state, breakout_paths()["state"])


def _candles(frame: pd.DataFrame) -> list:
    rows = frame.sort_values("datetime").reset_index(drop=True)
    return [vtb.Candle(ts=pd.Timestamp(r.datetime).strftime("%Y-%m-%d %H:%M"), open=float(r.open), high=float(r.high),
                       low=float(r.low), close=float(r.close), volume=float(r.volume)) for r in rows.itertuples(index=False)]


# The slow EMA exists only to classify the regime, never to gate an entry. Adding it to the JOURNAL is
# additive; adding it to the signal would be the naive filter the NEVER-BLOCK rule forbids.
REGIME_SLOW_EMA = 200


def indicator_values(candles: list) -> dict:
    """ATR, EMA and RSI of the last closed candle, with the same Pine-matching functions as the signal."""
    closes = [c.close for c in candles]
    return {"atr": vtb.atr(candles, RULES.atr_len)[-1], "ema": vtb.ema(closes, RULES.ema_len)[-1],
            "rsi": vtb.rsi(closes, RULES.rsi_len)[-1],
            "ema_slow": vtb.ema(closes, REGIME_SLOW_EMA)[-1] if len(closes) >= 2 else None}


def regime_phase(ema_fast, ema_slow, atr_value) -> Optional[str]:
    """"trending" or "ranging", by the rule declared on 26 September 2026 BEFORE the split was measured:
    trending when the EMA50/EMA200 gap exceeds one ATR, ranging otherwise.

    Recorded on every trade so the live account can eventually confirm or kill the backtest finding that
    this breakout earns three to four times more per trade in ranging conditions (+0.309 vs +0.096 R on
    gold, +0.384 vs +0.099 on bitcoin, two markets independently). That split was chosen on full history,
    so it is a HYPOTHESIS; only forward trades carrying this label can settle it.

    It changes no decision. The strategy takes exactly the trades it took before.
    """
    if ema_fast is None or ema_slow is None or not atr_value:
        return None
    return "trending" if abs(float(ema_fast) - float(ema_slow)) > float(atr_value) else "ranging"


# ----------------------------------------------------------------------------------------------- open trade
def manage_breakout_trade(engine, state: dict, trade: dict, candles: list, config: dict, now) -> list[dict]:
    """Break-even and trail for leg B after leg A's take-profit, 65-candle time exit, settlement from broker deals."""
    sink, events = _sink(), []
    positions = engine.positions(symbol=SYMBOL, magic=MAGIC)
    if positions is None:
        return [shared._mt5_error(state, now, "could not read open positions", sink=sink, trade_id=trade["id"])]
    live = {int(p["ticket"]): p for p in positions}
    legs = {leg["leg"]: leg for leg in trade["legs"]}
    open_legs = [leg for leg in trade["legs"] if int(leg["ticket"]) in live]

    deals = {}
    if len(open_legs) < len(trade["legs"]):
        history = engine.deal_history(days=30) or {}
        if not history.get("ok"):
            return [shared._mt5_error(state, now, f"deal history unreadable ({history.get('reason')})", sink=sink,
                                      trade_id=trade["id"])]
        for deal in history.get("deals") or []:
            deals.setdefault(int(deal["position_id"]), []).append(deal)

    # Record money a leg has already banked, for reporting only. state["closed_money"] still moves
    # only when the whole signal settles below, and the trade count is still one per SIGNAL - this
    # writes nothing the settlement reads. Without it the page showed +45.38 while the broker held
    # +61.34, because a leg had banked +15.28 and its sibling runner was still open (22 Sep 2026).
    for leg in trade.get("legs") or []:
        if int(leg["ticket"]) in live or leg.get("realised") is not None:
            continue
        leg_deals = deals.get(int(leg["ticket"])) or []
        exits = [d for d in leg_deals if float(d.get("net") or 0.0) or d.get("comment")]
        if leg_deals and exits:
            leg["realised"] = round(sum(float(d.get("net") or 0.0) for d in leg_deals), 2)

    since = [c for c in candles if c.ts >= trade["signal_bar"]]
    bars_after_entry = max(0, len(since) - 1)
    a_deals = deals.get(int(legs["A"]["ticket"])) or []
    tp1_taken = bool(a_deals) and a_deals[-1]["price"] > trade["entry"]
    leg_b = legs["B"]

    if tp1_taken and int(leg_b["ticket"]) in live and since:
        atr_now = indicator_values(candles)["atr"] or trade["atr"]
        be_stop = float(trade["setup"]["signal_close"]) + RULES.be_atr * atr_now   # the script's entryPrice is the signal close
        trail = max(c.high for c in since) - RULES.trail_atr * atr_now
        wanted = round(max(be_stop, trail) if RULES.use_trail else be_stop, 2)
        current = float(live[int(leg_b["ticket"])].get("sl") or 0.0)
        if wanted > current + 0.01:
            result = engine.modify_position_sltp(int(leg_b["ticket"]), stop_loss=wanted) or {}
            if not result.get("executed"):
                return events + [shared._mt5_error(state, now, f"stop change for leg B refused ({result.get('message')})",
                                                   sink=sink, trade_id=trade["id"])]
            trade["leg_b_stop"] = wanted
            events.append(shared.log("stop_moved", now, f"leg B stop {current} -> {wanted} (break-even +0.15 ATR / "
                                     f"trail 2.2 ATR below the high since entry)", sink=sink, trade_id=trade["id"]))

    if open_legs and RULES.use_time_exit and bars_after_entry >= RULES.max_bars:
        for leg in open_legs:
            result = engine.close_position(int(leg["ticket"]), comment=f"VTB time exit {leg['leg']}") or {}
            if not result.get("executed"):
                return events + [shared._mt5_error(state, now, f"time-exit close refused for ticket {leg['ticket']} "
                                                   f"({result.get('message')})", sink=sink, trade_id=trade["id"])]
            events.append(shared.log("time_exit", now, f"{bars_after_entry} closed 4H candles since entry (max "
                                     f"{RULES.max_bars})", sink=sink, trade_id=trade["id"], ticket=leg["ticket"]))
        return events

    if not open_legs:
        parts, r_total, money = [], 0.0, 0.0
        for leg in trade["legs"]:
            leg_deals = deals.get(int(leg["ticket"])) or []
            if not leg_deals:
                return events            # the broker has not reported the exit deal yet
            exit_price = float(leg_deals[-1]["price"])
            leg_r = (exit_price - float(leg["fill"])) / float(trade["r_price"])
            net = sum(float(d["net"]) for d in leg_deals)
            r_total += 0.5 * leg_r
            money += net
            parts.append({"leg": leg["leg"], "ticket": leg["ticket"], "fill": leg["fill"], "exit_price": exit_price,
                          "r": round(leg_r, 4), "net_money": round(net, 2), "comment": leg_deals[-1].get("comment")})
        trade.update(status="closed", closed_at=demo_executor._stamp(now), r_result=round(r_total, 4),
                     net_money=round(money, 2), exits=parts)
        state["closed_money"] = float(state.get("closed_money") or 0.0) + money
        shared._append_jsonl(breakout_paths()["trade_memory"], {
            "strategy": STRATEGY, "magic": MAGIC, "account": shared.DEMO_ACCOUNT_LOGIN,
            "symbol": trade.get("symbol", SYMBOL),
            "event": "closed",
            "mode": "demo_broker_fill", "trade_id": trade["id"], "side": "BUY", "opened_at": trade["opened_at"],
            "closed_at": trade["closed_at"], "setup": trade["setup"], "session": trade["session"], "regime": trade["regime"],
            "next_event": trade["next_event"], "minutes_to_next_tier1_event": trade["minutes_to_next_tier1_event"],
            "legs": parts, "r_result": trade["r_result"], "net_money": trade["net_money"]})
        events.append(shared.log("trade_closed", now, f"trade {trade['id']} closed at {r_total:+.2f}R ({money:+.2f})",
                                 sink=sink, trade_id=trade["id"], r_result=trade["r_result"], net_money=trade["net_money"]))
    return events


# ----------------------------------------------------------------------------------------------- the cycle
def breakout_cycle(engine, bars_fn: Callable, calendar_events: list[dict], now=None, config: Optional[dict] = None) -> dict:
    """One hourly decision. ``bars_fn(symbol, timeframe, count) -> (frame, source)``; only MT5 bars are accepted."""
    now = utc_timestamp(now)
    config = config or demo_executor.load_config(breakout_paths()["config"], DEFAULT_CONFIG)
    state = load_breakout_state()
    events: list[dict] = []
    summary = {"at": demo_executor._stamp(now), "strategy": STRATEGY, "dry_run": bool(config.get("dry_run", True)),
               "events": events}
    try:
        guarded = shared.DemoOnlyEngine(engine, magic=MAGIC, volume=VOLUME_PER_LEG)
        # One pass per market. Each keeps its own decision so the page and the log say which market it was about;
        # the kill switches and the halt state stay shared, because they protect the account, not one symbol.
        wanted = tuple(config.get("symbols") or SYMBOLS)
        per_symbol = {}
        for market in wanted:
            leg_events: list[dict] = []
            leg_summary = {"at": demo_executor._stamp(now), "strategy": STRATEGY, "symbol": market,
                           "dry_run": bool(config.get("dry_run", True)), "events": leg_events}
            _breakout_cycle(guarded, bars_fn, calendar_events, now, config, state, leg_events, leg_summary, market)
            events.extend(leg_events)
            per_symbol[market] = {"decision": leg_summary.get("decision"), "reason": leg_summary.get("reason")}
            if state.get("halted"):
                break
        summary["per_symbol"] = per_symbol
        acted = next((m for m, r in per_symbol.items() if r.get("decision") not in (None, "no_setup", "hold")), None)
        chosen = per_symbol.get(acted or wanted[0], {})
        summary.update(decision=chosen.get("decision"), reason=chosen.get("reason"),
                       symbol=acted or wanted[0])
        return summary
    except shared.AccountRefused as exc:
        events.append(shared._halt(state, now, "account_refused", f"Refused: {exc}. Only demo account "
                                   f"{shared.DEMO_ACCOUNT_LOGIN} may trade.", sink=_sink()))
        return summary
    finally:
        state["last_cycle"] = {"at": demo_executor._stamp(now), "decision": summary.get("decision"),
                               "reason": summary.get("reason")}
        save_breakout_state(state)


def _breakout_cycle(engine, bars_fn, calendar_events, now, config, state, events, summary, symbol=SYMBOL) -> dict:
    sink = _sink()

    def decide(decision, reason, **details):
        summary.update(decision=decision, reason=reason)
        events.append(shared.log(decision, now, reason, sink=sink, **details))
        return summary

    if not config.get("enabled"):
        return decide("disabled", "volatility breakout demo trading is disabled in data/paper_trading/demo_volatility_breakout.json")
    if state.get("halted"):
        return decide("halted_skip", f"halted ({state['halted']['kind']}): {state['halted']['reason']}")
    if not (engine.status() or {}).get("connected"):
        events.append(shared._mt5_error(state, now, "not connected", sink=sink))
        return summary
    account = engine.account_snapshot()
    if not account:
        events.append(shared._mt5_error(state, now, "account details unreadable", sink=sink))
        return summary
    ok, account_reason = shared.check_demo_account(account)
    if not ok:
        raise shared.AccountRefused(account_reason)

    today = now.strftime("%Y-%m-%d")
    if state.get("day") != today:
        state.update(day=today, day_stopped=None, day_start_equity=float(state.get("live_equity") or 1.0))

    frame, source = bars_fn(symbol, "4h", H4_BARS)
    if frame is None or frame.empty or not str(source or "").startswith("mt5"):
        events.append(shared._mt5_error(state, now, f"no broker 4H bars (source {source})", sink=sink))
        return summary
    frame = frame.copy()
    frame["datetime"] = pd.to_datetime(frame["datetime"], utc=True)
    closed = paper_trader.drop_forming_bars(frame, H4_MINUTES, now).sort_values("datetime").reset_index(drop=True)
    candles = _candles(closed)

    for trade in [t for t in state["trades"].values() if t["status"] == "open"]:
        events.extend(manage_breakout_trade(engine, state, trade, candles, config, now))
        if state.get("halted"):
            return summary
    floating = 0.0
    if any(t["status"] == "open" for t in state["trades"].values()):
        positions = engine.positions(symbol=symbol, magic=MAGIC)
        if positions is None:
            events.append(shared._mt5_error(state, now, "could not read open positions", sink=sink))
            return summary
        floating = sum(float(p.get("profit") or 0.0) for p in positions)
    events.extend(shared.update_kill_switches(state, config, account, floating, now, sink=sink))
    if state.get("halted"):
        return summary

    if any(t["status"] == "open" and t.get("symbol", SYMBOL) == symbol for t in state["trades"].values()):
        return decide("hold", f"one position at a time: a breakout trade is open on {symbol}")
    if state.get("day_stopped"):
        return decide("no_entry", f"daily loss stop: {state['day_stopped']}")
    if len(candles) < 250:
        return decide("no_entry", f"only {len(candles)} closed 4H candles")
    last = candles[-1]
    last_close_time = pd.Timestamp(last.ts, tz="UTC") + pd.Timedelta(minutes=H4_MINUTES)
    age = (now - last_close_time).total_seconds() / 60
    signals = {s.index: s for s in vtb.generate_signals(candles, RULES)}
    signal = signals.get(len(candles) - 1)
    if signal is None:
        return decide("no_setup", f"no breakout on the {symbol} 4H candle {last.ts} UTC")
    # Two markets share the same 4H candle times, so the id and the "already handled" guard carry the symbol.
    trade_key = f"{symbol} {last.ts}"
    base = {"trade_id": trade_key, "signal_bar": last.ts, "symbol": symbol, "side": "BUY"}
    if trade_key in state.get("attempted", []):
        return decide("refused", f"signal candle {last.ts} on {symbol} was already handled", **base)
    state.setdefault("attempted", []).append(trade_key)
    if age > float(config["max_signal_delay_minutes"]):
        return decide("refused", f"signal candle closed {age:.0f} min ago (limit {config['max_signal_delay_minutes']})", **base)

    tier1 = shared.all_tier1_events(calendar_events)
    window = tier1_window(tier1, now.to_pydatetime(), symbol)
    next_event = window.get("next_event")
    minutes_to_next = round((utc_timestamp(next_event["time_utc"]) - now).total_seconds() / 60) if next_event else None
    ind = indicator_values(candles)
    context = {"session": shared.session_name(now.hour),
               "regime": {"trend": "above EMA50" if last.close > (ind["ema"] or 0) else "below EMA50",
                          "ema50": round(ind["ema"], 2) if ind["ema"] else None, "rsi14": round(ind["rsi"], 1) if ind["rsi"] else None,
                          "atr14": round(signal.atr, 2),
                          # added 26 Sep 2026: the trending/ranging label the backtest finding needs
                          "ema200": round(ind["ema_slow"], 2) if ind.get("ema_slow") else None,
                          "phase": regime_phase(ind.get("ema"), ind.get("ema_slow"), signal.atr),
                          "phase_rule": "trending when |EMA50 - EMA200| > 1 ATR, else ranging"},
               "next_event": next_event, "minutes_to_next_tier1_event": minutes_to_next}
    if window["entries_blocked"]:
        return decide("refused", "tier-1 event window: " + ", ".join(f"{e['event']} {e['time_utc']}" for e in window["blocking_events"]),
                      **base, **context)
    h1, h1_source = bars_fn(symbol, "1h", 60)
    if h1 is None or h1.empty or not str(h1_source or "").startswith("mt5"):
        events.append(shared._mt5_error(state, now, f"no broker H1 bars for the volatility breaker (source {h1_source})", sink=sink))
        return summary
    h1 = h1.copy()
    h1["datetime"] = pd.to_datetime(h1["datetime"], utc=True)
    breaker = breaker_status(paper_trader.drop_forming_bars(h1, 60, now))
    if breaker.get("entries_blocked"):
        return decide("refused", f"volatility breaker: H1 range {breaker['trigger']['range']} > 3 x ATR14 at "
                                 f"{breaker['trigger']['bar_utc']}", **base, **context)
    if config.get("high_impact_news_window", True):
        from .economic_calendar import news_window
        news = news_window(calendar_events or [], symbol, now.to_pydatetime())
        if news.get("in_window"):
            return decide("refused", "High-impact news window: " + ", ".join(str(e.get("title")) for e in news["events"]),
                          **base, **context)

    quote = engine.quote(symbol) or {}
    if not quote.get("ok"):
        events.append(shared._mt5_error(state, now, f"no live quote ({quote.get('message')})", sink=sink, **base))
        return summary
    bid, ask = float(quote["bid"]), float(quote["ask"])
    r_price = signal.entry - signal.stop
    setup = {"signal_bar": last.ts, "signal_close": signal.entry, "stop": round(signal.stop, 2), "tp1": round(signal.tp1, 2),
             "tp2": round(signal.tp2, 2), "r_price": round(r_price, 3), "atr14": round(signal.atr, 3), "ask": ask,
             "spread": round(ask - bid, 3), "reason": signal.reason}
    if abs(ask - signal.entry) > float(config["max_drift_atr"]) * signal.atr:
        return decide("refused", f"price moved {ask - signal.entry:+.2f} from the signal close (limit "
                                 f"{config['max_drift_atr']} x ATR = {config['max_drift_atr'] * signal.atr:.2f})",
                      **base, setup=setup, **context)
    if ask - bid > float(config["max_spread_fraction_of_r"]) * r_price:
        return decide("refused", f"spread {ask - bid:.2f} is above {config['max_spread_fraction_of_r']:.0%} of R {r_price:.2f}",
                      **base, setup=setup, **context)
    if not ask < signal.tp1:
        return decide("refused", f"ask {ask} is already at or above TP1 {signal.tp1:.2f}", **base, setup=setup, **context)

    trade = {"id": trade_key, "symbol": symbol, "side": "BUY", "status": "open", "dry_run": bool(config.get("dry_run", True)),
             "opened_at": demo_executor._stamp(now), "signal_bar": last.ts, "entry": round(signal.entry, 2),
             "stop": setup["stop"], "r_price": setup["r_price"], "atr": signal.atr, "setup": setup, **context, "legs": []}
    if trade["dry_run"]:
        state["trades"][trade["id"]] = {**trade, "status": "dry_run_logged"}
        return decide("dry_run_order", "breakout passed every gate; dry run: 2 x 0.01 lot orders logged, not sent",
                      **base, setup=setup, **context)

    placed = []
    for leg, target in (("A", setup["tp1"]), ("B", setup["tp2"])):
        request = {"symbol": symbol, "side": "BUY", "volume": VOLUME_PER_LEG, "stop_loss": setup["stop"], "take_profit": target,
                   "comment": f"VTB {last.ts[2:4]}{last.ts[5:7]}{last.ts[8:10]}{last.ts[11:13]} {leg}", "magic": MAGIC,
                   "allow_retry_without_stops": False}
        result = engine.place_market_order(**request) or {}
        if not result.get("executed"):
            for done in placed:
                engine.close_position(int(done["ticket"]), comment=f"VTB undo {done['leg']}")
            events.append(shared._mt5_error(state, now, f"order for leg {leg} rejected ({result.get('message')})", sink=sink,
                                            **base, request=request, undone=[d["ticket"] for d in placed]))
            return summary
        order = result.get("result") or {}
        placed.append({"leg": leg, "ticket": int(order.get("order") or order.get("deal") or 0),
                       "fill": float(order.get("price") or ask), "target": target, "request": request})
        events.append(shared.log("order_sent", now, f"BUY leg {leg} 0.01 lot sent: breakout passed every gate", sink=sink,
                                 **base, request=request, ticket=placed[-1]["ticket"], **context))
    trade["legs"] = placed
    trade["entry"] = round(sum(p["fill"] for p in placed) / len(placed), 2)
    trade["r_price"] = round(trade["entry"] - setup["stop"], 3)
    state["trades"][trade["id"]] = trade
    # Journal the OPEN, not only the close. Until 20 Sep 2026 only closes were written here, so a live
    # position existed nowhere durable: the BTCUSD trade opened 18 Sep left a single log line reading
    # "BUY opened: 2 legs" - no symbol, no price, no size, no stop, no target, no ticket. Had the state
    # file been lost the trade could not have been reconstructed. Readers of this journal select on
    # r_result being present, so these rows are correctly ignored when counting closed trades.
    shared._append_jsonl(breakout_paths()["trade_memory"], {
        "strategy": STRATEGY, "magic": MAGIC, "account": shared.DEMO_ACCOUNT_LOGIN, "event": "opened",
        "symbol": symbol, "mode": "demo_broker_fill", "trade_id": trade["id"], "side": "BUY",
        "opened_at": trade["opened_at"], "signal_bar": trade["signal_bar"], "entry": trade["entry"],
        "stop": setup["stop"], "r_price": trade["r_price"], "atr": signal.atr,
        "volume_per_leg": VOLUME_PER_LEG, "total_volume": round(VOLUME_PER_LEG * len(placed), 4),
        "targets": {p["leg"]: p["target"] for p in placed},
        "tickets": [p["ticket"] for p in placed], "legs": placed, "setup": setup, **context})
    summary.update(decision="opened",
                   reason=(f"BUY {symbol} opened: {len(placed)} x {VOLUME_PER_LEG} lot at {trade['entry']}, "
                           f"stop {setup['stop']}, targets "
                           + " / ".join(str(p["target"]) for p in placed)
                           + ", tickets " + ", ".join(str(p["ticket"]) for p in placed)))
    return summary


def _positions_all_markets(engine, markets=None) -> list[dict]:
    """This strategy's open legs across every market it runs, one row per ticket.

    A ticket is unique, so asking each market in turn and keying on the ticket is safe even when a bridge ignores the
    symbol filter and answers with everything.
    """
    found: dict[int, dict] = {}
    for market in (markets or SYMBOLS):
        for position in engine.positions(symbol=market, magic=MAGIC) or []:
            found[int(position.get("ticket") or 0)] = position
    return list(found.values())


# ----------------------------------------------------------------------------------------------- owner controls
def stop_breakout(engine, now=None) -> dict:
    """STOP: halt and close this strategy's open legs (magic 440603 only)."""
    now = utc_timestamp(now)
    state = load_breakout_state()
    closed, failures, positions = [], [], None
    if engine is not None and (engine.status() or {}).get("connected"):
        # STOP must close this strategy's legs in every market it runs, not only the first
        positions = _positions_all_markets(engine)
    guarded = shared.DemoOnlyEngine(engine, magic=MAGIC, volume=VOLUME_PER_LEG) if engine is not None else None
    for position in positions or []:
        try:
            result = guarded.close_position(int(position["ticket"]), comment="VTB owner STOP") or {}
        except shared.AccountRefused as exc:
            result = {"executed": False, "message": str(exc)}
        (closed if result.get("executed") else failures).append({"ticket": position["ticket"], "message": result.get("message")})
    event = shared._halt(state, now, "owner_stop", "Owner pressed STOP for the volatility breakout on /demo-trading.",
                         sink=_sink(), closed=closed, close_failures=failures, positions_readable=positions is not None)
    save_breakout_state(state)
    return event


def resume_breakout(now=None) -> dict:
    now = utc_timestamp(now)
    state = load_breakout_state()
    previous = state.get("halted")
    state["halted"] = None
    if (previous or {}).get("kind") == "drawdown":
        state["peak_equity"] = float(state.get("live_equity") or 1.0)
    event = shared.log("resumed", now, "Owner resumed the volatility breakout on /demo-trading.", sink=_sink(),
                       previous_halt=previous)
    save_breakout_state(state)
    return event


def _money_view(state: dict, positions: Optional[list] = None) -> dict:
    """Realised money, including legs already banked by a signal that has not finished.

    Why this exists. ``state["closed_money"]`` only moves when a whole signal resolves - both the TP1
    leg and the runner - which is correct for the TRADE COUNT and wrong for the money. Measured on
    22 September 2026: the broker held +61.34 realised across three closed legs while the page showed
    +45.38, because position 2060045161 banked +15.28 and its sibling runner was still open. A
    strategy built around a TP1 leg plus a runner has a runner open most of the time, so the figure
    understated a working strategy by 26 % and would keep doing so.

    NOTHING ABOUT THE TRADE COUNT CHANGES. A trade is still one SIGNAL, recorded only when every leg
    has resolved, with R weighted 0.5 per leg. Counting legs as trades would turn two signals into
    four and inflate the sample - the same distortion as a partial take-profit scoring as a win, which
    is what produces a flattering win rate in vendor scripts. The ladder still needs five real
    signals; this only reports money that is already in the account.

    ``banked_open`` is summed from ``leg["realised"]``, written by the manage step when it sees a
    leg's exit deal. It is read-only: no state is recomputed and no existing value is altered.
    """
    settled = float(state.get("closed_money") or 0.0)
    banked_open = 0.0
    for trade in (state.get("trades") or {}).values():
        if trade.get("status") == "closed":
            continue
        for leg in trade.get("legs") or []:
            if leg.get("realised") is not None:
                banked_open += float(leg["realised"])
    floating = sum(float(p.get("profit") or 0.0) for p in (positions or []))
    return {"settled_trades": round(settled, 2),
            "banked_open_legs": round(banked_open, 2),
            "realised_total": round(settled + banked_open, 2),
            "floating": round(floating, 2),
            "including_open": round(settled + banked_open + floating, 2),
            "note": ("realised_total is money in the account; settled_trades counts only signals whose "
                     "every leg has closed, which is the unit the evidence ladder uses")}


def breakout_status(engine=None, now=None) -> dict:
    now = utc_timestamp(now)
    config = demo_executor.load_config(breakout_paths()["config"], DEFAULT_CONFIG)
    state = load_breakout_state()
    memory = shared.read_jsonl(breakout_paths()["trade_memory"])
    r = [float(m["r_result"]) for m in memory if m.get("r_result") is not None]
    positions = account = None
    if engine is not None and (engine.status() or {}).get("connected"):
        # every market this strategy runs, not just the first one
        positions = _positions_all_markets(engine, config.get("symbols"))
        account = engine.account_snapshot()
    today = now.strftime("%Y-%m-%d")
    trades = state.get("trades") or {}
    return {
        "strategy": STRATEGY, "magic": MAGIC, "demo_account": shared.DEMO_ACCOUNT_LOGIN,
        "symbol": ", ".join(config.get("symbols") or SYMBOLS), "symbols": list(config.get("symbols") or SYMBOLS),
        "timeframe": "4H",
        "volume_per_leg": VOLUME_PER_LEG, "config": config,
        "sending_orders": bool(config.get("enabled")) and not config.get("dry_run", True),
        "halted": state.get("halted"), "day_stopped": state.get("day_stopped"),
        "kill_switches": {"daily_loss_limit": config["daily_loss_limit"], "halt_drawdown": config["halt_drawdown"],
                          "day_change": state.get("day_change"), "drawdown": state.get("drawdown"),
                          "measured_on": f"this strategy's P&L only (magic {MAGIC})"},
        "account": demo_executor.account_view(account, positions),
        "cycle_health": demo_executor.cycle_health(state.get("last_cycle"), 60, now),
        "open_trade": next((t for t in trades.values() if t["status"] == "open"), None),
        "open_positions": positions,
        "today_trades": [t for t in trades.values() if str(t.get("opened_at", "")).startswith(today)],
        "expectancy": {"trades": len(r), "expectancy_r": round(sum(r) / len(r), 4) if r else None, "total_r": round(sum(r), 3),
                       "wins": sum(1 for x in r if x > 0)},
        "money": _money_view(state, positions),
        "recent_trades": list(reversed(memory))[:30],
        "log": list(reversed(shared.read_jsonl(breakout_paths()["log"], limit=60))),
        "last_cycle": state.get("last_cycle"),
        "backtest_verdict": "Backtest 2026-09-17 (BASELINE.md): replicates the TradingView row (PF 2.023 vs 2.027 on "
                            "2023-2026 broker 4H) but 2007-2022 PF 0.97 - not proven outside the 2023-2026 gold trend. "
                            "Demo results are forward evidence.",
    }


if __name__ == "__main__":
    import argparse

    from .execution_guard import SECRET_HEADER, load_or_create_secret

    parser = argparse.ArgumentParser(description="Volatility breakout demo trading (the app runs the cycle).")
    parser.add_argument("command", choices=["cycle", "status"])
    args = parser.parse_args()
    if args.command == "status":
        print(json.dumps(breakout_status(), indent=1, default=str)[:4000])
        raise SystemExit(0)
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
