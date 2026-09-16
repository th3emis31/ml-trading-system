"""Checks that must pass before the auto trader sends ANY order (live broker, broker demo or simulation).

Used by app.py /api/auto-trade/execute and the JARVIS autonomy loop (15 Sep 2026, execution safety item 1):
  * control secret: every HTTP call must carry X-Control-Secret; the autonomy loop calls in-process instead of
    sending "internal_auto_execute" in a request body (that field is ignored from requests)
  * fresh bar: the newest closed BROKER bar is at most ``max_bar_age_minutes`` old
  * spread: live broker spread <= ``max_spread_fraction`` x stop distance
  * news: no high-impact event for the symbol inside the calendar window (src/economic_calendar.py)
Missing data (no broker bars, no quote, no calendar) is a rejection, never a pass. Every rejection is appended to
data/execution_rejections.jsonl with its reason. Nothing here places, modifies or closes orders.
"""
from __future__ import annotations

import hmac
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SECRET_PATH = ROOT / "data" / "control_api.json"
REJECTIONS_PATH = ROOT / "data" / "execution_rejections.jsonl"
SECRET_HEADER = "X-Control-Secret"
DEFAULTS = {"bar_minutes": 60, "max_bar_age_minutes": 75, "max_spread_fraction": 0.25}


def load_or_create_secret(path: Optional[Path] = None) -> str:
    """The control secret, created once (never logged or returned by an API)."""
    path = Path(path or SECRET_PATH)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and str(data.get("secret") or "").strip():
            return str(data["secret"])
    except (OSError, ValueError):
        pass
    secret = secrets.token_urlsafe(24)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"secret": secret, "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                               "header": SECRET_HEADER}, indent=1), encoding="utf-8")
    os.replace(tmp, path)
    return secret


def secret_matches(provided: Optional[str], expected: Optional[str]) -> bool:
    if not provided or not expected:
        return False
    return hmac.compare_digest(str(provided).strip().encode("utf-8"), str(expected).strip().encode("utf-8"))


def fresh_bar_check(bars: Optional[pd.DataFrame], source: Optional[str], now: datetime,
                    bar_minutes: int = DEFAULTS["bar_minutes"],
                    max_bar_age_minutes: int = DEFAULTS["max_bar_age_minutes"]) -> dict:
    """The newest CLOSED broker bar (open + bar length <= now) must be at most max_bar_age_minutes old."""
    from .economic_calendar import _utc

    now = _utc(now)
    if not source or not str(source).lower().startswith("mt5"):
        return {"ok": False, "reason": f"no broker bars (source: {source or 'none'}); Yahoo and synthetic bars never gate an order"}
    if bars is None or len(bars) == 0 or "datetime" not in bars:
        return {"ok": False, "reason": "broker returned no bars"}
    opens = pd.to_datetime(bars["datetime"], utc=True)
    closes = opens + pd.Timedelta(minutes=bar_minutes)
    closed = closes[closes <= pd.Timestamp(now)]
    if closed.empty:
        return {"ok": False, "reason": "no closed broker bar yet"}
    last_close = closed.max().to_pydatetime()
    age = (now - last_close).total_seconds() / 60
    if age > max_bar_age_minutes:
        return {"ok": False, "reason": f"stale data: newest closed broker bar closed {age:.0f} min ago (limit {max_bar_age_minutes})",
                "last_closed_bar_close": last_close.strftime("%Y-%m-%d %H:%M")}
    return {"ok": True, "last_closed_bar_close": last_close.strftime("%Y-%m-%d %H:%M"), "age_minutes": round(age, 1)}


def spread_check(quote: Optional[dict], stop_distance: Optional[float],
                 max_spread_fraction: float = DEFAULTS["max_spread_fraction"]) -> dict:
    if not quote or not quote.get("ok"):
        return {"ok": False, "reason": f"no live broker quote ({(quote or {}).get('message') or 'unavailable'})"}
    try:
        bid, ask, stop = float(quote["bid"]), float(quote["ask"]), float(stop_distance)
    except (TypeError, ValueError, KeyError):
        return {"ok": False, "reason": "quote or stop distance unreadable"}
    if bid <= 0 or ask <= 0 or ask < bid:
        return {"ok": False, "reason": f"invalid quote bid {bid} ask {ask}"}
    if stop <= 0:
        return {"ok": False, "reason": "stop distance missing or zero"}
    spread = ask - bid
    limit = max_spread_fraction * stop
    if spread > limit:
        return {"ok": False, "reason": f"spread {spread:.5g} > {max_spread_fraction:.0%} of stop distance {stop:.5g}", "spread": spread}
    return {"ok": True, "spread": spread, "limit": limit}


