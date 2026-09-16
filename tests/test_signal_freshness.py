"""Execution safety item 4: the live signal is predicted on the newest closed bar.

build_features drops the rows whose 3-bar look-ahead target is unknown, so the live prediction used to sit 3 bars
behind the market. The inference path keeps those rows; training still drops them.
"""
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier

from src import signal_engine as engine
from src.features import FUTURE_TARGET_COLUMNS, build_features, build_inference_features
from src.paper_trader import drop_forming_bars
from src.train import FEATURE_COLUMNS

BAR = pd.Timedelta(hours=1)


def _hourly_bars_up_to(now: pd.Timestamp, n: int = 300) -> pd.DataFrame:
    """1h random-walk bars whose last row is the bar still forming at ``now``."""
    rng = np.random.default_rng(3)
    close = 4300 * np.cumprod(1 + rng.normal(0, 0.003, n))
    open_ = np.concatenate([[close[0]], close[:-1]])
    wick = np.abs(rng.normal(0, 2.0, n))
    times = pd.date_range(end=now.floor("h"), periods=n, freq="1h")
    return pd.DataFrame({"datetime": times, "open": open_, "high": np.maximum(open_, close) + wick,
                         "low": np.minimum(open_, close) - wick, "close": close,
                         "volume": rng.integers(1000, 5000, n), "symbol": "XAUUSD"})


def test_live_signal_timestamp_is_within_one_bar_of_now():
    now = pd.Timestamp.now(tz="UTC")
    closed = drop_forming_bars(_hourly_bars_up_to(now), 60, now)
    features = build_features(closed)
    rf = RandomForestClassifier(n_estimators=20, max_depth=4, random_state=0).fit(features[FEATURE_COLUMNS], features["target"])
    result = engine.live_signal(closed, {"rf": rf, "lstm": None, "feature_columns": FEATURE_COLUMNS})
    signal_time = pd.Timestamp(result["signal_time"])
    assert signal_time == pd.Timestamp(closed["datetime"].iloc[-1])
    bar_close = signal_time + BAR
    assert bar_close <= now, "the forming bar must never be the signal bar"
    assert now - bar_close < BAR, f"signal bar closed {now - bar_close} ago, more than one bar"


def test_inference_keeps_the_latest_rows_and_training_still_drops_them():
    now = pd.Timestamp.now(tz="UTC")
    closed = drop_forming_bars(_hourly_bars_up_to(now), 60, now)
    training = build_features(closed)
    inference = build_inference_features(closed)
    assert inference["datetime"].iloc[-1] == closed["datetime"].iloc[-1]
    assert training["datetime"].iloc[-1] == closed["datetime"].iloc[-4], "training rows need a known 3-bar target"
    assert not training.isna().any().any()
    assert not inference[FEATURE_COLUMNS].tail(3).isna().any().any()
    assert inference[list(FUTURE_TARGET_COLUMNS)].tail(3).isna().all().all()


def test_live_payload_uses_closed_bars_and_inference_features():
    import app as app_module
    from pathlib import Path

    source = Path(app_module.__file__).read_text(encoding="utf-8")
    assert "drop_forming_bars(data, 60, signal_now)" in source
    assert "features = build_inference_features(data)" in source
    now = pd.Timestamp("2026-09-15 19:05", tz="UTC")
    assert app_module._signal_bar_age_minutes(pd.Timestamp("2026-09-15 14:00", tz="America/New_York"), now) == 65.0
    assert app_module._signal_bar_time_text(pd.Timestamp("2026-09-15 14:00", tz="America/New_York")) == "2026-09-15 18:00:00"
    assert app_module._signal_bar_age_minutes(None, now) is None
