"""STEP 2: the signal engine's full research record, and the promise that it broke nothing.

Four things are pinned here, in the order the specification asked for them:

* a **valid** signal is accepted, carries every structured field, and invents none of them
* an **invalid** signal is refused, and refused for the most FUNDAMENTAL reason rather than the last
  test that happened to run
* every rejection carries a **deterministic code** so refusals can be counted rather than only read
* **backward compatibility**: `SignalDecision` and `decide_signal` behave exactly as before

The third is the one worth stating plainly. The reason a code exists at all is that free text cannot
be grouped: "0 of 2 present; missing fvg, sweep" and "1 of 2 present; missing sweep" are the same
refusal to a reader and two different keys to a `Counter`, so a run's rejection profile fragments into
near-duplicates and the question this engine exists to answer stops being answerable.
"""
import pytest

from engine import signal_engine as se
from engine.reject_codes import ALL_CODES, RISK_CODES, SIGNAL_CODES, RejectCode, named


BASE = dict(asset="XAUUSD", timestamp="2026-09-27 12:00", bar=10, timeframe="4h",
            strategy="sweep_reclaim", variant="v1")
GOOD = dict(direction="SELL", entry=4280.0, stop=4288.0, tp1=4256.0,
            conditions={"sweep": True, "reclaim": True}, min_confluence=2)


# --------------------------------------------------------------------------- a valid signal

def test_a_valid_signal_carries_every_structured_field():
    s = se.build_signal(**BASE, **GOOD, regime={"trend": "trending", "volatility": "high"},
                        invalidation=4290.0, confidence=0.61,
                        trial_id="t-7", experiment_id="exp-2026-09-27-a")
    assert s.accepted is True and s.reject_code is None
    assert (s.asset, s.timeframe, s.strategy, s.variant) == ("XAUUSD", "4h", "sweep_reclaim", "v1")
    assert (s.direction, s.entry, s.stop, s.tp1) == ("SELL", 4280.0, 4288.0, 4256.0)
    assert s.risk == pytest.approx(8.0), "risk is the price distance from entry to stop"
    assert s.reward_risk == pytest.approx(3.0), "24 points of target over 8 of risk"
    assert s.regime == {"trend": "trending", "volatility": "high"}
    assert s.confidence == 0.61 and s.invalidation == 4290.0
    assert (s.trial_id, s.experiment_id) == ("t-7", "exp-2026-09-27-a")
    assert s.reasons and "sweep" in s.reasons[0], "the free-text reason is preserved, not replaced"


def test_absent_targets_are_None_and_are_not_invented():
    """Most rules here produce ONE take-profit. A guessed tp2 would put a number in the ledger that no
    rule ever computed, and a later study of "how often does tp2 fill" would measure this function."""
    s = se.build_signal(**BASE, **GOOD)
    assert s.tp1 == 4256.0
    assert s.tp2 is None and s.tp3 is None
    assert s.targets == [4256.0], "only the targets that exist, and no zeros standing in for absence"


def test_invalidation_is_not_silently_set_to_the_stop():
    """They are different facts: the stop is where the position closes, the invalidation is what would
    prove the idea wrong. Defaulting one to the other destroys the distinction a post-mortem needs."""
    assert se.build_signal(**BASE, **GOOD).invalidation is None


def test_every_condition_checked_is_kept_not_only_the_ones_that_passed():
    """A condition that is always true is doing nothing, and that is invisible if only passes survive."""
    s = se.build_signal(**BASE, direction="SELL", entry=4280.0, stop=4288.0, tp1=4256.0,
                        conditions={"sweep": True, "reclaim": True, "fvg": False}, min_confluence=2)
    assert s.source_conditions == {"sweep": True, "reclaim": True, "fvg": False}


def test_the_row_serialises_the_code_as_its_stable_string():
    row = se.build_signal(**BASE, direction=None).as_row()
    assert row["reject_code"] == "REJECT_NO_SETUP", "a JSON ledger must hold the code, not a repr"
    assert row["accepted"] is False
    assert se.build_signal(**BASE, **GOOD).as_row()["reject_code"] is None


# --------------------------------------------------------------------------- invalid signals

