"""What counts as this system's trade, and what has to be said when reporting it.

On 24 September 2026 the owner was shown "the system is profitable: 100 trades, net +366.16". 94 of
those trades carried magic 903110. The owner confirmed 903110 IS the system's AI Auto Trader, so it
is counted - but it is ALSO the default value of mt5_service's ``magic`` parameter, so anything else
routed through that call lands on the same number. The figure is therefore reported with a caveat,
and the real repair is to give the auto trader a magic of its own.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import performance_analytics as pa


def test_the_ai_auto_trader_is_counted_as_the_systems():
    """The owner confirmed it is theirs; excluding it would understate what the system did."""
    assert 903110 in pa.SYSTEM_MAGICS


def test_but_it_carries_a_caveat_because_it_is_a_shared_default():
    assert 903110 in pa.SHARED_DEFAULT_MAGICS
    note = pa.SHARED_DEFAULT_MAGICS[903110].lower()
    assert "default" in note and "auto trader" in note


def test_the_caveat_is_true_of_the_code_it_describes():
    """If mt5_service stops defaulting to 903110 the caveat is obsolete and should be removed."""
    source = (Path(__file__).resolve().parents[1] / "trading" / "mt5_service.py").read_text(encoding="utf-8")
    assert "magic: int = 903110" in source


def test_the_strategies_own_magics_are_all_present():
    for magic in (440401, 440502, 440603, 440704, 440805):
        assert magic in pa.SYSTEM_MAGICS


def test_another_experts_magic_is_never_counted():
    """This account carries 34 distinct magics; these are the owner's other experts, not this system."""
    for foreign in (10002, 10003, 202503, 636363, 778899, 888888, 0):
        assert foreign not in pa.SYSTEM_MAGICS


def test_growth_counts_only_the_listed_magics():
    deals = [{"time": "2026-09-01T00:00:00Z", "magic": 440603, "net": 10.0, "symbol": "XAUUSD"},
             {"time": "2026-09-02T00:00:00Z", "magic": 888888, "net": 500.0, "symbol": "XAUUSD"}]
    totals = (pa.growth_tracker(deals, magic=list(pa.SYSTEM_MAGICS)) or {}).get("totals") or {}
    assert totals.get("trades") == 1
    assert abs(float(totals.get("net")) - 10.0) < 1e-6
