"""A survey that only ever forces a direction cannot see a rule whose edge needs both.

WHY THIS EXISTS
---------------
`direction_sweep` surveys every strategy family the system owns, long only and then short only. It
forces a side by taking the family's registered builder and zeroing every signal in the unwanted
direction.

That is not a neutral act, and on 8 October 2026 it produced a false negative on the owner's own rule.
Evaluated as declared, trading both ways, `reclaim|ref1|rr3|ema400` is positive on all three splits
(search +42.70 %, validation +13.43 %, holdout +23.70 %). Forced to one side, **all 36 of its survey
rows had a negative validation split** while search and holdout stayed positive. The survey reported
it as clearing nothing, twice, on both passes.

So the survey could measure every family and still be unable to see the best candidate in the
project. `--side both` runs each family exactly as it declared itself.

These tests do not run a backtest. They check the wiring: that `both` reaches the builder unwrapped,
that `long` and `short` still wrap, and that the builder is always put back.
"""
import inspect

import numpy as np
import pytest

from src import direction_sweep as ds
from src import strategy_lab as lab


def _orders(n=6):
    """A builder emitting both directions, so wrapping or not wrapping is visible in the output."""
    side = np.array([1, -1, 1, -1, 0, 1])[:n]
    return side, np.full(n, 1.0), np.full(n, 2.0)


def test_both_is_an_accepted_side():
    source = inspect.getsource(ds)
    assert '"long", "short", "both"' in source, "--side both is not offered on the command line"


def test_the_side_map_covers_all_three_and_both_is_falsy():
    """`both` is 0 on purpose: the wrap is applied only `if side_wanted`, so 0 means run unwrapped."""
    source = inspect.getsource(ds.run)
    assert '{"long": 1, "short": -1, "both": 0}' in source


def test_both_leaves_the_builder_unwrapped():
    source = inspect.getsource(ds.evaluate_family)
    assert "if side_wanted else original" in source, \
        "evaluate_family always wraps, so --side both would still force a direction"


# ------------------------------------------------------------------ the wrapper itself still works
def test_the_long_wrapper_keeps_only_longs():
    wrapped = ds.one_side_builder(lambda ind, spec: _orders(), 1)
    side, _stop, _target = wrapped(None, None)
    assert set(np.unique(side)) <= {0, 1}
    assert (side == 1).sum() == 3 and (side == -1).sum() == 0


def test_the_short_wrapper_keeps_only_shorts():
    wrapped = ds.one_side_builder(lambda ind, spec: _orders(), -1)
    side, _stop, _target = wrapped(None, None)
    assert set(np.unique(side)) <= {0, -1}
    assert (side == -1).sum() == 2 and (side == 1).sum() == 0


def test_the_wrapper_does_not_mutate_the_builders_own_array():
    """It copies before masking. Without that, a family reusing its array would be corrupted for the
    next pass and the second direction would silently measure the wrong thing."""
    shared = _orders()

    def builder(ind, spec):
        return shared

    ds.one_side_builder(builder, 1)(None, None)
    assert list(shared[0]) == [1, -1, 1, -1, 0, 1], "the wrapper mutated the builder's own signals"


def test_the_wrapper_leaves_stops_and_targets_alone():
    """Only the direction is forced. Changing a stop would make the survey measure a different strategy
    rather than the same one restricted."""
    side, stop, target = ds.one_side_builder(lambda ind, spec: _orders(), 1)(None, None)
    assert list(stop) == [1.0] * 6 and list(target) == [2.0] * 6


# ------------------------------------------------------------------ the builder is always restored
def test_the_original_builder_is_restored_even_when_a_variant_raises():
    """evaluate_family swaps a wrapper into the global ORDER_BUILDERS. If it ever failed to put the
    original back, every later caller in the process would silently get a one-sided strategy."""
    source = inspect.getsource(ds.evaluate_family)
    assert "finally:" in source and "lab.ORDER_BUILDERS[family] = original" in source


def test_the_survey_reports_which_side_it_ran():
    """The file name and the payload both carry it, so a `both` report can never be mistaken for a
    one-sided one when it is read back months later."""
    source = inspect.getsource(ds.run)
    assert '"side": side' in source
    assert 'direction_sweep_{side}_' in source


def test_both_mode_is_documented_as_a_false_negative_fix():
    """The reason has to survive in the code, or the next person removes the mode as redundant."""
    source = inspect.getsource(ds.evaluate_family)
    assert "validation" in source and "reclaim" in source, \
        "the comment explaining why forcing a side produced a false negative has gone"
