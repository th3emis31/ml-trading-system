"""The benchmark that separates prediction from drift.

A backtest returning +12 % looks like skill until you notice the market rose 80 % over the same window.
The daily learning gate promotes on after-cost return, so without this measure the system cannot tell a
model that predicts from a model that is merely pointing the right way in a trend.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import walkforward_backtest as wb


def _bars(n=600, drift=0.0, seed=7):
    """A synthetic series with a controllable drift, so each test knows the true answer in advance."""
    rng = np.random.default_rng(seed)
    steps = rng.normal(drift, 0.01, n)
    close = 100 * np.exp(np.cumsum(steps))
    return pd.DataFrame({
        "datetime": pd.date_range("2020-01-01", periods=n, freq="4h", tz="UTC"),
        "close": close, "high": close * 1.004, "low": close * 0.996,
        "volatility_5d": np.full(n, 0.01),
    })


def _run(directions, frame, draws=60):
    n = len(frame)
    proba = np.where(directions > 0, 0.8, np.where(directions < 0, 0.2, 0.5)).astype(float)
    trades = wb._simulate_trades(frame, proba, np.ones(n, dtype=int), buy_threshold=0.55,
                                 sell_threshold=0.45, hold_bars=20, cost_pct=0.0001,
                                 directions=directions)
    return wb.random_entry_benchmark(
        frame, proba, np.ones(n, dtype=int), trades, draws=draws, buy_threshold=0.55,
        sell_threshold=0.45, hold_bars=20, cost_pct=0.0001, test_start=frame["datetime"].iloc[0],
        test_end=frame["datetime"].iloc[-1], test_bars=n, first_test_row=0, directions=directions)


def test_a_clairvoyant_entry_beats_chance():
    """A model that enters long only just before real up-moves must clear the bar - otherwise the test
    cannot detect skill and would rubber-stamp anything."""
    frame = _bars(drift=0.0)
    close = frame["close"].to_numpy()
    ahead = np.zeros(len(frame), dtype=int)
    ahead[:-12] = np.where(close[12:] > close[:-12] * 1.01, 1, 0)   # peeks ahead ON PURPOSE
    out = _run(ahead, frame)
    assert out["available"] is True
    assert out["beats_chance"] is True, out
    assert out["p_value"] <= 0.05


def test_a_constant_long_in_a_rising_market_does_not_count_as_skill():
    """THE WHOLE POINT. Always-long in a market that only rises is a large positive return and zero
    prediction; a coin-flip control would call it skill, the permutation control must not."""
    frame = _bars(drift=0.004, seed=11)          # a market that climbs throughout
    always_long = np.ones(len(frame), dtype=int)
    out = _run(always_long, frame)
    assert out["model_total_return_pct"] > 0, "the setup is only meaningful if it made money"
    assert out["beats_chance"] is False, out["verdict"]


def test_the_control_preserves_the_direction_mix_exactly():
    """If a draw could change the long/short balance it would be measuring bias, not timing."""
    frame = _bars()
    rng = np.random.default_rng(3)
    directions = rng.choice([1, -1, 0], size=len(frame), p=[0.3, 0.2, 0.5])
    out = _run(directions, frame, draws=25)
    # The final bar is excluded, because a trade cannot be entered on it - _simulate_trades loops to
    # n-1 for the model too, so the control and the model are offered exactly the same bars.
    assert out["signal_bars_permuted"] == int(np.count_nonzero(directions[:-1]))
    assert out["eligible_bars"] == len(frame) - 1


def test_too_few_signals_is_refused_rather_than_answered():
    frame = _bars(n=100)
    directions = np.zeros(len(frame), dtype=int)
    directions[10] = 1
    out = _run(directions, frame, draws=10)
    assert out["available"] is False and "too few to permute" in out["reason"]


def test_the_benchmark_trains_nothing_and_writes_nothing():
    """It runs against live models; a benchmark that persisted one would be the 24 September accident
    over again, when a diagnostic called train_model() and overwrote the live champions."""
    source = Path(wb.__file__).read_text(encoding="utf-8")
    start = source.index("def random_entry_benchmark")
    body = source[start:source.index("def summarize_trades")]
    for forbidden in ("joblib.dump", "train_model", "to_json", "write_text", ".fit("):
        assert forbidden not in body, forbidden


def test_it_is_off_unless_asked_for():
    """Each draw is a full trade simulation, so it must never slow every ordinary backtest."""
    import inspect
    assert "random_draws: int = 0" in inspect.signature(wb.run_walkforward_backtest).__str__() or \
           inspect.signature(wb.run_walkforward_backtest).parameters["random_draws"].default == 0
