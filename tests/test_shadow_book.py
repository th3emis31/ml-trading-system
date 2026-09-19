"""Shadow forward tests for the strategy book: places nothing, starts fixed, reads honestly.

The three ways this could go wrong and quietly mislead: the start instant drifting so that already-fitted
bars creep into the "forward" record, a thin sample being presented as a finding, and one broken
strategy taking the rest of the run down with it.
"""
import json

import numpy as np
import pandas as pd
import pytest

from src import forward_evidence as fe
from src import shadow_book as sb
from src import strategy_lab as lab


def _book(entries: list) -> dict:
    return {"entries": {str(i): e for i, e in enumerate(entries)}}


def _entry(market="XAUUSD:4h", status="watchlist", eid="e1", family="donchian_breakout"):
    return {"id": eid, "market": market, "status": status, "family": family,
            "description": f"{family} on {market}",
            "spec": {"family": family, "params": {"side": "long", "lookback": 20, "trend_ema": 0},
                     "exits": {"stop": "atr", "sl_atr": 2.0, "rr": 2.0, "trail_atr": 0.0,
                               "max_bars": 50, "swing_lookback": 5}}}


def _shadow_bars(n=1200, start="2026-01-01", freq="4h", seed=3):
    rng = np.random.default_rng(seed)
    close = 4000 + np.cumsum(rng.normal(0, 12, n))
    return pd.DataFrame({"datetime": pd.date_range(start, periods=n, freq=freq, tz="UTC"),
                         "open": close, "high": close + 18, "low": close - 18, "close": close,
                         "volume": 1000.0})


# --- what is tracked -------------------------------------------------------------

def test_only_kept_strategies_are_tracked(tmp_path):
    book = tmp_path / "book.json"
    book.write_text(json.dumps(_book([
        _entry(eid="keep", status="watchlist"),
        _entry(eid="approved", status="approved_for_demo"),
        _entry(eid="gone", status="archived"),
        _entry(eid="down", status="demoted"),
    ])), encoding="utf-8")
    ids = {e["id"] for e in sb.book_entries(str(book))}
    assert ids == {"keep", "approved"}


def test_an_entry_without_a_spec_is_skipped_rather_than_guessed(tmp_path):
    book = tmp_path / "book.json"
    bad = _entry(eid="nospec")
    bad.pop("spec")
    book.write_text(json.dumps(_book([bad, _entry(eid="ok")])), encoding="utf-8")
    assert {e["id"] for e in sb.book_entries(str(book))} == {"ok"}


def test_a_missing_book_is_no_strategies_not_an_error(tmp_path):
    assert sb.book_entries(str(tmp_path / "absent.json")) == []


# --- the forward window ----------------------------------------------------------

def test_only_bars_after_the_start_instant_are_simulated():
    bars = _shadow_bars()
    market = lab.Market("XAUUSD", "4h", bars, swap=True)
    spec = _entry()["spec"]
    early = sb.simulate_since(market, spec, pd.Timestamp(bars["datetime"].iloc[400]))
    late = sb.simulate_since(market, spec, pd.Timestamp(bars["datetime"].iloc[1000]))
    assert early["bars_watched"] > late["bars_watched"]
    assert len(early["trades"]) >= len(late["trades"])


def test_a_start_after_the_last_bar_gives_no_trades_rather_than_an_error():
    bars = _shadow_bars()
    market = lab.Market("XAUUSD", "4h", bars, swap=True)
    out = sb.simulate_since(market, _entry()["spec"], pd.Timestamp("2030-01-01", tz="UTC"))
    assert out["trades"] == [] and out["r_values"] == []


def test_the_start_instant_is_fixed_on_first_sight_and_never_moves(tmp_path, monkeypatch):
    """If start_at drifted forward, bars the strategy was selected on would leak into the record."""
    book = tmp_path / "book.json"
    book.write_text(json.dumps(_book([_entry(eid="only")])), encoding="utf-8")
    state = tmp_path / "shadow.json"
    monkeypatch.setattr(sb, "load_bars_for_test", None, raising=False)
    monkeypatch.setattr("src.mtf_data.load_bars", lambda symbol, timeframe, source=None: _shadow_bars())

    first = sb.run(now="2026-06-01 00:00", book=str(book), state_path=str(state))
    second = sb.run(now="2026-07-15 00:00", book=str(book), state_path=str(state))
    assert first["strategies"]["only"]["start_at"] == second["strategies"]["only"]["start_at"]
    assert second["strategies"]["only"]["start_at"].startswith("2026-06-01")


