"""Trend Join Long scanner — which of the traded markets is breaking out right now.

The owner's specification was written for TradingView Desktop over a Chrome DevTools
connection. That is not available on this machine, so the same five measurements are
taken from the broker feed the rest of the system already trades on, through the running
app (``/api/data/bars``). Nothing here opens a second MetaTrader connection and nothing
here can place an order: the module only reads candles and writes a JSON file.

For each market, in sequence, never in parallel:

1. Daily candles — ``prev_daily_high`` and ``prev_daily_close`` from the most recent
   **closed** day, ``sma200`` as the mean close of the last 200 closed days.
2. One-minute candles — ``pmh`` is the highest high in today's 04:00–09:30 New York
   window, ``today_hod`` the highest high from 09:30 to now, both excluding the bar
   that is still forming.
3. ``curr_px`` is the close of the last completed one-minute candle, so the price and
   every level come from one feed at one moment.

Then ``daily_breakout`` = price above yesterday's high **and** yesterday's close above
the 200-day average; ``intraday_breakout`` = price above both the premarket high and the
session high so far. Both true is a PASS.

Two honest departures from the specification, each recorded in the report it writes:

* The gate only lets a scan run between 10:00 and 15:30 New York time on a weekday. The
  specification asked for that, and it means a scan outside those hours writes an error
  file and stops. ``--force`` runs anyway and stamps ``time_gate.forced`` so no reader
  mistakes an out-of-hours scan for a real one.
* The specification asked for 400 one-minute candles. At 15:30 New York that reaches
  back only to 08:50, which is after the premarket window opens, so the premarket high
  would be measured from a window that had already been cut short. The scanner instead
  asks for as many candles as the window actually needs and reports how many it used.

Gold and bitcoin are not US equities, so "premarket" here is a clock window rather than
a different trading session; gold is closed at weekends and bitcoin never is. When a
window holds no candles the result is ``no_data`` rather than a fail, because a missing
measurement is not a failed test.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import pandas as pd

from .mtf_data import fetch_app_bars
from .paper_trader import drop_forming_bars

NEW_YORK = ZoneInfo("America/New_York")
# (label the owner uses, symbol on the broker)
TICKERS = (("GOLD", "XAUUSD"), ("BTC", "BTCUSD"))
GATE_OPEN = time(10, 0)
GATE_CLOSE = time(15, 30)
PREMARKET_OPEN = time(4, 0)
SESSION_OPEN = time(9, 30)
DAILY_BARS = 210          # 200 for the average plus room for the forming day and gaps
SMA_PERIOD = 200
MIN_MINUTE_BARS = 400     # the specification's number, used as a floor
DAILY_MINUTES = 1440
REPORT_DIR = Path(".")


def time_gate(now_utc: datetime) -> dict:
    """Whether a scan may run: weekday, 10:00–15:30 New York."""
    local = now_utc.astimezone(NEW_YORK)
    weekday = local.weekday() < 5
    in_window = GATE_OPEN <= local.time() < GATE_CLOSE
    if weekday and in_window:
        reason = f"{local:%H:%M} New York is inside the 10:00–15:30 window"
    elif not weekday:
        reason = f"{local:%A} is not a trading day"
    else:
        reason = f"{local:%H:%M} New York is outside the 10:00–15:30 window"
    return {"open": bool(weekday and in_window), "new_york_time": local.strftime("%Y-%m-%d %H:%M"),
            "window": "10:00-15:30 America/New_York, Monday to Friday", "reason": reason}


def minute_bars_needed(now_utc: datetime) -> int:
    """Enough one-minute candles to reach back past today's premarket open, with an hour spare."""
    local = now_utc.astimezone(NEW_YORK)
    premarket_start = local.replace(hour=PREMARKET_OPEN.hour, minute=0, second=0, microsecond=0)
    if local < premarket_start:  # before 04:00 the window belongs to the previous day
        premarket_start -= pd.Timedelta(days=1)
    span = int((local - premarket_start).total_seconds() // 60) + 60
    return max(MIN_MINUTE_BARS, span)


def daily_levels(frame: pd.DataFrame, now_utc: datetime) -> dict:
    """Previous day's high and close, and the 200-day average close, from closed days only.

    The broker's daily candle opens at 21:00 UTC, so the newest row is usually still
    forming and would otherwise be mistaken for yesterday.
    """
    closed = drop_forming_bars(frame, DAILY_MINUTES, now_utc)
    if closed is None or closed.empty:
        return {"prev_daily_high": None, "prev_daily_close": None, "sma200": None,
                "closed_days": 0, "prev_day": None, "note": "no closed daily candles"}
    last = closed.iloc[-1]
    closes = closed["close"].tail(SMA_PERIOD)
    sma = round(float(closes.mean()), 4) if len(closes) == SMA_PERIOD else None
    note = None if sma is not None else f"only {len(closes)} closed days, need {SMA_PERIOD} for the average"
    return {"prev_daily_high": round(float(last["high"]), 4),
            "prev_daily_close": round(float(last["close"]), 4),
            "sma200": sma,
            "sma200_days": int(len(closes)),
            "closed_days": int(len(closed)),
            "prev_day": pd.Timestamp(last["datetime"]).strftime("%Y-%m-%d %H:%M UTC"),
            "note": note}


def intraday_levels(frame: pd.DataFrame, now_utc: datetime) -> dict:
    """Premarket high (04:00–09:30 NY) and session high so far (09:30–now), closed bars only."""
    closed = drop_forming_bars(frame, 1, now_utc)
    empty = {"pmh": None, "today_hod": None, "curr_px": None, "premarket_bars": 0, "session_bars": 0,
             "last_bar": None, "note": "no closed one-minute candles"}
    if closed is None or closed.empty:
        return empty
    local = pd.to_datetime(closed["datetime"], utc=True).dt.tz_convert(NEW_YORK)
    today = now_utc.astimezone(NEW_YORK).date()
    same_day = local.dt.date == today
    premarket = closed[same_day & (local.dt.time >= PREMARKET_OPEN) & (local.dt.time < SESSION_OPEN)]
    session = closed[same_day & (local.dt.time >= SESSION_OPEN)]
    notes = []
    if premarket.empty:
        notes.append("no candles in today's 04:00-09:30 New York window")
    if session.empty:
        notes.append("no candles since 09:30 New York")
    return {"pmh": round(float(premarket["high"].max()), 4) if not premarket.empty else None,
            "today_hod": round(float(session["high"].max()), 4) if not session.empty else None,
            "curr_px": round(float(closed["close"].iloc[-1]), 4),
            "premarket_bars": int(len(premarket)),
            "session_bars": int(len(session)),
            "last_bar": pd.Timestamp(closed["datetime"].iloc[-1]).strftime("%Y-%m-%d %H:%M UTC"),
            "note": "; ".join(notes) or None}


def evaluate(daily: dict, intraday: dict) -> dict:
    """PASS, fail_daily, fail_intraday — or no_data when a level could not be measured."""
    curr = intraday["curr_px"]
    needed = {"current price": curr, "previous daily high": daily["prev_daily_high"],
              "previous daily close": daily["prev_daily_close"], "200-day average": daily["sma200"],
              "premarket high": intraday["pmh"], "today's high": intraday["today_hod"]}
    missing = [name for name, value in needed.items() if value is None]
    if missing:
        return {"result": "no_data", "reason": "could not measure " + ", ".join(missing),
                "daily_breakout": None, "intraday_breakout": None}

    above_high = curr > daily["prev_daily_high"]
    close_above_sma = daily["prev_daily_close"] > daily["sma200"]
    above_pmh = curr > intraday["pmh"]
    above_hod = curr > intraday["today_hod"]
    daily_ok, intraday_ok = above_high and close_above_sma, above_pmh and above_hod

    if daily_ok and intraday_ok:
        reason = (f"{curr:g} above yesterday's high {daily['prev_daily_high']:g}, "
                  f"yesterday's close {daily['prev_daily_close']:g} above the 200-day {daily['sma200']:g}, "
                  f"and above both the premarket high {intraday['pmh']:g} and today's high {intraday['today_hod']:g}")
        result = "PASS"
    elif not daily_ok:
        parts = []
        if not above_high:
            parts.append(f"{curr:g} is not above yesterday's high {daily['prev_daily_high']:g}")
        if not close_above_sma:
            parts.append(f"yesterday's close {daily['prev_daily_close']:g} is not above the 200-day {daily['sma200']:g}")
        reason, result = " and ".join(parts), "fail_daily"
    else:
        parts = []
        if not above_pmh:
            parts.append(f"{curr:g} is not above the premarket high {intraday['pmh']:g}")
        if not above_hod:
            parts.append(f"{curr:g} is not above today's high {intraday['today_hod']:g}")
        reason, result = " and ".join(parts), "fail_intraday"
    return {"result": result, "reason": reason, "daily_breakout": bool(daily_ok),
            "intraday_breakout": bool(intraday_ok)}


def scan_ticker(label: str, symbol: str, now_utc: datetime) -> dict:
    """One market, measured end to end. Never raises on a missing feed."""
    daily_frame = fetch_app_bars(symbol, "1d", DAILY_BARS)
    minute_count = minute_bars_needed(now_utc)
    minute_frame = fetch_app_bars(symbol, "1m", minute_count)
    sources = sorted({str(f.attrs.get("source")) for f in (daily_frame, minute_frame)
                      if f is not None and not f.empty})

    if daily_frame is None or daily_frame.empty or minute_frame is None or minute_frame.empty:
        which = [name for name, frame in (("daily", daily_frame), ("one-minute", minute_frame))
                 if frame is None or frame.empty]
        return {"ticker": label, "symbol": symbol, "result": "no_data",
                "reason": f"the broker feed returned no {' or '.join(which)} candles",
                "data_source": sources or None, "minute_bars_requested": minute_count}

    daily = daily_levels(daily_frame, now_utc)
    intraday = intraday_levels(minute_frame, now_utc)
    verdict = evaluate(daily, intraday)
    notes = [note for note in (daily.get("note"), intraday.get("note")) if note]
    return {"ticker": label, "symbol": symbol,
            "result": verdict["result"], "reason": verdict["reason"],
            "daily_breakout": verdict["daily_breakout"], "intraday_breakout": verdict["intraday_breakout"],
            "curr_price": intraday["curr_px"], "prev_daily_high": daily["prev_daily_high"],
            "prev_daily_close": daily["prev_daily_close"], "sma200": daily["sma200"],
            "pmh": intraday["pmh"], "today_hod": intraday["today_hod"],
            "measured": {"prev_day": daily.get("prev_day"), "closed_days": daily.get("closed_days"),
                         "sma200_days": daily.get("sma200_days"), "last_minute_bar": intraday.get("last_bar"),
                         "premarket_bars": intraday.get("premarket_bars"),
                         "session_bars": intraday.get("session_bars"),
                         "minute_bars_requested": minute_count},
            "data_source": sources or None,
            "notes": notes or None}


def scan(now_utc: Optional[datetime] = None, force: bool = False) -> dict:
    """The whole universe, in order. Returns the report whether or not the gate was open."""
    now_utc = now_utc or datetime.now(timezone.utc)
    gate = time_gate(now_utc)
    gate["forced"] = bool(force and not gate["open"])
    report = {"scanned_at": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
              "candidates_checked": len(TICKERS),
              "strategy": "Trend Join Long",
              "price_source": "broker candles through the running app (/api/data/bars)",
              "time_gate": gate,
              "hits": [], "all_results": []}

    if not gate["open"] and not force:
        report["candidates_checked"] = 0
        report["error"] = f"outside the trading window: {gate['reason']}"
        return report

    results = []
    for label, symbol in TICKERS:  # sequential on purpose: one market's reading never waits on another's
        results.append(scan_ticker(label, symbol, now_utc))

    report["hits"] = [{"symbol": row["symbol"], "curr_price": row["curr_price"],
                       "prev_daily_high": row["prev_daily_high"], "sma200": row["sma200"],
                       "pmh": row["pmh"], "today_hod": row["today_hod"]}
                      for row in results if row["result"] == "PASS"]
    report["all_results"] = [{"symbol": row["symbol"], "result": row["result"]} for row in results]
    report["details"] = results
    return report


def report_path(now_utc: datetime, directory: Path = REPORT_DIR) -> Path:
    """``tjl_watchlist_YYYY-MM-DD_HHMMET.json`` — the stamp is New York time, as asked."""
    local = now_utc.astimezone(NEW_YORK)
    return Path(directory) / f"tjl_watchlist_{local:%Y-%m-%d_%H%M}ET.json"


def write_report(report: dict, now_utc: datetime, directory: Path = REPORT_DIR) -> Path:
    path = report_path(now_utc, directory)
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return path


def lines(report: dict) -> list:
    """One line per ticker: ``TICKER: PASS | fail_daily | fail_intraday — reason``."""
    by_symbol = {row["symbol"]: row for row in report.get("details", [])}
    out = []
    for row in report.get("all_results", []):
        detail = by_symbol.get(row["symbol"], {})
        label = detail.get("ticker", row["symbol"])
        out.append(f"{label}: {row['result']} — {detail.get('reason', 'no reason recorded')}")
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Trend Join Long scanner on broker candles")
    parser.add_argument("--force", action="store_true",
                        help="scan outside 10:00-15:30 New York and mark the report as forced")
    parser.add_argument("--dir", default=".", help="where to write the report (default: this directory)")
    parser.add_argument("--json", action="store_true", help="print the report instead of the summary lines")
    args = parser.parse_args(argv)

    started = datetime.now(timezone.utc)
    report = scan(started, force=args.force)
    path = write_report(report, started, Path(args.dir))
    report["elapsed_seconds"] = round((datetime.now(timezone.utc) - started).total_seconds(), 1)

    if args.json:
        print(json.dumps(report, indent=2))
        return 0
    print(f"saved {path}")
    if report.get("error"):
        print(f"no scan: {report['error']}")
        return 0
    if report["time_gate"].get("forced"):
        print(f"FORCED run outside the window ({report['time_gate']['reason']}) - not a live signal")
    for line in lines(report):
        print(line)
    print(f"{len(report['hits'])} of {report['candidates_checked']} passed "
          f"in {report['elapsed_seconds']}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
