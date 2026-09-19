"""The MT5 port of Aurum Flow: fidelity to the MQL4 source, and the licence check staying put.

There is no Python to exercise here — the compiler is the real test, and the port builds with
0 errors, 0 warnings. What these guard is the two ways the file could quietly go wrong later:
someone stripping the vendor's licence check (which would turn a translation into a crack), and
someone removing the warning about the martingale so a reader believes it is safer than it is.
"""
from pathlib import Path

import pytest

PORT = Path(__file__).resolve().parents[1] / "strategies" / "mt5" / "AurumFlow_MT5.mq5"


@pytest.fixture(scope="module")
def source() -> str:
    assert PORT.exists(), f"{PORT} is missing"
    return PORT.read_text(encoding="utf-8", errors="replace")


def test_the_licence_check_is_still_there(source):
    """Removing this would make the port a way round the vendor's licensing, not a translation."""
    assert "tradesmartfxtools.in/LicenseKey/aurum-flow-lite.php" in source
    assert "CheckAccountWithServer" in source
    assert "WebRequest" in source
    assert "GraceAllowed" in source


def test_a_failed_licence_check_still_blocks_the_expert(source):
    """OnInit must refuse to start, and OnTick must refuse to trade, exactly as the original."""
    assert source.count("INIT_FAILED") >= 3
    assert "g_licenseValid = false" in source
    assert "LICENSE EXPIRED - EA BLOCKED" in source


def test_the_licence_check_is_not_short_circuited(source):
    """A sneaky bypass would be initialising the flag to true, or returning early."""
    assert "bool     g_licenseValid     = false;" in source
    assert "g_licenseValid = true;\n   return" not in source


def test_the_martingale_warning_survives(source):
    """The cascade is the real risk in this expert. The header must keep saying so."""
    header = source[:source.index("#property copyright")]
    for phrase in ("martingale", "NO stop loss", "Recovery2MaxTrades = 60"):
        assert phrase in header, f"the header must still explain {phrase!r}"


def test_the_measured_result_is_stated_in_the_file(source):
    """Whoever opens this next should see the test result without having to go looking."""
    header = source[:source.index("#property copyright")]
    assert "PF 0.791" in header and "PF 0.747" in header
    assert "no directional edge" in header


def test_every_input_from_the_mql4_source_is_present(source):
    """37 real inputs plus the 8 GUI section separators, same names, nothing dropped."""
    expected = [
        "StructureDepth", "StructureSpacing", "StructureRefreshBars",
        "Lots", "SL", "TP", "Slippage", "MagicNumber", "UseBodyInsteadOfRange",
        "UseZoneStopLoss", "ZoneSLBuffer",
        "EnableFloatingLossCooldown", "FloatingLossCooldownTrigger", "FloatingLossCooldownHours",
        "EnableMaxTradeDuration", "MaxTradeDurationDays",
        "EnableSpreadFilter", "MinSpreadPoints", "MaxSpreadPoints",
        "EnableRecoveryMode2", "EnableMAFilter", "MAPeriod", "MAShift", "MAPrice",
        "OnlyOneTradeAtATime", "LosingStreakTrigger", "StreakPauseMinutes",
        "TradeInNovember", "TradeInDecember", "MaxLot",
        "UsePendingOrders", "PendingOrderDistance", "PendingExpiryMinutes",
        "SafetyZoneFilter", "SafetyZoneBars", "CooldownMinutes", "EnableMultiDealBreakeven",
    ]
    declared = [line.split("=")[0].split()[-1] for line in source.splitlines()
                if line.startswith("input ")]
    missing = [name for name in expected if name not in declared]
    assert not missing, f"inputs dropped in the port: {missing}"
    assert len(declared) == len(expected) + 8, f"expected {len(expected) + 8} input lines, got {len(declared)}"


def test_the_shipped_defaults_are_unchanged(source):
    """'Same settings, everything same' — a silent default change would be worse than the risk."""
    for declaration in ("input double Lots            = 0.01;",
                        "input int    SL              = 2100;",
                        "input int    TP              = 1800;",
                        "input bool EnableRecoveryMode2 = true;",
                        "input int    MAPeriod       = 600;",
                        "input int  PendingOrderDistance = 130;",
                        "input int  PendingExpiryMinutes = 60;",
                        "input bool TradeInNovember = false;",
                        "input bool TradeInDecember = false;",
                        "input double FloatingLossCooldownTrigger = -100.0;"):
        assert declaration in source, f"default changed: {declaration}"


def test_the_octal_magic_number_is_carried_over_as_its_real_value(source):
    """MT4 read 060701111 as octal, so it was really 12812873. Both platforms share one magic."""
    assert "input long   MagicNumber     = 12812873;" in source
    # The octal literal may still be mentioned in the comment that explains it, but never assigned.
    assert "= 060701111" not in source


def test_the_recovery_trades_still_carry_no_stop_or_target(source):
    """Faithful to the original, and commented so nobody mistakes it for an oversight."""
    assert "no SL, no TP, as the original" in source


def test_the_port_differences_are_each_marked(source):
    """Every place MQL5 forced a different API is annotated, so the diff is auditable."""
    assert source.count("PORT:") >= 15


def test_the_mql5_builtin_name_is_not_shadowed(source):
    """The original defined its own ArrayRemove, which is a built-in in MQL5."""
    assert "void RemovePendingExpiryAt(" in source
    assert "void ArrayRemove(" not in source
