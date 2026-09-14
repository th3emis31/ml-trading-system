"""Economic calendar from ForexFactory's public weekly export (information only, never trades).

Source: https://nfs.faireconomy.media/ff_calendar_thisweek.json - the export behind
https://www.forexfactory.com/calendar (title, currency, time with New York offset, impact,
forecast, previous). The page itself sits behind Cloudflare, so the export is used instead.

* The feed is downloaded at most once per ``MIN_FETCH_MINUTES`` and cached in
  ``data/economic_calendar/thisweek.json``; a failed download keeps the last good copy and says so.
* Times are converted to UTC. Gold and bitcoin are driven by US data, so ``RELEVANT_CURRENCIES``
  maps both symbols to USD (plus "All" for global events such as G7 meetings).
* ``news_window`` reports whether a high-impact event is near. It is context for the owner and the
  reports; nothing in the order path reads it. With no data it returns ``available: False`` and never
  invents events.
"""
from __future__ import annotations

import argparse
import json
import os
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

from .walkforward_backtest import _iso  # shared UTC "%Y-%m-%d %H:%M" formatter

ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / "data" / "economic_calendar"
FEED_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
SOURCE_PAGE = "https://www.forexfactory.com/calendar"
MIN_FETCH_MINUTES = 60          # be polite to the free export: one download an hour at most
STALE_HOURS = 24                # older cached data is still shown but flagged stale
IMPACT_RANK = {"Holiday": 0, "Non-Economic": 0, "Low": 1, "Medium": 2, "High": 3}
RELEVANT_CURRENCIES = {"XAUUSD": ("USD", "All"), "BTCUSD": ("USD", "All")}
WINDOW_BEFORE_MIN = 30
WINDOW_AFTER_MIN = 30


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def parse_events(raw) -> list[dict]:
    """Normalise the export's rows; rows without a readable date are skipped, not guessed."""
    events = []
    for row in raw if isinstance(raw, list) else []:
        try:
            when = datetime.fromisoformat(str(row["date"])).astimezone(timezone.utc)
        except (KeyError, ValueError, TypeError):
            continue
        impact = str(row.get("impact") or "")
        events.append({"title": str(row.get("title") or "").strip(), "currency": str(row.get("country") or ""),
                       "time_utc": _iso(when), "impact": impact, "impact_rank": IMPACT_RANK.get(impact, 0),
                       "forecast": row.get("forecast") or None, "previous": row.get("previous") or None})
    events.sort(key=lambda e: e["time_utc"])
    return events


