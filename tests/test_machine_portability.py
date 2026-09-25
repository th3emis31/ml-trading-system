"""The system must be movable: nothing it needs may be written into the source.

Build map step 9. Two properties are worth a test because both failed silently in the past:

* a path that names THIS machine's user profile or MetaTrader install cannot be in the code, or the
  copy on the USB drive is a backup rather than a working system;
* a configured path with forward slashes must still match what Windows reports with backslashes -
  comparing those as plain strings made the doctor say a running terminal was missing.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from src.runtime_paths import (DEFAULT_MACHINE, HOME_ENV, installed_terminal, machine_config,
                               machine_config_path, machine_report, same_path,
                               smartentry_data_dir, smartentry_models_dir, terminal_data_dir)

ROOT = Path(__file__).resolve().parents[1]
# A user profile or an absolute MetaTrader install in the code is what stops the system moving.
#
# The pattern carries NO backslash on purpose, and every line is flattened before it is matched.
# The first version of this test used a `[\\/]` character class and one backslash was lost on the way
# into the file, leaving `[\/]` - a class matching forward slash only. It therefore could not match a
# single Windows path and passed while four hard-coded paths sat in the code. A guard that cannot fail
# is worse than no guard, because it is reported as protection. Normalising the separator first means
# the pattern needs no escaping at all and cannot break the same way twice.
FORBIDDEN = re.compile(r"C:/+Users/+th_em|C:/+Program Files/+MetaTrader", re.IGNORECASE)


def _flatten_separators(line: str) -> str:
    """Every way a Windows path can be spelled in source, reduced to forward slashes.

    Source may hold a raw string (one backslash), an escaped string (two), or a forward-slash path.
    All three name the same file, so all three must be caught.
    """
    return line.replace("\\\\", "/").replace("\\", "/")
# Files allowed to name them: the config layer's own defaults, and the backup script, which is about
# THIS machine's disks by definition.
ALLOWED = {"src/runtime_paths.py", "scripts/backup_to_usb.ps1"}


def _sources():
    for folder in ("src", "scripts", "trading"):
        for path in (ROOT / folder).rglob("*.py"):
            yield path
    for path in (ROOT / "scripts").rglob("*.ps1"):
        yield path


def test_no_machine_specific_path_is_written_into_the_code():
    offenders = []
    for path in _sources():
        rel = path.relative_to(ROOT).as_posix()
        if rel in ALLOWED:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for number, line in enumerate(text.splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue          # a comment naming a path is documentation, not a dependency
            if FORBIDDEN.search(_flatten_separators(line)):
                offenders.append(f"{rel}:{number}: {line.strip()[:110]}")
    assert not offenders, ("machine-specific paths belong in config/machine.json, not in the code:\n"
                           + "\n".join(offenders))


def test_the_guard_itself_can_actually_fail():
    """The test above is only worth having if it CAN fail. It could not: a lost backslash left its
    pattern matching forward slashes only, so it passed over four real hard-coded paths. This pins the
    three spellings a Windows path takes in source, so the guard can never go quietly blind again."""
    assert FORBIDDEN.search(_flatten_separators(r'X = r"C:\Users\th_em\Desktop\book.xlsx"'))
    assert FORBIDDEN.search(_flatten_separators('X = "C:\\\\Users\\\\th_em\\\\AppData"'))
    assert FORBIDDEN.search(_flatten_separators('X = "C:/Users/th_em/AppData"'))
    assert FORBIDDEN.search(_flatten_separators(r'X = r"C:\Program Files\MetaTrader 5\terminal64.exe"'))
    # and it must not fire on paths that name no particular machine
    assert not FORBIDDEN.search(_flatten_separators(r'X = r"C:\Program Files\Python310"'))
    assert not FORBIDDEN.search(_flatten_separators('X = smartentry_data_dir() / "state.json"'))


def test_forward_and_back_slashes_name_the_same_file():
    # The exact failure this guards: config writes "/" and Win32_Process reports "\".
    assert same_path("C:/Users/x/MetaTrader/terminal64.exe", r"C:\Users\x\MetaTrader\terminal64.exe")
    assert same_path("C:/A/b.EXE", r"c:\a\B.exe")                  # Windows ignores case
    assert not same_path("C:/A/b.exe", "C:/A/c.exe")
    assert not same_path("", "C:/A/b.exe") and not same_path("C:/A/b.exe", None)


def test_every_terminal_the_doctor_requires_has_a_configured_path():
    from src.system_doctor import REQUIRED_TERMINALS_BY_NAME, required_terminals

    for name in REQUIRED_TERMINALS_BY_NAME:
        assert installed_terminal(name), f"{name} has no path in the machine config"
    assert len(required_terminals()) == len(REQUIRED_TERMINALS_BY_NAME)


def test_a_missing_config_file_changes_nothing(monkeypatch, tmp_path):
    # The defaults ARE this machine's values, so an absent or unreadable file must be harmless.
    monkeypatch.setenv(HOME_ENV, str(tmp_path))
    assert not machine_config_path().exists()
    assert machine_config()["terminals"] == DEFAULT_MACHINE["terminals"]
    assert installed_terminal("mt5_strategies") == DEFAULT_MACHINE["terminals"]["mt5_strategies"]


def test_a_config_file_overrides_only_what_it_names(monkeypatch, tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "machine.json").write_text(
        json.dumps({"terminals": {"mt4_bridge": "D:/MT4/terminal.exe"}}), encoding="utf-8")
    monkeypatch.setenv(HOME_ENV, str(tmp_path))
    assert installed_terminal("mt4_bridge") == "D:/MT4/terminal.exe"          # replaced
    assert installed_terminal("mt5_panel") == DEFAULT_MACHINE["terminals"]["mt5_panel"]   # kept
    assert terminal_data_dir("mt5_tester")                                    # untouched section


def test_unreadable_config_falls_back_instead_of_raising(monkeypatch, tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "machine.json").write_text("{ this is not json", encoding="utf-8")
    monkeypatch.setenv(HOME_ENV, str(tmp_path))
    assert machine_config()["terminals"] == DEFAULT_MACHINE["terminals"]


def test_home_moves_the_owned_folders_but_only_when_set(monkeypatch, tmp_path):
    for name in (HOME_ENV, "SMARTENTRY_MODELS_DIR", "SMARTENTRY_DATA_DIR"):
        monkeypatch.delenv(name, raising=False)
    assert smartentry_models_dir() == Path("models")       # unchanged default: opt in, never surprise
    assert smartentry_data_dir() == Path("data")

    monkeypatch.setenv(HOME_ENV, str(tmp_path))
    assert smartentry_models_dir() == tmp_path / "models"
    assert smartentry_data_dir() == tmp_path / "data"

    monkeypatch.setenv("SMARTENTRY_MODELS_DIR", str(tmp_path / "sandbox"))
    assert smartentry_models_dir() == tmp_path / "sandbox"  # the test suite's own redirect still wins


def test_report_says_what_is_missing_rather_than_guessing(monkeypatch, tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "machine.json").write_text(
        json.dumps({"terminals": {"mt5_panel": "Z:/nowhere/terminal64.exe"}}), encoding="utf-8")
    monkeypatch.setenv(HOME_ENV, str(tmp_path))
    report = machine_report()
    assert report["home"] == str(tmp_path)
    assert report["terminals"]["mt5_panel"] == {"path": "Z:/nowhere/terminal64.exe", "exists": False}
    assert "mt5_panel" in report["missing"]
