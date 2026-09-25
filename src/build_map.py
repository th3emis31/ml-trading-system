"""The i40 Pilot build map, read from the running system. Serves the /i40-build-map page.

The owner asked for the map to live inside their own system rather than only as a hosted page, and
that is the right home for it: a page on someone else's site is a dependency, and the whole point of
this system is that it does not need one.

It also means the figures stop being a snapshot. Every number here is read from the thing it
describes - the provider survey, the memory index, the skill checker, the loop ledger - so the page
cannot drift away from reality the way a written status always eventually does.

Steps carry a ``done`` flag because completion is a judgement about whether the acceptance test was
met, and a judgement is recorded rather than inferred. The EVIDENCE beside each one is measured.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional

# Each step names what it delivers and what proved it. `done` is set when the step's acceptance test
# was met, which is a decision; everything in `evidence` is read live below.
STEPS = (
    {"n": 1, "phase": "Make it run locally", "title": "Provider abstraction", "done": True,
     "delivers": "Claude and a local model behind one interface",
     "proof": "A prompt too big for a window is refused with the numbers, never truncated, and every "
              "reply says which model answered and whether it was the weaker one."},
    {"n": 2, "phase": "Make it run locally", "title": "Memory split into four kinds", "done": True,
     "delivers": "Evidence, episodic, semantic and procedural, each with its own rules",
     "proof": "Any claim traces to a measurement or is flagged a hypothesis. Fixing it uncovered 309 "
              "lines of the evidence file that had never been searchable."},
    {"n": 3, "phase": "Make it run locally", "title": "Context builder", "done": True,
     "delivers": "Five budgeted slots, cite-or-omit",
     "proof": "The same task builds at 282 tokens on a 4k window and 1,490 on 180k - identical work, "
              "different depth."},
    {"n": 4, "phase": "Make it believable", "title": "Acceptance tests on every skill", "done": True,
     "delivers": "No skill may mark its own homework",
     "proof": "Success needs a check something other than the model adjudicates. Coverage went from "
              "nothing to every skill."},
    {"n": 5, "phase": "Make it believable", "title": "Loop closure", "done": True,
     "delivers": "Every run records what it measured and whether anything changed",
     "proof": "A loop that only observes is flagged OPEN; one that never reports is SILENT, so a task "
              "that quietly stopped cannot hide."},
    {"n": 6, "phase": "Make it believable", "title": "Four permission tiers", "done": True,
     "delivers": "Read, local write, outward, and money or system",
     "proof": "Approval names one action and never carries to the next; drifted figures are refused; "
              "a halt stops the system and leaves open trades to exit on their own terms."},
    {"n": 7, "phase": "Make it believable", "title": "Failure-mode register", "done": True,
     "delivers": "The plan is criticised before it runs",
     "proof": "Each mode carries the check that would have caught it. Two of them would have caught "
              "this week's false 12x improvement before it was reported."},
    {"n": 8, "phase": "Make it broad", "title": "The five skill families", "done": True,
     "delivers": "Trading, software, market, business and media",
     "proof": "Every family has a skill, every skill declares an independent check, and one was "
              "proven end to end on the 3,544-trade bitcoin case."},
    {"n": 9, "phase": "Make it broad", "title": "Portability", "done": True,
     "delivers": "One home folder holding memory, evidence and config",
     "proof": "I40_HOME moves the whole system, MetaTrader locations come from config/machine.json "
              "instead of the source, and a test fails if any machine-specific path goes back into "
              "the code. Proven by running the USB copy as its own home: it found its models, data "
              "and config on the drive. Where a path is absent it is named, never guessed.",
     "gap": "Not yet proven on a second PC, and offline still needs a local model - none is "
            "installed, which the provider evidence below reports as independent: false."},
    {"n": 10, "phase": "Make it broad", "title": "Self-improvement loop", "done": True,
     "delivers": "It proposes a change from its own evidence and measures the effect",
     "proof": "src/self_improvement.py, and it has turned once end to end. The prediction is hashed "
              "when the experiment opens and a verdict is REFUSED if it changed; a command that "
              "prints no number is inconclusive, never a result; acting on a result goes through "
              "governance. First experiment (exp_20260925_01) asked whether the LSTM is starved of "
              "price structure, predicted a 0.02 accuracy gain, measured 0.0044 on a paired 3-seed "
              "run - refuted, and the feature-set explanation is now permanently ruled out.",
     "gap": "Not yet unattended. Putting the loop on a schedule is a T3 action under its own "
            "governance rules, so that is the owner's decision to make, not the loop's."},
)

SCOPE = (
    ("Trading", ("trading systems", "expert advisors", "indicators", "bots", "auto-trading",
                 "Pine script", "backtesting")),
    ("Software", ("applications", "websites", "dashboards", "automation", "PC control")),
    ("Market", ("SEO", "marketing", "deep analysis", "promotion", "web research")),
    ("Business", ("financial analysis", "planning", "reporting")),
    ("Media", ("video", "images", "charts")),
)

LAYERS = (
    (0, "Runtime", "Which model answers - cloud or local - and the portable home folder", True),
    (1, "Memory", "Evidence, episodic, semantic and procedural, each with its own rules", True),
    (2, "Context", "What enters the window, and what is deliberately left out", True),
    (3, "Brain", "Plan, then criticise the plan against what has gone wrong before", True),
    (4, "Loop", "Perceive, decide, act, verify, record", True),
    (5, "Skills", "One folder per capability, each stating how to check its own output", True),
    (6, "Tools", "Files, shell, browser, MetaTrader, media, web - typed and permissioned", False),
    (7, "Governance", "Four tiers, an audit of every action, a halt that spares open trades", True),
    (8, "Interfaces", "Dashboard, voice, command line, phone - views onto one brain", False),
)


def _providers() -> dict:
    try:
        from .ai_provider import provider_status

        state = provider_status()
        return {"available": True,
                "online": bool(state.get("internet")),
                "offline_capable": bool(state.get("independent")),
                "verdict": state.get("verdict"),
                "rows": [{"name": p.get("name"), "local": bool(p.get("local")),
                          "usable": bool(p.get("available")),
                          "context_tokens": p.get("context_tokens"),
                          "reason": p.get("reason")}
                         for p in (state.get("providers") or [])]}
    except Exception as exc:
        # Never invent a state for the thing whose whole job is stating the truth about itself.
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}"}


def _memory() -> dict:
    try:
        from .second_brain import provenance_report

        report = provenance_report()
        return {"available": True, "entries": report.get("total"),
                "traceable_pct": report.get("traceable_pct"),
                "hypotheses": report.get("hypotheses")}
    except Exception as exc:
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}"}


def _skills() -> dict:
    try:
        from .skill_acceptance import coverage

        found = coverage()
        return {"available": True, "total": found.get("total"),
                "coverage_pct": found.get("coverage_pct"),
                "families_covered": found.get("families_covered"),
                "families_missing": found.get("families_missing") or [],
                "by_family": found.get("by_family") or {}}
    except Exception as exc:
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}"}


def _loops() -> dict:
    try:
        from .loop_ledger import closure_report

        report = closure_report()
        return {"available": True, "known": report.get("total_known"),
                "reporting": report.get("reporting"), "counts": report.get("counts") or {},
                "rows": report.get("loops") or []}
    except Exception as exc:
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}"}


def _failure_modes() -> dict:
    try:
        from .failure_modes import REGISTER

        return {"available": True, "count": len(REGISTER),
                "names": [m.name for m in REGISTER]}
    except Exception as exc:
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}"}


def _portability() -> dict:
    """Step 9's evidence, read live: does this run resolve its own home, and what is missing here?"""
    try:
        from .runtime_paths import HOME_ENV, machine_report, smartentry_data_dir, smartentry_models_dir

        report = machine_report()
        return {"available": True,
                "home": report["home"],
                "home_from_environment": bool(os.environ.get(HOME_ENV)),
                "config_file": report["config_file"],
                "config_file_exists": report["config_file_exists"],
                "models_dir": str(smartentry_models_dir()),
                "data_dir": str(smartentry_data_dir()),
                "terminals": report["terminals"],
                "missing": report["missing"]}
    except Exception as exc:
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}"}


