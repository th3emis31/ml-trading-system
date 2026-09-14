import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd
import pytest

from src import mtf_data as mtf


def _bars(freq, n, start="2025-01-06"):
    stamps = pd.date_range(start, periods=n, freq=freq, tz="UTC")
    rng = np.random.default_rng(1)
    price = 100 * np.cumprod(1 + rng.normal(0, 0.001, n))
    return pd.DataFrame({"datetime": stamps, "open": price, "high": price * 1.001, "low": price * 0.999,
                         "close": price, "volume": 1.0})


def test_resample_bars_builds_correct_ohlc():
    base = _bars("15min", 8)
    hourly = mtf.resample_bars(base, "1h")
    assert len(hourly) == 2
    assert hourly["open"].iloc[0] == base["open"].iloc[0]
    assert hourly["close"].iloc[0] == base["close"].iloc[3]
    assert hourly["high"].iloc[0] == base["high"].iloc[:4].max()
    assert hourly["low"].iloc[1] == base["low"].iloc[4:].min()
    assert hourly["volume"].iloc[0] == pytest.approx(4.0)


def test_base_bar_sees_only_closed_higher_timeframe_bars():
    base = _bars("15min", 4000)
    hourly = mtf.resample_bars(base, "1h")
    merged, columns = mtf.add_htf_features(base, 15, {"h1": (hourly, 60)})
    assert columns and all(c.startswith("h1_") for c in columns)
    # The 10:15 bar closes at 10:30; the latest completed hourly bar then is 09:00-10:00.
    row = merged[merged["datetime"] == pd.Timestamp("2025-01-06 10:15", tz="UTC")].iloc[0]
    assert row["h1_bar_time"] == pd.Timestamp("2025-01-06 09:00", tz="UTC")
    # The 10:45 bar closes at 11:00, exactly when the 10:00-11:00 bar closes, so it may see it.
    row = merged[merged["datetime"] == pd.Timestamp("2025-01-06 10:45", tz="UTC")].iloc[0]
    assert row["h1_bar_time"] == pd.Timestamp("2025-01-06 10:00", tz="UTC")


def test_later_higher_timeframe_bars_never_change_earlier_rows():
    base = _bars("15min", 4000)
    hourly = mtf.resample_bars(base, "1h")
    before, columns = mtf.add_htf_features(base, 15, {"h1": (hourly, 60)})
    changed = hourly.copy()
    cutoff = pd.Timestamp("2025-01-20", tz="UTC")
    changed.loc[changed["datetime"] >= cutoff, ["open", "high", "low", "close"]] *= 2.0
    after, _ = mtf.add_htf_features(base, 15, {"h1": (changed, 60)})
    # Hourly bars from the cutoff become visible at cutoff + 1h, when a base bar closes at that time.
    visible_before = (before["datetime"] + pd.Timedelta(minutes=15)) < cutoff + pd.Timedelta(minutes=60)
    pd.testing.assert_frame_equal(before.loc[visible_before, columns], after.loc[visible_before, columns])
    assert not before.loc[~visible_before, columns].equals(after.loc[~visible_before, columns])
