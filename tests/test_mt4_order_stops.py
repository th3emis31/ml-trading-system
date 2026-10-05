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


# --------------------------------------------------------------------------------------------------
# Repairing a position that is ALREADY open unprotected. The narrowest possible tool: it can only add
# a missing level, never move one, never remove one, and never close anything.

_BOOK = ("{'_action': 'OPEN_TRADES', '_trades': {652199599: {'_magic': 123456, '_symbol': 'XAUUSD', "
         "'_lots': 0.01, '_type': 1, '_open_price': 4157.70, '_open_time': '2026.10.01 10:05:09', "
         "'_SL': 0.0, '_TP': 0.0, '_pnl': -12.89, '_comment': 'GOLD4H demo model'}}}")
_BOOK_PROTECTED = _BOOK.replace("'_SL': 0.0, '_TP': 0.0", "'_SL': 4188.54, '_TP': 4110.85")


class _Live(MT4Service):
    def __init__(self, book=_BOOK, after=None, modify_ok=True):
        self.sent = []
        self.connected = True
        self.available = True
        self.account_number = 12755139
        self._book, self._after, self._modify_ok = book, after or _BOOK_PROTECTED, modify_ok

    def check_symbol(self, symbol):
        return dict(XAU)

    def _send_command(self, message):
        self.sent.append(message)
        if message.startswith("TRADE;GET_OPEN_TRADES"):
            body = self._after if any(m.startswith("TRADE;MODIFY") for m in self.sent) else self._book
            return self._parse_response(body)
        if message.startswith("TRADE;MODIFY"):
            text = ("{'_action': 'EXECUTION', '_sl': 4188.54, '_tp': 4110.85}" if self._modify_ok
                    else "{'_action': 'EXECUTION', '_response': '130', '_response_value': 'invalid stops'}")
            return {"raw": text, "response": text}
        return {"raw": ""}


def test_open_trades_reads_the_mt4_book_that_was_never_asked_for():
    out = _Live().open_trades()
    assert out["ok"] is True and len(out["trades"]) == 1
    row = out["trades"][0]
    assert row["ticket"] == 652199599 and row["symbol"] == "XAUUSD" and row["side"] == "SELL"
    assert row["SL"] == 0.0, "this is the unprotected position the repair exists for"


def test_a_missing_stop_is_filled_in_and_confirmed_from_the_broker():
    live = _Live()
    out = live.set_stops(652199599, stop_loss=4188.54, take_profit=4110.85)
    modify = [m for m in live.sent if m.startswith("TRADE;MODIFY")][0].split(";")
    # distances are measured from the ORDER'S OWN open price (4157.70), not the other platform's
    assert modify[5] == "3084" and modify[6] == "4685" and modify[10] == "652199599"
    assert out["ok"] is True and out["changed"] is True
    assert out["stop_loss_before"] is None and out["stop_loss"] == 4188.54


def test_it_never_touches_a_stop_the_order_already_has():
    """So it cannot move or remove a level the owner or another expert set."""
    live = _Live(book=_BOOK_PROTECTED)
    out = live.set_stops(652199599, stop_loss=4000.0, take_profit=4300.0)
    assert out["changed"] is False and "left untouched" in out["message"]
    assert not any(m.startswith("TRADE;MODIFY") for m in live.sent), "no write was sent at all"


def test_it_refuses_a_ticket_that_is_not_open_rather_than_guessing():
    out = _Live().set_stops(111111, stop_loss=4188.54)
    assert out["ok"] is False and "not open" in out["message"]


def test_it_refuses_when_the_point_size_is_unknown():
    class _Blind(_Live):
        def check_symbol(self, symbol):
            return {k: v for k, v in XAU.items() if k not in ("digits", "point")}
    out = _Blind().set_stops(652199599, stop_loss=4188.54)
    assert out["ok"] is False and "point size" in out["message"]
    assert not any(m.startswith("TRADE;MODIFY") for m in _Blind().sent)


