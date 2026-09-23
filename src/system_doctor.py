"""System Doctor: one out-of-process health check for the whole trading system.

It runs outside app.py (Windows tasks "SmartEntry System Doctor" every 30 minutes and
"SmartEntry System Doctor Daily" with --deep), so it can still report when the app is down,
duplicated or hung. It reads the app's own endpoints, state files and logs.

Safety: the doctor never changes trading settings and never places, modifies or closes orders.
With --fix it applies only the fixes in ``SAFE_FIXES``: recreating a missing scheduled task from its
known definition, starting the app again when NOTHING listens on port 5000, and starting a required
MetaTrader terminal that is not running. Everything else - for example a second app server - is
reported with the exact action to take, because it needs judgement.
Note the asymmetry: "no server" is repaired, "too many servers" never is, because answering that by
starting another one is how you get three.

Each check returns {"name", "area", "status": ok | info | warn | fail, "summary", "detail"}.
Run:  python -m src.system_doctor            quick checks
      python -m src.system_doctor --deep     also compiles the code and runs the test suite
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

ROOT = Path(__file__).resolve().parents[1]
APP_URL = os.getenv("TRADING_APP_URL", "http://127.0.0.1:5000").rstrip("/")
HEALTH_DIR = ROOT / "data" / "system_health"
LATEST_PATH = HEALTH_DIR / "doctor_latest.json"
HISTORY_PATH = HEALTH_DIR / "doctor_history.json"
HISTORY_KEPT = 300
STATUS_RANK = {"ok": 0, "info": 0, "warn": 1, "fail": 2}
TEST_FILES = ("tests/test_rocket_features.py", "tests/test_edge_research.py", "tests/test_research_combine.py",
              "tests/test_mtf_data.py", "tests/test_meta_research.py", "tests/test_paper_trader.py",
              "tests/test_demo_executor.py", "tests/test_strategy_lab.py", "tests/test_system_doctor.py",
              "tests/test_daily_report.py", "tests/test_performance_analytics.py", "tests/test_strategy_lab_swap.py",
              "tests/test_strategy_book.py", "tests/test_ea_monitor.py", "tests/test_ai_employee.py",
              "tests/test_tradingview_intake.py", "tests/test_tradingview_plan.py", "tests/test_learning_curve.py",
              "tests/test_economic_calendar.py", "tests/test_daily_agent.py", "tests/test_crt_lab.py",
              "tests/test_crt_fvg_lab.py", "tests/test_crt_forward.py",
              "tests/test_crt_htf_lab.py", "tests/test_crt_mss_lab.py", "tests/test_stp_swap_lab.py",
              "tests/test_signal_parity.py", "tests/test_execution_guard.py", "tests/test_execute_api_security.py",
              "tests/test_broker_levels.py", "tests/test_approval_match.py", "tests/test_signal_freshness.py",
              "tests/test_autonomy_confidence.py", "tests/test_walkforward_live_engine.py",
              "tests/test_signals_live_api.py", "tests/test_approval_bypass.py", "tests/test_model_integrity.py",
              "tests/test_learning_pollution.py", "tests/test_training_gate.py", "tests/test_retrain_routes_locked.py",
              "tests/test_event_defence.py", "tests/test_gold_session_pullback_lab.py",
              "tests/test_demo_session_pullback.py",
              "tests/test_demo_volatility_breakout.py",
              "tests/test_plan_journal.py", "tests/test_atomic_analyst.py",
              "tests/test_positioning.py",
              "tests/test_i40_pilot.py",
              "tests/test_voice_single_speaker.py")
TASKS = {
    "SmartEntry Paper Trader": {"script": "run_paper_trader.cmd", "schedule": ["/sc", "hourly", "/mo", "1", "/st", "00:05"]},
    "SmartEntry Strategy Lab": {"script": "run_strategy_lab.cmd", "schedule": ["/sc", "hourly", "/mo", "1", "/st", "00:20"]},
    "SmartEntry System Doctor": {"script": "run_system_doctor.cmd", "schedule": ["/sc", "minute", "/mo", "30"]},
    "SmartEntry System Doctor Daily": {"script": "run_system_doctor_deep.cmd", "schedule": ["/sc", "daily", "/st", "06:30"]},
    "SmartEntry Daily Report": {"script": "run_daily_report.cmd", "schedule": ["/sc", "daily", "/st", "06:45"]},
    "SmartEntry AI Employee": {"script": "run_ai_employee.cmd", "schedule": ["/sc", "daily", "/st", "07:15"]},
    "SmartEntry Daily Learning": {"script": "run_daily_learning.cmd", "schedule": ["/sc", "daily", "/st", "05:30"]},
    "SmartEntry Demo Pullback": {"script": "run_demo_pullback.cmd", "schedule": ["/sc", "hourly", "/mo", "1", "/st", "00:01"]},
    "SmartEntry Demo Breakout": {"script": "run_demo_breakout.cmd", "schedule": ["/sc", "hourly", "/mo", "1", "/st", "00:03"]},
    # The daily plan's executor, added 20 Sep 2026 when the plan finally got an execution path. Magic 440704,
    # demo account 11581419 only; enabled/dry_run live in data/paper_trading/demo_plan_trader.json.
    "SmartEntry Demo Plan": {"script": "run_demo_plan.cmd", "schedule": ["/sc", "hourly", "/mo", "1", "/st", "00:07"]},
    # The owner's manipulation-candle rule, the one candidate positive in all three windows. Magic
    # 440805, demo 11581419 only; enabled/dry_run in data/paper_trading/demo_sweep_trader.json.
    "SmartEntry Demo Sweep": {"script": "run_demo_sweep.cmd", "schedule": ["/sc", "hourly", "/mo", "1", "/st", "00:09"]},
    # NOT listed, deliberately: "SmartEntry TV Chart Worker". It was built to keep the SmartEntry map
    # current on the real TradingView chart, but the Edge extension in extensions/smartentry_tv does
    # that better - live, inside the browser the owner is already signed into - so the worker is
    # superseded. Its task is disabled rather than deleted, and the doctor must not recreate it or it
    # would warn every cycle about a job that cannot succeed without a sign-in nobody needs any more.
    # Re-enable with: Enable-ScheduledTask -TaskName 'SmartEntry TV Chart Worker'.
    "SmartEntry Plan Journal": {"script": "run_plan_journal.cmd", "schedule": ["/sc", "hourly", "/mo", "1", "/st", "00:07"]},
    "SmartEntry Positioning": {"script": "run_positioning.cmd", "schedule": ["/sc", "daily", "/st", "21:10"]},
    "SmartEntry i40 Pilot": {"script": "run_i40_pilot.cmd", "schedule": ["/sc", "hourly", "/mo", "1", "/st", "00:40"]},
    "SmartEntry TradingView Plan": {"script": "run_tradingview_plan.cmd", "schedule": ["/sc", "hourly", "/mo", "1", "/st", "00:45"]},
    "SmartEntry Obsidian Notes": {"script": "run_obsidian_notes.cmd", "schedule": ["/sc", "hourly", "/mo", "1", "/st", "00:50"]},
    # Opens Claude Code (tabs "bridge" and "desk") at this user's logon; recreated by --fix if missing.
    "SmartEntry Claude Code": {"script": "start_claude.cmd", "schedule": ["/sc", "onlogon"]},
}
# schtasks "Last Result" codes that are not failures: success, running, not run yet.
TASK_OK_RESULTS = {"0", "267009", "267011"}
SAFE_FIXES = ("recreate_missing_scheduled_task", "restart_dead_app", "start_missing_terminals")

GetJson = Callable[[str, float], tuple[Optional[int], Optional[dict]]]


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _result(name: str, area: str, status: str, summary: str, **detail) -> dict:
    return {"name": name, "area": area, "status": status, "summary": summary, "detail": detail}


def get_json(path: str, timeout: float = 30.0) -> tuple[Optional[int], Optional[dict]]:
    try:
        with urllib.request.urlopen(APP_URL + path, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8"))
        except Exception:
            return exc.code, None
    except Exception as exc:
        return None, {"error": str(exc)}


def _parse_utc(text: Optional[str]) -> Optional[datetime]:
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(str(text)[:19], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _read_json(path: Path):
    """JSON from a file, or None. Accepts UTF-8 (with or without BOM) and the ANSI text MetaTrader writes."""
    try:
        raw = Path(path).read_bytes()
    except Exception:
        return None
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return json.loads(raw.decode(encoding))
        except (UnicodeDecodeError, ValueError):
            continue
    return None


# --------------------------------------------------------------------------- checks
def parse_listeners(netstat_text: str, port: int = 5000) -> list[str]:
    pids = set()
    for line in netstat_text.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0].upper() == "TCP" and parts[1].endswith(f":{port}") and parts[3].upper() == "LISTENING":
            pids.add(parts[4])
    return sorted(pids)


def check_app_process(netstat_text: Optional[str] = None) -> dict:
    if netstat_text is None:
        netstat_text = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True, timeout=60).stdout
    pids = parse_listeners(netstat_text)
    if not pids:
        return _result("App server", "app", "fail", "Nothing listens on port 5000: the dashboard is down. "
                       "start_trading.bat restarts it within seconds; if not, run it from C:\\Users\\th_em.", pids=pids)
    if len(pids) > 1:
        return _result("App server", "app", "fail", f"{len(pids)} app servers listen on port 5000 (PIDs {', '.join(pids)}). "
                       "Stop the python app.py process that start_trading.bat did not start.", pids=pids)
    return _result("App server", "app", "ok", f"One app server on port 5000 (PID {pids[0]}).", pids=pids)


def check_app_http(get: GetJson = get_json) -> dict:
    code, body = get("/api/self-test", 60)
    if code is None:
        return _result("App responds", "app", "fail", f"The app does not answer: {(body or {}).get('error')}")
    if code != 200 or not (body or {}).get("ok"):
        return _result("App responds", "app", "warn", "The app answers but its self-test reports a degraded state.", self_test=body)
    return _result("App responds", "app", "ok", "The app answers and its self-test passes.")


def check_brokers(get: GetJson = get_json) -> dict:
    _, mt4 = get("/api/mt4/status", 30)
    _, mt5 = get("/api/mt5/status", 30)
    mt4, mt5 = mt4 or {}, mt5 or {}
    problems = []
    if not mt4.get("connected"):
        problems.append(f"MT4 bridge disconnected ({mt4.get('message') or mt4.get('error') or 'no answer'})")
    elif mt4.get("account_matches") is False:
        problems.append(f"MT4 bridge is on account {mt4.get('account')}, expected {mt4.get('expected_account')}")
    if not mt5.get("connected"):
        problems.append(f"MT5 disconnected ({mt5.get('message') or mt5.get('error') or 'no answer'})")
    detail = {"mt4": {k: mt4.get(k) for k in ("connected", "account", "account_matches", "server", "port", "message")},
              "mt5": {k: mt5.get(k) for k in ("connected", "message")}}
    if not problems:
        return _result("Broker connections", "brokers", "ok", f"MT4 bridge on account {mt4.get('account')} and MT5 are connected.", **detail)
    return _result("Broker connections", "brokers", "fail" if len(problems) == 2 else "warn", "; ".join(problems), **detail)


def check_autonomy(state_path: Path = ROOT / "data" / "auto_trader_state.json") -> dict:
    state = _read_json(state_path)
    if not isinstance(state, dict):
        return _result("App auto-trading flags", "safety", "warn", "auto_trader_state.json cannot be read.")
    autonomy = (state.get("jarvis") or {}).get("autonomy") or {}
    assets = {sym: bool((cfg or {}).get("auto_enabled")) for sym, cfg in ((state.get("settings") or {}).get("asset_settings") or {}).items()}
    session_active = bool((state.get("session") or {}).get("active"))
    flags = {"autonomy_enabled": bool(autonomy.get("enabled")), "auto_execute": bool(autonomy.get("auto_execute")),
             "session_active": session_active, "asset_auto": assets}
    # app._autonomy_should_execute needs an active session, auto_execute and the asset switch; the loop needs autonomy on.
    armed = [sym for sym, on in assets.items() if on] if (flags["autonomy_enabled"] and flags["auto_execute"] and session_active) else []
    if armed:
        return _result("App auto-trading flags", "safety", "warn",
                       f"App automatic execution is ARMED for {', '.join(armed)}: autonomy, auto-execute, an active session "
                       "and the asset switch are all on. Confirm this is intended.", **flags)
    switched_on = [name for name, on in (("autonomy", flags["autonomy_enabled"]), ("auto-execute", flags["auto_execute"]),
                                         ("session", session_active)) if on] + [f"{sym} auto" for sym, on in assets.items() if on]
    if switched_on:
        missing = [name for name, on in (("autonomy", flags["autonomy_enabled"]), ("auto-execute", flags["auto_execute"]),
                                          ("an active session", session_active)) if not on]
        return _result("App auto-trading flags", "safety", "info",
                       f"Some auto switches are on ({', '.join(switched_on)}), but the app cannot trade by itself: "
                       f"{', '.join(missing)} {'is' if len(missing) == 1 else 'are'} off.", **flags)
    return _result("App auto-trading flags", "safety", "ok",
                   "App autonomy and auto-execute are off (the gold 4H demo executor is separate).", **flags)


def check_demo_execution(get: GetJson = get_json, config_path: Path = ROOT / "data" / "paper_trading" / "demo_execution.json",
                         journal_path: Path = ROOT / "data" / "paper_trading" / "demo_execution_journal.json",
                         now: Optional[datetime] = None) -> dict:
    now = now or _now_utc()
    config = _read_json(config_path)
    if not isinstance(config, dict) or not config.get("enabled"):
        return _result("Demo execution", "trading", "info", "Demo execution is off.")
    mode = "dry run" if config.get("dry_run", True) else f"ON for demo account {config.get('account_login')}"
    journal = _read_json(journal_path) or {}
    recent = [e for e in journal.get("events") or [] if (_parse_utc(e.get("at")) or now - timedelta(days=9)) >= now - timedelta(hours=24)]
    bad = [e for e in recent if e.get("event") in ("failed", "close_failed", "error", "sync_skipped")]
    _, status = get("/api/demo-model/status", 30)
    positions = (status or {}).get("open_positions")
    issues = []
    if not config.get("dry_run", True) and positions is None:
        issues.append("MT5 positions cannot be read")
    if bad:
        issues.append(f"{len(bad)} failed execution events in 24 h (latest: {bad[-1].get('event')} - {bad[-1].get('reason')})")
    detail = {"mode": mode, "volume": config.get("volume"), "magic": config.get("magic"), "events_24h": len(recent),
              "failed_events_24h": len(bad), "open_model_positions": positions}
    if issues:
        return _result("Demo execution", "trading", "warn", f"Demo execution {mode}: " + "; ".join(issues), **detail)
    return _result("Demo execution", "trading", "ok",
                   f"Demo execution {mode}; {len(recent)} events in 24 h, none failed; "
                   f"{0 if not positions else len(positions)} model position(s) open.", **detail)


def check_demo_pullback(state_path: Path = ROOT / "data" / "paper_trading" / "demo_session_pullback_state.json",
                        config_path: Path = ROOT / "data" / "paper_trading" / "demo_session_pullback.json",
                        now: Optional[datetime] = None, label: str = "Demo pullback",
                        task: str = "SmartEntry Demo Pullback") -> dict:
    """A demo strategy on account 11581419 (the gold session pullback by default; the volatility breakout passes its own
    files, label and task): warn when halted or when the hourly cycle stopped running."""
    now = now or _now_utc()
    config = _read_json(config_path)
    if not isinstance(config, dict) or not config.get("enabled"):
        return _result(label, "trading", "info", f"{label} demo trading is off.")
    mode = "dry run (orders logged, not sent)" if config.get("dry_run", True) else "SENDING orders to demo account 11581419"
    state = _read_json(state_path) or {}
    last = _parse_utc((state.get("last_cycle") or {}).get("at"))
    age_h = (now - last).total_seconds() / 3600 if last else None
    detail = {"mode": mode, "halted": state.get("halted"), "day_stopped": state.get("day_stopped"),
              "last_cycle": state.get("last_cycle"), "age_hours": round(age_h, 2) if age_h is not None else None}
    if state.get("halted"):
        halted = state["halted"]
        return _result(label, "trading", "warn", f"{label} HALTED ({halted.get('kind')}) at {halted.get('at')}: "
                       f"{halted.get('reason')} Resume on /demo-trading after checking.", **detail)
    if age_h is None or age_h > 2.5:
        return _result(label, "trading", "warn", f"{label} {mode}: no cycle in the last 2.5 h (task {task}).", **detail)
    return _result(label, "trading", "ok", f"{label} {mode}; last cycle {state['last_cycle'].get('at')} UTC: "
                   f"{state['last_cycle'].get('reason')}", **detail)


def live_model_return(symbol: str, decisions_path: Path = ROOT / "data" / "learning_decisions.json") -> str:
    """The after-cost return of the model that is actually live, from its own promotion record.

    Which record to read depends on what the gate did: when the challenger was promoted, the live
    model is that challenger; when it was refused, the champion stayed and its figure is the live one.
    Reading the wrong side would attribute a rejected model's return to the one doing the trading.
    """
    rows = _read_json(decisions_path) or []
    if isinstance(rows, dict):
        rows = rows.get("decisions") or []
    latest = next((r for r in reversed(rows) if str(r.get("symbol", "")).upper() == symbol.upper()), None)
    if not latest:
        return "no promotion record"
    side = latest.get("rf_challenger") if latest.get("rf_promoted") else latest.get("rf_champion")
    ret = (side or {}).get("total_return_pct")
    if not isinstance(ret, (int, float)):
        return "return not recorded"
    bars = (side or {}).get("rows")
    return f"{ret:+.2f}% after costs" + (f" on {bars} unseen bars" if bars else "")


def check_model_drift(now: Optional[datetime] = None) -> dict:
    """Does the live model still describe the prices the system trades, and does it beat guessing?

    Added 19 Sep 2026 because nothing was checking this. The learning gate records an accuracy and
    compares it with nothing, and the champions in use were trained on Yahoo while the feed is MT5.
    Both are now noticed here instead of needing someone to go looking.
    """
    from .drift_watch import collect_drift

    try:
        report = collect_drift()
    except Exception as exc:
        return _result("Model drift", "data", "info", f"Drift watch could not run: {exc}")
    concerns = report.get("concerns") or []
    mismatched = [s for s, m in report["models"].items()
                  if (m.get("source_match") or {}).get("status") == "MISMATCH"]
    no_edge = [s for s, m in report["models"].items()
               if (m.get("vs_baseline") or {}).get("status") in ("no edge", "negligible")]
    if mismatched:
        return _result("Model drift", "data", "warn",
                       f"{', '.join(mismatched)}: the live champion was trained on a different price "
                       f"source than the feed serves, so its accuracy describes another series.",
                       concerns=concerns[:6])
    if no_edge:
        # Accuracy alone is the wrong verdict here, and this system settled that on 20 Sep 2026: across
        # 41 head-to-head runs the accuracy winner and the money winner agreed 17 times and disagreed
        # 17, so the promotion gate was changed to decide on AFTER-COST RETURN. Reporting only accuracy
        # therefore says "not beating a guess" about a champion that is live precisely because it made
        # money - XAUUSD sits at 0.496 accuracy and was promoted on +4.62% against -1.30%.
        #
        # It stays a warning: a model at the majority class IS worth knowing about, and hiding it would
        # be exactly the cosmetic reassurance this owner has said they do not want. But it now carries
        # the number the gate actually used, so the reader can tell "weak but earning" from "broken".
        returns = [f"{symbol} {live_model_return(symbol)}" for symbol in no_edge]
        return _result("Model drift", "data", "warn",
                       f"{', '.join(no_edge)}: accuracy is at or barely above the majority class. The gate "
                       f"promotes on after-cost return, not accuracy, and that return is: "
                       f"{'; '.join(returns)}. Weak on direction, so judge these on money.",
                       concerns=concerns[:6])
    if concerns:
        return _result("Model drift", "data", "info",
                       f"{len(concerns)} drift note(s) worth reading.", concerns=concerns[:6])
    return _result("Model drift", "data", "ok", "The live models match the feed and beat the majority class.")


def check_i40_pilot(now: Optional[datetime] = None) -> dict:
    """The system brain: warn when its brief stops refreshing, because a stale brain misdescribes the system."""
    from .i40_pilot import REFRESH_MINUTES, loop

    state = loop(now or _now_utc())
    if not state.get("available"):
        return _result("i40 Pilot", "data", "info", "The i40 Pilot brief has not been written yet.",
                       reason=state.get("reason"))
    age = state.get("age_minutes")
    if state.get("stale"):
        return _result("i40 Pilot", "data", "warn", f"The i40 Pilot brief is {age} min old (it refreshes every "
                       f"{REFRESH_MINUTES} min). Check the SmartEntry i40 Pilot task.", **state)
    return _result("i40 Pilot", "data", "ok", f"The i40 Pilot brief is {age} min old.", **state)


def check_positioning(now: Optional[datetime] = None) -> dict:
    """CFTC positioning: warn when the cached report is older than two weeks (the task or the download stopped)."""
    from .positioning import positioning_dir
    from .runtime_paths import read_latest_json

    now = now or _now_utc()
    data = read_latest_json(positioning_dir() / "latest.json", "not downloaded yet (task SmartEntry Positioning)")
    if not data.get("available"):
        return _result("Positioning", "data", "info", "CFTC positioning has not been downloaded yet.", reason=data.get("reason"))
    report = data.get("report") or {}
    as_of = _parse_utc((report.get("as_of_tuesday") or "") + " 00:00:00")
    age_days = (now - as_of).days if as_of else None
    detail = {"as_of": report.get("as_of_tuesday"), "age_days": age_days, "markets": len(data.get("markets") or [])}
    if age_days is None or age_days > 14:
        return _result("Positioning", "data", "warn", f"CFTC positioning is {age_days} days old (a weekly report should "
                       "be under 14). Check the SmartEntry Positioning task.", **detail)
    return _result("Positioning", "data", "ok", f"CFTC positioning as of {report.get('as_of_tuesday')} ({age_days} days old).",
                   **detail)


def check_atomic_analyst(now: Optional[datetime] = None) -> dict:
    """The owner's MT5 ATOMIC ANALYST V85 panel: reports whether its files are fresh. Evidence only, never an input."""
    from .atomic_analyst import SYMBOLS, read_panel

    now = now or _now_utc()
    panels = {symbol: read_panel(symbol, now) for symbol in SYMBOLS}
    found = {s: p for s, p in panels.items() if p.get("available")}
    detail = {s: {"verdict": p.get("verdict"), "age_minutes": p.get("age_minutes"), "fresh": p.get("fresh")}
              for s, p in found.items()}
    if not found:
        return _result("Atomic panel", "trading", "info", "The ATOMIC ANALYST V85 panel has written no files "
                       "(its MT5 chart is closed, or the indicator is not attached).")
    stale = [s for s, p in found.items() if not p.get("fresh")]
    if stale:
        return _result("Atomic panel", "trading", "warn", f"Panel files are stale for {', '.join(stale)}: the MT5 chart "
                       "is probably closed. Read-only evidence, so nothing in the system is affected.", **detail)
    return _result("Atomic panel", "trading", "ok",
                   "Panel fresh for " + ", ".join(f"{s} ({p.get('verdict')})" for s, p in found.items()), **detail)


