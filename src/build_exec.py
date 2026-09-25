"""Generate the artefact, then actually RUN its checks - in a sandbox, never against the live system.

This is the builder's missing half. Until now `builder.py` wrote a spec and adjudicated whatever could
be decided without executing anything, which meant the verdict `verified` was **unreachable on real
work**: a `run` check always came back "not run here". A builder that cannot run what it built is a
spec writer. This closes that.

WHY EXECUTION FEEDBACK, AND WHAT IT IS WORTH
--------------------------------------------
The measured lever is large and it is free. CoCoGen (Iterative Refinement of Project-Level Code Context
with Compiler Feedback, ACL Findings 2024) improved code that depends on project context by **over 80%**
by handing the model its compiler and static-analysis errors instead of asking it to guess. Ruff and the
Python compiler are already installed here and cost nothing, which is the whole point: this is
capability bought with architecture rather than with a subscription or a bigger model.

Order matters and is deliberate. Static analysis runs FIRST and needs no execution at all, so a
syntactically broken artefact is rejected and repaired without a single line of model-written code ever
being run. Only something that compiles is allowed near the interpreter.

THE SAFETY BOUNDARY, STATED HONESTLY
------------------------------------
Running a check that a model wrote means running code that a model wrote. There is no way around that,
so it is bounded rather than pretended away:

* **OFF unless explicitly asked.** `allow_execution` defaults False and the caller must pass True.
  Nothing on a schedule turns it on.
* **A temp folder outside the repository**, and the process's working directory is that folder - never
  `ROOT`. `skill_acceptance.run_checks` uses `cwd=ROOT`, which is right for a command the OWNER wrote
  in a skill file and wrong for one a model invented.
* **The repository is not importable.** `PYTHONPATH` is emptied and `-I` isolates the interpreter, so
  generated code cannot `import trading.mt5_service` and reach the account. This is the guard that
  matters most in this project.
* **A stripped environment.** No inherited variables, so no secrets, tokens or control-API keys are
  visible to it.
* **An allowlist, not a denylist.** Only `python -m pytest <path>` and `python <file>` are permitted,
  with every path resolved and required to sit inside the sandbox. `python -c` is refused outright
  because inline code is unbounded by construction.
* **A timeout**, and every attempt recorded.

What this does NOT give: a container, a network block, or protection from a generated *test file* that
contains something destructive - pytest will execute whatever that file says. The blast radius is
reduced to a temp directory with no repo on its path and no credentials in its environment; it is not
zero. That is why it is opt-in, and why it must never be switched on unattended on the machine that
holds live positions.
"""
from __future__ import annotations

import ast
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from .runtime_paths import smartentry_data_dir

SANDBOX_PREFIX = "i40_build_"
DEFAULT_TIMEOUT = 120
MAX_ARTEFACT_BYTES = 200_000

# Only these two shapes. Anything else is refused with the reason, never silently skipped.
PYTEST_FORM = ("-m", "pytest")
# A fenced block, preferred; otherwise the whole answer is treated as code and must still compile.
FENCE = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.S)

# The INTERFACE is stated to both sides from one variable, so the code and the tests cannot disagree
# about it. Measured need, 25 September 2026: asked for `clamp`, the model wrote a logically perfect
# function and named it `bound_value`, then `check_value` - while the tests, written from the same spec,
# imported `clamp`. Every test failed on ImportError and three repair rounds never found it. An
# interface mismatch is not a bug to be repaired, it is a bug to be made impossible.
CODE_PROMPT = """Write the Python file that satisfies this specification. Output ONLY the code, in one
```python block, with no explanation before or after.

{rules}

THE INTERFACE IS FIXED. The file is `{module}.py` and it MUST define a function named exactly
`{entry}`. It will be imported as `from {module} import {entry}`. Do not rename it, do not wrap it and
do not choose a name you prefer - the tests already import that exact name and nothing else.

Requirements:
* The whole file must compile as written - no placeholders, no `...`, no TODO.
* Handle every rule above, including the ones about empty, invalid and boundary input.
* Import only the Python standard library. Nothing else is available where this runs.
"""

