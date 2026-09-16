"""Signal parity: the live wrapper and the backtest wrapper must give identical signals on the same bars.

Both go through src/signal_engine.py. Bars are generated in memory (no network) and the RF model is fitted in the
test (nothing is written to models/). The LSTM is a deterministic stub so the 50/50 blend and all three bands
(BUY / SELL / HOLD) are exercised.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from sklearn.ensemble import RandomForestClassifier

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_mtf_data import _bars as mtf_bars   # the shared synthetic-bar helper (1h random walk)

from src import signal_engine as engine
from src import walkforward_backtest as wf
from src.features import build_features
from src.train import FEATURE_COLUMNS, ensemble_predict

N_BARS, START = 500, 250
VOL = {"XAUUSD": (2000.0, 0.004), "BTCUSD": (60000.0, 0.009)}


def _symbol_bars(symbol: str) -> pd.DataFrame:
    """mtf_bars scaled to the symbol's price, with a per-symbol random walk and real candle ranges."""
    base, vol = VOL[symbol]
    frame = mtf_bars("1h", N_BARS, start="2026-01-05")
    rng = np.random.default_rng(7 if symbol == "XAUUSD" else 11)
    close = frame["close"].to_numpy() / 100.0 * base * np.cumprod(1 + rng.normal(0, vol, N_BARS))
    open_ = np.concatenate([[close[0]], close[:-1]])
    wick = np.abs(rng.normal(0, vol * 0.6, N_BARS)) * close
    frame = frame.assign(open=open_, high=np.maximum(open_, close) + wick, low=np.minimum(open_, close) - wick,
                         close=close, volume=rng.integers(1000, 5000, N_BARS), symbol=symbol)
    return frame


class StubLSTM:
    """Deterministic stand-in for LSTMTrader.predict: 20-bar momentum mapped into 0.3..0.7."""

    def predict(self, bars: pd.DataFrame):
        close = bars["close"].to_numpy(dtype=float)
        if len(close) < 21:
            return None
        move = close[-1] / close[-21] - 1.0
        return float(0.5 + max(-0.2, min(0.2, move * 25.0)))


def _models(bars: pd.DataFrame) -> dict:
    features = build_features(bars)
    train = features.iloc[:200]
    rf = RandomForestClassifier(n_estimators=40, max_depth=5, random_state=0)
    rf.fit(train[FEATURE_COLUMNS], train["target"])
    return {"rf": rf, "lstm": StubLSTM(), "feature_columns": FEATURE_COLUMNS}


@pytest.mark.parametrize("symbol", ["XAUUSD", "BTCUSD"])
def test_live_and_backtest_wrappers_give_identical_signals_on_500_bars(symbol):
    bars = _symbol_bars(symbol)
    models = _models(bars)
    live = [engine.live_signal(bars.iloc[: i + 1], models) for i in range(START, N_BARS)]
    backtest = wf.backtest_signal_series(bars, models, start=START)
    assert len(backtest) == len(live) == N_BARS - START
    assert [r["signal"] for r in live] == backtest["signal"].tolist()
    assert [r["probability"] for r in live] == backtest["probability"].tolist()
    assert len(set(backtest["signal"])) >= 2, "the fixture should exercise more than one band"


def test_walkforward_simulation_rule_matches_the_live_rule_at_and_around_the_thresholds():
    grid = [0.0, 0.44, 0.4499, 0.45, 0.45004, 0.45006, 0.5, 0.54994, 0.54996, 0.55, 0.5501, 0.9, float("nan"), None]
    for p in grid:
        live_side = engine.classify_probability(p, "live")
        expected = 1 if live_side == engine.BUY else (-1 if live_side == engine.SELL else 0)
        if p is None or (isinstance(p, float) and np.isnan(p)):
            continue   # _simulate_trades skips NaN probabilities before classifying
        assert wf._direction_for_probability(p, 0.55, 0.45) == expected, p


def test_one_config_drives_every_wrapper():
    live = engine.get_config("live")
    legacy = engine.get_config("legacy_ensemble")
    assert (live["buy_threshold"], live["sell_threshold"], live["weights"]) == (0.55, 0.45, {"rf": 0.5, "lstm": 0.5})
    result = ensemble_predict(lstm_prob=0.6, rf_prob=0.5)
    assert result["lstm_weight"] == pytest.approx(legacy["weights"]["lstm"])
    assert result["signal"] == engine.classify_probability(result["probability"], legacy)
    app_source = Path(__file__).resolve().parents[1].joinpath("app.py").read_text(encoding="utf-8")
    assert 'ENGINE_SIGNAL_CONFIGS["live"]["buy_threshold"]' in app_source
    assert "engine_live_signal(data" in app_source and 'engine_classify_probability(probability, "live")' in app_source
