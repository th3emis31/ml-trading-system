import numpy as np
import pandas as pd

from src import strategy_lab as lab
from src import crt_lab


def _flat_trade(n=10):
    times = pd.date_range("2026-09-08 08:00", periods=n, freq="15min", tz="UTC")
    o = np.full(n, 100.0)
    h = np.full(n, 100.2)
    l = np.full(n, 99.8)
    c = np.full(n, 100.0)
    side = np.zeros(n, dtype=int)
    side[0] = 1
    stop = np.full(n, np.nan)
    stop[0] = 98.0                      # risk 2.0 from a 100 entry
    target = np.full(n, np.nan)
    target[0] = 104.0
    return times, o, h, l, c, side, stop, target


def test_managed_exits_absent_keys_change_nothing():
    times, o, h, l, c, side, stop, target = _flat_trade()
    h[4] = 104.5
    atr = np.full(len(o), 1.0)
    trades = lab.simulate_orders(o, h, l, c, atr, times, side, stop, target, np.arange(len(o)),
                                 {"trail_atr": 0.0, "max_bars": 100}, 0.0)
    assert trades[0]["outcome"] == "TARGET" and trades[0]["exit_price"] == 104.0


def test_price_trail_moves_stop_from_next_bar_and_exits_as_trail():
    times, o, h, l, c, side, stop, target = _flat_trade()
    h[2] = 101.0                        # best price 101 (1.0 in profit) on bar 2
    o[3], h[3] = 100.7, 100.8           # bar 3 opens above the trailed stop ...
    l[3] = 100.4                        # ... and dips to 100.4: below a 0.5 trail (100.5), above the 98 stop
    exits = {"trail_atr": 0.0, "max_bars": 100, "trail_start_price": 0.8, "trail_dist_price": 0.5}
    trades = lab.simulate_orders(o, h, l, c, np.full(len(o), 1.0), times, side, stop, target, np.arange(len(o)), exits, 0.0)
    assert trades[0]["outcome"] == "TRAIL" and trades[0]["exit_price"] == 100.5 and trades[0]["exit_time"] == lab._iso(times[3])


def test_r_trail_and_breakeven_use_initial_risk():
    times, o, h, l, c, side, stop, target = _flat_trade()
    h[2] = 102.1                        # 1.05R with risk 2.0
    o[3] = 100.3                        # opens above the break-even stop (100.1), then trades through it
    l[3] = 99.9
    be = lab.simulate_orders(o, h, l, c, np.full(len(o), 1.0), times, side, stop, target, np.arange(len(o)),
                             {"trail_atr": 0.0, "max_bars": 100, "be_trigger_r": 1.0, "be_lock_price": 0.1}, 0.0)
    assert be[0]["outcome"] == "TRAIL" and be[0]["exit_price"] == 100.1
    h[6] = 104.5                        # later the 104 target trades, so the untrailed trade closes there
    no_trigger = lab.simulate_orders(o, h, l, c, np.full(len(o), 1.0), times, side, stop, target, np.arange(len(o)),
                                     {"trail_atr": 0.0, "max_bars": 100, "trail_start_r": 1.5, "trail_dist_r": 1.0}, 0.0)
    assert no_trigger[0]["outcome"] != "TRAIL"   # 1.05R never reached the 1.5R trail start


def test_crt_signal_detects_bullish_setup_with_sweep_and_h4_bias():
    n = 400
    times = pd.date_range("2026-09-07 00:00", periods=n, freq="15min", tz="UTC")
    rng = np.random.default_rng(1)
    base = 4300 + np.cumsum(rng.normal(0, 0.3, n))
    o = base.copy()
    c = base + rng.normal(0, 0.2, n)
    h = np.maximum(o, c) + 0.4
    l = np.minimum(o, c) - 0.4
    i = 300                               # breakout bar: 2026-09-10 03:00 UTC closes 03:15 = 06:15 server; use a later bar
    i = int(np.searchsorted(times, pd.Timestamp("2026-09-10 09:00", tz="UTC")))
    a = i - 2                             # anchor, one middle bar
    o[a], c[a], h[a], l[a] = 4310.0, 4300.0, 4310.5, 4299.5          # bearish anchor, body 10 (>= 1.80)
    o[a + 1], c[a + 1], h[a + 1], l[a + 1] = 4301.0, 4302.0, 4303.0, 4298.5  # inside close, sweep 1.0 below the low
    o[i], c[i], h[i], l[i] = 4302.0, 4312.0, 4312.5, 4301.5          # bullish close above the anchor high
    bars = pd.DataFrame({"datetime": times, "open": o, "high": h, "low": l, "close": c})
    ind = lab.Indicators(bars)
    h4_times = pd.date_range("2026-06-01 01:00", "2026-09-10 05:00", freq="4h", tz="UTC")
    h4_close = np.linspace(4000, 4320, len(h4_times))                 # rising: close above EMA50
    ind.htf_bars = pd.DataFrame({"datetime": h4_times, "open": h4_close, "high": h4_close + 5, "low": h4_close - 5, "close": h4_close})
    sig = crt_lab.crt_signals(ind, "XAUUSD")
    assert sig["direction"][i] == 1
    side, stop, target = crt_lab.crt_orders(ind, {"params": {"symbol": "XAUUSD"}, "exits": {"rr": 2.0}})
    buffer = max(1.20, round(sig["atr"][i] * 0.8 / 0.01) * 0.01)
    assert side[i] == 1 and abs(stop[i] - (4299.5 - buffer)) < 1e-6
    assert abs(target[i] - (c[i] + 2.0 * (c[i] - stop[i]))) < 1e-6


