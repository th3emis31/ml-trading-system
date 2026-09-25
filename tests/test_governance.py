"""What the system may do, and who has to say so - i40 Pilot build map step 6.

The owner wants a system that can use the PC fully, build and deploy, and trade. That surface is
held by a tier per action, and the three rules below are the ones that make it safe rather than
merely careful. All three were learned in this codebase, not invented.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from src import governance as g


@pytest.fixture()
def base(tmp_path):
    return tmp_path


def test_actions_land_in_the_right_tier():
    assert g.tier_for("read the account balance") == g.T0_READ
    assert g.tier_for("write a draft note") == g.T1_LOCAL
    assert g.tier_for("send the weekly email") == g.T2_OUTWARD
    assert g.tier_for("place a buy order on XAUUSD") == g.T3_CRITICAL
    assert g.tier_for("delete the archive") == g.T3_CRITICAL


def test_a_caller_may_raise_its_own_tier_but_never_lower_it():
    """Self-declaration downward is exactly how a guard gets walked around."""
    assert g.tier_for("place a buy order", declared=g.T0_READ) == g.T3_CRITICAL
    assert g.tier_for("read a file", declared=g.T3_CRITICAL) == g.T3_CRITICAL


def test_reading_never_needs_permission(base):
    assert g.check_permission("read the account balance", base=base).allowed is True


def test_an_outward_action_without_approval_is_refused(base):
    out = g.check_permission("send the weekly email", base=base)
    assert out.allowed is False and "explicit approval" in out.reason


def test_approval_does_not_carry_from_one_action_to_another(base):
    """Yesterday's yes is not today's, and an approval for one order is not approval for the next."""
    approval = {"action": "place a sell order on BTCUSD", "by": "owner", "figures": {"lots": 0.01}}
    out = g.check_permission("place a buy order on XAUUSD", approval=approval,
                             figures={"lots": 0.01}, base=base)
    assert out.allowed is False
    assert "does not carry" in out.reason


def test_a_critical_action_must_state_its_figures(base):
    approval = {"action": "place a buy order on XAUUSD", "by": "owner"}
    out = g.check_permission("place a buy order on XAUUSD", approval=approval, base=base)
    assert out.allowed is False
    assert "figures" in out.reason


def test_figures_that_drifted_since_approval_are_refused(base):
    """The owner approved 0.01 lots. Placing 0.10 is a different decision wearing the same words."""
    approval = {"action": "place a buy order on XAUUSD", "by": "owner",
                "figures": {"lots": 0.01, "entry": 4268.0}}
    out = g.check_permission("place a buy order on XAUUSD", approval=approval,
                             figures={"lots": 0.10, "entry": 4268.0}, base=base)
    assert out.allowed is False
    assert "changed since approval" in out.reason and "lots" in out.reason


def test_a_matching_approval_allows_the_action(base):
    approval = {"action": "place a buy order on XAUUSD", "by": "owner",
                "figures": {"lots": 0.01, "entry": 4268.0}}
    out = g.check_permission("place a buy order on XAUUSD", approval=approval,
                             figures={"lots": 0.01, "entry": 4268.0}, base=base)
    assert out.allowed is True and out.audit_id


def test_a_halt_stops_new_actions_but_never_closes_positions(base):
    """The owner's standing rule: a trade closes at its own stop, target or time exit, never by hand.
    A kill switch that flattened the book would be obeying a panic and breaking a rule."""
    g.halt("owner pressed stop", base=base)
    approval = {"action": "place a buy order on XAUUSD", "by": "owner", "figures": {"lots": 0.01}}
    blocked = g.check_permission("place a buy order on XAUUSD", approval=approval,
                                 figures={"lots": 0.01}, base=base)
    assert blocked.allowed is False and "halted" in blocked.reason

    # Checked as CODE, not as prose: the module's own docstring explains why a halt does not flatten
    # the book, and a naive substring search matches that explanation.
    source = Path(g.__file__).read_text(encoding="utf-8")
    code = "\n".join(line for line in source.splitlines()
                     if not line.lstrip().startswith("#"))
    for forbidden in ("close_position(", "close_all(", "flatten(", "order_close(",
                      "import MetaTrader5", "from .mt5", "mt5_service"):
        assert forbidden not in code, f"a halt must not be able to close anything: {forbidden}"
    assert "NOT closed" in g.halt_state(base)["note"]


def test_reading_still_works_while_halted(base):
    g.halt("stop", base=base)
    assert g.check_permission("read the account balance", base=base).allowed is True


def test_resume_lifts_the_halt(base):
    g.halt("stop", base=base)
    g.resume(base=base)
    assert g.check_permission("write a draft note", base=base).allowed is True


def test_an_unproven_capability_defaults_to_dry_run(base):
    """Unknown means unproven, and unproven produces output without acting."""
    assert g.in_dry_run("brand_new_thing", base) is True
    out = g.check_permission("write a draft note", capability="brand_new_thing", base=base)
    assert out.allowed is False and "dry run" in out.reason


def test_a_capability_taken_out_of_dry_run_may_act(base):
    (base / g.DRY_RUN_NAME).write_text('{"proven_thing": false}', encoding="utf-8")
    assert g.in_dry_run("proven_thing", base) is False
    assert g.check_permission("write a draft", capability="proven_thing", base=base).allowed is True


def test_refusals_are_audited_as_carefully_as_actions(base):
    """A trade that does not happen must be as visible as one that does, or the next person sees an
    idle system and no reason for it."""
    g.check_permission("send the weekly email", base=base)
    rows = g.read_audit(base=base)
    assert rows and rows[-1]["allowed"] is False
    assert rows[-1]["tier"] == g.T2_OUTWARD and rows[-1]["reason"]


def test_the_audit_is_append_only(base):
    g.check_permission("write a note", base=base)
    g.check_permission("write another note", base=base)
    assert len(g.read_audit(base=base)) == 2


def test_dry_run_beats_even_an_owner_approval(base):
    """The gap this closed: dry run used to be checked only at T1, so an unproven capability carrying
    an approval could place an order on its very first outing."""
    approval = {"action": "place a buy order on XAUUSD", "by": "owner", "figures": {"lots": 0.01}}
    out = g.check_permission("place a buy order on XAUUSD", approval=approval,
                             figures={"lots": 0.01}, capability="untested_strategy", base=base)
    assert out.allowed is False and "dry run" in out.reason
