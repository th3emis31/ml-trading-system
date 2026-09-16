import numpy as np
import pandas as pd

from src import strategy_lab as lab


def test_rollover_counts_weekdays_and_wednesday_triple():
    # September: New York is UTC-4, so the 17:00 rollover is 21:00 UTC.
    times = pd.to_datetime([
        "2026-09-07 20:00", "2026-09-08 01:00",   # Monday before and after Monday's rollover
        "2026-09-09 20:00", "2026-09-10 01:00",   # Wednesday before and after Wednesday's rollover
        "2026-09-11 20:00", "2026-09-14 01:00",   # Friday before, Monday after the weekend
    ], utc=True)
    roll = lab.rollover_counts(times)
    assert roll[1] - roll[0] == 1
    assert roll[3] - roll[2] == 3
    assert roll[5] - roll[4] == 1          # Friday's rollover only; weekend nights were charged on Wednesday
    assert roll[5] - roll[0] == 1 + 1 + 3 + 1 + 1


def test_crypto_calendar_charges_every_night_and_btc_uses_it():
    times = pd.to_datetime(["2026-09-11 20:00", "2026-09-14 01:00"], utc=True)  # Friday before, Monday after the weekend
    crypto = lab.rollover_counts(times, lab.ROLLOVER_CALENDARS["crypto"])
    forex = lab.rollover_counts(times, lab.ROLLOVER_CALENDARS["forex"])
    assert crypto[1] - crypto[0] == 3 and forex[1] - forex[0] == 1   # Friday, Saturday and Sunday nights vs Friday only
    assert lab.HOLDING_COSTS["BTCUSD"]["calendar"] == "crypto" and lab.HOLDING_COSTS["XAUUSD"]["calendar"] == "forex"
    btc_times = pd.date_range("2026-01-01", periods=1200, freq="4h", tz="UTC")
    price = 90000 + np.cumsum(np.random.default_rng(2).normal(0, 300, len(btc_times)))
    bars = pd.DataFrame({"datetime": btc_times, "open": price, "high": price + 500, "low": price - 500, "close": price})
    market = lab.Market("BTCUSD", "4h", bars, now=btc_times[-1] + pd.Timedelta(hours=8))
    assert market.holding["long"] == 0.0560 and market.info["cost_model"].endswith("percent")
    assert market.holding["roll"][-1] - market.holding["roll"][0] >= 199   # 200 days of 4h bars: a night every day


def test_simulate_orders_charges_swap_per_night():
    times = pd.date_range("2026-09-07 00:00", periods=12, freq="12h", tz="UTC")   # Monday 00:00 .. Friday 12:00
    n = len(times)
    o = np.full(n, 100.0)
    h = np.full(n, 100.5)
    l = np.full(n, 99.5)
    c = np.full(n, 100.0)
    h[7] = 103.0                                   # target hit on bar 7 (Thursday 12:00 UTC)
    atr = np.full(n, 1.0)
    side = np.zeros(n, dtype=int)
    side[0] = 1
    stop = np.full(n, np.nan)
    stop[0] = 95.0
    target = np.full(n, np.nan)
    target[0] = 102.0
    exits = {"trail_atr": 0.0, "max_bars": 100}
    base = lab.simulate_orders(o, h, l, c, atr, times, side, stop, target, np.arange(n), exits, 0.0)
    roll = lab.rollover_counts(times)
    price_mode = {"roll": roll, "mode": "price", "long": 0.5, "short": 0.5}
    with_swap = lab.simulate_orders(o, h, l, c, atr, times, side, stop, target, np.arange(n), exits, 0.0, price_mode)
    assert len(base) == len(with_swap) == 1
    trade = with_swap[0]
    # entry Monday 12:00 UTC, exit bar Thursday 12:00 UTC: Monday, Tuesday and Wednesday (x3) rollovers = 5 nights
    assert trade["nights"] == 5
    assert trade["swap_pct"] == round(5 * 0.5 / 100.0 * 100, 4)        # 0.5 price units on a 100 entry = 0.5% a night
    assert abs(base[0]["net_pct"] - trade["net_pct"] - trade["swap_pct"]) < 1e-9
    assert base[0]["nights"] == 0 and base[0]["swap_pct"] == 0
    percent_mode = {"roll": roll, "mode": "percent", "long": 0.02, "short": 0.02}
    pct_trade = lab.simulate_orders(o, h, l, c, atr, times, side, stop, target, np.arange(n), exits, 0.0, percent_mode)[0]
    assert pct_trade["swap_pct"] == round(5 * 0.02, 4)                  # 0.02% of price a night, 5 nights


