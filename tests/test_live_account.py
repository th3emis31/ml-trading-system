"""Live-account readiness: the tests are about what it REFUSES, because that is the feature.

Every guard in this system has only ever been tested against demo fills. On 23 September 2026 one
threshold change produced ten losing trades in ninety minutes, which cost nothing on demo. This
module exists so the same code cannot reach real money until a specific list of conditions is true.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import live_account as la

LIVE = {"login": 555001, "balance": 1000.0, "currency": "GBP", "trade_mode": 2, "server": "Broker-Live01"}
ARMED = {"login": 555001, "allow_live": True, "confirm_live": 555001}
SANE = {"risk_percent": 1.0, "daily_loss_limit_pct": 3.0}
RECORD = {"available": True, "totals": {"trades": 98, "net": 345.06, "profit_factor": 1.555}}


def _ready(**over):
    args = {"mt5_account": LIVE, "mt4_account": LIVE, "growth": RECORD,
            "settings": SANE, "selection": ARMED}
    args.update(over)
    return la.live_readiness(**args)


def test_everything_in_place_is_ready_on_both_platforms():
    out = _ready()
    assert out["platforms"]["MT5"]["ready"] is True
    assert out["platforms"]["MT4"]["ready"] is True
    assert out["places_orders"] is False, "this module answers a question; it never trades"


def test_an_unarmed_selection_is_refused():
    """Arming live must take a deliberate double confirmation, never a slip of a dropdown."""
    out = _ready(selection={"login": 555001, "allow_live": True})      # confirm_live missing
    assert out["platforms"]["MT5"]["ready"] is False
    assert "Owner armed live trading" in out["platforms"]["MT5"]["blocked_by"]


def test_a_confirmation_for_a_different_login_is_refused():
    out = _ready(selection={"login": 555001, "allow_live": True, "confirm_live": 999999})
    assert out["platforms"]["MT5"]["ready"] is False


def test_a_balance_below_the_floor_is_refused():
    """The owner's floor is 300. Below it one normal loss is a large share of the account."""
    small = {**LIVE, "balance": 299.99}
    out = _ready(mt5_account=small)
    assert out["platforms"]["MT5"]["ready"] is False
    assert any("balance" in name.lower() for name in out["platforms"]["MT5"]["blocked_by"])


def test_there_is_no_maximum_balance():
    """The owner asked for no ceiling, so a large account must not be refused for being large."""
    big = {**LIVE, "balance": 5_000_000.0}
    assert _ready(mt5_account=big)["platforms"]["MT5"]["ready"] is True
    assert _ready()["limits"]["max_balance"] is None


def test_an_unreadable_account_fails_rather_than_passes():
    """"Could not read the balance" and "the balance is fine" must never give the same verdict."""
    out = _ready(mt5_account=None)
    assert out["platforms"]["MT5"]["ready"] is False
    assert out["platforms"]["MT4"]["ready"] is True, "one platform failing must not veto the other"


def test_risk_above_the_live_cap_is_refused():
    out = _ready(settings={"risk_percent": 2.0, "daily_loss_limit_pct": 3.0})
    assert out["platforms"]["MT5"]["ready"] is False


def test_a_missing_daily_loss_limit_is_refused():
    """No limit is not the same as a high limit; an unset stop is unbounded."""
    out = _ready(settings={"risk_percent": 1.0})
    assert out["platforms"]["MT5"]["ready"] is False


def test_real_money_needs_a_track_record():
    """Money follows evidence. Too few settled trades, or a negative record, and the answer is no."""
    thin = {"available": True, "totals": {"trades": 5, "net": 50.0}}
    assert _ready(growth=thin)["platforms"]["MT5"]["ready"] is False
    losing = {"available": True, "totals": {"trades": 98, "net": -20.0}}
    assert _ready(growth=losing)["platforms"]["MT5"]["ready"] is False
    assert _ready(growth=None)["platforms"]["MT5"]["ready"] is False


def test_a_demo_account_is_reported_but_does_not_block():
    """Pointing this at a demo is harmless and worth saying; it must not be treated as a fault."""
    demo = {**LIVE, "trade_mode": 0, "server": "Broker-Demo01"}
    out = _ready(mt5_account=demo)
    assert out["platforms"]["MT5"]["ready"] is True
    note = next(c for c in out["platforms"]["MT5"]["checks"] if "LIVE account" in c["name"])
    assert note["ok"] is False and note["blocking"] is False


def test_the_first_reason_is_given_so_the_page_can_say_why():
    out = _ready(selection={"login": 1, "allow_live": False})
    assert out["platforms"]["MT5"]["first_reason"]


def test_no_function_here_can_take_a_password():
    """Each terminal holds its own credentials; the system only chooses which terminal to attach to.
    A signature is the honest place to check that - a promise in a docstring is not a guard."""
    import inspect
    for name, fn in inspect.getmembers(la, inspect.isfunction):
        params = [p.lower() for p in inspect.signature(fn).parameters]
        assert not any(("password" in p or "passwd" in p or "secret" in p) for p in params), \
            f"{name} accepts a credential parameter"
