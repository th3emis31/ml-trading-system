import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import pytest

from src import demo_executor as de

DEMO_ACCOUNT = {"login": 11581419, "server": "VantageMarkets-Demo", "trade_mode": 0}
NOW = pd.Timestamp("2026-09-14 01:05", tz="UTC")


class FakeEngine:
    def __init__(self, account=DEMO_ACCOUNT, connected=True, positions=None, quote=None, executed=True):
        self.account = account
        self.connected = connected
        self.open_positions = list(positions or [])
        self.quote_value = quote or {"ok": True, "bid": 4350.00, "ask": 4350.30, "digits": 2}
        self.executed = executed
        self.orders = []
        self.closed = []

    def status(self):
        return {"connected": self.connected}

    def account_info(self):
        return {"account": self.account}

    def positions(self, symbol=None, magic=None):
        return [p for p in self.open_positions if magic is None or p.get("magic") == magic]

    def quote(self, symbol):
        return self.quote_value

    def place_market_order(self, **request):
        self.orders.append(request)
        if not self.executed:
            return {"executed": False, "message": "rejected", "result": {"retcode": 10016}}
        self.open_positions.append({"ticket": 555, "magic": request["magic"], "profit": 0.0})
        return {"executed": True, "message": "done", "result": {"order": 555}, "request": request}

    def close_position(self, ticket, comment=""):
        self.closed.append(ticket)
        self.open_positions = [p for p in self.open_positions if p["ticket"] != ticket]
        return {"executed": True, "message": "closed"}


def _signal(**overrides):
    # Bar 2026-09-13 21:00 closes at 2026-09-14 01:00, five minutes before NOW.
    return {"symbol": "XAUUSD", "side": "BUY", "bar_time": "2026-09-13 21:00", "bar_minutes": 240, "horizon_bars": 6,
            "atr": 20.0, "sl_atr": 1.0, "tp_atr": 1.5, "p_win": 0.5, "ev_r": 0.3, "threshold_r": 0.2, **overrides}


def _config(**overrides):
    return {**de.DEFAULT_CONFIG, "enabled": True, "dry_run": False, "account_login": 11581419, **overrides}


def test_disabled_config_never_trades():
    engine, journal = FakeEngine(), {}
    event = de.execute_signal(engine, _signal(), _config(enabled=False), journal, NOW)
    assert event["event"] == "skipped" and engine.orders == []


@pytest.mark.parametrize("account", [
    {"login": 11581419, "server": "VantageMarkets-Live", "trade_mode": 2},
    {"login": 11581419, "server": "VantageMarkets-Demo", "trade_mode": 2},
    {"login": 99999, "server": "Other-Demo", "trade_mode": 0},
    None,
])
def test_refuses_anything_but_the_configured_demo_account(account):
    engine, journal = FakeEngine(account=account), {}
    event = de.execute_signal(engine, _signal(), _config(), journal, NOW)
    assert event["event"] == "refused" and engine.orders == []


@pytest.mark.parametrize("signal", [
    _signal(side="NO_TRADE"), _signal(side="HOLD"), _signal(side=""), _signal(symbol="BTCUSD"),
    _signal(bar_time="2026-09-13 17:00"),  # closed 4h05m ago: stale
    _signal(bar_time="2026-09-14 01:00"),  # still forming
    _signal(atr=0.0),
])
def test_refuses_untradeable_or_stale_signals(signal):
    engine, journal = FakeEngine(), {}
    event = de.execute_signal(engine, signal, _config(), journal, NOW)
    assert event["event"] == "refused" and engine.orders == []


def test_dry_run_logs_the_order_without_sending_it():
    engine, journal = FakeEngine(), {}
    event = de.execute_signal(engine, _signal(), _config(dry_run=True), journal, NOW)
    assert event["event"] == "dry_run" and engine.orders == []
    assert journal["attempts"]["2026-09-13 21:00"]["status"] == "dry_run"


