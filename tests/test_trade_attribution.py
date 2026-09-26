"""A trade placed by hand must never be reported as the system's work.

Written after +GBP 381.96 of account profit was reported as this system's performance when GBP 328.68 of
it was the owner's own manual trading and the automatic route was in fact NEGATIVE. The hazard had been
documented in `performance_analytics.SHARED_DEFAULT_MAGICS` for days and nothing acted on it.
"""
from __future__ import annotations

from src.performance_analytics import (SHARED_DEFAULT_MAGICS, SYSTEM_MAGICS, attribute_trade,
                                       attribution_split)


def _trade(magic, volume=0.01, direction="BUY", comment="", net=1.0):
    return {"magic": magic, "volume": volume, "direction": direction, "comment": comment, "net": net}


def test_a_dedicated_magic_settles_it_outright():
    for magic in (440401, 440502, 440603, 440704, 440805, 440906):
        out = attribute_trade(_trade(magic, volume=0.05, direction="SELL"))
        assert out["owner"] == "system" and out["confidence"] == "certain", magic


def test_a_magic_this_system_never_uses_is_somebody_elses():
    out = attribute_trade(_trade(0))
    assert out["owner"] == "other" and out["confidence"] == "certain"
    assert attribute_trade(_trade(123456))["owner"] == "other"


def test_the_shared_default_is_split_on_footprint_not_assumed():
    """903110 is mt5_service's default, so it proves nothing on its own."""
    assert 903110 in SHARED_DEFAULT_MAGICS and 903110 in SYSTEM_MAGICS
    system = attribute_trade(_trade(903110, volume=0.01, direction="BUY"))
    assert system["owner"] == "system" and system["confidence"] == "probable"
    manual = attribute_trade(_trade(903110, volume=0.04, direction="SELL"))
    assert manual["owner"] == "manual"


def test_the_owners_real_winning_trades_are_attributed_to_them():
    """The actual shape of the trades that made the money: SELL gold at 0.04-0.08 lots, comment only the
    broker's own exit annotation. This system is long-only at 0.01."""
    for volume in (0.02, 0.04, 0.05, 0.06, 0.08):
        out = attribute_trade(_trade(903110, volume=volume, direction="SELL", comment="[tp 4028.40]"))
        assert out["owner"] == "manual", f"{volume} lots SELL was credited to the system"


def test_the_brokers_own_exit_annotation_is_not_a_system_comment():
    """`[tp 4028.40]` and `[sl 4111.71]` are what MT5 writes on an exit, not what this system writes on
    an order. Treating them as a system signature is what hid 96 trades."""
    for comment in ("[tp 4028.40]", "[sl 4111.71]", "[tp 65044.22]"):
        out = attribute_trade(_trade(903110, volume=0.04, direction="SELL", comment=comment))
        assert out["owner"] == "manual", comment


def test_a_comment_naming_the_system_is_believed_over_the_footprint():
    out = attribute_trade(_trade(903110, volume=0.05, direction="SELL", comment="SmartEntry auto"))
    assert out["owner"] == "system" and out["confidence"] == "certain"


def test_the_heuristic_errs_toward_crediting_the_system():
    """So it cannot flatter the answer to 'is the automatic trading any good'. A manual 0.01 buy is
    counted as the system's, which can only make the system look WORSE, never better."""
    out = attribute_trade(_trade(903110, volume=0.01, direction="BUY"))
    assert out["owner"] == "system", "the conservative direction is to credit the system"


def test_the_split_keeps_the_two_totals_apart():
    trades = ([_trade(903110, 0.04, "SELL", "[tp 1]", net=100.0)] * 3
              + [_trade(903110, 0.01, "BUY", "[sl 1]", net=-10.0)] * 2
              + [_trade(440603, 0.01, "BUY", net=25.0)])
    out = attribution_split(trades)
    assert out["manual_trades"] == 3 and out["manual_net"] == 300.0
    assert out["system_trades"] == 3 and out["system_net"] == 5.0
    assert "cannot flatter" in out["caveat"]


def test_the_split_never_returns_one_number_for_an_account_holding_both():
    out = attribution_split([_trade(903110, 0.04, "SELL", net=50.0), _trade(440401, net=5.0)])
    assert "system_net" in out and "manual_net" in out
    assert out["system_net"] != out["manual_net"]


def test_an_empty_list_is_handled():
    out = attribution_split([])
    assert out["system_trades"] == 0 and out["manual_trades"] == 0
