import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd
import pytest

from src import edge_research as er
from src.data import generate_synthetic_data


def _hourly(n=2600, seed=7):
    frame = generate_synthetic_data("XAUUSD", start_date="2024-01-01", end_date="2024-12-31", n=n)
    frame["datetime"] = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    return frame


def test_features_never_use_future_bars():
    frame = _hourly()
    base = er.build_research_features(frame, htf_bars=480)
    columns = ["open", "high", "low", "close", "volume"]
    changed = frame.copy()
    changed[columns] = changed[columns].astype(float)  # volume is int; scaling it must not change dtype
    changed.loc[changed.index[-100:], columns] *= 1.5
    after = er.build_research_features(changed, htf_bars=480)
    cutoff = len(frame) - 100
    left = base[er.RESEARCH_FEATURES].iloc[:cutoff]
    right = after[er.RESEARCH_FEATURES].iloc[:cutoff]
    pd.testing.assert_frame_equal(left, right)


def test_labels_only_depend_on_the_horizon_ahead():
    df = er.build_research_features(_hourly(), htf_bars=480)
    horizon = 24
    base = er.triple_barrier_outcomes(df, 1.0, 2.0, horizon)
    t = 1200
    changed = df.copy()
    changed.loc[changed.index[t + horizon + 1:], ["open", "high", "low", "close"]] *= 2.0
    after = er.triple_barrier_outcomes(changed, 1.0, 2.0, horizon)
    for side in (1, -1):
        assert base[side]["label"][t] == after[side]["label"][t]
        assert base[side]["gross"][t] == after[side]["gross"][t]
    # Rows without a full horizon ahead stay undefined instead of being guessed.
    assert np.isnan(base[1]["label"][len(df) - horizon])


def test_mtf_run_adds_higher_timeframe_and_alignment_features():
    from src.mtf_data import resample_bars

    hourly = _hourly()
    four_hour = resample_bars(hourly, "4h")
    result = er.run_edge_research("XAUUSD", "1h", n_folds=5, holdout_folds=2, models=("logit",),
                                  configs=[{"name": "balanced", "sl_atr": 1.0, "tp_atr": 2.0, "horizon": 24}],
                                  data=hourly, mtf=True, htf_data={"4h": four_hour})
    assert result["available"] is True
    assert result["mtf"] is True
    assert "h_4h_trend_up" in result["features"]
    assert "align_h_4h" in result["features"]
    assert [item["timeframe"] for item in result["htf_used"]] == ["4h"]


def test_fold_net_returns_compound_per_fold():
    fold_of_row = np.array([1, 1, 2, 2, 3])
    trades = [{"entry_idx": 0, "net_pct": 10.0}, {"entry_idx": 1, "net_pct": -5.0}, {"entry_idx": 2, "net_pct": 2.0}]
    returns = er.fold_net_returns(trades, fold_of_row, [1, 2, 3])
    assert returns[1] == pytest.approx(4.5)  # 1.10 x 0.95 - 1
    assert returns[2] == pytest.approx(2.0)
    assert returns[3] == 0.0


def test_trailing_quantile_uses_only_past_values():
    values = np.arange(200, dtype=float)
    base = er.trailing_quantile(values, 40, 0.9)
    changed = values.copy()
    changed[120:] = 1e9  # a huge change from bar 120 onward
    after = er.trailing_quantile(changed, 40, 0.9)
    # The threshold for bar t uses bars before t only, so bars 0..120 are unaffected.
    np.testing.assert_array_equal(base[:121], after[:121])
    assert after[121] >= base[121]


@pytest.fixture(scope="module")
def report():
    return er.run_edge_research("XAUUSD", "1h", n_folds=5, holdout_folds=2, models=("logit",),
                                configs=[{"name": "balanced", "sl_atr": 1.0, "tp_atr": 2.0, "horizon": 24}],
                                data=_hourly())


def test_folds_purge_the_label_horizon(report):
    assert report["available"] is True
    for fold in report["folds"]:
        assert fold["train_end_idx"] < fold["test_start_idx"] - 24 + 1


def test_holdout_is_never_used_for_selection(report):
    used = set(report["selection"]["folds_used"])
    assert used.isdisjoint(report["holdout_folds"])
    assert used == set(report["validation_folds"])


def test_costs_are_charged_on_holdout_trades(report):
    cost = report["cost_round_trip_pct"] * 100
    for trade in report["holdout"]["recent_trades"]:
        assert trade["net_pct"] == pytest.approx(trade["gross_pct"] - cost, abs=1e-3)


def test_verdict_reports_every_criterion(report):
    verdict = report["holdout"]["verdict"]
    assert set(verdict["checks"]) == {"profit_factor", "trades", "max_drawdown", "beats_buy_and_hold"}
    assert verdict["passed"] == all(verdict["checks"].values())
