"""The owner's Thursday/Friday/Monday rule: the calendar, the measurement, and the two bugs it hid.

The calendar is the part that quietly ruins this kind of test, and the touch-order assumption is the
part that quietly decides the result. Both are pinned here.
"""
import pandas as pd
import pytest

from src import weekly_structure as ws


def _daily_sessions(rows, start="2026-01-01 21:00"):
    """rows = (open, high, low, close), one per session, stamped like the broker's 21:00 daily bar."""
    stamps = pd.date_range(start, periods=len(rows), freq="1D", tz="UTC")
    return pd.DataFrame({"datetime": stamps,
                         "open": [r[0] for r in rows], "high": [r[1] for r in rows],
                         "low": [r[2] for r in rows], "close": [r[3] for r in rows],
                         "volume": [1.0] * len(rows)})


# --- the calendar ----------------------------------------------------------------

def test_a_bar_is_labelled_by_the_session_it_covers_not_its_stamp():
    """The broker's daily candle opens 21:00 UTC, so a Sunday-stamped bar is Monday's session."""
    frame = ws.session_frame(_daily_sessions([(1, 2, 0, 1)] * 3, start="2026-01-04 21:00"))  # Sun, Mon, Tue
    assert list(frame["weekday"]) == ["Monday", "Tuesday", "Wednesday"]


def test_a_week_needs_thursday_friday_and_monday_in_order():
    # stamps Wed..Sun -> sessions Thu, Fri, Sat, Sun, Mon: the weekend breaks the triplet
    frame = ws.session_frame(_daily_sessions([(1, 2, 0, 1)] * 5, start="2026-01-07 21:00"))
    assert ws.weeks(frame) == []


def test_a_clean_thursday_friday_monday_is_found():
    # Wed 21:00 -> Thursday, Thu 21:00 -> Friday, then Sun 21:00 -> Monday
    stamps = [pd.Timestamp("2026-01-07 21:00", tz="UTC"),   # Thursday session
              pd.Timestamp("2026-01-08 21:00", tz="UTC"),   # Friday session
              pd.Timestamp("2026-01-11 21:00", tz="UTC")]   # Monday session
    frame = pd.DataFrame({"datetime": stamps, "open": [10, 10, 10], "high": [12, 11, 11],
                          "low": [8, 9, 7], "close": [10, 10, 9], "volume": [1.0] * 3})
    found = ws.weeks(ws.session_frame(frame))
    assert len(found) == 1
    assert found[0]["thursday"]["high"] == 12 and found[0]["monday"]["low"] == 7


# --- the rule ---------------------------------------------------------------------

def _week(th_high, th_low, fr_high, fr_low, mo_low, mo_high):
    return {"thursday": {"high": th_high, "low": th_low, "session_date": "t"},
            "friday": {"high": fr_high, "low": fr_low, "session_date": "f"},
            "monday": {"high": mo_high, "low": mo_low, "open": 100.0, "close": 100.0,
                       "session_date": "2026-01-12", "datetime": pd.Timestamp("2026-01-11 21:00", tz="UTC")}}


def test_the_bearish_setup_is_friday_failing_to_take_thursdays_high():
    assert ws.classify_week(_week(110, 90, 109, 95, 94, 108))["bearish_setup"] is True
    assert ws.classify_week(_week(110, 90, 111, 95, 94, 112))["bearish_setup"] is False


def test_the_bearish_hit_is_monday_reaching_fridays_low():
    assert ws.classify_week(_week(110, 90, 109, 95, 95, 108))["bearish_hit"] is True   # touched
    assert ws.classify_week(_week(110, 90, 109, 95, 96, 108))["bearish_hit"] is False


def test_the_bullish_setup_and_hit_are_the_mirror():
    week = ws.classify_week(_week(110, 90, 108, 92, 95, 108))
    assert week["bullish_setup"] is True          # Friday's low 92 >= Thursday's 90
    assert week["bullish_hit"] is True            # Monday's high 108 >= Friday's 108


