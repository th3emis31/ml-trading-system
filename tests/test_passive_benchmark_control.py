"""A strategy that cannot beat holding the instrument has not found an edge in it.

WHY THIS CONTROL EXISTS
-----------------------
`buy_and_hold_pct` has been recorded on every split since the beginning and NOTHING ever read it.

On 5 October 2026 it was finally read. The single best candidate out of 754,597 - the only one in the
project's history to approach the 0.95 bar, with a holdout profit factor of 2.83, 52 trades and a
4.7% maximum drawdown - returned **42.03%** on its holdout while simply holding gold returned
**65.98%**. It lost to buy-and-hold on all three splits:

    search       19.70%  vs   99.74%
    validation   12.49%  vs   43.16%
    holdout      42.03%  vs   65.98%

It was not finding an edge. It was capturing part of a bull market and charging 52 round trips for a
fraction of it. `.claude/memory/BASELINE.md` records the identical trap on 19 September 2026.

WHAT IT GATES, AND WHAT IT DELIBERATELY DOES NOT
------------------------------------------------
It gates PROMOTION and nothing else. The owner's standing rule is that the evidence bars govern
whether money follows, never whether the system may learn: a candidate is still evaluated, still
scored, still ranked, still stored and still available to the research loop exactly as before.
`test_the_control_gates_promotion_and_never_learning` is the test that holds that line.

DIRECTION MATTERS, or the control rejects the very candidates worth finding
--------------------------------------------------------------------------
A short strategy returning +10% while gold rises 66% has beaten ITS passive alternative - holding
short, which would have lost 66% - by a huge margin. Demanding it beat +66% would reject every short
strategy on a rising instrument by construction, which is exactly the blind spot that left the book
holding 909 long XAUUSD entries and 1 short.
"""
import pytest

from src import strategy_lab as lab


def _summary(total_return_pct, buy_and_hold_pct, **extra):
    base = {"trades": 50, "total_return_pct": total_return_pct, "buy_and_hold_pct": buy_and_hold_pct,
            "max_drawdown_pct": 5.0, "profit_factor": 2.0, "gross_profit": 200.0, "gross_loss": 100.0}
    base.update(extra)
    return base


def _record(side="long", total=42.03, bh=65.98, returns=None):
    return {
        "spec": {"family": "donchian_breakout", "params": {"side": side}, "exits": {}},
        "holdout": _summary(total, bh),
        "holdout_returns": returns if returns is not None else [0.8] * 50,
    }


# ----------------------------------------------------------------- the benchmark itself
def test_a_long_candidate_is_measured_against_buy_and_hold():
    assert lab.passive_benchmark({"params": {"side": "long"}}, _summary(42.0, 65.98)) == 65.98


def test_a_short_candidate_is_measured_against_HOLDING_SHORT():
    """+10% while gold rose 66% beats holding short (-66%) by a mile, and must be allowed to."""
    assert lab.passive_benchmark({"params": {"side": "short"}}, _summary(10.0, 65.98)) == -65.98


def test_both_sided_candidates_use_buy_and_hold_not_hindsight():
    """max(bh, -bh) would be a hindsight benchmark: nobody knew which way to hold in advance."""
    assert lab.passive_benchmark({"params": {"side": "both"}}, _summary(5.0, -30.0)) == -30.0


def test_a_missing_side_defaults_to_long():
    assert lab.passive_benchmark({"params": {}}, _summary(5.0, 20.0)) == 20.0
    assert lab.passive_benchmark({}, _summary(5.0, 20.0)) == 20.0


def test_a_missing_benchmark_is_None_not_zero():
    """Zero would silently turn 'unverifiable' into 'beat it', which is the failure mode this whole
    control exists to stop."""
    assert lab.passive_benchmark({"params": {"side": "long"}}, {"total_return_pct": 10.0}) is None


# ----------------------------------------------------------------- the gate
def test_the_real_beta_rider_is_refused():
    """The exact candidate, with its real numbers."""
    verdict = lab.holdout_verdict(_record(), n_trials=90_231, sr_variance=0.05)
    assert verdict["checks"]["beats_buy_and_hold"] is False
    assert verdict["passed"] is False
    assert verdict["passive_benchmark_pct"] == 65.98
    assert verdict["beat_benchmark_by_pct"] == pytest.approx(-23.95, abs=0.01)


def test_a_strategy_that_genuinely_beats_holding_passes_this_check():
    verdict = lab.holdout_verdict(_record(total=80.0, bh=65.98), n_trials=10, sr_variance=0.05)
    assert verdict["checks"]["beats_buy_and_hold"] is True
    assert verdict["beat_benchmark_by_pct"] == pytest.approx(14.02, abs=0.01)


def test_a_short_strategy_on_a_RISING_market_is_not_rejected_by_this_check():
    """The blind spot being corrected: 909 long XAUUSD entries in the book against 1 short."""
    verdict = lab.holdout_verdict(_record(side="short", total=10.0, bh=65.98), n_trials=10, sr_variance=0.05)
    assert verdict["passive_benchmark_pct"] == -65.98
    assert verdict["checks"]["beats_buy_and_hold"] is True, "a short must be judged against holding short"


def test_an_unverifiable_benchmark_fails_closed():
    """Promotion needs evidence, and an unverifiable control is not evidence. Failing OPEN here would
    let any candidate with a missing column promote itself."""
    record = _record()
    record["holdout"].pop("buy_and_hold_pct")
    verdict = lab.holdout_verdict(record, n_trials=10, sr_variance=0.05)
    assert verdict["checks"]["beats_buy_and_hold"] is False
    assert verdict["passive_benchmark_pct"] is None


def test_the_check_can_be_turned_off_without_touching_code(monkeypatch):
    """It is a configured criterion like every other, so the owner can see and change it in one place."""
    monkeypatch.setitem(lab.HOLDOUT_CRITERIA, "require_beating_buy_and_hold", False)
    verdict = lab.holdout_verdict(_record(), n_trials=10, sr_variance=0.05)
    assert verdict["checks"]["beats_buy_and_hold"] is True


# ----------------------------------------------------------------- the line that must not move
def test_the_control_gates_promotion_and_never_learning():
    """The owner's standing rule: the bars govern whether MONEY follows, never whether the system may
    learn. A candidate refused here must still be validated, scored and ranked.
    """
    assert "require_beating_buy_and_hold" in lab.HOLDOUT_CRITERIA
    assert "buy_and_hold" not in str(lab.GATES), "the search/validation gates must be untouched"
    for key in ("search_min_trades", "validation_min_trades", "min_profit_factor",
                "min_year_share", "min_trades_per_counted_year"):
        assert key in lab.GATES, f"{key} disappeared from the learning gates"
    assert lab.GATES == {"search_min_trades": 30, "validation_min_trades": 15, "min_profit_factor": 1.1,
                         "min_year_share": 0.6, "min_trades_per_counted_year": 5}, \
        "the gates that decide what the system LEARNS from must be exactly as they were"


def test_the_095_bar_is_untouched():
    """Adding a control is not an excuse to move the bar, in either direction."""
    assert lab.HOLDOUT_CRITERIA["min_deflated_sharpe"] == 0.95
    assert lab.HOLDOUT_CRITERIA["min_profit_factor"] == 1.2
    assert lab.HOLDOUT_CRITERIA["min_trades"] == 30
    assert lab.HOLDOUT_CRITERIA["max_drawdown_pct"] == 20.0
    assert lab.HOLDOUT_CRITERIA["benchmark_margin_pct"] == 0.0, "no extra hurdle may be invented"
