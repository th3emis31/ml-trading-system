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
    # The REAL format, as LESSONS.md states in its own header: one lesson per BULLET. The previous
    # version of this test wrote "# one lesson" headings, so it passed against a counter that read 1
    # lesson where the file holds 17 - the test encoded the bug and protected it.
    (tmp_path / "LESSONS.md").write_text(
        "# Lessons (append-only)\n\nFormat: `- YYYY-MM-DD [area]: what went wrong`\n\n"
        "- 2026-09-12 [setup]: first thing that went wrong\n"
        "- 2026-09-13 [data]: second thing that went wrong\n"
        "- 2026-09-14 [costs]: third thing that went wrong\n", encoding="utf-8")
    (tmp_path / "BACKLOG.md").write_text("- [ ] open item\n- [x] done item\n", encoding="utf-8")
    memory = pilot.memory(tmp_path)
    assert memory["available"] and memory["baseline_rows"] == 2, "the header row is not a result"
    assert memory["lesson_count"] == 3, "one lesson per bullet, not per heading"
    assert memory["open_backlog_count"] == 1
    assert not any(line.startswith("- [ ]") for line in memory["lessons"]), "backlog items are not lessons"
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


def test_attention_names_what_is_wrong_and_stays_quiet_when_nothing_is():
    """Counting problems is useless; the page has to name them, worst first, with somewhere to look."""
    healthy = {"context": {"strategies": {"pullback": {"available": True, "halted": None, "sending_orders": False,
                                                       "cycle_health": {"available": True, "late": False},
                                                       "account": {"available": True, "is_demo": True}}},
                           "positioning": {"available": True}},
               "schedule": {"tasks": [{"task": "SmartEntry Demo Pullback", "registered": True, "last_result": "0"}],
                            "missing": []},
               "loop": {"available": True, "stale": False}}
    quiet = pilot.attention(healthy)
    assert quiet["count"] == 0 and quiet["worst"] == "ok" and "running" in quiet["headline"]

    broken = {"context": {"strategies": {
                  "pullback": {"available": True, "halted": {"kind": "daily_loss", "reason": "day loss 3 %"},
                               "cycle_health": {"available": True, "late": False},
                               "account": {"available": True, "is_demo": True}},
                  "breakout": {"available": True, "halted": None,
                               "cycle_health": {"available": True, "late": True, "age_minutes": 240,
                                                "note": "looks stopped"},
                               "account": {"available": True, "is_demo": False}}},
                  "positioning": {"available": False, "reason": "not downloaded"}},
              "schedule": {"tasks": [{"task": "SmartEntry Daily Learning", "registered": True, "last_result": "1"}],
                           "missing": ["SmartEntry i40 Pilot"]},
              "loop": {"available": True, "stale": True, "age_minutes": 300}}
    found = pilot.attention(broken)
    # halted, non-demo account, unregistered task (bad); late cycle, failed task, stale brief (warn); no positioning (info)
    assert found["worst"] == "bad" and found["count"] == 7
    assert [i["severity"] for i in found["items"]] == sorted([i["severity"] for i in found["items"]],
                                                            key=lambda s: pilot.ATTENTION_ORDER[s]), "worst first"
    what = " ".join(i["what"] for i in found["items"])
    assert "halted" in what and "non-demo account" in what and "not registered" in what and "brief is 300 min old" in what


def test_activity_merges_sources_newest_first_and_drops_never_run_tasks():
    brief = {"context": {"strategies": {"breakout": {"available": True, "recent_decisions": [
                 {"at": "2026-09-17 22:03:06", "event": "no_setup", "reason": "no breakout"},
                 {"at": "2026-09-17 21:03:05", "event": "no_setup", "reason": "no breakout"}]}},
                         "learning": {"available": True, "per_symbol": {
                             "XAUUSD": {"status": "trained", "rf_promoted": True, "lstm_promoted": False,
                                        "at": "2026-09-17 05:31:00"}}}},
             "schedule": {"tasks": [
                 {"task": "SmartEntry System Doctor", "registered": True, "last_run": "17/09/2026 23:28:01",
                  "last_result": "0"},
                 {"task": "SmartEntry i40 Pilot", "registered": True, "last_run": "30/11/1999 00:00:00",
                  "last_result": "267011"}]}}
    trail = pilot.activity(brief)
    stamps = [e["at"] for e in trail["events"]]
    assert stamps == sorted(stamps, reverse=True), "newest first, whatever the source wrote"
    assert "2026-09-17 23:28:01" in stamps, "a local dd/mm/yyyy task time is normalised"
    assert all("1999" not in s for s in stamps), "a task that never ran is not an event"
    assert {e["source"] for e in trail["events"]} == {"breakout", "schedule", "learning"}
    assert pilot._as_iso("30/11/1999 00:00:00") is None and pilot._as_iso("") is None


def test_the_brain_reports_the_doctors_open_findings_rather_than_its_own_subset(tmp_path):
    """Measured 20 Sep 2026: the doctor reported overall "warnings" with a live model-drift warn on both
    traded symbols at 18:28, while the pilot's 17:40 brief said "Everything the pilot can check is
    running." A green control room over an open warning is the flattering label the owner forbids."""
    report = {"generated_at": "2026-09-20 18:28:02", "overall": "warnings",
              "counts": {"ok": 17, "info": 1, "warn": 1, "fail": 0},
              "checks": [{"name": "App server", "area": "app", "status": "ok", "summary": "fine"},
                         {"name": "Model drift", "area": "models", "status": "warn",
                          "summary": "XAUUSD, BTCUSD: trained on a different price source than the feed serves"}]}
    path = tmp_path / "doctor_latest.json"
    path.write_text(json.dumps(report), encoding="utf-8")

    found = pilot.health(path)
    assert found["available"] and found["overall"] == "warnings"
    assert found["checks_read"] == 2 and len(found["open"]) == 1, "only warn/fail/error are open findings"
    assert found["open"][0]["level"] == "warn", "the doctor's field is 'status'; reading 'level' silently found none"

    out = pilot.attention({"health": found})
    drift = [i for i in out["items"] if "Model drift" in i["what"]]
    assert len(drift) == 1, "the doctor's open finding must reach the attention list"
    assert drift[0]["severity"] == "warn" and drift[0]["where"] == "/system-doctor"
    # a warn outranks the informational items, so the headline can no longer claim all is well
    assert out["worst"] == "warn"
    assert out["headline"] != "Everything the pilot can check is running."


def test_a_missing_doctor_report_says_so_instead_of_claiming_health(tmp_path):
    """Unknown is not the same as healthy, and it is not the same as a finding either: a missing report
    must add no attention item, and must not let the pilot claim the doctor said everything was fine."""
    missing = pilot.health(tmp_path / "nothing.json")
    assert missing["available"] is False and missing["open"] == []
    assert "overall" not in missing, "a missing report has no verdict to report"
    items = pilot.attention({"health": missing})["items"]
    assert not any("/system-doctor" == i["where"] and "drift" in i["what"].lower() for i in items)
    assert all(i["severity"] != "bad" for i in items), "a missing report is not itself a failure"
