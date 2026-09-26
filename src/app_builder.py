"""Build a whole APPLICATION from a request - build map step 11. Never trades, never touches the repo.

    python -m src.app_builder plan "a tool that tracks my reading list"
    python -m src.app_builder build "a tool that tracks my reading list" --kind cli --execute
    python -m src.app_builder report

WHY THIS IS A DIFFERENT PROBLEM FROM `build_exec`
------------------------------------------------
`src/build_exec.py` builds ONE module and proves it with tests. That is the right shape for a function and
the wrong shape for an application, because an application has two properties a module does not:

* **It is several files that have to agree with each other.** The failure mode is not a wrong line, it is
  file A importing a name file B never defined. Tests written per file cannot see that.
* **It has to START.** A program whose unit tests pass and which crashes on launch is worthless, and it is
  exactly what a test-only check certifies as working. So the acceptance test here is a **smoke run**: the
  thing is started as a user would start it, asked for something, and has to answer.

THE CONTRACT IS OURS, NOT THE MODEL'S
-------------------------------------
Every prompt below is handed the same `Contract`: the entry file's name, how it is invoked, and what it must
answer. That is the lesson `build_exec` learned the hard way - when the model chose the interface, the code
and its tests each invented a different one and the mismatch looked like a code bug. The model fills in a
contract it is given; it does not get to negotiate it.

WHAT IS DELIBERATELY REFUSED
----------------------------
* **Third-party packages.** Standard library only. Installing a package means reaching the network from a
  sandbox running model-written code, and no amount of allowlisting makes that a good idea.
* **Anything outside the sandbox.** Paths are vetted against the sandbox root before a byte is written, the
  interpreter runs with `-I` and an empty `PYTHONPATH` (so generated code cannot import this project and
  reach a broker), and a web app may bind only 127.0.0.1 on an ephemeral port.
* **A GUI.** A window cannot be smoke-tested without a person looking at it, and a check nobody can run is
  not a check.

`verified` requires all of: every file compiles, the tests pass, AND the app started and answered. Anything
less is `partial` with the reason, and the verdict comes from the checks - never from the model saying so.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from .build_exec import (DEFAULT_TIMEOUT, MAX_ARTEFACT_BYTES, code_from, lint, run_checked, safe_command,
                         sandbox_env, static_check)

SANDBOX_PREFIX = "i40_app_"
MAX_FILES = 8                     # a first slice; more files is more ways for them to disagree
MAX_REPAIRS = 3
SMOKE_TIMEOUT = 25
START_GRACE = 12.0                # seconds a web app gets to start listening
APP_BUILDS_NAME = "app_builds.jsonl"

# An application is several files that import each other, and under python -I the script's own
# directory is NOT on sys.path - so the first sibling import dies with ModuleNotFoundError. -E -s
# still ignores every environment variable (PYTHONPATH cannot reach this repository) and still
# skips user site-packages; it only adds the sandbox itself. See safe_command's docstring.
APP_ISOLATION = ("-E", "-s")

# A file name the model chose still has to be one we are willing to create.
SAFE_NAME = re.compile(r"^[a-z][a-z0-9_]{0,40}\.py$")
SAFE_DIR = re.compile(r"^[a-z][a-z0-9_]{0,20}$")
SAFE_EXPORT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,40}$")
MAX_EXPORTS = 8


@dataclass
class Contract:
    """The interface the app must expose. Ours, stated identically to every prompt."""

    kind: str                 # "cli" or "web"
    entry: str                # the file a user runs
    invocation: str           # how it is run, in words the prompt repeats verbatim
    must_answer: str          # what the smoke run will look for


def contract_for(kind: str, entry: str = "app.py") -> Contract:
    """The two app kinds this can prove. A kind whose success cannot be checked is not offered."""
    if kind == "web":
        return Contract(
            kind="web", entry=entry,
            invocation=(f"python {entry} --port PORT   (PORT is given on the command line; bind "
                        "127.0.0.1 only, using http.server from the standard library)"),
            must_answer="GET /health must return HTTP 200 with a short JSON body")
    if kind == "cli":
        return Contract(
            kind="cli", entry=entry,
            invocation=f"python {entry} --selftest",
            must_answer=("with --selftest it must do its main job once on temporary data, print exactly "
                         "ONE line starting with OK: and exit 0. It is a smoke check, not a test suite: "
                         "do not embed pytest, unittest or a bundled suite, and do not print a pass/fail "
                         "tally"))
    raise ValueError(f"unknown app kind {kind!r}; known kinds are cli and web")


PLAN_PROMPT = """Plan the FILES of a small Python application, before any code exists.

