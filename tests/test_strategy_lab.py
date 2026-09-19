import json
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd
import pytest

from src import strategy_lab as lab
from src.data import generate_synthetic_data

TIMES = pd.date_range("2024-01-01", periods=10, freq="h", tz="UTC")
NO_TRAIL = {"trail_atr": 0.0, "max_bars": 50}


def _arrays(**overrides):
    base = {"o": [100.0] * 10, "h": [101.0] * 10, "l": [99.0] * 10, "c": [100.0] * 10, "atr": [2.0] * 10}
    for key, changes in overrides.items():
        for index, value in changes.items():
            base[key][index] = value
    return {k: np.array(v, dtype=float) for k, v in base.items()}


def _run(arrays, side_at=1, side=1, stop=95.0, target=110.0, exits=NO_TRAIL, cost=0.0):
    sides = np.zeros(10, dtype=int)
    stops = np.full(10, np.nan)
    targets = np.full(10, np.nan)
    sides[side_at], stops[side_at], targets[side_at] = side, stop, target
    return lab.simulate_orders(arrays["o"], arrays["h"], arrays["l"], arrays["c"], arrays["atr"], TIMES, sides, stops,
                               targets, np.arange(10), exits, cost)


def test_target_hit_after_next_open_entry_with_costs():
    trades = _run(_arrays(h={3: 111.0}), cost=0.001)
    assert len(trades) == 1
    trade = trades[0]
    assert trade["outcome"] == "TARGET" and trade["entry_price"] == 100.0 and trade["exit_price"] == 110.0
    assert trade["gross_pct"] == 10.0 and trade["net_pct"] == 9.9 and trade["bars_held"] == 1


def test_stop_is_checked_before_target_and_gaps_fill_at_the_open():
    both = _run(_arrays(h={3: 111.0}, l={3: 94.0}))[0]
    assert both["outcome"] == "STOP" and both["exit_price"] == 95.0
    gap = _run(_arrays(o={3: 93.0}, l={3: 92.0}))[0]
    assert gap["outcome"] == "STOP" and gap["exit_price"] == 93.0


def test_time_exit_and_trailing_stop_and_short_side():
    timed = _run(_arrays(), target=np.nan, exits={"trail_atr": 0.0, "max_bars": 2})[0]
    assert timed["outcome"] == "TIME" and timed["bars_held"] == 2
    # Bar 2 makes a 106 high; with a 1 ATR (2.0) trail the stop rises to 104. Bar 3 opens above it, then trades through it.
    trailed = _run(_arrays(o={3: 105.0}, h={2: 106.0, 3: 106.0}, l={3: 103.0}), target=np.nan,
                   exits={"trail_atr": 1.0, "max_bars": 50})[0]
    assert trailed["outcome"] == "TRAIL" and trailed["exit_price"] == 104.0
    short = _run(_arrays(l={4: 89.0}), side=-1, stop=105.0, target=90.0)[0]
    assert short["side"] == "SELL" and short["outcome"] == "TARGET" and short["gross_pct"] == 10.0


def test_one_position_at_a_time():
    arrays = _arrays(h={6: 111.0})
    sides = np.zeros(10, dtype=int)
    sides[[1, 3]] = 1
    stops = np.where(sides == 1, 95.0, np.nan)
    targets = np.where(sides == 1, 110.0, np.nan)
    trades = lab.simulate_orders(arrays["o"], arrays["h"], arrays["l"], arrays["c"], arrays["atr"], TIMES, sides, stops,
                                 targets, np.arange(10), NO_TRAIL, 0.0)
    assert len(trades) == 1 and trades[0]["exit_time"] == lab._iso(TIMES[6])


