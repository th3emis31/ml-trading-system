"""Gold session pullback demo trading: account refusal, gates, two legs, break-even, 21:00 flat, kill switches, STOP.

Every test uses a fake MT5 engine; the real terminal is never touched. tests/conftest.py redirects the data folder, so
config, state, log and trade memory are temporary files.
"""
import json

import numpy as np
import pandas as pd
import pytest

from src import demo_executor
from test_demo_executor import FakeEngine as ModelExecutorFakeEngine   # pytest puts tests/ on sys.path
from src import demo_session_pullback as dsp

DEMO = {"login": 11581419, "server": "VantageInternational-Demo", "trade_mode": 0, "balance": 100000.0, "equity": 100000.0}
NOW = pd.Timestamp("2026-09-17 10:01", tz="UTC")     # Thursday; no tier-1 event that day


class PullbackEngine(ModelExecutorFakeEngine):
    """The demo executor tests' fake MT5 engine, extended with unique tickets, deals, stop changes and a light account read."""

    def __init__(self, account=None, connected=True):
        super().__init__(account=dict(DEMO if account is None else account), connected=connected,
                         quote={"ok": True, "bid": 2000.0, "ask": 2000.3, "digits": 2})
        self.deals, self.modified, self.sent = [], [], self.orders
        self.next_ticket = 1000
        self.reject_leg = None
        self.bid, self.ask = 2000.0, 2000.3

    @property
    def open(self):
        return {int(p["ticket"]): p for p in self.open_positions}

    def account_snapshot(self):
        return dict(self.account) if self.connected else None

    def add_foreign_position(self, ticket, magic):
        self.open_positions.append({"ticket": ticket, "magic": magic, "volume": 0.5, "profit": 0.0, "comment": "other EA"})

    def place_market_order(self, **request):
        self.orders.append(request)
        if self.reject_leg and request["comment"].endswith(self.reject_leg):
            return {"executed": False, "message": "rejected by test"}
        self.next_ticket += 1
        price = self.ask if request["side"] == "BUY" else self.bid
        self.open_positions.append({"ticket": self.next_ticket, "magic": request["magic"], "volume": request["volume"],
                                    "price_open": price, "sl": request["stop_loss"], "tp": request["take_profit"],
                                    "profit": 0.0, "comment": request["comment"], "direction": request["side"]})
        return {"executed": True, "result": {"order": self.next_ticket, "price": price}}

    def close_position(self, ticket, comment=""):
        result = super().close_position(ticket, comment)
        self.deals.append({"position_id": int(ticket), "price": self.bid, "net": 1.0, "comment": comment})
        return result

    def modify_position_sltp(self, ticket, stop_loss=None, take_profit=None):
        self.modified.append((ticket, stop_loss))
        self.open[int(ticket)]["sl"] = stop_loss
        return {"executed": True}

    def deal_history(self, days=5):
        return {"ok": True, "deals": list(self.deals)}

    def broker_closes(self, ticket, price, net):
        self.open_positions = [p for p in self.open_positions if int(p["ticket"]) != int(ticket)]
        self.deals.append({"position_id": int(ticket), "price": price, "net": net, "comment": "[tp]"})


def quiet_h1_bars_ending_at(now=NOW, n=300, price=2000.0):
    """Quiet H1 bars ending with the forming bar that opened at ``now``'s hour."""
    times = pd.date_range(end=now.floor("h"), periods=n, freq="1h", tz="UTC")
    return pd.DataFrame({"datetime": times, "open": price, "high": price + 2, "low": price - 2, "close": price, "volume": 1})


def _bars_fn(frame, source="mt5:XAUUSD"):
    return lambda symbol, timeframe, count: (frame, source)


def _setup(monkeypatch, side=1, atr=4.0, swing_low=1996.0, swing_high=2004.0):
    def fake_setups(bars):
        return pd.DataFrame([{"signal_idx": len(bars) - 2, "entry_idx": len(bars) - 1, "side": side, "atr": atr,
                              "swing_low": swing_low, "swing_high": swing_high, "ema20": 2000.0,
                              "h4_ema_fast": 1990.0, "h4_ema_slow": 1950.0}])
    monkeypatch.setattr(dsp, "session_pullback_setups", fake_setups)


