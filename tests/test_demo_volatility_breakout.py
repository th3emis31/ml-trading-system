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
# The mechanics tests below are about one market's behaviour, so they pin the strategy to gold. The strategy runs
# gold and bitcoin together in production; that pairing has its own test at the end of this file.
SENDING = {**dvb.DEFAULT_CONFIG, "enabled": True, "dry_run": False, "symbols": ["XAUUSD"]}


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
    # Two rows since 20 Sep 2026: one when the order was placed, one when it settled.
    assert len(memory) == 2 and [m.get("event") for m in memory] == ["opened", "closed"]
    closed = [m for m in memory if m.get("event") == "closed"]
    assert len(closed) == 1 and closed[0]["magic"] == 440603 and closed[0]["strategy"] == "volatility_trend_breakout"
    for key in ("setup", "session", "regime", "minutes_to_next_tier1_event", "r_result", "legs"):
        assert key in closed[0]
    assert closed[0]["r_result"] == pytest.approx(0.5 * (2019.5 - leg_a["fill"]) / trade["r_price"]
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


def test_gold_and_bitcoin_are_traded_independently(monkeypatch):
    """Bitcoin was added on 2026-09-18 after testing the owner's own settings on BTCUSD: positive over 248 trades and
    8.7 years, and it fires roughly twice as often as gold. The two markets must not collide - same 4H candle times,
    one shared magic number - so each keeps its own trade id, its own open position and its own decision."""
    signal_on_last_closed(monkeypatch)
    engine = PullbackEngine()
    both = {**dvb.DEFAULT_CONFIG, "enabled": True, "dry_run": True, "symbols": ["XAUUSD", "BTCUSD"]}
    asked = []

    def bars(symbol, timeframe, count):
        asked.append((symbol, timeframe))
        return bars_for(h4_frame())(symbol, timeframe, count)

    summary = dvb.breakout_cycle(engine, bars, [], now=NOW, config=both)
    assert set(summary["per_symbol"]) == {"XAUUSD", "BTCUSD"}, "both markets get their own decision"
    assert {s for s, tf in asked if tf == "4h"} == {"XAUUSD", "BTCUSD"}, "candles are fetched per market"

    trades = dvb.load_breakout_state()["trades"]
    assert len(trades) == 2, "the same candle time on two markets must not overwrite one trade"
    assert {t["symbol"] for t in trades.values()} == {"XAUUSD", "BTCUSD"}
    assert all(key.startswith(t["symbol"]) for key, t in trades.items()), "the trade id carries its market"

    # a second pass takes nothing new: each market has its own position and its own "already handled" guard
    again = dvb.breakout_cycle(engine, bars, [], now=NOW + pd.Timedelta(minutes=5), config=both)
    assert all(r["decision"] in ("hold", "refused") for r in again["per_symbol"].values())
    assert len(dvb.load_breakout_state()["trades"]) == 2


def _memory_rows():
    return dsp.read_jsonl(dvb.breakout_paths()["trade_memory"])


def test_an_order_is_journalled_the_moment_it_is_placed(monkeypatch):
    """Added 20 Sep 2026. Only closes were recorded before, so a live position existed nowhere durable:
    the BTCUSD trade opened 18 Sep left one log line saying "BUY opened: 2 legs" - no symbol, no price,
    no size, no stop, no target, no ticket. If the state file had been lost it was unreconstructable."""
    signal_on_last_closed(monkeypatch)
    engine = PullbackEngine()
    summary = dvb.breakout_cycle(engine, bars_for(h4_frame()), [], now=NOW, config=SENDING)
    assert summary["decision"] == "opened"

    opens = [m for m in _memory_rows() if m.get("event") == "opened"]
    assert len(opens) == 1, "placing an order must leave exactly one durable record"
    row = opens[0]
    # everything needed to reconstruct the trade without the state file
    assert row["symbol"] == "XAUUSD" and row["side"] == "BUY"
    trade = next(iter(dvb.load_breakout_state()["trades"].values()))
    assert row["entry"] == trade["entry"], "the record must carry the FILL, not the signal price"
    assert row["stop"] == 1985.0
    assert row["volume_per_leg"] == 0.01 and row["total_volume"] == 0.02
    assert sorted(row["targets"].values()) == [2019.5, 2042.0]
    assert len(row["tickets"]) == 2 and all(isinstance(t, int) for t in row["tickets"])
    assert row["magic"] == 440603 and row["account"] == dsp.DEMO_ACCOUNT_LOGIN
    assert row["opened_at"] and row["signal_bar"]


def test_the_log_line_names_the_symbol_price_size_and_tickets(monkeypatch):
    """"BUY opened: 2 legs" told the owner nothing about what had been placed."""
    signal_on_last_closed(monkeypatch)
    summary = dvb.breakout_cycle(PullbackEngine(), bars_for(h4_frame()), [], now=NOW, config=SENDING)
    reason = summary["reason"]
    fill = next(iter(dvb.load_breakout_state()["trades"].values()))["entry"]
    for expected in ("XAUUSD", "0.01", str(fill), "1985.0", "tickets"):
        assert expected in reason, f"{expected!r} missing from {reason!r}"


def test_an_open_record_is_never_counted_as_a_closed_trade(monkeypatch):
    """The journal is read by selecting on r_result, so open rows must not inflate the trade count."""
    signal_on_last_closed(monkeypatch)
    dvb.breakout_cycle(PullbackEngine(), bars_for(h4_frame()), [], now=NOW, config=SENDING)
    rows = _memory_rows()
    assert any(m.get("event") == "opened" for m in rows)
    assert [m for m in rows if m.get("r_result") is not None] == [], "an open trade has no result yet"


def test_a_dry_run_order_is_not_journalled_as_placed(monkeypatch):
    """A dry run sends nothing, so it must leave no 'opened' record claiming it did."""
    signal_on_last_closed(monkeypatch)
    summary = dvb.breakout_cycle(PullbackEngine(), bars_for(h4_frame()), [], now=NOW,
                                 config={**dvb.DEFAULT_CONFIG, "enabled": True, "dry_run": True,
                                         "symbols": ["XAUUSD"]})
    assert summary["decision"] == "dry_run_order"
    assert [m for m in _memory_rows() if m.get("event") == "opened"] == []


# --- the regime label the forward evidence needs (added 26 Sep 2026) ----------------------------

def test_the_regime_phase_rule_is_the_one_declared_before_the_split_was_measured():
    """trending when |EMA50 - EMA200| exceeds one ATR, ranging otherwise. Strict >, so equality ranges."""
    from src.demo_volatility_breakout import regime_phase

    assert regime_phase(1050, 1000, 10) == "trending"
    assert regime_phase(1005, 1000, 10) == "ranging"
    assert regime_phase(1010, 1000, 10) == "ranging", "a gap exactly one ATR wide is not trending"
    assert regime_phase(950, 1000, 10) == "trending", "the rule is on the absolute gap, either direction"


def test_a_missing_input_gives_no_label_rather_than_a_guess():
    from src.demo_volatility_breakout import regime_phase

    assert regime_phase(None, 1000, 10) is None
    assert regime_phase(1000, None, 10) is None
    assert regime_phase(1050, 1000, None) is None
    assert regime_phase(1050, 1000, 0) is None


def test_the_slow_ema_cannot_reach_the_entry_decision():
    """Adding EMA200 to the JOURNAL is additive; adding it to the signal would be the naive filter the
    NEVER-BLOCK rule forbids.

    The decisive check is not a string search over the caller - it is that the SIGNAL GENERATOR has no
    access to it. Entries come from src.volatility_trend_breakout, whose Config drives every rule, so if
    that module knows nothing of a 200 EMA then no entry can be conditioned on one.
    """
    import inspect

    from src import volatility_trend_breakout as vtb
    from src.demo_volatility_breakout import REGIME_SLOW_EMA

    signal_source = inspect.getsource(vtb)
    assert "REGIME_SLOW_EMA" not in signal_source, "the signal module must not know the regime EMA"
    assert vtb.Config().ema_len == 50, "the entry filter is the 50 EMA and nothing else"
    assert REGIME_SLOW_EMA == 200 and REGIME_SLOW_EMA != vtb.Config().ema_len
    # And no field of the Config that drives entries mentions the slow length.
    assert 200 not in [getattr(vtb.Config(), f) for f in
                       ("ema_len", "donchian_len", "atr_len", "rsi_len", "vol_ma_len", "max_bars")]


def test_indicator_values_now_carries_the_slow_ema():
    from src.demo_volatility_breakout import indicator_values
    from src.volatility_trend_breakout import Candle

    candles = [Candle(ts=f"2026-01-01T{i:02d}:00", open=100 + i, high=101 + i, low=99 + i,
                      close=100 + i, volume=10.0) for i in range(300)]
    out = indicator_values(candles)
    assert "ema_slow" in out and out["ema_slow"] is not None
    assert out["ema_slow"] < out["ema"], "on a rising series the slow EMA lags below the fast one"


# --- MAE / MFE / holding time (added 26 Sep 2026) ------------------------------------------------

def _long_bars(pairs):
    from src.volatility_trend_breakout import Candle

    return [Candle(ts=f"2026-01-{i+1:02d} 00:00", open=100.0, high=h, low=l, close=100.0, volume=1.0)
            for i, (h, l) in enumerate(pairs)]


# The signal bar sits BEFORE every bar _long_bars generates, so all of them count as held bars.
# (Originally this was 2026-01-01, the same as the first generated bar - which only passed because the
# code was including the signal bar, the off-by-one fixed on 26 Sep 2026.)
_TRADE = {"side": "BUY", "signal_bar": "2025-12-31 00:00", "entry": 100.0, "r_price": 2.0,
          "r_result": 1.5, "opened_at": "2026-01-01 00:00:00", "closed_at": "2026-01-04 12:00:00"}


def test_mae_and_mfe_are_measured_in_r_from_the_entry():
    from src.demo_volatility_breakout import excursion

    out = excursion(_long_bars([(100.5, 99.0), (101.0, 98.5), (104.0, 100.0), (106.0, 103.0)]), _TRADE)
    assert out["mae_r"] == -0.75, "worst low 98.5 is 1.5 below a 100 entry, over an R of 2"
    assert out["mfe_r"] == 3.0, "best high 106 is 6 above entry, over an R of 2"
    assert out["mae_price"] == 98.5 and out["mfe_price"] == 106.0
    assert out["bars_held"] == 4


def test_mfe_capture_shows_how_much_of_the_move_the_exits_kept():
    """The number that changes decisions: a low capture says the exits, not the entries, lose the money."""
    from src.demo_volatility_breakout import excursion

    out = excursion(_long_bars([(106.0, 98.5)]), _TRADE)
    assert out["mfe_capture"] == 0.5, "took 1.5R of the 3.0R that was available"


def test_holding_time_comes_from_the_stamps_not_from_a_bar_count():
    from src.demo_volatility_breakout import excursion

    out = excursion(_long_bars([(101.0, 99.0)]), _TRADE)
    assert out["hours_held"] == 84.0


def test_a_short_trade_is_refused_rather_than_silently_inverted():
    """MAE off the lows and MFE off the highs is only right for a long. Inverting the meaning of both
    numbers without saying so would be worse than not measuring them."""
    from src.demo_volatility_breakout import excursion

    out = excursion(_long_bars([(101.0, 99.0)]), {**_TRADE, "side": "SELL"})
    assert "mae_r" not in out
    assert "only correct for a long" in out["excursion_note"]


def test_nothing_here_can_stop_a_trade_being_settled():
    """A missing measurement is a gap in the record; an exception during settlement would leave a real
    position untracked. Every bad input must come back empty, never raise."""
    from src.demo_volatility_breakout import excursion

    for candles, trade in (([], _TRADE),
                           (_long_bars([(1.0, 1.0)]), {"side": "BUY"}),
                           (_long_bars([(1.0, 1.0)]), {**_TRADE, "r_price": 0}),
                           (_long_bars([(1.0, 1.0)]), {**_TRADE, "entry": 0}),
                           (None, _TRADE),
                           (_long_bars([(1.0, 1.0)]), {**_TRADE, "opened_at": "not a date"})):
        out = excursion(candles, trade)
        assert isinstance(out, dict)


def test_an_unparseable_stamp_loses_only_the_holding_time():
    from src.demo_volatility_breakout import excursion

    out = excursion(_long_bars([(106.0, 98.5)]), {**_TRADE, "closed_at": "nonsense"})
    assert "hours_held" not in out
    assert out["mae_r"] == -0.75, "the excursions must survive a bad timestamp"


def test_the_signal_bars_own_low_is_not_counted_against_the_trade():
    """The off-by-one that produced an impossible number on a real trade.

    The entry fills at the signal bar's CLOSE, so that bar's low happened before the position existed.
    Measured on the real 18 Sep BTCUSD trade, including it gave MAE -1.95R on a trade whose stop sat at
    -1.0R - which could not have happened without being stopped out, and that is what exposed it.
    """
    from src.demo_volatility_breakout import excursion
    from src.volatility_trend_breakout import Candle

    bars = [
        # the signal bar: a huge range, low far below where the entry will fill
        Candle(ts="2026-01-01 00:00", open=90.0, high=100.5, low=80.0, close=100.0, volume=1.0),
        Candle(ts="2026-01-02 00:00", open=100.0, high=103.0, low=99.0, close=102.0, volume=1.0),
    ]
    trade = {"side": "BUY", "signal_bar": "2026-01-01 00:00", "entry": 100.0, "r_price": 2.0}
    out = excursion(bars, trade)
    assert out["mae_price"] == 99.0, "MAE must come from bars after the entry, not the signal bar's low"
    assert out["mae_r"] == -0.5
    assert out["bars_held"] == 1, "the signal bar is not a bar the position was held for"
