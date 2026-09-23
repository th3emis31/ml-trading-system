import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import system_doctor as doc

NETSTAT = """
  Proto  Local Address          Foreign Address        State           PID
  TCP    0.0.0.0:5000           0.0.0.0:0              LISTENING       43300
  TCP    127.0.0.1:5000         127.0.0.1:61000        ESTABLISHED     43300
  TCP    0.0.0.0:32768          0.0.0.0:0              LISTENING       1200
"""


def test_app_process_detects_down_single_and_duplicate_servers():
    assert doc.check_app_process(NETSTAT)["status"] == "ok"
    duplicate = NETSTAT + "  TCP    0.0.0.0:5000           0.0.0.0:0              LISTENING       44804\n"
    result = doc.check_app_process(duplicate)
    assert result["status"] == "fail" and result["detail"]["pids"] == ["43300", "44804"]
    assert doc.check_app_process("  TCP    0.0.0.0:80   0.0.0.0:0   LISTENING   4\n")["status"] == "fail"


def test_brokers_and_http_use_the_app_endpoints():
    answers = {
        "/api/mt4/status": (200, {"connected": True, "account": 12755139, "account_matches": True}),
        "/api/mt5/status": (200, {"connected": True}),
        "/api/self-test": (200, {"ok": True}),
    }
    get = lambda path, timeout: answers[path]
    assert doc.check_brokers(get)["status"] == "ok"
    assert doc.check_app_http(get)["status"] == "ok"
    answers["/api/mt4/status"] = (200, {"connected": True, "account": 1, "account_matches": False, "expected_account": 2})
    assert doc.check_brokers(get)["status"] == "warn"
    down = lambda path, timeout: (None, {"error": "connection refused"})
    assert doc.check_app_http(down)["status"] == "fail" and doc.check_brokers(down)["status"] == "fail"


def test_autonomy_flags(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"jarvis": {"autonomy": {"enabled": False, "auto_execute": False}},
                                "settings": {"asset_settings": {"XAUUSD": {"auto_enabled": False}}}}), encoding="utf-8")
    assert doc.check_autonomy(path)["status"] == "ok"
    # Asset switches on, but no session and auto-execute off: reported, not alarming.
    path.write_text(json.dumps({"jarvis": {"autonomy": {"enabled": False, "auto_execute": False}},
                                "settings": {"asset_settings": {"XAUUSD": {"auto_enabled": True}}}}), encoding="utf-8")
    partial = doc.check_autonomy(path)
    assert partial["status"] == "info" and "cannot trade by itself" in partial["summary"]
    # Everything the app needs to execute on its own is on: warn.
    path.write_text(json.dumps({"jarvis": {"autonomy": {"enabled": True, "auto_execute": True}}, "session": {"active": True},
                                "settings": {"asset_settings": {"XAUUSD": {"auto_enabled": True}}}}), encoding="utf-8")
    armed = doc.check_autonomy(path)
    assert armed["status"] == "warn" and "ARMED for XAUUSD" in armed["summary"]


def test_paper_trader_and_lab_staleness(tmp_path):
    now = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)
    paper = tmp_path / "paper.json"
    paper.write_text(json.dumps({"last_run": "2026-09-14 11:05", "last_error": None, "decisions": [1, 2]}), encoding="utf-8")
    assert doc.check_paper_trader(paper, now)["status"] == "ok"
    paper.write_text(json.dumps({"last_run": "2026-09-14 06:05", "last_error": "broker candles unavailable"}), encoding="utf-8")
    assert doc.check_paper_trader(paper, now)["status"] == "warn"
    lab = tmp_path / "status.json"
    lab.write_text(json.dumps({"state": "idle", "last_finished_at": "2026-09-14 11:30", "evaluated_last_run": 1000, "problems": {}}), encoding="utf-8")
    assert doc.check_strategy_lab(lab, now)["status"] == "ok"
    lab.write_text(json.dumps({"state": "running", "heartbeat": "2026-09-14 10:00"}), encoding="utf-8")
    assert doc.check_strategy_lab(lab, now)["status"] == "warn"


