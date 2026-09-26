"""Describing a turn is only useful if the instruments are right, so the two new ones get pinned.

Parabolic SAR and Wilder's RSI are written from scratch here - nothing in this project had either - and
they are read off the owner's own charts, so an error would make me describe their trades wrongly and
then reason from it.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.turn_anatomy import (LEAD_CANDLES, anatomy, describe_turn, find_turn, parabolic_sar,
                              wilder_rsi)


def frame_from(prices, spread=1.0):
    data = pd.DataFrame({"open": prices, "close": prices,
                         "high": [p + spread for p in prices], "low": [p - spread for p in prices]})
    data["volume"] = 100.0
    data["datetime"] = pd.date_range("2026-01-01", periods=len(prices), freq="h", tz="UTC")
    return data


# --- Parabolic SAR --------------------------------------------------------------------------------

def test_sar_sits_below_price_in_an_uptrend_and_above_in_a_downtrend():
    rising = list(np.linspace(100, 200, 80))
    frame = frame_from(rising)
    sar, up = parabolic_sar(frame["high"], frame["low"])
    assert up[-1] and sar[-1] < frame["close"].iloc[-1], "a clean uptrend must end with SAR below price"

    frame = frame_from(rising[::-1])
    sar, up = parabolic_sar(frame["high"], frame["low"])
    assert not up[-1] and sar[-1] > frame["close"].iloc[-1]


def test_sar_flips_when_price_penetrates_it():
    prices = list(np.linspace(100, 160, 50)) + list(np.linspace(160, 100, 50))
    frame = frame_from(prices)
    _sar, up = parabolic_sar(frame["high"], frame["low"])
    flips = np.flatnonzero(up[1:] != up[:-1]) + 1
    assert len(flips) >= 1, "a market that rises then falls must flip the SAR at least once"
    assert any(40 < int(f) < 75 for f in flips), f"the flip should be near the turn, got {flips.tolist()}"


def test_sar_never_moves_against_the_trend_while_it_holds():
    """Wilder's rule: in an uptrend SAR only rises. A SAR that fell would loosen a live stop."""
    frame = frame_from(list(np.linspace(100, 200, 120)))
    sar, up = parabolic_sar(frame["high"], frame["low"])
    run = sar[10:][up[10:]]
    assert np.all(np.diff(run) >= -1e-9), "SAR stepped backwards during an uptrend"


def test_sar_handles_too_few_bars():
    sar, up = parabolic_sar([1.0, 2.0], [0.5, 1.5])
    assert np.isnan(sar).all() or len(sar) == 2


# --- RSI ------------------------------------------------------------------------------------------

def test_rsi_is_100_when_every_bar_rises_and_0_when_every_bar_falls():
    up = wilder_rsi(list(np.linspace(100, 200, 60)), length=12)
    down = wilder_rsi(list(np.linspace(200, 100, 60)), length=12)
    assert up[-1] == pytest.approx(100.0, abs=0.01)
    assert down[-1] == pytest.approx(0.0, abs=0.01)


def test_rsi_sits_near_fifty_when_gains_and_losses_balance():
    prices = [100 + (1 if i % 2 else -1) for i in range(80)]
    values = wilder_rsi(prices, length=12)
    assert 40 <= values[-1] <= 60


def test_rsi_has_no_value_before_it_has_enough_bars():
    values = wilder_rsi(list(range(20)), length=12)
    assert np.isnan(values[:12]).all() and np.isfinite(values[12])


# --- finding and describing the turn ----------------------------------------------------------------

def test_a_window_that_ends_higher_turns_at_its_low():
    frame = frame_from(list(np.linspace(120, 100, 30)) + list(np.linspace(100, 140, 30)))
    turn = find_turn(frame, frame["datetime"].iloc[0], frame["datetime"].iloc[-1])
    assert turn is not None and turn.kind == "low"
    assert turn.index == pytest.approx(29, abs=2)


def test_a_window_that_ends_lower_turns_at_its_high():
    frame = frame_from(list(np.linspace(100, 140, 30)) + list(np.linspace(140, 90, 30)))
    turn = find_turn(frame, frame["datetime"].iloc[0], frame["datetime"].iloc[-1])
    assert turn is not None and turn.kind == "high"


def test_the_description_covers_the_lead_candles_and_marks_the_turn():
    frame = frame_from(list(np.linspace(120, 100, 40)) + list(np.linspace(100, 140, 40)))
    turn = find_turn(frame, frame["datetime"].iloc[0], frame["datetime"].iloc[-1])
    out = describe_turn(frame, turn, lead=LEAD_CANDLES)
    assert len(out["candles"]) == LEAD_CANDLES + 1
    assert sum(1 for candle in out["candles"] if candle["at_turn"]) == 1
    assert out["candles"][-1]["at_turn"] is True, "the turn must be the LAST candle described"
    assert out["places_orders"] is False


def test_a_turn_that_took_out_the_prior_low_is_reported_as_a_sweep():
    prices = list(np.linspace(120, 105, 25)) + [104, 103, 102] + [95] + list(np.linspace(97, 130, 20))
    frame = frame_from(prices, spread=0.5)
    turn = find_turn(frame, frame["datetime"].iloc[0], frame["datetime"].iloc[-1])
    out = describe_turn(frame, turn)
    assert out["swept_prior_20_bar_extreme"] is True


def test_an_empty_window_is_reported_rather_than_crashing():
    frame = frame_from(list(np.linspace(100, 110, 20)))
    assert find_turn(frame, "2030-01-01", "2030-01-02") is None


def test_anatomy_reports_when_there_are_no_bars(monkeypatch):
    from src import turn_anatomy

    monkeypatch.setattr("src.mtf_data.load_bars", lambda *a, **k: pd.DataFrame())
    out = turn_anatomy.anatomy("XAUUSD", "1h", [("2026-01-01", "2026-01-02")])
    assert out["available"] is False and "no broker bars" in out["reason"]