def test_the_tendency_is_measured_against_the_base_rate_not_against_fifty_percent():
    rows = [{"bearish_setup": True, "bearish_hit": True} for _ in range(60)]
    rows += [{"bearish_setup": False, "bearish_hit": True} for _ in range(40)]
    out = ws.tendency(rows, "bearish")
    assert out["hit_rate"] == 1.0
    assert out["base_rate"] == 1.0                # it happens every week regardless
    assert out["edge"] == 0.0
    assert out["beats_base_with_confidence"] is False, "a rule cannot beat a base rate it equals"


def test_a_condition_that_never_occurs_says_so():
    rows = [{"bullish_setup": False, "bullish_hit": True} for _ in range(10)]
    assert ws.tendency(rows, "bullish")["weeks"] == 0


# --- the two bugs the run exposed -------------------------------------------------

def _row(**kw):
    base = {"session": "2026-01-12", "session_start": pd.Timestamp("2026-01-11 21:00", tz="UTC"),
            "bearish_setup": True, "bullish_setup": True,
            "friday_low": 95.0, "friday_high": 105.0,
            "monday_open": 100.0, "monday_low": 94.0, "monday_high": 106.0, "monday_close": 100.0}
    base.update(kw)
    return base


def _hours(sequence, start="2026-01-11 21:00"):
    stamps = pd.date_range(start, periods=len(sequence), freq="1h", tz="UTC")
    return pd.DataFrame({"datetime": stamps, "open": [s[0] for s in sequence],
                         "high": [s[0] for s in sequence], "low": [s[1] for s in sequence],
                         "close": [s[0] for s in sequence], "volume": [1.0] * len(sequence)})


def test_intraday_resolution_uses_the_sessions_own_time_window():
    """Grouping hourly bars by date arithmetic misfiled them and lost most touches."""
    # target (Friday's low 95) is reached in hour 1; the stop (Friday's high 105) never is
    hourly = _hours([(101, 99), (101, 94), (101, 99)])
    out = ws.resolve_with_intraday([_row()], "bearish", "structure", 0.0, {}, hourly)
    assert out["trades"] == 1
    assert out["outcomes"]["target"] == 1
    assert out["setups_without_intraday_history"] == 0


def test_whichever_level_comes_first_wins():
    """The daily bar cannot say; the hourly walk can, and that decided three of four variants."""
    stop_first = _hours([(106, 104), (101, 94)])     # stop touched in hour 0, target in hour 1
    target_first = _hours([(101, 94), (106, 104)])
    assert ws.resolve_with_intraday([_row()], "bearish", "structure", 0.0, {}, stop_first)["outcomes"]["stop"] == 1
    assert ws.resolve_with_intraday([_row()], "bearish", "structure", 0.0, {}, target_first)["outcomes"]["target"] == 1


def test_the_inverse_tests_the_opposite_side_not_an_instant_stop():
    """The bug this pins: the inverse returned exactly -1.0 R on every trade because the touch test
    still followed the setup's direction after the side had been flipped."""
    hourly = _hours([(100.5, 99.5)] * 5)            # price goes nowhere, so nothing is touched
    plain = ws.resolve_with_intraday([_row()], "bearish", "structure", 0.0, {}, hourly)
    inverse = ws.resolve_with_intraday([_row()], "bearish", "structure", 0.0, {}, hourly, inverse=True)
    assert plain["outcomes"]["close"] == 1
    assert inverse["outcomes"]["close"] == 1, "the inverse must not be stopped out instantly"
    assert inverse["expectancy_r"] != pytest.approx(-1.0, abs=0.01)


def test_a_setup_with_no_intraday_history_is_counted_not_silently_dropped():
    hourly = _hours([(101, 99)], start="2020-01-01 00:00")
    out = ws.resolve_with_intraday([_row()], "bearish", "structure", 0.0, {}, hourly)
    assert out.get("trades", 0) == 0
    assert "no setups covered" in out.get("why", "")


def test_the_module_cannot_trade():
    text = open(ws.__file__.replace(".pyc", ".py"), encoding="utf-8").read()
    for forbidden in ("order_send", "OrderSend", "place_order", "auto_execute", "MetaTrader5"):
        assert forbidden not in text
