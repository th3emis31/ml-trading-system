"""The ledger's entry price must be the price the execution engine actually filled at.

The defect this pins down: `engine/loop.py` recorded the SIGNAL bar's close as the entry price while
`strategy_lab.simulate_orders` fills at the NEXT bar's open. On gold 4h those differed by up to 10.04
points — a trade whose stop was 8.3 points away was being audited against a price it never traded at, so
every slippage figure, every MAE/MFE read off the ledger and every reconstruction of a decision was wrong
by more than one unit of risk.

These tests are deliberately built on a hand-made frame with the answer known in advance, so they pass or
fail on arithmetic rather than on whatever the market did. `test_engine_truth.py` proves the simulator's
fill rule itself; this proves the LEDGER agrees with it.
"""
import numpy as np
import pandas as pd
import pytest

from engine import ledger as ledger_mod, loop
from src import strategy_lab as lab


def _frame(n: int = 40) -> pd.DataFrame:
    """Opens deliberately gapped away from the prior close, so close[i] != open[i+1] on every bar.

    If the two were equal the test would pass under the defect as well, and prove nothing.
    """
    close = 4000.0 + np.arange(n, dtype=float)
    open_ = close - 0.5 + np.arange(n, dtype=float) * 0.25      # a widening gap from the prior close
    return pd.DataFrame({
        "datetime": pd.date_range("2026-01-01", periods=n, freq="4h", tz="UTC"),
        "open": open_, "high": np.maximum(open_, close) + 3.0,
        "low": np.minimum(open_, close) - 3.0, "close": close,
        "volume": np.full(n, 100.0),
    })


def _trades_and_ledger(frame: pd.DataFrame):
    """One long signal per third bar, simulated, with a ledger written the way the loop writes it."""
    n = len(frame)
    side = np.zeros(n, dtype=int)
    side[5:n - 2:3] = 1
    stop = frame["close"].to_numpy(dtype=float) - 20.0
    target = frame["close"].to_numpy(dtype=float) + 40.0
    rows = np.arange(n)

    trades = lab.simulate_orders(
        frame["open"].to_numpy(dtype=float), frame["high"].to_numpy(dtype=float),
        frame["low"].to_numpy(dtype=float), frame["close"].to_numpy(dtype=float),
        np.full(n, 5.0), frame["datetime"], side, stop, target, rows,
        exits={}, cost_pct=0.0)

    led = ledger_mod.Ledger()
    open_px = frame["open"].to_numpy(dtype=float)
    for i in range(n):
        if side[i] == 0:
            continue
        fill = float(open_px[i + 1]) if i + 1 < n else None
        led.entry(bar=i, ts=str(frame["datetime"].iloc[i]), symbol="XAUUSD", side="BUY",
                  price=fill, stop=float(stop[i]), target=float(target[i]), reason="test")
    return trades, led


def test_the_fixture_actually_distinguishes_the_two_prices():
    """Guard the guard: a frame where close[i] == open[i+1] would let the defect pass unnoticed."""
    frame = _frame()
    close, open_ = frame["close"].to_numpy(), frame["open"].to_numpy()
    gaps = np.abs(open_[1:] - close[:-1])
    assert gaps.min() > 0.1, "every bar must open away from the prior close for this test to mean anything"


def test_ledger_entry_price_equals_the_simulator_fill_price():
    """The assertion the owner asked for: ledger.entry_price == execution.fill_price, every trade."""
    frame = _frame()
    trades, led = _trades_and_ledger(frame)
    assert len(trades) > 0, "no trades means nothing was proved"

    check = loop.reconcile_entry_prices(led, trades)
    assert check["checked"] == len(trades), "every trade must be matched to a ledger row"
    assert check["mismatches"] == 0, check["worst"]
    assert check["unmatched_trade_bars"] == []
    assert check["agrees"] is True


def test_recording_the_signal_close_instead_would_be_caught():
    """The defect itself, re-injected. If this test ever passes silently the guard is not working."""
    frame = _frame()
    trades, _ = _trades_and_ledger(frame)
    close = frame["close"].to_numpy(dtype=float)

    defective = ledger_mod.Ledger()
    for t in trades:
        i = int(t["entry_idx"])
        defective.entry(bar=i, ts="t", symbol="XAUUSD", side="BUY", price=float(close[i]), reason="test")

    check = loop.reconcile_entry_prices(defective, trades)
    assert check["agrees"] is False
    assert check["mismatches"] == len(trades), "the old behaviour was wrong on EVERY trade, not some"


def test_a_limit_entry_that_fills_away_from_the_next_open_is_caught():
    """Why the reconciliation exists at all, rather than trusting the loop's own derivation.

    A builder returning resting limit entries fills somewhere other than the next bar's open. The loop's
    `open[i + 1]` would then be quietly wrong, and only a check against the simulator finds it.
    """
    frame = _frame()
    n = len(frame)
    side = np.zeros(n, dtype=int)
    side[5:n - 2:3] = 1
    close = frame["close"].to_numpy(dtype=float)
    stop, target = close - 20.0, close + 40.0
    limits = np.full(n, np.nan)
    for i in np.flatnonzero(side):
        limits[i] = float(frame["low"].iloc[i + 1]) + 0.05     # inside the next bar's range, not its open

    trades = lab.simulate_orders(
        frame["open"].to_numpy(dtype=float), frame["high"].to_numpy(dtype=float),
        frame["low"].to_numpy(dtype=float), close, np.full(n, 5.0), frame["datetime"],
        side, stop, target, np.arange(n), exits={}, cost_pct=0.0, entry_prices=limits)
    assert trades, "the limit orders must actually fill for this to test anything"

    open_px = frame["open"].to_numpy(dtype=float)
    led = ledger_mod.Ledger()
    for t in trades:
        i = int(t["entry_idx"])
        led.entry(bar=i, ts="t", symbol="XAUUSD", side="BUY", price=float(open_px[i + 1]), reason="test")

    assert loop.reconcile_entry_prices(led, trades)["agrees"] is False, \
        "the ledger must not be allowed to report the next open when the fill was a limit"


def test_a_trade_with_no_ledger_row_is_reported_rather_than_ignored():
    """Silence is the failure mode: an unmatched trade must not count as agreement."""
    frame = _frame()
    trades, _ = _trades_and_ledger(frame)
    check = loop.reconcile_entry_prices(ledger_mod.Ledger(), trades)
    assert check["checked"] == 0 and check["agrees"] is False
    assert check["unmatched_trade_bars"], "the bars with no ledger row must be named"


@pytest.mark.parametrize("gap,expect_agrees", [(0.0004, True), (0.002, False)])
def test_the_tolerance_covers_rounding_and_nothing_more(gap, expect_agrees):
    """The trade record rounds to 3 decimals, so 0.0005 is rounding; 0.002 is a disagreement."""
    trades = [{"entry_idx": 7, "entry_price": 4000.0}]
    led = ledger_mod.Ledger()
    led.entry(bar=7, ts="t", price=4000.0 + gap, reason="test")
    assert loop.reconcile_entry_prices(led, trades)["agrees"] is expect_agrees