def test_a_refused_modify_is_reported_as_refused():
    out = _Live(after=_BOOK, modify_ok=False).set_stops(652199599, stop_loss=4188.54, take_profit=4110.85)
    assert out["ok"] is False and out["stop_loss"] is None
    assert out["stop_loss_before"] is None


def test_the_repair_can_only_modify_never_close():
    """CLOSE, CLOSE_ALL and CLOSE_MAGIC exist in the EA. Nothing in this path can reach them."""
    live = _Live()
    live.set_stops(652199599, stop_loss=4188.54, take_profit=4110.85)
    assert all(m.startswith("TRADE;GET_OPEN_TRADES") or m.startswith("TRADE;MODIFY") or m.startswith("RATES")
               for m in live.sent), live.sent
    assert not any("CLOSE" in m for m in live.sent)


# --------------------------------------------------------------------------------------------------
# Closing ONE ticket, by number. The last gap in the trading path: the mirror was opened and then
# abandoned, so an MT5 time exit left a live MT4 position with nothing managing it.

class _Closer(_Live):
    def __init__(self, book=None, after_close=None, **kw):
        super().__init__(book=book or _BOOK, **kw)
        self._after_close = after_close if after_close is not None else (
            "{'_action': 'OPEN_TRADES', '_trades': {}}")
        self._did_close = False

    def _send_command(self, message):
        self.sent.append(message)
        if message.startswith("TRADE;CLOSE"):
            self._did_close = True
            return {"raw": "{'_action': 'CLOSE', '_ticket': 652199599, '_response': 'CLOSE_MARKET', "
                           "'_response_value': 'SUCCESS'}"}
        if message.startswith("TRADE;GET_OPEN_TRADES"):
            return self._parse_response(self._after_close if self._did_close else self._book)
        return {"raw": ""}


def test_closing_a_ticket_sends_the_ticket_in_field_ten_and_confirms_it_is_gone():
    bridge = _Closer()
    out = bridge.close_ticket(652199599, expect_symbol="XAUUSD")
    close = [m for m in bridge.sent if m.startswith("TRADE;CLOSE")][0].split(";")
    assert close[0:2] == ["TRADE", "CLOSE"]
    assert close[10] == "652199599", "the EA reads the ticket from field 10"
    assert out["ok"] is True and out["closed"] is True and out["symbol"] == "XAUUSD"


def test_a_close_that_did_not_take_is_reported_as_not_closed():
    """If the position is still in the open book afterwards, say so. Never claim a close."""
    bridge = _Closer(after_close=_BOOK)          # still open after the attempt
    out = bridge.close_ticket(652199599, expect_symbol="XAUUSD")
    assert out["ok"] is False and out["closed"] is False
    assert "still open" in out["message"]


def test_a_ticket_that_is_not_open_is_not_an_error_and_sends_no_close():
    bridge = _Closer()
    out = bridge.close_ticket(111111, expect_symbol="XAUUSD")
    assert out["already_closed"] is True and out["closed"] is False
    assert not any(m.startswith("TRADE;CLOSE") for m in bridge.sent), "nothing may be sent"


def test_a_symbol_mismatch_refuses_rather_than_closing_the_wrong_position():
    """A stale ticket number must never close somebody else's trade."""
    bridge = _Closer()
    out = bridge.close_ticket(652199599, expect_symbol="BTCUSD")
    assert out["ok"] is False and "not BTCUSD" in out["message"]
    assert not any(m.startswith("TRADE;CLOSE") for m in bridge.sent)


def test_close_ticket_can_never_reach_the_bulk_close_commands():
    """CLOSE_ALL, CLOSE_MAGIC and CLOSE_PARTIAL exist in the EA. None is reachable from here."""
    bridge = _Closer()
    bridge.close_ticket(652199599, expect_symbol="XAUUSD")
    for message in bridge.sent:
        assert "CLOSE_ALL" not in message
        assert "CLOSE_MAGIC" not in message
        assert "CLOSE_PARTIAL" not in message


def test_a_disconnected_bridge_closes_nothing():
    bridge = _Closer()
    bridge.connected = False
    out = bridge.close_ticket(652199599)
    assert out["closed"] is False and "not connected" in out["message"]
    assert bridge.sent == []
