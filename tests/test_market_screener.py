"""The screener's value is in what it refuses to rank, so that is what these test.

Screening 823 stocks against a generic cost assumption would let the assumption decide the ranking
rather than the market - for XRPUSD the real round trip is 1.64 % and the generic one is sixteen
times too cheap. And a six-trade result with a 5.96 profit factor is an artefact, not a find.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import market_screener as ms


def _screen_row(symbol, trades, expectancy, factor=1.2):
    return ms.screen_row(symbol, {"trades": trades, "expectancy_pct": expectancy,
                                  "profit_factor": factor}, {"round_trip_pct": 0.0001, "p90_pct": 0.005},
                         bars=5000, years=8.0)


def test_a_result_under_the_evidence_minimum_is_not_ranked():
    """XRPUSD's six trades produced the best-looking numbers of five markets and meant the least."""
    thin = _screen_row("XRPUSD", 6, 6.05, factor=5.96)
    assert thin["evidence"] is False
    assert "under the 100-trade minimum" in thin["evidence_note"]
    strong = _screen_row("NAS100", 185, 0.19)
    assert strong["evidence"] is True and strong["evidence_note"] is None


def test_thin_results_sort_behind_every_real_one_however_good_they_look():
    rows = [_screen_row("XRPUSD", 6, 6.05, factor=5.96), _screen_row("NAS100", 185, 0.19), _screen_row("BTCUSD", 196, 0.35)]
    ranked = sorted(rows, key=ms.rank_key)
    assert [r["symbol"] for r in ranked] == ["BTCUSD", "NAS100", "XRPUSD"]
    assert ranked[-1]["symbol"] == "XRPUSD", "the flattering six-trade row must sort last"


def test_ranking_uses_expectancy_not_total_return():
    """Total return rewards whichever symbol trended hardest in the window; expectancy is per trade
    and comparable between markets."""
    patient = _screen_row("A", 120, 0.90)
    busy = _screen_row("B", 900, 0.10)
    assert [r["symbol"] for r in sorted([busy, patient], key=ms.rank_key)] == ["A", "B"]


def test_every_ranked_row_carries_its_own_measured_cost():
    row = _screen_row("NAS100", 185, 0.19)
    assert row["cost_measured"] is True
    assert row["round_trip_pct"] is not None and row["spread_p90_pct"] is not None


def test_the_screener_reads_bars_and_never_trades():
    source = Path(ms.__file__).read_text(encoding="utf-8")
    for forbidden in ("order_send", "place_market_order", "OrderSend", "TRADE;OPEN"):
        assert forbidden not in source


def test_h4_is_the_default_timeframe():
    """Daily bars make the stop-before-target resolution ambiguous far more often, which
    systematically understates, and yield about a sixth of the trades."""
    source = Path(ms.__file__).read_text(encoding="utf-8")
    assert "TIMEFRAME_H4" in source and "TIMEFRAME_D1" not in source


def test_the_spread_sample_stays_under_the_terminal_cap():
    """100,000 is the cap and MT5 returns nothing at it rather than erroring - a silent empty result
    that reads as "no data" and would have every symbol skipped for the wrong reason."""
    assert ms.SPREAD_SAMPLE_BARS < 100_000
