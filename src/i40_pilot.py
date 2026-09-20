"""i40 Pilot - the system's big brain: what it is, what it knows, what it may do (owner request 2026-09-17).

The trading system grew into nine scheduled tasks, three executing strategies, two models and twenty-odd pages, and
nothing held the whole picture in one place. This module assembles that picture from the system's own files and
endpoints, so a person or an agent can read one brief instead of nine.

It is deliberately **read-only**. It runs no strategy, places no order, trains nothing and writes exactly one file,
``data/i40_pilot/latest.json``. Every section reports ``available: false`` with a reason rather than filling a gap
with a plausible number, because a brain that guesses is worse than one that admits it does not know.

Eight parts, the ones the owner asked for:

* **identity**   - who the pilot is and which copy of the system it is looking at (folder, branch, commit).
* **rules**      - the signature rules: the owner's standing decisions, stated once and never silently relaxed.
* **memory**     - the written record: BASELINE.md results, NOTES.md history, LESSONS.md, BACKLOG.md.
* **context**    - what is true right now: strategies, models, learning, positioning, data.
* **schedule**   - the scheduled tasks with their last and next run, straight from Windows Task Scheduler.
* **skills**     - the repeatable procedures in .claude/skills.
* **tools**      - the command-line entry points and HTTP endpoints the system exposes.
* **loop**       - how often this brief refreshes, and how old the one on disk is.

Run: python -m src.i40_pilot [--json] [--no-http]
"""
from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from .demo_executor import _stamp          # one UTC stamp format across the system
from .runtime_paths import read_latest_json, smartentry_data_dir, smartentry_models_dir

NAME = "i40 Pilot"
ROLE = ("The trading system's brain: it reads everything the system knows, states it in one place, and decides "
        "nothing. Orders come from the strategies and from the owner, never from this module.")
REFRESH_MINUTES = 60
ROOT = Path(__file__).resolve().parents[1]
MEMORY_DIR = ROOT / ".claude" / "memory"
SKILLS_DIR = ROOT / ".claude" / "skills"

# The owner's standing decisions. They are written here, in code, because a rule that lives only in a chat log gets
# forgotten: every one of these was set explicitly by the owner and none of them may be relaxed without them saying so.
SIGNATURE_RULES = [
    {"rule": "The deflated Sharpe bar stays at 0.95",
     "why": "A near miss is still a miss; lowering the bar to let a candidate through is how a losing strategy reaches "
            "real money.",
     "since": "2026-09-14"},
    {"rule": "No change to the live SwingTrendPullback inputs or preset until a candidate clears that bar",
     "why": "The running expert is the one thing touching the funded account; research and separate presets are free, "
            "the live inputs are not.",
     "since": "2026-09-14"},
    {"rule": "Only the SwingTrendPullback expert may be edited",
     "why": "Every other expert on the terminals belongs to the owner; they are not to be edited, disabled, "
            "re-attached or argued about, and their terminals are never restarted.",
     "since": "2026-09-13"},
    {"rule": "Automated orders go to demo account 11581419 only",
     "why": "Both demo strategies refuse to run if the logged-in account is not that demo login, so a mis-pointed "
            "terminal stops them instead of trading the funded account.",
     "since": "2026-09-16"},
    {"rule": "HOLD is a real state and never reaches execution",
     "why": "A non-directional reading has no levels; anything that turns it into an order has invented them.",
     "since": "2026-09-12"},
    {"rule": "Report only true results; a bad row recorded is worth more than a good row invented",
     "why": "Every verdict, page label and backtest row has to survive being checked; a flattering number costs money "
            "later.",
     "since": "2026-09-14"},
    {"rule": "Never invent data to fill a gap - say it is unavailable",
     "why": "A plausible-looking number is indistinguishable from a real one on a dashboard, and that is exactly when "
            "it does damage.",
     "since": "2026-09-12"},
    {"rule": "The live champions are retrained only inside the Daily Learning task's own window",
     "why": "Training writes straight over the live model files; an on-demand retrain once replaced the champions "
            "behind /api/signals in the middle of a trading day.",
     "since": "2026-09-16"},
    {"rule": "Back up app.py before editing it, and never edit old BASELINE.md rows",
     "why": "The app serves a funded account, and a result history that can be rewritten is not a record.",
     "since": "2026-09-14"},
    {"rule": "One change at a time, the safest option, and every commit on claude/* pushed",
     "why": "Small reversible steps on a system that can place orders; work that is not pushed is work that can be "
            "lost.",
     "since": "2026-09-15"},
]

