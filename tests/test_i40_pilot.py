"""i40 Pilot, the system brain: the signature rules, the memory read, the live context, the loop and the endpoint.

The brain's whole value is that it does not invent: every test here checks either a real reading or an honest gap.
"""
import json

from src import i40_pilot as pilot

CSV = ('"HostName","TaskName","Next Run Time","Status","Last Run Time","Last Result"\n'
       '"PC","\\SmartEntry Daily Learning","2026-09-18 05:30:00","Ready","2026-09-17 05:30:00","0"\n')


def test_signature_rules_are_stated_with_a_reason_and_never_empty():
    """Rules carry the why, because a rule without one gets argued away the first time it is inconvenient."""
    assert len(pilot.SIGNATURE_RULES) >= 8
    for rule in pilot.SIGNATURE_RULES:
        assert rule["rule"] and rule["why"] and rule["since"], rule
    text = " ".join(r["rule"] for r in pilot.SIGNATURE_RULES)
    assert "0.95" in text and "11581419" in text and "HOLD" in text, "the money rules must be in the list"
    assert pilot.identity()["places_orders"] is False and pilot.identity()["reads_only"] is True


def test_memory_counts_results_and_says_when_there_is_no_folder(tmp_path):
    (tmp_path / "BASELINE.md").write_text("| date | run | result |\n|---|---|---|\n| 2026-09-16 | a | FAIL |\n"
                                          "| 2026-09-17 | b | PASS |\n", encoding="utf-8")
    (tmp_path / "NOTES.md").write_text("- first\n- second\n", encoding="utf-8")
    (tmp_path / "LESSONS.md").write_text("# one lesson\ntext\n# another\n", encoding="utf-8")
    (tmp_path / "BACKLOG.md").write_text("- [ ] open item\n- [x] done item\n", encoding="utf-8")
    memory = pilot.memory(tmp_path)
    assert memory["available"] and memory["baseline_rows"] == 2, "the header row is not a result"
    assert memory["lesson_count"] == 2 and memory["open_backlog_count"] == 1
    assert memory["recent_notes"][-1] == "- second"
    missing = pilot.memory(tmp_path / "nothing")
    assert missing["available"] is False and "no memory folder" in missing["reason"]


def test_skills_and_schedule_read_the_real_shapes(tmp_path):
    skill = tmp_path / "verify" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: verify\ndescription: check the diff before committing\n---\n# Verify\n", encoding="utf-8")
    found = pilot.skills(tmp_path)
    assert found["available"] and found["count"] == 1
    assert found["skills"][0]["name"] == "verify" and "diff" in found["skills"][0]["description"]

    schedule = pilot.schedule(CSV)
    assert schedule["available"] and schedule["count"] >= 10
    learning = next(t for t in schedule["tasks"] if t["task"] == "SmartEntry Daily Learning")
    assert learning["registered"] and learning["last_run"] == "2026-09-17 05:30:00"
    assert "SmartEntry i40 Pilot" in schedule["missing"], "a task absent from Task Scheduler is reported missing"


def test_learning_state_takes_the_newest_row_per_symbol(tmp_path):
    path = tmp_path / "learning_decisions.json"
    path.write_text(json.dumps([
        {"symbol": "XAUUSD", "status": "trained", "rf_promoted": False, "lstm_promoted": False, "accuracy": 0.51},
        {"symbol": "XAUUSD", "status": "trained", "rf_promoted": True, "lstm_promoted": False, "accuracy": 0.54},
        {"symbol": "BTCUSD", "status": "skipped", "rf_promoted": False, "lstm_promoted": False, "accuracy": None},
    ]), encoding="utf-8")
    state = pilot.learning_state(path)
    assert state["available"] and state["decisions"] == 3
    assert state["per_symbol"]["XAUUSD"]["rf_promoted"] is True and state["per_symbol"]["XAUUSD"]["accuracy"] == 0.54
    assert state["per_symbol"]["BTCUSD"]["status"] == "skipped"
    assert pilot.learning_state(tmp_path / "gone.json")["available"] is False


def test_context_reports_a_dead_endpoint_instead_of_inventing_a_strategy(tmp_path, monkeypatch):
    monkeypatch.setattr(pilot, "smartentry_data_dir", lambda: tmp_path)
    monkeypatch.setattr(pilot, "smartentry_models_dir", lambda: tmp_path / "models")

    def get(path, timeout=0):
        if path == "/api/demo-trading/status":
            return 200, {"magic": 440502, "symbol": "XAUUSD", "sending_orders": False, "halted": None,
                         "account": {"available": True, "balance": 100.0}, "cycle_health": {"available": True},
                         "expectancy": {}, "last_cycle": {"at": "2026-09-17 19:01:08"}, "backtest_verdict": "FAIL"}
        return None, {"error": "connection refused"}

    context = pilot.context(get)
    pullback = context["strategies"]["gold_session_pullback"]
    assert pullback["available"] and pullback["sending_orders"] is False and pullback["magic"] == 440502
    breakout = context["strategies"]["volatility_trend_breakout"]
    assert breakout["available"] is False and "returned None" in breakout["reason"]
    assert context["models"]["available"] is False and "no model files" in context["models"]["reason"]


def test_loop_flags_a_brief_that_stopped_refreshing(tmp_path):
    import os
    from datetime import datetime, timedelta, timezone

    path = tmp_path / "latest.json"
    assert pilot.loop(path=path)["available"] is False, "nothing written yet is a state, not an error"
    path.write_text("{}", encoding="utf-8")
    now = datetime.now(timezone.utc)
    assert pilot.loop(now=now, path=path)["stale"] is False
    old = (now - timedelta(hours=4)).timestamp()
    os.utime(path, (old, old))
    stale = pilot.loop(now=now, path=path)
    assert stale["stale"] is True and stale["age_minutes"] > 2 * pilot.REFRESH_MINUTES


def test_endpoint_serves_the_brief_and_the_page_lists_the_rules(tmp_path, monkeypatch):
    import app as app_module

    monkeypatch.setattr(pilot, "pilot_dir", lambda: tmp_path)
    client = app_module.app.test_client()
    body = client.get("/api/i40-pilot").get_json()
    assert body["available"] is False and "SmartEntry i40 Pilot" in body["reason"]

    (tmp_path / "latest.json").write_text(json.dumps({"generated_at": "2026-09-17 22:00:00",
                                                      "identity": {"name": "i40 Pilot"},
                                                      "rules": {"count": 10, "signature_rules": []},
                                                      "tools": {"commands": [], "api_endpoints": []}}), encoding="utf-8")
    served = client.get("/api/i40-pilot").get_json()
    assert served["identity"]["name"] == "i40 Pilot"
    assert served["tools"]["api_endpoint_count"] > 50, "the live route table fills in the endpoints"

    html = client.get("/i40-pilot").get_data(as_text=True)
    for label in ("Signature rules", "Memory", "Schedule", "Skills", "Instructions", "Tools", "Context"):
        assert label in html, f"the page must show {label}"
