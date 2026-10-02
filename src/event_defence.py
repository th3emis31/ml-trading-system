"""Event defence: the tier-1 news window and the volatility circuit breaker.

Why: on 16 Sep 2026 the 18:00 UTC FOMC decision printed a gold H1 candle of roughly 90 USD. The existing filter
(src/economic_calendar.news_window) only blocks 30 min either side of any High-impact USD event and has no history,
so it could not be backtested. This module adds:

- a tier-1 list (FOMC decision, FOMC minutes, FOMC press conference, CPI, NFP) with a wider window: no new entries
  from 60 min before to 90 min after, and an alert from 60 min before the event to reduce or flatten open positions
  in the affected symbols;
- a volatility circuit breaker: when an H1 bar's range exceeds 3 x ATR14, no new entries on the next 3 bars;
- the same tier-1 window on history from data/historical_events.csv (official Fed and BLS schedules), so backtests
  can apply it.

Dry run: every function here only decides and logs. Nothing places, modifies or closes an order, and nothing is wired
into /api/auto-trade/execute.
"""
from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

import numpy as np
import pandas as pd

from .features import compute_atr
from .runtime_paths import smartentry_data_dir

TIER1_EVENTS = ("FOMC_DECISION", "FOMC_PRESS_CONFERENCE", "FOMC_MINUTES", "CPI", "NFP")
TIER1_BEFORE_MIN = 60
TIER1_AFTER_MIN = 90
ALERT_BEFORE_MIN = 60
AFFECTED_SYMBOLS = {"USD": ("XAUUSD", "BTCUSD")}
BREAKER_ATR_MULTIPLE = 3.0
BREAKER_ATR_LEN = 14
BREAKER_BLOCK_BARS = 3

# ForexFactory titles (the live calendar) that are tier-1 events; checked in order, first match wins.
LIVE_TITLE_TIER1 = (
    ("fomc press conference", "FOMC_PRESS_CONFERENCE"),
    ("fomc meeting minutes", "FOMC_MINUTES"),
    ("federal funds rate", "FOMC_DECISION"),
    ("fomc statement", "FOMC_DECISION"),
    ("core cpi", "CPI"),
    ("cpi m/m", "CPI"),
    ("cpi y/y", "CPI"),
    ("non-farm employment change", "NFP"),
)


# ---------------------------------------------------------------------------------------------------
# WHAT THE SYSTEM KNOWS, as opposed to what it blocks on. Added 2 October 2026.
#
# The owner: "System needs to know everything about CPI, NFP, payroll, investment ... the need really
# to know everything for impact news system the need better analyzes."
#
# He is right that the vocabulary was too small, and the gaps were not minor:
#
#   * AVERAGE HOURLY EARNINGS and the UNEMPLOYMENT RATE are released in the SAME MINUTE as Non-Farm
#     Payrolls. Neither was recognised. The wage number is frequently what actually moves gold - a
#     headline beat with soft wages and a headline miss with hot wages trade in opposite directions -
#     so the system was watching one third of the release it cared most about.
#   * CORE PCE was absent entirely. It is the Fed's PREFERRED inflation gauge; CPI was in the list and
#     the measure the Fed actually targets was not.
#   * EUR CPI could not be seen at all, for two independent reasons: `classify_live_event` returns
#     None for any currency that is not USD, and the title the calendar publishes is "CPI Flash
#     Estimate y/y", which does not contain the substring "cpi y/y".
#   * ADP, PPI, GDP, Retail Sales, ISM, JOLTS, claims and central-bank decisions: all invisible.
#
# THIS DOES NOT CHANGE WHAT IS BLOCKED. `TIER1_EVENTS`, `LIVE_TITLE_TIER1` and `classify_live_event`
# are untouched, so every gate behaves exactly as before and no signal is newly suppressed. The owner
# has said twice this week not to block anything, and he is right about that too: a wider blocking set
# would silently stop trades firing and starve the very record needed to judge whether these events
# matter. So this vocabulary is for OBSERVING and LOGGING - the bias beside the signal, the same shape
# as the Coinversa Pulse card. Whether any of it should ever gate a trade is decided later, from the
# accumulated outcomes, by the owner.
TIER = {"1": "moves the market on its own", "2": "moves it when it surprises", "3": "context"}

