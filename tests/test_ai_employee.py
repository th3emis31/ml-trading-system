import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from src import ai_employee as ae
from src import system_doctor as doctor

GOOD_REPLY = {
    "summary": "System healthy. Strategy book has 44 watchlist strategies and none approved.",
    "system_health": "healthy",
    "findings": [{"area": "research", "severity": "warn", "text": "BTC results exclude swap", "evidence": "cost_model spread-only"}],
    "proposals": [{"title": "Measure the BTCUSD swap rate in the MT5 tester", "category": "data", "detail": "Run one test",
                   "evidence": "BTC cost_model spread-only", "expected_benefit": "honest BTC results", "risk": "none", "effort": "small"}],
    "memory_notes": ["BTC swap not measured yet"],
    "questions_for_owner": [],
}


def test_parse_reply_accepts_fenced_json_clips_and_flags_trading_actions():
    reply = dict(GOOD_REPLY, system_health="bogus", proposals=GOOD_REPLY["proposals"] + [
        {"title": "Enable auto-execute on the live account", "category": "nonsense", "detail": "x" * 5000, "effort": "huge"}])
    brief = ae.parse_employee_reply("Here you go:\n```json\n" + json.dumps(reply) + "\n```")
    assert brief["system_health"] == "attention"
    assert len(brief["proposals"]) == 2
    risky = brief["proposals"][1]
    assert risky["category"] == "research" and risky["effort"] == "medium" and len(risky["detail"]) == 1500 and "flag" in risky
    assert "flag" not in brief["proposals"][0]
    for bad in ("no json at all", "{not valid json}", json.dumps({"summary": ""}), "[1, 2]"):
        with pytest.raises(ValueError):
            ae.parse_employee_reply(bad)


def test_merge_proposals_never_duplicates():
    store = {"items": []}
    first = ae.merge_proposals(store, [{"title": "Measure the BTCUSD swap rate in the MT5 tester"}], "2026-09-14 07:15:00", "r1")
    assert first == {"added": 1, "merged": 0, "total": 1}
    store["items"][0]["status"] = "rejected"
    again = ae.merge_proposals(store, [{"title": "Measure BTCUSD swap rate with the MT5 tester"},
                                       {"title": "Add a warning when the EA status file is stale"}], "2026-09-15 07:15:00", "r2")
    assert again == {"added": 1, "merged": 1, "total": 2}
    assert store["items"][0]["seen_count"] == 2 and store["items"][0]["status"] == "rejected"
    assert store["items"][1]["status"] == "pending" and store["items"][1]["id"]


def test_memory_notes_dedupe_and_cap(monkeypatch):
    memory = {"notes": []}
    assert ae.add_memory_notes(memory, ["BTC swap not measured yet", "btc swap NOT measured yet!", ""], "t") == 1
    monkeypatch.setattr(ae, "MAX_MEMORY_NOTES", 3)
    ae.add_memory_notes(memory, [f"note {i}" for i in range(5)], "t")
    assert len(memory["notes"]) == 3 and memory["notes"][-1]["text"] == "note 4"


def test_claude_command_is_read_only_and_env_has_no_api_keys():
    command = ae.claude_command("claude")
    joined = " ".join(command)
    for flag in ("-p", "--restricted", "--strict-mcp-config", "--no-session-persistence"):
        assert flag in command
    assert command[command.index("--permission-mode") + 1] == "dontAsk"
    assert command[command.index("--permission-prompts") + 1] == "none"
    allowed = command[command.index("--allowedTools") + 1].split(",")
    denied = command[command.index("--disallowedTools") + 1].split(",")
    assert set(allowed) == {"Read", "Grep", "Glob"}
    assert {"Bash", "Edit", "Write", "WebFetch"} <= set(denied) and "bypassPermissions" not in joined
    env = ae.child_environment({"ANTHROPIC_API_KEY": "secret", "ANTHROPIC_AUTH_TOKEN": "t", "CLAUDECODE": "1", "PATH": "x"})
    assert "ANTHROPIC_API_KEY" not in env and "ANTHROPIC_AUTH_TOKEN" not in env and "CLAUDECODE" not in env and env["PATH"] == "x"


def test_interpret_cli_output():
    ok = ae.interpret_cli_output(0, json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "{}",
                                                "total_cost_usd": 0.12, "num_turns": 3, "duration_ms": 900}), "")
    assert ok["ok"] and ok["num_turns"] == 3
    expired = ae.interpret_cli_output(1, json.dumps({"is_error": True, "result": "Login expired. Please run /login"}), "")
    assert not expired["ok"] and "run /login" in expired["error"]
    garbage = ae.interpret_cli_output(1, "", "node: not found")
    assert not garbage["ok"] and "node" in garbage["error"]