def test_filter_mask_keeps_everything_without_filters_and_applies_score_and_session():
    n = 4
    times = pd.date_range("2026-09-08 08:00", periods=n, freq="15min", tz="UTC")
    bars = pd.DataFrame({"datetime": times, "open": [1.0] * n, "high": [1.2] * n, "low": [0.8] * n, "close": [1.1] * n})
    ind = lab.Indicators(bars)
    side = np.array([1, -1, 1, 0])
    sig = {"hours": np.array([9, 11, 12, 12]), "score": np.array([72.0, 65.0, 80.0, np.nan]), "ema50": np.full(n, 1.0),
           "h4_close": np.full(n, 1.0), "h4_ema": np.full(n, 1.0), "h4_ema200": np.full(n, 1.0), "mid": np.array([1, 2, 5, 0]),
           "atr": np.full(n, 0.1), "mode": np.array([1, 2, 1, 0])}
    assert list(crt_lab.filter_mask(ind, sig, side, {})) == [True, True, True, False]
    assert list(crt_lab.filter_mask(ind, sig, side, {"min_score": 70.0})) == [True, False, True, False]
    assert list(crt_lab.filter_mask(ind, sig, side, {"session": (10, 19)})) == [False, True, True, False]
    assert list(crt_lab.filter_mask(ind, sig, side, {"max_mid": 3})) == [True, True, False, False]


def test_round3_is_five_declared_exits_with_v1_as_the_reference():
    """Round 3 tests the exits shipped in strategies/mt4/CRT_Dashboard_EA_v2.mq4. It must stay a small
    DECLARED set: the gold M15 holdout is spent, so a sweep over TrailStartR/TrailDistanceR here would
    be fitting rather than measuring."""
    v = crt_lab.round3_variants("XAUUSD")
    assert len(v) == crt_lab.ROUND3_TRIALS == 5
    names = [n for n, _, _ in v]
    assert names[0] == "V1_live_exits_reference", "the reference must be in the table, not remembered"
    assert set(names) == {"V1_live_exits_reference", "V2_default", "V2_break_even_only",
                          "V2_r_trail_only", "V2_default_3R"}
    exits = {n: e for n, _, e in v}
    # v2's shipped default: break-even at 1R locking 0.10R, trail off, 2R target
    assert exits["V2_break_even_only"]["be_trigger_r"] == 1.0
    assert exits["V2_break_even_only"]["be_lock_r"] == 0.10
    assert "trail_start_r" not in exits["V2_break_even_only"]
    # the two halves of the change are separable, so credit lands on the right one
    assert "be_trigger_r" not in exits["V2_r_trail_only"]
    assert exits["V2_r_trail_only"]["trail_start_r"] == 1.5
    assert exits["V2_r_trail_only"]["trail_dist_r"] == 1.0


def test_round3_charges_every_crt_trial_ever_made_against_this_holdout():
    """Rounds 1 and 2 already read this holdout, so their trials must still count."""
    import inspect
    src = inspect.getsource(crt_lab.run)
    assert "ROUND1_TRIALS + len(ROUND2_FILTERS) * 2 + len(variants)" in src


def test_the_unseen_window_ends_before_the_app_history_begins():
    """The point of that window is that no CRT choice was ever made with it. If it overlapped the
    app's bars, every conclusion drawn from it would be contaminated by rounds 1-3."""
    from pathlib import Path

    import pandas as pd

    path = Path(crt_lab.UNSEEN_CACHE.format(tf="15m"))
    if not path.exists():
        pytest.skip("the unseen window exists only as a cached export")
    frame = pd.read_csv(path, usecols=["datetime"])
    end = pd.to_datetime(frame["datetime"], utc=True).max()
    # the app's own XAUUSD 15m history starts 2024-08-07; this must finish before it
    assert end < pd.Timestamp("2024-08-07", tz="UTC"), f"unseen window ends {end}, which overlaps the app's bars"