#: (title fragment, event kind, tier). Checked in order, first match wins, so the more specific
#: fragment must come first - "core cpi" before "cpi", "adp non-farm" before "non-farm".
EVENT_VOCABULARY = (
    # -- central banks ------------------------------------------------------------------------------
    ("fomc press conference", "FOMC_PRESS_CONFERENCE", "1"),
    ("fomc meeting minutes", "FOMC_MINUTES", "1"),
    ("fomc statement", "FOMC_DECISION", "1"),
    ("federal funds rate", "FOMC_DECISION", "1"),
    ("fomc member", "FED_SPEAKER", "3"),
    ("fed chair", "FED_SPEAKER", "2"),
    ("main refinancing rate", "ECB_DECISION", "1"),
    ("ecb press conference", "ECB_DECISION", "1"),
    ("official bank rate", "BOE_DECISION", "1"),
    ("cash rate", "RBA_DECISION", "2"),
    ("rate statement", "CB_STATEMENT", "2"),
    ("monetary policy", "CB_STATEMENT", "2"),
    # -- inflation ----------------------------------------------------------------------------------
    ("core pce price index", "CORE_PCE", "1"),       # the Fed's preferred gauge; was missing entirely
    ("pce price index", "PCE", "1"),
    ("core cpi", "CORE_CPI", "1"),
    ("trimmed mean cpi", "CORE_CPI", "2"),
    ("cpi flash estimate", "CPI", "1"),              # the EUR title; "cpi y/y" never matched it
    ("cpi m/m", "CPI", "1"),
    ("cpi y/y", "CPI", "1"),
    ("cpi q/q", "CPI", "1"),
    ("core ppi", "CORE_PPI", "2"),
    ("ppi m/m", "PPI", "2"),
    ("inflation expectations", "INFLATION_EXPECTATIONS", "3"),
    # -- jobs and payrolls --------------------------------------------------------------------------
    ("adp non-farm", "ADP", "2"),                    # the payrolls preview, two days before
    ("non-farm employment change", "NFP", "1"),
    ("average hourly earnings", "AVG_HOURLY_EARNINGS", "1"),   # same minute as NFP
    ("unemployment rate", "UNEMPLOYMENT_RATE", "1"),           # same minute as NFP
    ("unemployment claims", "JOBLESS_CLAIMS", "2"),
    ("jolts job openings", "JOLTS", "2"),
    ("employment change", "EMPLOYMENT", "2"),
    ("claimant count", "EMPLOYMENT", "2"),
    # -- growth, demand, surveys --------------------------------------------------------------------
    ("advance gdp", "GDP", "2"),
    ("final gdp", "GDP", "2"),
    ("prelim gdp", "GDP", "2"),
    ("gdp m/m", "GDP", "2"),
    ("gdp q/q", "GDP", "2"),
    ("core retail sales", "CORE_RETAIL_SALES", "2"),
    ("retail sales", "RETAIL_SALES", "2"),
    ("ism manufacturing pmi", "ISM_MANUFACTURING", "2"),
    ("ism services pmi", "ISM_SERVICES", "2"),
    ("flash manufacturing pmi", "PMI", "3"),
    ("flash services pmi", "PMI", "3"),
    ("consumer confidence", "CONSUMER_CONFIDENCE", "3"),
    ("consumer sentiment", "CONSUMER_SENTIMENT", "3"),
    ("durable goods", "DURABLE_GOODS", "3"),
    ("factory orders", "FACTORY_ORDERS", "3"),
    ("building approvals", "BUILDING", "3"),
    ("business confidence", "BUSINESS_CONFIDENCE", "3"),
    ("trade balance", "TRADE_BALANCE", "3"),
    ("crude oil inventories", "OIL_INVENTORIES", "3"),
)

