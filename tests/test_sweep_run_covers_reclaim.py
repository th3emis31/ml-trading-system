"""`sweep_reversal run` must execute every grid it declares, and count every one it executes.

WHY THIS EXISTS
---------------
`src/sweep_reversal.py` declares three readings of the owner's 4H manipulation candle: `reject`,
`continue`, and `reclaim`. `reclaim` is his own wording from 26 September 2026 - "manipulate lower
than previous low and close above the previous high for buy, and the same opposite for sell" - and it
has its own declared grid in `reclaim_variants()`.

Until 8 October 2026 `run()` iterated `sweep_variants()` alone. `reclaim_variants()` was declared in
that same module and never called by it.

The omission was invisible, and that is the part worth guarding. `n_trials_total` was computed from
exactly what was run (2 modes x 3 lookbacks x 2 body filters x 3 reward ratios = 36), so the report
was internally consistent and looked complete while leaving out the rule the module was written for.
A backtest was reported to the owner on that basis and he said, correctly, that it was wrong.

Two things therefore have to hold together, and a test for either one alone would have passed while
the bug was live:

  1. every declared grid is actually iterated, and
  2. the trial count covers every grid iterated, because a trial count that undercounts is worse than
     no trial count - it makes the deflated Sharpe look better than it is earned.

These tests never run a backtest. They inspect the declared grids and the source of `run()`.
"""
import inspect

import pytest

from src import sweep_reversal as sr


def test_the_three_declared_modes_are_all_reachable():
    assert set(sr.MODES) == {"reject", "continue"}
    assert sr.RECLAIM_MODE == "reclaim"
    assert sr.RECLAIM_MODE not in sr.MODES, \
        "reclaim is a separate grid by design, so MODES alone never describes everything declared"


def test_run_iterates_the_reclaim_grid_as_well_as_the_sweep_grid():
    """The exact regression: run() calling only sweep_variants()."""
    source = inspect.getsource(sr.run)
    assert "sweep_variants(" in source
    assert "reclaim_variants(" in source, \
        "run() does not iterate reclaim_variants, so the owner's own rule is declared and never executed"


def test_the_trial_count_covers_both_grids():
    """36 was the giveaway: it matched the sweep grid exactly, so the report looked self-consistent."""
    n_sweep = len(sr.MODES) * len(sr.LOOKBACKS) * len(sr.BODY_FILTERS) * len(sr.REWARD_RATIOS)
    n_reclaim = len(sr.RECLAIM_REFS) * len(sr.REWARD_RATIOS) * len(sr.RECLAIM_TREND_EMAS)
    assert n_sweep == 36, "the sweep grid changed; check the trial arithmetic in run()"
    assert n_reclaim == len(sr.reclaim_variants("XAUUSD", "4h")), \
        "the reclaim trial arithmetic and the reclaim grid disagree"

    source = inspect.getsource(sr.run)
    assert "n_trials_sweep" in source and "n_trials_reclaim" in source, \
        "run() must count both grids, or the deflated Sharpe is deflated by too few trials"
    assert "(n_sweep + n_reclaim)" in source


def test_every_declared_variant_has_a_unique_name():
    """Two grids feeding one report: a collision would silently overwrite a result."""
    names = [v["variant"] for v in sr.sweep_variants("XAUUSD", "4h")]
    names += [v["variant"] for v in sr.reclaim_variants("XAUUSD", "4h")]
    duplicates = {n for n in names if names.count(n) > 1}
    assert not duplicates, f"variant names collide across the two grids: {sorted(duplicates)}"


def test_the_reclaim_grid_really_is_the_owners_rule():
    """Guards the rule itself, not just that something ran. The distinguishing property is that one
    candle uses BOTH extremes in OPPOSITE directions, which neither other mode does."""
    specs = sr.reclaim_variants("XAUUSD", "4h")
    assert specs, "the reclaim grid is empty"
    assert all(s["params"]["mode"] == sr.RECLAIM_MODE for s in specs)
    assert {s["params"]["ref"] for s in specs} == set(sr.RECLAIM_REFS)
    assert 1 in sr.RECLAIM_REFS, "ref 1 is the owner's words read literally and must stay in the grid"


def test_run_does_not_parse_the_whole_registry():
    """It used to call load_registry() for two timestamps, which is a 617 MB parse and what killed the
    whole-system survey on 8 October."""
    source = inspect.getsource(sr.run)
    assert "lab.market_meta()" in source
    # The CALL, not the word: the comment above it names load_registry() to explain what was replaced,
    # and a bare substring test fails on its own documentation.
    assert "lab.load_registry(" not in source


def test_the_forward_candidates_are_declared_once_and_differ():
    """Both forward tests come out of this module. If they ever collapse onto the same spec, one of the
    two forward tests is measuring nothing new."""
    assert sr.FORWARD_CANDIDATE["params"]["mode"] == "continue"
    assert sr.FORWARD_RECLAIM["params"]["mode"] == sr.RECLAIM_MODE
    assert sr.FORWARD_CANDIDATE["variant"] != sr.FORWARD_RECLAIM["variant"]
    assert sr.FORWARD_RECLAIM["params"]["ref"] == 1, \
        "the forward test must carry the owner's literal reading, not a generalisation of it"
