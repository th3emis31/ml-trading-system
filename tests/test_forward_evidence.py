"""The system's memory of its own trades: the maths, the verdict thresholds, and the boundaries.

The point of this record is that it cannot flatter anything. These tests check the three ways it
could go wrong: counting a trade that has not closed, reaching a verdict on too little evidence, and
scoring an expert this system does not run.
"""
import json

import pytest

from src import forward_evidence as fe


def _state_file(trades: dict) -> dict:
    return {"trades": trades}


def _closed_trade(rid: str, r: float, symbol: str = "XAUUSD") -> dict:
    return {"id": rid, "symbol": symbol, "status": "closed", "r_result": r, "side": "BUY"}


# --- the arithmetic ---------------------------------------------------------------

def test_score_of_nothing_says_nothing_rather_than_zero_expectancy():
    stats = fe.score([])
    assert stats["trades"] == 0
    assert stats["expectancy_r"] is None
    assert stats["win_rate_pct"] is None
    assert stats["profit_factor"] is None


def test_profit_factor_and_expectancy_are_the_usual_definitions():
    stats = fe.score([2.0, -1.0, 2.0, -1.0])
    assert stats["profit_factor"] == 2.0          # 4 won / 2 lost
    assert stats["expectancy_r"] == 0.5
    assert stats["win_rate_pct"] == 50.0
    assert stats["total_r"] == 2.0


def test_drawdown_is_measured_in_r_from_the_running_peak():
    stats = fe.score([1.0, 1.0, -3.0, 0.5])
    assert stats["max_drawdown_r"] == 3.0


def test_a_run_of_only_winners_has_no_finite_profit_factor():
    stats = fe.score([1.0, 2.0])
    assert stats["profit_factor"] == float("inf")


def test_the_expectancy_interval_narrows_as_trades_accumulate():
    few = fe.score([1.0, -1.0, 1.0, -1.0])
    many = fe.score([1.0, -1.0] * 50)
    width_few = few["expectancy_95_high"] - few["expectancy_95_low"]
    width_many = many["expectancy_95_high"] - many["expectancy_95_low"]
    assert width_many < width_few


# --- the verdict ------------------------------------------------------------------

def test_a_verdict_needs_thirty_trades():
    stats = fe.score([1.0] * (fe.MIN_TRADES - 1))
    v = fe.verdict(stats)
    assert v["status"] == "collecting"
    assert v["needed"] == 1


def test_a_profitable_run_is_confirmed_only_once_the_interval_clears_zero():
    v = fe.verdict(fe.score([1.0, -0.5] * 40))
    assert v["status"] == "confirmed"
    assert "95 %" in v["why"]


def test_a_losing_run_is_refuted_on_profit_factor():
    v = fe.verdict(fe.score([0.5, -1.0] * 20))
    assert v["status"] == "refuted"
    assert "profit factor" in v["why"]


def test_a_noisy_break_even_run_is_inconclusive_not_confirmed():
    """The trap this avoids: calling a coin flip an edge because the total happens to be positive."""
    values = ([3.0, -1.0, -1.0, -1.0] * 10)[:40]
    v = fe.verdict(fe.score(values))
    assert v["status"] in ("inconclusive", "refuted")
    assert v["status"] != "confirmed"


def test_a_deep_forward_drawdown_is_refuted_even_when_profitable():
    values = [-1.0] * 15 + [2.0] * 25          # recovers, but the hole was 15 R deep
    v = fe.verdict(fe.score(values))
    assert v["status"] == "refuted"
    assert "drawdown" in v["why"]


# --- reading the real state files -------------------------------------------------

def test_open_trades_are_not_counted_as_evidence(tmp_path):
    """The breakout's live BTC position must not be scored until it closes."""
    folder = tmp_path / "paper_trading"
    folder.mkdir(parents=True)
    (folder / "demo_volatility_breakout_state.json").write_text(json.dumps(_state_file({
        "a": {"id": "a", "status": "open", "symbol": "BTCUSD"},
        "b": _closed_trade("b", 1.5),
    })), encoding="utf-8")
    (folder / "demo_session_pullback_state.json").write_text(json.dumps(_state_file({})), encoding="utf-8")

    report = fe.collect(tmp_path)
    breakout = next(s for s in report["sources"] if s["magic"] == 440603)
    assert breakout["stats"]["trades"] == 1          # only the closed one
    assert breakout["open_trades"] == 1
    assert report["totals"]["closed_forward_trades"] == 1


def test_a_missing_state_file_is_no_evidence_not_an_error(tmp_path):
    report = fe.collect(tmp_path)
    assert report["totals"]["closed_forward_trades"] == 0
    for source in report["sources"]:
        assert source["state_present"] is False
        assert source["verdict"]["status"] == "collecting"


def test_a_cancelled_trade_is_not_a_loss(tmp_path):
    """demo_session_pullback writes status 'cancelled_by_stop'; that is not a closed trade."""
    folder = tmp_path / "paper_trading"
    folder.mkdir(parents=True)
    (folder / "demo_session_pullback_state.json").write_text(json.dumps(_state_file({
        "x": {"id": "x", "status": "cancelled_by_stop", "r_result": -1.0},
    })), encoding="utf-8")
    report = fe.collect(tmp_path)
    pullback = next(s for s in report["sources"] if s["magic"] == 440502)
    assert pullback["stats"]["trades"] == 0