def check_paper_trader(state_path: Path = ROOT / "data" / "paper_trading" / "xauusd_4h_mtf_xgb_tight_q90.json",
                       now: Optional[datetime] = None) -> dict:
    now = now or _now_utc()
    state = _read_json(state_path)
    if not isinstance(state, dict):
        return _result("Paper trader", "trading", "warn", "No paper-trading state file yet.")
    last_run = _parse_utc(state.get("last_run"))
    age_h = (now - last_run).total_seconds() / 3600 if last_run else None
    closed = state.get("closed_trades") or []
    detail = {"last_run": state.get("last_run"), "age_hours": round(age_h, 2) if age_h is not None else None,
              "last_error": state.get("last_error"), "last_message": state.get("last_message"),
              "decisions": len(state.get("decisions") or []), "closed_trades": len(closed)}
    issues = []
    if age_h is None or age_h > 2.5:
        issues.append(f"last run {detail['age_hours']} h ago (it should run hourly)")
    if state.get("last_error"):
        issues.append(f"last error: {state.get('last_error')}")
    if issues:
        return _result("Paper trader", "trading", "warn", "Paper trader: " + "; ".join(issues), **detail)
    return _result("Paper trader", "trading", "ok",
                   f"Paper trader ran {detail['age_hours']} h ago; {detail['decisions']} decisions, {len(closed)} closed paper trades.", **detail)


