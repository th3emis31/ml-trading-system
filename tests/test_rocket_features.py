import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd
import pytest

from src import rocket_features as rf
from src.data import generate_synthetic_data
from src.edge_research import build_research_features


@pytest.fixture(scope="module")
def frame():
    raw = generate_synthetic_data("XAUUSD", start_date="2024-01-01", end_date="2024-12-31", n=1500).reset_index(drop=True)
    raw["datetime"] = pd.date_range("2024-01-01", periods=len(raw), freq="4h", tz="UTC")
    return build_research_features(raw, htf_bars=120)


def test_shape_names_and_determinism(frame):
    features, names = rf.rocket_features(frame, n_kernels=20, window=16, fit_rows=500, seed=7)
    assert len(names) == 40 and list(features.columns) == names and len(features) == len(frame)
    again, _ = rf.rocket_features(frame, n_kernels=20, window=16, fit_rows=500, seed=7)
    pd.testing.assert_frame_equal(features, again)
    other, _ = rf.rocket_features(frame, n_kernels=20, window=16, fit_rows=500, seed=8)
    assert not np.allclose(np.nan_to_num(features.to_numpy()), np.nan_to_num(other.to_numpy()))


def test_features_never_use_future_bars(frame):
    cut = 1100
    full, _ = rf.rocket_features(frame, n_kernels=20, window=16, fit_rows=500, seed=3)
    part, _ = rf.rocket_features(frame.iloc[:cut], n_kernels=20, window=16, fit_rows=500, seed=3)
    np.testing.assert_allclose(full.iloc[:cut].to_numpy(), part.to_numpy(), equal_nan=True)


def test_features_are_finite_after_warm_up(frame):
    features, _ = rf.rocket_features(frame, n_kernels=20, window=16, fit_rows=500, seed=1)
    # research warm-up (vol_120) plus the kernel reach and window
    assert np.isfinite(features.iloc[400:].to_numpy()).all()
    ppv = features[[c for c in features.columns if c.endswith("_ppv")]].iloc[400:].to_numpy()
    assert ppv.min() >= 0.0 and ppv.max() <= 1.0


def test_research_engine_adds_rocket_features_on_request():
    from src.edge_research import load_research_frame

    raw = generate_synthetic_data("XAUUSD", start_date="2024-01-01", end_date="2024-12-31", n=1500).reset_index(drop=True)
    raw["datetime"] = pd.date_range("2024-01-01", periods=len(raw), freq="4h", tz="UTC")
    plain_df, plain_names, _, _ = load_research_frame("XAUUSD", "4h", data=raw)
    rocket_df, rocket_names, _, _ = load_research_frame("XAUUSD", "4h", data=raw, rocket=True)
    added = [name for name in rocket_names if name not in plain_names]
    assert len(added) == 2 * rf.DEFAULT_KERNELS and all(name.startswith("rk") for name in added)
    assert rocket_names[:len(plain_names)] == plain_names
    pd.testing.assert_frame_equal(rocket_df[plain_names], plain_df[plain_names])


def test_causal_convolution_matches_a_manual_sum():
    series = np.arange(20, dtype=float)
    weights = np.array([1.0, -2.0, 1.0])
    out = rf._causal_conv(series, weights, dilation=2)
    t = 10
    assert np.isnan(out[3]) and out[t] == pytest.approx(1.0 * series[t] - 2.0 * series[t - 2] + 1.0 * series[t - 4])
