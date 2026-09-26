"""The audit must find real faults and must not invent them out of normal market closures.

Getting the second half wrong is the more dangerous failure, and it happened during the build: a
hard-coded "a Friday gap is the weekend" rule reported 20,017 missing bars on XAUUSD 4h - 55.6 % of the
history - because gold also closes for about an hour every day. An audit that cries wolf is worse than
none, because the real holes then hide inside the noise.
"""
from __future__ import annotations

import pandas as pd

from src.data_audit import audit_bars, candle_faults, gap_report, trading_slots


def _ohlc_frame(rows):
    """rows of (timestamp, open, high, low, close). The timestamp may be a string or already tz-aware."""
    def stamp(ts):
        value = pd.Timestamp(ts)
        return value.tz_localize("UTC") if value.tzinfo is None else value.tz_convert("UTC")

    return pd.DataFrame([{"datetime": stamp(ts), "open": o, "high": h,
                          "low": l, "close": c, "volume": 1.0} for ts, o, h, l, c in rows])


def _clean_4h(hours, start="2026-01-05 00:00"):
    """A continuous 4h series from a Monday, so nothing is a closure."""
    base = pd.Timestamp(start, tz="UTC")
    return _ohlc_frame([(base + pd.Timedelta(hours=4 * i), 100.0, 100.5, 99.5, 100.0) for i in range(hours)])


# --- faults that must be found -------------------------------------------------------------------

def test_a_high_below_its_own_low_is_found():
    out = candle_faults(_ohlc_frame([("2026-01-05 00:00", 100, 99, 101, 100)]))
    assert out["impossible_high_low"]["count"] == 1
    assert out["total"] >= 1


def test_a_close_outside_the_bar_range_is_found():
    out = candle_faults(_ohlc_frame([("2026-01-05 00:00", 100, 101, 99, 105)]))
    assert out["close_outside_range"]["count"] == 1


def test_an_open_outside_the_bar_range_is_found():
    out = candle_faults(_ohlc_frame([("2026-01-05 00:00", 90, 101, 99, 100)]))
    assert out["open_outside_range"]["count"] == 1


def test_a_non_positive_price_is_found_because_one_zero_ruins_every_return():
    out = candle_faults(_ohlc_frame([("2026-01-05 00:00", 100, 101, 0.0, 100)]))
    assert out["non_positive"]["count"] == 1


def test_clean_candles_produce_no_faults():
    out = candle_faults(_clean_4h(20))
    assert out["total"] == 0


def test_faults_are_sampled_not_just_counted():
    """A count alone is not actionable - the report must say WHEN."""
    rows = [("2026-01-05 00:00", 100, 99, 101, 100), ("2026-01-05 04:00", 100, 99, 101, 100)]
    out = candle_faults(_ohlc_frame(rows))
    assert out["impossible_high_low"]["count"] == 2
    assert out["impossible_high_low"]["examples"], "an example timestamp must be given"


def test_duplicated_timestamps_are_reported():
    frame = _clean_4h(10)
    doubled = pd.concat([frame, frame.iloc[[3]]], ignore_index=True).sort_values("datetime")
    out = audit_bars(doubled.reset_index(drop=True), "4h", "TEST")
    assert out["duplicated_rows"] >= 2
    assert any("duplicat" in p for p in out["problems"])


def test_bars_out_of_time_order_are_reported():
    frame = _clean_4h(10)
    shuffled = frame.iloc[[0, 2, 1] + list(range(3, 10))].reset_index(drop=True)
    out = audit_bars(shuffled, "4h", "TEST")
    assert out["in_time_order"] is False
    assert any("time order" in p for p in out["problems"])


# --- and closures that must NOT be reported as faults --------------------------------------------

def test_a_market_that_never_trades_a_slot_is_not_missing_bars_there():
    """The failure that would make this audit useless. Gold closes daily and at weekends; an absence from
    a slot it never trades is the market being shut, not a hole in the data."""
    base = pd.Timestamp("2026-01-05 00:00", tz="UTC")
    rows = []
    for day in range(40):                      # 40 days, trading only 00:00 08:00 16:00, never 04:00/12:00/20:00
        for hour in (0, 8, 16):
            stamp = base + pd.Timedelta(days=day, hours=hour)
            if stamp.dayofweek >= 5:           # and never at the weekend
                continue
            rows.append((stamp, 100.0, 100.5, 99.5, 100.0))
    out = gap_report(_ohlc_frame(rows), "4h")
    assert out["available"]
    assert out["missing_bars_total"] == 0, (
        f"a consistent session was read as {out['missing_bars_total']} missing bars: {out['examples']}")
    assert out["buckets"]["closed"] > 0, "the closures must be counted somewhere, and reported as closed"


def test_learned_slots_differ_between_a_24_7_and_a_24_5_instrument():
    base = pd.Timestamp("2026-01-05 00:00", tz="UTC")
    always = pd.Series([base + pd.Timedelta(hours=4 * i) for i in range(6 * 60)])
    weekdays = pd.Series([t for t in always if t.dayofweek < 5])
    assert len(trading_slots(always, 240)) > len(trading_slots(weekdays, 240))


def test_a_genuine_hole_inside_the_session_is_counted():
    # Long enough for every weekly slot to recur many times. On a 10-day fixture this test failed, and
    # correctly so: with one occurrence per slot, a removed bar leaves its slot with NO occurrences, and
    # the detector cannot then tell "never traded" from "the only instance is missing". That limitation is
    # recorded in the module docstring; real audits run on thousands of bars.
    frame = _clean_4h(60 * 6)
    holed = frame.drop(index=[200, 201]).reset_index(drop=True)   # two bars removed mid-run
    out = gap_report(holed, "4h")
    assert out["missing_bars_total"] >= 2, f"buckets {out['buckets']} examples {out['examples']}"
    assert out["worst_gap"] is not None


def test_a_timestamp_off_the_claimed_grid_is_reported_as_odd():
    frame = _clean_4h(20)
    shifted = frame.copy()
    shifted.loc[10, "datetime"] = shifted.loc[10, "datetime"] + pd.Timedelta(minutes=17)
    out = gap_report(shifted, "4h")
    assert out["buckets"]["odd"] >= 1


def test_an_unknown_timeframe_is_refused_rather_than_guessed():
    assert gap_report(_clean_4h(10), "7h")["available"] is False


# --- the whole audit ----------------------------------------------------------------------------

def test_a_clean_series_is_reported_clean():
    out = audit_bars(_clean_4h(200), "4h", "TEST")
    assert out["clean"] is True, f"problems: {out['problems']}"
    assert out["places_orders"] is False


def test_the_audit_reports_and_never_repairs():
    """A silent repair to price history is worse than a known hole: the hole can be worked around."""
    frame = _clean_4h(40)
    before = frame.copy(deep=True)
    audit_bars(frame, "4h", "TEST")
    assert frame.equals(before), "the audit must not modify the frame it was given"


def test_no_bars_is_reported_rather_than_crashing():
    out = audit_bars(_ohlc_frame([]), "4h", "TEST")
    assert out["available"] is False and "no bars" in out["reason"]


def test_a_run_of_motionless_bars_is_flagged_as_a_possible_stalled_feed():
    base = pd.Timestamp("2026-01-05 00:00", tz="UTC")
    rows = [(base + pd.Timedelta(hours=4 * i), 100.0, 100.0, 100.0, 100.0) for i in range(12)]
    out = audit_bars(_ohlc_frame(rows), "4h", "TEST")
    assert out["longest_frozen_run"] >= 5
    assert any("motionless" in p for p in out["problems"])
