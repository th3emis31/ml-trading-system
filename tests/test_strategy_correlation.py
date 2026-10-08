"""A book of near-identical rules is one edge counted many times, and nothing measured that.

WHY THIS EXISTS
---------------
The strategy book holds 1,972 entries, 84 % of them `ema_pullback`, and nothing had ever asked whether
any two of them are the same trade. The answer changes what the lab should be doing:

    18 variants of ONE rule        mean correlation 0.331   effective independent  2.72 of 18
                                   equal-weight portfolio +5.69 % against the best single's +26.37 %
                                   -> combining variants of one rule DILUTES

    6 best-of-DIFFERENT-families   mean correlation 0.016   effective independent  5.55 of 6
                                   portfolio Sharpe 1.538 against the best single's 1.302
                                   -> combining different rules IMPROVES

That is the difference between a book and a list, and it is invisible without this measurement.

DRAWDOWN CORRELATION IS NOT DECORATION
--------------------------------------
Two profitable strategies over a long backtest both drift up, so the correlation of their returns can
read low while they still lose money in the same weeks. Correlating their RUNNING DRAWDOWNS asks the
question a person holding both actually cares about. On the six-family set the two disagree sharply -
0.016 on returns against 0.214 on drawdowns - and the drawdown number is the honest one.

No bars are loaded and no backtest runs here: every test builds its own return streams.
"""
import numpy as np
import pytest

from src import strategy_correlation as sc


# ------------------------------------------------------------------ drawdown
def test_drawdown_is_zero_at_a_new_high_and_negative_below_it():
    dd = sc.running_drawdown([0.1, 0.1, -0.3, 0.05])
    assert dd[0] == 0.0 and dd[1] == 0.0
    assert dd[2] == pytest.approx(-0.3)
    assert dd[3] == pytest.approx(-0.25)


def test_a_strategy_that_only_wins_never_draws_down():
    assert np.allclose(sc.running_drawdown([0.01] * 20), 0.0)


# ------------------------------------------------------------------ the effective count
def test_identical_strategies_are_worth_one():
    """The headline number for the book: 1,972 entries that all trade alike are worth one."""
    assert sc.effective_independent_count(1.0, 1972) == pytest.approx(1.0)


def test_uncorrelated_strategies_are_worth_all_of_themselves():
    assert sc.effective_independent_count(0.0, 6) == pytest.approx(6.0)


def test_the_measured_within_family_case():
    """18 reclaim variants at mean correlation 0.331 came out at 2.72."""
    assert sc.effective_independent_count(0.3305, 18) == pytest.approx(2.72, abs=0.01)


def test_the_measured_across_family_case():
    """6 different families at 0.016 came out at 5.55."""
    assert sc.effective_independent_count(0.0163, 6) == pytest.approx(5.55, abs=0.01)


def test_more_correlated_is_never_worth_more():
    counts = [sc.effective_independent_count(r, 10) for r in (0.0, 0.2, 0.5, 0.9, 1.0)]
    assert counts == sorted(counts, reverse=True)


def test_a_missing_correlation_is_none_not_a_guess():
    assert sc.effective_independent_count(None, 10) is None


# ------------------------------------------------------------------ the correlation matrix
def _spec(label):
    return {"family": "x", "params": {}, "exits": {}, "variant": label}


def _report(monkeypatch, streams, benchmark=None):
    order = list(streams)
    monkeypatch.setattr(sc, "strategy_bar_returns",
                        lambda market, spec, split="holdout": np.asarray(streams[spec["variant"]], float))
    monkeypatch.setattr(sc, "benchmark_bar_returns",
                        lambda market, split="holdout": np.asarray(
                            benchmark if benchmark is not None else np.zeros(len(streams[order[0]])), float))
    return sc.correlation_report(market=None, specs=[_spec(k) for k in order])


def test_two_identical_streams_report_correlation_one(monkeypatch):
    rng = np.random.default_rng(1)
    s = rng.normal(0, 0.01, 400)
    report = _report(monkeypatch, {"a": s, "b": s.copy()})
    assert report["mean_return_correlation"] == pytest.approx(1.0, abs=1e-6)
    assert report["effective_independent"] == pytest.approx(1.0, abs=0.01)


def test_independent_streams_report_near_zero(monkeypatch):
    rng = np.random.default_rng(2)
    report = _report(monkeypatch, {f"s{i}": rng.normal(0, 0.01, 2000) for i in range(6)})
    assert abs(report["mean_return_correlation"]) < 0.08
    assert report["effective_independent"] > 4.5


def test_opposite_streams_are_reported_as_negative(monkeypatch):
    rng = np.random.default_rng(3)
    s = rng.normal(0, 0.01, 400)
    report = _report(monkeypatch, {"a": s, "b": -s})
    assert report["mean_return_correlation"] == pytest.approx(-1.0, abs=1e-6)


def test_a_candidate_that_never_traded_is_excluded_not_counted_as_independent(monkeypatch):
    """An all-zero stream has no variance. Counting it as uncorrelated would inflate the independent
    count with strategies that did nothing at all."""
    rng = np.random.default_rng(4)
    report = _report(monkeypatch, {"a": rng.normal(0, 0.01, 300),
                                   "b": rng.normal(0, 0.01, 300),
                                   "flat": np.zeros(300)})
    assert report["candidates"] == 2


def test_fewer_than_two_trading_candidates_reports_a_reason(monkeypatch):
    report = _report(monkeypatch, {"a": np.zeros(50), "b": np.zeros(50)})
    assert report["available"] is False and "at least two" in report["reason"]


def test_no_specs_reports_a_reason():
    assert sc.correlation_report(market=None, specs=[])["available"] is False


# ------------------------------------------------------------------ the portfolio arithmetic
def test_the_equal_weight_portfolio_is_the_mean_of_its_members(monkeypatch):
    streams = {"a": np.array([0.02, 0.0, -0.01]), "b": np.array([0.0, 0.04, 0.01])}
    report = _report(monkeypatch, streams)
    assert report["portfolio_sum_pct"] == pytest.approx(100.0 * (0.06 / 2), abs=1e-6)


def test_the_best_single_is_identified_by_total_not_by_order(monkeypatch):
    streams = {"weak": np.array([0.001] * 10), "strong": np.array([0.01] * 10)}
    report = _report(monkeypatch, streams)
    assert report["best_single"] == "strong"


def test_the_report_says_the_sums_are_arithmetic_and_that_it_promotes_nothing(monkeypatch):
    """Both caveats have already caused confusion once: an arithmetic sum read as a compounded return,
    and a measurement read as permission."""
    rng = np.random.default_rng(5)
    report = _report(monkeypatch, {"a": rng.normal(0, 0.01, 200), "b": rng.normal(0, 0.01, 200)})
    assert "not compounded" in report["note"]
    assert "promotes nothing" in report["note"]