def check_strategy_lab(status_path: Path = ROOT / "data" / "strategy_lab" / "status.json", now: Optional[datetime] = None) -> dict:
    now = now or _now_utc()
    status = _read_json(status_path)
    if not isinstance(status, dict):
        return _result("Strategy Lab", "research", "warn", "The Strategy Lab has not run yet.")
    issues = []
    if status.get("state") == "running":
        beat = _parse_utc(status.get("heartbeat"))
        if beat and (now - beat) > timedelta(minutes=10):
            issues.append(f"a run stopped without finishing (heartbeat {status.get('heartbeat')}); the next run ignores it")
    else:
        finished = _parse_utc(status.get("last_finished_at"))
        if not finished or (now - finished) > timedelta(hours=3):
            issues.append(f"last finished {status.get('last_finished_at')} (it should run hourly)")
    if status.get("problems"):
        issues.append(f"data problems: {status.get('problems')}")
    if issues:
        return _result("Strategy Lab", "research", "warn", "Strategy Lab: " + "; ".join(issues), **status)
    return _result("Strategy Lab", "research", "ok",
                   f"Strategy Lab {status.get('state')}; last run evaluated {status.get('evaluated_last_run', status.get('evaluated_this_run'))} strategies.",
                   **status)


def check_ai_employee(runs_path: Path = ROOT / "data" / "ai_employee" / "runs.json", now: Optional[datetime] = None) -> dict:
    """The daily read-only Claude Code review (src/ai_employee.py): did it run, and did it succeed?"""
    now = now or _now_utc()
    runs = _read_json(runs_path)
    if not isinstance(runs, list) or not runs:
        return _result("AI employee", "research", "info", "The AI employee has not run yet (daily read-only review at 07:15).")
    last = runs[-1]
    last_ok = next((run for run in reversed(runs) if run.get("status") == "ok"), None)
    finished = _parse_utc((last_ok or {}).get("finished_at"))
    detail = {"last_status": last.get("status"), "last_error": last.get("error"), "last_ok": (last_ok or {}).get("finished_at"),
              "runs": len(runs)}
    if last.get("status") != "ok":
        return _result("AI employee", "research", "warn", f"AI employee's last run failed: {str(last.get('error'))[:200]}", **detail)
    if not finished or now - finished > timedelta(hours=30):
        return _result("AI employee", "research", "warn",
                       f"AI employee has not completed a review since {detail['last_ok']} UTC (it runs daily at 07:15).", **detail)
    return _result("AI employee", "research", "ok",
                   f"AI employee reviewed the system at {detail['last_ok']} UTC; {last.get('proposals_added', 0)} new proposal(s).",
                   **detail)


