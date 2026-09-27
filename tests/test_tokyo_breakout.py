"""The 1pm UK trigger and the Tokyo range, checked against hand-built bars.

The owner asked for "UK TIME 1PM". London is BST (UTC+1) for about seven months and GMT (UTC) for the other
five, so 1pm UK is 12:00 UTC in summer and 13:00 UTC in winter. Broker bars arrive stamped UTC. Picking a
single UTC hour would put the entry an hour early for half the year and silently average two different
strategies together - so that conversion is what these tests exist to hold in place.

No market data and no orders: every bar here is constructed so the right answer is known in advance.
"""
import numpy as np
import pandas as pd
import pytest

from src import tokyo_breakout as tb


def _bars(day: str, closes_by_hour: dict, tokyo_high=None, tokyo_low=None):
    """One UTC day of hourly bars. Tokyo hours get a flat range; later hours get the given closes."""
    rows = []
    for hour in range(24):
        close = closes_by_hour.get(hour, 100.0)
        if hour < 8 and tokyo_high is not None:
            high, low = tokyo_high, tokyo_low
        else:
            high, low = close + 0.5, close - 0.5
        rows.append({"datetime": pd.Timestamp(f"{day} {hour:02d}:00:00", tz="UTC"),
                     "open": close, "high": max(high, close), "low": min(low, close),
                     "close": close, "volume": 1.0, "spread": 1.0})
    return pd.DataFrame(rows)


class _Ind:
    """The few attributes tokyo_breakout_orders reads off an Indicators object."""

    def __init__(self, frame):
        self.times = frame["datetime"]
        self.o = frame["open"].to_numpy(dtype=float)
        self.h = frame["high"].to_numpy(dtype=float)
        self.l = frame["low"].to_numpy(dtype=float)
        self.c = frame["close"].to_numpy(dtype=float)


def _spec(window="asia_0_8", rr=2.0, **extra):
    return {"family": "tokyo_breakout",
            "params": {"symbol": "XAUUSD", "timeframe": "1h", "tokyo_window": window, "rr": rr, **extra},
            "exits": {}}


def test_one_pm_uk_is_twelve_utc_in_summer():
    """June: BST. A break at 12:00 UTC is 13:00 London and must be taken."""
    frame = _bars("2026-06-15", {12: 105.0}, tokyo_high=101.0, tokyo_low=99.0)
    side, _stop, _target = tb.tokyo_breakout_orders(_Ind(frame), _spec())
    hours = frame["datetime"].dt.hour.to_numpy()
    assert side[hours == 12][0] == 1, "12:00 UTC in June is 1pm London and should fire"


def test_eleven_utc_in_summer_is_only_noon_london_and_must_not_fire():
    frame = _bars("2026-06-15", {11: 105.0}, tokyo_high=101.0, tokyo_low=99.0)
    side, _s, _t = tb.tokyo_breakout_orders(_Ind(frame), _spec())
    hours = frame["datetime"].dt.hour.to_numpy()
    assert side[hours == 11][0] == 0, "11:00 UTC in June is noon London - too early"


def test_one_pm_uk_is_thirteen_utc_in_winter():
    """January: GMT. The same 1pm is now 13:00 UTC, and 12:00 UTC is only midday."""
    frame = _bars("2026-01-15", {12: 105.0, 13: 105.0}, tokyo_high=101.0, tokyo_low=99.0)
    side, _s, _t = tb.tokyo_breakout_orders(_Ind(frame), _spec())
    hours = frame["datetime"].dt.hour.to_numpy()
    assert side[hours == 12][0] == 0, "12:00 UTC in January is noon London - too early"
    assert side[hours == 13][0] == 1, "13:00 UTC in January is 1pm London and should fire"


def test_a_break_below_the_tokyo_low_goes_short_with_the_break():
    """Continuation means with the break, not against it."""
    frame = _bars("2026-06-15", {12: 95.0}, tokyo_high=101.0, tokyo_low=99.0)
    side, stop, target = tb.tokyo_breakout_orders(_Ind(frame), _spec())
    hours = frame["datetime"].dt.hour.to_numpy()
    i = int(np.flatnonzero(hours == 12)[0])
    assert side[i] == -1
    assert stop[i] == pytest.approx(101.0), "a short stops at the far end of the range, the Tokyo high"
    assert target[i] < 95.0, "a short targets below the entry"


def test_the_stop_is_the_far_end_of_the_range_and_the_target_is_an_R_multiple_of_it():
    frame = _bars("2026-06-15", {12: 105.0}, tokyo_high=101.0, tokyo_low=99.0)
    ind = _Ind(frame)
    side, stop, target = tb.tokyo_breakout_orders(ind, _spec(rr=3.0))
    i = int(np.flatnonzero(frame["datetime"].dt.hour.to_numpy() == 12)[0])
    assert stop[i] == pytest.approx(99.0), "a long stops at the Tokyo low"
    risk = 105.0 - 99.0
    assert target[i] == pytest.approx(105.0 + 3.0 * risk), "target is rr x the range width"