INSTRUCTIONS = [
    "Read the whole brief before answering a question about the system; the answer is usually already in it.",
    "State what is measured and what is missing. An unavailable section is a fact, not something to work around.",
    "Quote the signature rules when a request would cross one, and say which rule and why.",
    "Never place, size or modify an order from here, and never enable automatic execution.",
    "Prefer the system's own entry points over new code; if something is missing, say so rather than improvising.",
]


def pilot_dir() -> Path:
    return smartentry_data_dir() / "i40_pilot"


def _git(*args: str) -> Optional[str]:
    try:
        out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=20)
        return out.stdout.strip() or None
    except Exception:
        return None


def identity() -> dict:
    """Who the pilot is and which copy of the system it is reading."""
    return {"name": NAME, "role": ROLE, "folder": str(ROOT), "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
            "commit": _git("rev-parse", "--short", "HEAD"),
            "commit_subject": _git("log", "-1", "--pretty=%s"),
            "reads_only": True, "places_orders": False}


def _tail(path: Path, lines: int) -> list[str]:
    try:
        return [line.rstrip() for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()][-lines:]
    except OSError:
        return []


def memory(memory_dir: Optional[Path] = None) -> dict:
    """The written record: how many results have been logged, what was decided, what is still open."""
    directory = Path(memory_dir) if memory_dir else MEMORY_DIR
    if not directory.exists():
        return {"available": False, "reason": f"no memory folder at {directory}"}
    baseline = directory / "BASELINE.md"
    rows = [line for line in _tail(baseline, 10_000) if line.startswith("|") and not line.startswith("|---")]
    notes = _tail(directory / "NOTES.md", 6)
    # LESSONS.md stores one lesson per BULLET ("- YYYY-MM-DD [area]: ..."), stated in its own header line.
    # Counting "#" lines instead reported 1 lesson where there are 17 - the memory pillar undercounting
    # itself by seventeen times, in the one section the owner named first.
    lessons = [line for line in _tail(directory / "LESSONS.md", 10_000)
               if line.strip().startswith("- ") and not line.strip().startswith("- [ ]")]
    backlog = [line for line in _tail(directory / "BACKLOG.md", 10_000) if line.strip().startswith(("- [ ]", "* [ ]"))]
    return {"available": True, "folder": str(directory),
            "baseline_rows": max(0, len(rows) - 1),          # the header row is not a result
            "latest_results": rows[-3:],
            "recent_notes": notes,
            "lessons": lessons[:12], "lesson_count": len(lessons),
            "open_backlog": backlog[:10], "open_backlog_count": len(backlog),
            "note": "BASELINE.md rows are appended and never edited, so this count is the whole measured history."}


def skills(skills_dir: Optional[Path] = None) -> dict:
    """The repeatable procedures kept in .claude/skills, with the one-line description each declares."""
    directory = Path(skills_dir) if skills_dir else SKILLS_DIR
    if not directory.exists():
        return {"available": False, "reason": f"no skills folder at {directory}"}
    found = []
    for skill_file in sorted(directory.glob("*/SKILL.md")):
        description = ""
        for line in _tail(skill_file, 10_000)[:12]:
            if line.lower().startswith("description:"):
                description = line.split(":", 1)[1].strip()
                break
        try:
            shown = str(skill_file.relative_to(ROOT))
        except ValueError:                       # a folder outside the repo (the test suite passes a temporary one)
            shown = str(skill_file)
        found.append({"name": skill_file.parent.name, "description": description, "path": shown})
    return {"available": True, "count": len(found), "skills": found}


