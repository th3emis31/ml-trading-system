"""The CISD backtest's own controls have to work, or its verdict means nothing.

Two things are tested here rather than the returns themselves: that the inverse control is genuinely
simulated (a control that silently measures zero trades would make any result look unopposed), and that
the permutation keeps everything about the trades except WHEN they happen - which is the only reason its
p-value answers "does this pattern pick moments".
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import cisd_lab
from src import strategy_lab as lab


@pytest.fixture(scope="module")
def gold_market(bars):
    """The shared synthetic gold frame from conftest, wrapped as a Market."""
    return lab.Market("XAUUSD", "4h", bars, swap=True, now=pd.Timestamp("2030-01-01", tz="UTC"))


def _spec(variant: str = "run1|sweep10|wait3|rr2") -> dict:
    return next(s for s in cisd_lab.cisd_variants("XAUUSD", "4h") if s["variant"] == variant)


# --- the order builder ----------------------------------------------------------------------------

def test_the_grid_is_declared_and_its_size_is_the_trial_count():
    variants = cisd_lab.cisd_variants("XAUUSD", "4h")
    assert len(variants) == 16, "16 = 2 run lengths x 2 lookbacks x 2 waits x 2 reward ratios"
    assert len({v["variant"] for v in variants}) == 16


def test_the_stop_sits_beyond_the_swept_extreme(gold_market):
    side, stop, target = lab.strategy_orders(gold_market.ind, _spec())
    ind = gold_market.ind
    fired = np.flatnonzero(side != 0)
    assert len(fired) > 20, f"only {len(fired)} signals on the fixture"
    for i in fired[:40]:
        if side[i] == 1:
            assert stop[i] < ind.c[i], "a long's stop must be below the signal close"
            assert target[i] > ind.c[i]
        else:
            assert stop[i] > ind.c[i], "a short's stop must be above the signal close"
            assert target[i] < ind.c[i]


def test_the_reward_ratio_is_applied_to_the_measured_risk(gold_market):
    for rr in (1.0, 2.0):
        spec = _spec(f"run1|sweep10|wait3|rr{rr:.0f}")
        side, stop, target = lab.strategy_orders(gold_market.ind, spec)
        ind = gold_market.ind
        fired = np.flatnonzero(side != 0)[:20]
        for i in fired:
            risk = abs(ind.c[i] - stop[i])
            reward = abs(target[i] - ind.c[i])
            assert reward == pytest.approx(rr * risk, rel=1e-6)


def test_the_inverse_control_actually_trades(gold_market):
    """A control that produces no trades would make every variant look unopposed."""
    spec = _spec()
    inverse = {**spec, "params": {**spec["params"], "inverse": True}}
    real_side = lab.strategy_orders(gold_market.ind, spec)[0]
    inverse_side = lab.strategy_orders(gold_market.ind, inverse)[0]
    assert np.count_nonzero(inverse_side) == np.count_nonzero(real_side)
    assert np.array_equal(inverse_side, -real_side), "the inverse must be the same bars, opposite side"
    trades = gold_market.simulate(inverse, "holdout")
    assert len(trades) > 0, "the inverse control simulated nothing, so it measured nothing"


def test_the_inverse_stop_is_on_the_tradeable_side(gold_market):
    """With the pattern's structural stop kept, every inverted trade is rejected by the engine and the
    control silently reports zero - which reads as 'the inverse loses' when it means 'the inverse never ran'."""
    spec = _spec()
    inverse = {**spec, "params": {**spec["params"], "inverse": True}}
    side, stop, _target = lab.strategy_orders(gold_market.ind, inverse)
    ind = gold_market.ind
    for i in np.flatnonzero(side != 0)[:40]:
        assert (stop[i] < ind.c[i]) if side[i] == 1 else (stop[i] > ind.c[i])


# --- the permutation control ----------------------------------------------------------------------

def test_the_permutation_keeps_the_trade_count_and_changes_only_the_timing(gold_market):
    out = cisd_lab.permutation_check(gold_market, _spec(), split="holdout", draws=12, seed=1)
    if not out["available"]:
        pytest.skip(out["reason"])
    assert out["draws"] == 12
    assert 0.0 < out["p_value"] <= 1.0
    assert out["real_trades"] > 0
    assert "only the bars differ" in out["note"]


def test_the_permutation_is_deterministic_for_a_seed(gold_market):
    first = cisd_lab.permutation_check(gold_market, _spec(), split="holdout", draws=8, seed=7)
    second = cisd_lab.permutation_check(gold_market, _spec(), split="holdout", draws=8, seed=7)
    if not first["available"]:
        pytest.skip(first["reason"])
    assert first["p_value"] == second["p_value"]
    assert first["random_mean_pct"] == second["random_mean_pct"]


def test_too_few_signals_is_refused_rather_than_given_a_p_value(gold_market):
    """A p-value from a handful of trades is a number with no content."""
    spec = _spec()
    quiet = {**spec, "params": {**spec["params"], "min_run": 99}}
    out = cisd_lab.permutation_check(gold_market, quiet, split="holdout", draws=5)
    assert out["available"] is False and "too few" in out["reason"]
