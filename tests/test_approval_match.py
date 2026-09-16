"""Execution safety item 3: an approval executes only the exact symbol, side and broker price band approved."""
import numpy as np
import pandas as pd

from src import execution_guard as guard


def _levels(side="BUY", bid=4285.10, ask=4285.40):
    rng = np.random.default_rng(5)
    close = 4285 + np.cumsum(rng.normal(0, 4, 60))
    bars = pd.DataFrame({"datetime": pd.date_range("2026-09-15", periods=60, freq="1h", tz="UTC"),
                         "open": close, "high": close + 6, "low": close - 6, "close": close})
    return guard.broker_trade_levels(symbol="XAUUSD", side=side, quote={"ok": True, "bid": bid, "ask": ask},
                                     quote_source="mt5:XAUUSD", bars=bars, bars_source="mt5:XAUUSD")


def _request(side="BUY"):
    return {"request_id": "r1", "symbol": "XAUUSD", "side": side, "status": "approved",
            "price_band": guard.approval_price_band(_levels(side))}


def test_band_is_built_from_broker_levels_and_exact_match_passes():
    request = _request()
    band = request["price_band"]
    assert band["available"] and band["low"] < band["entry"] < band["high"] and band["source"] == "mt5"
    result = guard.approval_matches(request, symbol="XAUUSD", side="BUY", broker_entry=band["entry"] + band["half_width"] / 2,
                                    price_source="mt5")
    assert result["ok"]


def test_side_symbol_price_and_feed_mismatches_are_rejected():
    request = _request()
    band = request["price_band"]
    assert "side" in guard.approval_matches(request, symbol="XAUUSD", side="SELL", broker_entry=band["entry"])["reason"]
    assert "symbol" in guard.approval_matches(request, symbol="BTCUSD", side="BUY", broker_entry=band["entry"])["reason"]
    outside = guard.approval_matches(request, symbol="XAUUSD", side="BUY", broker_entry=band["high"] + 0.01)
    assert not outside["ok"] and "outside the approved band" in outside["reason"]
    assert not guard.approval_matches(request, symbol="XAUUSD", side="BUY", broker_entry=band["entry"], price_source="mt4")["ok"]
    assert not guard.approval_matches(request, symbol="XAUUSD", side="BUY", broker_entry=None)["ok"]


def test_requests_without_a_broker_band_or_missing_are_rejected():
    legacy = {"request_id": "old", "symbol": "XAUUSD", "side": "BUY", "status": "approved"}
    assert "no broker price band" in guard.approval_matches(legacy, symbol="XAUUSD", side="BUY", broker_entry=4285.4)["reason"]
    assert not guard.approval_matches(None, symbol="XAUUSD", side="BUY", broker_entry=4285.4)["ok"]
    assert not guard.approval_price_band({"available": False, "reason": "MT5 is not connected"})["available"]