@pytest.mark.parametrize("why,kw,code", [
    ("no candidate",      dict(direction=None), RejectCode.NO_SETUP),
    ("entry missing",     dict(direction="SELL", entry=None, stop=4288.0, tp1=4256.0),
     RejectCode.INVALID_ENTRY),
    ("entry not a price", dict(direction="SELL", entry=0.0, stop=4288.0, tp1=4256.0),
     RejectCode.INVALID_ENTRY),
    ("no stop",           dict(direction="SELL", entry=4280.0, stop=None, tp1=4256.0),
     RejectCode.INVALID_STOP),
    ("stop at entry",     dict(direction="SELL", entry=4280.0, stop=4280.0, tp1=4256.0),
     RejectCode.INVALID_STOP),
    ("stop wrong side",   dict(direction="SELL", entry=4280.0, stop=4272.0, tp1=4256.0),
     RejectCode.INVALID_STOP),
    ("no target",         dict(direction="SELL", entry=4280.0, stop=4288.0, tp1=None),
     RejectCode.INVALID_TARGET),
    ("target wrong side", dict(direction="SELL", entry=4280.0, stop=4288.0, tp1=4300.0),
     RejectCode.INVALID_TARGET),
])
def test_an_unusable_setup_is_refused_with_the_right_code(why, kw, code):
    s = se.build_signal(**BASE, **kw)
    assert s.accepted is False, why
    assert s.reject_code is code, f"{why}: got {s.reject_code}"
    assert s.reasons and s.reasons[0], "a refusal must also say why in words"


@pytest.mark.parametrize("missing,code", [
    ("fvg", RejectCode.NO_FVG),
    ("sweep", RejectCode.NO_LIQUIDITY),
    ("liquidity_sweep", RejectCode.NO_LIQUIDITY),
    ("reclaim", RejectCode.INVALID_RECLAIM),
    ("session_ok", RejectCode.SESSION),
])
def test_a_NAMED_condition_that_failed_reports_its_own_code(missing, code):
    """"no FVG" and "not enough conditions" are different research findings. Collapsing them into one
    loses the only one that can be acted on."""
    s = se.build_signal(**BASE, direction="SELL", entry=4280.0, stop=4288.0, tp1=4256.0,
                        conditions={missing: False}, min_confluence=1)
    assert s.reject_code is code
    assert missing in s.reasons[0]


def test_an_unnamed_shortfall_falls_back_to_invalid_structure():
    s = se.build_signal(**BASE, direction="SELL", entry=4280.0, stop=4288.0, tp1=4256.0,
                        conditions={"poc": False, "volume": False}, min_confluence=2)
    assert s.reject_code is RejectCode.INVALID_STRUCTURE
    assert "poc" in s.reasons[0] and "volume" in s.reasons[0], "it must still name what was missing"


def test_trading_against_the_structural_bias_is_its_own_code():
    s = se.build_signal(**BASE, direction="SELL", entry=4280.0, stop=4288.0, tp1=4256.0,
                        conditions={"poc": True}, min_confluence=1,
                        bias=se.BULLISH, require_bias_agreement=True)
    assert s.reject_code is RejectCode.NO_TREND


def test_the_most_fundamental_objection_is_the_one_reported():
    """A setup with no stop AND too few conditions has two faults. "it had no stop" is the deeper one,
    and reporting the shallower one would send a reader looking for a confluence problem."""
    s = se.build_signal(**BASE, direction="SELL", entry=4280.0, stop=None, tp1=4256.0,
                        conditions={"fvg": False}, min_confluence=3)
    assert s.reject_code is RejectCode.INVALID_STOP


def test_a_signal_rejection_never_carries_a_RISK_code():
    """A risk code appearing in a signal rejection would silently move the funnel's blame from one
    engine to another, and the funnel would still add up."""
    for kw in (dict(direction=None),
               dict(direction="SELL", entry=4280.0, stop=None, tp1=4256.0),
               dict(direction="SELL", entry=4280.0, stop=4288.0, tp1=4256.0,
                    conditions={"fvg": False}, min_confluence=2)):
        code = se.build_signal(**BASE, **kw).reject_code
        assert code in SIGNAL_CODES and code not in RISK_CODES, code


# --------------------------------------------------------------------------- counting the refusals