def test_scheduled_task_csv_parsing_and_missing_task_fix(tmp_path):
    header = '"HostName","TaskName","Next Run Time","Status","Logon Mode","Last Run Time","Last Result"\n'
    rows = header + '"PC","\\SmartEntry Paper Trader","14/09/2026 13:05:00","Ready","Interactive only","14/09/2026 12:05:01","0"\n' \
                  + header + '"PC","\\SmartEntry Strategy Lab","14/09/2026 13:20:00","Ready","Interactive only","14/09/2026 12:20:00","1"\n'
    parsed = doc.parse_task_csv(rows)
    assert parsed["SmartEntry Paper Trader"]["last_result"] == "0"
    check = doc.check_scheduled_tasks(rows)
    assert check["status"] == "warn"
    assert "SmartEntry System Doctor" in check["detail"]["missing"]
    assert check["detail"]["failing"] == {"SmartEntry Strategy Lab": "1"}
    calls = []

    class Done:
        returncode, stdout, stderr = 0, "SUCCESS", ""

    applied = doc.fix_missing_tasks({"detail": {"missing": ["SmartEntry Paper Trader"]}},
                                    run=lambda args, **kw: calls.append(args) or Done())
    assert applied and applied[0]["ok"] and calls and calls[0][0] == "powershell" and "-NonInteractive" in calls[0]
    ps = calls[0][-1]
    assert "Register-ScheduledTask -TaskName 'SmartEntry Paper Trader'" in ps and "schtasks" not in ps
    assert "-LogonType Interactive -RunLevel Limited" in ps and "-Password" not in ps
    assert str(doc.ROOT / "scripts" / "run_paper_trader.cmd") in ps
    assert "-Once -At '00:05' -RepetitionInterval (New-TimeSpan -Hours 1)" in ps
    assert doc.task_trigger_ps(["/sc", "minute", "/mo", "30"]).endswith("(New-TimeSpan -Minutes 30)")
    assert doc.task_trigger_ps(["/sc", "daily", "/st", "06:30"]) == "New-ScheduledTaskTrigger -Daily -DaysInterval 1 -At '06:30'"
    logon = doc.task_trigger_ps(doc.TASKS["SmartEntry Claude Code"]["schedule"])
    assert logon.startswith("New-ScheduledTaskTrigger -AtLogOn -User ") and "GetCurrent().Name" in logon
    assert doc.TASKS["SmartEntry Claude Code"]["script"] == "start_claude.cmd"
    assert (doc.ROOT / "scripts" / "start_claude.cmd").exists() and (doc.ROOT / "scripts" / "claude_desk.ps1").exists()
    assert "'it''s'" in doc.task_register_command("it's", doc.ROOT / "x.cmd", ["/sc", "daily", "/st", "01:00"])[-1]


def test_app_error_log_window(tmp_path):
    log = tmp_path / "app_stderr.log"
    log.write_text("2026-09-13 09:00:00,001 ERROR app old failure\n"
                   "2026-09-14 11:00:00,001 INFO werkzeug fine\n"
                   "2026-09-14 11:30:00,001 ERROR app something broke\n"
                   "Traceback (most recent call last):\n"
                   "2026-09-14 11:40:00,001 INFO werkzeug 127.0.0.1 - - \"GET /x HTTP/1.1\" 500 -\n", encoding="utf-8")
    result = doc.check_app_errors(log, now_local=datetime(2026, 9, 14, 12, 0), hours=24)
    assert result["status"] == "warn" and result["detail"]["count"] == 3


TASKLIST = "\n".join(['"msedge.exe","6120","Console","1","1,806,912 K"',
                      '"python.exe","43300","Console","1","521,216 K"',
                      '"claude.exe","7788","Console","1","738,304 K"',
                      '"broken row"', ""])
PROCESSES = doc.process_memory(TASKLIST)


def test_process_memory_names_who_holds_the_ram():
    """A low-RAM line that does not name the processes reads as an accusation against the trading app."""
    assert PROCESSES["available"] and PROCESSES["top"][0] == {"name": "msedge.exe", "mb": 1764}
    assert PROCESSES["by_pid"]["43300"] == 509, "the app process's own working set, in MB"
    assert doc.process_memory("")["available"] is False