def test_a_trade_with_no_r_result_is_skipped_rather_than_guessed(tmp_path):
    folder = tmp_path / "paper_trading"
    folder.mkdir(parents=True)
    (folder / "demo_volatility_breakout_state.json").write_text(json.dumps(_state_file({
        "a": {"id": "a", "status": "closed"},               # no r_result
        "b": _closed_trade("b", 2.0),
    })), encoding="utf-8")
    report = fe.collect(tmp_path)
    breakout = next(s for s in report["sources"] if s["magic"] == 440603)
    assert breakout["stats"]["trades"] == 1
    assert breakout["stats"]["total_r"] == 2.0


# --- the boundaries --------------------------------------------------------------

def test_only_this_systems_own_magics_are_tracked():
    """The owner's standing rule: other experts are never journalled or scored here."""
    magics = {s["magic"] for s in fe.SOURCES}
    assert magics == {440502, 440603}


def test_every_watchlist_candidate_cites_the_evidence_row_that_justifies_it():
    assert fe.WATCHLIST, "the watchlist is the link from research to forward proof"
    for candidate in fe.WATCHLIST:
        assert "BASELINE" in candidate["evidence"]
        assert candidate["why_watch"]
        assert candidate["key"] and candidate["label"]


def test_a_running_forward_test_places_nothing_and_has_no_verdict_yet():
    """Against the real data directory: the paper tests are live, and must still place nothing."""
    report = fe.collect()
    for candidate in report["watchlist"]:
        assert candidate["verdict"]["status"] == "collecting"
        if candidate["forward_test_running"]:
            assert candidate["places_orders"] is False, "a paper forward test must never place orders"
            assert "places nothing" in candidate["note"]


def test_the_thresholds_are_stricter_than_the_confirm_level_is_generous():
    assert fe.CONFIRM_PROFIT_FACTOR > 1.0
    assert fe.REFUTE_PROFIT_FACTOR < 1.0
    assert fe.MIN_TRADES >= 30


def test_the_module_cannot_trade_or_open_its_own_broker_connection():
    text = open(fe.__file__.replace(".pyc", ".py"), encoding="utf-8").read()
    for forbidden in ("order_send", "OrderSend", "place_order", "auto_execute",
                      "MetaTrader5", "initialize()", "demo_executor"):
        assert forbidden not in text, f"{forbidden} must not appear in a read-only record"


# --- the watchlist reads the running paper forward tests --------------------------

def test_the_watchlist_reads_the_forward_test_state_and_counts_only_closed_trades(tmp_path):
    """src/crt_forward.py writes closed_trades with a net_r each; that is the evidence."""
    folder = tmp_path / "strategy_lab"
    folder.mkdir(parents=True)
    (folder / "forward_trendline_break_btc_4h.json").write_text(json.dumps({
        "places_orders": False, "start_at": "2026-09-19 10:30",
        "closed_trades": [{"entry_time": "2026-09-19 12:00", "net_r": 1.4},
                          {"entry_time": "2026-09-19 16:00", "net_r": -1.0}],
        "open_position": {"side": "BUY"},
    }), encoding="utf-8")
    report = fe.collect(tmp_path)
    btc = next(c for c in report["watchlist"] if c["key"] == "aurum_mechanism_btc_4h")
    assert btc["forward_test_running"] is True
    assert btc["places_orders"] is False
    assert btc["stats"]["trades"] == 2
    assert btc["stats"]["total_r"] == 0.4
    assert btc["collecting_since"] == "2026-09-19 10:30"
    assert btc["verdict"]["status"] == "collecting"


def test_a_watchlist_candidate_with_no_state_file_says_no_test_is_running(tmp_path):
    report = fe.collect(tmp_path)
    for candidate in report["watchlist"]:
        assert candidate["forward_test_running"] is False
        assert "no forward test is running" in candidate["note"]


def test_a_forward_test_that_claimed_to_place_orders_would_be_visible(tmp_path):
    """places_orders is surfaced, not assumed: a paper test that started trading must be obvious."""
    folder = tmp_path / "strategy_lab"
    folder.mkdir(parents=True)
    (folder / "forward_morning_star_xau_4h.json").write_text(json.dumps({
        "places_orders": True, "start_at": "2026-09-19 10:30", "closed_trades": [],
    }), encoding="utf-8")
    report = fe.collect(tmp_path)
    star = next(c for c in report["watchlist"] if c["key"] == "morning_star_xauusd_4h")
    assert star["places_orders"] is True


def test_every_watchlist_candidate_names_a_state_file_the_forward_tester_writes():
    from src import crt_forward as cf

    written = {str(c["state"]).replace("\\", "/").split("strategy_lab/")[-1] for c in cf.CANDIDATES.values()}
    for candidate in fe.WATCHLIST:
        name = candidate["state"].split("/")[-1]
        assert name in written, f"{name} is not written by any crt_forward candidate"