def parse_task_csv(csv_text: str) -> dict[str, dict]:
    tasks = {}
    for row in csv.DictReader(io.StringIO(csv_text)):
        name = (row.get("TaskName") or "").strip()
        if not name or name == "TaskName":
            continue
        tasks[name.lstrip("\\")] = {"status": row.get("Status"), "last_result": (row.get("Last Result") or "").strip(),
                                    "last_run": row.get("Last Run Time"), "next_run": row.get("Next Run Time"),
                                    "runs": (row.get("Task To Run") or "").strip()}
    return tasks


def check_scheduled_tasks(csv_text: Optional[str] = None) -> dict:
    if csv_text is None:
        csv_text = subprocess.run(["schtasks", "/query", "/fo", "CSV", "/v"], capture_output=True, text=True, timeout=90).stdout
    found = parse_task_csv(csv_text)
    missing = [name for name in TASKS if name not in found]
    failing = {name: found[name]["last_result"] for name in TASKS if name in found and found[name]["last_result"] not in TASK_OK_RESULTS}
    # A task can exist, report OK and still run the fallback copy of the system: on 16-18 Sep 2026 the Daily Agent
    # did exactly that for two days, journalling into the fallback folder while the live pages read from here.
    elsewhere = {name: row["runs"] for name, row in found.items()
                 if name.startswith("SmartEntry") and row.get("runs")
                 and str(ROOT).lower() not in row["runs"].lower()}
    detail = {"tasks": {name: found.get(name) for name in TASKS}, "missing": missing, "failing": failing,
              "running_from_elsewhere": elsewhere, "live_folder": str(ROOT)}
    if missing or failing or elsewhere:
        parts = ([f"missing: {', '.join(missing)}"] if missing else []) + \
                ([f"last result not OK: {', '.join(f'{k} ({v})' for k, v in failing.items())}"] if failing else []) + \
                ([f"running from outside the live folder: {', '.join(elsewhere)}"] if elsewhere else [])
        return _result("Scheduled tasks", "schedule", "warn", "Scheduled tasks " + "; ".join(parts), **detail)
    return _result("Scheduled tasks", "schedule", "ok",
                   f"All {len(TASKS)} scheduled tasks exist, last ran OK and run from the live folder.", **detail)


