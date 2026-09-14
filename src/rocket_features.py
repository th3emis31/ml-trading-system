"""Random convolutional kernel features (ROCKET-style) for bar sequences, computed causally.

Why this instead of scaling the LSTM up
---------------------------------------
R3 (``src/sequence_research.py``) trained an LSTM/GRU end to end and it overfit: validation
profit factors of 1.6-1.7 collapsed on every holdout, because a recurrent network has far more
parameters than a few thousand bars of labels can pin down. ROCKET (Dempster, Petitjean & Webb,
2020) and MiniRocket keep what a sequence model is for - reading multi-bar shapes - but replace
the trained network with a large set of fixed random convolution kernels. Only the final linear
or tree model is fitted, so it needs much less data, trains in seconds on CPU, and slots into the
existing walk-forward engine as ordinary per-bar features.

Leak-free by construction
-------------------------
* A kernel at bar t reads bars t, t-d, ..., t-(length-1)*d only (causal dilation).
* Each base series is standardised, and each kernel's bias is a quantile of its output, using
  rows before ``fit_rows`` only. ``edge_research`` sets that to 35% of the rows, which is before
  its first walk-forward test block (40% of the rows after warm-up).
* Features are PPV (share of the last ``window`` outputs above the bias) and the scaled maximum of
  those outputs, both over trailing windows.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

KERNEL_LENGTHS = (7, 9, 11)
DEFAULT_KERNELS = 84
DEFAULT_WINDOW = 32
FIT_FRACTION = 0.35


def rocket_base_series(df: pd.DataFrame) -> dict[str, np.ndarray]:
    """Stationary per-bar inputs taken from the research feature frame."""
    atr = df["atr_14"].astype(float).replace(0, np.nan)
    vol = df["vol_120"].astype(float).replace(0, np.nan)
    return {
        "ret_vol": (df["ret_1"].astype(float) / vol).to_numpy(),
        "body": df["body_frac"].astype(float).to_numpy(),
        "range_atr": ((df["high"].astype(float) - df["low"].astype(float)) / atr).to_numpy(),
        "dist_ema_50": df["dist_ema_50"].astype(float).to_numpy(),
        "volume_z": df["volume_z_120"].astype(float).to_numpy(),
    }


def _causal_conv(series: np.ndarray, weights: np.ndarray, dilation: int) -> np.ndarray:
    n, length = len(series), len(weights)
    reach = (length - 1) * dilation
    out = np.full(n, np.nan)
    if n <= reach:
        return out
    total = np.zeros(n - reach)
    for j, weight in enumerate(weights):
        total += weight * series[reach - j * dilation: n - j * dilation]
    out[reach:] = total
    return out


def rocket_features(df: pd.DataFrame, n_kernels: int = DEFAULT_KERNELS, window: int = DEFAULT_WINDOW,
                    fit_rows: Optional[int] = None, seed: int = 42) -> tuple[pd.DataFrame, list[str]]:
    """Per-bar ROCKET features for ``df`` (a ``build_research_features`` frame). Returns (features, names)."""
    n = len(df)
    fit_rows = int(fit_rows if fit_rows is not None else n * FIT_FRACTION)
    fit_rows = max(1, min(fit_rows, n))
    rng = np.random.default_rng(seed)
    base = {}
    for name, values in rocket_base_series(df).items():
        head = values[:fit_rows]
        head = head[np.isfinite(head)]
        mean, std = (float(head.mean()), float(head.std())) if len(head) > 1 else (0.0, 1.0)
        standardised = (values - mean) / (std if std > 0 else 1.0)
        base[name] = np.nan_to_num(np.clip(standardised, -6, 6), nan=0.0)
    series_names = sorted(base)

    columns: dict[str, np.ndarray] = {}
    names: list[str] = []
    for k in range(n_kernels):
        series_name = series_names[int(rng.integers(len(series_names)))]
        length = int(rng.choice(KERNEL_LENGTHS))
        max_exponent = np.log2(max(1.0, (window - 1) / (length - 1)))
        dilation = int(2 ** rng.uniform(0, max_exponent))
        weights = rng.normal(0.0, 1.0, length)
        weights -= weights.mean()
        conv = _causal_conv(base[series_name], weights, dilation)
        fitted = conv[:fit_rows]
        fitted = fitted[np.isfinite(fitted)]
        if len(fitted) < 10:
            bias, scale = 0.0, 1.0
        else:
            bias = float(np.quantile(fitted, rng.uniform(0.25, 0.75)))
            scale = float(fitted.std()) or 1.0
        valid = np.isfinite(conv)
        above = np.where(valid, (conv > bias).astype(float), np.nan)
        ppv = pd.Series(above).rolling(window, min_periods=window).mean().to_numpy()
        peak = pd.Series(conv).rolling(window, min_periods=window).max().to_numpy() / scale
        stem = f"rk{k:03d}_{series_name}_l{length}_d{dilation}"
        columns[f"{stem}_ppv"], columns[f"{stem}_max"] = ppv, peak
        names += [f"{stem}_ppv", f"{stem}_max"]
    return pd.DataFrame(columns, index=df.index), names