SENDING = {**dsp.DEFAULT_CONFIG, "enabled": True, "dry_run": False}
DRY_RUN = {**SENDING, "dry_run": True}


@pytest.fixture(autouse=True)
def fresh_files():
    for path in dsp.demo_paths().values():
        if path.exists():
            path.unlink()
    yield


def test_only_the_demo_account_11581419_is_accepted():
    assert dsp.check_demo_account(DEMO)[0] is True
    assert dsp.check_demo_account({**DEMO, "login": 12345678})[0] is False
    assert dsp.check_demo_account({**DEMO, "trade_mode": 2})[0] is False, "a real (trade_mode 2) account is refused"
    assert dsp.check_demo_account({**DEMO, "server": "VantageInternational-Live"})[0] is False
    assert dsp.check_demo_account(None)[0] is False


def test_a_live_account_is_refused_and_halts_without_any_order(monkeypatch):
    _setup(monkeypatch)
    engine = PullbackEngine(account={**DEMO, "login": 20250101, "trade_mode": 2, "server": "Vantage-Live"})
    result = dsp.run_cycle(engine, _bars_fn(quiet_h1_bars_ending_at()), [], now=NOW, config=SENDING)
    state = dsp.read_state()
    assert engine.sent == [] and state["halted"]["kind"] == "account_refused"
    assert "11581419" in state["halted"]["reason"]
    assert result["events"][-1]["event"] == "halted"


def test_account_is_rechecked_on_every_order(monkeypatch):
    engine = PullbackEngine()
    guarded = dsp.DemoOnlyEngine(engine)
    engine.account["login"] = 999
    with pytest.raises(dsp.AccountRefused):
        guarded.place_market_order(symbol="XAUUSD", side="BUY", volume=0.01, magic=dsp.MAGIC)
    engine.account["login"] = dsp.DEMO_ACCOUNT_LOGIN
    with pytest.raises(dsp.AccountRefused):
        guarded.place_market_order(symbol="XAUUSD", side="BUY", volume=0.10, magic=dsp.MAGIC)   # not 0.01
    assert engine.sent == []


def test_live_entry_sends_two_001_legs_with_the_strategy_levels(monkeypatch):
    _setup(monkeypatch)
    engine = PullbackEngine()
    summary = dsp.run_cycle(engine, _bars_fn(quiet_h1_bars_ending_at()), [], now=NOW, config=SENDING)
    assert summary["decision"] == "opened"
    assert [r["volume"] for r in engine.sent] == [0.01, 0.01]
    assert {r["magic"] for r in engine.sent} == {440502}
    entry = engine.ask
    stop = 1996.0 - 1.5 * 4.0
    r = entry - stop
    assert engine.sent[0]["stop_loss"] == engine.sent[1]["stop_loss"] == round(stop, 2)
    assert engine.sent[0]["take_profit"] == round(entry + 1.5 * r, 2) and engine.sent[1]["take_profit"] == round(entry + 3 * r, 2)
    assert all(r_["allow_retry_without_stops"] is False for r_ in engine.sent)
    logged = [json.loads(line) for line in dsp.demo_paths()["log"].read_text(encoding="utf-8").splitlines()]
    assert [x["event"] for x in logged].count("order_sent") == 2 and all(x["reason"] for x in logged)
    # one trade at a time
    engine2_summary = dsp.run_cycle(engine, _bars_fn(quiet_h1_bars_ending_at(NOW + pd.Timedelta(hours=1))), [], now=NOW + pd.Timedelta(hours=1), config=SENDING)
    assert engine2_summary["decision"] == "hold" and len(engine.sent) == 2


def test_a_rejected_second_leg_undoes_the_first_and_halts(monkeypatch):
    _setup(monkeypatch)
    engine = PullbackEngine()
    engine.reject_leg = "B"
    dsp.run_cycle(engine, _bars_fn(quiet_h1_bars_ending_at()), [], now=NOW, config=SENDING)
    assert engine.closed and not engine.open, "leg A must be closed again"
    assert dsp.read_state()["halted"]["kind"] == "mt5_error"