def check_data_freshness(get: GetJson = get_json) -> dict:
    code, feed = get("/api/data-feed", 120)
    if code != 200 or not isinstance(feed, dict):
        return _result("Market data", "data", "warn", "The data feed could not be read.", error=(feed or {}).get("error"))
    stale, closed = [], []
    for item in feed.get("symbols") or []:
        for row in item.get("timeframes") or []:
            if not row.get("available"):
                stale.append(f"{item.get('symbol')} {row.get('timeframe')} unavailable")
            elif row.get("stale"):
                (closed if item.get("market_closed") else stale).append(f"{item.get('symbol')} {row.get('timeframe')}")
    if stale:
        return _result("Market data", "data", "warn", "Stale or missing candles while the market is open: " + ", ".join(stale), stale=stale)
    note = f" (market closed: {', '.join(closed)})" if closed else ""
    return _result("Market data", "data", "ok", "Broker candles are fresh on every timeframe" + note + ".", closed=closed)


ERROR_LINE = re.compile(r"\b(ERROR|CRITICAL)\b|^Traceback|\" 500 -")
STAMP = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")


def check_app_errors(log_path: Path = ROOT / "logs" / "app_stderr.log", now_local: Optional[datetime] = None,
                     hours: int = 24) -> dict:
    now_local = now_local or datetime.now()
    path = Path(log_path)
    if not path.exists():
        return _result("App errors", "app", "info", "No app error log found.")
    with path.open("rb") as handle:
        size = path.stat().st_size
        handle.seek(max(0, size - 600_000))
        lines = handle.read().decode("utf-8", errors="replace").splitlines()
    since = now_local - timedelta(hours=hours)
    current, hits = None, []
    for line in lines:
        match = STAMP.match(line)
        if match:
            try:
                current = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S")
            except ValueError:
                pass
        if current is not None and current >= since and ERROR_LINE.search(line):
            hits.append(line.strip()[:240])
    if hits:
        return _result("App errors", "app", "warn", f"{len(hits)} error lines in the app log in the last {hours} h.",
                       count=len(hits), latest=hits[-5:])
    return _result("App errors", "app", "ok", f"No errors in the app log in the last {hours} h.")


