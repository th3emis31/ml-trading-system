"""Volatility Trend Breakout on the demo account: account refusal, two legs, gates, trail, time exit, STOP, independence.

A fake MT5 engine only (the pullback tests' engine); tests/conftest.py redirects the data folder.
"""
import json

import numpy as np
import pandas as pd
import pytest

from src import demo_session_pullback as dsp
from src import demo_volatility_breakout as dvb
from src import volatility_trend_breakout as vtb
from test_demo_session_pullback import DEMO, PullbackEngine, fresh_files  # noqa: F401  (autouse fixture; tests/ is on sys.path)

NOW = pd.Timestamp("2026-09-17 09:03", tz="UTC")               # the 05:00 4H candle closed at 09:00
SENDING = {**dvb.DEFAULT_CONFIG, "enabled": True, "dry_run": False}


def h4_frame(now=NOW, n=400, price=2000.0):
    """Quiet 4H candles ending with the still-forming candle that opened at the last 4H boundary before ``now``."""
    last_open = pd.Timestamp(now).floor("4h") + pd.Timedelta(hours=1)
    if last_open > now:
        last_open -= pd.Timedelta(hours=4)
    times = pd.date_range(end=last_open, periods=n, freq="4h", tz="UTC")
    return pd.DataFrame({"datetime": times, "open": price, "high": price + 3, "low": price - 3, "close": price, "volume": 100.0})


def h1_frame(now=NOW, n=60, price=2000.0):
    times = pd.date_range(end=pd.Timestamp(now).floor("h"), periods=n, freq="1h", tz="UTC")
    return pd.DataFrame({"datetime": times, "open": price, "high": price + 2, "low": price - 2, "close": price, "volume": 1.0})


def bars_for(h4, h1=None, source="mt5:XAUUSD"):
    return lambda symbol, timeframe, count: ((h4 if timeframe == "4h" else (h1 if h1 is not None else h1_frame())), source)


def signal_on_last_closed(monkeypatch, entry=2000.0, atr=10.0):
    def fake(candles, cfg=None):
        i = len(candles) - 1
        return [vtb.Signal(i, candles[i].ts, "long", entry, entry - 1.5 * atr, entry + 1.5 * atr * 1.3,
                           entry + 1.5 * atr * 2.8, atr, "test breakout")]
    monkeypatch.setattr(dvb.vtb, "generate_signals", fake)


def test_a_live_account_is_refused_and_nothing_is_sent(monkeypatch):
    signal_on_last_closed(monkeypatch)
    engine = PullbackEngine(account={**DEMO, "login": 20250101, "trade_mode": 2, "server": "Vantage-Live"})
    dvb.breakout_cycle(engine, bars_for(h4_frame()), [], now=NOW, config=SENDING)
    state = dvb.load_breakout_state()
    assert engine.sent == [] and state["halted"]["kind"] == "account_refused"


def test_breakout_sends_two_001_legs_with_the_script_levels_and_holds_one_position(monkeypatch):
    signal_on_last_closed(monkeypatch)
    engine = PullbackEngine()
    summary = dvb.breakout_cycle(engine, bars_for(h4_frame()), [], now=NOW, config=SENDING)
    assert summary["decision"] == "opened"
    assert [r["volume"] for r in engine.sent] == [0.01, 0.01] and {r["magic"] for r in engine.sent} == {440603}
    assert all(r["side"] == "BUY" and r["stop_loss"] == 1985.0 and r["allow_retry_without_stops"] is False for r in engine.sent)
    assert [r["take_profit"] for r in engine.sent] == [2019.5, 2042.0]
    later = NOW + pd.Timedelta(hours=1)
    assert dvb.breakout_cycle(engine, bars_for(h4_frame(later)), [], now=later, config=SENDING)["decision"] == "hold"
    assert len(engine.sent) == 2


def test_stale_signal_price_drift_and_tier1_window_are_refused(monkeypatch):
    signal_on_last_closed(monkeypatch)
    engine = PullbackEngine()
    late = pd.Timestamp("2026-09-17 09:58", tz="UTC")
    summary = dvb.breakout_cycle(engine, bars_for(h4_frame(late), h1_frame(late)), [], now=late, config=SENDING)
    assert summary["decision"] == "refused" and "closed 58 min ago" in summary["reason"]

    dvb.breakout_paths()["state"].unlink()
    signal_on_last_closed(monkeypatch, entry=1990.0)        # ask 2000.3 is 10.3 above the signal close, limit 0.5 x 10
    summary = dvb.breakout_cycle(engine, bars_for(h4_frame()), [], now=NOW, config=SENDING)
    assert summary["decision"] == "refused" and "price moved" in summary["reason"]

    dvb.breakout_paths()["state"].unlink()
    signal_on_last_closed(monkeypatch)
    cpi = [{"title": "CPI m/m", "currency": "USD", "impact": "High", "time_utc": "2026-09-17 09:30"}]
    summary = dvb.breakout_cycle(engine, bars_for(h4_frame()), cpi, now=NOW, config=SENDING)
    assert summary["decision"] == "refused" and "tier-1 event window" in summary["reason"] and engine.sent == []


