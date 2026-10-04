"""A stored candidate must be scoreable no matter which entry point is running.

THE DEFECT THIS PINS DOWN
-------------------------
`strategy_lab.strategy_orders` dispatches to `ORDER_BUILDERS` first and falls back to `SIGNALS`. But a
builder only reaches `ORDER_BUILDERS` when its own module is imported, so the same stored candidate
was scoreable or not depending purely on what the entry point happened to import.

`src/crt_forward.py` knew this and carries an explicit
``from . import aurum_flow_lab  # registers the trendline-break order builder``.
`src/strategy_book.py` imports none of them. So `python -m src.strategy_book` ran with an EMPTY
`ORDER_BUILDERS`, every stored candidate from those families fell through to the `SIGNALS` lookup, and
the hourly book update died on ``KeyError: 'trendline_break'``. That traceback appears 297 times in
`data/strategy_lab/strategy_lab.log` and was still killing the 07:20, 08:20 and 09:20 runs on
4 October 2026.

The families were never missing. They are implemented, they have parameter grids in `FAMILIES`, they
have descriptions in `describe_spec`, and one of them has a live paper forward test. Skipping them,
which is what the 2 October guard did by checking only `SIGNALS`, would have turned a loud crash into
a silent gap and left fourteen real families keeping a stale score for ever.

WHY THE MAP IS READ OUT OF THE SOURCE
-------------------------------------
`FAMILY_MODULES` is written by hand, so it can drift the moment somebody adds a family and forgets it.
These tests parse the source of every module under `src/` and fail if a module-level registration has
no map entry, which is the only way to keep a hand-written map honest.
"""
import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import strategy_lab as lab

SRC = ROOT / "src"


def _module_level_registrations():
    """{family: module stem} for every ``ORDER_BUILDERS[...] = ...`` at module level under src/.

    Module level only. A registration inside a function body does not run on import, so importing
    that module would register nothing and the map must not claim otherwise.
    """
    found = {}
    unresolved = []
    for path in sorted(SRC.glob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:                       # pragma: no cover - a broken file is its own failure
            continue
        constants = {t.id: n.value.value
                     for n in tree.body if isinstance(n, ast.Assign)
                     for t in n.targets
                     if isinstance(t, ast.Name) and isinstance(n.value, ast.Constant)
                     and isinstance(n.value.value, str)}
        for node in tree.body:                    # tree.body only == module level
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if not isinstance(target, ast.Subscript):
                    continue
                owner = target.value
                name = (owner.attr if isinstance(owner, ast.Attribute)
                        else owner.id if isinstance(owner, ast.Name) else None)
                if name != "ORDER_BUILDERS":
                    continue
                key = target.slice
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    found[key.value] = path.stem
                elif isinstance(key, ast.Name) and key.id in constants:
                    found[constants[key.id]] = path.stem   # e.g. vtb_lab's FAMILY = "volatility_breakout"
                else:
                    unresolved.append(f"{path.name}:{node.lineno}")
    return found, unresolved


def test_every_module_level_family_is_in_the_map():
    """The drift guard. Add a family, forget the map, and this fails."""
    found, unresolved = _module_level_registrations()
    assert not unresolved, f"could not read the family name at: {unresolved}"
    assert found, "no registrations found at all, which means this test stopped working"
    missing = {fam: mod for fam, mod in found.items() if fam not in lab.FAMILY_MODULES}
    assert not missing, (
        f"these families register a builder at module level but are not in "
        f"strategy_lab.FAMILY_MODULES, so any entry point that does not import them will crash: {missing}")


def test_the_map_points_at_the_module_that_really_registers_each_family():
    found, _ = _module_level_registrations()
    wrong = {fam: (lab.FAMILY_MODULES[fam], mod) for fam, mod in found.items()
             if lab.FAMILY_MODULES.get(fam) != mod}
    assert not wrong, f"map says (mapped, actual): {wrong}"


def test_the_map_claims_nothing_it_cannot_deliver():
    """Every mapped module must exist and must really register its family on import."""
    found, _ = _module_level_registrations()
    for family, module in lab.FAMILY_MODULES.items():
        assert (SRC / f"{module}.py").exists(), f"{family} maps to src/{module}.py, which is not there"
        assert found.get(family) == module, (
            f"{family} is mapped to {module} but that module does not register it at module level")


@pytest.mark.parametrize("family", sorted(lab.FAMILY_MODULES))
def test_ensure_family_resolves_every_mapped_family(family):
    """The actual repair: the family is available even though nothing imported its module."""
    assert lab.ensure_family(family) is True
    assert family in lab.ORDER_BUILDERS or family in lab.SIGNALS


def test_trendline_break_is_the_case_that_was_crashing():
    """Named explicitly, because this is the one that killed the hourly update for days."""
    assert "trendline_break" not in lab.SIGNALS, "it never was a SIGNALS family; that was the confusion"
    assert lab.ensure_family("trendline_break") is True
    assert "trendline_break" in lab.ORDER_BUILDERS
    assert callable(lab.ORDER_BUILDERS["trendline_break"])


def test_the_builtin_signal_families_still_resolve_without_any_import():
    for family in sorted(lab.SIGNALS):
        assert lab.ensure_family(family) is True


def test_a_genuinely_unknown_family_is_false_not_an_exception():
    """The book's skip path depends on this answering rather than raising."""
    assert lab.ensure_family("no_such_family_anywhere") is False
    assert lab.ensure_family("") is False
    assert lab.ensure_family(None) is False


def test_families_registered_inside_a_function_are_honestly_unresolvable():
    """fvg_retest, poi_liquidity, tokyo_breakout and direction_sweep register inside a function body.

    Importing their module registers nothing, so ensure_family cannot honestly return True for them.
    They are the real case for the book's skip path, and the map must not pretend otherwise.
    """
    for family in ("fvg_retest", "poi_liquidity", "tokyo_breakout"):
        assert family not in lab.FAMILY_MODULES, f"{family} registers in a function; the map must not claim it"


def test_a_missing_module_is_tried_once_and_not_on_every_candidate(monkeypatch):
    """A broken module must not be re-imported for every candidate in a 2,500 candidate sweep."""
    monkeypatch.setitem(lab.FAMILY_MODULES, "ghost_family", "module_that_does_not_exist")
    lab._FAMILY_MODULES_TRIED.discard("module_that_does_not_exist")
    calls = []
    import importlib

    real = importlib.import_module

    def counting(name, *a, **k):
        calls.append(name)
        return real(name, *a, **k)

    monkeypatch.setattr(importlib, "import_module", counting)
    assert lab.ensure_family("ghost_family") is False
    assert lab.ensure_family("ghost_family") is False
    assert lab.ensure_family("ghost_family") is False
    assert len([c for c in calls if "module_that_does_not_exist" in c]) == 1, "imported more than once"