def schedule(csv_text: Optional[str] = None) -> dict:
    """The scheduled tasks with their last and next run, read from Windows Task Scheduler."""
    from .system_doctor import TASKS, parse_task_csv

    if csv_text is None:
        try:
            csv_text = subprocess.run(["schtasks", "/query", "/fo", "CSV", "/v"],
                                      capture_output=True, text=True, timeout=90).stdout
        except Exception as exc:
            return {"available": False, "reason": f"the task list could not be read: {exc}"}
    found = parse_task_csv(csv_text or "")
    rows = [{"task": name, "schedule": " ".join(str(x) for x in TASKS[name]["schedule"]),
             "script": TASKS[name]["script"], **(found.get(name) or {"status": None, "last_run": None,
                                                                    "next_run": None, "last_result": None}),
             "registered": name in found}
            for name in TASKS]
    missing = [r["task"] for r in rows if not r["registered"]]
    return {"available": True, "count": len(rows), "missing": missing, "tasks": rows,
            "note": "Tasks only run while the owner is logged in; a missing task means that job is not happening."}


def tools(url_map=None) -> dict:
    """What the system can be asked to do: its command-line entry points and its HTTP endpoints."""
    commands = [
        {"command": "python -m src.system_doctor [--deep] [--fix]", "does": "health of the whole system; never trades"},
        {"command": "python -m src.daily_learning", "does": "the gated retrain, inside its window only"},
        {"command": "python -m src.strategy_lab run|ea|status", "does": "rule-strategy search against a locked holdout"},
        {"command": "python -m src.strategy_book update|status", "does": "re-checks kept strategies on new bars"},
        {"command": "python -m src.paper_trader", "does": "the gold 4H model forward test"},
        {"command": "python -m src.demo_session_pullback cycle|status", "does": "gold session pullback, demo only"},
        {"command": "python -m src.demo_volatility_breakout cycle|status", "does": "volatility breakout, demo only"},
        {"command": "python -m src.positioning", "does": "downloads and reads the CFTC positioning report"},
        {"command": "python -m src.plan_journal", "does": "records chart plans and how they turned out"},
        {"command": "python -m src.daily_report", "does": "writes today's market and system report"},
        {"command": "python -m src.i40_pilot", "does": "this brief"},
    ]
    endpoints = []
    if url_map is not None:
        try:
            endpoints = sorted({str(rule) for rule in url_map.iter_rules()
                                if str(rule).startswith("/api/") and "static" not in str(rule)})
        except Exception:
            endpoints = []
    return {"commands": commands, "api_endpoints": endpoints, "api_endpoint_count": len(endpoints),
            "note": "Listed so work uses the system's own entry points instead of new one-off code."}


def learning_state(path: Optional[Path] = None) -> dict:
    """The last decision the Daily Learning task recorded per symbol: whether a challenger replaced the champion.

    ``data/learning_decisions.json`` is an append-only list, so the newest entry per symbol is the live verdict.
    """
    target = Path(path) if path else smartentry_data_dir() / "learning_decisions.json"
    try:
        rows = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"available": False, "reason": "the daily learning task has not written a decision yet"}
    except (OSError, ValueError) as exc:
        return {"available": False, "reason": f"{target} is unreadable: {exc}"}
    if not isinstance(rows, list) or not rows:
        return {"available": False, "reason": f"{target} holds no decisions"}
    latest: dict[str, dict] = {}
    for row in rows:
        if isinstance(row, dict) and row.get("symbol"):
            latest[str(row["symbol"])] = row
    return {"available": True, "decisions": len(rows), "file": str(target),
            "per_symbol": {symbol: {"status": row.get("status"), "rf_promoted": row.get("rf_promoted"),
                                    "lstm_promoted": row.get("lstm_promoted"), "accuracy": row.get("accuracy"),
                                    # The field in data/learning_decisions.json is "trained_at". Reading
                                    # "at"/"timestamp"/"date" returned None every time, and because
                                    # activity() drops events with no time, LEARNING NEVER APPEARED IN THE
                                    # ACTIVITY TRAIL AT ALL - 0 of 27 events, in the pillar the owner most
                                    # wants to see moving. The older names are kept as fallbacks.
                                    "at": (row.get("trained_at") or row.get("at")
                                           or row.get("timestamp") or row.get("date")),
                                    "data_source": row.get("data_source")}
                           for symbol, row in sorted(latest.items())}}


