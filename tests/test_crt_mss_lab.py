import numpy as np
import pandas as pd

from src import crt_mss_lab as mss
from src.mtf_data import mt5_server_to_utc


def test_mt5_server_time_converts_to_utc_in_summer_and_winter():
    summer = pd.Timestamp("2026-09-15 17:00").value // 10 ** 9     # server clock UTC+3
    winter = pd.Timestamp("2026-01-15 17:00").value // 10 ** 9     # server clock UTC+2
    out = mt5_server_to_utc([summer, winter])
    assert str(out[0]) == "2026-09-15 14:00:00+00:00" and str(out[1]) == "2026-01-15 15:00:00+00:00"


def _bullish_bars(n=30, j=15):
    o = np.full(n, 102.0)
    h = np.full(n, 103.5)
    l = np.full(n, 100.5)
    c = np.full(n, 102.0)
    h[j - 2] = 104.0                                                   # swing high before the sweep
    o[j], h[j], l[j], c[j] = 101.0, 100.0 + 0.0, 98.5, 99.5            # sweep below the range low 100
    h[j] = 100.0
    o[j + 1], h[j + 1], l[j + 1], c[j + 1] = 99.6, 101.0, 99.6, 100.8
    o[j + 2], h[j + 2], l[j + 2], c[j + 2] = 102.1, 103.0, 102.0, 102.8
    o[j + 3], h[j + 3], l[j + 3], c[j + 3] = 102.8, 105.0, 102.5, 104.5  # closes above the 104 swing: MSS
    o[j + 4], h[j + 4], l[j + 4], c[j + 4] = 103.0, 103.2, 101.5, 102.0  # retests the latest FVG's 50 %
    return o, h, l, c


def test_sweep_mss_latest_fvg_and_50_percent_fill():
    o, h, l, c = _bullish_bars()
    window = {"H": 110.0, "L": 100.0, "start": 12, "end": 22, "fill_end": 25}
    setup = mss.mss_scan(window, 1, h, l, c)
    # latest gap: bar 18 low 102.5 above bar 16 high 101.0 -> 50 % = 101.75
    assert setup["dir"] == 1 and setup["sweep"] == 98.5 and setup["mss_bar"] == 18 and setup["ce"] == 101.75
    fill = mss.mss_fill(setup, o, h, l, c, np.full(len(o), 1.0), "opposite")
    j, entry, stop, target = fill
    assert j == 19 and entry == 101.75 and abs(stop - 98.4) < 1e-9 and target == 110.0
    j2, _, stop2, target2 = mss.mss_fill(setup, o, h, l, c, np.full(len(o), 1.0), "rr2")
    assert abs(target2 - (101.75 + 2 * (101.75 - 98.4))) < 1e-9


def test_no_fvg_means_no_trade_and_asia_confluence_blocks_unconfirmed_sweep():
    o, h, l, c = _bullish_bars()
    h[16], h[17] = 103.0, 103.0                                        # fill every gap after the sweep
    l[17], l[18] = 99.9, 102.0
    window = {"H": 110.0, "L": 100.0, "start": 12, "end": 22, "fill_end": 25}
    assert mss.mss_scan(window, 1, h, l, c) is None
    o, h, l, c = _bullish_bars()
    asia_hi, asia_lo = np.full(len(o), np.nan), np.full(len(o), 97.0)   # Asian low 97 not swept by 98.5
    assert mss.mss_scan(window, 1, h, l, c, asia_hi, asia_lo) is None
