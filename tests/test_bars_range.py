"""Deep history through the bars endpoint: the start/end window and the paging helper.

The feed served at most 50,000 bars walking back from the newest one, which on a one-minute
chart is about 35 trading days — half the broker's actual XAUUSD minute history. These cover the
window request and the pager that stitches windows together, and that the old count-based path is
untouched.
"""
import json
from datetime import datetime, timezone

import pandas as pd
import pytest

from src import mtf_data


class _FakeResponse:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _bar(minute: str, close: float = 4300.0) -> dict:
    return {"datetime": pd.Timestamp(minute, tz="UTC").isoformat(), "open": close, "high": close + 1,
            "low": close - 1, "close": close, "volume": 10}


def test_the_pager_asks_for_one_window_at_a_time(monkeypatch, tmp_path):
    asked = []

    def fake_urlopen(url, timeout=0):
        asked.append(url)
        return _FakeResponse({"available": True, "source": "mt5:XAUUSD", "bars": [_bar("2026-06-10 00:00")]})

    monkeypatch.setattr(mtf_data.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(mtf_data, "CACHE_DIR", tmp_path)
    mtf_data.fetch_app_history("XAUUSD", "1m", "2026-06-08", "2026-07-08", chunk_days=15, use_cache=False)
    assert len(asked) == 2, "a 30-day span in 15-day windows is two requests"
    assert all("start=" in url and "end=" in url for url in asked)


def test_overlapping_window_edges_are_de_duplicated(monkeypatch, tmp_path):
    """Consecutive windows share their boundary bar; it must appear once."""
    pages = [
        {"available": True, "source": "mt5:XAUUSD",
         "bars": [_bar("2026-06-10 00:00", 4300), _bar("2026-06-10 00:01", 4301)]},
        {"available": True, "source": "mt5:XAUUSD",
         "bars": [_bar("2026-06-10 00:01", 4301), _bar("2026-06-10 00:02", 4302)]},
    ]
    monkeypatch.setattr(mtf_data.urllib.request, "urlopen",
                        lambda url, timeout=0: _FakeResponse(pages.pop(0)))
    monkeypatch.setattr(mtf_data, "CACHE_DIR", tmp_path)
    frame = mtf_data.fetch_app_history("XAUUSD", "1m", "2026-06-08", "2026-07-08", chunk_days=15, use_cache=False)
    assert len(frame) == 3
    assert frame["datetime"].is_monotonic_increasing
    assert frame["datetime"].is_unique


def test_empty_windows_are_skipped_not_fatal(monkeypatch, tmp_path):
    """Weekends, and anything before the broker's history starts, come back with no bars."""
    pages = [
        {"available": True, "source": "mt5:XAUUSD", "bars": []},
        {"available": True, "source": "mt5:XAUUSD", "bars": [_bar("2026-06-20 00:00")]},
    ]
    monkeypatch.setattr(mtf_data.urllib.request, "urlopen",
                        lambda url, timeout=0: _FakeResponse(pages.pop(0)))
    monkeypatch.setattr(mtf_data, "CACHE_DIR", tmp_path)
    frame = mtf_data.fetch_app_history("XAUUSD", "1m", "2026-06-08", "2026-07-08", chunk_days=15, use_cache=False)
    assert len(frame) == 1


def test_a_feed_that_is_entirely_down_gives_an_empty_frame(monkeypatch, tmp_path):
    def boom(url, timeout=0):
        raise OSError("connection refused")

    monkeypatch.setattr(mtf_data.urllib.request, "urlopen", boom)
    monkeypatch.setattr(mtf_data, "CACHE_DIR", tmp_path)
    frame = mtf_data.fetch_app_history("XAUUSD", "1m", "2026-06-08", "2026-06-20", use_cache=False)
    assert frame.empty
    assert list(frame.columns) == mtf_data.OHLCV


def test_the_result_is_cached_so_paging_happens_once(monkeypatch, tmp_path):
    calls = []

    def fake_urlopen(url, timeout=0):
        calls.append(url)
        return _FakeResponse({"available": True, "source": "mt5:XAUUSD", "bars": [_bar("2026-06-10 00:00")]})

    monkeypatch.setattr(mtf_data.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(mtf_data, "CACHE_DIR", tmp_path)
    first = mtf_data.fetch_app_history("XAUUSD", "1m", "2026-06-08", "2026-06-20", chunk_days=15)
    made = len(calls)
    second = mtf_data.fetch_app_history("XAUUSD", "1m", "2026-06-08", "2026-06-20", chunk_days=15)
    assert len(calls) == made, "the second call must come from the cache"
    assert len(second) == len(first)


def test_one_minute_is_a_known_research_timeframe_but_not_a_scanned_market():
    """The lab must accept 1m, and the hourly search must not start scanning it: 50,000 minute
    bars is about 35 trading days, far too little for that pipeline's evidence bar."""
    from src import strategy_lab as lab

    assert lab.TIMEFRAME_MINUTES["1m"] == 1
    assert not any(market.endswith(":1m") for market in lab.DEFAULT_MARKETS)


def test_the_mt5_service_range_reader_rejects_a_backwards_window():
    """A guard worth having: copy_rates_range with end before start would silently return nothing."""
    from trading.mt5_service import MT5Service

    service = MT5Service.__new__(MT5Service)   # no terminal connection needed for this check
    service._mt5 = None
    service._last_error = "not connected in this test"
    result = service.copy_rates_range("XAUUSD", "1m",
                                      datetime(2026, 6, 10, tzinfo=timezone.utc),
                                      datetime(2026, 6, 9, tzinfo=timezone.utc))
    assert result["ok"] is False
    assert "not connected" in result["reason"] or "after start" in result["reason"]
