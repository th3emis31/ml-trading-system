import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import economic_calendar as ec

RAW = [
    {"title": "FOMC Statement", "country": "USD", "date": "2026-09-16T14:00:00-04:00", "impact": "High", "forecast": "", "previous": ""},
    {"title": "Federal Funds Rate", "country": "USD", "date": "2026-09-16T14:00:00-04:00", "impact": "High", "forecast": "4.00%", "previous": "3.75%"},
    {"title": "Retail Sales m/m", "country": "USD", "date": "2026-09-15T08:30:00-04:00", "impact": "Medium", "forecast": "0.3%", "previous": "0.5%"},
    {"title": "CPI y/y", "country": "GBP", "date": "2026-09-16T02:00:00-04:00", "impact": "High", "forecast": "3.1%", "previous": "3.2%"},
    {"title": "Bank Holiday", "country": "JPY", "date": "2026-09-14T00:00:00-04:00", "impact": "Holiday", "forecast": "", "previous": ""},
    {"title": "broken row", "country": "USD", "date": "not a date", "impact": "High"},
]
NOW = datetime(2026, 9, 16, 17, 45, tzinfo=timezone.utc)  # 15 minutes before the FOMC (18:00 UTC)


def test_parse_converts_new_york_times_to_utc_and_skips_unreadable_rows():
    events = ec.parse_events(RAW)
    assert len(events) == 5 and all(e["title"] != "broken row" for e in events)
    fomc = next(e for e in events if e["title"] == "FOMC Statement")
    assert fomc["time_utc"] == "2026-09-16 18:00" and fomc["impact_rank"] == 3 and fomc["forecast"] is None
    assert [e["time_utc"] for e in events] == sorted(e["time_utc"] for e in events)
    assert ec.parse_events({"not": "a list"}) == []


def test_news_window_and_relevant_currencies():
    events = ec.parse_events(RAW)
    window = ec.news_window(events, "XAUUSD", NOW)
    assert window["in_window"] and {e["minutes_to"] for e in window["events"]} == {15}
    assert all(e["currency"] == "USD" for e in window["events"])            # GBP CPI does not count for gold
    after = ec.news_window(events, "XAUUSD", datetime(2026, 9, 16, 19, 0, tzinfo=timezone.utc))
    assert not after["in_window"] and after["next_high_impact"] is None
    early = ec.news_window(events, "BTCUSD", datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc))
    assert not early["in_window"] and early["next_high_impact"]["title"] in ("FOMC Statement", "Federal Funds Rate")
    nxt = ec.upcoming(events, datetime(2026, 9, 15, 0, 0, tzinfo=timezone.utc), hours=48, symbol="XAUUSD")
    assert [e["title"] for e in nxt] == ["Retail Sales m/m", "FOMC Statement", "Federal Funds Rate"] or \
        [e["title"] for e in nxt] == ["Retail Sales m/m", "Federal Funds Rate", "FOMC Statement"]


def test_cache_limits_downloads_and_keeps_last_good_copy_on_failure(tmp_path):
    calls = []

    def fetch():
        calls.append(1)
        return RAW

    first = ec.load_calendar(NOW, fetch, tmp_path)
    assert first["available"] and len(calls) == 1 and (tmp_path / "thisweek.json").exists()
    again = ec.load_calendar(datetime(2026, 9, 16, 18, 30, tzinfo=timezone.utc), fetch, tmp_path)
    assert again["available"] and len(calls) == 1                           # 45 min later: served from cache

    def broken():
        raise OSError("network down")

    later = ec.load_calendar(datetime(2026, 9, 16, 19, 0, tzinfo=timezone.utc), broken, tmp_path)
    assert later["available"] and "network down" in later["last_error"] and len(later["events"]) == 5
    stale = ec.load_calendar(datetime(2026, 9, 18, 19, 0, tzinfo=timezone.utc), broken, tmp_path)
    assert stale["available"] and stale["stale"] is True


def test_no_data_is_reported_never_invented(tmp_path):
    def broken():
        raise OSError("network down")

    result = ec.calendar_overview(NOW, broken, tmp_path)
    assert result["available"] is False and "network down" in result["reason"] and result["events"] == []
    empty = ec.load_calendar(NOW, lambda: [], tmp_path / "empty")
    assert empty["available"] is False and result["places_orders"] is False


def test_overview_shape(tmp_path):
    overview = ec.calendar_overview(NOW, lambda: RAW, tmp_path)
    json.dumps(overview)
    assert overview["available"] and overview["counts"]["High"] == 3 and overview["places_orders"] is False
    assert overview["news_window"]["XAUUSD"]["in_window"] and set(overview["news_window"]) == {"XAUUSD", "BTCUSD"}
    assert all(e["currency"] in ("USD", "All") for e in overview["week_usd_high_medium"])