# The ``bars`` fixture (synthetic 4h gold bars) lives in tests/conftest.py, shared with test_strategy_book.py.
def test_signals_never_use_future_bars(bars):
    rng = random.Random(7)
    full = lab.Indicators(bars)
    cut = 2000
    part = lab.Indicators(bars.iloc[:cut])
    for family in lab.FAMILIES:
        for _ in range(3):
            spec = lab.random_spec(rng)
            while spec["family"] != family:
                spec = lab.random_spec(rng)
            # Builders return (side, stop, target) or, when the entry is a resting order,
            # (side, stop, target, entry_price). trendline_break is the second kind, and the entry
            # prices must be free of look-ahead exactly like the rest.
            orders_full = lab.strategy_orders(full, spec)
            orders_part = lab.strategy_orders(part, spec)
            assert len(orders_full) == len(orders_part) and len(orders_full) in (3, 4)
            np.testing.assert_array_equal(orders_full[0][:cut], orders_part[0],
                                          err_msg=f"{family}: side depends on future bars")
            for index, name in ((1, "stop"), (2, "target")):
                np.testing.assert_allclose(orders_full[index][:cut], orders_part[index], equal_nan=True,
                                           err_msg=f"{family}: {name} depends on future bars")
            if len(orders_full) == 4:
                np.testing.assert_allclose(orders_full[3][:cut], orders_part[3], equal_nan=True,
                                           err_msg=f"{family}: entry price depends on future bars")


def test_holdout_is_only_simulated_for_validated_candidates(bars):
    market = lab.Market("XAUUSD", "4h", bars, now=bars["datetime"].iloc[-1] + pd.Timedelta(days=1))
    assert market.rows["search"][-1] < market.rows["validation"][0] < market.rows["holdout"][0]
    rng = random.Random(3)
    for _ in range(12):
        record = lab.evaluate_candidate(market, lab.random_spec(rng))
        assert record["validated"] == (not record["gate_reasons"])
        assert ("holdout" in record) == record["validated"]
    ea = lab.evaluate_candidate(market, lab.SWING_TREND_PULLBACK_SPEC, with_holdout=True)
    assert "holdout" in ea and ea["spec"]["params"]["side"] == "long"
    json.dumps(lab.holdout_verdict(ea, 1, 0.01))  # must be plain JSON: numpy bools once broke this


def test_family_filter_and_trend_filter_option(bars):
    rng = random.Random(11)
    specs = [lab.random_spec(rng, ["ema_pullback"]) for _ in range(20)]
    assert {spec["family"] for spec in specs} == {"ema_pullback"}
    assert all("trend_ema" in spec["params"] for spec in specs)
    ind = lab.Indicators(bars)
    base = {"family": "ema_pullback", "params": {**lab.SWING_TREND_PULLBACK_SPEC["params"], "side": "both", "trend_ema": 0},
            "exits": lab.SWING_TREND_PULLBACK_SPEC["exits"]}
    filtered = {**base, "params": {**base["params"], "trend_ema": 200}}
    side_all, _, _ = lab.strategy_orders(ind, base)
    side_filtered, _, _ = lab.strategy_orders(ind, filtered)
    trend = ind.ema(200)
    assert np.all(side_filtered[side_filtered == 1] == side_all[side_filtered == 1])
    assert not np.any((side_filtered == 1) & (ind.c <= trend))
    assert not np.any((side_filtered == -1) & (ind.c >= trend))


def test_trial_variance_removes_sampling_noise():
    rng = np.random.default_rng(5)
    # 500 trials with the same true Sharpe (0.05): all of their spread is 60-trade sampling noise.
    pairs = [[float(rng.normal(0.05, math.sqrt(1 / 60))), 60] for _ in range(500)]
    # The noise is removed, but the result never drops below a quarter of it (about 0.0042 here).
    assert 0.0035 < lab._variance(pairs) < 0.006
    assert lab._variance([0.1, 0.3, -0.2]) > 0.01  # older registries stored bare values


def test_deflated_sharpe_gets_stricter_with_more_trials():
    returns = np.random.default_rng(1).normal(0.002, 0.01, 200)
    assert lab.deflated_sharpe(returns, 1, 0.01) > lab.deflated_sharpe(returns, 5000, 0.01)