def test_only_the_first_break_of_the_day_is_taken():
    frame = _bars("2026-06-15", {12: 105.0, 13: 106.0, 14: 107.0}, tokyo_high=101.0, tokyo_low=99.0)
    side, _s, _t = tb.tokyo_breakout_orders(_Ind(frame), _spec())
    assert int(np.count_nonzero(side)) == 1, "one trade per day, the first qualifying break"


def test_a_break_inside_the_tokyo_window_itself_is_never_a_signal():
    """The range is not tradable while it is still forming, whatever price does."""
    frame = _bars("2026-06-15", {3: 120.0}, tokyo_high=101.0, tokyo_low=99.0)
    side, _s, _t = tb.tokyo_breakout_orders(_Ind(frame), _spec())
    hours = frame["datetime"].dt.hour.to_numpy()
    assert side[hours == 3][0] == 0


def test_the_two_tokyo_windows_can_give_different_levels():
    """A spike between 06:00 and 08:00 belongs to asia_0_8 but not to tokyo_0_6."""
    frame = _bars("2026-06-15", {7: 110.0, 12: 105.0}, tokyo_high=101.0, tokyo_low=99.0)
    ind = _Ind(frame)
    wide, _s, _t = tb.tokyo_breakout_orders(ind, _spec(window="asia_0_8"))
    narrow, _s2, _t2 = tb.tokyo_breakout_orders(ind, _spec(window="tokyo_0_6"))
    hours = frame["datetime"].dt.hour.to_numpy()
    i = int(np.flatnonzero(hours == 12)[0])
    assert narrow[i] == 1, "105 clears the 0-6 range high of 101"
    assert wide[i] == 0, "the 07:00 spike to 110.5 lifts the 0-8 range above 105, so there is no break"


def test_the_side_filter_works():
    frame = _bars("2026-06-15", {12: 105.0}, tokyo_high=101.0, tokyo_low=99.0)
    ind = _Ind(frame)
    i = int(np.flatnonzero(frame["datetime"].dt.hour.to_numpy() == 12)[0])
    assert tb.tokyo_breakout_orders(ind, _spec(side="long"))[0][i] == 1
    assert tb.tokyo_breakout_orders(ind, _spec(side="short"))[0][i] == 0, "a long break must vanish on a short pass"


def test_the_inverse_control_actually_trades_and_mirrors_the_risk():
    """The regression that mattered: the first version of this control took ZERO trades.

    The stop was read off the range edge AFTER flipping the side, so an inverted long became a short whose
    stop sat at the Tokyo high - a level price had already broken above - and the geometry check rejected
    every one. All six variants reported an inverse of 0.0 % on 0 trades, which is a free pass, not a
    control. The mirrored stop keeps the timing, the count and the risk size and changes only direction.
    """
    frame = _bars("2026-06-15", {12: 105.0}, tokyo_high=101.0, tokyo_low=99.0)
    ind = _Ind(frame)
    i = int(np.flatnonzero(frame["datetime"].dt.hour.to_numpy() == 12)[0])

    plain_side, plain_stop, _t = tb.tokyo_breakout_orders(ind, _spec())
    inv_side, inv_stop, inv_target = tb.tokyo_breakout_orders(ind, _spec(inverse=True))

    assert plain_side[i] == 1 and inv_side[i] == -1, "the control must take the other side"
    assert inv_stop[i] > ind.c[i], "an inverted short must stop ABOVE the entry or it is rejected as unusable"
    base_risk = abs(ind.c[i] - plain_stop[i])
    assert abs(inv_stop[i] - ind.c[i]) == pytest.approx(base_risk), "the control must risk the same distance"
    assert inv_target[i] < ind.c[i], "an inverted short targets below the entry"


def test_the_control_takes_the_same_number_of_trades_as_the_signal():
    """Same bars, same count, opposite direction - anything else is not a like-for-like control."""
    frame = pd.concat([_bars("2026-06-15", {12: 105.0}, 101.0, 99.0),
                       _bars("2026-06-16", {13: 94.0}, 101.0, 99.0),
                       _bars("2026-06-17", {14: 106.0}, 101.0, 99.0)], ignore_index=True)
    ind = _Ind(frame)
    plain = tb.tokyo_breakout_orders(ind, _spec())[0]
    inverse = tb.tokyo_breakout_orders(ind, _spec(inverse=True))[0]
    assert int(np.count_nonzero(plain)) == 3
    assert int(np.count_nonzero(inverse)) == int(np.count_nonzero(plain)), "the control must not lose trades"
    assert list(np.sign(inverse[plain != 0])) == list(-np.sign(plain[plain != 0]))


def test_the_declared_grid_is_the_size_the_module_says_it_is():
    variants = tb.breakout_variants("XAUUSD", "15m")
    assert len(variants) == len(tb.TOKYO_WINDOWS) * len(tb.REWARD_RATIOS) == 6
    assert len({v["variant"] for v in variants}) == 6, "variant names must be unique"
    assert all(v["family"] == "tokyo_breakout" for v in variants)
