"""AI Employee (phase 1): a daily, read-only review of the trading system by Claude Code.

Every morning (Windows task "SmartEntry AI Employee", 07:15, after the System Doctor deep check at 06:30 and the
daily market report at 06:45) this module:

1. builds a compact context from the system's own reports - health, daily market report, Strategy Lab and
   strategy book, the SwingTrendPullback expert, paper trading and demo execution, recent BASELINE results -
   plus the employee's memory notes and every earlier proposal with the owner's decision;
2. runs Claude Code once in print mode on the owner's Claude subscription. Any API key is removed from the child
   environment so it never switches to API billing. It is restricted to reading files: no shell, no edits,
   no web access, no MCP connectors, and permission prompts are denied automatically;
3. validates the JSON reply and stores the brief, new proposals (a repeat of an earlier proposal is merged into
   it, never added twice) and new memory notes under ``data/ai_employee/``.

It never places, modifies or closes orders, never changes settings and never edits code. Proposals wait for the
owner's decision on the /ai-employee page.

Run:  python -m src.ai_employee run [--force]
      python -m src.ai_employee context        (build the context only, no Claude call)
      python -m src.ai_employee status
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

from .ai_provider import estimate_tokens as _estimate_tokens
from .ea_monitor import _write_json_atomic
from .system_doctor import _parse_utc, _read_json

ROOT = Path(__file__).resolve().parents[1]
EMPLOYEE_DIR = ROOT / "data" / "ai_employee"
MODEL = os.environ.get("AI_EMPLOYEE_MODEL", "sonnet")
TIMEOUT_SECONDS = 900
LOCK_STALE_MINUTES = 30
SCHEDULE_TEXT = "daily at 07:15 (task SmartEntry AI Employee)"
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
MAX_FINDINGS, MAX_PROPOSALS, MAX_NOTES_PER_RUN, MAX_QUESTIONS = 10, 5, 5, 3
MAX_MEMORY_NOTES = 150
MAX_RUNS_KEPT = 200
DUPLICATE_SIMILARITY = 0.6
HEALTH_VALUES = ("healthy", "attention", "problem")
SEVERITIES = ("info", "warn", "problem")
CATEGORIES = ("stability", "fix", "monitoring", "research", "strategy", "risk", "data")
EFFORTS = ("small", "medium", "large")
PROPOSAL_STATUSES = ("pending", "approved", "rejected", "done")
ALLOWED_TOOLS = "Read,Grep,Glob"
DENIED_TOOLS = "Bash,PowerShell,Edit,Write,NotebookEdit,WebFetch,WebSearch"
REMOVED_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
               "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")
TRADING_ACTION = re.compile(r"\b(place|open|close|execute|send)\w*\s+(\w+\s+){0,3}(trades?|orders?|positions?)\b"
                            r"|\benabl\w*\s+(auto[- ]?trad\w*|auto[- ]?execut\w*|autonomy)\b|\breal[- ]money\b|\blive account\b",
                            re.IGNORECASE)
STOP_WORDS = {"the", "and", "for", "with", "from", "into", "that", "this", "add", "use", "make", "new", "system"}

PROMPT_RULES = """You are the AI employee of a personal automated-trading research system (MetaTrader 4/5, gold and bitcoin,
Flask dashboard on this PC). You work for the owner and report to them; the owner decides everything.

