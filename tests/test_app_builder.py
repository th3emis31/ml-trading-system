"""Building a whole application is only a capability if the thing it produces actually STARTS.

The failure this module exists to prevent is a build that compiles, passes its unit tests, and dies on
launch - which a test-only check certifies as working. So the tests here are mostly about the smoke run and
about the verdict refusing to say `verified` without one.

The end-to-end tests use a SCRIPTED model: a fake completer that returns a fixed spec, a fixed file plan and
real hand-written files. That keeps them deterministic and free, and it still exercises every part this
module owns - path vetting, the repair loop, the smoke run, the pytest run and the verdict.
"""
from __future__ import annotations

import json
import socket
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import app_builder as ab

SPEC_TEXT = """TITLE: Reading list
R1 | the app stores a title and returns the count | run: python app.py --selftest
R2 | an empty title is refused | run: python app.py --selftest
R3 | the count starts at zero | run: python app.py --selftest
"""

CLI_APP = '''"""A reading list, scripted by the test rather than by a model."""
import argparse

from store import Store


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--add", default="")
    args = parser.parse_args(argv)
    store = Store()
    if args.selftest:
        assert store.count() == 0
        store.add("Dune")
        assert store.count() == 1
        try:
            store.add("")
        except ValueError:
            pass
        else:
            raise AssertionError("an empty title must be refused")
        print("OK: 3 rules exercised")
        return 0
    if args.add:
        store.add(args.add)
    print(store.count())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''

STORE_FILE = '''class Store:
    """Titles in memory. R2: an empty title is refused. R3: the count starts at zero."""

    def __init__(self):
        self._titles = []

    def add(self, title):
        if not str(title).strip():
            raise ValueError("a title is required")
        self._titles.append(str(title).strip())
        return len(self._titles)

    def count(self):
        return len(self._titles)
'''

RULE_TESTS = '''from store import Store


def test_the_count_starts_at_zero():
    assert Store().count() == 0


def test_an_empty_title_is_refused():
    import pytest

    with pytest.raises(ValueError):
        Store().add("   ")


def test_adding_a_title_raises_the_count():
    store = Store()
    store.add("Dune")
    assert store.count() == 1