def _seed_root(root: Path):
    health = root / "data" / "system_health"
    health.mkdir(parents=True)
    (health / "doctor_latest.json").write_text(json.dumps({"generated_at": "2026-09-14 06:30:00", "overall": "healthy",
                                                           "counts": {"ok": 12}, "checks": [{"status": "ok", "name": "App", "summary": "fine"}]}))


def test_run_employee_end_to_end_with_a_fake_claude(tmp_path):
    root, base = tmp_path / "root", tmp_path / "employee"
    _seed_root(root)
    prompts = []

    def fake(prompt):
        prompts.append(prompt)
        return {"ok": True, "text": json.dumps(GOOD_REPLY), "cost_usd": 0.0, "num_turns": 2, "duration_ms": 1000}

    now = datetime(2026, 9, 14, 6, 15, tzinfo=timezone.utc)
    record = ae.run_employee(runner=fake, now=now, base=base, root=root)
    assert record["status"] == "ok" and record["proposals_added"] == 1 and record["memory_added"] == 1
    assert "HARD RULES" in prompts[0] and '"system_doctor": "ok"' in prompts[0]
    brief = json.loads((base / "brief_latest.json").read_text(encoding="utf-8"))
    assert brief["system_health"] == "healthy" and (base / "briefs" / f"{brief['date']}.json").exists()
    assert not (base / "lock.json").exists()

    assert ae.run_employee(runner=fake, now=now, base=base, root=root)["status"] == "skipped"   # once a day
    forced = ae.run_employee(force=True, runner=fake, now=now, base=base, root=root)
    assert forced["proposals_added"] == 0 and forced["proposals_merged"] == 1                   # no duplicate proposal
    assert "Measure the BTCUSD swap rate" in prompts[-1]                                        # earlier proposals in context

    failed = ae.run_employee(force=True, runner=lambda p: {"ok": False, "error": "Login expired"}, now=now, base=base, root=root)
    assert failed["status"] == "error" and "Login expired" in failed["error"]
    assert json.loads((base / "brief_latest.json").read_text(encoding="utf-8"))["run_id"] == forced["run_id"]  # last good brief kept
    bad = ae.run_employee(force=True, runner=lambda p: {"ok": True, "text": "sorry, no json"}, now=now, base=base, root=root)
    assert bad["status"] == "error" and "could not be used" in bad["error"]
    runs = json.loads((base / "runs.json").read_text(encoding="utf-8"))
    assert [r["status"] for r in runs] == ["ok", "ok", "error", "error"]

    summary = ae.employee_summary(base)
    assert summary["read_only"] and summary["proposal_counts"]["pending"] == 1 and summary["memory_notes"]
    proposal_id = summary["proposals"][0]["id"]
    assert ae.set_proposal_status(proposal_id, "approved", "go ahead", base)["status"] == "approved"
    with pytest.raises(ValueError):
        ae.set_proposal_status(proposal_id, "maybe", base=base)
    with pytest.raises(KeyError):
        ae.set_proposal_status("missing", "done", base=base)


def test_active_lock_prevents_a_second_run(tmp_path):
    base = tmp_path / "employee"
    base.mkdir()
    now = datetime(2026, 9, 14, 6, 15, tzinfo=timezone.utc)
    (base / "lock.json").write_text(json.dumps({"started_at": (now - timedelta(minutes=5)).strftime(ae.TIME_FORMAT)}))
    assert ae.run_employee(runner=lambda p: {"ok": True, "text": json.dumps(GOOD_REPLY)}, now=now, base=base,
                           root=tmp_path)["status"] == "skipped"


def test_doctor_check_ai_employee(tmp_path):
    runs = tmp_path / "runs.json"
    now = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)
    assert doctor.check_ai_employee(runs, now)["status"] == "info"
    runs.write_text(json.dumps([{"status": "ok", "finished_at": "2026-09-14 06:20:00", "proposals_added": 2}]))
    assert doctor.check_ai_employee(runs, now)["status"] == "ok"
    assert doctor.check_ai_employee(runs, now + timedelta(hours=40))["status"] == "warn"
    runs.write_text(json.dumps([{"status": "ok", "finished_at": "2026-09-14 06:20:00"},
                                {"status": "error", "error": "Login expired -> run /login", "finished_at": "2026-09-14 07:00:00"}]))
    failed = doctor.check_ai_employee(runs, now)
    assert failed["status"] == "warn" and "/login" in failed["summary"]
    assert "SmartEntry AI Employee" in doctor.TASKS and "tests/test_ai_employee.py" in doctor.TEST_FILES
