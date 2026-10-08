"""A declared family must be able to have its robustness measured, or it can never be approved.

WHY THIS EXISTS
---------------
`strategy_book.neighbours()` builds a candidate's one-step neighbours by stepping along the grids in
`lab.FAMILIES`. Families registered through `ORDER_BUILDERS` declare their variants in code and have no
entry in `FAMILIES`, so there was no grid, `neighbour_share` returned None, and `classify` recorded
"neighbour share n/a" - which keeps the entry out of `approved_for_demo`.

So every declared hypothesis was unapprovable by construction. On 8 October 2026 the declared
hypotheses were the only things in the system producing candidates with 100 or more holdout trades.

Measuring it changed the ranking immediately, which is the point of measuring it:

    XAUUSD reclaim|ref1|rr2|ema400   5 of 6 neighbours profitable   a plateau
    XAUUSD reclaim|ref1|rr3|ema400   4 of 5                         a plateau
    BTCUSD reclaim|ref1|rr3|notrend  1 of 5                         a lone cell

The bitcoin variant had the biggest headline return (+44.14 %) and was the weakest thing on the board.

WHY A CALLABLE AND NOT A DICT
-----------------------------
One family can carry several rules with different parameters. `sweep_reversal`'s reject and continue
modes step along lookback / rr / require_body; its reclaim mode steps along ref / rr / trend_ema.
Stepping a reclaim spec along `lookback` would change nothing at all, because `sweep_orders` reads
`ref` in that mode - and a neighbour set that changes nothing reports 100 % profitable, turning a
fitted spike into a perfect robustness score. That failure would be worse than the missing measurement
it replaced.

No backtests here: these check the grid wiring, not the market.
"""
import pytest

from src import strategy_book as book
from src import strategy_lab as lab
from src import sweep_reversal as sr


def _reclaim(**overrides):
    spec = next(s for s in sr.reclaim_variants("XAUUSD", "4h") if s["variant"] == "reclaim|ref1|rr3|ema400")
    spec = {**spec, "params": {**spec["params"], **overrides}}
    return spec


def _sweep(**overrides):
    spec = next(s for s in sr.sweep_variants("XAUUSD", "4h") if s["params"].get("mode") == "continue")
    return {**spec, "params": {**spec["params"], **overrides}}


# ------------------------------------------------------------------ the hook exists and is used
def test_the_family_registers_a_neighbour_grid():
    assert "sweep_reversal" in lab.NEIGHBOUR_GRIDS
    assert callable(lab.NEIGHBOUR_GRIDS["sweep_reversal"])


def test_neighbours_uses_the_declared_grid_when_families_has_none():
    assert lab.FAMILIES.get("sweep_reversal") is None, \
        "this family now has a FAMILIES grid; the declared-grid path is no longer the one under test"
    rows = book.neighbours(_reclaim())
    assert rows, "a declared family still produces no neighbours, so it stays unapprovable"


# ------------------------------------------------------------------ the right grid for the right rule
def test_a_reclaim_spec_steps_along_ref_not_lookback():
    """The failure that would be worse than no measurement: stepping a setting the rule ignores."""
    grid = sr.neighbour_grid(_reclaim())
    assert set(grid) == {"ref", "rr", "trend_ema"}
    assert "lookback" not in grid, \
        "reclaim reads `ref`; stepping `lookback` changes nothing and scores a spike as 100 % robust"


def test_a_sweep_spec_steps_along_lookback_not_ref():
    grid = sr.neighbour_grid(_sweep())
    assert set(grid) == {"lookback", "rr", "require_body"}
    assert "ref" not in grid


def test_the_grids_match_the_declared_constants():
    """If a grid drifts from the constants the variants are built from, the neighbours stop being
    neighbours of anything that was actually tried."""
    reclaim = sr.neighbour_grid(_reclaim())
    assert reclaim["ref"] == list(sr.RECLAIM_REFS)
    assert reclaim["rr"] == list(sr.REWARD_RATIOS)
    assert reclaim["trend_ema"] == list(sr.RECLAIM_TREND_EMAS)
    sweep = sr.neighbour_grid(_sweep())
    assert sweep["lookback"] == list(sr.LOOKBACKS)
    assert sweep["require_body"] == list(sr.BODY_FILTERS)