def news_check(calendar: Optional[dict], symbol: str, now: datetime) -> dict:
    from . import economic_calendar as ec

    if not calendar or not calendar.get("available"):
        return {"ok": False, "reason": f"economic calendar unavailable ({(calendar or {}).get('reason') or 'no data'}): news risk unknown"}
    window = ec.news_window(calendar.get("events") or [], symbol, ec._utc(now))
    if window["in_window"]:
        names = ", ".join(f"{e.get('title')} ({e.get('minutes_to')} min)" for e in window["events"])
        return {"ok": False, "reason": f"high-impact news window: {names}", "events": window["events"]}
    return {"ok": True, "next_high_impact": window.get("next_high_impact")}


LEVEL_MULTIPLIERS = {"stop_loss": 1.2, "take_profit_1": 2.4, "take_profit_2": 3.6, "take_profit_3": 4.8}   # x ATR, as the live plan
PRICE_FIELDS = ("entry", "stop_loss", "take_profit_1", "take_profit_2", "take_profit_3")


def _broker_prefix(label: Optional[str]) -> str:
    return str(label or "").split(":", 1)[0].lower()


def broker_trade_levels(*, symbol: str, side: str, quote: Optional[dict], quote_source: Optional[str],
                        bars: Optional[pd.DataFrame], bars_source: Optional[str], atr_period: int = 14,
                        multipliers: Optional[dict] = None, digits: int = 2) -> dict:
    """Entry, stop and targets from ONE broker feed (execution safety item 2).

    Entry is the price the order fills at (ask for BUY, bid for SELL) from the broker quote; the ATR behind the stop
    and targets comes from that same broker's bars. Yahoo, synthetic or mixed feeds return available=False - there is
    no fallback price.
    """
    from .features import compute_atr

    side = str(side or "").upper()
    if side not in {"BUY", "SELL"}:
        return {"available": False, "reason": f"no tradeable side ({side or 'none'})"}
    q_prefix, b_prefix = _broker_prefix(quote_source), _broker_prefix(bars_source)
    if q_prefix not in {"mt5", "mt4"}:
        return {"available": False, "reason": f"entry quote is not from a broker feed (source: {quote_source or 'none'})"}
    if b_prefix != q_prefix:
        return {"available": False, "reason": f"quote ({quote_source}) and bars ({bars_source or 'none'}) come from different feeds"}
    if not quote or not quote.get("ok"):
        return {"available": False, "reason": f"no live broker quote ({(quote or {}).get('message') or 'unavailable'})"}
    if bars is None or len(bars) < atr_period + 2 or not {"high", "low", "close"} <= set(bars.columns):
        return {"available": False, "reason": "not enough broker bars for the ATR"}
    frame = bars[["high", "low", "close"]].astype(float)
    atr = float(compute_atr(frame, atr_period).iloc[-1])
    if not atr or atr != atr or atr <= 0:
        return {"available": False, "reason": "broker ATR unavailable"}
    entry = float(quote["ask"] if side == "BUY" else quote["bid"])
    direction = 1 if side == "BUY" else -1
    mult = {**LEVEL_MULTIPLIERS, **(multipliers or {})}
    levels = {"entry": round(entry, digits)}
    for field, m in mult.items():
        offset = -m if field == "stop_loss" else m
        levels[field] = round(entry + direction * offset * atr, digits)
    sources = {"entry": f"{quote_source}:tick"}
    for field in mult:
        sources[field] = f"{quote_source}:tick+{bars_source}:atr{atr_period}"
    return {"available": True, "symbol": symbol, "side": side, **levels, "atr": round(atr, 5),
            "price_source": q_prefix, "sources": sources}


def levels_share_source(levels: dict) -> bool:
    """True only when entry, stop and every target were priced from the same broker feed."""
    sources = (levels or {}).get("sources") or {}
    present = [f for f in PRICE_FIELDS if (levels or {}).get(f) is not None]
    if "entry" not in present or "stop_loss" not in present or "take_profit_1" not in present:
        return False
    prefixes = {_broker_prefix(sources.get(f)) for f in present}
    return len(prefixes) == 1 and prefixes <= {"mt5", "mt4"} and all(
        _broker_prefix(part) == next(iter(prefixes)) for f in present for part in str(sources.get(f)).split("+"))


APPROVAL_BAND_FRACTION = 0.3   # half-width of the approved price band, as a share of the broker stop distance