def test_breakeven_after_leg_a_then_flat_at_21_and_trade_memory(monkeypatch):
    _setup(monkeypatch)
    engine = PullbackEngine()
    dsp.run_cycle(engine, _bars_fn(quiet_h1_bars_ending_at()), [], now=NOW, config=SENDING)
    trade = next(iter(dsp.read_state()["trades"].values()))
    leg_a, leg_b = trade["legs"]
    engine.broker_closes(leg_a["ticket"], price=trade["setup"]["tp1"], net=15.0)      # leg A target filled at the broker
    later = NOW + pd.Timedelta(hours=2)
    dsp.run_cycle(engine, _bars_fn(quiet_h1_bars_ending_at(later)), [], now=later, config=SENDING)
    assert engine.modified == [(leg_b["ticket"], round(trade["entry"], 2))]
    flat = pd.Timestamp("2026-09-17 21:01", tz="UTC")
    dsp.run_cycle(engine, _bars_fn(quiet_h1_bars_ending_at(flat)), [], now=flat, config=SENDING)
    assert leg_b["ticket"] in engine.closed
    settle = flat + pd.Timedelta(hours=1)
    dsp.run_cycle(engine, _bars_fn(quiet_h1_bars_ending_at(settle)), [], now=settle, config=SENDING)
    memory = dsp.read_jsonl(dsp.demo_paths()["trade_memory"])
    assert len(memory) == 1
    record = memory[0]
    for key in ("magic", "setup", "session", "regime", "minutes_to_next_tier1_event", "next_event", "r_result", "legs"):
        assert key in record, key
    assert record["magic"] == 440502 and record["mode"] == "demo_broker_fill"
    assert record["session"] == "London" and record["regime"]["trend"] == "up"
    expected = 0.5 * 1.5 + 0.5 * (engine.bid - leg_b["fill"]) / trade["r_price"]
    assert record["r_result"] == pytest.approx(expected, abs=1e-3)
    assert dsp.status_payload(engine, now=settle)["expectancy"]["demo_broker_fills"]["trades"] == 1


def test_tier1_window_and_breaker_refuse_with_a_reason(monkeypatch):
    _setup(monkeypatch)
    engine = PullbackEngine()
    fomc = [{"title": "Federal Funds Rate", "currency": "USD", "impact": "High", "time_utc": "2026-09-17 11:00"}]
    summary = dsp.run_cycle(engine, _bars_fn(quiet_h1_bars_ending_at()), fomc, now=NOW, config=SENDING)
    assert summary["decision"] == "refused" and "tier-1 event window" in summary["reason"] and engine.sent == []

    dsp.demo_paths()["state"].unlink()
    frame = quiet_h1_bars_ending_at()
    frame.loc[len(frame) - 2, ["high", "low"]] = [2060.0, 1940.0]                     # a 120 range vs ATR 4 on the last closed bar
    summary = dsp.run_cycle(engine, _bars_fn(frame), [], now=NOW, config=SENDING)
    assert summary["decision"] == "refused" and "volatility breaker" in summary["reason"] and engine.sent == []


def test_non_broker_bars_and_disconnects_halt_until_the_owner_resumes(monkeypatch):
    _setup(monkeypatch)
    engine = PullbackEngine()
    dsp.run_cycle(engine, _bars_fn(quiet_h1_bars_ending_at(), source="yahoo:1h"), [], now=NOW, config=SENDING)
    assert dsp.read_state()["halted"]["kind"] == "mt5_error"
    summary = dsp.run_cycle(engine, _bars_fn(quiet_h1_bars_ending_at()), [], now=NOW + pd.Timedelta(minutes=1), config=SENDING)
    assert summary["decision"] == "halted_skip" and engine.sent == []
    dsp.owner_resume(now=NOW + pd.Timedelta(minutes=2))
    assert dsp.read_state()["halted"] is None
    engine.connected = False
    dsp.run_cycle(engine, _bars_fn(quiet_h1_bars_ending_at()), [], now=NOW + pd.Timedelta(minutes=3), config=SENDING)
    assert "not connected" in dsp.read_state()["halted"]["reason"]


