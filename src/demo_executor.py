"""Demo-account execution of the gold 4h research model's signals.

This is the only place where a paper-trading signal may become a broker order, and it
only ever allows a DEMO account. The app calls it with its own MT5 connection (MT5 must
not be opened from a second process). Every one of these must hold, or nothing is sent:

* enabled in ``data/paper_trading/demo_execution.json``; with ``dry_run`` the order is
  logged instead of sent;
* MT5 connected, and the account is a demo account (MT5 trade_mode 0 and "demo" in the
  server name) with the configured login;
* the side is BUY or SELL (NO_TRADE / HOLD never execute) for the configured symbol;
* the signal is fresh: its 4h bar closed at most ``max_signal_age_minutes`` ago;
* it has not been attempted before (idempotent by signal bar) and no position with this
  model's magic number is open (one position at a time, as in the backtest);
* the spread is normal and both stop and target are attached - an order the broker would
  only accept without stops is refused rather than retried naked.

Size is a fixed small volume (default 0.01 lots, capped by ``max_volume``). Positions are
closed at the model's time limit (close of the 6th bar after the signal bar) by
``sync_positions``; stop and target sit at the broker. Every decision is journaled.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

CONFIG_PATH = Path("data") / "paper_trading" / "demo_execution.json"
JOURNAL_PATH = Path("data") / "paper_trading" / "demo_execution_journal.json"
MT5_TRADE_MODE_DEMO = 0
MAX_EVENTS_KEPT = 1000

DEFAULT_CONFIG = {
    "enabled": False,
    "dry_run": True,
    "symbol": "XAUUSD",
    "volume": 0.01,
    "max_volume": 0.10,
    "magic": 440401,
    "comment": "GOLD4H demo model",
    "account_login": None,
    "max_signal_age_minutes": 75,
    "max_spread_fraction_of_stop": 0.25,
}


def _stamp(value) -> str:
    return pd.Timestamp(value).tz_convert("UTC").strftime("%Y-%m-%d %H:%M:%S")


def load_config(path: Optional[Path] = None, defaults: Optional[dict] = None) -> dict:
    """JSON config over ``defaults`` (this executor's DEFAULT_CONFIG unless another demo strategy passes its own)."""
    path = Path(path or CONFIG_PATH)
    config = dict(DEFAULT_CONFIG if defaults is None else defaults)
    if path.exists():
        config.update(json.loads(path.read_text(encoding="utf-8")))
    return config


def load_journal(path: Optional[Path] = None) -> dict:
    path = Path(path or JOURNAL_PATH)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"attempts": {}, "events": []}


def save_journal(journal: dict, path: Optional[Path] = None) -> None:
    path = Path(path or JOURNAL_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    journal["events"] = journal.get("events", [])[-MAX_EVENTS_KEPT:]
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(journal, indent=1, default=str), encoding="utf-8")
    os.replace(tmp, path)


def _event(journal: dict, now, kind: str, **details) -> dict:
    event = {"at": _stamp(now), "event": kind, **details}
    journal.setdefault("events", []).append(event)
    return event


def is_demo_account(account: Optional[dict], expected_login=None) -> tuple[bool, str]:
    if not account:
        return False, "MT5 account details unavailable"
    server = str(account.get("server") or "")
    if account.get("trade_mode") != MT5_TRADE_MODE_DEMO or "demo" not in server.lower():
        return False, f"account {account.get('login')} on {server or '?'} is not a demo account (trade_mode {account.get('trade_mode')})"
    if expected_login not in (None, "", 0) and int(account.get("login") or 0) != int(expected_login):
        return False, f"logged-in account {account.get('login')} is not the configured demo account {expected_login}"
    return True, f"demo account {account.get('login')} on {server}"


def check_signal(signal: dict, config: dict, now) -> tuple[bool, str]:
    side = str(signal.get("side") or "").upper()
    if side not in {"BUY", "SELL"}:
        return False, f"side {side or 'missing'} is not tradeable"
    if str(signal.get("symbol") or "").upper() != str(config["symbol"]).upper():
        return False, f"symbol {signal.get('symbol')} is not {config['symbol']}"
    try:
        closed_at = pd.Timestamp(signal["bar_time"], tz="UTC") + pd.Timedelta(minutes=int(signal["bar_minutes"]))
    except Exception:
        return False, "signal bar time missing or unreadable"
    age_min = (pd.Timestamp(now) - closed_at).total_seconds() / 60.0
    if age_min < 0:
        return False, f"signal bar {signal['bar_time']} has not closed yet"
    if age_min > float(config["max_signal_age_minutes"]):
        return False, f"signal is stale: bar closed {age_min:.0f} min ago (limit {config['max_signal_age_minutes']})"
    for key in ("atr", "sl_atr", "tp_atr"):
        try:
            if not float(signal[key]) > 0:
                raise ValueError
        except Exception:
            return False, f"signal {key} missing or not positive"
    return True, f"fresh {side} signal (bar closed {age_min:.0f} min ago)"


def order_levels(side: str, price: float, atr: float, sl_atr: float, tp_atr: float, digits: int = 2) -> tuple[float, float]:
    direction = 1 if side == "BUY" else -1
    return (round(price - direction * sl_atr * atr, digits), round(price + direction * tp_atr * atr, digits))