def _as_iso(value) -> Optional[str]:
    """A timestamp as 'YYYY-MM-DD HH:MM:SS' so entries from different sources sort together.

    Task Scheduler prints local formats (``17/09/2026 22:40:00`` here) and uses 30/11/1999 to mean "never ran", which
    is dropped rather than shown as an event.
    """
    text = str(value or "").strip()
    if not text:
        return None
    for pattern in ("%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M:%S", "%m/%d/%Y %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            moment = datetime.strptime(text[:19], pattern)
        except ValueError:
            continue
        return moment.strftime("%Y-%m-%d %H:%M:%S") if moment.year >= 2000 else None
    return None


ATTENTION_ORDER = {"bad": 0, "warn": 1, "info": 2}


def health(path: Optional[Path] = None) -> dict:
    """The System Doctor's own latest verdict, read rather than re-derived.

    The doctor runs 19 checks every 30 minutes and is the richest health source the system has. Until
    20 Sep 2026 the pilot ignored it and worked out a thin subset for itself, so the two could disagree -
    and did: at 18:28 the doctor reported overall "warnings" with a live model-drift warn on both traded
    symbols, while the pilot's 17:40 brief said "Everything the pilot can check is running."
    """
    path = Path(path or ROOT / "data" / "system_health" / "doctor_latest.json")
    if not path.exists():
        return {"available": False, "reason": f"no {path.name} yet", "open": []}
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"available": False, "reason": f"unreadable: {exc}", "open": []}
    checks = report.get("checks") or []
    # The doctor's field is "status", not "level" - reading the wrong one silently returned zero open
    # findings while the report said "warnings", which is the same class of mistake this fix exists to stop.
    open_items = [{"name": c.get("name"), "area": c.get("area"), "level": c.get("status"),
                   "summary": c.get("summary") or c.get("message") or "",
                   "detail": (str(c.get("detail")) if c.get("detail") else "")[:300]}
                  for c in checks if c.get("status") in ("warn", "fail", "error")]
    return {"available": True, "generated_at": report.get("generated_at"), "overall": report.get("overall"),
            "counts": report.get("counts"), "checks_read": len(checks), "open": open_items}


def attention(brief: dict) -> dict:
    """What is wrong, in the order it deserves attention - the point of the page.

    Counting problems is useless; naming them is not. Every entry says what it is, why it matters and where to look,
    and an empty list is itself the answer: nothing is stuck.
    """
    items = []

    def add(severity: str, what: str, why: str, where: str) -> None:
        items.append({"severity": severity, "what": what, "why": why, "where": where})

    for name, strategy in ((brief.get("context") or {}).get("strategies") or {}).items():
        if not strategy.get("available"):
            add("bad", f"{name} cannot be read", strategy.get("reason") or "the endpoint did not answer",
                "/demo-trading")
            continue
        halted = strategy.get("halted")
        if halted:
            add("bad", f"{name} is halted ({halted.get('kind')})", halted.get("reason") or "", "/demo-trading")
        cycle = strategy.get("cycle_health") or {}
        if cycle.get("late"):
            add("warn", f"{name} has not run for {cycle.get('age_minutes')} min",
                cycle.get("note") or "its scheduled task may have stopped", "/demo-trading")
        account = strategy.get("account") or {}
        if account.get("available") and account.get("is_demo") is False:
            add("bad", f"{name} is pointed at a non-demo account", "automated orders are demo-only by rule",
                "/demo-trading")

    schedule_section = brief.get("schedule") or {}
    for missing in schedule_section.get("missing") or []:
        add("bad", f"the task {missing} is not registered", "that job is simply not happening", "/system-doctor")
    for task in schedule_section.get("tasks") or []:
        if task.get("registered") and str(task.get("last_result") or "0") not in ("0", "267009", "267011", ""):
            add("warn", f"{task['task']} last finished with result {task['last_result']}",
                "a non-zero result means the run failed", "/system-doctor")

    loop_section = brief.get("loop") or {}
    if loop_section.get("stale"):
        add("warn", f"this brief is {loop_section.get('age_minutes')} min old",
            "the hourly refresh has stopped, so everything here may be out of date", "/system-doctor")

    positioning = ((brief.get("context") or {}).get("positioning") or {})
    if not positioning.get("available"):
        add("info", "positioning has no data", positioning.get("reason") or "", "/positioning")

    # The System Doctor runs 19 checks every 30 minutes and is the richest health source in the system.
    # Until 20 Sep 2026 the pilot ignored its findings and re-derived a thin subset, so the two disagreed:
    # at 18:28 the doctor reported overall "warnings" with a live model-drift warn on BOTH traded symbols,
    # while the pilot's 17:40 brief said "Everything the pilot can check is running." A green control room
    # sitting on top of an open warning is exactly the flattering label the owner's standing rule forbids.
    # An open loop is not a failure, but it is the thing the owner most wants to know: the system is
    # observing and recording without anything changing as a result.
    closure = (brief.get("loop") or {}).get("closure") or {}
    if closure.get("available") and not closure.get("closing"):
        add("info", f"the learning loop is open: {closure.get('observations_since_action')} runs since "
                    f"anything changed", closure.get("note") or "", "/self-learning")

    for check in (brief.get("health") or {}).get("open") or []:
        severity = {"fail": "bad", "error": "bad", "warn": "warn"}.get(check.get("level"), "info")
        add(severity, f"{check.get('name')}: {check.get('summary')}",
            check.get("detail") or "raised by the System Doctor", "/system-doctor")

    items.sort(key=lambda item: ATTENTION_ORDER.get(item["severity"], 3))
    worst = items[0]["severity"] if items else "ok"
    return {"count": len(items), "worst": worst, "items": items,
            "headline": {"ok": "Everything the pilot can check is running.",
                         "info": "Running, with one thing worth knowing.",
                         "warn": "Running, but something needs looking at.",
                         "bad": "Something is stopped or misdirected."}[worst]}