def test_neighbours_of_a_reclaim_spec_actually_differ_from_it():
    """The whole measurement is void if the 'neighbours' are the same strategy.

    A neighbour may differ in params OR in exits: neighbours() steps both the family grid and
    lab.EXIT_GRID, so stepping max_bars with the params untouched is a legitimate neighbour. What is
    not acceptable is a row identical in both.
    """
    spec = _reclaim()
    rows = book.neighbours(spec)
    assert rows
    for row in rows:
        same_params = row["params"] == spec["params"]
        same_exits = row["exits"] == spec["exits"]
        assert not (same_params and same_exits), f"a neighbour is identical to the candidate: {row}"


def test_every_neighbour_keeps_the_rule_itself():
    """A neighbour varies a setting, never the mode. Comparing a reclaim candidate against a `continue`
    strategy would answer a different question entirely."""
    for row in book.neighbours(_reclaim()):
        assert row["params"]["mode"] == sr.RECLAIM_MODE


def test_the_side_is_never_stepped():
    """Flipping direction is not a neighbour, it is the inverse control, which is measured separately."""
    for row in book.neighbours(_reclaim()):
        assert row["params"].get("side") == _reclaim()["params"].get("side")


# ------------------------------------------------------------------ it must fail safe
def test_a_family_with_no_declared_grid_still_returns_no_neighbours():
    assert book.neighbours({"family": "not-a-real-family", "params": {}, "exits": {}}) == []


def test_a_grid_callable_that_raises_does_not_break_the_book(monkeypatch):
    """This runs inside the hourly book update. One bad grid must not stop everything being scored."""
    def explode(spec):
        raise RuntimeError("bad grid")

    monkeypatch.setitem(lab.NEIGHBOUR_GRIDS, "sweep_reversal", explode)
    assert book.neighbours(_reclaim()) == []


def test_a_grid_callable_returning_nothing_is_treated_as_no_grid(monkeypatch):
    monkeypatch.setitem(lab.NEIGHBOUR_GRIDS, "sweep_reversal", lambda spec: {})
    assert book.neighbours(_reclaim()) == []


# ------------------------------------------------------------------ no family may be unmeasurable
def test_every_surveyed_family_can_have_its_robustness_measured():
    """The guard for the whole class of bug.

    A family with neither a FAMILIES grid nor a NEIGHBOUR_GRIDS entry gets neighbour_share None,
    classify() writes "neighbour share n/a", and the entry can never reach approved_for_demo. On
    8 October 2026 five of the nine surveyed families were in that state, including `cisd`, which was
    already running as a paper forward test, and `poi_liquidity`, which had produced a 158-trade
    survivor. Measuring them was worth doing: CISD came out at 5 of 6 neighbours profitable and
    poi_liquidity at 2 of 4, which are very different verdicts that nobody could see before.

    A new family added without a grid is silently unapprovable, so this fails the build instead.
    """
    from src import direction_sweep

    direction_sweep.load_families()
    unmeasurable = []
    for family in sorted(direction_sweep.FAMILY_VARIANTS):
        # A registry key labels a grid; the engine family that owns the builder may differ.
        engine = "sweep_reversal" if family == "sweep_reclaim" else family
        if (lab.FAMILIES.get(family) is None and lab.FAMILIES.get(engine) is None
                and family not in lab.NEIGHBOUR_GRIDS and engine not in lab.NEIGHBOUR_GRIDS):
            unmeasurable.append(family)
    assert not unmeasurable, (
        f"these families have no neighbour grid, so they can never be approved for demo however well "
        f"they perform: {unmeasurable}. Declare one with lab.NEIGHBOUR_GRIDS[family] = callable(spec).")


def test_a_declared_grid_never_steps_a_rule_selector():
    """`mode`, `model`, `combination` and `side` choose WHICH rule runs. Stepping one compares a
    candidate against a different strategy, which is not a robustness measurement at all - and it
    would inflate the score rather than deflate it, so it fails quietly in the flattering direction."""
    from src import direction_sweep

    direction_sweep.load_families()
    selectors = {"side", "mode", "model", "combination"}
    for family, build in sorted(lab.NEIGHBOUR_GRIDS.items()):
        grid = build({"params": {}, "exits": {}}) or {}
        leaked = selectors & set(grid)
        assert not leaked, f"{family}'s neighbour grid steps rule selectors {sorted(leaked)}"
