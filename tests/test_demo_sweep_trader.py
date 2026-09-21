"""The owner's manipulation-candle rule, executing on demo. Tests pin SAFETY, not profitability.

The rule is positive in all three gold windows and beats its own inverse, but its deflated Sharpe is
0.181 against the standing 0.95 bar. Nothing here asserts it makes money, because nothing has shown that.
"""
import numpy as np
import pandas as pd
import pytest

from src import demo_session_pullback as dsp
from src import demo_sweep_trader as st
from src import sweep_reversal
from test_demo_session_pullback import DEMO, PullbackEngine, fresh_files  # noqa: F401  (autouse fixture)

NOW = pd.Timestamp("2026-09-21 13:05", tz="UTC")
SENDING = {**st.DEFAULT_CONFIG, "enabled": True, "dry_run": False}


@pytest.fixture(autouse=True)
def _clean_state():
    """A halt is deliberately sticky in production, so each case must start clean or the account-refusal
    test silently disables every case after it."""
    for key in ("state", "trade_memory", "log"):
        path = st.sweep_paths()[key]
        if path.exists():
            path.unlink()
    yield


def _signal(side="BUY", bar="2026-09-21 09:00", close=2000.0):
    """Close sits near the fake engine's 2000.0/2000.3 quote; further away and the entry-drift
    gate refuses it, which is the gate working rather than a failure."""
    return {"side": side, "bar_time": bar, "close": close,
            "stop": close - 20.0 if side == "BUY" else close + 20.0,
            "target": close + 40.0 if side == "BUY" else close - 40.0, "atr": 10.0}


def _bars_fn(source="mt5:XAUUSD"):
    frame = pd.DataFrame({"datetime": pd.date_range(end=NOW.floor("4h"), periods=800, freq="4h", tz="UTC"),
                          "open": 4000.0, "high": 4005.0, "low": 3995.0, "close": 4000.0, "volume": 1.0})
    return lambda symbol, tf, count: (frame, source)


def test_it_trades_the_spec_that_was_measured_not_a_retyped_copy():
    """What executes must be sweep_reversal.FORWARD_CANDIDATE, the spec frozen in code and pinned by tests."""
    params = sweep_reversal.FORWARD_CANDIDATE["params"]
    assert params["mode"] == "continue" and params["lookback"] == 40
    assert params["rr"] == 2.0 and params["trend_ema"] == 400
    assert st.sweep_status()["rule"] == sweep_reversal.FORWARD_VARIANT


def test_it_ships_switched_off():
    assert st.DEFAULT_CONFIG["dry_run"] is True
    assert st.MAGIC == 440805, "its own magic, never another strategy's"
    assert st.MAGIC not in (440502, 440603, 440704)


def test_a_live_account_is_refused_and_nothing_is_sent(monkeypatch):
    monkeypatch.setattr(st, "latest_signal", lambda bars, now: _signal())
    engine = PullbackEngine(account={**DEMO, "login": 20250101, "trade_mode": 2, "server": "Vantage-Live"})
    st.sweep_cycle(engine, _bars_fn(), now=NOW, config=SENDING)
    assert engine.sent == []
    assert st.load_sweep_state()["halted"]["kind"] == "account_refused"


def test_a_dry_run_decides_everything_and_sends_nothing(monkeypatch):
    monkeypatch.setattr(st, "latest_signal", lambda bars, now: _signal())
    engine = PullbackEngine()
    out = st.sweep_cycle(engine, _bars_fn(), now=NOW, config={**st.DEFAULT_CONFIG, "dry_run": True})
    assert out["decision"] == "dry_run_order" and "NOT sent" in out["reason"]
    assert engine.sent == []


def test_no_signal_means_no_order(monkeypatch):
    monkeypatch.setattr(st, "latest_signal", lambda bars, now: None)
    engine = PullbackEngine()
    out = st.sweep_cycle(engine, _bars_fn(), now=NOW, config=SENDING)
    assert out["decision"] == "no_setup" and engine.sent == []


def test_a_stale_signal_is_refused():
    """A 4H rule firing on a bar that closed six hours ago is not the trade that was measured."""
    fresh, why = st.signal_is_fresh(_signal(bar="2026-09-21 09:00"), NOW, 60)
    assert fresh is True and "closed" in why
    stale, why2 = st.signal_is_fresh(_signal(bar="2026-09-20 21:00"), NOW, 60)
    assert stale is False and "stale signal" in why2


def test_a_bar_that_has_not_closed_is_refused():
    ok, why = st.signal_is_fresh(_signal(bar="2026-09-21 13:00"), NOW, 60)
    assert ok is False and "not closed yet" in why


def test_the_same_signal_bar_is_never_traded_twice(monkeypatch):
    monkeypatch.setattr(st, "latest_signal", lambda bars, now: _signal())
    engine = PullbackEngine()
    assert st.sweep_cycle(engine, _bars_fn(), now=NOW, config=SENDING)["decision"] == "opened"
    engine.open_positions.clear()                      # pretend it closed, same bar still latest
    second = st.sweep_cycle(engine, _bars_fn(), now=NOW + pd.Timedelta(minutes=5), config=SENDING)
    assert second["decision"] == "hold" and len(engine.sent) == 1


def test_a_valid_signal_sends_one_order_with_a_stop_and_a_target(monkeypatch):
    monkeypatch.setattr(st, "latest_signal", lambda bars, now: _signal())
    engine = PullbackEngine()
    out = st.sweep_cycle(engine, _bars_fn(), now=NOW, config=SENDING)
    assert out["decision"] == "opened" and len(engine.sent) == 1
    request = engine.sent[0]
    assert request["magic"] == 440805 and request["volume"] == 0.01
    assert request["stop_loss"] == 1980.0 and request["take_profit"] == 2040.0
    assert request["allow_retry_without_stops"] is False, "it must never go on without its stop"
    rows = [r for r in dsp.read_jsonl(st.sweep_paths()["trade_memory"]) if r.get("event") == "opened"]
    assert len(rows) == 1 and rows[0]["signal_bar"] == "2026-09-21 09:00"


def test_one_position_at_a_time(monkeypatch):
    monkeypatch.setattr(st, "latest_signal", lambda bars, now: _signal())
    engine = PullbackEngine()
    st.sweep_cycle(engine, _bars_fn(), now=NOW, config=SENDING)
    later = st.sweep_cycle(engine, _bars_fn(), now=NOW + pd.Timedelta(hours=4), config=SENDING)
    assert later["decision"] == "hold" and len(engine.sent) == 1


def test_non_broker_bars_are_refused(monkeypatch):
    monkeypatch.setattr(st, "latest_signal", lambda bars, now: _signal())
    engine = PullbackEngine()
    st.sweep_cycle(engine, _bars_fn(source="yahoo"), now=NOW, config=SENDING)
    assert engine.sent == [], "it trades broker candles or nothing"
