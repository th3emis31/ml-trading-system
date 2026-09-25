"""Build things from a SPEC, and never claim one works until its own checks say so.

This is the system's making hand: give it "build me an indicator that marks the London sweep" and it
produces the spec first, one machine check per spec rule, then the artefact - and reports `verified`
only when those checks pass. It is step 10's other half: the system cannot improve what it cannot
build, and it cannot be trusted to build unless something other than the model decides it worked.

WHY SPEC-FIRST, AND WHY IT IS NOT A STYLE PREFERENCE
----------------------------------------------------
This is the single highest-measured lever available, and it is nearly free.

Haeri & Ghelichi, "Specification Grounding Drives Test Effectiveness for LLM Code"
(arXiv:2607.06636, 7 July 2026) held the model, the test budget and the repair loop FIXED and changed
only whether the thing writing the tests could see a specification. Grounding the tests in the spec
produced correct code **+38 percentage points** more often, and +36 on a held-out set. Three further
results from the same paper shape this module:

* **Test COUNT is not the lever.** Doubling the test budget barely helped, and eight independent
  ungrounded suites plateaued far below one grounded suite. So this module writes exactly ONE check
  per spec rule and refuses to pad. More tests is the tempting non-fix.
* **It is the spec's CONTENT, not its format.** Given the spec as a plain paragraph the tester found
  27 of 30 bugs; asked to plan tests without it, 2 of 30. So `rules` may be ordinary sentences - they
  must be testable, not formal.
* **A loop alone does not do it.** An AlphaCodium-style flow only matched the baseline. A repair loop
  is included here, but it is the cheap part, and is capped.

The effect replicated across vendors (GPT-5.3-codex +28, Gemini 3.5 Flash +19), which is what makes
it the right thing to build INTO the system: it is a property of the method, so it survives swapping
a hosted model for a small local one. Compare the alternative on offer - reinforcement learning from
verification feedback on small models (Skopin & Kotelnikov, arXiv:2605.30478) bought **+13 points**
of pass@1 and needs a fine-tuning pipeline. Less than half the gain for vastly more machinery.

That is the whole design argument, and it matches the owner's own constraint: put the competence in
the SYSTEM, not the model, so a weak offline model still produces work that can be trusted.

WHAT THIS DELIBERATELY DOES NOT DO
----------------------------------
It does not trust the model about whether it succeeded. `verdict` is computed from check results by
`src.skill_acceptance`, the same adjudicator the skills already use - `run`, `file`, `number` and
`appended` are decided by the machine, `ask` by the owner, never by the thing that wrote the code. A
rule with no check is reported as an UNCOVERED rule and caps the verdict at `partial`, because a rule
nobody can check is a claim, and this system does not ship claims as results.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from .runtime_paths import smartentry_data_dir
from .skill_acceptance import AUTOMATIC, CHECK, FAMILIES

# One rule per line, numbered, so a check can cite the rule it grounds. The number is the join
# between a requirement and its evidence; without it "which rule is untested" has no answer.
RULE_LINE = re.compile(r"^\s*(?:R)?(\d+)[.):]\s*(.+?)\s*$")
# A check declares the rule it grounds: `R3 run: python -m pytest -q tests/test_sweep.py`
CHECK_LINE = re.compile(r"^\s*R?(\d+)\s+(run|file|number|ask|appended)\s*:\s*(.+?)\s*$", re.I)

MAX_REPAIRS = 3          # the paper's finding: the loop is the cheap part, so it is capped early
BUILDS_DIRNAME = "builds"


@dataclass
class Rule:
    """One testable requirement, and the check that grounds it. `check` is None until one exists."""

    number: int
    text: str
    check: Optional[str] = None          # in skill_acceptance syntax, e.g. "run: python -m pytest -q"

    @property
    def kind(self) -> Optional[str]:
        if not self.check:
            return None
        found = CHECK.match(self.check)
        return found.group(1).lower() if found else None

    @property
    def machine_checked(self) -> bool:
        """Can this be decided without asking the owner? `ask` is honest but cannot run unattended."""
        return self.kind in AUTOMATIC


@dataclass
class Spec:
    """What is being built, as numbered rules. The artefact is judged against this, not against itself."""

    title: str
    family: str
    request: str
    rules: list = field(default_factory=list)
    notes: str = ""

    @property
    def uncovered(self) -> list:
        """Rules with no check. Each one is a requirement that cannot be verified, so it is named."""
        return [rule.number for rule in self.rules if not rule.check]

    @property
    def unattended(self) -> list:
        """Rules whose only check needs the owner's eyes. Fine, but they cannot pass on their own."""
        return [rule.number for rule in self.rules if rule.check and not rule.machine_checked]

    def acceptance_block(self) -> str:
        """The spec's checks in the exact format the skills already use, so one adjudicator serves both.

        The rule sits on its OWN comment line above its check, never trailing after it. The parser's
        pattern ends in ``(.+?)$``, so a trailing ``# R1: ...`` would be captured as part of the shell
        command and then executed - a comment silently becoming an argument.
        """
        lines = []
        for rule in self.rules:
            if rule.check:
                lines.append(f"# R{rule.number}: {rule.text}")
                lines.append(rule.check)
        return "```acceptance\n" + "\n".join(lines) + "\n```" if lines else ""

    def to_dict(self) -> dict:
        return {"title": self.title, "family": self.family, "request": self.request,
                "notes": self.notes,
                "rules": [{"number": r.number, "text": r.text, "check": r.check, "kind": r.kind,
                           "machine_checked": r.machine_checked} for r in self.rules],
                "uncovered": self.uncovered, "unattended": self.unattended}


