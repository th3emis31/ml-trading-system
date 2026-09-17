"""The MT5 Atomic Analyst panel as read-only evidence: reading, staleness, agreement with the system plan, endpoint."""
import json

import pandas as pd
import pytest

from src import atomic_analyst as aa

NOW = pd.Timestamp("2026-09-17 18:30", tz="UTC")


def write_panel(directory, symbol="XAUUSD", generated="2026.09.17 18:29:00", direction="BUY", verdict="BUY ALIGNED"):
    payload = {"source": "ATOMIC_ANALYST_V85", "symbol": symbol, "account": "25446287", "timeframe": "PERIOD_H1",
               "generatedAt": generated, "verdict": verdict, "direction": direction, "confidence": 52.9,
               "mtfAligned": True, "dominance": {"bullish": 52.9, "wait": 41.2, "bearish": 5.9},
               "mtf": {"H1": "WAIT"}, "consensus": {"rsi": "BUY"}, "indicators": {"rsi": 59.7},
               "atomic": {"trend": "BULL", "trailingStop": 4332.81},
               "ticket": {"direction": "SELL", "barTime": "2026.09.16 22:00", "entry": 4272.13, "sl": 4307.97,
                          "tp1": 4249.98, "tp2": 4236.29, "tp3": 4214.15, "status": 2, "tpHits": 0},
               "chartStats": {"signalsBuy": 16, "signalsSell": 17, "closedTrades": 33, "wins": 14, "profitFactor": 1.371,
                              "note": "on-chart simulation, spread only, no swap; not evidence"},
               "feedsTheGate": False}
    (directory / f"{symbol}.json").write_text(json.dumps(payload), encoding="utf-8")
    return payload


def test_reads_the_panel_and_flags_a_stale_file(tmp_path, monkeypatch):
    monkeypatch.setattr(aa, "panel_dir", lambda: tmp_path)
    write_panel(tmp_path)
    panel = aa.read_panel("XAUUSD", NOW)
    assert panel["available"] and panel["fresh"] and panel["age_minutes"] == pytest.approx(1.0)
    assert panel["verdict"] == "BUY ALIGNED" and panel["confidence"] == 52.9 and panel["feeds_the_gate"] is False
    assert panel["last_ticket"]["entry"] == 4272.13 and panel["chart_sim"]["profitFactor"] == 1.371

    write_panel(tmp_path, generated="2026.09.17 16:00:00")
    stale = aa.read_panel("XAUUSD", NOW)
    assert stale["available"] and stale["fresh"] is False and "chart open" in stale["stale_reason"]

    missing = aa.read_panel("BTCUSD", NOW)
    assert missing["available"] is False and "no panel file" in missing["reason"]


def test_agreement_states_cover_both_sides_and_waiting(tmp_path, monkeypatch):
    monkeypatch.setattr(aa, "panel_dir", lambda: tmp_path)
    write_panel(tmp_path)
    panel = aa.read_panel("XAUUSD", NOW)
    armed = {"armed_now": {"trigger": 4396.99}}
    assert aa.agreement(panel, armed)["state"] == "agree (both long)"
    assert "4396.99" in aa.agreement(panel, armed)["detail"]
    assert aa.agreement(panel, {"armed_now": None})["state"] == "panel long, system waiting"
    write_panel(tmp_path, direction="SELL", verdict="SELL ALIGNED")
    assert aa.agreement(aa.read_panel("XAUUSD", NOW), armed)["state"].startswith("disagree")
    write_panel(tmp_path, direction="WAIT", verdict="WAIT")
    assert aa.agreement(aa.read_panel("XAUUSD", NOW), armed)["state"] == "panel waiting"
    assert aa.agreement({"available": False, "reason": "gone"}, armed)["state"] == "no panel"


def test_overview_and_endpoint_never_feed_orders(tmp_path, monkeypatch):
    import app as app_module
    from src import system_doctor

    monkeypatch.setattr(aa, "panel_dir", lambda: tmp_path)
    write_panel(tmp_path)
    write_panel(tmp_path, symbol="BTCUSD", direction="WAIT", verdict="WAIT")
    body = app_module.app.test_client().get("/api/atomic-analyst").get_json()
    assert body["feeds_orders"] is False and "never" in body["note"]
    assert set(body["symbols"]) == {"XAUUSD", "BTCUSD"}
    assert body["symbols"]["XAUUSD"]["panel"]["verdict"] == "BUY ALIGNED"
    assert body["symbols"]["BTCUSD"]["agreement"]["state"] == "panel waiting"
    assert system_doctor.check_atomic_analyst()["status"] in ("ok", "warn")
