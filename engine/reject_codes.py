"""Deterministic rejection codes, shared by the signal, risk and execution engines.

WHY A CODE AND NOT JUST A SENTENCE
----------------------------------
A free-text reason is readable but not countable. "confluence 1 below the required 2; missing sweep"
and "confluence 0 below the required 2; missing fvg, sweep" are the same rejection to a human and two
different strings to a `Counter`, so a run's rejection profile fragments into near-duplicates and the
question the research engine exists to answer — *how many opportunities were there, and why did they
not become trades* — cannot be answered by grouping.

So every rejection carries a CODE from this enum **plus** the human detail string. The code is what
gets counted; the detail is what gets read. Neither replaces the other.

CODES ARE ADDED, NEVER EDITED
-----------------------------
A code is a stable identifier. Renaming one silently invalidates comparison against every ledger
already written, and a ledger you cannot compare against last month's is a log, not a record. So the
names here are permanent: a better name is a new code, and the old one stays.

THE PREFIX IS PART OF THE VALUE
-------------------------------
`REJECT_NO_FVG`, not `NO_FVG`. The value is what lands in a JSON ledger, and a bare `NO_FVG` in a
column of mixed values reads like a state rather than a refusal. Spelling it out means a row is
self-describing to someone reading the file years later with none of this context.
"""
from __future__ import annotations

from enum import Enum


class RejectCode(str, Enum):
    """Why an opportunity did not become a trade. `str` mixin so it serialises as its own value."""

    # -- the setup itself did not qualify ---------------------------------------------------------
    NO_SETUP = "REJECT_NO_SETUP"                    # no directional candidate at this bar at all
    NO_TREND = "REJECT_NO_TREND"                    # direction disagrees with the structural bias
    NO_LIQUIDITY = "REJECT_NO_LIQUIDITY"            # the required sweep or liquidity event is absent
    NO_FVG = "REJECT_NO_FVG"                        # the required imbalance is absent
    INVALID_RECLAIM = "REJECT_INVALID_RECLAIM"      # price did not close back inside as the rule needs
    INVALID_STRUCTURE = "REJECT_INVALID_STRUCTURE"  # structure unreadable, or too few conditions present
    SESSION = "REJECT_SESSION"                      # outside the session the rule is defined for

    # -- the levels are not usable ----------------------------------------------------------------
    INVALID_ENTRY = "REJECT_INVALID_ENTRY"          # entry missing, non-finite or non-positive
    INVALID_STOP = "REJECT_INVALID_STOP"            # stop missing, at the entry, or on the wrong side
    INVALID_TARGET = "REJECT_INVALID_TARGET"        # first target missing, or on the wrong side

    # -- the account or the rules would not take it ------------------------------------------------
    RISK = "REJECT_RISK"                            # a risk refusal with no more specific cause
    EXECUTION = "REJECT_EXECUTION"                  # spread, fill or broker-side refusal
    DATA = "REJECT_DATA"                            # bars missing, stale, or not from the broker

    # -- STEP 3: risk engine. Specific enough that a funnel says WHICH rule refused ----------------
    # REJECT_RISK above stays as the catch-all so an older ledger keeps its meaning; new refusals use
    # the specific code, because "risk refused it" cannot be acted on and "below the broker's minimum
    # lot" can.
    RISK_TOO_LARGE = "REJECT_RISK_TOO_LARGE"        # requested risk % above what the limits allow
    INVALID_LOT = "REJECT_INVALID_LOT"              # computed size is not a finite positive number
    MIN_LOT = "REJECT_MIN_LOT"                      # size rounds below the broker's minimum volume
    MAX_LOT = "REJECT_MAX_LOT"                      # size exceeds the broker's maximum volume
    INVALID_INSTRUMENT = "REJECT_INVALID_INSTRUMENT"  # no specification for this symbol at all
    INVALID_SPEC = "REJECT_INVALID_SPEC"            # a specification exists but is incomplete/absurd
    CURRENCY_CONVERSION = "REJECT_CURRENCY_CONVERSION"  # profit currency differs and no rate is known
    MAX_EXPOSURE = "REJECT_MAX_EXPOSURE"            # too much already open
    MAX_DRAWDOWN = "REJECT_MAX_DRAWDOWN"            # drawdown halt reached
    DAILY_LOSS = "REJECT_DAILY_LOSS"                # the day's loss stop reached
    REWARD_RISK = "REJECT_REWARD_RISK"              # reward:risk below the minimum worth its spread

    def __str__(self) -> str:                       # so f-strings print the code, not the member repr
        return self.value


#: Codes the SIGNAL engine may return. A code outside this set coming from `build_signal` is a bug,
#: and `tests/test_signal_engine.py` asserts it, because a risk code appearing in a signal rejection
#: would silently move the funnel's blame from one engine to another.
SIGNAL_CODES = frozenset({
    RejectCode.NO_SETUP, RejectCode.NO_TREND, RejectCode.NO_LIQUIDITY, RejectCode.NO_FVG,
    RejectCode.INVALID_RECLAIM, RejectCode.INVALID_STRUCTURE, RejectCode.SESSION,
    RejectCode.INVALID_ENTRY, RejectCode.INVALID_STOP, RejectCode.INVALID_TARGET,
})

#: Codes the RISK engine may return. `RISK` remains a member so ledgers written before STEP 3 still
#: read back, but new refusals name the specific rule: a funnel that says "risk refused 400 setups"
#: tells you nothing, while "380 below the minimum lot" tells you the account is too small for the stop.
RISK_CODES = frozenset({
    RejectCode.RISK, RejectCode.RISK_TOO_LARGE, RejectCode.INVALID_LOT,
    RejectCode.MIN_LOT, RejectCode.MAX_LOT, RejectCode.INVALID_INSTRUMENT,
    RejectCode.INVALID_SPEC, RejectCode.CURRENCY_CONVERSION, RejectCode.MAX_EXPOSURE,
    RejectCode.MAX_DRAWDOWN, RejectCode.DAILY_LOSS, RejectCode.REWARD_RISK,
})

#: Codes raised once an order is being placed or a bar is being read, rather than while deciding.
RUNTIME_CODES = frozenset({RejectCode.EXECUTION, RejectCode.DATA})

ALL_CODES = SIGNAL_CODES | RISK_CODES | RUNTIME_CODES


def named(value) -> "RejectCode":
    """The enum member for a stored value, accepting either the code or the bare member name.

    A ledger written months ago holds the string. Reading it back must not need the writer's Python.
    """
    if isinstance(value, RejectCode):
        return value
    text = str(value)
    for code in RejectCode:
        if text in (code.value, code.name):
            return code
    raise ValueError(f"{value!r} is not a known reject code; codes are added, never renamed")