def approval_price_band(levels: Optional[dict], fraction: float = APPROVAL_BAND_FRACTION) -> dict:
    """The broker price band an approval is valid for (execution safety item 3), fixed when the request is created."""
    if not levels or not levels.get("available") or not levels_share_source(levels):
        return {"available": False, "reason": (levels or {}).get("reason") or "no broker-priced levels when the request was created"}
    entry, stop = float(levels["entry"]), float(levels["stop_loss"])
    half = abs(entry - stop) * float(fraction)
    if half <= 0:
        return {"available": False, "reason": "zero stop distance"}
    return {"available": True, "side": levels["side"], "symbol": levels.get("symbol"), "entry": entry,
            "low": round(entry - half, 5), "high": round(entry + half, 5), "half_width": round(half, 5),
            "source": levels.get("price_source"), "fraction_of_stop": fraction}


def approval_matches(request: Optional[dict], *, symbol: str, side: str, broker_entry: Optional[float],
                     price_source: Optional[str] = None) -> dict:
    """Execute only the exact symbol, side and broker price band that was approved; anything else is a rejection."""
    if not request:
        return {"ok": False, "reason": "approval request not found"}
    want_symbol, want_side = str(request.get("symbol") or "").upper(), str(request.get("side") or "").upper()
    if want_symbol != str(symbol or "").upper():
        return {"ok": False, "reason": f"symbol {symbol} does not match the approved {want_symbol}"}
    if want_side != str(side or "").upper():
        return {"ok": False, "reason": f"side {side} does not match the approved {want_side}"}
    band = request.get("price_band") or {}
    if not band.get("available"):
        return {"ok": False, "reason": f"the approval has no broker price band ({band.get('reason') or 'created before bands existed'})"}
    if str(band.get("side") or "").upper() != want_side:
        return {"ok": False, "reason": "the stored price band belongs to the other side"}
    if price_source and band.get("source") and str(band["source"]).lower() != str(price_source).lower():
        return {"ok": False, "reason": f"price feed {price_source} differs from the approved feed {band['source']}"}
    try:
        price = float(broker_entry)
    except (TypeError, ValueError):
        return {"ok": False, "reason": "no broker entry price to compare with the approved band"}
    if not (float(band["low"]) <= price <= float(band["high"])):
        return {"ok": False, "reason": f"broker entry {price} is outside the approved band {band['low']} - {band['high']}"}
    return {"ok": True, "band": band}


def pre_order_checks(*, symbol: str, now: datetime, bars: Optional[pd.DataFrame], bars_source: Optional[str],
                     quote: Optional[dict], stop_distance: Optional[float], calendar: Optional[dict],
                     settings: Optional[dict] = None) -> dict:
    cfg = {**DEFAULTS, **(settings or {})}
    checks = {
        "fresh_bar": fresh_bar_check(bars, bars_source, now, cfg["bar_minutes"], cfg["max_bar_age_minutes"]),
        "spread": spread_check(quote, stop_distance, cfg["max_spread_fraction"]),
        "news": news_check(calendar, symbol, now),
    }
    reasons = [f"{name}: {c['reason']}" for name, c in checks.items() if not c.get("ok")]
    return {"ok": not reasons, "reasons": reasons, "checks": checks}


def log_rejection(record: dict, path: Optional[Path] = None) -> dict:
    """Append one rejection (time, source, symbol, http status, reason) to the JSONL log."""
    path = Path(path or REJECTIONS_PATH)
    row = {"at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), **record}
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with Path(path).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str) + "\n")
    except OSError:
        pass
    return row


SIMULATED_MODES = {"demo", "simulation", "simulated", "paper"}


def is_real_broker_fill(trade_record: Optional[dict]) -> bool:
    """True only for an order the broker (MT4/MT5, demo or live account) actually filled.

    Execution safety item 5: simulated demo outcomes (simulate_demo_trade) must never count toward the autonomy
    confidence threshold. Anything marked simulated, rejected, or without a broker ticket is not a real fill.
    """
    if not isinstance(trade_record, dict):
        return False
    source = str(trade_record.get("outcome_source") or "").lower()
    if source != "broker_fill":
        return False
    if str(trade_record.get("execution_mode") or "").lower() in SIMULATED_MODES:
        return False
    order = trade_record.get("mt5") if isinstance(trade_record.get("mt5"), dict) else trade_record.get("mt4")
    if not isinstance(order, dict) or not order.get("executed"):
        return False
    return trade_record.get("ticket") not in (None, "", 0)


def recent_rejections(limit: int = 50, path: Optional[Path] = None) -> list[dict]:
    path = Path(path or REJECTIONS_PATH)
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines[-int(limit):]:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out
