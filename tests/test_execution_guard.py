from datetime import datetime, timezone

import pandas as pd

from src import execution_guard as guard

NOW = datetime(2026, 9, 15, 14, 10, tzinfo=timezone.utc)


def _hourly(last_open: str, n: int = 5) -> pd.DataFrame:
    opens = pd.date_range(end=pd.Timestamp(last_open, tz="UTC"), periods=n, freq="1h")
    return pd.DataFrame({"datetime": opens, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0})


def test_secret_created_once_and_compared_safely(tmp_path):
    path = tmp_path / "control_api.json"
    first = guard.load_or_create_secret(path)
    assert first == guard.load_or_create_secret(path) and len(first) >= 20
    assert guard.secret_matches(first, first)
    assert not guard.secret_matches("wrong", first) and not guard.secret_matches(None, first) and not guard.secret_matches(first, "")


def test_fresh_bar_needs_a_recent_closed_broker_bar():
    # 14:00 bar is still forming at 14:10; the 13:00 bar closed at 14:00 -> 10 minutes old
    assert guard.fresh_bar_check(_hourly("2026-09-15 14:00"), "mt5:XAUUSD", NOW)["ok"]
    stale = guard.fresh_bar_check(_hourly("2026-09-15 11:00"), "mt5:XAUUSD", NOW)
    assert not stale["ok"] and "stale" in stale["reason"]
    yahoo = guard.fresh_bar_check(_hourly("2026-09-15 14:00"), "yahoo:1h", NOW)
    assert not yahoo["ok"] and "no broker bars" in yahoo["reason"]
    assert not guard.fresh_bar_check(None, "mt5:XAUUSD", NOW)["ok"]


def test_spread_must_be_small_against_the_stop():
    ok = guard.spread_check({"ok": True, "bid": 4285.0, "ask": 4285.3}, stop_distance=20.0)
    wide = guard.spread_check({"ok": True, "bid": 4285.0, "ask": 4291.0}, stop_distance=20.0)
    assert ok["ok"] and not wide["ok"] and "spread" in wide["reason"]
    assert not guard.spread_check({"ok": False, "message": "MT5 is not connected"}, 20.0)["ok"]
    assert not guard.spread_check({"ok": True, "bid": 1.0, "ask": 1.1}, 0)["ok"]


def test_news_window_and_missing_calendar_reject():
    event = {"title": "US CPI", "currency": "USD", "impact": "High", "impact_rank": 3, "time_utc": "2026-09-15 14:30"}
    inside = guard.news_check({"available": True, "events": [event]}, "XAUUSD", NOW)
    assert not inside["ok"] and "US CPI" in inside["reason"]
    later = dict(event, time_utc="2026-09-15 18:00")
    assert guard.news_check({"available": True, "events": [later]}, "XAUUSD", NOW)["ok"]
    assert not guard.news_check({"available": False, "reason": "download failed"}, "XAUUSD", NOW)["ok"]


def test_pre_order_checks_collect_every_reason_and_rejections_are_logged(tmp_path):
    result = guard.pre_order_checks(symbol="XAUUSD", now=NOW, bars=None, bars_source="yahoo", quote=None,
                                    stop_distance=10.0, calendar=None)
    assert not result["ok"] and len(result["reasons"]) == 3
    good = guard.pre_order_checks(symbol="XAUUSD", now=NOW, bars=_hourly("2026-09-15 14:00"), bars_source="mt5:XAUUSD",
                                  quote={"ok": True, "bid": 4285.0, "ask": 4285.2}, stop_distance=10.0,
                                  calendar={"available": True, "events": []})
    assert good["ok"] and good["reasons"] == []
    log = tmp_path / "rejections.jsonl"
    guard.log_rejection({"source": "http", "symbol": "XAUUSD", "http_status": 403, "reason": "missing control secret"}, log)
    rows = guard.recent_rejections(10, log)
    assert rows[-1]["reason"] == "missing control secret" and rows[-1]["http_status"] == 403
