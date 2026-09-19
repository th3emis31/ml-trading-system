"""Trend Join Long scanner: the gate, the windows and the verdict.

Every test builds its own candles, so nothing here needs the broker feed. The point of
these is that a PASS means what it says: measured on closed bars, from one price source,
inside the hours the owner asked for.
"""
import json
from datetime import datetime, timezone

import pandas as pd

from src import tjl_scanner as tjl

NY = tjl.NEW_YORK


def _ny_clock(text: str) -> datetime:
    """A New York wall-clock time, as the UTC instant the scanner is given."""
    return pd.Timestamp(text, tz=NY).tz_convert("UTC").to_pydatetime()


def _daily_bars(closes, highs=None, last_open="2026-09-18 21:00"):
    """Daily candles labelled by their 21:00 UTC open, newest last."""
    n = len(closes)
    opens = pd.date_range(end=pd.Timestamp(last_open, tz="UTC"), periods=n, freq="1D")
    highs = highs if highs is not None else [c + 1 for c in closes]
    return pd.DataFrame({"datetime": opens, "open": closes, "high": highs,
                         "low": [c - 1 for c in closes], "close": closes, "volume": [1] * n})


def _minute_bars(rows):
    """``rows`` = (New York time string, high, close)."""
    return pd.DataFrame({"datetime": [pd.Timestamp(t, tz=NY).tz_convert("UTC") for t, _, _ in rows],
                         "open": [c for _, _, c in rows],
                         "high": [h for _, h, _ in rows],
                         "low": [c - 1 for _, _, c in rows],
                         "close": [c for _, _, c in rows],
                         "volume": [1] * len(rows)})


# --- the gate -------------------------------------------------------------------

def test_gate_opens_inside_the_window_on_a_weekday():
    gate = tjl.time_gate(_ny_clock("2026-09-18 11:00"))  # Friday
    assert gate["open"] is True
    assert "inside" in gate["reason"]


def test_gate_is_shut_before_ten_and_after_half_past_three():
    assert tjl.time_gate(_ny_clock("2026-09-18 09:59"))["open"] is False
    assert tjl.time_gate(_ny_clock("2026-09-18 15:30"))["open"] is False
    assert tjl.time_gate(_ny_clock("2026-09-18 15:29"))["open"] is True


def test_gate_is_shut_at_the_weekend_even_inside_the_hours():
    gate = tjl.time_gate(_ny_clock("2026-09-19 11:00"))  # Saturday
    assert gate["open"] is False
    assert "not a trading day" in gate["reason"]


def test_a_shut_gate_writes_an_error_report_and_checks_nothing():
    report = tjl.scan(_ny_clock("2026-09-19 11:00"))
    assert report["hits"] == [] and report["all_results"] == []
    assert report["candidates_checked"] == 0
    assert "outside the trading window" in report["error"]


def test_forcing_a_scan_is_recorded_as_forced(monkeypatch):
    monkeypatch.setattr(tjl, "TICKERS", ())
    report = tjl.scan(_ny_clock("2026-09-19 11:00"), force=True)
    assert report["time_gate"]["forced"] is True
    assert "error" not in report


# --- how far back the minute candles must reach ---------------------------------

def test_minute_request_reaches_past_the_premarket_open():
    """The specification's 400 candles stop at 08:50, after the 04:00 window has opened."""
    needed = tjl.minute_bars_needed(_ny_clock("2026-09-18 15:30"))
    assert needed >= (15 * 60 + 30) - (4 * 60)
    assert needed > tjl.MIN_MINUTE_BARS


def test_minute_request_never_drops_below_the_specified_floor():
    assert tjl.minute_bars_needed(_ny_clock("2026-09-18 04:05")) == tjl.MIN_MINUTE_BARS


# --- the daily levels -----------------------------------------------------------

