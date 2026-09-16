"""Event defence (16 Sep 2026 FOMC candle): tier-1 window, pre-event alert, volatility breaker, historical events."""
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from src import event_defence as ed

FOMC = {"event": "FOMC_DECISION", "time_utc": datetime(2026, 9, 16, 18, 0, tzinfo=timezone.utc), "currency": "USD"}


def _at(h, m):
    return datetime(2026, 9, 16, h, m, tzinfo=timezone.utc)


def test_tier1_window_blocks_60_before_to_90_after():
    assert not ed.tier1_window([FOMC], _at(16, 59))["entries_blocked"]
    assert ed.tier1_window([FOMC], _at(17, 0))["entries_blocked"]
    assert ed.tier1_window([FOMC], _at(19, 30))["entries_blocked"]
    assert not ed.tier1_window([FOMC], _at(19, 31))["entries_blocked"]
    assert not ed.tier1_window([FOMC], _at(18, 0), symbol="EURJPY")["entries_blocked"], "only USD-affected symbols"


def test_alert_runs_in_the_hour_before_and_never_changes_positions():
    assert not ed.tier1_window([FOMC], _at(16, 59))["alert"]["active"]
    alert = ed.tier1_window([FOMC], _at(17, 30), symbol="BTCUSD")["alert"]
    assert alert["active"] and "reduce or flatten open BTCUSD positions" in alert["message"] and "Dry run" in alert["message"]
    assert not ed.tier1_window([FOMC], _at(18, 0))["alert"]["active"], "the alert ends when the event starts"


def test_live_calendar_titles_map_to_tier1_and_others_are_ignored():
    rows = [{"title": "Federal Funds Rate", "currency": "USD", "time_utc": "2026-09-16 18:00"},
            {"title": "FOMC Press Conference", "currency": "USD", "time_utc": "2026-09-16 18:30"},
            {"title": "Core CPI m/m", "currency": "USD", "time_utc": "2026-10-14 12:30"},
            {"title": "Non-Farm Employment Change", "currency": "USD", "time_utc": "2026-10-02 12:30"},
            {"title": "Unemployment Claims", "currency": "USD", "time_utc": "2026-09-17 12:30"},
            {"title": "CPI y/y", "currency": "GBP", "time_utc": "2026-09-16 06:00"}]
    kinds = [e["event"] for e in ed.tier1_events_from_calendar(rows)]
    assert kinds == ["FOMC_DECISION", "FOMC_PRESS_CONFERENCE", "NFP", "CPI"]


def test_vectorised_mask_matches_the_live_window():
    times = pd.date_range("2026-09-16 16:00", "2026-09-16 21:00", freq="15min", tz="UTC")
    mask = ed.entries_blocked_mask(times, [FOMC])
    expected = [ed.tier1_window([FOMC], t)["entries_blocked"] for t in times]
    assert mask.tolist() == expected


def test_breaker_blocks_the_three_bars_after_a_spike_and_logs(tmp_path):
    n = 40
    close = 4300 + np.arange(n) * 0.5
    bars = pd.DataFrame({"datetime": pd.date_range("2026-09-15 00:00", periods=n, freq="1h", tz="UTC"),
                         "open": close, "high": close + 5, "low": close - 5, "close": close})
    bars.loc[30, "high"], bars.loc[30, "low"] = close[30] + 45, close[30] - 45   # 90 range vs ATR ~10
    triggered, blocked = ed.volatility_breaker_mask(bars)
    assert np.flatnonzero(triggered).tolist() == [30]
    assert np.flatnonzero(blocked).tolist() == [31, 32, 33]
    live = ed.breaker_status(bars.iloc[:32])
    assert live["entries_blocked"] and live["trigger"]["range"] == 90.0 and live["trigger"]["bars_left"] == 2
    assert not ed.breaker_status(bars)["entries_blocked"]
    log = tmp_path / "event_defence_log.jsonl"
    ed.log_event_defence({"kind": "volatility_breaker", "symbol": "XAUUSD", **live["trigger"]}, path=log)
    row = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
    assert row["dry_run"] is True and row["kind"] == "volatility_breaker"