#: Which currencies' events are worth recording against which instruments. Wider than
#: AFFECTED_SYMBOLS (which governs BLOCKING and stays USD-only), because gold reacts to the euro leg
#: of the dollar as well as to the dollar's own data - a hot EUR CPI moves EURUSD and therefore XAUUSD,
#: and the system could not see one before.
OBSERVED_CURRENCIES = {
    "XAUUSD": ("USD", "EUR", "GBP", "CHF"),
    "BTCUSD": ("USD",),
}


def classify_calendar_event(title: str, currency: str) -> Optional[dict]:
    """Classify ANY calendar row for observation. Never used to block - see the note above.

    Returns {"kind", "tier", "currency", "title"} or None when the row is not in the vocabulary.
    Unlike `classify_live_event` this accepts every currency, because an event that cannot be named
    cannot be studied, and the question "does this kind of release change our results?" needs the
    releases recorded first.
    """
    lowered = str(title or "").lower()
    for needle, kind, tier in EVENT_VOCABULARY:
        if needle in lowered:
            return {"kind": kind, "tier": tier, "currency": str(currency or "").upper(),
                    "title": str(title or "")}
    return None


def events_near(calendar_events: Iterable[dict], now, symbol: str = "XAUUSD",
                before_min: int = 120, after_min: int = 120) -> dict:
    """Every recognised event around ``now`` for ``symbol``, for logging beside a signal.

    This is the "better analysis" half: it answers "what was happening when this trade was taken?"
    and records it, so that after enough trades the question "do we actually do worse near payrolls?"
    can be settled with the system's own outcomes instead of with an opinion. It blocks nothing and
    returns no verdict.
    """
    now = _as_utc(now)
    symbol = symbol.upper()
    wanted = OBSERVED_CURRENCIES.get(symbol, ("USD",))
    seen, nearby, upcoming = [], [], []
    for row in calendar_events or []:
        if not row.get("time_utc"):
            continue
        hit = classify_calendar_event(row.get("title"), row.get("currency"))
        if not hit or hit["currency"] not in wanted:
            continue
        when = _as_utc(row["time_utc"])
        entry = {**hit, "time_utc": when,
                 "minutes_away": round((when - now).total_seconds() / 60.0, 1),
                 "impact": row.get("impact"), "forecast": row.get("forecast"),
                 "previous": row.get("previous"), "actual": row.get("actual")}
        seen.append(entry)
        if -after_min <= entry["minutes_away"] <= before_min:
            nearby.append(entry)
        if entry["minutes_away"] > 0:
            upcoming.append(entry)
    seen.sort(key=lambda e: e["time_utc"])
    nearby.sort(key=lambda e: abs(e["minutes_away"]))
    upcoming.sort(key=lambda e: e["time_utc"])
    tier1_nearby = [e for e in nearby if e["tier"] == "1"]
    return {
        "symbol": symbol, "at_utc": now, "recognised": len(seen),
        "nearby": nearby, "upcoming": upcoming[:8],
        "next": upcoming[0] if upcoming else None,
        "tier1_nearby": tier1_nearby,
        # One readable line to stamp on a signal, e.g. "NFP in 25 min; AVG_HOURLY_EARNINGS in 25 min".
        "label": "; ".join(f"{e['kind']} in {int(e['minutes_away'])} min" if e["minutes_away"] >= 0
                           else f"{e['kind']} {int(-e['minutes_away'])} min ago"
                           for e in nearby[:4]) or "no recognised event within the window",
        "blocks_anything": False,
    }


def historical_events_path() -> Path:
    return smartentry_data_dir() / "historical_events.csv"


def event_defence_log_path() -> Path:
    return smartentry_data_dir() / "event_defence_log.jsonl"


def _as_utc(value) -> datetime:
    stamp = pd.Timestamp(value)
    stamp = stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")
    return stamp.to_pydatetime()


