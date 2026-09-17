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
    lessons = [line for line in _tail(directory / "LESSONS.md", 10_000) if line.startswith("#")]
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
                                    "at": row.get("at") or row.get("timestamp") or row.get("date")}
                           for symbol, row in sorted(latest.items())}}


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


def loop(now=None, path: Optional[Path] = None) -> dict:
    """How often the brief refreshes, and how stale the copy on disk is."""
    now = now or datetime.now(timezone.utc)
    target = Path(path) if path else pilot_dir() / "latest.json"
    if not target.exists():
        return {"available": False, "every_minutes": REFRESH_MINUTES,
                "reason": f"{target} has not been written yet (task SmartEntry i40 Pilot)"}
    age = (now.timestamp() - target.stat().st_mtime) / 60
    return {"available": True, "every_minutes": REFRESH_MINUTES, "age_minutes": round(age, 1),
            "stale": age > REFRESH_MINUTES * 2, "file": str(target)}


def build_brief(url_map=None, get: Optional[Callable] = None, now=None, csv_text: Optional[str] = None,
                include_http: bool = True) -> dict:
    """The whole brain in one payload. Sections that cannot be read say so; none of them is filled in with a guess."""
    now = now or datetime.now(timezone.utc)
    return {
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
    }


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