def _self_improvement() -> dict:
    """Step 10's evidence: what the loop has actually settled, and whether it looks honest."""
    try:
        from .self_improvement import experiment_report

        report = experiment_report()
        return {"available": True, "total": report["total"], "open": report["open"],
                "counts": report["counts"], "confirm_rate": report["confirm_rate"],
                "summary": report["summary"], "honest": report["honest"],
                "honest_note": report["honest_note"],
                "ruled_out": report["ruled_out"][:6], "tampered": len(report["tampered"])}
    except Exception as exc:
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}"}


def build_map_state() -> dict:
    """Everything the page shows, read from the running system."""
    done = [s for s in STEPS if s["done"]]
    nxt = next((s for s in STEPS if not s["done"]), None)
    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "steps": list(STEPS),
        "steps_done": len(done), "steps_total": len(STEPS),
        "next_step": nxt,
        "layers": [{"level": l, "name": n, "desc": d, "built": b} for l, n, d, b in LAYERS],
        "scope": [{"family": f, "items": list(items)} for f, items in SCOPE],
        "providers": _providers(),
        "memory": _memory(),
        "skills": _skills(),
        "loops": _loops(),
        "failure_modes": _failure_modes(),
        "portability": _portability(),
        "self_improvement": _self_improvement(),
        "constraint": ("Work anywhere with internet, and fully local without internet. Offline means "
                       "a small local model, so the competence is put in the SYSTEM rather than the "
                       "model: memory states what is known, skills carry the procedure, tools do "
                       "anything that must be exact, and verification refuses work that fails its "
                       "own check."),
        "places_orders": False,
    }