def activity(brief: dict, limit: int = 18) -> dict:
    """The trail: what the system actually did recently, newest first.

    A state tells you where things are; a trail tells you which way they are going. Entries come from the strategies'
    own decision logs and from the scheduled tasks' last run times - nothing is synthesised.
    """
    events = []
    for name, strategy in ((brief.get("context") or {}).get("strategies") or {}).items():
        for row in strategy.get("recent_decisions") or []:
            events.append({"at": _as_iso(row.get("at")) or str(row.get("at") or "")[:19], "source": name,
                           "what": row.get("event"), "detail": row.get("reason")})
    for task in (brief.get("schedule") or {}).get("tasks") or []:
        ran = _as_iso(task.get("last_run")) if task.get("registered") else None
        if ran:                       # Task Scheduler writes 30/11/1999 for a task that has never run
            events.append({"at": ran, "source": "schedule", "what": task["task"],
                           "detail": f"last result {task.get('last_result')}"})
    learning = ((brief.get("context") or {}).get("learning") or {})
    for symbol, decision in (learning.get("per_symbol") or {}).items():
        events.append({"at": _as_iso(decision.get("at")) or "", "source": "learning", "what": f"{symbol} {decision.get('status')}",
                       "detail": f"RF {'promoted' if decision.get('rf_promoted') else 'kept'}, "
                                 f"LSTM {'promoted' if decision.get('lstm_promoted') else 'kept'}"})
    events = [e for e in events if e["at"]]
    events.sort(key=lambda e: e["at"], reverse=True)
    # A straight newest-first cut is dominated by whichever source ticks fastest. Measured 20 Sep 2026:
    # 29 events built, 18 shown, and the oldest shown was 16:01 - so the day's LEARNING decisions, stamped
    # 05:30, fell off the end and the trail read as 100 % scheduler rows. Give each source a share first,
    # then sort, so a slow but important source is not buried by a noisy one. count stays the true total.
    per_source = max(2, limit // max(1, len({e["source"] for e in events})))
    seen: dict[str, int] = {}
    fair = []
    for event in events:
        taken = seen.get(event["source"], 0)
        if taken < per_source:
            seen[event["source"]] = taken + 1
            fair.append(event)
    for event in events:                       # backfill any spare slots with the newest remaining
        if len(fair) >= limit:
            break
        if event not in fair:
            fair.append(event)
    fair.sort(key=lambda e: e["at"], reverse=True)
    return {"count": len(events), "events": fair[:limit], "sources": sorted(seen),
            "note": "Straight from the strategies' decision logs, the learning decisions and the task "
                    "scheduler; nothing here is inferred. Each source gets a share so the fastest-ticking "
                    "one cannot crowd out the rest."}


def context(get: Optional[Callable] = None) -> dict:
    """What is true right now: the strategies, the models, the learning, and the data behind them.

    Live account and strategy state is read through the running app's HTTP API, never by opening MT5 from a second
    process - two MetaTrader connections in one machine is how the bridge gets confused.
    """
    if get is None:
        from .system_doctor import get_json as get

    def endpoint(path: str) -> dict:
        code, body = get(path, 20)
        if code != 200 or not isinstance(body, dict):
            return {"available": False, "reason": f"{path} returned {code}", "error": (body or {}).get("error")}
        return {"available": True, **body}

    strategies = {}
    for key, path in (("gold_session_pullback", "/api/demo-trading/status"),
                      ("volatility_trend_breakout", "/api/demo-breakout/status")):
        body = endpoint(path)
        if not body.get("available"):
            strategies[key] = body
            continue
        strategies[key] = {"available": True, "magic": body.get("magic"), "symbol": body.get("symbol"),
                           "sending_orders": body.get("sending_orders"), "halted": body.get("halted"),
                           "account": body.get("account"), "cycle_health": body.get("cycle_health"),
                           "expectancy": body.get("expectancy"), "last_cycle": body.get("last_cycle"),
                           "open_trade": body.get("open_trade"), "today_trades": len(body.get("today_trades") or []),
                           "recent_decisions": (body.get("log") or [])[:6],
                           "backtest_verdict": body.get("backtest_verdict")}

    models_dir = smartentry_models_dir()
    model_files = sorted(models_dir.glob("*")) if models_dir.exists() else []
    models = {"available": bool(model_files), "folder": str(models_dir), "count": len(model_files),
              "newest": max((f.stat().st_mtime for f in model_files if f.is_file()), default=None)}
    if models["newest"]:
        models["newest_written"] = _stamp(datetime.fromtimestamp(models["newest"], timezone.utc))
    models.pop("newest", None)
    if not model_files:
        models["reason"] = f"no model files in {models_dir}"

    positioning = read_latest_json(pilot_dir().parent / "positioning" / "latest.json",
                                   "positioning has not been downloaded yet")
    return {"strategies": strategies, "models": models, "learning": learning_state(),
            "positioning": {"available": positioning.get("available", False),
                            "as_of": (positioning.get("report") or {}).get("as_of_tuesday"),
                            "reason": positioning.get("reason")}}


def loop_closure(now=None, path: Optional[Path] = None) -> dict:
    """Does the loop CLOSE? That is: has anything the system observed actually changed what it does?

    A refresh cadence is not a loop. Observing, recording and displaying is an OPEN loop, and this system
    has been running one: as of 20 Sep 2026 the Daily Learning task had recorded eleven consecutive runs
    saying the live model loses money on unseen bars, and not one threshold, weight or gate had moved.
    Measuring file freshness cannot see that, which is why it was worth separating the two questions.

    The observable action here is a promotion: the gate replacing a champion with a challenger. Every
    learning run is an observation; a run with rf_promoted or lstm_promoted is the loop closing.
    """
    now = now or datetime.now(timezone.utc)
    target = Path(path) if path else smartentry_data_dir() / "learning_decisions.json"
    try:
        rows = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"available": False, "reason": f"cannot read {target}"}
    if not isinstance(rows, list) or not rows:
        return {"available": False, "reason": f"{target} holds no decisions"}

    def when(row):
        return row.get("trained_at") or row.get("at") or ""

    observations = [r for r in rows if isinstance(r, dict) and when(r)]
    actions = [r for r in observations if r.get("rf_promoted") or r.get("lstm_promoted")]
    last_observation = max((when(r) for r in observations), default=None)
    last_action = max((when(r) for r in actions), default=None)
    since = None
    if last_action:
        try:
            since = round((now - datetime.fromisoformat(last_action).replace(tzinfo=timezone.utc)).days)
        except ValueError:
            since = None
    # How many observations have been made since the loop last closed: the honest measure of an open loop.
    idle = sum(1 for r in observations if last_action and when(r) > last_action)
    return {"available": True, "observations": len(observations), "actions": len(actions),
            "last_observation": last_observation, "last_action": last_action,
            "days_since_action": since, "observations_since_action": idle,
            "closing": bool(last_action) and idle == 0,
            "what_counts_as_action": "the learning gate promoting a challenger over the live champion",
            "note": ("The loop is CLOSING: the last thing observed changed what runs."
                     if last_action and idle == 0 else
                     f"The loop is OPEN: {idle} learning runs recorded since anything last changed"
                     f"{f' ({since} days)' if since is not None else ''}. Observation without action is "
                     f"not learning." if last_action else
                     "The loop has never closed: nothing observed has ever changed what runs.")}


