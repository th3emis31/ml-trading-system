"""i40 Pilot layer 5 - a skill must say how to tell its own output is correct. Build map step 4.

The rule this module exists to enforce:

    **No skill may report success on the model's own say-so.**

A model asked "did that work?" will usually answer yes. That is not dishonesty, it is the shape of
the thing: it has no independent view of the artefact it just produced. So every skill declares at
least one check that some OTHER mechanism performs - a command that exits non-zero, a file that must
exist or contain something, a number that must clear a threshold, or a question only the owner can
answer. The model may run the check; it may not BE the check.

This matters most offline, which is the owner's constraint. A local 7B is more likely to declare
victory over broken work than Claude is, and the only defence that scales down to a weaker model is a
check the model does not adjudicate.

Declared in SKILL.md as a fenced block:

    ## Acceptance

    ```acceptance
    run: python -m pytest -q tests/test_thing.py
    file: data/thing.json contains "places_orders": false
    number: profit_factor >= 1.0
    ask: did the chart show the levels where you expected?
    ```

Four kinds, and the distinction that matters is not how strong they are but WHO adjudicates:
``run`` and ``file`` and ``number`` are adjudicated by the machine, ``ask`` by the owner. All four
are independent of the model. Nothing else counts.
"""
from __future__ import annotations

import json
import re
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = ROOT / ".claude" / "skills"

BLOCK = re.compile(r"```acceptance\s*\n(.*?)```", re.S | re.I)
CHECK = re.compile(r"^\s*(run|file|number|ask|appended)\s*:\s*(.+?)\s*$", re.I)
FAMILY_TAG = re.compile(r"^family:\s*(\S+)\s*$", re.M | re.I)

# The five families the owner named, plus `core` for skills that serve the system itself rather than
# a domain. A family with no skill is reported as a gap rather than quietly missing.
FAMILIES = ("trading", "software", "market", "business", "media")

# Adjudicated by the machine. `ask` is adjudicated by the owner, which is also not the model - but it
# cannot run unattended, so a skill whose ONLY check is `ask` is flagged rather than accepted.
AUTOMATIC = {"run", "file", "number", "appended"}

NUMBER_RULE = re.compile(r"^([A-Za-z_][\w.]*)\s*(>=|<=|>|<|==)\s*(-?[\d.]+)$")


@dataclass
class Check:
    kind: str
    spec: str
    passed: Optional[bool] = None
    detail: str = ""

    @property
    def automatic(self) -> bool:
        return self.kind in AUTOMATIC


@dataclass
class SkillAcceptance:
    name: str
    path: str
    checks: list = field(default_factory=list)
    family: str = ""

    @property
    def declared(self) -> bool:
        return bool(self.checks)

    @property
    def has_automatic(self) -> bool:
        return any(c.automatic for c in self.checks)

    def as_dict(self) -> dict:
        return {"name": self.name, "path": self.path, "family": self.family,
                "declared": self.declared,
                "has_automatic": self.has_automatic,
                "checks": [{"kind": c.kind, "spec": c.spec, "passed": c.passed,
                            "detail": c.detail} for c in self.checks]}


def parse(text: str) -> list:
    """The checks a SKILL.md declares. An unparseable line is dropped, never guessed at."""
    found = []
    for block in BLOCK.findall(text or ""):
        for line in block.splitlines():
            if not line.strip() or line.strip().startswith("#"):
                continue
            match = CHECK.match(line)
            if match:
                found.append(Check(kind=match.group(1).lower(), spec=match.group(2).strip()))
    return found


def read_skills(skills_dir: Optional[Path] = None) -> list:
    directory = Path(skills_dir or SKILLS_DIR)
    out = []
    for skill_file in sorted(directory.glob("*/SKILL.md")):
        text = skill_file.read_text(encoding="utf-8", errors="replace")
        try:
            shown = str(skill_file.relative_to(ROOT))
        except ValueError:
            shown = str(skill_file)
        tag = FAMILY_TAG.search(text)
        out.append(SkillAcceptance(name=skill_file.parent.name, path=shown, checks=parse(text),
                                   family=(tag.group(1).lower() if tag else "")))
    return out


def coverage(skills_dir: Optional[Path] = None) -> dict:
    """Which skills can prove their own output, and which are still taking the model's word for it."""
    found = read_skills(skills_dir)
    undeclared = [s.name for s in found if not s.declared]
    manual_only = [s.name for s in found if s.declared and not s.has_automatic]
    ready = [s.name for s in found if s.has_automatic]
    by_family: dict = {}
    for skill in found:
        by_family.setdefault(skill.family or "untagged", []).append(skill.name)
    missing = [f for f in FAMILIES if f not in by_family]
    return {
        "total": len(found), "with_automatic_check": len(ready),
        "by_family": by_family, "families_covered": len(FAMILIES) - len(missing),
        "families_missing": missing,
        "coverage_pct": round(100.0 * len(ready) / len(found), 1) if found else None,
        "ready": ready, "manual_only": manual_only, "undeclared": undeclared,
        "skills": [s.as_dict() for s in found],
        "rule": ("A skill may not report success on the model's own say-so. At least one check must be "
                 "adjudicated by something else: a command's exit code, a file's contents, a number "
                 "against a threshold, or the owner."),
    }