def test_run_search_is_resumable_and_respects_an_active_run(tmp_path, bars):
    registry_path, status_path = tmp_path / "registry.json", tmp_path / "status.json"
    now = bars["datetime"].iloc[-1] + pd.Timedelta(days=1)
    loader = lambda symbol, timeframe: bars
    first = lab.run_search(["XAUUSD:4h"], max_candidates=10, seed=1, loader=loader, registry_path=registry_path,
                           status_path=status_path, now=now)
    registry = lab.load_registry(registry_path)
    assert first["evaluated_last_run"] == 10 and registry["markets"]["XAUUSD:4h"]["candidates_tried"] == 10
    assert any(rec.get("tag") == "ea_baseline" for rec in registry["candidates"].values())
    boundaries = registry["markets"]["XAUUSD:4h"]["boundaries"]
    lab.run_search(["XAUUSD:4h"], max_candidates=10, seed=1, loader=loader, registry_path=registry_path,
                   status_path=status_path, now=now)
    registry = lab.load_registry(registry_path)
    assert registry["markets"]["XAUUSD:4h"]["candidates_tried"] == 20
    seen = registry["markets"]["XAUUSD:4h"]["seen"]
    assert len(seen) == 20 and len(set(seen)) == 20
    stored = [rec for rec in registry["candidates"].values() if not rec.get("tag")]
    assert all(rec["validated"] for rec in stored)  # rejected ones are kept only as ids and a short recent list
    families = registry["markets"]["XAUUSD:4h"]["families"]
    assert sum(f["evaluated"] for f in families.values()) == 20
    assert registry["markets"]["XAUUSD:4h"]["boundaries"] == boundaries
    assert lab.read_status(status_path)["state"] == "idle"
    summary = lab.lab_summary(registry_path, status_path)
    assert summary["markets"]["XAUUSD:4h"]["counts"]["evaluated"] == 20

    lab.write_status({"state": "running", "heartbeat": lab._iso(pd.Timestamp.now(tz="UTC"))}, status_path)
    assert lab.run_search(["XAUUSD:4h"], max_candidates=5, loader=loader, registry_path=registry_path,
                          status_path=status_path, now=now)["skipped"] is True


def test_deflation_toll_shows_what_the_bar_rejected():
    """"0 passed the holdout" reads as a broken lab unless the step before it is visible: candidates that cleared
    every money test on unseen bars and were refused only because the trials on that market were counted."""
    from src.strategy_lab import deflation_toll

    def candidate(cid, sharpe, deflated, checks, validated=True, tag=None):
        return {"id": cid, "market": "XAUUSD:4h", "family": "donchian_breakout", "validated": validated, "tag": tag,
                "holdout": {"trades": 69, "profit_factor": 3.185, "total_return_pct": 49.78,
                            "max_drawdown_pct": 2.616, "sharpe": sharpe, "years": 2.04},
                "holdout_verdict": {"passed": False, "checks": checks, "deflated_sharpe": deflated, "n_trials": 6382}}

    money_ok = {"profit_factor": True, "trades": True, "max_drawdown": True, "positive_return": True,
                "deflated_sharpe": False}
    registry = {"candidates": {
        "a": candidate("a", 2.362, 0.5606, money_ok),
        "b": candidate("b", 1.100, 0.4000, money_ok),
        "c": candidate("c", 3.000, 0.3000, {**money_ok, "profit_factor": False}),   # failed a money test too
        "d": candidate("d", 4.000, 0.2000, money_ok, validated=False),               # never validated
        "e": candidate("e", 5.000, 0.1000, money_ok, tag="baseline"),                # a reference, not a candidate
    }}
    toll = deflation_toll(registry)
    assert toll["rejected_only_by_deflation"] == 2, "only the ones the deflation bar alone stopped"
    best = toll["best_rejected"]
    assert best["sharpe"] == 2.362 and best["deflated_sharpe"] == 0.5606 and best["n_trials"] == 6382
    assert "chance" in toll["note"], "the card has to say why, not only how many"
    assert deflation_toll({"candidates": {}})["best_rejected"] is None