def utc_timestamp(value=None) -> pd.Timestamp:
    """``value`` (or now) as a tz-aware UTC pandas Timestamp; naive values are taken as UTC."""
    return pd.Timestamp(_as_utc(value if value is not None else datetime.now(timezone.utc)))


def load_historical_events(path: Optional[Path] = None) -> list[dict]:
    """Tier-1 events with a clock time from data/historical_events.csv, oldest first.

    Rows without a time (unscheduled FOMC actions whose release time the Fed page does not give) are skipped, never
    guessed; ``skipped_untimed`` on the returned list's first element is not used, callers count them separately.
    """
    path = Path(path) if path else historical_events_path()
    if not path.exists():
        return []
    events = []
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row.get("event") not in TIER1_EVENTS or not row.get("datetime_utc"):
                continue
            events.append({"event": row["event"], "time_utc": _as_utc(row["datetime_utc"]), "currency": row.get("currency") or "USD",
                           "source": row.get("source"), "note": row.get("note") or ""})
    events.sort(key=lambda e: e["time_utc"])
    return events


def classify_live_event(title: str, currency: str) -> Optional[str]:
    """Tier-1 event type for a live calendar row, or None."""
    if str(currency or "").upper() != "USD":
        return None
    lowered = str(title or "").lower()
    for needle, kind in LIVE_TITLE_TIER1:
        if needle in lowered:
            return kind
    return None


def tier1_events_from_calendar(calendar_events: Iterable[dict]) -> list[dict]:
    """Tier-1 events from src.economic_calendar rows ({"title", "currency", "time_utc", ...})."""
    events = []
    for row in calendar_events or []:
        kind = classify_live_event(row.get("title"), row.get("currency"))
        if kind and row.get("time_utc"):
            events.append({"event": kind, "time_utc": _as_utc(row["time_utc"]), "currency": "USD",
                           "source": "economic_calendar", "note": row.get("title") or ""})
    events.sort(key=lambda e: e["time_utc"])
    return events