def test_historical_events_loader_skips_untimed_rows(tmp_path):
    path = tmp_path / "events.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["event", "date_et", "time_et", "datetime_utc", "tier", "currency", "source", "note"])
        w.writeheader()
        w.writerow({"event": "FOMC_DECISION", "date_et": "2026-09-16", "time_et": "14:00", "datetime_utc": "2026-09-16 18:00", "tier": "1", "currency": "USD"})
        w.writerow({"event": "FOMC_UNSCHEDULED", "date_et": "2020-03-15", "time_et": "", "datetime_utc": "", "tier": "1", "currency": "USD"})
    events = ed.load_historical_events(path)
    assert [e["event"] for e in events] == ["FOMC_DECISION"] and events[0]["time_utc"].hour == 18


def test_the_shipped_historical_events_file_is_complete_and_honest():
    path = Path(__file__).resolve().parents[1] / "data" / "historical_events.csv"
    rows = list(csv.DictReader(path.open(newline="", encoding="utf-8")))
    counts = {k: sum(1 for r in rows if r["event"] == k) for k in ("FOMC_DECISION", "CPI", "NFP", "FOMC_MINUTES", "FOMC_PRESS_CONFERENCE")}
    assert counts["FOMC_DECISION"] >= 69 and counts["CPI"] >= 104 and counts["NFP"] >= 104
    assert all(r["source"] for r in rows), "every row names its official source"
    assert max(r["date_et"] for r in rows) <= "2026-09-16", "history only, no future dates"
    assert {"event": "FOMC_DECISION", "datetime_utc": "2026-09-16 18:00"}.items() <= next(
        r for r in rows if r["date_et"] == "2026-09-16" and r["event"] == "FOMC_DECISION").items()
    assert not any(r["date_et"].startswith("2025-11-") and r["event"] == "CPI" and "October 2025" in r["note"] for r in rows), \
        "October 2025 CPI was never published"


def test_event_defence_endpoint_reports_and_logs_without_touching_orders(tmp_path, monkeypatch):
    import app as app_module
    from src import economic_calendar

    monkeypatch.setattr(economic_calendar, "load_calendar", lambda now=None, **kw: {
        "available": True, "events": [{"title": "Federal Funds Rate", "currency": "USD", "time_utc": "2026-09-16 18:00"}]})
    n = 40
    close = 4300 + np.arange(n) * 0.5
    bars = pd.DataFrame({"datetime": pd.date_range("2026-09-15 01:00", periods=n, freq="1h", tz="UTC"),
                         "open": close, "high": close + 5, "low": close - 5, "close": close})
    monkeypatch.setattr(app_module, "get_bars", lambda symbol, timeframe, count=600: (bars, "mt5:" + symbol))
    log = tmp_path / "event_defence_log.jsonl"
    monkeypatch.setattr(ed, "event_defence_log_path", lambda: log)
    monkeypatch.setattr(app_module, "_EVENT_DEFENCE_LOGGED", set())
    client = app_module.app.test_client()
    body = client.get("/api/event-defence?now=2026-09-16T17:30:00Z").get_json()
    assert body["dry_run"] is True and body["places_orders"] is False
    gold = body["symbols"]["XAUUSD"]
    assert gold["entries_blocked"] is True and gold["tier1"]["alert"]["active"] is True
    assert gold["volatility_breaker"]["available"] and gold["volatility_breaker"]["entries_blocked"] is False
    kinds = [json.loads(line)["kind"] for line in log.read_text(encoding="utf-8").splitlines()]
    assert kinds.count("tier1_window_block") == 2 and kinds.count("tier1_pre_event_alert") == 2   # XAUUSD and BTCUSD
    client.get("/api/event-defence?now=2026-09-16T17:31:00Z")
    assert len(log.read_text(encoding="utf-8").splitlines()) == len(kinds), "each event is logged once, not on every poll"
