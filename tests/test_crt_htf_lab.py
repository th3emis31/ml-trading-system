import numpy as np
import pandas as pd

from src import strategy_lab as lab
from src import crt_htf_lab as htf


def _ind(o, h, l, c):
    times = pd.date_range("2026-01-01", periods=len(o), freq="4h", tz="UTC")
    return lab.Indicators(pd.DataFrame({"datetime": times, "open": o, "high": h, "low": l, "close": c}))


def _flat(n=40):
    o = np.full(n, 100.0)
    c = np.full(n, 100.0)
    return o, c + 1.0, c - 1.0, c


def test_classic_crt_long_after_low_sweep_and_range_target_needs_1r():
    o, h, l, c = _flat()
    i = 30
    h[i - 1], l[i - 1] = 104.0, 99.0      # bar A range 99-104
    l[i], c[i], h[i] = 98.0, 99.8, 100.2  # bar B sweeps 99, closes back inside
    side, stop, target = htf.classic_crt_orders(_ind(o, h, l, c), "range", "none")
    atr = _ind(o, h, l, c).atr(14)[i]
    assert side[i] == 1 and abs(stop[i] - (98.0 - 0.1 * atr)) < 1e-9 and target[i] == 104.0
    side2, _, target2 = htf.classic_crt_orders(_ind(o, h, l, c), "rr2", "none")
    assert side2[i] == 1 and abs(target2[i] - (99.8 + 2 * (99.8 - stop[i]))) < 1e-9


def test_classic_crt_skips_outside_bar_and_trend_filter_blocks_counter_trend():
    o, h, l, c = _flat()
    i = 30
    h[i - 1], l[i - 1] = 101.0, 99.0
    h[i], l[i], c[i] = 102.0, 98.0, 100.0   # sweeps both sides
    side, _, _ = htf.classic_crt_orders(_ind(o, h, l, c), "rr2", "none")
    assert side[i] == 0
    o, h, l, c = _flat(260)
    c[:] = np.linspace(200, 100, 260)       # falling market: price below EMA200
    o[:] = c
    h, l = c + 1.0, c - 1.0
    i = 250
    h[i - 1], l[i - 1] = c[i] + 3.0, c[i] - 0.5
    l[i] = c[i] - 2.0                       # low sweep, close back inside -> long signal
    no_filter, _, _ = htf.classic_crt_orders(_ind(o, h, l, c), "rr2", "none")
    with_filter, _, _ = htf.classic_crt_orders(_ind(o, h, l, c), "rr2", "ema200")
    assert no_filter[i] == 1 and with_filter[i] == 0


def test_scaled_ea_crt_bullish_breakout_after_sweep():
    n = 60
    o, h, l, c = _flat(n)
    i = 50
    a = i - 2
    o[a], c[a], h[a], l[a] = 106.0, 100.0, 106.5, 99.5    # bearish anchor, body 6 >= 1 ATR
    o[a + 1], c[a + 1], h[a + 1], l[a + 1] = 100.5, 101.0, 101.5, 99.0   # inside close, sweeps the low by 0.5
    o[i], c[i], h[i], l[i] = 101.0, 107.0, 107.5, 100.8   # closes above the anchor high
    ind = _ind(o, h, l, c)
    side, stop, target = htf.scaled_crt_orders(ind, "rr2", "none")
    atr = ind.atr(14)[i]
    assert side[i] == 1 and abs(stop[i] - (99.5 - 0.8 * atr)) < 1e-9     # EA reference: min(anchor low, breakout low)
    assert abs(target[i] - (107.0 + 2 * (107.0 - stop[i]))) < 1e-9