def test_previous_day_is_the_last_closed_day_not_the_forming_one():
    """The broker's daily candle opens at 21:00 UTC, so the newest row is usually still open."""
    bars = _daily_bars([100.0] * 199 + [200.0, 999.0], highs=[101.0] * 199 + [201.0, 1999.0])
    levels = tjl.daily_levels(bars, _ny_clock("2026-09-19 11:00"))  # Sat 11:00 NY = 15:00 UTC
    assert levels["prev_daily_close"] == 200.0  # not 999.0
    assert levels["prev_daily_high"] == 201.0
    assert levels["closed_days"] == 200


def test_the_average_is_refused_when_two_hundred_closed_days_are_missing():
    levels = tjl.daily_levels(_daily_bars([100.0] * 50), _ny_clock("2026-09-19 11:00"))
    assert levels["sma200"] is None
    assert "need 200" in levels["note"]


def test_no_daily_candles_gives_no_levels_rather_than_an_error():
    levels = tjl.daily_levels(pd.DataFrame(columns=["datetime", "high", "close"]), _ny_clock("2026-09-19 11:00"))
    assert levels["prev_daily_high"] is None and levels["sma200"] is None


# --- the intraday levels --------------------------------------------------------

def test_premarket_and_session_highs_use_their_own_windows():
    now = _ny_clock("2026-09-18 11:00")
    bars = _minute_bars([("2026-09-18 03:59", 500.0, 10.0),   # before 04:00, ignored
                         ("2026-09-18 05:00", 120.0, 10.0),   # premarket
                         ("2026-09-18 09:29", 130.0, 10.0),   # premarket, last minute of it
                         ("2026-09-18 09:30", 140.0, 10.0),   # session
                         ("2026-09-18 10:59", 150.0, 42.0)])  # session, the closed bar
    levels = tjl.intraday_levels(bars, now)
    assert levels["pmh"] == 130.0
    assert levels["today_hod"] == 150.0
    assert levels["curr_px"] == 42.0
    assert levels["premarket_bars"] == 2 and levels["session_bars"] == 2


def test_the_forming_minute_is_excluded_from_the_session_high():
    now = _ny_clock("2026-09-18 11:00")
    bars = _minute_bars([("2026-09-18 05:00", 120.0, 10.0),
                         ("2026-09-18 10:58", 150.0, 40.0),
                         ("2026-09-18 11:00", 900.0, 99.0)])  # opened at 11:00, closes at 11:01
    levels = tjl.intraday_levels(bars, now)
    assert levels["today_hod"] == 150.0
    assert levels["curr_px"] == 40.0


def test_yesterdays_candles_do_not_become_todays_highs():
    now = _ny_clock("2026-09-18 11:00")
    bars = _minute_bars([("2026-09-17 05:00", 900.0, 10.0),
                         ("2026-09-17 10:00", 900.0, 10.0),
                         ("2026-09-18 05:00", 120.0, 10.0),
                         ("2026-09-18 10:00", 150.0, 40.0)])
    levels = tjl.intraday_levels(bars, now)
    assert levels["pmh"] == 120.0 and levels["today_hod"] == 150.0


# --- the verdict ----------------------------------------------------------------

DAILY_OK = {"prev_daily_high": 100.0, "prev_daily_close": 99.0, "sma200": 90.0}
INTRA_OK = {"curr_px": 105.0, "pmh": 101.0, "today_hod": 102.0}


def test_pass_needs_all_four_conditions():
    verdict = tjl.evaluate(DAILY_OK, INTRA_OK)
    assert verdict["result"] == "PASS"
    assert verdict["daily_breakout"] and verdict["intraday_breakout"]


def test_price_below_yesterdays_high_fails_the_daily_half():
    verdict = tjl.evaluate({**DAILY_OK, "prev_daily_high": 120.0}, INTRA_OK)
    assert verdict["result"] == "fail_daily"
    assert "not above yesterday's high" in verdict["reason"]


