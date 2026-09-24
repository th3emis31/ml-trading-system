"""SmartEntry has its own magic now, and the change must not orphan what it already opened.

Until 24 September 2026 its orders carried 903110, which is ALSO the default value of mt5_service's
``magic`` parameter - so a dashboard button, a panel or a manual click landed on the same number and
was indistinguishable from SmartEntry's own work. 440906 belongs to SmartEntry alone.

The risk in the change is the one-trade-per-asset rule. If the guard looked only for the new magic it
would see nothing on a symbol that already holds a position under the old one and open a SECOND trade
on it - the fault that once put nine gold BUYs on the account in ninety minutes.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trading.mt5_service import MT5Service

SOURCE = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")


def test_smartentry_places_orders_under_its_own_magic():
    assert "AUTO_TRADE_MAGIC = 440906" in SOURCE
    assert "AUTO_TRADE_LEGACY_MAGICS = (903110,)" in SOURCE


def test_the_old_magic_is_still_read_so_open_trades_are_not_orphaned():
    assert "AUTO_TRADE_MAGICS = (AUTO_TRADE_MAGIC,) + AUTO_TRADE_LEGACY_MAGICS" in SOURCE


def test_every_position_lookup_covers_both_magics():
    """A lookup left on the single magic is the bug this whole change has to avoid."""
    assert "magic=AUTO_TRADE_MAGIC)" not in SOURCE, "a position lookup still sees only the new magic"
    assert SOURCE.count("magic=list(AUTO_TRADE_MAGICS))") >= 3


def test_positions_accepts_several_magics_and_filters_correctly():
    class _Pos:
        def __init__(self, magic, ticket):
            self._d = {"ticket": ticket, "symbol": "XAUUSD", "type": 0, "volume": 0.01,
                       "price_open": 1.0, "price_current": 1.0, "sl": 0.0, "tp": 0.0,
                       "profit": 0.0, "time": 0, "magic": magic, "comment": "SmartEntry"}
        def _asdict(self):
            return dict(self._d)

    class _MT5:
        def positions_get(self, symbol=None):
            return [_Pos(440906, 1), _Pos(903110, 2), _Pos(888888, 3)]

    service = MT5Service.__new__(MT5Service)
    service._mt5 = _MT5()
    service.status = lambda: {"connected": True}

    both = service.positions(symbol="XAUUSD", magic=[440906, 903110])
    assert sorted(p["ticket"] for p in both) == [1, 2], "must see the new AND the old magic"
    assert all(p["magic"] != 888888 for p in both), "another expert's position must never be included"

    one = service.positions(symbol="XAUUSD", magic=440906)
    assert [p["ticket"] for p in one] == [1], "a single magic must still work exactly as before"


def test_the_growth_record_counts_both_magics():
    from src import performance_analytics as pa
    assert 440906 in pa.SYSTEM_MAGICS and 903110 in pa.SYSTEM_MAGICS


def test_nothing_in_this_change_closes_a_trade():
    """The owner's standing rule: trades are left to close naturally."""
    for token in ("close_position", "order_close", "position_close"):
        assert token not in SOURCE[SOURCE.index("AUTO_TRADE_MAGIC = 440906"):][:3000]
