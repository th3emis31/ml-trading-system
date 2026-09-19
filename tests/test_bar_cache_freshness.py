"""The bar cache must expire when a new bar closes, not once a day.

Found 19 September 2026 while answering "where is all this strategy, all is dead". The cache key was
``{symbol}_{timeframe}_{provider}_{YYYYMMDD}.csv``, so whatever the first caller of a UTC day fetched was
served to every later caller that day. The hourly Strategy Lab runs at :20, so btcusd_15m_app_20260919.csv
was written at 01:20 UTC and every strategy reading bars through load_bars for the next 23 hours saw prices
frozen at 01:20 - while fetch_app_bars, which bypasses this cache, returned bars 36 minutes old.

It was not a cosmetic lag. It stopped the shadow book dead: all 56 tracked strategies had bars ending
BEFORE the instant they started watching from, so not one could ever count a bar or open a trade. After the
fix BTCUSD:1h went from 0 to 50 bars watched on the very next run.
"""
from datetime import datetime, timezone

import pytest

from src import mtf_data


def _at(hour, minute=0, day=19):
    return datetime(2026, 9, day, hour, minute, tzinfo=timezone.utc)


def test_an_hourly_bucket_changes_every_hour():
    assert mtf_data.cache_stamp("1h", _at(1, 20)) == "20260919_0100"
    assert mtf_data.cache_stamp("1h", _at(1, 59)) == "20260919_0100"
    assert mtf_data.cache_stamp("1h", _at(2, 0)) == "20260919_0200"


def test_the_bug_itself_two_calls_seventeen_hours_apart_must_not_share_a_cache():
    """01:20 and 17:35 on the same day: the exact pair that served 17-hour-old bitcoin prices."""
    assert mtf_data.cache_stamp("15m", _at(1, 20)) != mtf_data.cache_stamp("15m", _at(17, 35))
    assert mtf_data.cache_stamp("1h", _at(1, 20)) != mtf_data.cache_stamp("1h", _at(17, 35))
    assert mtf_data.cache_stamp("4h", _at(1, 20)) != mtf_data.cache_stamp("4h", _at(17, 35))


def test_each_timeframe_buckets_to_its_own_bar_interval():
    assert mtf_data.cache_stamp("1m", _at(17, 42)) == "20260919_1742"
    assert mtf_data.cache_stamp("15m", _at(17, 42)) == "20260919_1730"
    assert mtf_data.cache_stamp("1h", _at(17, 42)) == "20260919_1700"
    assert mtf_data.cache_stamp("4h", _at(17, 42)) == "20260919_1600"


def test_a_daily_timeframe_keeps_the_old_daily_shape():
    """1d bars close once a day, so nothing about their caching should change."""
    assert mtf_data.cache_stamp("1d", _at(1, 20)) == "20260919"
    assert mtf_data.cache_stamp("1d", _at(23, 59)) == "20260919"
    assert mtf_data.cache_stamp("1d", _at(0, 1, day=20)) == "20260920"


def test_within_one_bar_the_cache_is_still_reused():
    """The point is to cap staleness at one bar, not to refetch on every call."""
    for tf in ("15m", "1h", "4h"):
        first = mtf_data.cache_stamp(tf, _at(12, 0))
        assert mtf_data.cache_stamp(tf, _at(12, 1)) == first
        assert mtf_data.cache_stamp(tf, _at(12, 2)) == first


def test_every_declared_timeframe_has_a_stamp():
    for tf in mtf_data.TIMEFRAMES:
        assert mtf_data.cache_stamp(tf, _at(9, 7))


def test_the_stamp_never_runs_ahead_of_the_clock():
    """A bucket in the future would cache bars that have not closed yet."""
    for tf in ("1m", "15m", "1h", "4h"):
        now = _at(17, 42)
        stamp = mtf_data.cache_stamp(tf, now)
        bucket = datetime.strptime(stamp, "%Y%m%d_%H%M").replace(tzinfo=timezone.utc)
        assert bucket <= now
        assert (now - bucket).total_seconds() < mtf_data.TIMEFRAMES[tf]["minutes"] * 60