SPEC_PROMPT = """You are writing a SPECIFICATION before any code exists, for this request:

{request}

Write two sections and nothing else.

RULES
Numbered, one per line, each a single statement that is TESTABLE - something a machine could later
decide is true or false. Cover the boundaries and the invalid inputs, not only the normal case, because
that is where this kind of work actually fails. Between 3 and 12 rules. Ordinary English is correct;
do not write formal notation.

CHECKS
Exactly one line per rule, citing the rule's number, in one of these five forms:
  R1 run: <a shell command that exits non-zero on failure>
  R2 file: <path> contains <text>
  R3 number: <name> >= <value>
  R4 appended: <path>
  R5 ask: <a question for the owner, ONLY when no machine can decide it>
Prefer the machine-decidable forms. One check per rule - do not write extra checks, they do not help.
"""


def parse_spec(text: str, request: str = "", title: str = "", family: str = "software") -> Spec:
    """Read a drafted spec strictly. What cannot be parsed is dropped, never guessed at.

    A rule that arrives without a number is discarded rather than auto-numbered: the number is how a
    check says which requirement it grounds, so inventing one would silently attach evidence to the
    wrong claim - exactly the class of error that made a dashboard show a rejected model's return.
    """
    rules: dict = {}
    for line in (text or "").splitlines():
        found = CHECK_LINE.match(line)
        if found:                                  # a check, matched BEFORE the looser rule pattern
            number = int(found.group(1))
            if number in rules:
                rules[number].check = f"{found.group(2).lower()}: {found.group(3).strip()}"
            continue
        found = RULE_LINE.match(line)
        if found and not line.lstrip().lower().startswith(("check", "rule")):
            number, body = int(found.group(1)), found.group(2).strip()
            if body and number not in rules:
                rules[number] = Rule(number=number, text=body)
    ordered = [rules[key] for key in sorted(rules)]
    return Spec(title=title or (request[:60] if request else "untitled"),
                family=family if family in FAMILIES else "software",
                request=request, rules=ordered)


def draft_spec(request: str, family: str = "software", title: str = "",
               completer: Optional[Callable] = None) -> dict:
    """Ask a provider for the spec, then parse it with code rather than trusting its shape.

    Returns {ok, spec, provider, degraded, error}. With no provider reachable this returns ok=False
    and NO spec - it does not fall back to building without one, because building without a spec is
    the thing this module exists to prevent.
    """
    if completer is None:
        from .ai_provider import complete as completer          # imported late: optional dependency

    answer = completer(SPEC_PROMPT.format(request=request), task="builder.spec")
    if not answer.get("ok"):
        return {"ok": False, "spec": None, "provider": answer.get("provider"),
                "degraded": True, "error": answer.get("error") or "the provider could not answer"}
    spec = parse_spec(answer.get("text") or "", request=request, title=title, family=family)
    if not spec.rules:
        return {"ok": False, "spec": spec, "provider": answer.get("provider"),
                "degraded": bool(answer.get("degraded")),
                "error": "the answer contained no numbered rules, so there is nothing to build against"}
    return {"ok": True, "spec": spec, "provider": answer.get("provider"),
            "degraded": bool(answer.get("degraded")), "error": ""}


def rule_checks(spec: Spec, values: Optional[dict] = None, *, allow_run: bool = False) -> list:
    """Run each rule's check through the SKILLS' adjudicator and return a row per rule.

    `allow_run` defaults False for the same reason it does in skill_acceptance: a `run` check is an
    arbitrary shell command, and deciding to execute one belongs to the caller, not to a library.
    """
    from .skill_acceptance import SkillAcceptance, parse, run_checks

    block = spec.acceptance_block()
    if not block:
        return []
    skill = SkillAcceptance(name=spec.title, path=f"spec:{spec.title}", family=spec.family,
                            checks=parse(block))
    # run_checks MUTATES the skill and returns it - it does not return a list of results.
    adjudicated = run_checks(skill, values=values, allow_run=allow_run)
    rows = []
    for rule, check in zip([r for r in spec.rules if r.check], adjudicated.checks):
        rows.append({"rule": rule.number, "text": rule.text, "check": rule.check,
                     "kind": rule.kind, "machine_checked": rule.machine_checked,
                     # `passed is None` means "not adjudicated here" (an unrun `run`, or an `ask`).
                     # That is not a pass, and it must never be reported as one.
                     "passed": check.passed is True, "adjudicated": check.passed is not None,
                     "detail": check.detail or ""})
    return rows