'''

WEB_APP = '''import argparse
import json
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"ok": True, "path": self.path}).encode("utf-8")
        self.send_response(200 if self.path == "/health" else 404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args(argv)
    HTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''

# The plan declares each file's interface, because the builder now checks it in both directions: a
# file must define everything it promised, and may import nothing that was not promised to it.
PLAN_JSON = json.dumps([{"path": "app.py", "purpose": "entry point", "exports": ["main"]},
                        {"path": "store.py", "purpose": "the titles", "exports": ["Store"]}])


def scripted(*, app_code: str = CLI_APP, store_code: str = STORE_FILE, tests: str = RULE_TESTS,
             plan: str = PLAN_JSON, spec: str = SPEC_TEXT, repair_code: str = STORE_FILE):
    """A completer that answers each prompt by task name. Records what it was asked, so prompts can be
    checked for carrying the contract."""
    asked: list = []
    contract_entry = ab.contract_for("cli").entry

    def complete(prompt, task="", **kwargs):
        asked.append({"task": task, "prompt": prompt})
        if task == "builder.spec":
            return {"ok": True, "text": spec, "provider": "scripted"}
        if task == "app_builder.plan":
            return {"ok": True, "text": plan, "provider": "scripted"}
        if task == "app_builder.tests":
            return {"ok": True, "text": f"```python\n{tests}```", "provider": "scripted"}
        if task == "app_builder.file":
            # Read the file being asked for from the THE FILE header. An earlier version of this fixture
            # matched "app.py -" anywhere in the prompt, which the CLI invocation ("python app.py
            # --selftest") also contains - so every file was handed the entry point's code and the fixture
            # quietly lied about which file it was answering for.
            asked_for = prompt.split("THE FILE", 1)[1].strip().split()[0]
            body = app_code if asked_for == contract_entry else store_code
            return {"ok": True, "text": f"```python\n{body}```", "provider": "scripted"}
        if task == "app_builder.repair":
            return {"ok": True, "text": f"```python\n{repair_code}```", "provider": "scripted"}
        return {"ok": False, "error": f"unscripted task {task!r}"}

    complete.asked = asked
    return complete


# --- the contract is ours ------------------------------------------------------------------------

def test_the_contract_names_the_entry_point_and_how_success_is_judged():
    for kind in ("cli", "web"):
        contract = ab.contract_for(kind)
        assert contract.entry == "app.py"
        assert contract.invocation and contract.must_answer


def test_a_kind_whose_success_cannot_be_checked_is_refused():
    with pytest.raises(ValueError):
        ab.contract_for("gui")


def test_every_prompt_carries_the_same_invocation(tmp_path):
    complete = scripted()
    ab.build_app("a reading list", title="Reading list", completer=complete, allow_execution=False,
                 record=False)
    contract = ab.contract_for("cli")
    for row in complete.asked:
        if row["task"] in ("app_builder.plan", "app_builder.file"):
            assert contract.invocation in row["prompt"], f"{row['task']} did not state the invocation"


# --- path vetting, before a byte is written ------------------------------------------------------

def test_a_path_outside_the_folder_is_refused():
    contract = ab.contract_for("cli")
    for path in ("../app.py", "/etc/passwd", "C:/Windows/system.py", "a/b/c.py", "app.py.exe",
                 "App.PY", "../../src/app.py"):
        plan = json.dumps([{"path": path, "purpose": "x"}])
        out = ab.parse_file_plan(plan, contract)
        assert out["ok"] is False, f"{path!r} must be refused"


def test_one_subfolder_is_allowed_and_deeper_is_not():
    contract = ab.contract_for("cli")
    ok = ab.parse_file_plan(json.dumps([{"path": "app.py", "purpose": "e"},
                                        {"path": "core/store.py", "purpose": "s",
                                         "exports": ["Store"]}]), contract)
    assert ok["ok"] is True and len(ok["files"]) == 2
    deep = ab.parse_file_plan(json.dumps([{"path": "a/b/c.py", "purpose": "s"}]), contract)
    assert deep["ok"] is False


def test_the_entry_point_is_supplied_when_the_plan_forgets_it_and_is_written_first():
    contract = ab.contract_for("cli")
    out = ab.parse_file_plan(json.dumps([{"path": "store.py", "purpose": "s", "exports": ["Store"]}]),
                             contract)
    assert out["ok"] is True
    assert out["files"][0]["path"] == contract.entry, "the entry point must be written first"


def test_a_plan_that_is_not_json_is_refused_rather_than_guessed():
    contract = ab.contract_for("cli")
    for text in ("", "I will create app.py and store.py", "{}", "[]"):
        assert ab.parse_file_plan(text, contract)["ok"] is False


def test_the_plan_is_capped():
    contract = ab.contract_for("cli")
    plan = json.dumps([{"path": f"file{i}.py", "purpose": "x"} for i in range(40)])
    out = ab.parse_file_plan(plan, contract, max_files=3)
    assert len(out["files"]) <= 3


# --- the verdict --------------------------------------------------------------------------------

GOOD_FILES = [{"path": "app.py", "compiles": True}, {"path": "store.py", "compiles": True}]
PASSED_TESTS = {"generated": True, "ran": True, "passed": True}
PASSED_SMOKE = {"ran": True, "passed": True}


def test_a_file_that_does_not_compile_fails_the_build():
    out = ab.app_verdict([{"path": "app.py", "compiles": False}], PASSED_TESTS, PASSED_SMOKE, True)
    assert out["verdict"] == "failed" and "app.py" in out["why"]


def test_compiling_is_never_reported_as_working():
    """The exact claim this module refuses to make: static checks alone cap the verdict at partial."""
    out = ab.app_verdict(GOOD_FILES, PASSED_TESTS, PASSED_SMOKE, executed=False)
    assert out["verdict"] == "partial" and out["ok"] is False
    assert "nothing was run" in out["why"]


def test_an_app_that_does_not_answer_fails_even_if_its_tests_pass():
    smoke = {"ran": True, "passed": False, "why": "nothing was listening"}
    out = ab.app_verdict(GOOD_FILES, PASSED_TESTS, smoke, True)
    assert out["verdict"] == "failed" and "did not answer" in out["why"]


def test_an_app_that_starts_but_fails_its_rules_is_partial_not_verified():
    tests = {"generated": True, "ran": True, "passed": False}
    out = ab.app_verdict(GOOD_FILES, tests, PASSED_SMOKE, True)
    assert out["verdict"] == "partial" and "tests fail" in out["why"]


def test_starting_without_any_rule_test_is_partial():
    out = ab.app_verdict(GOOD_FILES, {"generated": False, "ran": False}, PASSED_SMOKE, True)
    assert out["verdict"] == "partial" and "no test of its rules" in out["why"]


def test_verified_needs_the_whole_chain():
    out = ab.app_verdict(GOOD_FILES, PASSED_TESTS, PASSED_SMOKE, True)
    assert out["verdict"] == "verified" and out["ok"] is True


# --- the smoke run, on real hand-written apps ----------------------------------------------------

def test_a_cli_app_that_says_ok_passes_its_smoke_run(tmp_path):
    (tmp_path / "app.py").write_text(CLI_APP, encoding="utf-8")
    (tmp_path / "store.py").write_text(STORE_FILE, encoding="utf-8")
    out = ab.smoke_cli(tmp_path, ab.contract_for("cli"))
    assert out["ran"] is True and out["passed"] is True, out
    assert "OK:" in out["output"]


def test_a_cli_app_that_crashes_on_launch_fails_its_smoke_run(tmp_path):
    (tmp_path / "app.py").write_text("import missing_module_xyz\n", encoding="utf-8")
    out = ab.smoke_cli(tmp_path, ab.contract_for("cli"))
    assert out["ran"] is True and out["passed"] is False


def test_a_cli_app_that_exits_quietly_does_not_count_as_answering(tmp_path):
    """Exit 0 alone is not an answer; a program that prints nothing has proved nothing."""
    (tmp_path / "app.py").write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
    out = ab.smoke_cli(tmp_path, ab.contract_for("cli"))
    assert out["answered"] is False and out["passed"] is False


def test_a_web_app_that_serves_health_passes_and_is_killed_afterwards(tmp_path):
    (tmp_path / "app.py").write_text(WEB_APP, encoding="utf-8")
    out = ab.smoke_web(tmp_path, ab.contract_for("web"))
    assert out["ran"] is True and out["passed"] is True, out
    assert out["status"] == 200
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(1.0)
        assert probe.connect_ex(("127.0.0.1", out["port"])) != 0, "the app was left running"


def test_a_web_app_that_never_listens_fails_with_a_reason_and_leaves_nothing_behind(tmp_path):
    (tmp_path / "app.py").write_text("import sys\nsys.exit(3)\n", encoding="utf-8")
    out = ab.smoke_web(tmp_path, ab.contract_for("web"))
    assert out["passed"] is False and out["why"], out
    assert "exit" in out["why"] or "listening" in out["why"]


def test_the_port_is_local_only():
    port = ab.free_port()
    assert 1024 < port < 65536


# --- end to end ---------------------------------------------------------------------------------

def test_a_whole_app_is_built_verified_and_recorded(tmp_path, monkeypatch):
    record = tmp_path / "app_builds.jsonl"
    monkeypatch.setattr(ab, "app_builds_path", lambda: record)
    out = ab.build_app("a reading list", title="Reading list", kind="cli", completer=scripted(),
                       allow_execution=True)
    assert out["verdict"] == "verified", out.get("why") or out
    assert out["files_written"] == 2 and out["smoke"]["passed"] is True
    assert out["tests"]["ran"] is True and out["tests"]["passed"] is True
    assert out["places_orders"] is False
    rows = [json.loads(l) for l in record.read_text(encoding="utf-8").splitlines()]
    assert rows[-1]["verdict"] == "verified" and rows[-1]["kind"] == "cli"


def test_the_sandbox_is_removed_unless_it_is_asked_for(tmp_path, monkeypatch):
    monkeypatch.setattr(ab, "app_builds_path", lambda: tmp_path / "builds.jsonl")
    kept = ab.build_app("a reading list", title="t", completer=scripted(), allow_execution=False,
                        keep_sandbox=True)
    assert kept["sandbox"] and Path(kept["sandbox"]).exists()
    gone = ab.build_app("a reading list", title="t", completer=scripted(), allow_execution=False)
    assert gone["sandbox"] is None


def test_a_broken_file_is_repaired_and_the_attempts_are_recorded(tmp_path, monkeypatch):
    monkeypatch.setattr(ab, "app_builds_path", lambda: tmp_path / "builds.jsonl")
    out = ab.build_app("a reading list", title="t",
                       completer=scripted(store_code="class Store(   # broken\n"),
                       allow_execution=False)
    store = [f for f in out["files"] if f["path"] == "store.py"][0]
    assert store["attempts"] > 1, "a file that does not compile must be given a repair"
    assert store["compiles"] is True, "the scripted repair returns valid code"


def test_no_spec_means_no_build():
    """The rule the module builder established: without a spec there is nothing to build against."""
    out = ab.build_app("anything", completer=lambda *a, **k: {"ok": False, "error": "no provider"},
                       record=False)
    assert out["verdict"] == "no_spec" and out["ok"] is False


def test_a_refused_plan_stops_the_build_before_any_file_is_written():
    out = ab.build_app("anything", title="t", completer=scripted(plan="not json at all"), record=False)
    assert out["verdict"] == "no_plan" and out["ok"] is False


def test_app_builds_are_recorded_separately_from_module_builds(tmp_path, monkeypatch):
    """Mixing them would inflate the module builder's verdict counts on the build map page."""
    monkeypatch.setattr(ab, "app_builds_path", lambda: tmp_path / "app_builds.jsonl")
    ab.build_app("a reading list", title="t", completer=scripted(), allow_execution=False)
    assert (tmp_path / "app_builds.jsonl").exists()
    assert not (tmp_path / "builds.jsonl").exists()


def test_the_report_counts_verdicts_not_builds(tmp_path):
    path = tmp_path / "app_builds.jsonl"
    path.write_text("\n".join(json.dumps({"verdict": v, "kind": "cli"})
                              for v in ("verified", "failed", "partial", "verified")), encoding="utf-8")
    out = ab.app_build_report(path)
    assert out["builds"] == 4 and out["verified"] == 2
    assert out["verdicts"]["failed"] == 1


def test_the_report_says_so_when_nothing_has_been_built(tmp_path):
    out = ab.app_build_report(tmp_path / "nothing.jsonl")
    assert out["available"] is True and out["builds"] == 0


# --- the isolation the app builder trades for sibling imports -------------------------------------

def test_the_sandbox_is_importable_but_this_repository_is_not(tmp_path):
    """The one property loosened for app builds, so it gets its own test.

    App files must be able to import each other, which `python -I` forbids (it keeps the script's own
    directory off sys.path). The replacement, `-E -s`, must still leave THIS project unreachable: a
    generated app that could `import trading.mt5_service` would be one import from a live broker.
    """
    (tmp_path / "sibling.py").write_text("VALUE = 41\n", encoding="utf-8")
    (tmp_path / "app.py").write_text(
        "import sibling\n"
        "try:\n"
        "    import src.app_builder  # this repository\n"
        "except Exception as exc:\n"
        "    print('OK: sibling', sibling.VALUE + 1, 'and the repo is unreachable:', type(exc).__name__)\n"
        "else:\n"
        "    raise SystemExit('the repository was importable from the sandbox')\n",
        encoding="utf-8")
    out = ab.smoke_cli(tmp_path, ab.contract_for("cli"))
    assert out["passed"] is True, out
    assert "sibling 42" in out["output"], out["output"]
    assert "unreachable" in out["output"]


def test_the_app_builders_isolation_still_ignores_the_environment():
    """-E is what stops PYTHONPATH being used to smuggle a path in; losing it would undo the test above."""
    assert "-E" in ab.APP_ISOLATION and "-s" in ab.APP_ISOLATION


# --- the interface check: files that do not agree -------------------------------------------------

def test_a_declared_name_that_is_not_defined_is_found_without_running_anything():
    """The first real build failed exactly here: app.py called book_counter.test() and book_counter.py
    never defined `test`. Each file compiles on its own, so only an interface check can see it."""
    code = "import os\n\n\ndef add(title):\n    return title\n\n\nclass Store:\n    pass\n\n\nVERSION = 1\n"
    assert ab.missing_exports(code, ["add", "Store", "VERSION", "os"]) == []
    assert ab.missing_exports(code, ["test", "count"]) == ["test", "count"]


def test_a_name_defined_only_inside_a_function_does_not_count_as_exported():
    code = "def outer():\n    def inner():\n        return 1\n    return inner\n"
    assert ab.missing_exports(code, ["inner"]) == ["inner"]
    assert ab.missing_exports(code, ["outer"]) == []


def test_unparseable_code_reports_every_name_missing_and_does_not_raise():
    assert ab.missing_exports("def broken(", ["a", "b"]) == ["a", "b"]


def test_no_declared_exports_means_nothing_to_check():
    assert ab.missing_exports("x = 1\n", []) == []


def test_the_plan_carries_each_files_interface():
    contract = ab.contract_for("cli")
    plan = json.dumps([{"path": "app.py", "purpose": "entry", "exports": ["main"]},
                       {"path": "store.py", "purpose": "titles", "exports": ["Store", "bad name", "x" * 80]}])
    out = ab.parse_file_plan(plan, contract)
    assert out["ok"] is True
    store = [f for f in out["files"] if f["path"] == "store.py"][0]
    assert store["exports"] == ["Store"], "an unsafe name must be dropped, not passed to a prompt"


def test_a_missing_interface_is_a_different_verdict_message_from_a_syntax_error():
    files = [{"path": "app.py", "compiles": True},
             {"path": "store.py", "compiles": True, "missing_exports": ["count"]}]
    out = ab.app_verdict(files, PASSED_TESTS, PASSED_SMOKE, True)
    assert out["verdict"] == "failed"
    assert "do not agree on their interface" in out["why"] and "count" in out["why"]


def test_a_file_missing_its_declared_name_is_repaired_before_anything_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(ab, "app_builds_path", lambda: tmp_path / "builds.jsonl")
    plan = json.dumps([{"path": "app.py", "purpose": "entry", "exports": ["main"]},
                       {"path": "store.py", "purpose": "titles", "exports": ["Store", "count_titles"]}])
    # The first answer for store.py defines Store but not count_titles; the repair supplies both.
    first = "class Store:\n    pass\n"
    fixed = "class Store:\n    pass\n\n\ndef count_titles(titles):\n    return len(titles)\n"
    out = ab.build_app("a reading list", title="t",
                       completer=scripted(store_code=first, repair_code=fixed, plan=plan),
                       allow_execution=False)
    store = [f for f in out["files"] if f["path"] == "store.py"][0]
    assert store["attempts"] > 1, "a missing declared name must trigger a repair"
    assert store["missing_exports"] == [] and store["interface_ok"] is True


def test_every_file_prompt_names_the_interface_that_file_owes():
    plan = json.dumps([{"path": "app.py", "purpose": "entry", "exports": ["main"]},
                       {"path": "store.py", "purpose": "titles", "exports": ["Store"]}])
    complete = scripted(plan=plan)
    ab.build_app("a reading list", title="t", completer=complete, allow_execution=False, record=False)
    for row in complete.asked:
        if row["task"] == "app_builder.file":
            assert "THIS FILE MUST DEFINE" in row["prompt"]


# --- the contract reaches the TEST prompt too -----------------------------------------------------

def test_the_test_prompt_states_the_interface_instead_of_leaving_it_to_be_guessed():
    """The cloud build's generated tests contained a helper that tried argv three ways and then gave up,
    because this was the one prompt that never received the contract."""
    complete = scripted()
    ab.build_app("a reading list", title="t", completer=complete, allow_execution=True, record=False)
    asked = [r for r in complete.asked if r["task"] == "app_builder.tests"]
    assert asked, "the test prompt must be sent when execution is allowed"
    contract = ab.contract_for("cli")
    assert contract.invocation in asked[0]["prompt"]
    assert contract.entry in asked[0]["prompt"]
    assert "do not probe for it" in asked[0]["prompt"]


def test_the_cli_contract_asks_for_a_smoke_check_not_a_bundled_test_suite():
    """The cloud build read 'exercise its own main path' as 'embed a suite' and reported 29 tests with
    22 errors from inside --selftest, which is not an answer to anything."""
    answer = ab.contract_for("cli").must_answer
    assert "not a test suite" in answer and "pytest" in answer


def test_the_plan_prompt_asks_for_as_few_files_as_possible():
    complete = scripted()
    ab.build_app("a reading list", title="t", completer=complete, allow_execution=False, record=False)
    plan_prompt = [r for r in complete.asked if r["task"] == "app_builder.plan"][0]["prompt"]
    assert "As FEW files as the job needs" in plan_prompt


# --- imports: the rules that were stated but never checked ----------------------------------------

def test_a_module_that_imports_itself_is_caught():
    """The local model's second build wrote `from book_manager import BookManager` inside book_manager.py.
    It compiles, defines every declared name, and dies the moment anything loads it."""
    faults = ab.import_faults("from book_manager import BookManager\n", "book_manager.py",
                              ["app.py", "book_manager.py"])
    assert faults and "itself" in faults[0]


def test_a_third_party_import_is_caught_because_this_must_run_offline():
    """'Standard library only' was an instruction with nothing to enforce it. A build that imports requests
    is a build that fails on a machine with no internet - the one machine this has to work on."""
    for line in ("import requests\n", "from flask import Flask\n", "import numpy as np\n"):
        faults = ab.import_faults(line, "app.py", ["app.py", "store.py"])
        assert faults, f"{line.strip()!r} must be refused"
        assert "standard library" in faults[0]


def test_the_standard_library_and_the_apps_own_files_are_allowed():
    code = ("import json\nimport os.path\nfrom pathlib import Path\nfrom store import Store\n"
            "import argparse\n")
    assert ab.import_faults(code, "app.py", ["app.py", "store.py"]) == []


def test_a_relative_import_is_refused_because_these_files_are_not_a_package():
    faults = ab.import_faults("from .store import Store\n", "app.py", ["app.py", "store.py"])
    assert faults and "relative import" in faults[0]


def test_an_import_fault_is_its_own_verdict_message():
    files = [{"path": "app.py", "compiles": True},
             {"path": "store.py", "compiles": True, "import_faults": ["'requests' is neither"]}]
    out = ab.app_verdict(files, PASSED_TESTS, PASSED_SMOKE, True)
    assert out["verdict"] == "failed" and "imports that are not allowed" in out["why"]


def test_a_bad_import_is_repaired_before_anything_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(ab, "app_builds_path", lambda: tmp_path / "builds.jsonl")
    out = ab.build_app("a reading list", title="t",
                       completer=scripted(store_code="import requests\n\n\nclass Store:\n    pass\n",
                                          repair_code=STORE_FILE),
                       allow_execution=False)
    store = [f for f in out["files"] if f["path"] == "store.py"][0]
    assert store["attempts"] > 1 and store["import_faults"] == []


# --- the plan checked in the other direction ------------------------------------------------------

PLAN_WITH_EXPORTS = [{"path": "app.py", "purpose": "entry", "exports": ["main"]},
                     {"path": "store.py", "purpose": "titles", "exports": ["Store", "count_titles"]}]


def test_importing_a_name_the_plan_never_promised_is_caught():
    """The third real build died here: app.py did `from book_list_manager import read_input, count_books`
    and the plan for that file promised neither, so the export check had nothing to compare."""
    faults = ab.unpromised_imports("from store import Store, read_input\n", PLAN_WITH_EXPORTS)
    assert len(faults) == 1
    assert "read_input" in faults[0] and "Store" in faults[0], faults


def test_importing_only_promised_names_is_clean():
    assert ab.unpromised_imports("from store import Store, count_titles\nimport json\n",
                                 PLAN_WITH_EXPORTS) == []


def test_a_star_import_is_refused_because_it_hides_which_names_are_used():
    faults = ab.unpromised_imports("from store import *\n", PLAN_WITH_EXPORTS)
    assert faults and "hides which names" in faults[0]


def test_a_stdlib_import_is_not_judged_against_the_plan():
    assert ab.unpromised_imports("from pathlib import Path\nfrom json import dumps\n",
                                 PLAN_WITH_EXPORTS) == []


def test_an_unpromised_import_is_repaired_before_anything_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(ab, "app_builds_path", lambda: tmp_path / "builds.jsonl")
    plan = json.dumps([{"path": "app.py", "purpose": "entry", "exports": ["main"]},
                       {"path": "store.py", "purpose": "titles", "exports": ["Store"]}])
    broken_app = "from store import Store, read_input\n\n\ndef main():\n    return 0\n"
    fixed_app = "from store import Store\n\n\ndef main():\n    return 0\n"
    out = ab.build_app("a reading list", title="t",
                       completer=scripted(app_code=broken_app, repair_code=fixed_app, plan=plan),
                       allow_execution=False)
    entry = [f for f in out["files"] if f["path"] == "app.py"][0]
    assert entry["attempts"] > 1, "an unpromised import must trigger a repair"
    assert entry["import_faults"] == []


# --- a plan with no interface is not a plan -------------------------------------------------------

def test_a_plan_that_declares_no_interface_is_rejected():
    """The cloud model's plan omitted `exports` entirely, which silently switched off both agreement checks;
    the build then died on a name nothing had promised. Refuse here, with the clear message."""
    contract = ab.contract_for("cli")
    plan = json.dumps([{"path": "app.py", "purpose": "entry"}, {"path": "store.py", "purpose": "titles"}])
    out = ab.parse_file_plan(plan, contract)
    assert out["ok"] is False and "store.py" in out["why"]


def test_the_entry_point_alone_need_not_export_anything():
    contract = ab.contract_for("cli")
    out = ab.parse_file_plan(json.dumps([{"path": "app.py", "purpose": "entry"}]), contract)
    assert out["ok"] is True


def test_a_rejected_plan_is_retried_once_with_the_reason(tmp_path, monkeypatch):
    monkeypatch.setattr(ab, "app_builds_path", lambda: tmp_path / "builds.jsonl")
    bad = json.dumps([{"path": "app.py", "purpose": "entry"}, {"path": "store.py", "purpose": "titles"}])
    seen: list = []

    base = scripted()

    def complete(prompt, task="", **kwargs):
        seen.append(task)
        if task == "app_builder.plan":
            return {"ok": True, "text": bad, "provider": "scripted"}
        if task == "app_builder.plan_retry":
            assert "REJECTED" in prompt and "store.py" in prompt, "the retry must say what was wrong"
            return {"ok": True, "text": PLAN_JSON, "provider": "scripted"}
        return base(prompt, task=task, **kwargs)

    out = ab.build_app("a reading list", title="t", completer=complete, allow_execution=False)
    assert "app_builder.plan_retry" in seen
    assert out["files_planned"] == 2 and out["verdict"] == "partial", out.get("why")


def test_the_retry_happens_only_once(tmp_path, monkeypatch):
    monkeypatch.setattr(ab, "app_builds_path", lambda: tmp_path / "builds.jsonl")
    calls: list = []

    def always_bad(prompt, task="", **kwargs):
        calls.append(task)
        if task == "builder.spec":
            return {"ok": True, "text": SPEC_TEXT, "provider": "scripted"}
        return {"ok": True, "text": "not a plan at all", "provider": "scripted"}

    out = ab.build_app("a reading list", title="t", completer=always_bad)
    assert out["verdict"] == "no_plan"
    assert calls.count("app_builder.plan_retry") == 1, calls


# --- the tests get the same repair loop the app files get -----------------------------------------

def test_a_test_file_that_does_not_compile_is_repaired(tmp_path, monkeypatch):
    """The local model's first working build fell to `partial` for a reason that had nothing to do with the
    app: its test file came back unfenced, did not compile, and had no repair - so the only check of the
    RULES was thrown away."""
    monkeypatch.setattr(ab, "app_builds_path", lambda: tmp_path / "builds.jsonl")
    base = scripted()
    state = {"first": True}

    def complete(prompt, task="", **kwargs):
        if task == "app_builder.tests":
            return {"ok": True, "text": "Here are the tests:\ndef test_broken(:\n", "provider": "scripted"}
        if task == "app_builder.test_repair":
            state["first"] = False
            return {"ok": True, "text": f"```python\n{RULE_TESTS}```", "provider": "scripted"}
        return base(prompt, task=task, **kwargs)

    out = ab.build_app("a reading list", title="t", completer=complete, allow_execution=True)
    assert state["first"] is False, "a test file that does not compile must be repaired"
    assert out["tests"]["compiles"] is True and out["tests"]["ran"] is True
    assert out["verdict"] == "verified", out.get("why")


def test_each_file_record_carries_the_interface_it_was_held_to(tmp_path, monkeypatch):
    monkeypatch.setattr(ab, "app_builds_path", lambda: tmp_path / "builds.jsonl")
    out = ab.build_app("a reading list", title="t", completer=scripted(), allow_execution=False)
    store = [f for f in out["files"] if f["path"] == "store.py"][0]
    assert store["exports"] == ["Store"], "the report must say what the file was required to define"


# --- reaching a name through the module object ----------------------------------------------------

def test_using_a_module_attribute_nothing_promised_is_caught():
    """`import counter` then `counter.count_items(...)` reaches a name as surely as importing it, and that
    is how the generated tests produced four failures that read as app bugs and were not."""
    ok = ab.unpromised_imports("import store\n\n\ndef f():\n    return store.count_titles()\n",
                               PLAN_WITH_EXPORTS)
    assert ok == []
    bad = ab.unpromised_imports("import store\n\n\ndef f():\n    return store.load_all()\n",
                                PLAN_WITH_EXPORTS)
    assert bad and "store.load_all" in bad[0]


def test_an_aliased_import_is_followed():
    bad = ab.unpromised_imports("import store as s\n\n\ndef f():\n    return s.load_all()\n",
                                PLAN_WITH_EXPORTS)
    assert bad and "store.load_all" in bad[0]


def test_an_attribute_on_something_that_is_not_a_sibling_module_is_ignored():
    code = "import json\nfrom store import Store\n\n\ndef f():\n    return Store().anything()\n"
    assert ab.unpromised_imports(code, PLAN_WITH_EXPORTS) == []


def test_generated_tests_that_reach_unpromised_names_are_repaired(tmp_path, monkeypatch):
    monkeypatch.setattr(ab, "app_builds_path", lambda: tmp_path / "builds.jsonl")
    base = scripted()
    repaired = {"done": False}
    reaching = "```python\nimport store\n\n\ndef test_x():\n    assert store.load_all() == []\n```"

    def complete(prompt, task="", **kwargs):
        if task == "app_builder.tests":
            return {"ok": True, "text": reaching, "provider": "scripted"}
        if task == "app_builder.test_repair":
            assert "no file promised" in prompt, prompt[-300:]
            repaired["done"] = True
            return {"ok": True, "text": "```python\n" + RULE_TESTS + "```", "provider": "scripted"}
        return base(prompt, task=task, **kwargs)

    out = ab.build_app("a reading list", title="t", completer=complete, allow_execution=True)
    assert repaired["done"] is True
    assert out["tests"]["contract_ok"] is True and out["tests"]["passed"] is True
    assert out["verdict"] == "verified", out.get("why")


def test_tests_that_stay_outside_the_contract_are_not_run_at_all(tmp_path, monkeypatch):
    """Running them would blame the app for the test file's mistake."""
    monkeypatch.setattr(ab, "app_builds_path", lambda: tmp_path / "builds.jsonl")
    base = scripted()
    reaching = "```python\nimport store\n\n\ndef test_x():\n    assert store.load_all() == []\n```"

    def complete(prompt, task="", **kwargs):
        if task in ("app_builder.tests", "app_builder.test_repair"):
            return {"ok": True, "text": reaching, "provider": "scripted"}
        return base(prompt, task=task, **kwargs)

    out = ab.build_app("a reading list", title="t", completer=complete, allow_execution=True)
    assert out["tests"]["contract_ok"] is False and out["tests"]["ran"] is False
    assert out["smoke"]["passed"] is True, "the app itself was fine"
    assert out["verdict"] == "partial" and "no test of its rules" in out["why"]