def test_resources_and_overall_status():
    assert doc.check_resources(ram_mb=2000, disk_free_gb=50, processes=PROCESSES)["status"] == "ok"
    low = doc.check_resources(ram_mb=300, disk_free_gb=50, processes=PROCESSES)
    assert low["status"] == "warn" and "msedge.exe 1764 MB" in low["summary"], "the warning says where the RAM went"
    checks = [{"status": "ok"}, {"status": "info"}]
    assert doc.overall_status(checks) == "healthy"
    assert doc.overall_status(checks + [{"status": "warn"}]) == "warnings"
    assert doc.overall_status(checks + [{"status": "fail"}]) == "problems"


def test_ram_trend_flags_a_sharp_drop_only_within_the_same_app_process():
    now = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)
    history = [{"generated_at": "2026-09-14 10:00:00", "free_ram_mb": 1500, "app_pid": "43300"},
               {"generated_at": "2026-09-14 11:30:00", "free_ram_mb": 1200, "app_pid": "43300"},
               {"generated_at": "2026-09-14 11:45:00", "free_ram_mb": 2500, "app_pid": "99999"},  # another app process
               {"generated_at": "2026-09-13 20:00:00", "free_ram_mb": 3000, "app_pid": "43300"}]  # outside the window
    checks = [doc.check_app_process(NETSTAT), doc.check_resources(ram_mb=800, disk_free_gb=50, processes=PROCESSES)]
    doc.apply_ram_trend(checks, history, now)
    resources = checks[1]
    assert resources["status"] == "info" and resources["detail"]["ram_trend"]["drop_mb"] == 700
    assert resources["detail"]["ram_trend"]["since"] == "2026-09-14 10:00:00"
    # the drop is attributed, not blamed: the app's own working set and the biggest users are named
    assert resources["detail"]["ram_trend"]["app_rss_mb"] == 509
    assert "PID 43300) itself holds 509 MB" in resources["summary"] and "msedge.exe 1764 MB" in resources["summary"]
    steady = [doc.check_app_process(NETSTAT), doc.check_resources(ram_mb=1400, disk_free_gb=50, processes=PROCESSES)]
    doc.apply_ram_trend(steady, history, now)
    assert steady[1]["status"] == "ok" and steady[1]["detail"]["ram_trend"]["sharp_drop"] is False
    low = [doc.check_app_process(NETSTAT), doc.check_resources(ram_mb=300, disk_free_gb=50, processes=PROCESSES)]
    doc.apply_ram_trend(low, history, now)
    assert low[1]["status"] == "warn"  # the existing low-RAM warning is unchanged
    assert doc.ram_trend([], 800, "43300", now) is None and doc.ram_trend(history, None, "43300", now) is None


def test_run_doctor_never_crashes_and_saves_history(tmp_path, monkeypatch):
    monkeypatch.setattr(doc, "check_app_process", lambda: doc._result("App server", "app", "ok", "fake"))
    monkeypatch.setattr(doc, "check_scheduled_tasks", lambda: (_ for _ in ()).throw(RuntimeError("schtasks missing")))
    monkeypatch.setattr(doc, "check_resources", lambda: doc._result("PC resources", "pc", "ok", "fake"))
    down = lambda path, timeout: (None, {"error": "down"})
    report = doc.run_doctor(get=down, save=False)
    json.dumps(report)
    names = [c["name"] for c in report["checks"]]
    assert "App responds" in names and report["overall"] in ("warnings", "problems") and report["places_orders"] is False
    assert any("Check could not run" in c["summary"] for c in report["checks"])
    doc.store_health_report(report, health_dir=tmp_path)
    doc.store_health_report(report, health_dir=tmp_path)
    history = json.loads((tmp_path / "doctor_history.json").read_text(encoding="utf-8"))
    assert len(history) == 2 and (tmp_path / "doctor_latest.json").exists()