def tier1_window(events: list[dict], now, symbol: str = "XAUUSD", before_min: int = TIER1_BEFORE_MIN,
                 after_min: int = TIER1_AFTER_MIN, alert_min: int = ALERT_BEFORE_MIN) -> dict:
    """Whether new entries are blocked for ``symbol`` at ``now``, and whether the pre-event alert is on."""
    now = _as_utc(now)
    symbol = symbol.upper()
    relevant = [e for e in events if symbol in AFFECTED_SYMBOLS.get(e.get("currency", "USD"), ())]
    blocking = [e for e in relevant
                if e["time_utc"] - timedelta(minutes=before_min) <= now <= e["time_utc"] + timedelta(minutes=after_min)]
    alerting = [e for e in relevant if e["time_utc"] - timedelta(minutes=alert_min) <= now < e["time_utc"]]
    upcoming = [e for e in relevant if e["time_utc"] > now]

    def _view(e):
        return {"event": e["event"], "time_utc": e["time_utc"].strftime("%Y-%m-%d %H:%M"), "note": e.get("note", "")}

    alert = {"active": bool(alerting), "events": [_view(e) for e in alerting]}
    if alerting:
        first = alerting[0]
        minutes = int((first["time_utc"] - now).total_seconds() // 60)
        alert["message"] = (f"{first['event']} in {minutes} min ({first['time_utc']:%H:%M} UTC): reduce or flatten open "
                            f"{symbol} positions. Dry run: this is an alert only, no position is changed.")
    return {
        "symbol": symbol,
        "now_utc": now.strftime("%Y-%m-%d %H:%M"),
        "entries_blocked": bool(blocking),
        "window": {"before_min": before_min, "after_min": after_min},
        "blocking_events": [_view(e) for e in blocking],
        "alert": alert,
        "next_event": _view(upcoming[0]) if upcoming else None,
    }


def entries_blocked_mask(entry_times, events: list[dict], before_min: int = TIER1_BEFORE_MIN,
                         after_min: int = TIER1_AFTER_MIN) -> np.ndarray:
    """Vectorised tier-1 window for backtests: True where an entry at that UTC time falls inside any event window."""
    times = pd.to_datetime(pd.Series(entry_times), utc=True).dt.tz_convert(None).to_numpy(dtype="datetime64[ns]")
    blocked = np.zeros(len(times), dtype=bool)
    if not events or not len(times):
        return blocked
    order = np.argsort(times, kind="stable")
    sorted_times = times[order]
    for e in events:
        stamp = pd.Timestamp(e["time_utc"]).tz_convert(None) if pd.Timestamp(e["time_utc"]).tzinfo else pd.Timestamp(e["time_utc"])
        lo = np.searchsorted(sorted_times, (stamp - pd.Timedelta(minutes=before_min)).to_datetime64(), side="left")
        hi = np.searchsorted(sorted_times, (stamp + pd.Timedelta(minutes=after_min)).to_datetime64(), side="right")
        blocked[order[lo:hi]] = True
    return blocked


def volatility_breaker_mask(bars: pd.DataFrame, multiple: float = BREAKER_ATR_MULTIPLE, atr_len: int = BREAKER_ATR_LEN,
                            block_bars: int = BREAKER_BLOCK_BARS) -> tuple[np.ndarray, np.ndarray]:
    """(triggered, entry_blocked) per bar.

    A bar triggers when its high-low range exceeds ``multiple`` x ATR14 measured up to the previous bar (the spike is
    not allowed to inflate its own yardstick). No new entry may be opened on the ``block_bars`` bars after a trigger:
    ``entry_blocked[j]`` is True when an entry at the open of bar j would come within those bars.
    """
    frame = bars.reset_index(drop=True)
    atr_prev = compute_atr(frame, atr_len).shift(1).to_numpy(dtype=float)
    rng = (frame["high"] - frame["low"]).to_numpy(dtype=float)
    with np.errstate(invalid="ignore"):
        triggered = np.nan_to_num(rng > multiple * atr_prev, nan=False).astype(bool)
    blocked = np.zeros(len(frame), dtype=bool)
    for t in np.flatnonzero(triggered):
        blocked[t + 1: t + 1 + block_bars] = True
    return triggered, blocked


def breaker_status(bars: pd.DataFrame, multiple: float = BREAKER_ATR_MULTIPLE, atr_len: int = BREAKER_ATR_LEN,
                   block_bars: int = BREAKER_BLOCK_BARS) -> dict:
    """Live view on closed bars: whether the next entry (at the open of the bar after the last one) is blocked."""
    frame = bars.sort_values("datetime").reset_index(drop=True)
    if len(frame) < atr_len + 2:
        return {"available": False, "reason": f"need at least {atr_len + 2} closed bars"}
    triggered, _ = volatility_breaker_mask(frame, multiple, atr_len, block_bars)
    atr_prev = compute_atr(frame, atr_len).shift(1)
    recent = [int(t) for t in np.flatnonzero(triggered) if int(t) >= len(frame) - block_bars]
    status = {"available": True, "multiple": multiple, "block_bars": block_bars, "entries_blocked": bool(recent),
              "last_bar_utc": pd.Timestamp(frame["datetime"].iloc[-1]).strftime("%Y-%m-%d %H:%M")}
    if recent:
        t = recent[-1]
        status["trigger"] = {"bar_utc": pd.Timestamp(frame["datetime"].iloc[t]).strftime("%Y-%m-%d %H:%M"),
                             "range": round(float(frame["high"].iloc[t] - frame["low"].iloc[t]), 2),
                             "atr14_before": round(float(atr_prev.iloc[t]), 2),
                             "bars_left": block_bars - (len(frame) - 1 - t)}
    return status


def log_event_defence(record: dict, path: Optional[Path] = None) -> dict:
    """Append one decision (breaker trigger, window block, alert) to data/event_defence_log.jsonl."""
    path = Path(path) if path else event_defence_log_path()
    row = {"at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), "dry_run": True, **record}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str) + "\n")
    except OSError:
        pass
    return row
