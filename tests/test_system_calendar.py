"""One row per day for the whole system. It reads; it never writes, never trades, never smooths."""
import json
from datetime import date

import pytest

from src import system_calendar as cal


@pytest.fixture()
def sources(tmp_path, monkeypatch):
    data = tmp_path / "data"
    (data / "strategy_lab").mkdir(parents=True)
    (data / "paper_trading").mkdir()
    (data / "system_health").mkdir()
    (data / "daily_reports").mkdir()
    (data / "trade_memory").mkdir()
    memory = tmp_path / "memory"
    memory.mkdir()

    (data / "learning_decisions.json").write_text(json.dumps([
        {"symbol": "XAUUSD", "trained_at": "2026-09-20 05:30:00", "rf_promoted": False, "data_source": "broker"},
        {"symbol": "XAUUSD", "trained_at": "2026-09-21 05:30:00", "rf_promoted": True, "data_source": "broker"},
    ]), encoding="utf-8")
    (data / "signals.json").write_text(json.dumps([
        {"generated_at": "2026-09-20 10:00:00", "outcome": "WIN"},
        {"generated_at": "2026-09-20 11:00:00", "outcome": "LOSS"},
        {"generated_at": "2026-09-20 12:00:00", "outcome": None},
    ]), encoding="utf-8")
    (data / "strategy_lab" / "registry.json").write_text(json.dumps(
        {"candidates": {"a": {"evaluated_at": "2026-09-21 09:00:00"}}}), encoding="utf-8")
    (data / "strategy_lab" / "forward_thing.json").write_text(json.dumps(
        {"closed_trades": [{"exit_time": "2026-09-21 14:00:00", "net_r": 1.5}]}), encoding="utf-8")
    (data / "paper_trading" / "demo_volatility_breakout.log").write_text(
        "2026-09-21 09:03:01 no_setup: nothing\n"
        "2026-09-21 10:03:01 opened: BUY opened\n", encoding="utf-8")
    (data / "system_health" / "doctor_history.json").write_text(json.dumps(
        [{"generated_at": "2026-09-21 06:00:00", "overall": "warnings"}]), encoding="utf-8")
    (data / "daily_reports" / "2026-09-21.json").write_text("{}", encoding="utf-8")
    # An earlier row so the window contains a day that IS recording but had no activity - otherwise
    # there is no quiet day to test, only days from before recording began.
    (memory / "BASELINE.md").write_text(
        "| date | commit |\n|---|---|\n"
        "| 2026-09-18 | old | an earlier recorded result |\n"
        "| 2026-09-21 | abc | a recorded result |\n", encoding="utf-8")

    monkeypatch.setattr(cal, "smartentry_data_dir", lambda: data)
    monkeypatch.setattr(cal, "MEMORY_DIR", memory)
    return data


def test_it_assembles_every_kind_of_work_into_one_day(sources):
    out = cal.build_calendar(days=3, today=date(2026, 9, 21))
    day = next(r for r in out["rows"] if r["date"] == "2026-09-21")
    assert day["learning_runs"] == 1 and day["promotions"] == 1
    assert day["research_results"] == 1 and day["candidates_evaluated"] == 1
    assert day["cycles"] == 2 and day["orders_placed"] == 1
    assert day["trades_closed"] == 1 and day["net_r"] == 1.5
    assert day["health"] == "warnings" and day["report"] is True
    assert day["learning_sources"] == ["broker"]


def test_a_quiet_day_is_reported_as_zero_not_smoothed(sources):
    out = cal.build_calendar(days=3, today=date(2026, 9, 21))
    day = next(r for r in out["rows"] if r["date"] == "2026-09-19")
    assert day["recording"] is True, "the system WAS recording, it just did nothing"
    assert day["learning_runs"] == 0 and day["cycles"] == 0 and day["activity"] == 0


def test_before_the_first_record_is_not_the_same_as_a_quiet_day(sources):
    """'We were not recording yet' and 'nothing happened' are different facts."""
    out = cal.build_calendar(days=400, today=date(2026, 9, 21))
    early = next(r for r in out["rows"] if r["date"] == "2025-09-21")
    assert early["recording"] is False
    assert "learning_runs" not in early, "an unrecorded day must not claim a zero it cannot know"


def test_signal_outcomes_split_win_loss_and_unresolved(sources):
    out = cal.build_calendar(days=3, today=date(2026, 9, 21))
    day = next(r for r in out["rows"] if r["date"] == "2026-09-20")
    assert day["signals"] == 3
    assert day["signal_wins"] == 1 and day["signal_losses"] == 1 and day["signal_unresolved"] == 1


def test_heat_counts_kinds_of_work_not_volume(sources):
    """A day that scored 4,000 candidates and nothing else is not busier than a day that did four things."""
    many = {"learning_runs": 0, "research_results": 0, "candidates_evaluated": 4000,
            "signals": 0, "cycles": 0, "orders_placed": 0, "trades_closed": 0}
    varied = {"learning_runs": 1, "research_results": 1, "candidates_evaluated": 0,
              "signals": 1, "cycles": 0, "orders_placed": 0, "trades_closed": 1}
    assert cal.day_activity(many) == 1
    assert cal.day_activity(varied) == 4


def test_totals_count_only_recording_days(sources):
    out = cal.build_calendar(days=400, today=date(2026, 9, 21))
    assert out["totals"]["days_recording"] < 400
    assert out["totals"]["promotions"] == 1
    assert out["totals"]["trades_closed"] == 1 and out["totals"]["net_r"] == 1.5
    assert out["totals"]["days_with_a_closed_trade"] == 1


def test_backtests_never_count_as_trades(sources):
    """net_r is closed forward and demo trades only - a backtest result is not money."""
    out = cal.build_calendar(days=3, today=date(2026, 9, 21))
    assert "backtest" not in json.dumps(out["legend"]).lower() or "never counted" in out["legend"]["net_r"]
    day = next(r for r in out["rows"] if r["date"] == "2026-09-21")
    assert day["research_results"] == 1 and day["net_r"] == 1.5, "research and money are separate columns"


def test_it_cannot_trade_or_write():
    text = open(cal.__file__.replace(".pyc", ".py"), encoding="utf-8").read()
    for forbidden in ("order_send", "OrderSend", "place_order", "auto_execute", "MetaTrader5", "write_text"):
        assert forbidden not in text