def execute_signal(engine, signal: dict, config: dict, journal: dict, now=None) -> dict:
    """Try to open one demo position for ``signal``. Returns the journal event describing what happened."""
    now = pd.Timestamp(now) if now is not None else pd.Timestamp(datetime.now(timezone.utc))
    bar = str(signal.get("bar_time") or "")
    base = {"signal_bar": bar, "side": signal.get("side"), "symbol": signal.get("symbol")}
    if not config.get("enabled"):
        # Off by default (DEFAULT_CONFIG); since 2026-09-16 the owner keeps it off so only the gold session pullback
        # strategy (src/demo_session_pullback.py) trades the demo account. Every skipped signal says so in the journal.
        why = config.get("disabled_reason") or "enabled is false in data/paper_trading/demo_execution.json"
        return _event(journal, now, "skipped", reason=f"demo execution is disabled: {why}", **base)
    ok, reason = check_signal(signal, config, now)
    if not ok:
        return _event(journal, now, "refused", reason=reason, **base)
    if bar in journal.setdefault("attempts", {}):
        return _event(journal, now, "refused", reason=f"signal bar {bar} was already attempted", **base)
    status = engine.status() or {}
    if not status.get("connected"):
        return _event(journal, now, "refused", reason="MT5 is not connected", **base)
    demo, account_reason = is_demo_account((engine.account_info() or {}).get("account"), config.get("account_login"))
    if not demo:
        return _event(journal, now, "refused", reason=account_reason, **base)
    symbol = str(config["symbol"]).upper()
    open_positions = engine.positions(symbol=symbol, magic=int(config["magic"]))
    if open_positions is None:
        return _event(journal, now, "refused", reason="could not read open MT5 positions", **base)
    if open_positions:
        return _event(journal, now, "refused", reason=f"model position already open (ticket {open_positions[0].get('ticket')})", **base)
    quote = engine.quote(symbol) or {}
    if not quote.get("ok"):
        return _event(journal, now, "refused", reason=f"no live quote: {quote.get('message')}", **base)

    side = str(signal["side"]).upper()
    atr, sl_atr, tp_atr = float(signal["atr"]), float(signal["sl_atr"]), float(signal["tp_atr"])
    bid, ask = float(quote["bid"]), float(quote["ask"])
    spread = ask - bid
    if spread < 0 or spread > float(config["max_spread_fraction_of_stop"]) * sl_atr * atr:
        return _event(journal, now, "refused", reason=f"spread {spread:.2f} is too wide for a {sl_atr * atr:.2f} stop", **base)
    price = ask if side == "BUY" else bid
    stop, target = order_levels(side, price, atr, sl_atr, tp_atr, int(quote.get("digits") or 2))
    volume = round(min(float(config["volume"]), float(config["max_volume"])), 2)
    request = {"symbol": symbol, "side": side, "volume": volume, "stop_loss": stop, "take_profit": target,
               "comment": str(config["comment"])[:31], "magic": int(config["magic"]), "allow_retry_without_stops": False}
    details = {**base, "account": account_reason, "price": price, "spread": round(spread, 3), "request": request,
               "p_win": signal.get("p_win"), "ev_r": signal.get("ev_r"), "threshold_r": signal.get("threshold_r"),
               "expires_at": _stamp(pd.Timestamp(bar, tz="UTC")
                                    + pd.Timedelta(minutes=int(signal["bar_minutes"]) * (int(signal.get("horizon_bars") or 6) + 1)))}
    if config.get("dry_run", True):
        event = _event(journal, now, "dry_run", reason="dry run: order logged, not sent", **details)
        journal["attempts"][bar] = {**event, "status": "dry_run"}
        return event

    result = engine.place_market_order(**request) or {}
    order = result.get("result") or {}
    executed = bool(result.get("executed"))
    event = _event(journal, now, "opened" if executed else "failed", reason=result.get("message"),
                   ticket=order.get("order") or order.get("deal"), broker_request=result.get("request"), **details)
    journal["attempts"][bar] = {**event, "status": "open" if executed else "failed"}
    return event


def sync_positions(engine, config: dict, journal: dict, now=None) -> list[dict]:
    """Mark positions the broker closed (stop/target) and close positions that reached the time limit."""
    now = pd.Timestamp(now) if now is not None else pd.Timestamp(datetime.now(timezone.utc))
    events = []
    # Dry-run signals never reach the broker; record when their time limit passes so none just vanish.
    for bar, record in (journal.get("attempts") or {}).items():
        if record.get("status") == "dry_run" and record.get("expires_at") and now >= pd.Timestamp(record["expires_at"], tz="UTC"):
            record["status"] = "dry_run_expired"
            events.append(_event(journal, now, "dry_run_expired", signal_bar=bar, side=record.get("side"),
                                 expires_at=record["expires_at"], reason="dry-run signal reached its time limit; no order was sent"))
    open_attempts = {bar: rec for bar, rec in (journal.get("attempts") or {}).items() if rec.get("status") == "open"}
    if not open_attempts:
        return events
    if not (engine.status() or {}).get("connected"):
        return [_event(journal, now, "sync_skipped", reason="MT5 is not connected")]
    positions = engine.positions(symbol=str(config["symbol"]).upper(), magic=int(config["magic"]))
    if positions is None:
        return [_event(journal, now, "sync_skipped", reason="could not read open MT5 positions")]
    live = {int(p.get("ticket")): p for p in positions}
    for bar, record in open_attempts.items():
        ticket = int(record.get("ticket") or 0)
        position = live.get(ticket)
        if position is None:
            record["status"] = "closed_at_broker"
            events.append(_event(journal, now, "closed_at_broker", signal_bar=bar, ticket=ticket,
                                 reason="position no longer open (stop or target hit, or closed by hand)"))
            continue
        if now < pd.Timestamp(record["expires_at"], tz="UTC"):
            continue
        result = engine.close_position(ticket, comment="GOLD4H time limit") or {}
        if result.get("executed"):
            record["status"] = "closed_time_limit"
        events.append(_event(journal, now, "closed_time_limit" if result.get("executed") else "close_failed",
                             signal_bar=bar, ticket=ticket, profit=position.get("profit"), reason=result.get("message")))
    return events
