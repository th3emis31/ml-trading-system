"""Read the ATOMIC ANALYST V85 panel's output and compare it with the system's own plans (owner request 2026-09-17).

The panel is an MT5 indicator on the owner's chart (MQL5/Indicators/ATOMIC_ANALYST_V85.mq5). It writes one JSON file
per symbol every tick, and its own file says: "Evidence only. This is a second opinion from an indicator and must never
be wired into confidence, the gate, position size or a stop." This module keeps to that: it reads the files, reports
staleness, and says whether the panel agrees with the system's breakout plan. Nothing here feeds a signal, a size or an
order, and the payload carries ``feeds_orders: False``.

Agreement is only descriptive: both call the same side, they disagree, or one is waiting. Whether the panel's verdict
adds anything is a question for the plan journal once enough forward plans have been recorded.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

import pandas as pd

from .event_defence import utc_timestamp

SYMBOLS = ("XAUUSD", "BTCUSD")
STALE_MINUTES = 30          # the panel rewrites its file on every tick while its chart is open
PANEL_DIR_ENV = "ATOMIC_ANALYST_DIR"
_APPDATA = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
DEFAULT_PANEL_DIR = _APPDATA / "MetaQuotes" / "Terminal" / \
    "D0E8209F77C8CF37AD8BF550E51FF075" / "MQL5" / "Files" / "atomic_analyst"


def panel_dir() -> Path:
    return Path(os.environ.get(PANEL_DIR_ENV) or DEFAULT_PANEL_DIR)


def read_panel(symbol: str, now=None) -> dict:
    """One symbol's panel file with its age; never raises, and never returns a guessed value."""
    now = utc_timestamp(now)
    path = panel_dir() / f"{symbol.upper()}.json"
    if not path.exists():
        return {"symbol": symbol.upper(), "available": False,
                "reason": f"no panel file at {path} (the indicator writes it while its chart is open)"}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"symbol": symbol.upper(), "available": False, "reason": f"panel file unreadable: {exc}"}
    generated = str(data.get("generatedAt") or "")
    try:
        age_min = round((now - utc_timestamp(pd.Timestamp(generated.replace(".", "-", 2)))).total_seconds() / 60, 1)
    except Exception:
        age_min = None
    ticket = data.get("ticket") or {}
    stats = data.get("chartStats") or {}
    return {
        "symbol": data.get("symbol", symbol.upper()), "available": True, "source": data.get("source"),
        "generated_at": generated, "age_minutes": age_min,
        "fresh": bool(age_min is not None and age_min <= STALE_MINUTES),
        "stale_reason": None if (age_min is not None and age_min <= STALE_MINUTES)
        else f"panel last wrote {generated} ({age_min} min ago); is the MT5 chart open?",
        "account": data.get("account"), "timeframe": data.get("timeframe"),
        "verdict": data.get("verdict"), "direction": data.get("direction"), "confidence": data.get("confidence"),
        "mtf_aligned": data.get("mtfAligned"), "dominance": data.get("dominance"), "mtf": data.get("mtf"),
        "consensus": data.get("consensus"), "indicators": data.get("indicators"), "atomic": data.get("atomic"),
        "last_ticket": {k: ticket.get(k) for k in ("direction", "barTime", "entry", "sl", "tp1", "tp2", "tp3", "status", "tpHits")},
        "chart_sim": {**{k: stats.get(k) for k in ("signalsBuy", "signalsSell", "closedTrades", "wins", "profitFactor")},
                      "note": stats.get("note")},
        "feeds_the_gate": bool(data.get("feedsTheGate")),
    }


def agreement(panel: dict, plan: Optional[dict]) -> dict:
    """Does the panel's direction line up with the system's breakout plan for the same symbol?"""
    if not panel.get("available"):
        return {"state": "no panel", "detail": panel.get("reason")}
    side = str(panel.get("direction") or "WAIT").upper()
    armed = bool((plan or {}).get("armed_now"))
    trigger = ((plan or {}).get("armed_now") or {}).get("trigger")
    if side == "WAIT":
        return {"state": "panel waiting", "detail": "the panel has no directional verdict right now",
                "system_breakout": "armed" if armed else "not armed"}
    if side == "BUY" and armed:
        return {"state": "agree (both long)", "detail": f"panel {panel.get('verdict')}; system breakout armed above {trigger}",
                "system_breakout": "armed"}
    if side == "BUY":
        return {"state": "panel long, system waiting", "detail": "the breakout plan is not armed (trend or RSI filter)",
                "system_breakout": "not armed"}
    return {"state": "disagree (panel short, system long-only)",
            "detail": "the system's breakout and pullback plans are long only, so a SELL verdict has no system equivalent",
            "system_breakout": "armed" if armed else "not armed"}


def overview(now=None) -> dict:
    """Both symbols: the panel's verdict, its freshness, and how it lines up with the system's plans."""
    from .plan_journal import read_latest

    journal = read_latest()
    symbols = {}
    for symbol in SYMBOLS:
        panel = read_panel(symbol, now)
        plan = ((journal.get("symbols") or {}).get(symbol)) if journal.get("available") else None
        symbols[symbol] = {"panel": panel, "agreement": agreement(panel, plan),
                           "system_plan": {"armed_now": (plan or {}).get("armed_now"),
                                           "all_history": (plan or {}).get("all_history")} if plan else None}
    return {
        "available": True, "checked_at": utc_timestamp(now).strftime("%Y-%m-%d %H:%M:%S"),
        "panel_dir": str(panel_dir()), "stale_after_minutes": STALE_MINUTES,
        "feeds_orders": False,
        "note": "Read-only second opinion from the owner's MT5 panel. It never feeds the signal engine, the execution "
                "gate, position size or a stop; the panel's own file says the same.",
        "symbols": symbols,
    }