def _available_ram_mb() -> Optional[int]:
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    class MemoryStatus(ctypes.Structure):
        _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD), ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong), ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong), ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

    status = MemoryStatus()
    status.dwLength = ctypes.sizeof(status)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GlobalMemoryStatusEx.argtypes = [ctypes.c_void_p]
    if not kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return None
    return int(status.ullAvailPhys // 2 ** 20)


def process_memory(csv_text: Optional[str] = None, limit: int = 4) -> dict:
    """Which processes hold the RAM, from ``tasklist``.

    Free memory is a whole-machine number: a browser or a second editor moves it as much as this system does. Without
    naming the processes, a "low RAM" line reads like an accusation against the trading app, and on 17 Sep 2026 that
    guess was wrong - the app held 509 MB while browser windows held about three times that.
    """
    if csv_text is None:
        try:
            csv_text = subprocess.run(["tasklist", "/fo", "csv", "/nh"], capture_output=True, text=True,
                                      timeout=60).stdout
        except Exception as exc:
            return {"available": False, "reason": f"the process list could not be read: {exc}"}
    rows = []
    for row in csv.reader(io.StringIO(csv_text or "")):
        if len(row) < 5:
            continue
        try:
            rows.append({"name": row[0], "pid": row[1], "mb": int(row[4].replace(",", "").replace(" K", "").strip() or 0) // 1024})
        except ValueError:
            continue
    rows.sort(key=lambda r: r["mb"], reverse=True)
    by_name: dict[str, int] = {}
    for row in rows:
        by_name[row["name"]] = by_name.get(row["name"], 0) + row["mb"]
    top = sorted(by_name.items(), key=lambda item: item[1], reverse=True)[:limit]
    return {"available": bool(rows), "top": [{"name": name, "mb": mb} for name, mb in top],
            "by_pid": {row["pid"]: row["mb"] for row in rows[:40]},
            "reason": None if rows else "tasklist returned nothing"}


def check_resources(ram_mb: Optional[int] = None, disk_free_gb: Optional[float] = None,
                    processes: Optional[dict] = None) -> dict:
    ram_mb = _available_ram_mb() if ram_mb is None else ram_mb
    disk_free_gb = shutil.disk_usage(ROOT.anchor).free / 2 ** 30 if disk_free_gb is None else disk_free_gb
    processes = process_memory() if processes is None else processes
    detail = {"free_ram_mb": ram_mb, "free_disk_gb": round(disk_free_gb, 1), "processes": processes}
    issues = []
    if ram_mb is not None and ram_mb < 500:
        issues.append(f"only {ram_mb} MB RAM free")
    if disk_free_gb < 5:
        issues.append(f"only {disk_free_gb:.1f} GB disk free")
    if issues:
        holders = ", ".join(f"{p['name']} {p['mb']} MB" for p in (processes.get("top") or [])[:3])
        return _result("PC resources", "pc", "warn",
                       "; ".join(issues) + (f". Largest users: {holders}." if holders else ""), **detail)
    return _result("PC resources", "pc", "ok", f"{ram_mb} MB RAM and {disk_free_gb:.0f} GB disk free.", **detail)


RAM_DROP_MB = 500
RAM_TREND_HOURS = 6


def ram_trend(history: list, current_mb: Optional[int], app_pid: Optional[str], now: datetime) -> Optional[dict]:
    """Free-RAM change against earlier checks of the same app process (no restart in between); None if nothing to compare."""
    if current_mb is None or not app_pid:
        return None
    earlier = []
    for row in history or []:
        try:
            at = datetime.strptime(str(row.get("generated_at")), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if row.get("app_pid") == app_pid and row.get("free_ram_mb") is not None and now - at <= timedelta(hours=RAM_TREND_HOURS):
            earlier.append(row)
    if not earlier:
        return None
    peak = max(earlier, key=lambda row: row["free_ram_mb"])
    drop = int(peak["free_ram_mb"]) - int(current_mb)
    return {"app_pid": app_pid, "since": peak["generated_at"], "peak_free_ram_mb": peak["free_ram_mb"], "drop_mb": drop,
            "checks_compared": len(earlier), "sharp_drop": drop >= RAM_DROP_MB}


def apply_ram_trend(checks: list, history: list, now: datetime) -> None:
    """Add the RAM trend to the PC resources check; a sharp drop without an app restart is info, never a warning."""
    resources = next((c for c in checks if c.get("name") == "PC resources"), None)
    server = next((c for c in checks if c.get("name") == "App server"), None)
    pids = ((server or {}).get("detail") or {}).get("pids") or []
    if resources is None or len(pids) != 1:
        return
    trend = ram_trend(history, (resources.get("detail") or {}).get("free_ram_mb"), pids[0], now)
    if trend is None:
        return
    resources.setdefault("detail", {})["ram_trend"] = trend
    if trend["sharp_drop"]:
        # Attribute the drop before implying the trading app leaked: the app's own working set is the only number
        # that can say so, and a browser can move free RAM by a gigabyte without this system growing at all.
        app_mb = ((resources.get("detail") or {}).get("processes") or {}).get("by_pid", {}).get(str(trend["app_pid"]))
        trend["app_rss_mb"] = app_mb
        resources["summary"] += f" Free RAM fell {trend['drop_mb']} MB since {trend['since']} UTC."
        if app_mb is not None:
            resources["summary"] += f" The app process (PID {trend['app_pid']}) itself holds {app_mb} MB."
        holders = ", ".join(f"{p['name']} {p['mb']} MB"
                            for p in (((resources.get("detail") or {}).get("processes") or {}).get("top") or [])[:3])
        if holders:
            resources["summary"] += f" Largest users now: {holders}."
        if resources["status"] == "ok":
            resources["status"] = "info"


from .runtime_paths import LEARNING_WINDOW, inside_learning_window  # noqa: E402  shared with the training gate
MODEL_FILE_PATTERNS = ("xauusd_*", "btcusd_*")


def check_model_integrity(models_dir: Path = ROOT / "models",
                          restores_path: Path = ROOT / "data" / "model_restores.json") -> dict:
    """Fail when a live champion file changed outside the 05:30 learning window and is not a logged restore.

    On 15-16 Sep 2026 test runs trained straight into models/ and replaced the models behind /api/signals; nothing
    noticed. A file is accepted when its write time falls in the learning window (the task trigger converted to UTC), or when its SHA-256 matches a
    file recorded in data/model_restores.json (restores keep the champion's original write time).
    """
    restored: dict[str, set] = {}
    for entry in _read_json(restores_path) or []:
        for name, digest in ((entry or {}).get("files") or {}).items():
            restored.setdefault(name, set()).add(digest)
    files = sorted({p for pattern in MODEL_FILE_PATTERNS for p in Path(models_dir).glob(pattern) if p.is_file()})
    if not files:
        return _result("Model integrity", "learning", "warn", f"No live model files found in {models_dir}.")
    start, end = LEARNING_WINDOW
    outside = []
    for path in files:
        written = datetime.fromtimestamp(path.stat().st_mtime)
        if inside_learning_window(datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)):
            continue
        if path.name in restored and hashlib.sha256(path.read_bytes()).hexdigest() in restored[path.name]:
            continue
        outside.append(f"{path.name} ({written:%Y-%m-%d %H:%M})")
    if outside:
        return _result("Model integrity", "learning", "fail",
                       f"{len(outside)} live model file(s) changed outside the {start}-{end} learning window and are not a "
                       f"logged restore: {', '.join(outside[:6])}. Something other than the Daily Learning task wrote "
                       "models/ (test runs did on 15-16 Sep 2026).", outside=outside)
    return _result("Model integrity", "learning", "ok",
                   f"All {len(files)} live model files come from the {start}-{end} learning window or a logged restore.")


def check_code_compiles() -> dict:
    import py_compile

    files = [ROOT / "app.py"] + sorted((ROOT / "src").glob("*.py")) + sorted((ROOT / "trading").glob("*.py"))
    broken = []
    for path in files:
        try:
            py_compile.compile(str(path), doraise=True)
        except py_compile.PyCompileError as exc:
            broken.append(f"{path.name}: {str(exc).splitlines()[-1][:160]}")
    if broken:
        return _result("Code compiles", "code", "fail", f"{len(broken)} file(s) do not compile.", broken=broken)
    return _result("Code compiles", "code", "ok", f"{len(files)} Python files compile.")


def check_tests(timeout: int = 900) -> dict:
    files = [f for f in TEST_FILES if (ROOT / f).exists()]
    try:
        proc = subprocess.run([sys.executable, "-m", "pytest", "-q", *files], cwd=str(ROOT), capture_output=True, text=True,
                              timeout=timeout, env={**os.environ, "PYTHONIOENCODING": "utf-8", "RESEARCH_JOBS": "1"})
    except subprocess.TimeoutExpired:
        return _result("Test suite", "code", "fail", f"Tests did not finish within {timeout} s.")
    tail = [line for line in proc.stdout.splitlines() if line.strip()][-1:] or ["no output"]
    if proc.returncode != 0:
        failures = [line for line in proc.stdout.splitlines() if line.startswith("FAILED") or line.startswith("ERROR")][:10]
        return _result("Test suite", "code", "fail", f"Tests failed: {tail[0]}", failures=failures)
    return _result("Test suite", "code", "ok", f"Tests pass: {tail[0]}", files=len(files))


# --------------------------------------------------------------------------- fixes and runner
def _ps_quote(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def task_trigger_ps(schedule: list) -> str:
    """PowerShell trigger for a schtasks-style schedule (["/sc", "hourly", "/mo", "1", "/st", "00:05"] etc.)."""
    opts = {schedule[i].lower(): schedule[i + 1] for i in range(0, len(schedule) - 1, 2)}
    kind, every, start = opts.get("/sc", "").lower(), int(opts.get("/mo", "1")), opts.get("/st", "00:00")
    if kind == "hourly":
        return f"New-ScheduledTaskTrigger -Once -At {_ps_quote(start)} -RepetitionInterval (New-TimeSpan -Hours {every})"
    if kind == "minute":
        return f"New-ScheduledTaskTrigger -Once -At {_ps_quote(start)} -RepetitionInterval (New-TimeSpan -Minutes {every})"
    if kind == "daily":
        return f"New-ScheduledTaskTrigger -Daily -DaysInterval {every} -At {_ps_quote(start)}"
    if kind == "onlogon":
        # Only this user's logon, matching the interactive-token principal; no password is involved.
        return "New-ScheduledTaskTrigger -AtLogOn -User ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name)"
    raise ValueError(f"unsupported schedule {schedule}")


def task_register_command(name: str, script: Path, schedule: list) -> list[str]:
    """Register a task for the current user with an interactive token and limited rights: no stored password, no prompt."""
    ps = "; ".join([
        "$ErrorActionPreference = 'Stop'",
        f"$action = New-ScheduledTaskAction -Execute {_ps_quote(script)}",
        f"$trigger = {task_trigger_ps(schedule)}",
        "$principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name)"
        " -LogonType Interactive -RunLevel Limited",
        "$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew",
        f"Register-ScheduledTask -TaskName {_ps_quote(name)} -Action $action -Trigger $trigger -Principal $principal"
        " -Settings $settings -Force | Out-Null",
        f"'registered ' + {_ps_quote(name)}",
    ])
    return ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", ps]


def fix_missing_tasks(check: dict, run: Callable = subprocess.run) -> list[dict]:
    applied = []
    for name in (check.get("detail") or {}).get("missing") or []:
        spec = TASKS[name]
        script = ROOT / "scripts" / spec["script"]
        if not script.exists():
            applied.append({"fix": "recreate_missing_scheduled_task", "task": name, "ok": False, "reason": f"{script} missing"})
            continue
        proc = run(task_register_command(name, script, spec["schedule"]), capture_output=True, text=True, timeout=120)
        applied.append({"fix": "recreate_missing_scheduled_task", "task": name, "ok": proc.returncode == 0,
                        "output": (proc.stdout or proc.stderr or "").strip()[:200]})
    return applied


REQUIRED_TERMINALS = {
    r"C:\Users\th_em\AppData\Roaming\MetaTrader\terminal64.exe": "MT5 demo 11581419 (SmartEntry strategies)",
    r"C:\Program Files\MetaTrader 5\terminal64.exe": "MT5 demo 25446287 (Atomic panel, SwingTrendPullback)",
    r"C:\Users\th_em\AppData\Roaming\CMC Markets MetaTrader 4\terminal.exe": "MT4 bridge 12755139",
}


def check_terminals(run: Callable = subprocess.run) -> dict:
    """Which MetaTrader terminals are running, by executable path.

    The app is useless without them: the strategies read broker candles and place orders through MT5,
    and MT4 quotes come over the DWX bridge. Nothing watched them until now - the autostart brings them
    up at logon, but a terminal that dies at noon was invisible until a cycle failed.

    Matched on the full path, not the process name, because five terminals run here and two of them are
    both called terminal64.exe. Only the three the system actually depends on are required; the other
    two MT4s are the owner's and are not this system's business.
    """
    running = set()
    try:
        proc = run(["powershell", "-NoProfile", "-Command",
                    "Get-CimInstance Win32_Process -Filter \"Name='terminal64.exe' OR Name='terminal.exe'\" "
                    "| ForEach-Object { $_.ExecutablePath }"],
                   capture_output=True, text=True, timeout=90)
        running = {line.strip() for line in (proc.stdout or "").splitlines() if line.strip()}
    except Exception as exc:
        return _result("MetaTrader terminals", "broker", "warn", f"Could not list terminals: {exc}")
    missing = {path: name for path, name in REQUIRED_TERMINALS.items() if path not in running}
    if missing:
        return _result("MetaTrader terminals", "broker", "fail",
                       f"{len(missing)} required terminal(s) not running: {', '.join(missing.values())}. "
                       "scripts\\start_everything.ps1 starts them.",
                       missing=list(missing), running=sorted(running))
    return _result("MetaTrader terminals", "broker", "ok",
                   f"All {len(REQUIRED_TERMINALS)} required terminals are running "
                   f"({len(running)} in total).", running=sorted(running))


def fix_missing_terminals(check: dict, run: Callable = subprocess.run) -> list[dict]:
    """Start the terminals that are not running, through the same script the autostart uses.

    It is the same idempotent script, so it cannot start a duplicate of one that is already up, and a
    terminal that is running but merely disconnected is left alone - that is a network problem, not a
    missing process, and restarting a live terminal would drop the owner's experts with it.
    """
    script = ROOT / "scripts" / "start_everything.ps1"
    if not script.exists():
        return [{"fix": "start_missing_terminals", "ok": False, "reason": f"{script} missing"}]
    try:
        run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
            capture_output=True, text=True, timeout=300)
    except Exception as exc:
        return [{"fix": "start_missing_terminals", "ok": False, "reason": f"{type(exc).__name__}: {exc}"}]
    return [{"fix": "start_missing_terminals", "ok": True,
             "reason": f"ran start_everything.ps1 for: {', '.join((check.get('detail') or {}).get('missing') or [])}"}]


def fix_dead_app(check: dict, run: Callable = subprocess.run) -> list[dict]:
    """Start the trading app again when nothing is listening on port 5000.

    Why this is needed. The machine rebooted at 23:33 on 22 September 2026 and the app never came
    back: the Startup shortcut only fires on an interactive logon, and nothing else watches. Every
    hourly cycle of all four demo strategies then failed with "connection refused" for seven hours,
    because they ask the running app to do the work. No trade was actually lost - checked against the
    candles rather than assumed - but nothing would have caught it if one had been.

    This check already existed and already reported the app as down every thirty minutes. It simply
    did nothing about it, so the repair belongs here: it covers a reboot, a crash, the relauncher loop
    dying and an out-of-memory kill alike, rather than only the boot case a logon task would fix.

    It launches ``start_trading.bat`` rather than python directly, because that script pins MT5_PATH -
    without it MetaTrader5 binds to whichever of the two terminals Windows offers, and the strategies
    halt on the wrong account. The script refuses to start a second server, so this cannot double up.
    """
    launcher = ROOT / "start_trading.bat"
    if not launcher.exists():
        return [{"fix": "restart_dead_app", "ok": False, "reason": f"{launcher} missing"}]
    try:
        run(["cmd", "/c", "start", "", "/min", str(launcher)], cwd=str(ROOT), timeout=60)
    except Exception as exc:                      # a failed repair must never fail the health check
        return [{"fix": "restart_dead_app", "ok": False, "reason": f"{type(exc).__name__}: {exc}"}]
    return [{"fix": "restart_dead_app", "ok": True, "reason": "started start_trading.bat"}]


def overall_status(checks: list[dict]) -> str:
    worst = max((STATUS_RANK.get(c["status"], 0) for c in checks), default=0)
    return {0: "healthy", 1: "warnings", 2: "problems"}[worst]


def run_doctor(deep: bool = False, fix: bool = False, get: GetJson = get_json, save: bool = True) -> dict:
    started = _now_utc()
    runners = [check_app_process, lambda: check_app_http(get), lambda: check_brokers(get), check_terminals, check_autonomy,
               lambda: check_demo_execution(get), check_demo_pullback,
               lambda: check_demo_pullback(ROOT / "data" / "paper_trading" / "demo_volatility_breakout_state.json",
                                           ROOT / "data" / "paper_trading" / "demo_volatility_breakout.json",
                                           label="Demo breakout", task="SmartEntry Demo Breakout"),
               check_paper_trader, check_atomic_analyst, check_positioning, check_i40_pilot, check_model_drift, check_strategy_lab, check_ai_employee, check_scheduled_tasks,
               lambda: check_data_freshness(get), check_app_errors, check_resources, check_model_integrity]
    if deep:
        runners += [check_code_compiles, check_tests]
    checks = []
    for runner in runners:
        try:
            checks.append(runner())
        except Exception as exc:  # a broken check is itself a finding, never a crash
            checks.append(_result(getattr(runner, "__name__", "check"), "doctor", "warn", f"Check could not run: {exc}"))
    try:
        apply_ram_trend(checks, _read_json(HISTORY_PATH) or [], started)
    except Exception:  # the trend is an extra; the checks above stand without it
        pass
    fixes = []
    if fix:
        task_check = next((c for c in checks if c["name"] == "Scheduled tasks"), None)
        if task_check and (task_check["detail"] or {}).get("missing"):
            fixes = fix_missing_tasks(task_check)
        # Only when NOTHING listens. A "too many servers" fail must never be answered by starting
        # another one, which is why this tests the check's own detail rather than the status alone.
        app_check = next((c for c in checks if c["name"] == "App server"), None)
        if app_check and app_check["status"] == "fail" and not (app_check.get("detail") or {}).get("pids"):
            fixes += fix_dead_app(app_check)
        term_check = next((c for c in checks if c["name"] == "MetaTrader terminals"), None)
        if term_check and (term_check.get("detail") or {}).get("missing"):
            fixes += fix_missing_terminals(term_check)
    report = {
        "generated_at": started.strftime("%Y-%m-%d %H:%M:%S"),
        "duration_sec": round((_now_utc() - started).total_seconds(), 1),
        "deep": bool(deep), "fix": bool(fix), "overall": overall_status(checks),
        "counts": {s: sum(1 for c in checks if c["status"] == s) for s in ("ok", "info", "warn", "fail")},
        "checks": checks, "fixes_applied": fixes, "safe_fixes": list(SAFE_FIXES), "places_orders": False,
    }
    if save:
        store_health_report(report)
    return report


def store_health_report(report: dict, health_dir: Path = HEALTH_DIR) -> None:
    """Write the doctor's latest report and append it to the health history (unrelated to research reports)."""
    health_dir.mkdir(parents=True, exist_ok=True)
    latest = health_dir / LATEST_PATH.name
    tmp = latest.with_suffix(".tmp")
    tmp.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    os.replace(tmp, latest)
    if report.get("deep"):
        deep_path = health_dir / "doctor_latest_deep.json"
        deep_path.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    history_path = health_dir / HISTORY_PATH.name
    history = _read_json(history_path) or []
    history.append({"generated_at": report["generated_at"], "overall": report["overall"], "deep": report["deep"],
                    "counts": report["counts"],
                    "free_ram_mb": next(((c.get("detail") or {}).get("free_ram_mb") for c in report["checks"] if c["name"] == "PC resources"), None),
                    "app_pid": next((((c.get("detail") or {}).get("pids") or [None])[0] for c in report["checks"]
                                     if c["name"] == "App server" and len((c.get("detail") or {}).get("pids") or []) == 1), None),
                    "problems": [f"{c['name']}: {c['summary']}" for c in report["checks"] if c["status"] in ("warn", "fail")]})
    history_path.write_text(json.dumps(history[-HISTORY_KEPT:], indent=1, default=str), encoding="utf-8")


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="System Doctor: health checks for the trading system (never trades).")
    parser.add_argument("--deep", action="store_true", help="also compile the code and run the test suite")
    parser.add_argument("--fix", action="store_true", help=f"apply safe fixes only: {', '.join(SAFE_FIXES)}")
    args = parser.parse_args(argv)
    report = run_doctor(deep=args.deep, fix=args.fix)
    print(f"overall: {report['overall']}  {report['counts']}  ({report['duration_sec']} s)")
    for check in report["checks"]:
        print(f"[{check['status'].upper():4s}] {check['name']}: {check['summary']}")
    for applied in report["fixes_applied"]:
        print("fix:", applied)
    return 0 if report["overall"] != "problems" else 1


if __name__ == "__main__":
    sys.exit(main())