def test_the_verdict_names_the_per_trade_sharpe_target(bars):
    """'failed the deflated Sharpe' is useless on its own; the verdict must say what was needed."""
    market = lab.Market("XAUUSD", "4h", bars, now=bars["datetime"].iloc[-1] + pd.Timedelta(days=1))
    record = lab.evaluate_candidate(market, lab.SWING_TREND_PULLBACK_SPEC, with_holdout=True)
    verdict = lab.holdout_verdict(record, 18230, 0.010637)
    assert verdict["per_trade_sharpe_needed"] == pytest.approx(0.4131, abs=1e-3)
    assert "per-trade Sharpe" in verdict["deflation_note"]


def test_the_sharpe_target_rises_with_the_trial_count_but_stays_reachable():
    """Measured 19 Sep 2026: the bar needs about 0.41 per-trade Sharpe, which real strategies reach."""
    var = 0.010637
    assert lab.sharpe_target(100, var) < lab.sharpe_target(1000, var) < lab.sharpe_target(18230, var)
    assert lab.sharpe_target(18230, var) < 0.5, "the requirement must stay inside what a strategy can do"


def test_per_trade_sharpe_needs_at_least_two_trades():
    assert lab.per_trade_sharpe([]) is None
    assert lab.per_trade_sharpe([1.0]) is None
    assert lab.per_trade_sharpe([1.0, 1.0]) is None          # no spread, so no ratio
    assert lab.per_trade_sharpe([2.0, -1.0, 1.0]) is not None


def test_near_misses_lists_only_candidates_failing_exactly_one_check():
    registry = {"candidates": {
        "a": {"id": "a", "market": "XAUUSD:1d", "description": "one short",
              "holdout": {"trades": 19, "profit_factor": 7.5, "total_return_pct": 107.0, "max_drawdown_pct": 8.4},
              "holdout_verdict": {"passed": False, "deflated_sharpe": 0.963,
                                  "checks": {"trades": False, "profit_factor": True, "max_drawdown": True,
                                             "positive_return": True, "deflated_sharpe": True}}},
        "b": {"id": "b", "market": "XAUUSD:4h", "description": "two short",
              "holdout": {"trades": 10, "profit_factor": 0.9, "total_return_pct": -2.0, "max_drawdown_pct": 5.0},
              "holdout_verdict": {"passed": False, "deflated_sharpe": 0.1,
                                  "checks": {"trades": False, "profit_factor": False, "max_drawdown": True,
                                             "positive_return": True, "deflated_sharpe": True}}},
        "c": {"id": "c", "market": "XAUUSD:4h", "description": "passed",
              "holdout": {"trades": 40, "profit_factor": 2.0, "total_return_pct": 20.0, "max_drawdown_pct": 5.0},
              "holdout_verdict": {"passed": True, "checks": {"trades": True, "profit_factor": True,
                                                             "max_drawdown": True, "positive_return": True,
                                                             "deflated_sharpe": True}}}}}
    rows = lab.near_misses(registry)
    assert [r["id"] for r in rows] == ["a"], "only the candidate short by one thing"
    assert rows[0]["only_missing"] == "trades"
    assert "11 more holdout trades" in rows[0]["needs"]


def test_near_misses_puts_the_closest_first():
    """A candidate needing more trades is nearer than one needing a better Sharpe: trades arrive on their own."""
    registry = {"candidates": {
        "sharpe": {"id": "sharpe", "market": "m", "description": "d", "holdout": {"trades": 50},
                   "holdout_verdict": {"passed": False, "deflated_sharpe": 0.9,
                                       "checks": {"trades": True, "deflated_sharpe": False}}},
        "trades": {"id": "trades", "market": "m", "description": "d", "holdout": {"trades": 25},
                   "holdout_verdict": {"passed": False, "deflated_sharpe": 0.96,
                                       "checks": {"trades": False, "deflated_sharpe": True}}}}}
    assert [r["id"] for r in lab.near_misses(registry)] == ["trades", "sharpe"]