THE REQUEST
{request}

THE RULES IT MUST SATISFY
{rules}

HARD CONSTRAINTS
- Standard library only. No pip, no third-party imports, no network access.
- The user runs it as: {invocation}
- Success is judged by: {must_answer}
- As FEW files as the job needs - two or three is normal, {max_files} is the hard limit, and every extra
  file is one more interface that two files can disagree about. One of them MUST be {entry}.
- Every file is plain Python, lower_case_with_underscores.py, in the top folder or ONE subfolder.

Output ONLY a JSON array, no prose, each item exactly:
  {{"path": "name.py", "purpose": "one line on what this file owns",
   "exports": ["names other files may call"]}}
The first item must be {entry}. `exports` is the file's whole public interface - every function, class or
exception another file is allowed to use. Name them here and they become a requirement on that file: it
must define them, and no other file may call anything else of it.

REQUIRED: every file except {entry} must list at least one export. A plan that leaves them out will be
rejected, because then nothing can check that the files agree."""

PLAN_REJECTED = """

YOUR PREVIOUS ANSWER WAS REJECTED: {why}
Answer again, correcting exactly that. Output ONLY the JSON array."""

FILE_PROMPT = """Write one file of a Python application. Output ONLY the code in one ```python block.

THE FILE
{path} - {purpose}

THE WHOLE APPLICATION, so this file agrees with the others
{plan}

THE RULES THE APPLICATION MUST SATISFY
{rules}

HARD CONSTRAINTS
- Standard library only.
- The application is run as: {invocation}
- It is judged by: {must_answer}
- Import the other files by their module name ({modules}) - they sit beside this one, and the plan
  above lists exactly what each of them defines. Call only those names.
- THIS FILE MUST DEFINE: {exports}
- If this file is {entry}, it must work when run directly and must implement the invocation above."""

REPAIR_PROMPT = """This file of the application does not work. Fix it and output ONLY the corrected full
file in one ```python block.

THE FILE
{path}

THE ERROR
{error}

THE RULES THE APPLICATION MUST SATISFY
{rules}

HARD CONSTRAINTS (unchanged)
- Standard library only. Run as: {invocation}. Judged by: {must_answer}
- The other files are: {modules}
- THIS FILE MUST DEFINE: {exports}

THE CURRENT CONTENT
```python
{code}
```"""

TEST_PROMPT = """Write pytest tests for this application, from its RULES and not from its code.

THE RULES
{rules}

THE FILES, AND EXACTLY WHAT EACH ONE DEFINES
{plan}

THE INTERFACE (this is fixed; do not probe for it)
- The application is run as: {invocation}
- It is judged by: {must_answer}
- Entry point: {entry}. Call the names the plan lists above and no others.

HARD CONSTRAINTS
- Standard library and pytest only. The files sit beside the test file; import them by module name.
- Test what the rules say the application must DO. Do not test private helpers.
- No network, no sleeping for more than a second, no subprocesses.
- Do NOT try to discover how to call a function by trying several ways. The plan above is the contract;
  if something it names is missing, let the test fail on that plainly.