def loop(now=None, path: Optional[Path] = None, decisions_path: Optional[Path] = None) -> dict:
    """How often the brief refreshes, how stale the copy on disk is, and whether the loop actually closes."""
    now = now or datetime.now(timezone.utc)
    target = Path(path) if path else pilot_dir() / "latest.json"
    closure = loop_closure(now, decisions_path)
    if not target.exists():
        return {"available": False, "every_minutes": REFRESH_MINUTES, "closure": closure,
                "reason": f"{target} has not been written yet (task SmartEntry i40 Pilot)"}
    age = (now.timestamp() - target.stat().st_mtime) / 60
    return {"available": True, "every_minutes": REFRESH_MINUTES, "age_minutes": round(age, 1),
            "stale": age > REFRESH_MINUTES * 2, "file": str(target), "closure": closure}


def build_brief(url_map=None, get: Optional[Callable] = None, now=None, csv_text: Optional[str] = None,
                include_http: bool = True) -> dict:
    """The whole brain in one payload. Sections that cannot be read say so; none of them is filled in with a guess."""
    now = now or datetime.now(timezone.utc)
    brief = {
        "generated_at": _stamp(now),
        "identity": identity(),
        "rules": {"count": len(SIGNATURE_RULES), "signature_rules": SIGNATURE_RULES,
                  "note": "Standing owner decisions. They are not defaults and are not relaxed without the owner."},
        "instructions": INSTRUCTIONS,
        "memory": memory(),
        "context": context(get) if include_http else {"available": False, "reason": "HTTP reads were switched off for this run"},
        "schedule": schedule(csv_text),
        "skills": skills(),
        "tools": tools(url_map),
        "loop": loop(now),
        "health": health(),
    }
    brief["attention"] = attention(brief)
    brief["activity"] = activity(brief)
    return brief


