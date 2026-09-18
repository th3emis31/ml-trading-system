"""Gold Reaper forward watch: matching the expert's deals, journalling each once, the record, and the evidence bar.

The watch exists to keep an honest forward record of someone else's expert, so the tests care about two things: that
it never counts a trade twice or claims one that is not the expert's, and that it refuses to call a small sample a
result.
"""
import json

from src import gold_reaper_watch as watch


def deal(ticket, profit=1.0, magic=8000, comment="The Gold Reaper_XAUUSD_6", symbol="XAUUSD", swap=0.0, commission=0.0):
    return {"ticket": ticket, "position_id": ticket, "time": 1789700000 + ticket * 3600, "symbol": symbol,
            "volume": 0.01, "price": 4300.0, "profit": profit, "swap": swap, "commission": commission, "fee": 0.0,
            "magic": magic, "comment": comment}


def responder(deals, ok=True):
    def get(path, timeout=0):
        return 200, {"ok": ok, "deals": deals}
    return get


def test_only_the_experts_own_deals_are_kept():
    config = dict(watch.DEFAULT_CONFIG)
    assert watch.matches(deal(1), config), "its magic number"
    assert watch.matches(deal(2, 1.0, magic=0), config), "or its comment"
    assert not watch.matches(deal(3, 1.0, magic=999, comment="other ea"), config), "another expert's trade is not ours"
    assert not watch.matches(deal(4, 1.0, symbol="BTCUSD"), config), "another symbol is not ours"
    assert watch.net_of(deal(5, 10.0, swap=-0.79, commission=-0.5)) == 8.71, "net is after swap and commission"


def test_each_trade_is_journalled_once_even_when_the_scan_repeats(tmp_path):
    path = tmp_path / "trades.jsonl"
    config = dict(watch.DEFAULT_CONFIG)
    deals = [deal(1, 12.0), deal(2, -8.0), deal(3, 5.0, magic=777, comment="someone else")]
    first = watch.collect(responder(deals), 30, config, path)
    assert first["available"] and first["added"] == 2 and first["scanned"] == 3
    again = watch.collect(responder(deals + [deal(4, 3.0)]), 30, config, path)
    assert again["added"] == 1, "only the new one"
    rows = watch.journal_rows(path)
    assert [r["ticket"] for r in rows] == [1, 2, 4] and len(rows) == 3

    dead = watch.collect(lambda p, t=0: (None, {"reason": "connection refused"}), 30, config, path)
    assert dead["available"] is False and "unavailable" in dead["reason"], "a missing app is a gap, not an empty result"


def test_record_and_evidence_bar_refuse_to_call_a_small_sample_a_result():
    empty = watch.forward_record([])
    assert empty["trades"] == 0 and "no trade" in empty["note"]
    assert watch.compare(empty)["stage"] == "not enough yet"

    rows = [{"net": 10.0, "closed_utc": "2026-09-18 10:00:00"}, {"net": -5.0, "closed_utc": "2026-09-18 11:00:00"},
            {"net": 20.0, "closed_utc": "2026-09-18 12:00:00"}]
    record = watch.forward_record(rows)
    assert record["trades"] == 3 and record["wins"] == 2 and record["net"] == 25.0
    assert record["profit_factor"] == 6.0 and record["average_trade"] == 8.33
    assert record["max_drawdown_money"] == 5.0 and record["largest_loss"] == -5.0
    assert record["win_rate_95_ci"][0] < record["win_rate_pct"] < record["win_rate_95_ci"][1], "a range, not a point"
    assert watch.compare(record)["stage"] == "not enough yet", "three trades prove nothing"

    interim = watch.compare(watch.forward_record([{"net": 1.0, "closed_utc": "x"}] * watch.INTERIM_TRADES))
    assert interim["stage"] == "interim" and str(watch.VERDICT_TRADES) in interim["verdict"]
    verdict = watch.compare(watch.forward_record([{"net": 1.0, "closed_utc": "x"}] * watch.VERDICT_TRADES))
    assert verdict["stage"] == "verdict"
    assert "commission" in verdict["reminder"], "the backtest's caveats travel with every comparison"


def test_status_and_endpoint_never_offer_to_trade(tmp_path, monkeypatch):
    import app as app_module

    monkeypatch.setattr(watch, "watch_dir", lambda: tmp_path)
    status = watch.build_status(responder([deal(1, 12.0)]), 30, dict(watch.DEFAULT_CONFIG), tmp_path / "trades.jsonl")
    assert status["places_orders"] is False and "never trades" in status["note"]
    assert status["backtest"]["trades"] == 279 and status["forward"]["trades"] == 1

    client = app_module.app.test_client()
    body = client.get("/api/gold-reaper").get_json()
    assert body["available"] is False and "SmartEntry Gold Reaper Watch" in body["reason"]
    watch.save_status(status, tmp_path / "latest.json")
    served = client.get("/api/gold-reaper").get_json()
    assert served["forward"]["trades"] == 1 and served["places_orders"] is False

    html = client.get("/gold-reaper").get_data(as_text=True)
    for label in ("Forward, on this account", "The vendor's backtest", "Comparison", "Recorded trades"):
        assert label in html
