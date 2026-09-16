import numpy as np
import pandas as pd

from src import strategy_lab as lab
from src import crt_fvg_lab as fvg


def test_limit_entry_fills_at_limit_and_no_target_credit_on_fill_bar():
    n = 8
    times = pd.date_range("2026-09-08 08:00", periods=n, freq="15min", tz="UTC")
    o = np.full(n, 101.0)
    h = np.full(n, 101.2)
    l = np.full(n, 100.8)
    c = np.full(n, 101.0)
    h[1], l[1] = 104.5, 99.9          # fill bar trades the 100 limit and the 104 target: target not credited here
    h[4] = 104.2                      # target reached later
    side = np.zeros(n, dtype=int)
    side[0] = 1
    stop, target, entry = np.full(n, np.nan), np.full(n, np.nan), np.full(n, np.nan)
    stop[0], target[0], entry[0] = 98.0, 104.0, 100.0
    trades = lab.simulate_orders(o, h, l, c, np.full(n, 1.0), times, side, stop, target, np.arange(n),
                                 {"trail_atr": 0.0, "max_bars": 100}, 0.0, None, entry)
    assert trades[0]["entry_price"] == 100.0 and trades[0]["outcome"] == "TARGET"
    assert trades[0]["exit_time"] == lab._iso(times[4]) and trades[0]["r_multiple"] == 2.0


def test_fvg_order_long_retest_proximal_and_ce_with_structure_stop():
    o = np.array([100.0, 100.0, 101.0, 103.0, 103.5, 102.5, 103.0])
    h = np.array([100.5, 100.8, 103.0, 104.0, 104.0, 103.0, 104.0])
    l = np.array([99.0, 99.5, 100.4, 102.0, 102.8, 101.2, 102.5])   # bar 2 low 100.4 <= bar 0 high: no earlier gap
    c = np.array([100.0, 100.6, 102.9, 103.8, 103.2, 102.6, 103.8])
    atr = np.full(len(o), 1.0)
    # bars 1,2,3: low[3] 102.0 > high[1] 100.8 -> bullish FVG zone [100.8, 102.0]
    setup = {"dir": 1, "invalid": 99.0, "target": 106.0, "mid_from": 1, "k_to": 3, "created": 1, "fill_to": None}
    j, entry, stop, target = fvg.fvg_order(setup, o, h, l, c, atr, "proximal", "structure", "rr2")
    assert j == 5 and entry == 102.0 and abs(stop - 98.9) < 1e-9 and abs(target - (102.0 + 2 * 3.1)) < 1e-9
    j, entry, stop, target = fvg.fvg_order(setup, o, h, l, c, atr, "ce", "fvg", "range")
    assert j == 5 and entry == 101.4 and abs(stop - 100.7) < 1e-9 and target == 106.0


def test_classic_crt_htf_setup_long_after_low_sweep_close_inside():
    buckets = pd.to_datetime(["2026-09-08 10:00", "2026-09-08 11:00"])
    candles = pd.DataFrame({"bucket": buckets, "start": [0, 4], "end": [3, 7], "n": [4, 4],
                            "o": [101.0, 100.5], "h": [102.0, 101.0], "l": [100.0, 99.4], "c": [100.4, 100.8]})
    setups = fvg.crt_htf_setups(candles, 1)
    assert len(setups) == 1 and setups[0]["dir"] == 1 and setups[0]["invalid"] == 99.4 and setups[0]["target"] == 102.0
    assert setups[0]["mid_from"] == 8 and setups[0]["created"] == 7


def test_amd_asian_range_sweep_high_then_close_inside_gives_short():
    times = pd.date_range("2026-09-08 00:00", periods=64, freq="15min", tz="UTC")   # 00:00 .. 15:45
    n = len(times)
    o = np.full(n, 100.0)
    c = np.full(n, 100.0)
    h = np.full(n, 100.5)
    l = np.full(n, 99.5)
    i6 = 24                                        # 06:00 bar sweeps the Asian high 100.5
    h[i6], c[i6] = 101.2, 100.9
    c[i6 + 1] = 100.2                              # 06:15 closes back inside
    setups = fvg.amd_setups(times, o, h, l, c)
    assert len(setups) == 1
    s = setups[0]
    assert s["dir"] == -1 and s["invalid"] == 101.2 and s["target"] == 99.5 and s["created"] == i6 + 1
    assert s["k_to"] == 51 and s["fill_to"] == 63
