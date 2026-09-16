"""Execution safety item 2: entry, stop and targets must all come from the same broker feed, never from Yahoo."""
import numpy as np
import pandas as pd

from src import execution_guard as guard


def _broker_bars(n=60, price=4285.0):
    rng = np.random.default_rng(3)
    close = price + np.cumsum(rng.normal(0, 4, n))
    return pd.DataFrame({"datetime": pd.date_range("2026-09-15", periods=n, freq="1h", tz="UTC"),
                         "open": close, "high": close + 6, "low": close - 6, "close": close})


QUOTE = {"ok": True, "bid": 4285.10, "ask": 4285.40}


def test_levels_use_the_broker_fill_price_and_one_feed():
    buy = guard.broker_trade_levels(symbol="XAUUSD", side="BUY", quote=QUOTE, quote_source="mt5:XAUUSD",
                                    bars=_broker_bars(), bars_source="mt5:XAUUSD")
    sell = guard.broker_trade_levels(symbol="XAUUSD", side="SELL", quote=QUOTE, quote_source="mt5:XAUUSD",
                                     bars=_broker_bars(), bars_source="mt5:XAUUSD")
    assert buy["available"] and buy["entry"] == 4285.40 and sell["entry"] == 4285.10
    assert buy["stop_loss"] < buy["entry"] < buy["take_profit_1"] < buy["take_profit_2"] < buy["take_profit_3"]
    assert sell["stop_loss"] > sell["entry"] > sell["take_profit_1"]
    assert guard.levels_share_source(buy) and guard.levels_share_source(sell)


def test_yahoo_or_mixed_feeds_are_refused():
    yahoo_bars = guard.broker_trade_levels(symbol="XAUUSD", side="BUY", quote=QUOTE, quote_source="mt5:XAUUSD",
                                           bars=_broker_bars(), bars_source="yahoo:1h")
    yahoo_quote = guard.broker_trade_levels(symbol="XAUUSD", side="BUY", quote=QUOTE, quote_source="yahoo:GC=F",
                                            bars=_broker_bars(), bars_source="yahoo:1h")
    mt4_mt5 = guard.broker_trade_levels(symbol="XAUUSD", side="BUY", quote=QUOTE, quote_source="mt4:XAUUSD",
                                        bars=_broker_bars(), bars_source="mt5:XAUUSD")
    assert not yahoo_bars["available"] and not yahoo_quote["available"] and not mt4_mt5["available"]
    assert not guard.broker_trade_levels(symbol="XAUUSD", side="HOLD", quote=QUOTE, quote_source="mt5:XAUUSD",
                                         bars=_broker_bars(), bars_source="mt5:XAUUSD")["available"]


def test_source_check_fails_when_entry_sl_and_tp_come_from_different_sources():
    good = guard.broker_trade_levels(symbol="XAUUSD", side="BUY", quote=QUOTE, quote_source="mt5:XAUUSD",
                                     bars=_broker_bars(), bars_source="mt5:XAUUSD")
    mixed_entry = dict(good, sources=dict(good["sources"], entry="yahoo:GC=F:quote"))
    mixed_stop = dict(good, sources=dict(good["sources"], stop_loss="mt5:XAUUSD:tick+yahoo:1h:atr14"))
    missing_tp = dict(good, take_profit_1=None)
    assert guard.levels_share_source(good)
    assert not guard.levels_share_source(mixed_entry)
    assert not guard.levels_share_source(mixed_stop)
    assert not guard.levels_share_source(missing_tp)