def save_brief(brief: dict, path: Optional[Path] = None) -> Path:
    target = Path(path) if path else pilot_dir() / "latest.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(brief, indent=1, default=str), encoding="utf-8")
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=f"{NAME}: the system's brain, read-only.")
    parser.add_argument("--json", action="store_true", help="print the whole brief as JSON")
    parser.add_argument("--no-http", action="store_true", help="skip the live reads through the running app")
    cli_args = parser.parse_args()

    the_brief = build_brief(include_http=not cli_args.no_http)
    written_to = save_brief(the_brief)
    if cli_args.json:
        print(json.dumps(the_brief, indent=1, default=str))
    else:
        who = the_brief["identity"]
        print(f"{who['name']} - {who['branch']} @ {who['commit']} ({who['commit_subject']})")
        print(f"  rules      {the_brief['rules']['count']} signature rules")
        mem = the_brief["memory"]
        print(f"  memory     {mem.get('baseline_rows', 0)} recorded results, {mem.get('lesson_count', 0)} lessons, "
              f"{mem.get('open_backlog_count', 0)} open backlog items" if mem.get("available")
              else f"  memory     {mem.get('reason')}")
        for strategy_key, strategy in (the_brief["context"].get("strategies") or {}).items():
            state = ("sending orders" if strategy.get("sending_orders") else "dry run") if strategy.get("available")                 else strategy.get("reason")
            print(f"  strategy   {strategy_key}: {state}")
        sched = the_brief["schedule"]
        print(f"  schedule   {sched.get('count', 0)} tasks, missing: {', '.join(sched.get('missing') or []) or 'none'}"
              if sched.get("available") else f"  schedule   {sched.get('reason')}")
        print(f"  skills     {the_brief['skills'].get('count', 0)}")
        print(f"  tools      {len(the_brief['tools']['commands'])} commands, "
              f"{the_brief['tools']['api_endpoint_count']} endpoints")
        print(f"  written    {written_to}")