YOUR JOB TODAY
Review the CONTEXT below (the system's own reports from this morning), verify anything important by reading files
under the project folder if needed (for example src/, strategies/, .claude/memory/BASELINE.md),
and give a short, specific morning brief with proposals that make the system, in this order:
more stable, then smarter, then profitable - small safe steps.

HARD RULES
- You can only read files. You cannot run commands or change anything; do not try.
- Never propose placing, closing or modifying trades, enabling auto-trading, autonomy or auto-execute, moving to a
  real-money account, or changing any MetaTrader expert other than SwingTrendPullback.
- Evidence rules: nothing is "profitable" without after-cost results on a holdout the selection never saw, enough
  trades (100, or 30 for one rule strategy), drawdown within limits and a deflated Sharpe that counts every trial.
  Say "not proven" otherwise. Never overstate.
- No duplicates: EARLIER_PROPOSALS lists everything already proposed with the owner's decision. Do not propose any
  of them again (including rejected ones). Build on approved ones.
- If a data source is missing, stale or failing, report that as a finding.
- Be concrete: name the file, field or number your point comes from.

OUTPUT
Reply with ONE JSON object and nothing else:
{
  "summary": "at most 120 words, plain language for the owner",
  "system_health": "healthy | attention | problem",
  "findings": [{"area": "short", "severity": "info | warn | problem", "text": "what you found", "evidence": "file/field/number"}],
  "proposals": [{"title": "short imperative title", "category": "stability | fix | monitoring | research | strategy | risk | data",
                 "detail": "what exactly to do", "evidence": "why, with numbers", "expected_benefit": "...", "risk": "...",
                 "effort": "small | medium | large"}],
  "memory_notes": ["facts worth remembering for tomorrow's review"],
  "questions_for_owner": ["only if a decision is needed"]
}
At most 10 findings, 5 proposals, 5 memory notes and 3 questions."""


# --------------------------------------------------------------------------- context
def _compact(value, max_list: int = 20, max_str: int = 700, depth: int = 0):
    """Bound the size of nested report data before it goes into the prompt."""
    if depth > 7:
        return str(value)[:max_str]
    if isinstance(value, dict):
        return {str(k): _compact(v, max_list, max_str, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        items = [_compact(v, max_list, max_str, depth + 1) for v in list(value)[:max_list]]
        if len(value) > max_list:
            items.append(f"... {len(value) - max_list} more")
        return items
    if isinstance(value, str):
        return value if len(value) <= max_str else value[:max_str] + " ..."
    return value


def build_employee_context(root: Path = ROOT, base: Path = EMPLOYEE_DIR, now: Optional[datetime] = None) -> dict:
    """Compact snapshot of the system's own reports; every source is optional and its state is recorded."""
    now = now or datetime.now(timezone.utc)
    data = Path(root) / "data"
    sources: dict = {}

    def gather(name: str, loader: Callable):
        try:
            value = loader()
        except Exception as exc:  # one broken source must not stop the review
            sources[name] = f"error: {exc.__class__.__name__}: {exc}"[:200]
            return None
        sources[name] = "ok" if value not in (None, {}, []) else "missing"
        return value

    def health():
        out = {}
        for key, file in (("latest", "doctor_latest.json"), ("deep", "doctor_latest_deep.json")):
            report = _read_json(data / "system_health" / file)
            if isinstance(report, dict):
                out[key] = {"generated_at": report.get("generated_at"), "overall": report.get("overall"),
                            "counts": report.get("counts"),
                            "checks": [f"[{c.get('status')}] {c.get('name')}: {c.get('summary')}" for c in report.get("checks") or []]}
        return out or None

    def daily_report():
        return _read_json(data / "daily_reports" / "latest.json")

    def strategy_book():
        path = data / "strategy_lab" / "strategy_book.json"
        if not path.exists():
            return None
        from .strategy_book import book_summary
        summary = book_summary(path)
        entries = []
        for entry in summary.get("entries", [])[:12]:
            latest = entry.get("latest") or {}
            entries.append({
                "market": entry.get("market"), "status": entry.get("status"), "strategy": entry.get("description"),
                "first_added": entry.get("first_added"), "checks_so_far": len(entry.get("history") or []),
                "holdout": latest.get("holdout"), "deflated_sharpe": latest.get("deflated_sharpe"),
                "monte_carlo_loss_probability": (latest.get("monte_carlo") or {}).get("loss_probability"),
                "neighbour_share": (latest.get("neighbours") or {}).get("share"),
                "rolling_12m_positive_share": (latest.get("rolling") or {}).get("positive_share"),
                "still_missing": latest.get("missing")})
        return {"updated_at": summary.get("updated_at"), "counts": summary.get("counts"), "rules": summary.get("rules"),
                "top_entries": entries, "last_update": summary.get("last_update")}

    def strategy_lab():
        status = _read_json(data / "strategy_lab" / "status.json")
        registry = _read_json(data / "strategy_lab" / "registry.json")
        markets, baselines = {}, []
        if isinstance(registry, dict):
            for key, meta in (registry.get("markets") or {}).items():
                markets[key] = {k: meta.get(k) for k in ("candidates_tried", "bars", "data_start", "data_end", "cost_model", "periods")}
            for record in (registry.get("candidates") or {}).values():
                if record.get("tag"):
                    baselines.append({k: record.get(k) for k in ("market", "name", "search", "validation", "holdout", "gate_reasons", "cost_model")})
                    continue
                bucket = markets.setdefault(record.get("market"), {})
                bucket["stored_candidates"] = bucket.get("stored_candidates", 0) + 1
                bucket["still_validated"] = bucket.get("still_validated", 0) + int(bool(record.get("validated")))
        return {"status": status, "markets": markets, "ea_baselines": baselines} if (status or markets) else None

    def swing_trend_pullback():
        from . import ea_monitor
        summary = ea_monitor.build_summary(store_path=data / "ea" / "swing_trend_pullback" / "trades.json")
        status = summary.get("status") or {}
        return {
            "health": summary.get("health"),
            "stats": {k: v for k, v in (summary.get("stats") or {}).items() if k != "equity_curve"},
            "recent_trades": (summary.get("trades") or [])[:10],
            "inputs": (summary.get("inputs") or {}).get("text"),
            "inputs_differences_from_preset": (summary.get("inputs") or {}).get("differences"),
            "status": {k: status.get(k) for k in ("version", "running", "account_trade_mode", "balance", "equity", "currency",
                                                  "terminal_connected", "algo_terminal", "algo_expert", "position",
                                                  "last_event", "file_age_seconds", "signal")},
            "recent_events": (summary.get("events") or [])[:15],
        }

    def paper_and_demo():
        state = _read_json(data / "paper_trading" / "xauusd_4h_mtf_xgb_tight_q90.json")
        journal = _read_json(data / "paper_trading" / "demo_execution_journal.json") or {}
        if not state and not journal:
            return None
        def last(value, count):  # the journal keeps lists or id-keyed dicts depending on the entry type
            if isinstance(value, dict):
                return dict(list(value.items())[-count:])
            return list(value or [])[-count:]

        return {"paper_trader": state, "demo_execution_recent_events": last(journal.get("events"), 10),
                "demo_execution_recent_attempts": last(journal.get("attempts"), 5)}

    def baseline_results():
        path = Path(root) / "ml_trading_system" / ".claude" / "memory" / "BASELINE.md"
        if not path.exists():
            return None
        rows = [line for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if line.startswith("| 20")]
        return rows[-15:] or None

    paths = _paths(base)
    memory = _read_json(paths["memory"]) or {}
    proposals = _read_json(paths["proposals"]) or {}
    context = {
        "generated_at": now.strftime(TIME_FORMAT), "local_date": now.astimezone().strftime("%Y-%m-%d"),
        "places_orders": False,
        "system_health": gather("system_doctor", health),
        "daily_market_report": gather("daily_report", daily_report),
        "strategy_book": gather("strategy_book", strategy_book),
        "strategy_lab": gather("strategy_lab", strategy_lab),
        "swing_trend_pullback_ea": gather("swing_trend_pullback_ea", swing_trend_pullback),
        "paper_trading_and_demo": gather("paper_trading", paper_and_demo),
        "recent_baseline_results": gather("baseline", baseline_results),
        "memory_notes": (memory.get("notes") or [])[-40:],
        "earlier_proposals": [{k: item.get(k) for k in ("id", "title", "category", "status", "created_at", "seen_count", "owner_note")}
                              for item in (proposals.get("items") or [])[-60:]],
    }
    context = _compact(context)
    context["sources"] = sources
    return context


def employee_prompt(context: dict) -> str:
    return PROMPT_RULES + "\n\nCONTEXT (JSON, generated by the system this morning):\n" + json.dumps(context, indent=1, default=str)

# --------------------------------------------------------------------------- layer 2: the context budget
# The window `run_claude_code` answers in. Named rather than measured because the CLI does not report it,
# and at this size the JSON dump below fits with room to spare - which is why the daily 07:15 run keeps
# exactly the prompt it has always had.
CLAUDE_CONTEXT_TOKENS = 180_000

EMPLOYEE_TASK = ("Review this trading system's own overnight record and report what needs attention. "
                 "Read-only: you may not trade, change a setting, or run anything.")

# Stated in a form the code itself checks, before the work rather than after: `parse_employee_reply`
# either accepts the answer or raises. An acceptance test that only a human can judge is not one.
EMPLOYEE_ACCEPTANCE = ("One JSON object with summary, system_health, findings, proposals, memory_notes and "
                       "questions_for_owner, such that src.ai_employee.parse_employee_reply accepts it. "
                       "Every finding and proposal cites the context field it came from.")

# Which slot each context section belongs in. `working` is the live state under review; `episodic` is what
# recently happened. The EVIDENCE slot is deliberately absent: layer 2 fills it from the system's own
# record (`second_brain`), so measured results arrive WITH their sources rather than as more live state.
WORKING_KEYS = ("places_orders", "system_health", "daily_market_report", "strategy_book", "strategy_lab",
                "swing_trend_pullback_ea", "paper_trading_and_demo")
EPISODIC_KEYS = ("recent_baseline_results", "earlier_proposals", "memory_notes")


def _fact_lines(prefix: str, value, out: list, limit: int) -> None:
    """Flatten nested context into one `path: value` line per fact, so a budget can drop lines not sections.

    Dropping whole sections is what the JSON dump forces: a truncated JSON object is not JSON at all, so the
    only way to shrink it is to cut a branch and hope it was not the one that mattered. One fact per line
    lets the budget keep the most of everything.
    """
    if len(out) >= limit:
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _fact_lines(f"{prefix}.{key}" if prefix else str(key), item, out, limit)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _fact_lines(f"{prefix}[{index}]", item, out, limit)
    else:
        text = _clean(value, 300)
        if text:
            out.append(f"{prefix}: {text}")


def context_slot_lines(context: dict, limit: int = 600) -> dict:
    """The context dict as flat citable lines, split into layer 2's working and episodic slots."""
    working: list = []
    episodic: list = []
    for key in WORKING_KEYS:
        if key in context:
            _fact_lines(key, context[key], working, limit)
    for key in EPISODIC_KEYS:
        if key in context:
            _fact_lines(key, context[key], episodic, limit)
    return {"working": working, "episodic": episodic}


def employee_brief(context: dict, *, budget_tokens: int):
    """The same review, assembled to fit `budget_tokens`, with what was left out stated."""
    from .context_builder import brief_for_task

    slots = context_slot_lines(context)
    return brief_for_task(EMPLOYEE_TASK, budget_tokens=budget_tokens, identity=PROMPT_RULES,
                          acceptance=EMPLOYEE_ACCEPTANCE,
                          working=slots["working"], episodic=slots["episodic"])


def prompt_for_budget(context: dict, budget_tokens: Optional[int] = None) -> tuple:
    """Return ``(prompt, shape)``: the JSON prompt unchanged when it fits, a layer-2 brief when it does not.

    Measured on 26 September 2026: the JSON prompt is about 24,000 tokens. Claude answers in 180,000, so
    it fits and nothing about the daily run changes. The local model this system is meant to fall back to
    answers in 8,192 - three times too small - so on that provider the AI employee could not run at all.
    That is the gap layer 2 was built for, and until now nothing called it.

    A brief that cannot be assembled is REFUSED (``prompt`` is None) rather than truncated, because a
    silently shortened prompt asks a different question than the caller believes it asked.
    """
    from .context_builder import OUTPUT_RESERVE

    budget = int(budget_tokens if budget_tokens is not None else CLAUDE_CONTEXT_TOKENS)
    whole = employee_prompt(context)
    whole_tokens = _estimate_tokens(whole)
    usable = max(1, int(budget * (1 - OUTPUT_RESERVE)))
    if whole_tokens <= usable:
        return whole, {"shape": "json", "budget_tokens": budget, "prompt_tokens": whole_tokens,
                       "usable_tokens": usable, "ok": True, "omitted": [],
                       "note": "the whole context fits; prompt unchanged"}

    brief = employee_brief(context, budget_tokens=budget)
    shape = {"shape": "brief", "budget_tokens": budget, "prompt_tokens": brief.prompt_tokens,
             "usable_tokens": usable, "ok": bool(brief.ok), "omitted": brief.omitted,
             "note": (f"the whole context needs about {whole_tokens:,} tokens and only {usable:,} are "
                      f"usable, so it was assembled by src.context_builder instead")}
    if not brief.ok:
        shape["note"] = brief.reason
        return None, shape
    return brief.prompt, shape


# --------------------------------------------------------------------------- reply handling
def _clean(value, limit: int) -> str:
    return re.sub(r"\s+", " ", str(value if value is not None else "")).strip()[:limit]


def _choice(value, allowed: tuple, default: str) -> str:
    text = _clean(value, 30).lower()
    return text if text in allowed else default


def parse_employee_reply(text: str) -> dict:
    """The validated brief from Claude's reply; raises ValueError when there is no usable JSON object."""
    raw = str(text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw, flags=re.S)
    candidate = fenced.group(1) if fenced else raw[raw.find("{"): raw.rfind("}") + 1] if "{" in raw else ""
    if not candidate:
        raise ValueError("no JSON object in the reply")
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise ValueError(f"reply JSON is invalid: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("reply JSON is not an object")
    summary = _clean(data.get("summary"), 1500)
    if not summary:
        raise ValueError("reply has no summary")

    def rows(key: str, limit: int) -> list:
        value = data.get(key)
        return [row for row in value if isinstance(row, dict)][:limit] if isinstance(value, list) else []

    findings = [{"area": _clean(f.get("area"), 60), "severity": _choice(f.get("severity"), SEVERITIES, "info"),
                 "text": _clean(f.get("text"), 600), "evidence": _clean(f.get("evidence"), 400)}
                for f in rows("findings", MAX_FINDINGS)]
    proposals = []
    for p in rows("proposals", MAX_PROPOSALS):
        item = {"title": _clean(p.get("title"), 140), "category": _choice(p.get("category"), CATEGORIES, "research"),
                "detail": _clean(p.get("detail"), 1500), "evidence": _clean(p.get("evidence"), 600),
                "expected_benefit": _clean(p.get("expected_benefit"), 400), "risk": _clean(p.get("risk"), 400),
                "effort": _choice(p.get("effort"), EFFORTS, "medium")}
        if not item["title"]:
            continue
        if TRADING_ACTION.search(" ".join([item["title"], item["detail"]])):
            item["flag"] = "Mentions a trading action. The employee may not trade; review with care."
        proposals.append(item)
    notes = [_clean(n, 400) for n in (data.get("memory_notes") or []) if isinstance(n, str) and _clean(n, 400)][:MAX_NOTES_PER_RUN]
    questions = [_clean(q, 300) for q in (data.get("questions_for_owner") or []) if isinstance(q, str) and _clean(q, 300)][:MAX_QUESTIONS]
    return {"summary": summary, "system_health": _choice(data.get("system_health"), HEALTH_VALUES, "attention"),
            "findings": [f for f in findings if f["text"]], "proposals": proposals, "memory_notes": notes,
            "questions_for_owner": questions}


def _title_words(title: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", str(title).lower()) if len(w) > 2 and w not in STOP_WORDS}


def merge_proposals(store: dict, new: list[dict], when: str, run_id: str) -> dict:
    """Add new proposals; one similar to any earlier proposal (whatever its status) is merged into it instead."""
    items = store.setdefault("items", [])
    added = merged = 0
    for proposal in new:
        words = _title_words(proposal["title"])
        match = None
        for item in items:
            other = set(item.get("title_words") or _title_words(item.get("title", "")))
            if words and other and len(words & other) / len(words | other) >= DUPLICATE_SIMILARITY:
                match = item
                break
        if match is not None:
            match["seen_count"] = int(match.get("seen_count") or 1) + 1
            match["last_seen"] = when
            merged += 1
            continue
        items.append(dict(proposal, id=hashlib.sha1(f"{proposal['title']}|{when}".encode("utf-8")).hexdigest()[:10],
                          status="pending", created_at=when, last_seen=when, seen_count=1, run_id=run_id,
                          title_words=sorted(words), owner_note="", decided_at=None))
        added += 1
    return {"added": added, "merged": merged, "total": len(items)}


def add_memory_notes(memory: dict, notes: list[str], when: str) -> int:
    rows = memory.setdefault("notes", [])
    known = {re.sub(r"\W+", " ", str(r.get("text", "")).lower()).strip() for r in rows}
    added = 0
    for note in notes:
        key = re.sub(r"\W+", " ", note.lower()).strip()
        if not key or key in known:
            continue
        rows.append({"date": when, "text": note})
        known.add(key)
        added += 1
    del rows[:-MAX_MEMORY_NOTES]
    return added


def set_proposal_status(proposal_id: str, status: str, note: str = "", base: Path = EMPLOYEE_DIR) -> dict:
    if status not in PROPOSAL_STATUSES:
        raise ValueError(f"status must be one of {PROPOSAL_STATUSES}")
    path = _paths(base)["proposals"]
    store = _read_json(path) or {"items": []}
    for item in store.get("items", []):
        if item.get("id") == proposal_id:
            item["status"] = status
            item["owner_note"] = _clean(note, 500)
            item["decided_at"] = datetime.now(timezone.utc).strftime(TIME_FORMAT)
            _write_json_atomic(path, store)
            return item
    raise KeyError(f"no proposal {proposal_id}")


# --------------------------------------------------------------------------- Claude Code
def find_claude_cli() -> Optional[str]:
    found = shutil.which("claude")
    if found:
        return found
    appdata = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
    for name in ("claude.cmd", "claude.exe", "claude"):
        if (appdata / "npm" / name).exists():
            return str(appdata / "npm" / name)
    return None


def claude_command(cli: str, model: str = MODEL) -> list[str]:
    """Print mode, read-only: code-running tools removed (--restricted), no MCP servers, prompts denied."""
    return [cli, "-p", "--output-format", "json", "--no-session-persistence", "--restricted", "--strict-mcp-config",
            "--permission-mode", "dontAsk", "--permission-prompts", "none", "--model", model,
            "--allowedTools", ALLOWED_TOOLS, "--disallowedTools", DENIED_TOOLS]


def child_environment(base_env: Optional[dict] = None) -> dict:
    """The environment for Claude Code without API credentials, so the run always uses the Claude subscription."""
    env = dict(os.environ if base_env is None else base_env)
    for key in REMOVED_ENV:
        env.pop(key, None)
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def interpret_cli_output(returncode: int, stdout: str, stderr: str) -> dict:
    try:
        payload = json.loads(stdout or "")
    except ValueError:
        payload = None
    if not isinstance(payload, dict):
        message = (stderr or stdout or f"exit code {returncode}").strip()
        return {"ok": False, "error": _login_hint(message[:600])}
    failed = bool(payload.get("is_error")) or returncode != 0 or payload.get("subtype") not in (None, "success")
    result = {"ok": not failed, "text": payload.get("result") or "", "cost_usd": payload.get("total_cost_usd"),
              "num_turns": payload.get("num_turns"), "duration_ms": payload.get("duration_ms")}
    if failed:
        result["error"] = _login_hint(str(payload.get("result") or stderr or payload.get("subtype") or f"exit code {returncode}")[:600])
    return result


def _login_hint(message: str) -> str:
    if re.search(r"log ?in|logged|authenticat|oauth|credential|/login", message, re.IGNORECASE):
        return message + " -> open Claude Code on this PC and run /login, then the next run works again."
    return message


def run_claude_code(prompt: str, timeout: int = TIMEOUT_SECONDS) -> dict:
    cli = find_claude_cli()
    if not cli:
        return {"ok": False, "error": "Claude Code CLI not found on this PC."}
    try:
        proc = subprocess.run(claude_command(cli), input=prompt, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout, cwd=str(ROOT), env=child_environment(),
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"Claude Code did not finish within {timeout} s."}
    except OSError as exc:
        return {"ok": False, "error": f"Claude Code could not start: {exc}"}
    return interpret_cli_output(proc.returncode, proc.stdout, proc.stderr)


# --------------------------------------------------------------------------- run and summary
def _paths(base: Path) -> dict:
    base = Path(base)
    return {"context": base / "context_latest.json", "brief": base / "brief_latest.json", "briefs": base / "briefs",
            "proposals": base / "proposals.json", "memory": base / "memory.json", "runs": base / "runs.json",
            "lock": base / "lock.json"}


def run_employee(force: bool = False, runner: Optional[Callable[[str], dict]] = None, now: Optional[datetime] = None,
                 base: Path = EMPLOYEE_DIR, root: Path = ROOT,
                 budget_tokens: Optional[int] = None) -> dict:
    """One review. `budget_tokens` is the window of whoever will ANSWER - not of whoever asks.

    It defaults to Claude's, because `run_claude_code` is the default runner, so the prompt sent
    today is byte-for-byte the one this has always sent. Pass a smaller window (a local model's)
    and layer 2 assembles a brief that fits instead, or refuses and says by how much it missed.
    """
    now = now or datetime.now(timezone.utc)
    paths = _paths(base)
    Path(base).mkdir(parents=True, exist_ok=True)
    today = now.astimezone().strftime("%Y-%m-%d")
    latest = _read_json(paths["brief"]) or {}
    if not force and latest.get("date") == today:
        return {"status": "skipped", "reason": f"today's brief ({today}) already exists"}
    lock = _read_json(paths["lock"]) or {}
    lock_started = _parse_utc(lock.get("started_at"))
    if lock_started and now - lock_started < timedelta(minutes=LOCK_STALE_MINUTES):
        return {"status": "skipped", "reason": f"another AI employee run is active (since {lock.get('started_at')} UTC)"}
    started = now.strftime(TIME_FORMAT)
    run_id = hashlib.sha1(f"{started}|{os.getpid()}".encode("utf-8")).hexdigest()[:10]
    _write_json_atomic(paths["lock"], {"pid": os.getpid(), "started_at": started, "run_id": run_id})
    record = {"run_id": run_id, "started_at": started, "date": today, "status": "error", "model": MODEL}
    clock = time.monotonic()
    try:
        context = build_employee_context(root=root, base=base, now=now)
        _write_json_atomic(paths["context"], context)
        prompt, shape = prompt_for_budget(context, budget_tokens)
        record["context_shape"] = shape
        if prompt is None:
            # Refused, not truncated: a shortened prompt asks a different question than this asked.
            record["error"] = _clean(shape.get("note") or "the context would not fit the budget", 800)
            raise RuntimeError(record["error"])
        reply = (runner or run_claude_code)(prompt)
        record.update({k: reply.get(k) for k in ("cost_usd", "num_turns", "duration_ms")})
        if not reply.get("ok"):
            record["error"] = _clean(reply.get("error") or "Claude Code failed", 800)
        else:
            brief = parse_employee_reply(reply.get("text") or "")
            proposals = _read_json(paths["proposals"]) or {"items": []}
            merge = merge_proposals(proposals, brief["proposals"], started, run_id)
            _write_json_atomic(paths["proposals"], proposals)
            memory = _read_json(paths["memory"]) or {"notes": []}
            notes_added = add_memory_notes(memory, brief["memory_notes"], started)
            _write_json_atomic(paths["memory"], memory)
            document = {"date": today, "status": "ok", "generated_at": datetime.now(timezone.utc).strftime(TIME_FORMAT),
                        "run_id": run_id, "model": MODEL, **brief, "proposal_merge": merge, "memory_added": notes_added,
                        "sources": context.get("sources")}
            paths["briefs"].mkdir(parents=True, exist_ok=True)
            _write_json_atomic(paths["briefs"] / f"{today}.json", document)
            _write_json_atomic(paths["brief"], document)
            record.update(status="ok", findings=len(brief["findings"]), proposals_added=merge["added"],
                          proposals_merged=merge["merged"], memory_added=notes_added, system_health=brief["system_health"])
    except ValueError as exc:
        record["error"] = f"The reply could not be used: {exc}"
    except Exception as exc:  # recorded for the page and the System Doctor, never raised into the scheduler
        record["error"] = f"{exc.__class__.__name__}: {exc}"[:800]
    finally:
        record["finished_at"] = datetime.now(timezone.utc).strftime(TIME_FORMAT)
        record["seconds"] = round(time.monotonic() - clock, 1)
        runs = _read_json(paths["runs"])
        runs = runs if isinstance(runs, list) else []
        runs.append(record)
        _write_json_atomic(paths["runs"], runs[-MAX_RUNS_KEPT:])
        try:
            paths["lock"].unlink()
        except OSError:
            pass
    return record


def employee_summary(base: Path = EMPLOYEE_DIR) -> dict:
    paths = _paths(base)
    runs = _read_json(paths["runs"])
    runs = runs if isinstance(runs, list) else []
    proposals = (_read_json(paths["proposals"]) or {}).get("items") or []
    order = {"pending": 0, "approved": 1, "done": 2, "rejected": 3}
    proposals = sorted(proposals, key=lambda p: order.get(p.get("status"), 9))  # stable: oldest first within a status
    context = _read_json(paths["context"]) or {}
    lock = _read_json(paths["lock"])
    return {
        "places_orders": False, "read_only": True, "schedule": SCHEDULE_TEXT, "model": MODEL,
        "tools": {"allowed": ALLOWED_TOOLS.split(","), "denied": DENIED_TOOLS.split(","),
                  "flags": ["--restricted", "--strict-mcp-config", "--permission-mode dontAsk", "--permission-prompts none"],
                  "billing": "Claude subscription (API keys removed from the run's environment)"},
        "brief": _read_json(paths["brief"]), "running": bool(lock), "lock": lock,
        "runs": list(reversed(runs[-20:])),
        "proposals": [{k: v for k, v in p.items() if k != "title_words"} for p in proposals],
        "proposal_counts": {s: sum(1 for p in proposals if p.get("status") == s) for s in PROPOSAL_STATUSES},
        "memory_notes": list(reversed(((_read_json(paths["memory"]) or {}).get("notes") or [])[-60:])),
        "context_sources": context.get("sources"), "context_generated_at": context.get("generated_at"),
    }


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="AI Employee: daily read-only review by Claude Code (never trades).")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="build the context, ask Claude Code, store the brief")
    run.add_argument("--force", action="store_true", help="run even if today's brief exists")
    ctx = sub.add_parser("context", help="build and save the context only (no Claude call)")
    ctx.add_argument("--budget", type=int, default=None,
                     help="tokens the answering model has; default Claude's. "
                          "Use 8192 to see what a local model would get.")
    sub.add_parser("status", help="print the latest brief and run")
    args = parser.parse_args(argv)
    if args.command == "run":
        record = run_employee(force=args.force)
        print(json.dumps(record, indent=1, default=str))
        return 0 if record.get("status") in ("ok", "skipped") else 1
    if args.command == "context":
        context = build_employee_context()
        _write_json_atomic(_paths(EMPLOYEE_DIR)["context"], context)
        prompt, shape = prompt_for_budget(context, args.budget)
        print(json.dumps({"sources": context["sources"],
                          "prompt_characters": len(employee_prompt(context)),
                          "sent_characters": len(prompt or ""),
                          "context_shape": shape}, indent=1))
        return 0
    summary = employee_summary()
    print(json.dumps({"brief": (summary["brief"] or {}).get("summary"), "last_run": (summary["runs"] or [None])[0],
                      "proposals": summary["proposal_counts"]}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    # Wrapped so this loop cannot finish without a record - see loop_ledger.run_main.
    from .loop_ledger import run_main

    raise SystemExit(run_main("ai_employee", main))
