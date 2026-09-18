import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd
import pytest

from src import daily_report as dr


def _daily(n=400, seed=1, start="2025-08-01"):
    rng = np.random.default_rng(seed)
    close = 2000 * np.exp(np.cumsum(rng.normal(0.0005, 0.01, n)))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) * (1 + rng.uniform(0.001, 0.01, n))
    low = np.minimum(open_, close) * (1 - rng.uniform(0.001, 0.01, n))
    return pd.DataFrame({"datetime": pd.date_range(start, periods=n, freq="D", tz="UTC"), "open": open_, "high": high,
                         "low": low, "close": close, "volume": 1.0})


def test_levels_have_support_below_and_resistance_above():
    daily = _daily()
    levels = dr.analyse_levels(daily)
    close = levels["close"]
    assert levels["atr"] > 0
    if levels["nearest_support"]:
        assert levels["nearest_support"]["price"] < close
    if levels["nearest_resistance"]:
        assert levels["nearest_resistance"]["price"] > close
    names = [lv["name"] for lv in levels["levels"]]
    assert "Previous day high" in names and "55-day low" in names


def test_previous_day_levels_use_the_last_closed_day():
    # The callers pass closed days only; the last row is yesterday (17 Sep 2026: the FOMC day, high 4367.47).
    daily = _daily(n=100)
    daily.loc[99, ["high", "low", "close"]] = [daily["high"].max() * 1.05, daily["low"].min() * 0.95, daily.loc[98, "close"]]
    by_name = {lv["name"]: lv["price"] for lv in dr.analyse_levels(daily)["levels"]}
    assert by_name["Previous day high"] == round(daily.loc[99, "high"], 2)
    assert by_name["Previous day low"] == round(daily.loc[99, "low"], 2)
    assert by_name["5-day high"] == round(daily.loc[99, "high"], 2), "yesterday belongs in the 5-day window"
    assert by_name["20-day low"] == round(daily.loc[99, "low"], 2)


def test_swing_points_find_a_clear_peak_and_trough():
    daily = _daily(n=120)
    daily.loc[100, "high"] = daily["high"].max() * 1.2
    daily.loc[80, "low"] = daily["low"].min() * 0.8
    highs, lows = dr.swing_points(daily, keep=100)  # the default keeps only the 3 most recent swings
    assert any(abs(price - daily.loc[100, "high"]) < 1e-9 for _, price in highs)
    assert any(abs(price - daily.loc[80, "low"]) < 1e-9 for _, price in lows)


def test_volatility_and_momentum_ranges():
    daily = _daily()
    vol = dr.analyse_volatility(daily, "XAUUSD")
    assert 0 <= vol["atr_percentile_1y"] <= 100 and vol["regime"] in ("low", "normal", "high") and vol["realised_vol_20d_pct"] > 0
    mom = dr.analyse_momentum(daily)
    assert 0 <= mom["rsi_14"] <= 100 and set(mom["returns_pct"]) == {"1d", "5d", "20d", "60d"}


def test_sessions_use_the_last_completed_utc_day():
    times = pd.date_range("2026-09-10 00:00", periods=72, freq="h", tz="UTC")
    hourly = pd.DataFrame({"datetime": times, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0})
    hourly.loc[(hourly["datetime"] == pd.Timestamp("2026-09-12 14:00", tz="UTC")), "high"] = 110.0
    result = dr.analyse_sessions(hourly, datetime(2026, 9, 13, 6, 45, tzinfo=timezone.utc))
    assert result["available"] and result["date"] == "2026-09-12" and result["high_hour_utc"] == 14
    assert result["sessions"]["London"]["range"] == pytest.approx(11.0) and result["sessions"]["Asia"]["range"] == pytest.approx(2.0)