def test_market_swap_modes(monkeypatch):
    times = pd.date_range("2020-01-01", periods=1200, freq="4h", tz="UTC")
    price = 100 + np.cumsum(np.random.default_rng(1).normal(0, 0.5, len(times)))
    bars = pd.DataFrame({"datetime": times, "open": price, "high": price + 1, "low": price - 1, "close": price})
    now = times[-1] + pd.Timedelta(hours=8)
    percent = lab.Market("XAUUSD", "4h", bars, now=now)
    price_market = lab.Market("XAUUSD", "4h", bars, now=now, swap_mode="price")
    none = lab.Market("XAUUSD", "4h", bars, now=now, swap=False)
    assert percent.info["cost_model"].endswith("percent") and percent.holding["long"] == 0.0190
    assert price_market.holding["long"] == 0.8276 and none.holding is None and none.info["cost_model"] == "spread-only"


def test_fast_deflated_sharpe_matches_the_scipy_formula():
    import math
    from scipy.stats import kurtosis, norm, skew

    def reference(returns, n_trials, sr_variance):
        values = np.asarray(returns, dtype=float)
        sr = values.mean() / values.std(ddof=1)
        trials = max(int(n_trials), 2)
        euler = 0.5772156649
        sr0 = math.sqrt(max(sr_variance, 1e-6)) * ((1 - euler) * norm.ppf(1 - 1 / trials) + euler * norm.ppf(1 - 1 / (trials * math.e)))
        denominator = 1 - skew(values) * sr + (kurtosis(values, fisher=False) - 1) / 4 * sr ** 2
        return round(float(norm.cdf((sr - sr0) * math.sqrt(len(values) - 1) / math.sqrt(denominator))), 4)

    rng = np.random.default_rng(11)
    for trials, variance in ((1, 0.01), (50, 0.02), (2500, 0.005)):
        returns = rng.standard_t(4, size=80) * 0.01 + 0.002
        assert lab.deflated_sharpe(returns, trials, variance) == reference(returns, trials, variance)


def test_tradingview_spec_uses_its_atr_length():
    spec = lab.EA_SPECS["tradingview"]
    assert spec["params"]["atr_len"] == 12 and spec["exits"]["trail_atr"] == 4.5
    assert lab.spec_id(lab.SWING_TREND_PULLBACK_SPEC) != lab.spec_id(spec)


def test_exit_before_triple_swap_closes_before_wednesday_rollover():
    # 4h bars from Tuesday 21:00 UTC (= New York 17:00, the rollover) to Friday
    times = pd.date_range("2026-09-08 21:00", periods=18, freq="4h", tz="UTC")
    n = len(times)
    o = np.full(n, 100.0)
    h = np.full(n, 100.5)
    l = np.full(n, 99.5)
    c = np.full(n, 100.2)
    atr = np.full(n, 1.0)
    side = np.zeros(n, dtype=int)
    side[0] = 1
    stop, target = np.full(n, np.nan), np.full(n, np.nan)
    stop[0] = 90.0
    roll = lab.rollover_counts(times)
    holding = {"roll": roll, "mode": "price", "long": 0.5, "short": 0.5}
    base = {"trail_atr": 0.0, "max_bars": 100}
    plain = lab.simulate_orders(o, h, l, c, atr, times, side, stop, target, np.arange(n), dict(base, max_bars=12), 0.0, holding)
    triple = lab.simulate_orders(o, h, l, c, atr, times, side, stop, target, np.arange(n),
                                 dict(base, max_bars=12, exit_before_triple_swap=True), 0.0, holding)
    wed_rollover = int(np.argmax(np.diff(roll) >= 3)) + 1        # first bar at or after Wednesday's 17:00 New York
    assert triple[0]["outcome"] == "SWAP_EXIT" and triple[0]["exit_time"] == lab._iso(times[wed_rollover - 1])
    assert triple[0]["exit_price"] == 100.2 and triple[0]["nights"] < plain[0]["nights"]
    any_night = lab.simulate_orders(o, h, l, c, atr, times, side, stop, target, np.arange(n),
                                    dict(base, exit_before_rollover=True), 0.0, holding)
    assert any_night[0]["outcome"] == "SWAP_EXIT" and any_night[0]["nights"] == 0
