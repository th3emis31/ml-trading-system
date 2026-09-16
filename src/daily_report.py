"""Daily report: deep market analysis for XAUUSD and BTCUSD plus a one-page system brief.

Runs every morning (Windows task "SmartEntry Daily Report", 06:45) after the System Doctor's
deep check. Analysis only: it never places orders, and the bias it prints is context from
trends and momentum, not a trade signal.

Per market, from the broker's own candles (read through the app, closed bars only):
* multi-timeframe trend, RSI, ATR and range position (the app's Data Feed),
* key levels: previous day, 5/20/55-day highs and lows and recent swing points, each as a
  distance in % and in ATR, with the nearest support and resistance,
* volatility regime: today's ATR against its one-year percentile and 20-day realised volatility,
* momentum: daily RSI and 1/5/20/60-day returns, distance to the daily EMA50/EMA200,
* the last full UTC day's Asia / London / New York session ranges,
* gold-bitcoin correlation of daily returns.
The system brief adds the doctor's verdict, Strategy Lab progress, the paper trader, demo
execution, and the full daily schedule. The economic calendar (next 48 h of USD high/medium
events and the news window per symbol) comes from ForexFactory's export via
``src.economic_calendar``; news headlines are not included (no reliable source), and the report says so.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import pandas as pd

from .features import compute_atr, compute_ema, compute_rsi
from .system_doctor import ROOT, _read_json, check_scheduled_tasks, get_json

REPORT_DIR = ROOT / "data" / "daily_reports"
SYMBOLS = ("XAUUSD", "BTCUSD")
PERIODS_PER_YEAR = {"XAUUSD": 252, "BTCUSD": 365}
SESSIONS_UTC = {"Asia": (0, 8), "London": (7, 16), "New York": (12, 21)}
TIMEFRAME_WEIGHTS = {"1d": 3.0, "4h": 2.0, "1h": 1.0, "15m": 0.5}
SCHEDULE = {
    "SmartEntry Paper Trader": ("every hour at :05", "Gold 4H model decides on each closed bar; fresh BUY/SELL go to the demo executor."),
    "SmartEntry Strategy Lab": ("every hour at :20", "Searches rule strategies for 40 minutes with a locked holdout."),
    "SmartEntry System Doctor": ("every 30 minutes", "Quick health checks; recreates a missing task."),
    "SmartEntry System Doctor Daily": ("daily 06:30", "Deep health check: compiles all code and runs the test suite."),
    "SmartEntry Daily Report": ("daily 06:45", "This report: market analysis for gold and bitcoin plus the system brief."),
    "SmartEntry Daily Learning": ("daily 05:30", "Self-learning: retrains gold and bitcoin models on new candles; keeps a new model only if better on unseen bars."),
    "SmartEntry Obsidian Notes": ("every hour at :50", "Writes daily notes, the daily plan, learning verdict, research log and trade journal into the Obsidian vault."),
    "SmartEntry Daily Agent": ("every hour at :10", "Paper trading agent: research, decision per closed H4 candle for gold and bitcoin, and a journal entry for every decision."),
}


def _load_app_bars(symbol: str, timeframe: str, count: int) -> pd.DataFrame:
    from .mtf_data import fetch_app_bars

    return fetch_app_bars(symbol, timeframe, count)


def _closed(frame: Optional[pd.DataFrame], minutes: int, now: datetime) -> Optional[pd.DataFrame]:
    from .paper_trader import drop_forming_bars

    if frame is None or frame.empty:
        return frame
    return drop_forming_bars(frame, minutes, now)


def _level(name: str, price: float, close: float, atr: float) -> dict:
    return {"name": name, "price": round(float(price), 2), "distance_pct": round((float(price) / close - 1) * 100, 2),
            "distance_atr": round((float(price) - close) / atr, 2) if atr > 0 else None}


def swing_points(daily: pd.DataFrame, lookback: int = 90, wing: int = 2, keep: int = 3) -> tuple[list, list]:
    part = daily.iloc[-lookback:].reset_index(drop=True)
    highs, lows = part["high"].to_numpy(dtype=float), part["low"].to_numpy(dtype=float)
    times = pd.to_datetime(part["datetime"], utc=True)
    swing_highs, swing_lows = [], []
    for i in range(wing, len(part) - wing):
        window_h, window_l = highs[i - wing:i + wing + 1], lows[i - wing:i + wing + 1]
        if highs[i] == window_h.max() and highs[i] > highs[i - wing:i].max():
            swing_highs.append((times.iloc[i].strftime("%Y-%m-%d"), float(highs[i])))
        if lows[i] == window_l.min() and lows[i] < lows[i - wing:i].min():
            swing_lows.append((times.iloc[i].strftime("%Y-%m-%d"), float(lows[i])))
    return swing_highs[-keep:], swing_lows[-keep:]


def analyse_levels(daily: pd.DataFrame) -> dict:
    d = daily.reset_index(drop=True)
    close = float(d["close"].iloc[-1])
    atr = float(compute_atr(d.astype({"high": float, "low": float, "close": float})).iloc[-1])
    prev = d.iloc[-2] if len(d) >= 2 else d.iloc[-1]
    levels = [_level("Previous day high", prev["high"], close, atr), _level("Previous day low", prev["low"], close, atr),
              _level("Previous day close", prev["close"], close, atr)]
    for days in (5, 20, 55):
        window = d.iloc[-days - 1:-1] if len(d) > days else d
        levels += [_level(f"{days}-day high", window["high"].max(), close, atr), _level(f"{days}-day low", window["low"].min(), close, atr)]
    swing_highs, swing_lows = swing_points(d)
    swings = [_level(f"Swing high {day}", price, close, atr) for day, price in swing_highs] + \
             [_level(f"Swing low {day}", price, close, atr) for day, price in swing_lows]
    candidates = levels + swings
    above = sorted((lv for lv in candidates if lv["price"] > close), key=lambda lv: lv["price"])
    below = sorted((lv for lv in candidates if lv["price"] < close), key=lambda lv: lv["price"], reverse=True)
    return {"close": round(close, 2), "atr": round(atr, 2), "levels": levels, "swings": swings,
            "nearest_resistance": above[0] if above else None, "nearest_support": below[0] if below else None}


def analyse_volatility(daily: pd.DataFrame, symbol: str) -> dict:
    d = daily.reset_index(drop=True).astype({"high": float, "low": float, "close": float})
    atr_pct = (compute_atr(d) / d["close"]).dropna()
    current = float(atr_pct.iloc[-1])
    history = atr_pct.iloc[-252:]
    percentile = float((history < current).mean() * 100)
    returns = np.log(d["close"]).diff().dropna()
    realised = float(returns.iloc[-20:].std() * math.sqrt(PERIODS_PER_YEAR.get(symbol, 252)) * 100)
    regime = "low" if percentile < 30 else "high" if percentile > 70 else "normal"
    return {"atr_pct": round(current * 100, 3), "atr_percentile_1y": round(percentile, 1),
            "realised_vol_20d_pct": round(realised, 1), "regime": regime}


def analyse_momentum(daily: pd.DataFrame) -> dict:
    close = daily["close"].astype(float).reset_index(drop=True)
    rsi = float(compute_rsi(close, 14).iloc[-1])
    returns = {f"{n}d": round((close.iloc[-1] / close.iloc[-1 - n] - 1) * 100, 2) for n in (1, 5, 20, 60) if len(close) > n}
    ema50, ema200 = float(compute_ema(close, 50).iloc[-1]), float(compute_ema(close, 200).iloc[-1])
    return {"rsi_14": round(rsi, 1), "rsi_state": "overbought" if rsi >= 70 else "oversold" if rsi <= 30 else "neutral",
            "returns_pct": returns, "vs_ema50_pct": round((close.iloc[-1] / ema50 - 1) * 100, 2),
            "vs_ema200_pct": round((close.iloc[-1] / ema200 - 1) * 100, 2)}


def analyse_sessions(hourly: Optional[pd.DataFrame], now: datetime) -> dict:
    if hourly is None or hourly.empty:
        return {"available": False, "reason": "no hourly candles"}
    frame = hourly.copy()
    frame["datetime"] = pd.to_datetime(frame["datetime"], utc=True)
    days = sorted({stamp.date() for stamp in frame["datetime"] if stamp.date() < now.date()})
    if not days:
        return {"available": False, "reason": "no completed day in the hourly candles"}
    # Gold closes at the weekend and opens late on Sunday, so the last calendar day can hold only a
    # couple of bars and no session at all. Use the latest completed day that has a bar in every session.
    def covers_every_session(day_rows):
        hours = set(day_rows["datetime"].dt.hour)
        return all(any(start <= hour < end for hour in hours) for start, end in SESSIONS_UTC.values())

    day = next((d for d in reversed(days) if covers_every_session(frame[frame["datetime"].dt.date == d])), days[-1])
    rows = frame[frame["datetime"].dt.date == day]
    sessions = {}
    for name, (start, end) in SESSIONS_UTC.items():
        part = rows[(rows["datetime"].dt.hour >= start) & (rows["datetime"].dt.hour < end)]
        if part.empty:
            sessions[name] = None
            continue
        high, low = float(part["high"].max()), float(part["low"].min())
        sessions[name] = {"high": round(high, 2), "low": round(low, 2), "range": round(high - low, 2),
                          "return_pct": round((float(part["close"].iloc[-1]) / float(part["open"].iloc[0]) - 1) * 100, 3)}
    return {"available": True, "date": str(day), "day_range": round(float(rows["high"].max() - rows["low"].min()), 2),
            "high_hour_utc": int(rows.loc[rows["high"].idxmax(), "datetime"].hour),
            "low_hour_utc": int(rows.loc[rows["low"].idxmin(), "datetime"].hour), "sessions": sessions}


def returns_correlation(first: pd.DataFrame, second: pd.DataFrame, days: int = 60) -> Optional[float]:
    def daily_close(frame):
        stamps = pd.to_datetime(frame["datetime"], utc=True).dt.date
        return frame.assign(date=stamps.to_numpy()).groupby("date")["close"].last().astype(float)

    joined = pd.concat([np.log(daily_close(first)).diff(), np.log(daily_close(second)).diff()], axis=1, join="inner").dropna()
    joined = joined.iloc[-days:]
    if len(joined) < 20:
        return None
    return round(float(joined.iloc[:, 0].corr(joined.iloc[:, 1])), 3)


def market_bias(timeframes: list, momentum: dict) -> dict:
    score, weight_total, reasons = 0.0, 0.0, []
    for row in timeframes or []:
        if not row.get("available") or row.get("trend") not in ("up", "down"):
            continue
        weight = TIMEFRAME_WEIGHTS.get(row["timeframe"], 0.0)
        score += weight * (1 if row["trend"] == "up" else -1)
        weight_total += weight
        reasons.append(f"{row['timeframe']} trend {row['trend']} (EMA50 vs EMA200)")
    rsi = momentum.get("rsi_14")
    if rsi is not None and rsi >= 70:
        score -= 1.0
        reasons.append(f"daily RSI {rsi} is overbought: pullback risk")
    elif rsi is not None and rsi <= 30:
        score += 1.0
        reasons.append(f"daily RSI {rsi} is oversold: bounce risk")
    normalised = score / weight_total if weight_total else 0.0
    label = "bullish" if normalised >= 0.4 else "bearish" if normalised <= -0.4 else "neutral / mixed"
    return {"label": label, "score": round(normalised, 2), "reasons": reasons,
            "note": "Context from trends and momentum, not a trade signal."}


def system_brief(get: Callable = get_json) -> dict:
    doctor = _read_json(ROOT / "data" / "system_health" / "doctor_latest.json") or {}
    deep = _read_json(ROOT / "data" / "system_health" / "doctor_latest_deep.json") or {}
    brief = {
        "doctor": {"overall": doctor.get("overall"), "checked_at": doctor.get("generated_at"),
                   "problems": [f"{c['name']}: {c['summary']}" for c in doctor.get("checks") or [] if c.get("status") in ("warn", "fail")]},
        "deep_check": {"overall": deep.get("overall"), "checked_at": deep.get("generated_at"),
                       "code": [f"{c['name']}: {c['summary']}" for c in deep.get("checks") or [] if c.get("area") == "code"]},
    }
    try:
        from .strategy_lab import lab_summary

        lab = lab_summary(limit=3)
        brief["strategy_lab"] = {"status": lab["status"], "markets": {k: v.get("counts") for k, v in lab["markets"].items()},
                                 "top": [{"market": r["market"], "description": r["description"],
                                          "holdout_passed": (r.get("holdout_verdict") or {}).get("passed")} for r in lab["leaderboard"]]}
    except Exception as exc:
        brief["strategy_lab"] = {"error": str(exc)}
    try:
        from .paper_trader import load_state, summarize

        paper = summarize(load_state())
        brief["paper_trader"] = {k: paper.get(k) for k in ("last_run", "last_error", "last_message", "bars_decided", "signals",
                                                           "closed_trades", "metrics", "open_trade")}
    except Exception as exc:
        brief["paper_trader"] = {"error": str(exc)}
    config = _read_json(ROOT / "data" / "paper_trading" / "demo_execution.json") or {}
    journal = _read_json(ROOT / "data" / "paper_trading" / "demo_execution_journal.json") or {}
    _, demo_status = get("/api/demo-model/status", 30)
    brief["demo_execution"] = {"enabled": config.get("enabled"), "dry_run": config.get("dry_run"),
                               "account": config.get("account_login"),
                               "open_positions": (demo_status or {}).get("open_positions"),
                               "recent_events": list(reversed((journal.get("events") or [])[-5:]))}
    return brief


def schedule_overview(csv_text: Optional[str] = None) -> list[dict]:
    try:
        tasks = check_scheduled_tasks(csv_text)["detail"]["tasks"]
    except Exception as exc:
        return [{"task": "Task Scheduler", "error": str(exc)}]
    rows = []
    for name, info in tasks.items():
        when, what = SCHEDULE.get(name, ("", ""))
        rows.append({"task": name, "when": when, "what": what, **(info or {"status": "missing"})})
    return rows


def economic_calendar_section(now: datetime, calendar: Optional[Callable] = None) -> dict:
    """Next 48 h of USD high/medium events and the news window per symbol, from the ForexFactory export."""
    try:
        if calendar is None:
            from .economic_calendar import calendar_overview as calendar
        overview = calendar(now=now)
    except Exception as exc:
        return {"available": False, "reason": f"calendar unavailable: {exc}"}
    if not overview.get("available"):
        return {"available": False, "reason": overview.get("reason"), "source": overview.get("source")}
    return {k: overview.get(k) for k in ("available", "source", "source_page", "fetched_at", "age_hours", "stale",
                                         "last_error", "counts", "next_48h", "news_window")}


def build_daily_report(now: Optional[datetime] = None, get: Callable = get_json,
                       loader: Optional[Callable] = None, calendar: Optional[Callable] = None) -> dict:
    now = now or datetime.now(timezone.utc)
    loader = loader or _load_app_bars
    code, feed = get("/api/data-feed", 120)
    feed_symbols = {item["symbol"]: item for item in (feed or {}).get("symbols") or []} if code == 200 else {}
    markets, dailies = {}, {}
    for symbol in SYMBOLS:
        info = feed_symbols.get(symbol, {})
        section = {"symbol": symbol, "timeframes": info.get("timeframes") or [], "alignment": info.get("alignment"),
                   "market_closed": info.get("market_closed"), "quotes": info.get("quotes")}
        try:
            daily = _closed(loader(symbol, "1d", 400), 1440, now)
            hourly = _closed(loader(symbol, "1h", 240), 60, now)
        except Exception as exc:
            daily, hourly = None, None
            section["reason"] = f"candles unavailable: {exc}"
        if daily is None or len(daily) < 60:
            section.update({"available": False, "reason": section.get("reason") or "not enough daily broker candles"})
            markets[symbol] = section
            continue
        dailies[symbol] = daily
        section.update({
            "available": True,
            "last_daily_bar": pd.Timestamp(daily["datetime"].iloc[-1]).strftime("%Y-%m-%d"),
            "levels": analyse_levels(daily),
            "volatility": analyse_volatility(daily, symbol),
            "momentum": analyse_momentum(daily),
            "sessions": analyse_sessions(hourly, now),
        })
        section["bias"] = market_bias(section["timeframes"], section["momentum"])
        markets[symbol] = section
    correlation = returns_correlation(dailies["XAUUSD"], dailies["BTCUSD"]) if len(dailies) == 2 else None
    return {
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "date": now.strftime("%Y-%m-%d"),
        "places_orders": False,
        "markets": markets,
        "gold_btc_correlation_60d": correlation,
        "system": system_brief(get),
        "schedule": schedule_overview(),
        "economic_calendar": economic_calendar_section(now, calendar),
        "not_included": ["News headlines: no reliable source is connected (the economic calendar comes from ForexFactory)."],
    }


def save_daily_report(report: dict, report_dir: Path = REPORT_DIR) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    text = json.dumps(report, indent=1, default=str)
    dated = report_dir / f"{report['date']}.json"
    dated.write_text(text, encoding="utf-8")
    latest = report_dir / "latest.json"
    tmp = latest.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, latest)
    return dated


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="Daily market analysis and system brief (never trades).")
    parser.parse_args(argv)
    report = build_daily_report()
    path = save_daily_report(report)
    print(f"saved {path}")
    for symbol, section in report["markets"].items():
        if not section.get("available"):
            print(f"{symbol}: unavailable ({section.get('reason')})")
            continue
        lv, vol, mom, bias = section["levels"], section["volatility"], section["momentum"], section["bias"]
        support, resistance = lv.get("nearest_support") or {}, lv.get("nearest_resistance") or {}
        print(f"{symbol}: close {lv['close']} | bias {bias['label']} ({bias['score']}) | vol {vol['regime']} "
              f"(ATR {vol['atr_pct']}%, {vol['atr_percentile_1y']} pct) | RSI {mom['rsi_14']} | "
              f"support {support.get('name')} {support.get('price')} | resistance {resistance.get('name')} {resistance.get('price')}")
    print("gold-bitcoin 60d correlation:", report["gold_btc_correlation_60d"])
    print("doctor:", report["system"]["doctor"].get("overall"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