def test_sessions_skip_a_closed_market_day():
    # Friday full day, Saturday closed, Sunday only 22:00-23:00 (gold reopening); report run on Monday.
    friday = pd.date_range("2026-09-11 00:00", periods=21, freq="h", tz="UTC")
    sunday = pd.date_range("2026-09-13 22:00", periods=2, freq="h", tz="UTC")
    hourly = pd.DataFrame({"datetime": friday.append(sunday), "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0})
    result = dr.analyse_sessions(hourly, datetime(2026, 9, 14, 5, 45, tzinfo=timezone.utc))
    assert result["available"] and result["date"] == "2026-09-11"
    assert all(result["sessions"][name] is not None for name in ("Asia", "London", "New York"))


def test_correlation_and_bias():
    daily = _daily()
    assert dr.returns_correlation(daily, daily) == pytest.approx(1.0)
    up = [{"timeframe": tf, "available": True, "trend": "up"} for tf in ("1d", "4h", "1h")]
    down = [{"timeframe": tf, "available": True, "trend": "down"} for tf in ("1d", "4h", "1h")]
    assert dr.market_bias(up, {"rsi_14": 55})["label"] == "bullish"
    assert dr.market_bias(down, {"rsi_14": 50})["label"] == "bearish"
    mixed = [{"timeframe": "1d", "available": True, "trend": "up"}, {"timeframe": "4h", "available": True, "trend": "down"},
             {"timeframe": "1h", "available": True, "trend": "down"}]
    assert dr.market_bias(mixed, {"rsi_14": 75})["label"] == "neutral / mixed"


def test_build_daily_report_offline(monkeypatch, tmp_path):
    monkeypatch.setattr(dr, "system_brief", lambda get: {"doctor": {"overall": "healthy"}})
    monkeypatch.setattr(dr, "schedule_overview", lambda csv_text=None: [{"task": "SmartEntry Daily Report", "status": "Ready"}])
    feed = {"symbols": [{"symbol": sym, "timeframes": [{"timeframe": "1d", "available": True, "trend": "up"}],
                         "alignment": {"label": "all timeframes up"}, "market_closed": False} for sym in dr.SYMBOLS]}
    get = lambda path, timeout: (200, feed)

    def loader(symbol, timeframe, count):
        if timeframe == "1d":
            return _daily(seed=2 if symbol == "XAUUSD" else 3)
        times = pd.date_range("2026-09-01", periods=count, freq="h", tz="UTC")
        return pd.DataFrame({"datetime": times, "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.0})

    calendar = lambda now: {"available": True, "fetched_at": "2026-09-13 06:00", "counts": {"High": 1},
                            "next_48h": [{"time_utc": "2026-09-14 12:30", "impact": "High", "currency": "USD", "title": "CPI m/m"}],
                            "news_window": {"XAUUSD": {"in_window": False}}, "all_events": ["not copied"]}
    report = dr.build_daily_report(now=datetime(2026, 9, 13, 6, 45, tzinfo=timezone.utc), get=get, loader=loader,
                                   calendar=calendar)
    json.dumps(report)
    assert report["economic_calendar"]["next_48h"][0]["title"] == "CPI m/m" and "all_events" not in report["economic_calendar"]
    down = dr.economic_calendar_section(datetime(2026, 9, 13, tzinfo=timezone.utc),
                                        lambda now: (_ for _ in ()).throw(OSError("offline")))
    assert down["available"] is False and "offline" in down["reason"]
    assert report["places_orders"] is False
    assert all(report["markets"][sym]["available"] for sym in dr.SYMBOLS)
    assert report["markets"]["XAUUSD"]["bias"]["label"] == "bullish"
    assert report["gold_btc_correlation_60d"] is not None
    path = dr.save_daily_report(report, report_dir=tmp_path)
    assert path.name == "2026-09-13.json" and (tmp_path / "latest.json").exists()


def test_the_daily_candle_is_labelled_by_the_session_it_covers():
    """The broker's daily candle opens at 21:00 UTC, so labelling it by its open date made a current report read as a
    day stale: the candle stamped 16 September holds Wednesday the 17th."""
    import pandas as pd

    from src.daily_report import _daily_bar_window

    broker = _daily_bar_window(pd.Timestamp("2026-09-16 21:00", tz="UTC"))
    assert broker["opens_utc"] == "2026-09-16 21:00" and broker["closes_utc"] == "2026-09-17 21:00"
    assert broker["covers"] == "2026-09-17", "a trader calls this Wednesday's candle"

    midnight = _daily_bar_window(pd.Timestamp("2026-09-16 00:00", tz="UTC"))
    assert midnight["covers"] == "2026-09-16", "a candle that opens at midnight covers its own date"
    naive = _daily_bar_window(pd.Timestamp("2026-09-16 21:00"))
    assert naive["covers"] == "2026-09-17", "a stamp without a timezone is read as UTC, not rejected"