TEST_PROMPT = """Write pytest tests for this specification. Output ONLY the code, in one ```python
block.

{rules}

Import the thing under test with exactly this line and no other:
    from {module} import {entry}

Requirements:
* Import only pytest, the standard library, and that one import.
* One test function per rule, named so the rule it covers is obvious.
* Assert the BEHAVIOUR the rule describes, including for empty and invalid input.
* Test what the rules SAY. Do not soften a test to make it easier to pass.
"""

REPAIR_PROMPT = """This code does not work. Fix it and output ONLY the corrected full file in one
```python block.

The specification it must satisfy:
{rules}

The code, which must keep defining `{entry}` under exactly that name:
```python
{code}
```

What actually went wrong when it was checked:
{error}

Fix the cause of that error. Do not change anything the error did not complain about.
"""


def code_from(answer: str) -> str:
    """Pull the code out of a model answer. A fenced block wins; otherwise the whole answer is tried."""
    found = FENCE.findall(answer or "")
    if found:
        return found[0].strip()
    return (answer or "").strip()


def static_check(code: str) -> dict:
    """Does it compile, and does a linter object? No execution whatsoever happens here.

    Run before anything else precisely so that a broken artefact is repaired without the interpreter
    ever being handed model-written code.
    """
    if not code.strip():
        return {"ok": False, "stage": "empty", "error": "the model returned no code"}
    if len(code.encode("utf-8")) > MAX_ARTEFACT_BYTES:
        return {"ok": False, "stage": "size",
                "error": f"artefact is {len(code)} chars, over the {MAX_ARTEFACT_BYTES} limit"}
    try:
        ast.parse(code)
    except SyntaxError as exc:
        return {"ok": False, "stage": "compile",
                "error": f"SyntaxError line {exc.lineno}: {exc.msg}"}
    return {"ok": True, "stage": "compile", "error": ""}


