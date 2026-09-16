import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import daily_agent as da


def agent_plan(status="new_setup", close=100.0, stop=94.0, target=118.0):
    checklist = [{"name": "H4 uptrend: fast EMA above slow EMA and rising", "ok": status != "no_trade"},
                 {"name": "Momentum push beyond the slow EMA", "ok": True},
                 {"name": "Pullback touches the fast EMA zone", "ok": status == "new_setup"},
                 {"name": "Extra trend EMA filter (off)", "ok": True}]
    return {"available": True, "status": status, "headline": status, "close": close, "entry": close, "stop_loss": stop,
            "take_profit": target, "atr": 2.0, "rsi": 55.0, "checklist": checklist, "bias": {"label": "bullish"},
            "last_closed_candle": "2026-09-14 12:00", "detail": {"buy_zone": [98.0, 101.0]} if status == "waiting_pullback" else {},
            "nearest_support": {"name": "prev low", "price": 95.0}, "nearest_resistance": None}


def h4_candles(rows, start="2026-09-14 12:00"):
    times = pd.date_range(pd.Timestamp(start, tz="UTC"), periods=len(rows), freq="4h")
    return pd.DataFrame({"datetime": times, "open": [r[0] for r in rows], "high": [r[1] for r in rows],
                         "low": [r[2] for r in rows], "close": [r[3] for r in rows]})


def agent_research(plan, bars=None, next_open=None, in_window=False):
    news = {"available": True, "in_window": in_window, "next_high_impact": None, "high_impact_next_24h": [],
            "events_in_window": [{"title": "Federal Funds Rate", "minutes_to": 10, "time_utc": "2026-09-14 16:10"}] if in_window else []}
    return {"available": True, "symbol": "XAUUSD", "plan": plan,
            "closed_bars": bars if bars is not None else h4_candles([(99, 101, 98, 100)]),
            "last_closed_bar": plan["last_closed_candle"], "signal_high": 101.0,
            "next_open": next_open if next_open is not None else {"time": "2026-09-14 16:00", "price": 100.0},
            "data_fresh": True, "news": news, "atomic": {"available": False, "reason": "test"}}


def test_new_setup_opens_a_paper_buy_risking_one_percent(tmp_path):
    state = da.ledger_state(tmp_path, "2026-09-14 16:05")
    entry = da.decide("XAUUSD", agent_research(agent_plan()), state, "2026-09-14 16:05")
    assert entry["decision"] == "BUY" and entry["action"] == "opened"
    pos = state["positions"]["XAUUSD"]
    assert pos["entry"] == 100.0 and pos["stop"] == 94.0 and pos["target"] == 118.0
    assert abs(pos["risk_money"] - 100.0) < 1e-9 and abs(pos["units"] - 100.0 / 6.0) < 1e-6
    assert state["balance"] == da.START_BALANCE  # money only moves when the trade closes
    assert any("setup confirmed" in r for r in entry["reasons"])


def test_news_window_skips_the_entry_and_still_journals_it(tmp_path):
    state = da.ledger_state(tmp_path, "2026-09-14 16:05")
    entry = da.decide("XAUUSD", agent_research(agent_plan(), in_window=True), state, "2026-09-14 16:05")
    assert entry["decision"] == "SKIP_NEWS" and entry["action"] == "none"
    assert "XAUUSD" not in state["positions"]
    assert any("Federal Funds Rate" in r for r in entry["reasons"])


def test_same_candle_is_decided_only_once(tmp_path):
    state = da.ledger_state(tmp_path, "2026-09-14 16:05")
    first = da.decide("XAUUSD", agent_research(agent_plan("no_trade")), state, "2026-09-14 16:05")
    again = da.decide("XAUUSD", agent_research(agent_plan("no_trade")), state, "2026-09-14 17:05")
    assert first["decision"] == "NO_TRADE" and again is None
    assert any("no setup" in r for r in first["reasons"])


def test_stop_hit_closes_with_spread_and_swap_and_updates_balance(tmp_path):
    state = da.ledger_state(tmp_path, "2026-09-14 16:05")
    da.decide("XAUUSD", agent_research(agent_plan()), state, "2026-09-14 16:05")
    # the 16:00 fill candle and 20:00 candle close quietly; the 00:00 UTC candle (after the 17:00 New York rollover,
    # so one night of swap) trades through the stop
    bars = h4_candles([(99, 101, 98, 100), (100, 101, 99, 100.5), (100.5, 101, 99.5, 100), (100, 100.2, 93.0, 93.5)])
    plan = agent_plan("no_trade")
    plan["last_closed_candle"] = "2026-09-15 00:00"
    entry = da.decide("XAUUSD", agent_research(plan, bars=bars), state, "2026-09-15 04:05")
    assert entry["decision"] == "CLOSE"
    trade = state["closed_trades"][-1]
    assert trade["exit_reason"] == "stop" and trade["exit_price"] == 94.0
    assert trade["gross"] < 0 and trade["spread_cost"] > 0 and trade["nights"] >= 1 and trade["swap_cost"] > 0
    assert round(state["balance"], 2) == round(da.START_BALANCE + trade["net"], 2)
    assert "XAUUSD" not in state["positions"]


def test_waiting_pullback_explains_what_is_missing(tmp_path):
    state = da.ledger_state(tmp_path, "2026-09-14 16:05")
    entry = da.decide("XAUUSD", agent_research(agent_plan("waiting_pullback")), state, "2026-09-14 16:05")
    assert entry["decision"] == "WAIT"
    assert any("98.0-101.0" in r for r in entry["reasons"])


def test_run_writes_journal_state_and_markdown(tmp_path):
    def empty_loader(symbol, timeframe):
        return pd.DataFrame()  # no candles: every symbol is journalled as NO_DATA and nothing crashes
    out = da.run_agent(now="2026-09-14 16:05", data_dir=tmp_path, loader=empty_loader, calendar={"available": False}, atomic_dir=tmp_path)
    assert out["places_orders"] is False and out["mode"] == "paper"
    journal = da.read_journal(tmp_path)
    assert {e["symbol"] for e in journal} == set(da.WATCHLIST) and all(e["decision"] == "NO_DATA" for e in journal)
    assert (tmp_path / "daily_agent" / "journal" / "2026-09-14.md").exists()
    assert da.summary(tmp_path)["available"] is True


def test_agent_has_no_order_path():
    source = Path(da.__file__).read_text(encoding="utf-8")
    for forbidden in ("MetaTrader5", "mt5_service", "mt4_service", "order_send", "place_market_order", "demo_executor", "alpaca"):
        assert forbidden not in source