def test_one_broken_strategy_does_not_stop_the_others(tmp_path, monkeypatch):
    book = tmp_path / "book.json"
    broken = _entry(eid="broken", family="no_such_family")
    book.write_text(json.dumps(_book([broken, _entry(eid="fine")])), encoding="utf-8")
    monkeypatch.setattr("src.mtf_data.load_bars", lambda symbol, timeframe, source=None: _shadow_bars())
    report = sb.run(now="2026-06-01 00:00", book=str(book), state_path=str(tmp_path / "s.json"))
    assert report["strategies"]["broken"]["available"] is False
    assert report["strategies"]["fine"]["available"] is True


def test_a_market_with_no_bars_is_reported_unavailable(tmp_path, monkeypatch):
    book = tmp_path / "book.json"
    book.write_text(json.dumps(_book([_entry(eid="nobars")])), encoding="utf-8")
    monkeypatch.setattr("src.mtf_data.load_bars", lambda symbol, timeframe, source=None: pd.DataFrame())
    report = sb.run(now="2026-06-01 00:00", book=str(book), state_path=str(tmp_path / "s.json"))
    assert report["strategies"]["nobars"]["available"] is False
    assert "no broker bars" in report["strategies"]["nobars"]["reason"]


# --- honesty --------------------------------------------------------------------

def test_the_report_states_it_places_nothing(tmp_path, monkeypatch):
    book = tmp_path / "book.json"
    book.write_text(json.dumps(_book([_entry(eid="x")])), encoding="utf-8")
    monkeypatch.setattr("src.mtf_data.load_bars", lambda symbol, timeframe, source=None: _shadow_bars())
    report = sb.run(now="2026-06-01 00:00", book=str(book), state_path=str(tmp_path / "s.json"))
    assert report["places_orders"] is False
    assert "no order is ever sent" in report["note"]


def test_a_thin_sample_carries_an_interval_that_spans_zero():
    """One outlier over four trades produces a huge mean; the interval is what stops it reading as truth."""
    stats = fe.score([23.0, 2.0, 3.0, 2.8])
    conf = fe.confidence(stats, 0.2)
    assert stats["expectancy_r"] > 7
    assert conf["interval_spans_zero"] is True
    assert "rather than a finding" in conf["reading"]


def test_every_tracked_strategy_gets_a_tier_and_a_confidence(tmp_path, monkeypatch):
    book = tmp_path / "book.json"
    book.write_text(json.dumps(_book([_entry(eid="a"), _entry(eid="b", market="BTCUSD:4h")])), encoding="utf-8")
    monkeypatch.setattr("src.mtf_data.load_bars", lambda symbol, timeframe, source=None: _shadow_bars())
    report = sb.run(now="2026-06-01 00:00", book=str(book), state_path=str(tmp_path / "s.json"))
    for record in report["strategies"].values():
        assert record["tier"]["blocked_from_learning"] is False
        assert record["confidence"]["reading"]
        assert record["tier"]["to_advance"]


def test_the_module_cannot_trade():
    text = open(sb.__file__.replace(".pyc", ".py"), encoding="utf-8").read()
    for forbidden in ("order_send", "OrderSend", "place_order", "auto_execute",
                      "MetaTrader5", "demo_executor"):
        assert forbidden not in text, f"{forbidden} must not appear in a shadow test"


def test_the_hourly_task_runs_the_shadow_book_after_the_book_update():
    """It only keeps learning if it keeps running; the schedule is part of the feature."""
    script = open("scripts/run_strategy_lab.cmd", encoding="utf-8", errors="replace").read()
    assert "src.strategy_book update" in script
    assert "src.shadow_book run" in script
    assert script.index("src.strategy_book update") < script.index("src.shadow_book run")
