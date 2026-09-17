"""/api/signals/live: the live payload (not the history file) with each row's signal bar time and age.

/api/signals returns stored history rows, which only change when a signal changes, so it cannot show whether the live
prediction is fresh. This read-only endpoint exposes the freshness fields added by execution safety item 4.
"""
from datetime import datetime, timedelta, timezone

import app as app_module


def test_live_endpoint_reports_freshness_of_each_symbol(monkeypatch):
    now = datetime.now(timezone.utc)
    bar = (now - timedelta(minutes=70)).replace(second=0, microsecond=0)
    rows = [{"symbol": "XAUUSD", "signal": "HOLD", "ensemble_probability": 0.51,
             "signal_bar_time": bar.strftime("%Y-%m-%d %H:%M:%S"), "signal_bar_age_minutes": 70.0,
             "generated_at": now.strftime("%Y-%m-%d %H:%M:%S")}]
    monkeypatch.setattr(app_module, "build_signal_payload", lambda: rows)
    response = app_module.app.test_client().get("/api/signals/live")
    assert response.status_code == 200
    body = response.get_json()
    assert body["max_bar_age_minutes"] == 120
    item = body["signals"][0]
    assert item["symbol"] == "XAUUSD" and item["signal_bar_time"] == rows[0]["signal_bar_time"]
    assert item["fresh"] is True and body["all_fresh"] is True


def test_stale_or_missing_bar_time_is_not_fresh(monkeypatch):
    rows = [{"symbol": "XAUUSD", "signal": "BUY", "signal_bar_time": None, "signal_bar_age_minutes": None},
            {"symbol": "BTCUSD", "signal": "SELL", "signal_bar_time": "2026-09-14 10:00:00", "signal_bar_age_minutes": 900.0}]
    monkeypatch.setattr(app_module, "build_signal_payload", lambda: rows)
    body = app_module.app.test_client().get("/api/signals/live").get_json()
    assert [s["fresh"] for s in body["signals"]] == [False, False]
    assert body["all_fresh"] is False


def test_model_evidence_carries_the_engine_numbers_not_a_verdict():
    """The Signal Center shows the probability against the engine's own thresholds, or says it has none."""
    from src.signal_engine import SIGNAL_CONFIGS

    live = SIGNAL_CONFIGS["live"]
    evidence = app_module._model_evidence({"ensemble_probability": 0.5239, "rf_probability": 0.4978,
                                           "lstm_probability": 0.55, "signal_bar_time": "2026-09-17 19:00:00",
                                           "signal_bar_age_minutes": 68.7})
    assert evidence["available"] and evidence["ensemble_probability"] == 0.5239
    assert evidence["buy_threshold"] == live["buy_threshold"] and evidence["sell_threshold"] == live["sell_threshold"]
    assert evidence["distance_to_nearer_threshold"] == 0.0261, "distance to the nearer of the two lines"
    assert evidence["signal_bar_age_minutes"] == 68.7

    missing = app_module._model_evidence(None)
    assert missing["available"] is False and "no live signal record" in missing["reason"]
    assert missing["buy_threshold"] == live["buy_threshold"], "the thresholds are still stated when the row is missing"
