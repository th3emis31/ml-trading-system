"""Executing model-written code is the riskiest thing this project does, so the boundary is tested hard.

The rest of the system can only place an order through MetaTrader, and the guard that matters is that
generated code cannot reach it: the repository must not be importable, the working directory must not be
the repository, and no command outside a small allowlist may run at all.

These tests do not execute any generated code. They test the gate.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from src.build_exec import (DEFAULT_TIMEOUT, MAX_ARTEFACT_BYTES, code_from, safe_command,
                            sandbox_env, static_check)


@pytest.fixture
def box(tmp_path):
    return tmp_path


# --- the allowlist -------------------------------------------------------------------------------

def test_only_python_may_run(box):
    for command in ("bash -c 'rm -rf /'", "cmd /c del *.*", "powershell -Command Remove-Item",
                    "curl http://example.com", "pip install requests"):
        out = safe_command(command, box)
        assert out["ok"] is False, f"{command!r} must be refused"


def test_inline_code_is_refused_outright(box):
    """`python -c` is unbounded by construction: no path check can constrain what it does."""
    out = safe_command('python -c "import shutil; shutil.rmtree(\'C:/\')"', box)
    assert out["ok"] is False and "-c" in out["reason"]


def test_a_path_outside_the_sandbox_is_refused(box):
    for target in ("../../app.py", "../../../Windows/System32", "C:/Users/th_em/ml_trading_system/app.py"):
        out = safe_command(f"python -m pytest {target}", box)
        assert out["ok"] is False, f"{target!r} must be refused"
        assert "outside the sandbox" in out["reason"]


def test_an_unknown_flag_is_refused_but_output_flags_are_allowed(box):
    assert safe_command("python -m pytest -q test_x.py", box)["ok"] is True
    assert safe_command("python -m pytest --tb=short test_x.py", box)["ok"] is True
    # A flag that changes behaviour rather than output is not on the list.
    for bad in ("--cov", "-p no:cacheprovider --boom", "--pdb"):
        assert safe_command(f"python -m pytest {bad} test_x.py", box)["ok"] is False


def test_an_allowed_command_runs_the_isolated_interpreter(box):
    out = safe_command("python -m pytest -q test_x.py", box)
    assert out["ok"] is True
    # -I isolates: no user site-packages, no PYTHONPATH, and the cwd is not put on sys.path.
    assert out["argv"][1] == "-I", "the interpreter must be isolated"
    assert out["argv"][0].lower().endswith(("python.exe", "python")), "our interpreter, not the model's"


def test_a_malformed_command_is_refused_rather_than_guessed_at(box):
    assert safe_command('python -m pytest "unclosed', box)["ok"] is False
    assert safe_command("", box)["ok"] is False
    assert safe_command("python", box)["ok"] is False


# --- the environment -----------------------------------------------------------------------------

def test_the_repository_is_not_importable_by_generated_code():
    """The guard that matters most here: generated code must not be able to import trading modules."""
    env = sandbox_env()
    assert env.get("PYTHONPATH") == "", "an inherited PYTHONPATH could put the repo on sys.path"


def test_no_inherited_variable_can_leak_a_secret():
    env = sandbox_env()
    suspicious = [name for name in env
                  if any(word in name.upper() for word in ("KEY", "TOKEN", "SECRET", "PASS", "MT5", "MT4"))]
    assert suspicious == [], f"these would be visible to generated code: {suspicious}"
    # And it is a built set, not os.environ with deletions - so a new variable cannot appear by default.
    assert len(env) <= 12, f"the environment should be minimal, got {sorted(env)}"


# --- static analysis, which happens before anything runs -----------------------------------------

def test_broken_code_is_caught_without_being_executed():
    out = static_check("def f(:\n    pass")
    assert out["ok"] is False and out["stage"] == "compile" and "SyntaxError" in out["error"]


def test_an_empty_answer_is_not_treated_as_a_valid_artefact():
    assert static_check("")["ok"] is False
    assert static_check("   \n  ")["ok"] is False


def test_an_oversized_artefact_is_refused():
    out = static_check("x = 1\n" * (MAX_ARTEFACT_BYTES // 3))
    assert out["ok"] is False and out["stage"] == "size"


def test_good_code_passes_static_check():
    assert static_check("def clamp(v, lo, hi):\n    return max(lo, min(v, hi))")["ok"] is True


# --- pulling code out of a model answer ----------------------------------------------------------

def test_a_fenced_block_wins_over_surrounding_chatter():
    answer = "Sure, here you go:\n```python\ndef f():\n    return 1\n```\nHope that helps!"
    assert code_from(answer) == "def f():\n    return 1"


def test_an_unfenced_answer_is_still_tried_but_must_compile():
    assert code_from("def f():\n    return 1") == "def f():\n    return 1"
    # Prose with no code fails the static check rather than being written out as an artefact.
    assert static_check(code_from("I would be happy to help with that!"))["ok"] is False


def test_the_timeout_is_bounded():
    assert 0 < DEFAULT_TIMEOUT <= 600