def test_a_close_below_the_two_hundred_day_average_fails_the_daily_half():
    verdict = tjl.evaluate({**DAILY_OK, "sma200": 150.0}, INTRA_OK)
    assert verdict["result"] == "fail_daily"
    assert "200-day" in verdict["reason"]


def test_the_daily_half_is_reported_first_when_both_halves_fail():
    verdict = tjl.evaluate({**DAILY_OK, "sma200": 150.0}, {**INTRA_OK, "pmh": 999.0})
    assert verdict["result"] == "fail_daily"


def test_price_below_the_premarket_or_session_high_fails_the_intraday_half():
    assert tjl.evaluate(DAILY_OK, {**INTRA_OK, "pmh": 110.0})["result"] == "fail_intraday"
    assert tjl.evaluate(DAILY_OK, {**INTRA_OK, "today_hod": 110.0})["result"] == "fail_intraday"


def test_equal_is_not_a_breakout():
    assert tjl.evaluate({**DAILY_OK, "prev_daily_high": 105.0}, INTRA_OK)["result"] == "fail_daily"
    assert tjl.evaluate(DAILY_OK, {**INTRA_OK, "today_hod": 105.0})["result"] == "fail_intraday"


def test_a_missing_level_is_no_data_and_never_a_pass_or_a_fail():
    verdict = tjl.evaluate(DAILY_OK, {**INTRA_OK, "pmh": None})
    assert verdict["result"] == "no_data"
    assert "premarket high" in verdict["reason"]
    assert verdict["daily_breakout"] is None


# --- the report -----------------------------------------------------------------

def test_a_scan_writes_the_requested_schema(monkeypatch, tmp_path):
    now = _ny_clock("2026-09-18 11:00")

    def fake_bars(symbol, timeframe, count):
        if timeframe == "1d":
            return _daily_bars([90.0] * 199 + [99.0, 999.0],
                               highs=[91.0] * 199 + [100.0, 1999.0], last_open="2026-09-17 21:00")
        return _minute_bars([("2026-09-18 05:00", 101.0, 100.0),
                             ("2026-09-18 10:59", 102.0, 105.0)])

    monkeypatch.setattr(tjl, "fetch_app_bars", fake_bars)
    monkeypatch.setattr(tjl, "TICKERS", (("GOLD", "XAUUSD"),))
    report = tjl.scan(now)
    path = tjl.write_report(report, now, tmp_path)

    assert path.name == "tjl_watchlist_2026-09-18_1100ET.json"
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["scanned_at"] and saved["candidates_checked"] == 1
    assert saved["all_results"] == [{"symbol": "XAUUSD", "result": "PASS"}]
    assert saved["hits"] == [{"symbol": "XAUUSD", "curr_price": 105.0, "prev_daily_high": 100.0,
                              "sma200": 90.045, "pmh": 101.0, "today_hod": 102.0}]
    assert tjl.lines(report)[0].startswith("GOLD: PASS — ")


def test_an_empty_feed_is_no_data_rather_than_a_crash(monkeypatch):
    monkeypatch.setattr(tjl, "fetch_app_bars", lambda *a, **k: pd.DataFrame(columns=tjl.pd.Index(["datetime"])))
    monkeypatch.setattr(tjl, "TICKERS", (("BTC", "BTCUSD"),))
    report = tjl.scan(_ny_clock("2026-09-18 11:00"))
    assert report["all_results"] == [{"symbol": "BTCUSD", "result": "no_data"}]
    assert "no daily or one-minute candles" in report["details"][0]["reason"]


def test_the_scanner_cannot_place_an_order():
    """A scanner that can trade is a different kind of program. This one only reads and writes JSON."""
    source = (tjl.__file__).replace(".pyc", ".py")
    text = open(source, encoding="utf-8").read()
    for forbidden in ("order_send", "place_order", "execute", "auto_trade", "MetaTrader5"):
        assert forbidden not in text, f"{forbidden} must not appear in the scanner"
