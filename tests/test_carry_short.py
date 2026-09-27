"""The carry decomposition, and the sign convention it rests on.

`src/carry_short.py` claims a gold short's return can be split into what price paid and what financing paid.
The whole hypothesis in `strategies/carry_short_gold.md` turns on that split, and it turns on one sign: the
engine computes ``net_pct = (gross - cost_pct - swap_frac) * 100``, so a broker CREDIT appears as a NEGATIVE
`swap_pct` and adds to the result. Get that backwards and a credit reads as a cost, which is the exact
mistake the cost table itself made until 27 September 2026.

No market data, no engine run, no orders: synthetic trades with arithmetic that can be checked by hand.
"""
import pytest

from src import carry_short
from src import strategy_lab as lab


def _trade(net_pct, swap_pct, nights):
    return {"net_pct": net_pct, "swap_pct": swap_pct, "nights": nights}


def test_a_credit_shows_as_positive_carry_and_is_added_to_the_result():
    """swap_pct -0.10 means the broker PAID 0.10 %, so carry is +0.10 and price is net minus that."""
    trades = [_trade(net_pct=1.00, swap_pct=-0.10, nights=2)]
    out = carry_short.decompose(trades)
    assert out["carry_pct"] == 0.10, "a negative swap_pct is a credit and must read as positive carry"
    assert out["price_and_spread_pct"] == pytest.approx(0.90), "price did 0.90 of the 1.00; financing did 0.10"
    assert out["sum_net_pct"] == pytest.approx(1.00)


def test_a_charge_shows_as_negative_carry():
    """The long side, and every short before the correction: swap_pct positive, so carry is negative."""
    trades = [_trade(net_pct=0.80, swap_pct=0.20, nights=2)]
    out = carry_short.decompose(trades)
    assert out["carry_pct"] == -0.20
    assert out["price_and_spread_pct"] == pytest.approx(1.00), "price made 1.00; financing took 0.20 of it"


def test_the_parts_always_add_back_to_the_whole():
    trades = [_trade(1.5, -0.1, 1), _trade(-2.0, -0.4, 5), _trade(0.3, 0.05, 1), _trade(-0.7, -0.25, 3)]
    out = carry_short.decompose(trades)
    assert out["carry_pct"] + out["price_and_spread_pct"] == pytest.approx(out["sum_net_pct"], abs=1e-6)


def test_carry_per_night_is_the_rate_the_broker_is_paying():
    """Four nights earning 0.008 % each must read back as 0.008 %, or the hypothesis cannot be checked."""
    trades = [_trade(net_pct=0.5, swap_pct=-0.032, nights=4)]
    out = carry_short.decompose(trades)
    assert out["carry_per_night_pct"] == pytest.approx(0.008)
    assert out["nights_per_trade"] == pytest.approx(4.0)


def test_no_trades_is_absent_not_zero():
    """An empty result must not report 0.0 carry, which would look like a measurement of nothing."""
    assert carry_short.decompose([]) == {}


def test_the_configured_gold_short_rate_is_still_a_credit():
    """The hypothesis has no subject if the rate stops being negative - a live dependency, not a constant.

    `strategies/carry_short_gold.md` lists this under known failure modes: swap_short is a broker setting and
    can change or invert. If it does, this test fails and the strategy document must be revisited rather than
    the experiment quietly producing nothing.
    """
    assert lab.HOLDING_COSTS["XAUUSD"]["short_pct_per_night"] < 0, (
        "gold shorts are no longer credited; carry_short_gold.md needs revisiting")


def test_only_the_hold_length_differs_between_the_swept_specs():
    """The experiment's validity: if anything else moves, the result is not attributable to the hold."""
    specs = [carry_short.spec_for("XAUUSD", "4h", bars, "short") for bars in carry_short.HOLD_BARS]
    assert len({s["exits"]["max_bars"] for s in specs}) == len(carry_short.HOLD_BARS), "hold lengths must differ"
    first = specs[0]
    for other in specs[1:]:
        assert other["params"] == first["params"], "params must be identical across the sweep"
        moved = {k for k in first["exits"] if first["exits"][k] != other["exits"][k]}
        assert moved == {"max_bars"}, f"only max_bars may differ, but {moved} did"


def test_both_sides_are_tested_so_a_trend_effect_cannot_pass_as_carry():
    short = carry_short.spec_for("XAUUSD", "4h", 30, "short")
    long_ = carry_short.spec_for("XAUUSD", "4h", 30, "long")
    assert short["params"]["side"] == "short" and long_["params"]["side"] == "long"
    assert short["exits"] == long_["exits"], "the two sides must be compared at the same hold length"