def lint(path: Path, cwd: Path) -> dict:
    """Ruff if it is installed, otherwise say so rather than silently reporting a pass."""
    if shutil.which("ruff") is None:
        return {"ok": True, "available": False, "error": "",
                "note": "ruff is not installed, so style was not checked - not the same as clean"}
    try:
        proc = subprocess.run(["ruff", "check", "--output-format", "concise", str(path)],
                              cwd=str(cwd), capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": True, "available": False, "error": "", "note": f"ruff failed to run: {exc}"}
    return {"ok": proc.returncode == 0, "available": True,
            "error": (proc.stdout or proc.stderr or "").strip()[:1200], "note": ""}


def safe_command(spec: str, sandbox: Path) -> dict:
    """Is this model-written command one we are willing to execute? Allowlist, with the reason.

    Returns {ok, argv, reason}. Refusing is the safe outcome and is reported as "not adjudicated",
    never as a failure of the code being built - the command was the problem, not the artefact.
    """
    try:
        argv = shlex.split(spec or "")
    except ValueError as exc:
        return {"ok": False, "argv": [], "reason": f"could not parse the command: {exc}"}
    if not argv:
        return {"ok": False, "argv": [], "reason": "empty command"}

    head = Path(argv[0]).name.lower()
    if head not in ("python", "python.exe", "python3", "python3.exe", "py", "py.exe"):
        return {"ok": False, "argv": argv,
                "reason": f"only python may be run here, not {argv[0]!r}"}
    rest = argv[1:]
    if "-c" in rest:
        return {"ok": False, "argv": argv,
                "reason": "python -c is inline code with no bound on what it can do; refused"}
    # A short list of pytest flags that only affect OUTPUT, plus -m. Anything else is refused: a flag
    # is an instruction to the interpreter and this is the one place model-written input reaches it.
    # (-q was in my own generated command and this check refused it, so the tests silently never ran.)
    allowed_flags = {"-m", "-q", "-x", "--tb=short", "--tb=line", "--no-header", "-p"}
    bad = [part for part in rest if part.startswith("-") and part not in allowed_flags]
    if bad:
        return {"ok": False, "argv": argv,
                "reason": f"flag(s) not on the allowlist: {bad!r}"}

    if rest[:2] == list(PYTEST_FORM):
        targets = [part for part in rest[2:] if not part.startswith("-")]
        targets = [t for t in targets if t != "pytest"]
    elif rest and not rest[0].startswith("-"):
        targets = [rest[0]]
    else:
        return {"ok": False, "argv": argv,
                "reason": f"expected `python -m pytest <path>` or `python <file>`, got {rest!r}"}

    # Every path must resolve INSIDE the sandbox. This is what stops a command reaching the repo.
    root = sandbox.resolve()
    for target in targets:
        candidate = (sandbox / target).resolve() if not Path(target).is_absolute() else Path(target).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            return {"ok": False, "argv": argv,
                    "reason": f"{target!r} resolves outside the sandbox ({candidate}); refused"}
    return {"ok": True, "argv": [sys.executable, "-I"] + rest, "reason": ""}


def sandbox_env() -> dict:
    """A stripped environment: no inherited variables, and the repository NOT importable.

    PYTHONPATH is emptied and the interpreter is run with -I, so generated code cannot import this
    project and reach a broker connection. SYSTEMROOT stays because Windows needs it to start python
    at all, and TEMP because pytest writes there.
    """
    keep = {}
    for name in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATHEXT", "COMSPEC", "NUMBER_OF_PROCESSORS"):
        if os.environ.get(name):
            keep[name] = os.environ[name]
    keep["PYTHONPATH"] = ""
    keep["PYTHONDONTWRITEBYTECODE"] = "1"
    keep["PATH"] = str(Path(sys.executable).parent)
    return keep


def run_checked(argv: list, sandbox: Path, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """Execute an already-vetted command in the sandbox. Never called with an unvetted argv."""
    try:
        proc = subprocess.run(argv, cwd=str(sandbox), env=sandbox_env(), capture_output=True,
                              text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "exit": None, "output": f"timed out after {timeout}s"}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "exit": None, "output": f"{type(exc).__name__}: {exc}"}
    output = ((proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")).strip()
    return {"ok": proc.returncode == 0, "exit": proc.returncode, "output": output[:4000]}


def rules_text(spec) -> str:
    return "\n".join(f"R{rule.number}: {rule.text}" for rule in getattr(spec, "rules", []) or [])


def build_and_verify(request: str, *, family: str = "software", title: str = "",
                     completer: Optional[Callable] = None, allow_execution: bool = False,
                     max_repairs: int = 3, timeout: int = DEFAULT_TIMEOUT,
                     keep_sandbox: bool = False, record: bool = True) -> dict:
    """Spec -> code -> compile -> lint -> run its own checks -> repair -> verdict.

    `allow_execution` must be passed True explicitly. With it False the artefact is still generated and
    STATICALLY checked, which is useful and completely safe; the `run` checks stay unadjudicated and the
    verdict is capped at `partial`, exactly as before. Nothing about this runs on a schedule.
    """
    from .builder import draft_spec, rule_checks, verdict_for

    started = datetime.now(timezone.utc)
    if completer is None:
        from .ai_provider import complete as completer

    drafted = draft_spec(request, family=family, title=title, completer=completer)
    if not drafted["ok"]:
        return _record_build({"ok": False, "verdict": "no_spec", "request": request,
                        "why": drafted["error"], "provider": drafted.get("provider")}, started, record)

    spec = drafted["spec"]
    rules = rules_text(spec)
    module = re.sub(r"\W+", "_", (title or "artefact")).strip("_").lower() or "artefact"
    # One name, handed to the code prompt, the test prompt and every repair. Deriving it
    # here instead of letting the model pick makes an interface mismatch impossible.
    entry = module

    box = Path(tempfile.mkdtemp(prefix=SANDBOX_PREFIX))
    attempts = []
    try:
        answer = completer(CODE_PROMPT.format(rules=rules, module=module, entry=entry),
                            task="builder.code")
        if not answer.get("ok"):
            return _record_build({"ok": False, "verdict": "no_artefact", "request": request,
                            "spec": spec.to_dict(), "why": answer.get("error") or "no code came back",
                            "sandbox": str(box)}, started, record)
        code = code_from(answer.get("text") or "")

        # --- compile and lint, repairing without ever executing anything ---------------------------
        for attempt in range(max_repairs + 1):
            checked = static_check(code)
            attempts.append({"attempt": attempt + 1, "stage": checked["stage"], "ok": checked["ok"],
                             "error": checked["error"][:300]})
            if checked["ok"]:
                break
            if attempt == max_repairs:
                return _record_build({"ok": False, "verdict": "failed", "request": request,
                                "spec": spec.to_dict(), "attempts": attempts,
                                "why": f"still does not compile after {max_repairs} repairs: "
                                       f"{checked['error']}", "sandbox": str(box)}, started, record)
            repaired = completer(REPAIR_PROMPT.format(rules=rules, code=code, entry=entry,
                                                       error=checked["error"]),
                                 task="builder.repair")
            if not repaired.get("ok"):
                return _record_build({"ok": False, "verdict": "failed", "request": request,
                                "spec": spec.to_dict(), "attempts": attempts,
                                "why": f"repair failed: {repaired.get('error')}",
                                "sandbox": str(box)}, started, record)
            code = code_from(repaired.get("text") or "")

        artefact = box / f"{module}.py"
        artefact.write_text(code, encoding="utf-8")
        linted = lint(artefact, box)

        # --- tests, written from the SPEC and not from the code -----------------------------------
        # This ordering is the whole point. The tests are generated from the rules, so they encode what
        # the thing is supposed to do rather than what the code happens to do - which is the property
        # the +38pp grounding result depends on. Tests written by reading the implementation would
        # agree with its bugs.
        #
        # The command that runs them is built HERE, not taken from the model. A spec's `run` check names
        # an arbitrary path that does not exist in the sandbox, so obeying it would guarantee a failure
        # that says nothing about the artefact.
        executions = []
        tests = {"generated": False}
        if allow_execution:
            answer = completer(TEST_PROMPT.format(module=module, entry=entry, rules=rules),
                                  task="builder.tests")
            test_code = code_from(answer.get("text") or "") if answer.get("ok") else ""
            test_static = static_check(test_code)
            tests = {"generated": bool(test_code), "compiles": test_static["ok"],
                     "error": test_static["error"][:300], "lines": len(test_code.splitlines())}
            if test_static["ok"]:
                test_file = box / f"test_{module}.py"
                test_file.write_text(test_code, encoding="utf-8")
                vetted = safe_command(f"python -m pytest -q {test_file.name}", box)
                if not vetted["ok"]:            # cannot happen with a name we built, but never assume
                    executions.append({"rule": None, "ran": False, "passed": None,
                                       "reason": vetted["reason"]})
                else:
                    result = run_checked(vetted["argv"], box, timeout=timeout)
                    # --- REPAIR FROM THE REAL ERROR. This is the CoCoGen lever (+80% on project-context
                    # code): hand the model what actually went wrong instead of asking it to guess.
                    #
                    # The ARTEFACT is repaired and the TESTS are never touched. The tests were written
                    # from the spec, so they are the authority on what the thing should do; editing them
                    # to agree with the code is how a loop "fixes" a bug by deleting the evidence of it.
                    # Measured need for this on 25 Sep 2026: the first attempt at clamp() was logically
                    # perfect but named the function bound_value, so every test failed on ImportError -
                    # a one-line fix that no amount of re-prompting from the spec alone would find.
                    for repair in range(max_repairs):
                        if result["ok"]:
                            break
                        tests.setdefault("repairs", []).append(
                            {"round": repair + 1, "exit": result["exit"],
                             "error": result["output"][:300]})
                        fixed = completer(REPAIR_PROMPT.format(rules=rules, code=code, entry=entry,
                                                               error=result["output"][:2000]),
                                          task="builder.repair_from_test")
                        if not fixed.get("ok"):
                            break
                        candidate = code_from(fixed.get("text") or "")
                        if not static_check(candidate)["ok"]:
                            break            # a repair that does not compile is not a repair
                        code = candidate
                        artefact.write_text(code, encoding="utf-8")
                        result = run_checked(vetted["argv"], box, timeout=timeout)
                    tests["passed"] = result["ok"]
                    tests["exit"] = result["exit"]
                    tests["output"] = result["output"][:1500]
                    tests["repair_rounds"] = len(tests.get("repairs") or [])
                    # The pytest run is the evidence for every rule whose check was a `run`.
                    for rule in spec.rules:
                        if rule.check and rule.kind == "run":
                            executions.append({"rule": rule.number, "ran": True,
                                               "passed": result["ok"], "exit": result["exit"],
                                               "output": result["output"][:300],
                                               "by": f"pytest {test_file.name}"})

        rows = rule_checks(spec, allow_run=False)
        # A `file:` rule is adjudicated by skill_acceptance against the REPOSITORY, but a sandbox build
        # writes into a temp folder. So a file check naming a repo path is not evidence about this
        # artefact either way - it is unadjudicable here, and reporting it as failed would blame the
        # code for where the spec decided the file should live.
        # A check the adjudicator could not even READ is a defect in the SPEC, not evidence about the
        # artefact. skill_acceptance calls it failed, which is right for a check the owner hand-wrote in
        # a skill file and wrong for one the builder invented - blaming the code for the spec's bad
        # check is the misattribution this project keeps having to correct.
        spec_defects = []
        for row in rows:
            if "could not read the rule" in (row.get("detail") or ""):
                row["adjudicated"] = False
                row["passed"] = False
                spec_defects.append({"rule": row["rule"], "check": row["check"],
                                     "why": "the check is malformed, so it proves nothing either way"})
                row["detail"] = ("not adjudicated: the builder wrote a malformed check "
                                 f"({row['check']}) - a spec defect, not a code failure")
        for row in rows:
            if row["kind"] == "file" and not row["passed"]:
                named = row["check"].split(":", 1)[1].strip().split(" contains ")[0].strip()
                inside = (box / named).resolve()
                try:
                    inside.relative_to(box.resolve())
                    outside = False
                except ValueError:
                    outside = True
                if outside or not inside.exists():
                    row["adjudicated"] = False
                    row["detail"] = (f"not adjudicated: the spec names {named!r}, which is not "
                                     f"something this sandbox build creates")
        # Fold what actually ran into the adjudicated rows: an execution is stronger evidence than
        # "not adjudicated", in either direction.
        by_rule = {row["rule"]: row for row in rows}
        for execution in executions:
            row = by_rule.get(execution["rule"])
            if row is None or not execution["ran"]:
                continue
            row["passed"] = bool(execution["passed"])
            row["adjudicated"] = True
            row["detail"] = f"ran in the sandbox: exit {execution['exit']}"
        outcome = verdict_for(spec, list(by_rule.values()))

        return _record_build({
            "ok": outcome["verdict"] == "verified", "verdict": outcome["verdict"], "request": request,
            "why": outcome["why"], "provider": drafted.get("provider"), "spec": spec.to_dict(),
            "checks": list(by_rule.values()), "attempts": attempts, "executions": executions,
            "executed": bool(allow_execution), "lint": linted, "tests": tests,
            "spec_defects": spec_defects,
            "artefact": {"path": str(artefact), "lines": len(code.splitlines()),
                         "chars": len(code)},
            "sandbox": str(box), "places_orders": False,
        }, started, record)
    finally:
        if not keep_sandbox:
            shutil.rmtree(box, ignore_errors=True)


def _record_build(result: dict, started, record: bool) -> dict:
    result["generated_at"] = started.strftime("%Y-%m-%d %H:%M:%S UTC")
    result.setdefault("places_orders", False)
    if record:
        try:
            folder = Path(smartentry_data_dir()) / "builds"
            folder.mkdir(parents=True, exist_ok=True)
            with (folder / "builds.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(result) + "\n")
        except OSError as exc:
            result["record_error"] = f"{type(exc).__name__}: {exc}"
    return result