def test_leg_b_goes_to_break_even_and_trails_then_time_exit_and_trade_memory(monkeypatch):
    signal_on_last_closed(monkeypatch)
    engine = PullbackEngine()
    dvb.breakout_cycle(engine, bars_for(h4_frame()), [], now=NOW, config=SENDING)
    trade = next(iter(dvb.load_breakout_state()["trades"].values()))
    leg_a, leg_b = trade["legs"]
    monkeypatch.setattr(dvb.vtb, "generate_signals", lambda candles, cfg=None: [])
    engine.broker_closes(leg_a["ticket"], price=2019.5, net=0.19)          # TP1 filled at the broker
    after = NOW + pd.Timedelta(hours=4)
    frame = h4_frame(after)
    frame.loc[frame.index[-2], "high"] = 2030.0                             # new high since entry on a closed candle
    dvb.breakout_cycle(engine, bars_for(frame), [], now=after, config=SENDING)
    moved = [stop for ticket, stop in engine.modified if ticket == leg_b["ticket"]]
    assert moved, "leg B stop must move after TP1"
    atr_now = dvb.indicator_values(dvb._candles(frame.iloc[:-1]))["atr"]
    assert moved[-1] == pytest.approx(round(max(2000.0 + 0.15 * atr_now, 2030.0 - 2.2 * atr_now), 2), abs=0.011)

    end = NOW + pd.Timedelta(hours=4 * 66)
    dvb.breakout_cycle(engine, bars_for(h4_frame(end)), [], now=end, config=SENDING)
    assert leg_b["ticket"] in engine.closed, "65 closed 4H candles after entry closes the rest"
    settle = end + pd.Timedelta(hours=1)
    dvb.breakout_cycle(engine, bars_for(h4_frame(settle)), [], now=settle, config=SENDING)
    memory = dsp.read_jsonl(dvb.breakout_paths()["trade_memory"])
    assert len(memory) == 1 and memory[0]["magic"] == 440603 and memory[0]["strategy"] == "volatility_trend_breakout"
    for key in ("setup", "session", "regime", "minutes_to_next_tier1_event", "r_result", "legs"):
        assert key in memory[0]
    assert memory[0]["r_result"] == pytest.approx(0.5 * (2019.5 - leg_a["fill"]) / trade["r_price"]
                                                  + 0.5 * (engine.bid - leg_b["fill"]) / trade["r_price"], abs=1e-3)


def test_stop_closes_only_breakout_legs_and_does_not_halt_the_pullback(monkeypatch):
    signal_on_last_closed(monkeypatch)
    engine = PullbackEngine()
    dvb.breakout_cycle(engine, bars_for(h4_frame()), [], now=NOW, config=SENDING)
    engine.add_foreign_position(7, 440502)                                  # a pullback leg
    event = dvb.stop_breakout(engine, now=NOW + pd.Timedelta(minutes=5))
    assert len(event["closed"]) == 2 and 7 in engine.open
    assert dvb.load_breakout_state()["halted"]["kind"] == "owner_stop"
    assert dsp.read_state()["halted"] is None
    logged = [json.loads(line) for line in dvb.breakout_paths()["log"].read_text(encoding="utf-8").splitlines()]
    assert all(row["magic"] == 440603 for row in logged)
    assert not dsp.demo_paths()["log"].exists(), "the pullback's log is untouched"
    dvb.resume_breakout(now=NOW + pd.Timedelta(minutes=6))
    assert dvb.load_breakout_state()["halted"] is None


def test_real_signal_function_on_a_clean_breakout():
    n = 300
    close = np.full(n, 2000.0)
    close[-1] = 2060.0
    frame = pd.DataFrame({"datetime": pd.date_range("2026-01-01", periods=n, freq="4h", tz="UTC"),
                          "open": close - 1, "high": close + 3, "low": close - 3, "close": close, "volume": 100.0})
    frame.loc[n - 1, "volume"] = 500.0
    frame.loc[:n - 30, "close"] = np.linspace(1900, 1995, n - 29)            # rising into the breakout for EMA50 / RSI
    signals = vtb.generate_signals(dvb._candles(frame), dvb.RULES)
    assert signals and signals[-1].index == n - 1 and signals[-1].direction == "long"


def test_breakout_endpoints_need_the_secret_and_the_page_has_its_stop_button():
    import app as app_module
    from src import execution_guard

    client = app_module.app.test_client()
    for path in ("/api/demo-breakout/cycle", "/api/demo-breakout/stop", "/api/demo-breakout/resume"):
        assert client.post(path).status_code == 403, path
    status = client.get("/api/demo-breakout/status").get_json()
    assert status["magic"] == 440603 and status["demo_account"] == 11581419
    secret = execution_guard.load_or_create_secret()
    assert client.post("/api/demo-breakout/resume", headers={execution_guard.SECRET_HEADER: secret}).status_code == 200
    html = client.get("/demo-trading").get_data(as_text=True)
    assert "id='b-stop-btn'" in html and "/api/demo-breakout/stop" in html and "id='stop-btn'" in html