def test_a_task_running_from_the_fallback_folder_is_a_warning():
    """A task can exist, report result 0 and still run the other copy of the system - which is how the Daily Agent
    journalled into the fallback folder for two days while the live pages showed nothing."""
    header = ('"HostName","TaskName","Next Run Time","Status","Logon Mode","Last Run Time","Last Result","Author",'
              '"Task To Run","Start In","Comment"')
    live_script = str(doc.ROOT / "scripts" / "run_daily_learning.cmd")
    away_script = str(Path("C:/Users/th_em/scripts/run_daily_agent.cmd"))
    row = '"PC","{name}","2026-09-19 05:30:00","Ready","Interactive only","2026-09-18 05:30:00","0","me","{runs}","",""'
    csv_text = os.linesep.join([header,
                                row.format(name="SmartEntry Daily Learning", runs=live_script),
                                row.format(name="SmartEntry Daily Agent", runs=away_script), ""])

    result = doc.check_scheduled_tasks(csv_text)
    assert result["status"] == "warn" and "outside the live folder" in result["summary"]
    elsewhere = result["detail"]["running_from_elsewhere"]
    assert list(elsewhere) == ["SmartEntry Daily Agent"], "only the task pointing away from the live folder"
    assert "run_daily_agent.cmd" in elsewhere["SmartEntry Daily Agent"]
    assert result["detail"]["live_folder"] == str(doc.ROOT)


def test_the_doctor_page_states_the_check_age_and_labels_its_clocks():
    """The page stamps the check in UTC and the page-load time in local time. Unlabelled, a reader subtracts them:
    at 20:06 local a check stamped 18:58 UTC looks 68 minutes old when it is 8."""
    import app as app_module

    html = app_module.app.test_client().get("/system-doctor").get_data(as_text=True)
    assert "function checkAge(stamp)" in html, "the page computes how old the check is"
    assert "checkAge(r.generated_at)" in html, "and prints it beside the UTC stamp"
    assert "Page loaded ${new Date().toLocaleTimeString()} local" in html, "the local clock says it is local"
    assert "the 30-minute check has missed a run" in html, "past an hour it says a run was missed"


def test_a_dead_app_is_restarted_but_a_duplicated_one_never_is():
    """After the 22 September reboot the app never came back and all four demo strategies failed
    hourly with "connection refused" for seven hours. The doctor already reported it every thirty
    minutes and did nothing, so the repair lives here.

    The asymmetry is the point: nothing listening is repaired, too many listening never is, because
    answering "two servers" by starting a third is how the problem compounds.
    """
    calls = []
    dead = {"name": "App server", "status": "fail", "detail": {"pids": []}}
    out = doc.fix_dead_app(dead, run=lambda *a, **k: calls.append(a) or None)
    assert out[0]["ok"] is True and out[0]["fix"] == "restart_dead_app"
    assert calls, "it must actually launch something"
    launched = " ".join(str(x) for x in calls[0][0])
    assert "start_trading.bat" in launched, "must use the launcher, which pins MT5_PATH"


def test_the_restart_uses_the_launcher_so_the_mt5_terminal_stays_pinned():
    """Two MT5 terminals run on this machine and MetaTrader5.initialize() with no path binds to
    whichever Windows offers - that is how the strategies ended up halting on account 25446287.
    start_trading.bat sets MT5_PATH, so the repair must go through it and never call python directly.
    """
    launcher = (doc.ROOT / "start_trading.bat").read_text(encoding="utf-8", errors="replace")
    assert "MT5_PATH" in launcher, "the launcher is what pins the terminal; the restart relies on it"


def test_restart_is_declared_a_safe_fix():
    assert "restart_dead_app" in doc.SAFE_FIXES


def test_a_missing_launcher_is_reported_not_raised(monkeypatch, tmp_path):
    """A repair that cannot run must never take the health check down with it."""
    monkeypatch.setattr(doc, "ROOT", tmp_path)
    out = doc.fix_dead_app({"name": "App server", "status": "fail", "detail": {"pids": []}})
    assert out[0]["ok"] is False and "missing" in out[0]["reason"]