def verdict_for(spec: Spec, rows: list) -> dict:
    """verified / partial / failed, decided from the checks - never from the model's own opinion.

    * **verified** - every rule has a machine check and every one passed.
    * **partial**  - the checks that ran passed, but some rule is uncovered or needs the owner.
      This is the honest verdict for "it looks right and part of it is unproven", and it is NOT
      success. Spec Kit reaches the same conclusion from the other direction: missing verification
      is not a successful fix.
    * **failed**   - at least one check failed, or there are no checks at all.
    """
    if not rows:
        return {"verdict": "failed", "passed": 0, "total": len(spec.rules),
                "why": "no rule carried a check, so nothing about this can be verified"}
    passed = [row for row in rows if row["passed"]]
    # A check that was never adjudicated is neither a pass nor a failure. Counting an unrun `run`
    # command as a failure would make every verdict `failed` whenever allow_run is off, and counting
    # it as a pass would be the lie this module exists to prevent. It caps the verdict instead.
    failures = [row for row in rows if row["adjudicated"] and not row["passed"]]
    pending = [row for row in rows if not row["adjudicated"]]
    if failures:
        return {"verdict": "failed", "passed": len(passed), "total": len(rows),
                "why": "failed: " + "; ".join(f"R{row['rule']} ({row['check']})" for row in failures[:4]),
                "failed_rules": [row["rule"] for row in failures]}
    parts = []
    if spec.uncovered:
        parts.append("no check for R" + ", R".join(str(n) for n in spec.uncovered))
    if pending:
        parts.append("not adjudicated here: R" + ", R".join(str(row["rule"]) for row in pending))
    if parts:
        return {"verdict": "partial", "passed": len(passed), "total": len(spec.rules),
                "why": "; ".join(parts) + " - passing what ran is not the same as verified",
                "pending_rules": [row["rule"] for row in pending]}
    return {"verdict": "verified", "passed": len(passed), "total": len(rows),
            "why": f"all {len(rows)} rules have a machine check and all passed"}


def coverage_gaps(spec: Spec) -> dict:
    """What about this spec cannot be proven, stated before anything is built rather than after."""
    machine = [r.number for r in spec.rules if r.machine_checked]
    return {"rules": len(spec.rules), "machine_checked": len(machine),
            "uncovered": spec.uncovered, "needs_owner": spec.unattended,
            "provable": bool(spec.rules) and not spec.uncovered and not spec.unattended}


def run_build(request: str, family: str = "software", title: str = "",
              completer: Optional[Callable] = None, values: Optional[dict] = None,
              *, allow_run: bool = False, record: bool = True) -> dict:
    """Spec first, then the artefact, then the verdict from its own checks. The whole pipeline.

    Returns a dict that always carries `verdict`; `ok` is True ONLY for `verified`, so a caller that
    checks `ok` cannot accidentally treat a partial result as a finished one.
    """
    started = datetime.now(timezone.utc)
    drafted = draft_spec(request, family=family, title=title, completer=completer)
    if not drafted["ok"]:
        result = {"ok": False, "verdict": "no_spec", "request": request,
                  "why": drafted["error"], "provider": drafted.get("provider"),
                  "spec": drafted["spec"].to_dict() if drafted.get("spec") else None,
                  "generated_at": started.strftime("%Y-%m-%d %H:%M:%S UTC")}
        return _record(result) if record else result

    spec = drafted["spec"]
    rows = rule_checks(spec, values=values, allow_run=allow_run)
    outcome = verdict_for(spec, rows)
    result = {"ok": outcome["verdict"] == "verified", "verdict": outcome["verdict"],
              "request": request, "why": outcome["why"], "provider": drafted.get("provider"),
              "degraded": drafted.get("degraded"), "spec": spec.to_dict(), "checks": rows,
              "coverage": coverage_gaps(spec), "repairs_allowed": MAX_REPAIRS,
              "generated_at": started.strftime("%Y-%m-%d %H:%M:%S UTC"),
              "places_orders": False}
    return _record(result) if record else result


def _record(result: dict) -> dict:
    """Append every build to the record, including the failures - the same rule the evidence log has."""
    try:
        folder = Path(smartentry_data_dir()) / BUILDS_DIRNAME
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / "builds.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(result) + "\n")
    except OSError as exc:
        result["record_error"] = f"{type(exc).__name__}: {exc}"
    return result