def test_the_profile_counts_by_code_and_reports_uncoded_rejections_rather_than_hiding_them():
    signals = [
        se.build_signal(**BASE, **GOOD),
        se.build_signal(**BASE, direction=None),
        se.build_signal(**BASE, direction=None),
        se.build_signal(**BASE, direction="SELL", entry=4280.0, stop=None, tp1=4256.0),
    ]
    # A rejection with no code is a DEFECT, so the profile counts it instead of quietly dropping it.
    broken = se.build_signal(**BASE, direction=None)
    broken.reject_code = None
    signals.append(broken)

    out = se.reject_profile(signals)
    assert out["opportunities"] == 5 and out["accepted"] == 1 and out["rejected"] == 4
    assert out["by_code"] == {"REJECT_NO_SETUP": 2, "REJECT_INVALID_STOP": 1}
    assert out["rejections_without_a_code"] == 1
    assert out["acceptance_rate_pct"] == pytest.approx(20.0)


def test_an_empty_run_reports_no_acceptance_rate_rather_than_zero():
    """0 % implies opportunities that were all refused. None says there were none."""
    assert se.reject_profile([])["acceptance_rate_pct"] is None


# --------------------------------------------------------------------------- the codes themselves

def test_a_code_reads_back_from_its_stored_string_or_its_member_name():
    """A ledger written months ago holds the string; reading it must not need the writer's Python."""
    assert named("REJECT_NO_FVG") is RejectCode.NO_FVG
    assert named("NO_FVG") is RejectCode.NO_FVG
    assert named(RejectCode.NO_FVG) is RejectCode.NO_FVG
    with pytest.raises(ValueError):
        named("REJECT_SOMETHING_INVENTED")


def test_every_code_value_carries_the_REJECT_prefix_and_the_sets_are_disjoint():
    assert all(c.value.startswith("REJECT_") for c in RejectCode)
    assert SIGNAL_CODES.isdisjoint(RISK_CODES)
    assert SIGNAL_CODES | RISK_CODES <= ALL_CODES
    assert len({c.value for c in RejectCode}) == len(list(RejectCode)), "no two codes share a value"


# --------------------------------------------------------------------------- backward compatibility

def test_the_compact_pair_behaves_exactly_as_before():
    """`decide_signal` and `SignalDecision` are what the bar loop calls. STEP 2 is additive, so these
    must be untouched — `tests/test_engine_package.py` also still exercises them unchanged."""
    out = se.decide_signal(1, "t", side="SELL", entry=4280, stop=4288, target=4256,
                           conditions={"fvg": True, "sweep": True, "poc": False}, min_confluence=2)
    assert out.take is True
    assert out.present() == ["fvg", "sweep"] and out.confluence == 2
    assert "SELL with 2 condition(s)" in out.reason
    assert not hasattr(out, "reject_code"), "the compact decision gains no new fields"

    refused = se.decide_signal(1, "t", side="BUY", entry=4280, stop=4288, target=4310,
                               conditions={"a": True}, min_confluence=1)
    assert refused.take is False and "wrong side" in refused.reason


def test_a_compact_decision_can_be_promoted_without_rewriting_the_bar_loop():
    """The migration seam. Without it the old and new paths get rewritten separately and start
    disagreeing about the same bar, which is the failure this project has paid for twice."""
    decision = se.decide_signal(10, "2026-09-27 12:00", side="SELL", entry=4280.0, stop=4288.0,
                                target=4256.0, conditions={"sweep": True, "reclaim": True},
                                min_confluence=2)
    record = se.signal_from_decision(decision, asset="XAUUSD", timeframe="4h",
                                     strategy="sweep_reclaim", variant="v1")
    assert record.accepted is True and record.reject_code is None
    assert (record.bar, record.timestamp) == (decision.bar, decision.ts)
    assert (record.entry, record.stop, record.tp1) == (4280.0, 4288.0, 4256.0)
    assert record.source_conditions == decision.conditions
    assert record.invalidation == decision.stop, "promoted explicitly, defaulting to the stop"


def test_a_refused_compact_decision_promotes_to_a_refused_record_with_a_code():
    decision = se.decide_signal(5, "t", side="SELL", entry=4280.0, stop=4272.0, target=4256.0,
                                conditions={"sweep": True}, min_confluence=1)
    assert decision.take is False
    record = se.signal_from_decision(decision, asset="XAUUSD")
    assert record.accepted is False
    assert record.reject_code is RejectCode.INVALID_STOP, "the free text becomes a countable code"
