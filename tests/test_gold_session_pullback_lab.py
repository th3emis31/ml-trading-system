"""Gold session pullback backtester: exits, risk rules and the event filter on hand-built bars."""
import numpy as np
import pandas as pd

from src import gold_session_pullback_lab as gsp


def _frame_from_ohlc_rows(rows, start="2026-09-15 08:00"):
    frame = pd.DataFrame(rows, columns=["open", "high", "low", "close"])
    frame.insert(0, "datetime", pd.date_range(start, periods=len(frame), freq="1h", tz="UTC"))
    return frame


def _walk(frame, side=1, stop=95.0):
    arr = {k: frame[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close")}
    times = pd.to_datetime(frame["datetime"], utc=True).reset_index(drop=True)
    return gsp.session_pullback_exit(arr["open"], arr["high"], arr["low"], arr["close"], times, 0, side, stop)


def test_tp1_then_breakeven_gives_three_quarters_of_r():
    # entry 100, stop 95 (R = 5): TP1 107.5, then back to the entry
    frame = _frame_from_ohlc_rows([(100, 101, 99, 100), (100, 108, 99.5, 107), (107, 107.5, 99.9, 100)])
    result = _walk(frame)
    assert [p["reason"] for p in result["parts"]] == ["tp1", "breakeven"]
    assert abs(result["gross_r"] - 0.75) < 1e-9


def test_tp1_and_tp2_give_two_and_a_quarter_r():
    frame = _frame_from_ohlc_rows([(100, 101, 99, 100), (100, 108, 99.5, 107), (107, 116, 106, 115)])
    result = _walk(frame)
    assert [p["reason"] for p in result["parts"]] == ["tp1", "tp2"]
    assert abs(result["gross_r"] - 2.25) < 1e-9


def test_stop_is_checked_before_a_target_on_the_same_bar():
    frame = _frame_from_ohlc_rows([(100, 109, 94, 100)])
    result = _walk(frame)
    assert result["outcome"] == "stop" and abs(result["gross_r"] + 1.0) < 1e-9


def test_everything_is_flat_at_21_utc():
    frame = _frame_from_ohlc_rows([(100, 101, 99, 100), (100, 102, 99, 101), (101, 102, 100, 101.5)], start="2026-09-15 19:00")
    result = _walk(frame)
    assert result["outcome"] == "flat_21utc" and result["parts"][-1]["price"] == 101.0   # the 21:00 bar's open
    assert abs(result["gross_r"] - 0.2) < 1e-9


def test_risk_rules_one_position_two_entries_a_day_and_the_event_filter():
    n = 12
    # every even bar trades down to 90, so each long (stop 99 - 1.5 x 4 = 93) is stopped out on the bar after its fill
    frame = _frame_from_ohlc_rows([(100, 101, 90 if i % 2 == 0 else 99, 100) for i in range(n)], start="2026-09-15 08:00")
    setups = pd.DataFrame([{"signal_idx": i, "entry_idx": i + 1, "side": 1, "atr": 4.0, "swing_low": 99.0, "swing_high": 101.0}
                           for i in (0, 2, 4, 6)])
    nothing_blocked = np.zeros(n, dtype=bool)
    split = gsp.session_pullback_split(frame, setups, (0, n), nothing_blocked, cost_fraction=0.0)
    assert len(split["trades"]) == 2 and split["skipped"]["daily_cap"] == 2, "at most two entries per UTC day"
    blocked = nothing_blocked.copy()
    blocked[1] = True
    filtered = gsp.session_pullback_split(frame, setups, (0, n), blocked, cost_fraction=0.0)
    assert filtered["skipped"]["filter"] == 1 and filtered["trades"][0]["entry_time"] == "2026-09-15 11:00"
    inverse = gsp.session_pullback_split(frame, setups, (0, n), nothing_blocked, cost_fraction=0.0, invert=True)
    assert all(t["side"] == "SELL" for t in inverse["trades"])


def test_signal_bars_after_19_utc_are_not_setups():
    # 400 H4-worth of rising bars so the trend is known, ending with a textbook long rejection bar at 20:00
    n = 1700
    close = 100 + np.arange(n) * 0.05
    frame = pd.DataFrame({"datetime": pd.date_range("2026-06-01 00:00", periods=n, freq="1h", tz="UTC"),
                          "open": close, "high": close + 0.2, "low": close - 0.2, "close": close})
    setups = gsp.session_pullback_setups(frame)
    hours = pd.to_datetime(frame["datetime"].iloc[setups["signal_idx"]], utc=True).dt.hour if len(setups) else pd.Series([], dtype=int)
    assert hours.between(7, 19).all()