def _download(url: str = FEED_URL, timeout: float = 20.0):
    request = urllib.request.Request(url, headers={"User-Agent": "SmartEntry economic calendar (hourly cache)"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def load_calendar(now: Optional[datetime] = None, fetch: Callable = _download, cache_dir: Path = CACHE_DIR,
                  force: bool = False) -> dict:
    """Cached calendar; downloads when the cache is missing or older than MIN_FETCH_MINUTES (or ``force``)."""
    now = _utc(now or datetime.now(timezone.utc))
    cache = Path(cache_dir) / "thisweek.json"
    cached = None
    if cache.exists():
        try:
            cached = json.loads(cache.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cached = None
    fetched_at = None
    if cached and cached.get("fetched_at"):
        fetched_at = datetime.strptime(cached["fetched_at"], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
    error = None
    if force or fetched_at is None or now - fetched_at >= timedelta(minutes=MIN_FETCH_MINUTES):
        try:
            raw = fetch()
            events = parse_events(raw)
            if not events:
                raise ValueError("the feed returned no readable events")
            cached = {"fetched_at": _iso(now), "source": FEED_URL, "events": events}
            cache.parent.mkdir(parents=True, exist_ok=True)
            tmp = cache.with_suffix(".tmp")
            tmp.write_text(json.dumps(cached, indent=1), encoding="utf-8")
            os.replace(tmp, cache)
            fetched_at = now
        except Exception as exc:  # keep the last good copy; report the failure instead of inventing data
            error = f"download failed: {exc}"
    if not cached or not cached.get("events"):
        return {"available": False, "reason": error or "no calendar data yet", "source": FEED_URL,
                "source_page": SOURCE_PAGE, "events": [], "places_orders": False}
    age_hours = (now - fetched_at).total_seconds() / 3600 if fetched_at else None
    return {"available": True, "source": FEED_URL, "source_page": SOURCE_PAGE, "fetched_at": cached["fetched_at"],
            "age_hours": round(age_hours, 2) if age_hours is not None else None,
            "stale": age_hours is None or age_hours > STALE_HOURS, "last_error": error,
            "events": cached["events"], "places_orders": False}


def relevant_events(events: list[dict], symbol: Optional[str] = None, min_impact: str = "Medium") -> list[dict]:
    currencies = RELEVANT_CURRENCIES.get(str(symbol or "").upper())
    floor = IMPACT_RANK.get(min_impact, 0)
    return [e for e in events if e["impact_rank"] >= floor and (currencies is None or e["currency"] in currencies)]


def upcoming(events: list[dict], now: datetime, hours: float = 48, symbol: Optional[str] = None,
             min_impact: str = "Medium") -> list[dict]:
    now = _utc(now)
    start, end = _iso(now - timedelta(minutes=WINDOW_AFTER_MIN)), _iso(now + timedelta(hours=hours))
    return [e for e in relevant_events(events, symbol, min_impact) if start <= e["time_utc"] <= end]


def news_window(events: list[dict], symbol: str, now: datetime, before_min: int = WINDOW_BEFORE_MIN,
                after_min: int = WINDOW_AFTER_MIN) -> dict:
    """Whether a high-impact event for ``symbol`` is within ``before_min`` minutes ahead or ``after_min`` behind."""
    now = _utc(now)
    high = relevant_events(events, symbol, "High")
    near = []
    for event in high:
        when = datetime.strptime(event["time_utc"], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        minutes = round((when - now).total_seconds() / 60)
        if -after_min <= minutes <= before_min:
            near.append({**event, "minutes_to": minutes})
    later = [e for e in high if e["time_utc"] > _iso(now)]
    return {"symbol": symbol, "in_window": bool(near), "events": near, "before_min": before_min, "after_min": after_min,
            "next_high_impact": later[0] if later else None}


def calendar_overview(now: Optional[datetime] = None, fetch: Callable = _download, cache_dir: Path = CACHE_DIR,
                      force: bool = False, hours: float = 48) -> dict:
    """What the app, reports and pages show: this week's relevant events, the next 48 h and the news window per symbol."""
    now = _utc(now or datetime.now(timezone.utc))
    calendar = load_calendar(now, fetch, cache_dir, force)
    if not calendar["available"]:
        return calendar
    events = calendar["events"]
    return {**{k: v for k, v in calendar.items() if k != "events"}, "generated_at": _iso(now),
            "counts": {impact: sum(1 for e in events if e["impact"] == impact) for impact in ("High", "Medium", "Low")},
            "week_usd_high_medium": relevant_events(events, "XAUUSD", "Medium"),
            "next_48h": upcoming(events, now, hours, "XAUUSD", "Medium"),
            "news_window": {symbol: news_window(events, symbol, now) for symbol in RELEVANT_CURRENCIES},
            "all_events": events}


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="Economic calendar from ForexFactory (information only, never trades).")
    parser.add_argument("--refresh", action="store_true", help=f"download now even if the cache is under {MIN_FETCH_MINUTES} min old")
    args = parser.parse_args(argv)
    overview = calendar_overview(force=args.refresh)
    if not overview["available"]:
        print(f"calendar unavailable: {overview['reason']}")
        return 1
    print(f"fetched {overview['fetched_at']} UTC (age {overview['age_hours']} h, stale {overview['stale']}); counts {overview['counts']}")
    for symbol, window in overview["news_window"].items():
        nxt = window["next_high_impact"] or {}
        print(f"{symbol}: in news window {window['in_window']}; next high impact {nxt.get('time_utc')} {nxt.get('title')}")
    for event in overview["next_48h"]:
        print(f"  {event['time_utc']} UTC  {event['impact']:<6} {event['currency']}  {event['title']}  "
              f"(forecast {event['forecast']}, previous {event['previous']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