def test_kill_switches_daily_stop_and_drawdown_halt():
    state = dict(dsp.INITIAL_STATE, trades={}, day_start_equity=1.0, equity=0.965)
    events = dsp.update_kill_switches(state, SENDING, DEMO, 0.0, NOW)
    assert state["day_stopped"] and "day_stopped" in [e["event"] for e in events]
    state = dict(dsp.INITIAL_STATE, trades={}, peak_equity=1.2, day_start_equity=1.0, equity=1.0)
    dsp.update_kill_switches(state, SENDING, DEMO, 0.0, NOW)
    assert state["halted"]["kind"] == "drawdown"


def test_max_two_entries_a_day_and_dry_run_sends_nothing(monkeypatch):
    _setup(monkeypatch)
    engine = PullbackEngine()
    config = dict(DRY_RUN)
    summary = dsp.run_cycle(engine, _bars_fn(quiet_h1_bars_ending_at()), [], now=NOW, config=config)
    assert summary["decision"] == "dry_run_order" and engine.sent == []
    state = dsp.read_state()
    for trade in state["trades"].values():
        trade["status"] = "closed"
    state["entries_today"] = 2
    dsp.write_state(state)
    later = NOW + pd.Timedelta(hours=3)
    summary = dsp.run_cycle(engine, _bars_fn(quiet_h1_bars_ending_at(later)), [], now=later, config=config)
    assert summary["decision"] == "no_entry" and "max 2 entries" in summary["reason"]


def test_dry_run_trade_settles_on_bars_into_trade_memory(monkeypatch):
    _setup(monkeypatch)
    engine = PullbackEngine()
    config = dict(DRY_RUN)
    dsp.run_cycle(engine, _bars_fn(quiet_h1_bars_ending_at()), [], now=NOW, config=config)
    frame = quiet_h1_bars_ending_at(NOW + pd.Timedelta(hours=3))
    entry_row = frame.index[frame["datetime"] == NOW.floor("h")][0]
    frame.loc[entry_row + 1, "low"] = 1980.0                                          # stop (1990) hit on the next bar
    monkeypatch.setattr(dsp, "session_pullback_setups", lambda bars: pd.DataFrame(columns=["signal_idx"]))
    dsp.run_cycle(engine, _bars_fn(frame), [], now=NOW + pd.Timedelta(hours=3), config=config)
    memory = dsp.read_jsonl(dsp.demo_paths()["trade_memory"])
    assert len(memory) == 1 and memory[0]["mode"] == "dry_run_simulated" and memory[0]["r_result"] == pytest.approx(-1.0)


def test_owner_stop_closes_only_this_strategys_positions_and_halts(monkeypatch):
    _setup(monkeypatch)
    engine = PullbackEngine()
    dsp.run_cycle(engine, _bars_fn(quiet_h1_bars_ending_at()), [], now=NOW, config=SENDING)
    engine.add_foreign_position(1, 888888)
    event = dsp.owner_stop(engine, now=NOW + pd.Timedelta(minutes=5))
    assert len(event["closed"]) == 2 and 1 in engine.open, "the other expert's position is untouched"
    assert dsp.read_state()["halted"]["kind"] == "owner_stop"


def test_model_executor_440401_logs_that_it_is_off():
    journal = {"attempts": {}, "events": []}
    event = demo_executor.execute_signal(None, {"side": "BUY"}, {**demo_executor.DEFAULT_CONFIG, "enabled": False,
                                                                  "disabled_reason": "only the pullback trades"}, journal)
    assert event["event"] == "skipped" and "only the pullback trades" in event["reason"]


def test_endpoints_need_the_secret_and_the_page_has_a_stop_button():
    import app as app_module
    from src import execution_guard

    client = app_module.app.test_client()
    for path in ("/api/demo-trading/cycle", "/api/demo-trading/stop", "/api/demo-trading/resume"):
        assert client.post(path).status_code == 403, path
        assert client.get(path).status_code == 405, path
    secret = execution_guard.load_or_create_secret()
    assert client.post("/api/demo-trading/resume", headers={execution_guard.SECRET_HEADER: secret}).status_code == 200
    status = client.get("/api/demo-trading/status").get_json()
    assert status["demo_account"] == 11581419 and status["magic"] == 440502
    html = client.get("/demo-trading").get_data(as_text=True)
    assert "id='stop-btn'" in html and "/api/demo-trading/stop" in html and "X-Control-Secret" in html
