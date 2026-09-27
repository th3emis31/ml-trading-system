"""Measuring the owner's own trades has to be right, because a wrong reading here misdirects everything.

This is the module that corrected a real mistake: describing chart screenshots instead of the account's
closed trades. The tests guard the two things that make it worth more than the screenshots - matching each
entry to the bar it actually fell in, and signing the features by the trade's own direction so buys and
sells do not cancel out.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.manual_edge import bar_features, describe_entry, load_manual_trades, study


def bars(n=400, start=4000.0, step=-1.0):
    close = start + np.arange(n) * step
    frame = pd.DataFrame({"open": close - step, "close": close,
                          "high": close + 2.0, "low": close - 2.0, "volume": 100.0})
    frame["datetime"] = pd.date_range("2026-06-01", periods=n, freq="h", tz="UTC")
    return frame


def trade(opened, direction="SELL", net=10.0, symbol="XAUUSD", owner="manual", held_h=2):
    return {"symbol": symbol, "direction": direction, "volume": 0.02, "net": net,
            "opened_at": int(pd.Timestamp(opened, tz="UTC").timestamp()),
            "time": int(pd.Timestamp(opened, tz="UTC").timestamp()) + held_h * 3600,
            "entry_price": 4000.0, "price": 3990.0,
            "attribution": {"owner": owner, "confidence": "certain", "why": "fixture"}}


# --- only the owner's trades ----------------------------------------------------------------------

def test_the_systems_own_trades_are_excluded(tmp_path):
    payload = {"trades": [trade("2026-06-05 10:00", owner="manual"),
                          trade("2026-06-05 11:00", owner="system")]}
    path = tmp_path / "t.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    rows = load_manual_trades(str(path))
    assert len(rows) == 1 and rows[0]["attribution"]["owner"] == "manual"


# --- the entry lands on the right bar ---------------------------------------------------------------

def test_an_entry_is_matched_to_the_bar_it_fell_inside(tmp_path, monkeypatch):
    frame = bars()
    monkeypatch.setattr("src.mtf_data.load_bars", lambda *a, **k: frame)
    # 10:30 falls inside the 10:00 bar, not the 11:00 one.
    payload = {"trades": [trade("2026-06-05 10:30")]}
    path = tmp_path / "t.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    out = study("XAUUSD", "1h", str(path))
    assert out["available"] and out["trades"] == 1
    assert out["rows"][0]["entered"].endswith("10:30")


def test_a_trade_before_the_bars_start_is_skipped(tmp_path, monkeypatch):
    frame = bars()
    monkeypatch.setattr("src.mtf_data.load_bars", lambda *a, **k: frame)
    payload = {"trades": [trade("2020-01-01 10:00")]}
    path = tmp_path / "t.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    out = study("XAUUSD", "1h", str(path))
    assert out["available"] is False


def test_another_symbols_trades_are_not_read_against_gold_bars(tmp_path, monkeypatch):
    frame = bars()
    monkeypatch.setattr("src.mtf_data.load_bars", lambda *a, **k: frame)
    payload = {"trades": [trade("2026-06-05 10:00", symbol="BTCUSD")]}
    path = tmp_path / "t.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert study("XAUUSD", "1h", str(path))["available"] is False


# --- the features are signed by the trade's direction -------------------------------------------------

def test_features_are_signed_so_buys_and_sells_do_not_cancel():
    """Unsigned, "1 ATR above the EMA50" means the opposite thing for a buy and a sell, and pooling them
    averages a real split into nothing."""
    frame = bars(step=-1.0)                       # a falling market
    features = bar_features(frame)
    i = 300
    sell = describe_entry(features, i, "SELL")
    buy = describe_entry(features, i, "BUY")
    assert sell["atr_from_slow_ema"] == pytest.approx(-buy["atr_from_slow_ema"], abs=1e-6)
    assert sell["with_sar"] != buy["with_sar"]


def test_rsi_is_reported_both_raw_and_signed_toward_the_trade():
    frame = bars()
    features = bar_features(frame)
    out = describe_entry(features, 300, "SELL")
    assert out["rsi"] is not None and out["rsi_with_trade"] is not None
    assert out["rsi_with_trade"] == pytest.approx(100 - out["rsi"], abs=0.2)


def test_a_bar_with_no_usable_atr_is_skipped():
    frame = bars(n=5)
    features = bar_features(frame)
    features["atr"][0] = np.nan
    assert describe_entry(features, 0, "BUY") == {}


# --- the split ---------------------------------------------------------------------------------------

def test_winners_and_losers_are_split_and_counted(tmp_path, monkeypatch):
    frame = bars()
    monkeypatch.setattr("src.mtf_data.load_bars", lambda *a, **k: frame)
    payload = {"trades": [trade("2026-06-05 10:00", net=20.0), trade("2026-06-05 12:00", net=15.0),
                          trade("2026-06-05 14:00", net=-8.0)]}
    path = tmp_path / "t.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    out = study("XAUUSD", "1h", str(path))
    assert out["trades"] == 3 and out["winners"] == 2 and out["losers"] == 1
    assert out["net_winners"] == pytest.approx(35.0)
    assert out["net_losers"] == pytest.approx(-8.0)
    assert out["places_orders"] is False


def test_every_feature_reports_winners_losers_and_all(tmp_path, monkeypatch):
    frame = bars()
    monkeypatch.setattr("src.mtf_data.load_bars", lambda *a, **k: frame)
    payload = {"trades": [trade("2026-06-05 10:00", net=20.0), trade("2026-06-05 14:00", net=-8.0)]}
    path = tmp_path / "t.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    out = study("XAUUSD", "1h", str(path))
    for key, values in out["features"].items():
        assert set(values) == {"winners", "losers", "all"}, key
