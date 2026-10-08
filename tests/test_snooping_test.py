"""The data-snooping test must be proved on planted answers before any real candidate is judged by it.

WHY THIS EXISTS
---------------
The lab has searched 815,989 candidates and not one of its 1,972 book entries clears the 0.95 deflated
Sharpe bar, because that bar charges a candidate for every trial ever run on its market. Hansen's SPA
answers the same question without punishing a good rule for the junk it was searched alongside, and
StepM names which candidates survive rather than returning one yes/no.

A statistical test is an instrument, and this project's rule is that an instrument is validated on
answers known in advance before it is trusted on real data. These are those answers. The decisive one
is `test_the_luckiest_of_many_pure_noise_candidates_is_not_called_real`: the best of 500 noise series
reaches a t-statistic around 3.6, which naive selection calls highly significant, and SPA must refuse
it. If that test ever fails, every verdict this module produces is worthless.

The sign convention is pinned the same way. arch minimises LOSS, so returns enter negated. Fed raw
returns instead, the verdict flips - the kind of error that produces confident numbers pointing the
wrong way, which is worse than no numbers at all.

Nothing here loads bars, runs a backtest, or touches the live registry.
"""
import numpy as np
import pytest

from src import snooping_test as st

pytestmark = pytest.mark.skipif(not st.ARCH_AVAILABLE, reason="arch is not installed")

REPS = 400                      # enough for a stable verdict, quick enough for the suite
N = 1200


def _rng():
    return np.random.default_rng(20261008)


def _spa_p(benchmark, models):
    spa = st.SPA(st.as_losses(benchmark), st.as_losses(models), reps=REPS, seed=7)
    spa.compute()
    return float(spa.pvalues.iloc[1])


# ------------------------------------------------------------------ the planted answers
def test_pure_noise_is_not_called_an_edge():
    rng = _rng()
    assert _spa_p(rng.normal(0, 1, N), rng.normal(0, 1, (N, 40))) > 0.05


def test_a_real_edge_is_found():
    """The planted edge is deliberately unambiguous.

    At 0.15 it lands at p = 0.07 with this sample size and candidate count - a borderline case that
    would make the test flaky and would say nothing useful either way. A validation test has to fail
    loudly when the instrument is broken, so the planted signal is set well clear of the threshold.
    How small an edge SPA can resolve is a separate question, and not one a regression test answers.
    """
    rng = _rng()
    benchmark = rng.normal(0, 1, N)
    models = rng.normal(0, 1, (N, 40))
    models[:, 11] += 0.25
    assert _spa_p(benchmark, models) < 0.05


def test_the_luckiest_of_many_pure_noise_candidates_is_not_called_real():
    """THE test. Everything this module is for rests on this one behaving correctly.

    Draw 500 candidates with no edge whatsoever, take the best. Its t-statistic lands around 3.6,
    which any naive significance test calls a discovery. SPA must still refuse it, because it knows
    500 were tried.
    """
    rng = _rng()
    benchmark = rng.normal(0, 1, N)
    many = rng.normal(0, 1, (N, 500))
    best = int(np.argmax(many.mean(axis=0)))
    t_stat = many[:, best].mean() / (many[:, best].std(ddof=1) / np.sqrt(N))
    assert t_stat > 2.5, "the planted trap is not sharp enough to be a test"

    assert _spa_p(benchmark, many) > 0.05, (
        f"SPA accepted the luckiest of 500 pure-noise candidates (t={t_stat:.2f}). Every verdict this "
        f"module produces is then worthless.")


def test_stepm_names_the_right_candidate_and_only_it():
    rng = _rng()
    benchmark = rng.normal(0, 1, N)
    models = rng.normal(0, 1, (N, 30))
    models[:, 5] += 0.25
    step = st.StepM(st.as_losses(benchmark), st.as_losses(models), size=0.05, reps=REPS, seed=7)
    step.compute()
    chosen = [int("".join(ch for ch in str(c) if ch.isdigit())) for c in step.superior_models]
    assert chosen == [5], f"StepM selected {chosen}, expected only the planted edge at 5"


def test_stepm_selects_nothing_when_nothing_is_better():
    rng = _rng()
    step = st.StepM(st.as_losses(rng.normal(0, 1, N)), st.as_losses(rng.normal(0, 1, (N, 30))),
                    size=0.05, reps=REPS, seed=7)
    step.compute()
    assert list(step.superior_models) == []


# ------------------------------------------------------------------ the sign convention
def test_returns_enter_negated_because_arch_minimises_loss():
    assert list(st.as_losses([0.1, -0.2, 0.0])) == [-0.1, 0.2, -0.0]


def test_feeding_raw_returns_instead_of_losses_flips_the_verdict():
    """Pinned by measurement, not by trusting the documentation. A silent sign error here would
    produce confident p-values pointing the wrong way."""
    rng = _rng()
    benchmark = rng.normal(0, 1, N)
    models = rng.normal(0, 1, (N, 40))
    models[:, 3] += 0.20

    right = st.SPA(st.as_losses(benchmark), st.as_losses(models), reps=REPS, seed=7)
    right.compute()
    wrong = st.SPA(benchmark, models, reps=REPS, seed=7)
    wrong.compute()
    assert (float(right.pvalues.iloc[1]) < 0.05) != (float(wrong.pvalues.iloc[1]) < 0.05)


# ------------------------------------------------------------------ it degrades, never crashes
def test_no_candidates_reports_unavailable_rather_than_raising():
    result = st.superior_candidates(market=None, specs=[])
    assert result["available"] is False and "no candidates" in result["reason"]


def test_a_missing_arch_reports_a_status_not_an_exception(monkeypatch):
    """CLAUDE.md: optional integrations degrade rather than fail. A missing package must surface as a
    status field, the way MT5 and voice do."""
    monkeypatch.setattr(st, "ARCH_AVAILABLE", False)
    result = st.superior_candidates(market=None, specs=[{"variant": "x"}])
    assert result["available"] is False
    assert "arch" in result["reason"] and "pip install" in result["reason"]


# ------------------------------------------------------------------ it must not quietly promote
def test_the_result_says_plainly_that_it_promotes_nothing():
    """This is a second opinion beside the 0.95 bar, never a replacement. If that note ever goes, a
    low p-value here could be read as permission to trade."""
    import inspect
    source = inspect.getsource(st)
    assert "does NOT promote anything" in source
    assert "0.95" in source, "the standing bar must stay named in the module that could be mistaken for it"