def _check_file(spec: str) -> Check:
    """``file: path`` or ``file: path contains TEXT``."""
    check = Check("file", spec)
    if " contains " in spec:
        raw_path, needle = spec.split(" contains ", 1)
        needle = needle.strip().strip('"').strip("'")
    else:
        raw_path, needle = spec, None
    path = ROOT / raw_path.strip()
    if not path.exists():
        check.passed, check.detail = False, f"{raw_path.strip()} does not exist"
        return check
    if needle is None:
        check.passed, check.detail = True, f"{raw_path.strip()} exists"
        return check
    if path.is_dir():
        # Reading a directory as text raises PermissionError on Windows. Searching its file NAMES is
        # the sensible reading of "does this folder contain X".
        names = [child.name for child in path.iterdir()]
        check.passed = any(needle.lower() in name.lower() for name in names)
        check.detail = (f"{raw_path.strip()}/ holds a file matching {needle!r}" if check.passed
                        else f"no file in {raw_path.strip()}/ matches {needle!r} ({len(names)} files)")
        return check
    body = path.read_text(encoding="utf-8", errors="replace")
    check.passed = needle in body
    check.detail = (f"found {needle!r}" if check.passed else f"{needle!r} not in {raw_path.strip()}")
    return check


def _check_number(spec: str, values: Optional[dict]) -> Check:
    """``number: profit_factor >= 1.0``, read from a dict the caller supplies.

    Without values this is UNKNOWN, never a pass. A threshold nobody measured is not a threshold met.
    """
    check = Check("number", spec)
    match = NUMBER_RULE.match(spec.strip())
    if not match:
        check.passed, check.detail = False, "could not read the rule - expected 'name >= 1.0'"
        return check
    key, op, target = match.group(1), match.group(2), float(match.group(3))
    if not values or key not in values:
        check.passed, check.detail = None, f"{key} was not measured, so this is unknown - not a pass"
        return check
    try:
        actual = float(values[key])
    except (TypeError, ValueError):
        check.passed, check.detail = None, f"{key} is {values[key]!r}, which is not a number"
        return check
    check.passed = {">=": actual >= target, "<=": actual <= target, ">": actual > target,
                    "<": actual < target, "==": actual == target}[op]
    check.detail = f"{key} = {actual} (needs {op} {target})"
    return check


def run_checks(skill: SkillAcceptance, values: Optional[dict] = None, *, allow_run: bool = False,
               timeout: int = 300, runner=None) -> SkillAcceptance:
    """Adjudicate what can be adjudicated here.

    ``allow_run`` is off by default: executing a shell command a skill file names is a real action,
    and layer 7 governance decides whether that is permitted. Until then this reports the command
    rather than running it, which is the honest state and not a pass.
    """
    for check in skill.checks:
        if check.kind == "file":
            done = _check_file(check.spec)
            check.passed, check.detail = done.passed, done.detail
        elif check.kind == "number":
            done = _check_number(check.spec, values)
            check.passed, check.detail = done.passed, done.detail
        elif check.kind == "appended":
            # "Did the work actually get written down today?" - several skills need exactly this, and
            # writing it as `file: X contains <a description of the thing>` produced checks that could
            # never pass, which is worse than declaring none.
            target = ROOT / check.spec.strip()
            if not target.exists():
                check.passed, check.detail = False, f"{check.spec.strip()} does not exist"
            else:
                from datetime import datetime
                changed = datetime.fromtimestamp(target.stat().st_mtime).date()
                today = datetime.now().date()
                check.passed = changed == today
                check.detail = (f"last written {changed}"
                                + ("" if check.passed else " - nothing was recorded today"))
        elif check.kind == "ask":
            check.passed, check.detail = None, "needs the owner's answer"
        elif check.kind == "run":
            if not allow_run:
                check.passed, check.detail = None, f"not run here (governance): {check.spec}"
                continue
            try:
                proc = (runner or subprocess.run)(shlex.split(check.spec), cwd=str(ROOT),
                                                  capture_output=True, text=True, timeout=timeout)
                check.passed = proc.returncode == 0
                check.detail = f"exit {proc.returncode}: {(proc.stdout or proc.stderr or '').strip()[:160]}"
            except Exception as exc:
                check.passed, check.detail = False, f"{type(exc).__name__}: {exc}"
    return skill


def may_report_success(skill: SkillAcceptance) -> dict:
    """May this skill report success?

    Yes only when at least one check was adjudicated by something other than the model AND passed,
    and nothing that ran actually failed. Unknown is not a pass - an unmeasured threshold and an
    unanswered question both leave the work unproven, and unproven must not read as done.
    """
    if not skill.declared:
        return {"may_report_success": False, "reason": "the skill declares no acceptance check, so "
                                                       "success would rest on the model's own say-so"}
    failed = [c for c in skill.checks if c.passed is False]
    passed = [c for c in skill.checks if c.passed is True]
    unknown = [c for c in skill.checks if c.passed is None]
    if failed:
        return {"may_report_success": False,
                "reason": f"{len(failed)} check(s) failed: " + "; ".join(c.detail for c in failed[:3])}
    if not passed:
        return {"may_report_success": False,
                "reason": ("nothing was adjudicated - " + "; ".join(c.detail for c in unknown[:3])
                           if unknown else "no check produced a result")}
    return {"may_report_success": True,
            "reason": f"{len(passed)} independent check(s) passed"
                      + (f", {len(unknown)} still unknown" if unknown else ""),
            "unknown": [c.spec for c in unknown]}


if __name__ == "__main__":       # pragma: no cover - a hand check, not a test
    print(json.dumps(coverage(), indent=1))