Output ONLY the code in one ```python block."""


def parse_file_plan(text: str, contract: Contract, max_files: int = MAX_FILES) -> dict:
    """The model's file plan, vetted. Returns {ok, files, why}.

    Every path is checked before anything is written, because a plan is model-written input and a path is
    the one field in it that can reach outside the sandbox.
    """
    raw = str(text or "").strip()
    match = re.search(r"\[.*\]", raw, flags=re.S)
    if not match:
        return {"ok": False, "files": [], "why": "no JSON array in the plan"}
    try:
        items = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        return {"ok": False, "files": [], "why": f"the plan is not valid JSON: {exc}"}
    if not isinstance(items, list) or not items:
        return {"ok": False, "files": [], "why": "the plan is not a non-empty array"}

    files, seen = [], set()
    for item in items[:max_files]:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip().replace("\\", "/")
        purpose = re.sub(r"\s+", " ", str(item.get("purpose") or "")).strip()[:160]
        declared = item.get("exports")
        exports = [str(n).strip() for n in declared][:MAX_EXPORTS] if isinstance(declared, list) else []
        exports = [n for n in exports if SAFE_EXPORT.match(n)]
        if path.startswith("/") or ":" in path:
            return {"ok": False, "files": [], "why": f"{path!r} is an absolute path"}
        if path.startswith("./"):
            path = path[2:]
        parts = [p for p in path.split("/") if p]
        if any(part == ".." for part in parts):
            return {"ok": False, "files": [], "why": f"{path!r} climbs out of the folder"}
        if len(parts) == 1:
            if not SAFE_NAME.match(parts[0]):
                return {"ok": False, "files": [], "why": f"unsafe file name {path!r}"}
        elif len(parts) == 2:
            if not (SAFE_DIR.match(parts[0]) and SAFE_NAME.match(parts[1])):
                return {"ok": False, "files": [], "why": f"unsafe path {path!r}"}
        else:
            return {"ok": False, "files": [], "why": f"{path!r} is deeper than one subfolder"}
        if path in seen:
            continue
        seen.add(path)
        files.append({"path": path, "purpose": purpose, "exports": exports})

    if not files:
        return {"ok": False, "files": [], "why": "no usable file in the plan"}
    # A plan with no declared interface silently disables both agreement checks, and the build then fails
    # later with a confusing error instead of here with a clear one. The cloud model's plan omitted the
    # field entirely and the app died on `book_store.InvalidTitleError`, which nothing had promised.
    silent = [f["path"] for f in files if f["path"] != contract.entry and not f["exports"]]
    if silent:
        return {"ok": False, "files": [],
                "why": ("every file but the entry point must declare what it defines, or nothing can check "
                        "that the files agree; these declared nothing: " + ", ".join(silent))}
    if contract.entry not in seen:
        # Add it rather than fail: the entry point is OUR requirement, so supplying it is not a repair of
        # the model's work, it is us holding up our own end of the contract.
        files.insert(0, {"path": contract.entry, "purpose": "the entry point a user runs",
                         "exports": []})
        files = files[:max_files]
    # The entry point is written first, so every other file is written knowing what it must serve.
    files.sort(key=lambda f: 0 if f["path"] == contract.entry else 1)
    return {"ok": True, "files": files, "why": ""}


def missing_exports(code: str, names: list) -> list:
    """Which declared names this file does NOT define at the top level.

    This is the check that would have caught the first real build: the local model wrote an `app.py` that
    called `book_counter.test()` and a `book_counter.py` that never defined `test`, and the mismatch was
    only discovered when the app crashed on launch. Compiling cannot see it - each file is valid Python on
    its own. The plan already says what each file owes the others, so it can be checked by reading the
    tree, before anything runs, and the repair can be told the exact missing name.
    """
    import ast

    if not names:
        return []
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return list(names)                    # unparseable: the static check owns that message
    defined = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(node.name)
        elif isinstance(node, ast.Assign):
            defined.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            defined.add(node.target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            defined.update((alias.asname or alias.name).split(".")[0] for alias in node.names)
    return [name for name in names if name not in defined]


def import_faults(code: str, path: str, plan_paths: list) -> list:
    """Imports this file must not make: itself, and anything that is not standard library or a plan file.

    Both were stated in the prompts and neither was CHECKED, which is the difference this whole system is
    built on. The local model's second build produced a `book_manager.py` containing
    `from book_manager import BookManager` - a module importing itself, which compiles, defines every name
    the plan asked for, and dies the moment anything loads it. And "standard library only" was an
    instruction a model could simply not follow, with nothing to catch it: a build that imports `requests`
    would be a build that fails on a machine with no internet, which is the one machine this must work on.
    """
    import ast
    import sys

    own = Path(path).stem
    siblings = {Path(other).stem for other in plan_paths}
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []                              # the static check owns that message
    faults = []
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [alias.name.split(".")[0] for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:                     # a relative import has no package here
                faults.append("a relative import, but these files are not a package")
                continue
            names = [(node.module or "").split(".")[0]]
        for name in names:
            if not name:
                continue
            if name == own:
                faults.append(f"{name!r} is this file itself - a module cannot import itself")
            elif name in siblings or name in sys.stdlib_module_names:
                continue
            else:
                faults.append(f"{name!r} is neither the standard library nor one of this app's files")
    seen, unique = set(), []
    for fault in faults:
        if fault not in seen:
            seen.add(fault)
            unique.append(fault)
    return unique


def unpromised_imports(code: str, plan: list) -> list:
    """Names this file imports from a sibling that the sibling's plan entry never promised.

    The mirror image of `missing_exports`, and the check the third build needed: `app.py` did
    `from book_list_manager import read_input, count_books` while the plan for that file promised neither,
    so there was nothing for the export check to compare against and the app died on its first import.

    Checking both directions makes the plan the single contract: a file must define everything it promised,
    and may call nothing that was not promised to it.
    """
    import ast

    promised = {Path(item["path"]).stem: set(item.get("exports") or []) for item in plan}
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    faults = []
    bound = {}                                 # local name -> sibling module, from `import X` / `import X as Y`
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                head = alias.name.split(".")[0]
                if head in promised:
                    bound[alias.asname or head] = head
            continue
        if not isinstance(node, ast.ImportFrom) or node.level:
            continue
        module = (node.module or "").split(".")[0]
        if module not in promised:
            continue                           # stdlib, or an unknown module: import_faults owns that
        for alias in node.names:
            if alias.name == "*":
                faults.append(f"`from {module} import *` hides which names are used; import them by name")
            elif alias.name not in promised[module]:
                faults.append(f"{alias.name!r} is imported from {module} but that file does not promise it "
                              f"(it promises: {', '.join(sorted(promised[module])) or 'nothing'})")

    # `import counter` then `counter.count_items(...)` reaches a name just as surely as importing it, and
    # that is how the generated TESTS reached names nothing had promised - four failures that read as app
    # bugs and were not.
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute) or not isinstance(node.value, ast.Name):
            continue
        module = bound.get(node.value.id)
        if module and node.attr not in promised[module] and not node.attr.startswith("__"):
            faults.append(f"{module}.{node.attr} is used but {module} does not promise it "
                          f"(it promises: {', '.join(sorted(promised[module])) or 'nothing'})")

    seen, unique = set(), []
    for fault in faults:
        if fault not in seen:
            seen.add(fault)
            unique.append(fault)
    return unique


def free_port() -> int:
    """An ephemeral port the OS says is free. Bound to 127.0.0.1 only, never to a public interface."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def smoke_cli(box: Path, contract: Contract, timeout: int = SMOKE_TIMEOUT) -> dict:
    """Run the app as a user would and require it to say OK. The check the module builder cannot make."""
    vetted = safe_command(f"python {contract.entry}", box, APP_ISOLATION)
    if not vetted["ok"]:
        return {"ran": False, "passed": None, "answered": None, "why": vetted["reason"], "output": ""}
    # --selftest is appended AFTER vetting, not passed through it: `safe_command`'s allowlist refuses any
    # flag it does not know, and it was right to - the flag is ours, so it is added on this side of the gate.
    argv = vetted["argv"] + ["--selftest"]
    result = run_checked(argv, box, timeout=timeout)
    lines = [l.strip() for l in (result["output"] or "").splitlines() if l.strip()]
    answered = any(l.startswith("OK:") for l in lines)
    return {"ran": True, "passed": bool(result["ok"] and answered), "exit": result["exit"],
            "why": "" if result["ok"] else "non-zero exit",
            "answered": answered, "output": (result["output"] or "")[:1500]}


def smoke_web(box: Path, contract: Contract, timeout: int = SMOKE_TIMEOUT) -> dict:
    """Start the app, ask /health, kill it. Killed on every path, including when the request raises."""
    port = free_port()
    vetted = safe_command(f"python {contract.entry}", box, APP_ISOLATION)
    if not vetted["ok"]:
        return {"ran": False, "passed": None, "status": None, "why": vetted["reason"], "output": ""}
    argv = vetted["argv"] + ["--port", str(port)]
    try:
        proc = subprocess.Popen(argv, cwd=str(box), env=sandbox_env(), stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ran": False, "passed": None, "why": f"{type(exc).__name__}: {exc}", "output": ""}

    status, body, why = None, "", ""
    try:
        deadline = time.monotonic() + START_GRACE
        listening = False
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                why = f"the app exited before it listened (exit {proc.returncode})"
                break
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.settimeout(0.4)
                if probe.connect_ex(("127.0.0.1", port)) == 0:
                    listening = True
                    break
            time.sleep(0.3)
        if listening:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5) as reply:
                    status = int(reply.status)
                    body = reply.read(2000).decode("utf-8", "replace")
            except urllib.error.HTTPError as exc:
                status, why = int(exc.code), f"/health answered {exc.code}"
            except (urllib.error.URLError, OSError, ValueError) as exc:
                why = f"{type(exc).__name__}: {exc}"
        elif not why:
            why = f"nothing was listening on 127.0.0.1:{port} after {START_GRACE:.0f}s"
    finally:
        proc.terminate()
        try:
            output = proc.communicate(timeout=5)[0] or ""
        except subprocess.TimeoutExpired:
            proc.kill()
            output = proc.communicate()[0] or ""

    return {"ran": True, "passed": status == 200, "status": status, "port": port,
            "why": why, "body": body[:500], "output": (output or "")[:1500]}


def smoke_run(box: Path, contract: Contract, timeout: int = SMOKE_TIMEOUT) -> dict:
    return smoke_web(box, contract, timeout) if contract.kind == "web" else smoke_cli(box, contract, timeout)


def app_verdict(files: list, tests: dict, smoke: dict, executed: bool) -> dict:
    """The verdict, from the checks alone.

    `verified` needs the whole chain. A build that was never executed is capped at `partial` no matter how
    clean it looks, because "it compiles" is not "it runs" - that gap is the entire reason this module has
    a smoke run in it.
    """
    broken = [f["path"] for f in files if not f.get("compiles")]
    if broken:
        return {"verdict": "failed", "ok": False,
                "why": f"{len(broken)} file(s) do not compile: {', '.join(broken)}"}
    # Compiling is not agreeing. A file that is valid Python but does not define what the plan said it
    # owes the others is the multi-file failure mode, and naming it as "does not compile" would send the
    # next reader looking for a syntax error that is not there.
    mismatched = [f"{f['path']} (missing {', '.join(f.get('missing_exports') or [])})"
                  for f in files if f.get("missing_exports")]
    if mismatched:
        return {"verdict": "failed", "ok": False,
                "why": "the files do not agree on their interface: " + "; ".join(mismatched)}
    bad = [f"{f['path']}: {'; '.join(f.get('import_faults') or [])}" for f in files if f.get("import_faults")]
    if bad:
        return {"verdict": "failed", "ok": False,
                "why": "imports that are not allowed here: " + " | ".join(bad)}
    if not executed:
        return {"verdict": "partial", "ok": False,
                "why": "every file compiles, but nothing was run: pass --execute to prove it starts"}
    if not smoke.get("ran"):
        return {"verdict": "partial", "ok": False,
                "why": f"the smoke run was not adjudicated: {smoke.get('why')}"}
    if not smoke.get("passed"):
        return {"verdict": "failed", "ok": False,
                "why": f"the application did not answer: {smoke.get('why') or 'no answer'}"}
    if tests.get("generated") and tests.get("ran") and not tests.get("passed"):
        return {"verdict": "partial", "ok": False,
                "why": "it starts and answers, but its own tests fail"}
    if not (tests.get("generated") and tests.get("ran")):
        return {"verdict": "partial", "ok": False,
                "why": "it starts and answers, but no test of its rules was adjudicated"}
    return {"verdict": "verified", "ok": True,
            "why": "every file compiles, the rule tests pass, and the application started and answered"}


def app_builds_path() -> Path:
    from .runtime_paths import smartentry_data_dir

    return Path(smartentry_data_dir()) / "builds" / APP_BUILDS_NAME


def record_app_build(result: dict, record: bool = True) -> dict:
    """Append every app build to its OWN file, failures included.

    Deliberately not `builder._record`: that one writes to builds.jsonl, which `build_map`
    counts by verdict as the MODULE builder's record. Appending app builds there would inflate
    those figures with a different kind of work, and the page would not be able to tell them
    apart. A separate file keeps each claim about its own artefact honest."""
    if not record:
        return result
    try:
        path = app_builds_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        row = {k: result.get(k) for k in ("at", "request", "title", "kind", "verdict", "ok", "why",
                                          "files_written", "executed")}
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, default=str) + "\n")
    except OSError as exc:                      # a failed record must not fail the build it describes
        result["record_error"] = f"{type(exc).__name__}: {exc}"
    return result


def build_app(request: str, *, title: str = "", kind: str = "cli",
              completer: Optional[Callable] = None, allow_execution: bool = False,
              max_files: int = MAX_FILES, max_repairs: int = MAX_REPAIRS,
              timeout: int = DEFAULT_TIMEOUT, keep_sandbox: bool = False,
              record: bool = True) -> dict:
    """Request -> spec -> file plan -> files -> compile -> repair -> tests -> smoke run -> verdict.

    `allow_execution` must be passed True explicitly, and without it the verdict is capped at `partial`:
    the files are written and statically checked, which is safe and useful, but nothing claims the app runs.
    Nothing here is ever on a schedule.
    """
    started = datetime.now(timezone.utc)
    stamp = started.strftime("%Y-%m-%d %H:%M:%S")
    if completer is None:
        from .ai_provider import complete as completer

    try:
        contract = contract_for(kind)
    except ValueError as exc:
        return record_app_build({"at": stamp, "ok": False, "verdict": "no_spec", "request": request, "kind": kind,
                        "why": str(exc)}, record)

    from .build_exec import rules_text
    from .builder import draft_spec

    drafted = draft_spec(request, family="software", title=title, completer=completer)
    if not drafted["ok"]:
        return record_app_build({"at": stamp, "ok": False, "verdict": "no_spec", "request": request, "kind": kind,
                        "title": title, "why": drafted["error"]}, record)
    spec = drafted["spec"]
    rules = rules_text(spec)

    answer = completer(PLAN_PROMPT.format(request=request, rules=rules, invocation=contract.invocation,
                                         must_answer=contract.must_answer, max_files=max_files,
                                         entry=contract.entry), task="app_builder.plan")
    if not answer.get("ok"):
        return record_app_build({"at": stamp, "ok": False, "verdict": "no_plan", "request": request, "kind": kind,
                        "title": title, "spec": spec.to_dict(),
                        "why": answer.get("error") or "no plan came back"}, record)
    planned = parse_file_plan(answer.get("text") or "", contract, max_files)
    if not planned["ok"]:
        # One retry, told exactly what was wrong. A rejected plan is usually a missing field rather than a
        # misunderstanding, and saying so is cheaper than failing the whole build over it.
        retry = completer(PLAN_PROMPT.format(request=request, rules=rules, invocation=contract.invocation,
                                            must_answer=contract.must_answer, max_files=max_files,
                                            entry=contract.entry)
                          + PLAN_REJECTED.format(why=planned["why"]),
                          task="app_builder.plan_retry")
        planned = parse_file_plan(retry.get("text") or "", contract, max_files) if retry.get("ok") else planned
    if not planned["ok"]:
        return record_app_build({"at": stamp, "ok": False, "verdict": "no_plan", "request": request,
                        "kind": kind, "title": title, "spec": spec.to_dict(),
                        "why": planned["why"]}, record)

    plan = planned["files"]
    plan_text = "\n".join(f"- {f['path']}: {f['purpose']}" for f in plan)
    modules = ", ".join(Path(f["path"]).stem for f in plan if f["path"] != contract.entry) or "none"

    box = Path(tempfile.mkdtemp(prefix=SANDBOX_PREFIX))
    files: list = []
    try:
        for item in plan:
            path, purpose = item["path"], item["purpose"]
            written = {"path": path, "purpose": purpose, "exports": list(item.get("exports") or []),
                       "compiles": False, "attempts": 0, "error": "", "lines": 0}
            answer = completer(FILE_PROMPT.format(path=path, purpose=purpose, plan=plan_text, rules=rules,
                                                 invocation=contract.invocation,
                                                 must_answer=contract.must_answer, modules=modules,
                                                 exports=", ".join(item.get("exports") or []) or "nothing in particular",
                                                 entry=contract.entry), task="app_builder.file")
            code = code_from(answer.get("text") or "") if answer.get("ok") else ""
            if not code:
                written["error"] = (answer.get("error") or "no code came back")[:300]
                files.append(written)
                continue

            for attempt in range(max_repairs + 1):
                written["attempts"] = attempt + 1
                checked = static_check(code)
                written["compiles"] = bool(checked["ok"])
                problem = "" if checked["ok"] else checked["error"]
                if not problem:
                    absent = missing_exports(code, item.get("exports") or [])
                    written["missing_exports"] = absent
                    written["interface_ok"] = not absent
                    if absent:
                        # Stated as the requirement it is, and named, so the repair has somewhere to go.
                        problem = ("this file must define the names the plan gave it, because other files "
                                   "call them, and these are missing: " + ", ".join(absent))
                if not problem:
                    bad_imports = import_faults(code, path, [f["path"] for f in plan])
                    unpromised = unpromised_imports(code, plan)
                    written["import_faults"] = bad_imports + unpromised
                    if bad_imports:
                        problem = "these imports are not allowed: " + "; ".join(bad_imports)
                    elif unpromised:
                        problem = "this file imports names no other file promised: " + "; ".join(unpromised)
                if not problem:
                    written["error"] = ""
                    break
                written["error"] = problem[:300]
                if attempt == max_repairs:
                    break
                repaired = completer(REPAIR_PROMPT.format(path=path, error=problem, rules=rules,
                                                          invocation=contract.invocation,
                                                          must_answer=contract.must_answer,
                                                          modules=modules, code=code,
                                                          exports=", ".join(item.get("exports") or []) or "nothing in particular"),
                                     task="app_builder.repair")
                if not repaired.get("ok"):
                    written["error"] = f"repair failed: {repaired.get('error')}"[:300]
                    break
                code = code_from(repaired.get("text") or "")

            if written["compiles"]:
                target = box / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(code[:MAX_ARTEFACT_BYTES], encoding="utf-8")
                written["lines"] = len(code.splitlines())
                written["lint"] = lint(target, box)
            files.append(written)

        tests: dict = {"generated": False, "ran": False, "passed": None}
        smoke: dict = {"ran": False, "passed": None, "why": "execution was not allowed"}

        if allow_execution and all(f["compiles"] for f in files):
            smoke = smoke_run(box, contract, timeout=min(timeout, SMOKE_TIMEOUT))

            answer = completer(TEST_PROMPT.format(rules=rules, plan=plan_text,
                                                 invocation=contract.invocation,
                                                 must_answer=contract.must_answer, entry=contract.entry),
                               task="app_builder.tests")
            test_code = code_from(answer.get("text") or "") if answer.get("ok") else ""
            # The tests are bound by the SAME contract as the files, and for the same reason: left free,
            # they called names the plan never promised and produced four failures that read as app bugs.
            def test_problem_in(code: str) -> str:
                checked = static_check(code)
                if not checked["ok"]:
                    return checked["error"]
                reaching = unpromised_imports(code, plan)
                if reaching:
                    return ("the tests reach names no file promised: " + "; ".join(reaching)
                            + ". Test only what the plan above says each file defines.")
                return ""

            test_problem = test_problem_in(test_code) if test_code else "no test code came back"
            test_attempts = 1
            while test_problem and test_attempts <= max_repairs and test_code:
                repaired = completer(REPAIR_PROMPT.format(path="test_app_rules.py",
                                                         error=test_problem, rules=rules,
                                                         invocation=contract.invocation,
                                                         must_answer=contract.must_answer,
                                                         modules=modules, code=test_code,
                                                         exports="nothing - this file only tests"),
                                     task="app_builder.test_repair")
                if not repaired.get("ok"):
                    break
                test_code = code_from(repaired.get("text") or "")
                test_problem = test_problem_in(test_code) if test_code else "no test code came back"
                test_attempts += 1
            test_static = static_check(test_code)
            tests = {"generated": bool(test_code), "compiles": test_static["ok"],
                     "attempts": test_attempts, "contract_ok": not test_problem,
                     "error": (test_problem or "")[:300], "ran": False, "passed": None}
            # Run them only if they both compile AND stay inside the contract. Running tests that reach
            # unpromised names would produce failures that read as app bugs, which is worse than saying
            # plainly that no test of the rules was adjudicated.
            if test_code and not test_problem:
                test_file = box / "test_app_rules.py"
                test_file.write_text(test_code[:MAX_ARTEFACT_BYTES], encoding="utf-8")
                vetted = safe_command(f"python -m pytest -q {test_file.name}", box, APP_ISOLATION)
                if vetted["ok"]:
                    result = run_checked(vetted["argv"], box, timeout=timeout)
                    tests.update({"ran": True, "passed": bool(result["ok"]),
                                  "output": (result["output"] or "")[-1200:]})
                else:
                    tests["error"] = vetted["reason"][:300]

        decided = app_verdict(files, tests, smoke, executed=bool(allow_execution))
        result = {"at": stamp, "request": request, "title": title, "kind": kind,
                  "contract": {"entry": contract.entry, "invocation": contract.invocation,
                               "must_answer": contract.must_answer},
                  "spec": spec.to_dict(), "plan": plan, "files": files,
                  "files_written": sum(1 for f in files if f["compiles"]),
                  "files_planned": len(plan), "tests": tests, "smoke": smoke,
                  "executed": bool(allow_execution), "sandbox": str(box) if keep_sandbox else None,
                  "places_orders": False, **decided}
        return record_app_build(result, record)
    finally:
        if not keep_sandbox:
            shutil.rmtree(box, ignore_errors=True)


def app_build_report(path: Optional[Path] = None, limit: int = 500) -> dict:
    """What this has built, counted by VERDICT.

    Counting builds would flatter it: "11 apps built" is meaningless when `verified` and `failed` are in the
    same total. Only the split says anything.
    """
    target = Path(path) if path else app_builds_path()
    if not target.exists():
        return {"available": True, "builds": 0, "verdicts": {}, "last": None,
                "note": "nothing built yet - this step is capability, not history"}
    verdicts: dict = {}
    rows: list = []
    try:
        for line in target.read_text(encoding="utf-8").splitlines()[-limit:]:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            rows.append(row)
            key = str(row.get("verdict") or "?")
            verdicts[key] = verdicts.get(key, 0) + 1
    except OSError as exc:
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}"}
    return {"available": True, "builds": len(rows), "verdicts": verdicts,
            "verified": verdicts.get("verified", 0), "last": rows[-1] if rows else None,
            "kinds": sorted({str(r.get("kind")) for r in rows if r.get("kind")})}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Build a whole application from a request. Never trades.")
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan", help="spec and file plan only; writes nothing")
    plan.add_argument("request")
    plan.add_argument("--kind", default="cli", choices=("cli", "web"))
    plan.add_argument("--title", default="")
    build = sub.add_parser("build", help="build it, and with --execute prove it starts")
    build.add_argument("request")
    build.add_argument("--kind", default="cli", choices=("cli", "web"))
    build.add_argument("--title", default="")
    build.add_argument("--execute", action="store_true", help="run the tests and the smoke run")
    build.add_argument("--keep", action="store_true", help="keep the sandbox folder and print its path")
    sub.add_parser("report", help="what has been built, by verdict")
    args = parser.parse_args(argv)

    if args.command == "report":
        print(json.dumps(app_build_report(), indent=1, default=str))
        return 0

    if args.command == "plan":
        from .ai_provider import complete
        from .build_exec import rules_text
        from .builder import draft_spec

        contract = contract_for(args.kind)
        drafted = draft_spec(args.request, family="software", title=args.title, completer=complete)
        if not drafted["ok"]:
            print("no spec: " + str(drafted["error"])[:300])
            return 1
        answer = complete(PLAN_PROMPT.format(request=args.request, rules=rules_text(drafted["spec"]),
                                             invocation=contract.invocation,
                                             must_answer=contract.must_answer, max_files=MAX_FILES,
                                             entry=contract.entry), task="app_builder.plan")
        planned = parse_file_plan(answer.get("text") or "", contract)
        print(json.dumps({"rules": len(drafted["spec"].rules), "plan": planned}, indent=1))
        return 0 if planned["ok"] else 1

    result = build_app(args.request, title=args.title, kind=args.kind, allow_execution=args.execute,
                       keep_sandbox=args.keep)
    print(json.dumps({k: v for k, v in result.items() if k != "spec"}, indent=1, default=str))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
