"""The MT4 bridge must send the stop it was given, and must never claim one it did not set.

THE DEFECT THESE TESTS PIN DOWN
-------------------------------
`MT4Service.place_market_order` accepted `stop_loss` and `take_profit`, echoed both back in its
result as though the broker had taken them, and built the DWX command with the stop fields hard-coded
to zero:

    command = f"TRADE;OPEN;{order_type};{symbol};0;0;0;{comment};{volume};123456"
                                                   ^ ^  SL and TP, always zero

On 1 October 2026 the gold 4H executor mirrored a live SELL onto MT4 through this path. MT4's own log
recorded `order was opened : #652199599 sell 0.01 XAUUSD at 4157.70 sl: 0.00 tp: 0.00`, while the
system's journal recorded the order as executed with a stop at 4188.54. The position sat unprotected
for hours and every report said it was covered.

WHAT THE PROTOCOL ACTUALLY WANTS, read from DWX_ZeroMQ_Server_v2.0.1_RC8.mq4 on this machine:

    TRADE;OPEN;TYPE;SYMBOL;PRICE;SL;TP;COMMENT;LOTS;MAGIC;TICKET

`DWX_OpenOrder` reads SL and TP with `StrToInteger` and applies them as `price - SL * dir * MODE_POINT`
- so they are POINT DISTANCES, not prices - and only when its `DMA_MODE` input is false. That input
ships `true` and is `true` on this terminal, which is why no stop could ever land at open time.
`DWX_ModifyOrder` applies the same point distances in BOTH modes, so the stop is set with a follow-up
MODIFY and then confirmed from the reply.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trading.mt4_service import MT4Service


XAU = {"symbol": "XAUUSD", "valid": True, "bid": 4157.70, "ask": 4157.95,
       "spread": 0.0001, "digits": 2, "point": 0.01}


class _Bridge(MT4Service):
    """A service wired to a recorded transcript instead of a socket."""

    def __init__(self, modify_reply="{'_action': 'EXECUTION', '_sl': 4188.54, '_tp': 4110.85}"):
        self.sent = []
        self.connected = True
        self.available = True
        self.account_number = 12755139
        self._modify_reply = modify_reply

    def check_symbol(self, symbol):
        return dict(XAU)

    def _send_command(self, message):
        self.sent.append(message)
        if message.startswith("TRADE;MODIFY"):
            return {"raw": self._modify_reply, "response": self._modify_reply}
        return {"raw": "{'_action': 'EXECUTION', 'ticket': 652199599}", "ticket": 652199599}


def _fields(command):
    return command.split(";")


def test_points_are_computed_from_the_symbols_own_point_size():
    assert MT4Service._points_from_price(4157.70, 4188.54, XAU) == 3084
    assert MT4Service._points_from_price(4157.70, 4110.85, XAU) == 4685


def test_a_missing_point_size_yields_no_stop_rather_than_a_guess():
    """DWX multiplies the number it is given by the symbol's point. A wrong point size does not make a
    slightly wrong stop - it makes one a hundred times too close or too far."""
    blind = {k: v for k, v in XAU.items() if k not in ("digits", "point")}
    assert MT4Service._points_from_price(4157.70, 4188.54, blind) is None
    assert MT4Service._points_from_price(4157.70, None, XAU) is None
    assert MT4Service._points_from_price(4157.70, 4157.70, XAU) is None   # zero distance is no stop


def test_the_open_command_carries_the_stop_the_target_and_the_real_magic():
    bridge = _Bridge()
    bridge.place_market_order("XAUUSD", "SELL", 0.01, stop_loss=4188.54,
                              take_profit=4110.85, comment="GOLD4H demo model", magic=440401)
    open_cmd = _fields(bridge.sent[0])
    assert open_cmd[0:2] == ["TRADE", "OPEN"]
    assert open_cmd[2] == "1" and open_cmd[3] == "XAUUSD"
    assert open_cmd[5] == "3084", "SL must be the point distance, not 0 and not a price"
    assert open_cmd[6] == "4685", "TP must be the point distance, not 0 and not a price"
    assert open_cmd[9] == "440401", "the strategy's magic, not the hard-coded 123456"


def test_the_stop_is_set_with_a_follow_up_modify_and_confirmed():
    """DMA_MODE is true on this terminal, so OPEN ignores the distances; MODIFY applies them anyway."""
    bridge = _Bridge()
    out = bridge.place_market_order("XAUUSD", "SELL", 0.01, stop_loss=4188.54,
                                    take_profit=4110.85, magic=440401)
    modify = _fields(bridge.sent[1])
    assert modify[0:2] == ["TRADE", "MODIFY"]
    assert modify[5] == "3084" and modify[6] == "4685"
    assert modify[10] == "652199599", "the ticket goes in field 10, where the EA reads it"
    assert out["executed"] is True and out["stops_applied"] is True
    assert out["unprotected"] is False
    assert out["stop_loss"] == 4188.54 and out["take_profit"] == 4110.85


@pytest.mark.parametrize("reply", [
    "{'_action': 'EXECUTION', '_response': '130', '_response_value': 'invalid stops'}",
    "",
    "ERROR",
])
def test_an_unset_stop_is_reported_as_unset_never_echoed_back(reply):
    """The whole point. If the broker did not take the stop, the caller must be told the position is
    naked - not handed its own request back as confirmation."""
    bridge = _Bridge(modify_reply=reply)
    out = bridge.place_market_order("XAUUSD", "SELL", 0.01, stop_loss=4188.54,
                                    take_profit=4110.85, magic=440401)
    assert out["executed"] is True
    assert out["stops_applied"] is False
    assert out["unprotected"] is True
    assert out["stop_loss"] is None and out["take_profit"] is None
    assert out["stop_loss_requested"] == 4188.54
    assert "WITHOUT STOPS" in out["message"]


def test_an_order_with_no_stop_requested_is_not_called_unprotected():
    bridge = _Bridge()
    out = bridge.place_market_order("XAUUSD", "BUY", 0.01, magic=440401)
    assert out["executed"] is True and out["unprotected"] is False
    assert len(bridge.sent) == 1, "no MODIFY when nothing was asked for"