def test_opens_one_demo_position_with_stops_magic_and_small_volume():
    engine, journal = FakeEngine(), {}
    event = de.execute_signal(engine, _signal(), _config(volume=5.0), journal, NOW)
    assert event["event"] == "opened" and len(engine.orders) == 1
    order = engine.orders[0]
    assert order["side"] == "BUY" and order["volume"] == 0.10  # capped by max_volume
    assert order["stop_loss"] == round(4350.30 - 20.0, 2) and order["take_profit"] == round(4350.30 + 30.0, 2)
    assert order["magic"] == de.DEFAULT_CONFIG["magic"] and order["allow_retry_without_stops"] is False
    assert event["expires_at"] == "2026-09-15 01:00:00"  # close of the 6th bar after the signal bar
    # The same signal again, or any signal while the position is open, is refused.
    again = de.execute_signal(engine, _signal(), _config(), journal, NOW)
    assert again["event"] == "refused" and "already attempted" in again["reason"]
    other = de.execute_signal(engine, _signal(bar_time="2026-09-13 21:00", side="SELL"), _config(), {}, NOW)
    assert other["event"] == "refused" and "already open" in other["reason"]
    assert len(engine.orders) == 1


def test_sell_levels_and_wide_spread_refusal():
    engine, journal = FakeEngine(), {}
    event = de.execute_signal(engine, _signal(side="SELL"), _config(), journal, NOW)
    order = engine.orders[0]
    assert event["event"] == "opened"
    assert order["stop_loss"] == 4370.0 and order["take_profit"] == 4320.0
    wide = FakeEngine(quote={"ok": True, "bid": 4350.0, "ask": 4360.0, "digits": 2})
    refused = de.execute_signal(wide, _signal(), _config(), {}, NOW)
    assert refused["event"] == "refused" and "spread" in refused["reason"] and wide.orders == []


def test_sync_marks_broker_closes_and_closes_at_the_time_limit():
    engine, journal = FakeEngine(), {}
    de.execute_signal(engine, _signal(), _config(), journal, NOW)
    assert de.sync_positions(engine, _config(), journal, NOW + pd.Timedelta(hours=10)) == []
    events = de.sync_positions(engine, _config(), journal, pd.Timestamp("2026-09-15 01:05", tz="UTC"))
    assert [e["event"] for e in events] == ["closed_time_limit"] and engine.closed == [555]
    assert journal["attempts"]["2026-09-13 21:00"]["status"] == "closed_time_limit"

    engine2, journal2 = FakeEngine(), {}
    de.execute_signal(engine2, _signal(), _config(), journal2, NOW)
    engine2.open_positions = []  # stop or target hit at the broker
    events2 = de.sync_positions(engine2, _config(), journal2, NOW + pd.Timedelta(hours=2))
    assert [e["event"] for e in events2] == ["closed_at_broker"] and engine2.closed == []


def test_expired_dry_run_signal_is_journaled_once_without_broker_calls():
    engine, journal = FakeEngine(), {}
    de.execute_signal(engine, _signal(), _config(dry_run=True), journal, NOW)
    assert de.sync_positions(engine, _config(), journal, NOW + pd.Timedelta(hours=10)) == []
    events = de.sync_positions(engine, _config(), journal, pd.Timestamp("2026-09-15 01:05", tz="UTC"))
    assert [e["event"] for e in events] == ["dry_run_expired"] and events[0]["signal_bar"] == "2026-09-13 21:00"
    assert journal["attempts"]["2026-09-13 21:00"]["status"] == "dry_run_expired"
    assert de.sync_positions(engine, _config(), journal, pd.Timestamp("2026-09-15 05:05", tz="UTC")) == []
    assert engine.orders == [] and engine.closed == []


def test_failed_order_is_journaled_and_not_retried():
    engine, journal = FakeEngine(executed=False), {}
    event = de.execute_signal(engine, _signal(), _config(), journal, NOW)
    assert event["event"] == "failed" and journal["attempts"]["2026-09-13 21:00"]["status"] == "failed"
    again = de.execute_signal(engine, _signal(), _config(), journal, NOW)
    assert again["event"] == "refused" and len(engine.orders) == 1
