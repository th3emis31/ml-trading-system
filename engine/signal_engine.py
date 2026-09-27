"""08. Signal decision: bias, setup, confirmation, confluence, entry, invalidation.

This is where the other engines meet. It owns no market logic of its own — structure, liquidity, volume and
regime each answer their own question elsewhere — and its single job is to decide, at ONE bar, whether the
evidence adds up to a trade, and to say plainly why when it does not.

WHY CONFLUENCE IS COUNTED, NOT MULTIPLIED
-----------------------------------------
"Confluence" usually means stacking conditions until one survives the backtest, which is how a filter that
fits the past gets mistaken for an edge. This project has measured that directly: an architecture card's
five-condition stack was tested condition by condition and the stack did not beat its parts. So confluence
here is a COUNT of independently-computed conditions with a declared threshold, each one recorded by name,
so the ledger can answer "which conditions were present" for every trade and every rejection. A condition
that never changes an outcome is then visible as dead weight rather than hidden inside a score.

INVALIDATION IS PART OF THE SIGNAL, NOT AN AFTERTHOUGHT
-------------------------------------------------------
A setup with no stated invalidation cannot be refused, only exited. `Signal` carries the level that would
prove it wrong at the moment it was taken, which is what makes the risk engine's job arithmetic.

"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

BULLISH, BEARISH, NEUTRAL = "bullish", "bearish", "neutral"


@dataclass
class SignalDecision:
    """What the engine concluded at one bar, and everything needed to audit it later.

    Named `SignalDecision`, not `Decision`: `governance.Decision` already exists and means an approval.
    """

    bar: int
    ts: str
    take: bool
    side: Optional[str] = None            # BUY / SELL
    reason: str = ""                      # why taken, or why NOT — always populated
    entry: Optional[float] = None
    stop: Optional[float] = None
    target: Optional[float] = None
    bias: str = NEUTRAL
    conditions: Dict[str, bool] = field(default_factory=dict)
    confluence: int = 0

    def present(self) -> List[str]:
        """The conditions that were TRUE, by name — what the ledger records for this decision."""
        return sorted(k for k, v in self.conditions.items() if v)


def bias_from_structure(structure_row: Optional[dict]) -> str:
    """Directional bias from the higher-timeframe structure state at this bar.

    `structure_at` already labels trend per bar from confirmed BOS/CHOCH, so this reads it rather than
    re-deriving a trend, which would be a second opinion that can disagree with the first.
    """
    if not structure_row:
        return NEUTRAL
    trend = str(structure_row.get("trend") or "").lower()
    if trend.startswith("bull"):
        return BULLISH
    if trend.startswith("bear"):
        return BEARISH
    return NEUTRAL


def decide_signal(bar: int, ts: str, *, side: Optional[str], entry: Optional[float],
                  stop: Optional[float], target: Optional[float],
                  conditions: Dict[str, bool], bias: str = NEUTRAL,
                  min_confluence: int = 1, require_bias_agreement: bool = False) -> SignalDecision:
    """Turn the evidence at one bar into a take-or-not, with the reason either way.

    Named `decide_signal`, not `decide`: `daily_agent.decide` already exists.

    `min_confluence` is declared by the caller BEFORE the run, not tuned afterwards — a threshold chosen
    once results are visible is a fitted parameter wearing the name of a rule.
    """
    confluence = sum(1 for v in conditions.values() if v)
    base = SignalDecision(bar=bar, ts=ts, take=False, side=side, entry=entry, stop=stop, target=target,
                          bias=bias, conditions=dict(conditions), confluence=confluence)

    if side not in ("BUY", "SELL"):
        base.reason = "no directional signal at this bar"
        return base
    if entry is None or stop is None or target is None:
        base.reason = "incomplete levels: a setup needs an entry, a stop and a target"
        return base
    if stop == entry:
        base.reason = "stop is at the entry, so the setup has no invalidation"
        return base
    if (side == "BUY" and stop >= entry) or (side == "SELL" and stop <= entry):
        base.reason = f"stop is on the wrong side of the entry for a {side}"
        return base
    if confluence < min_confluence:
        missing = sorted(k for k, v in conditions.items() if not v)
        base.reason = (f"confluence {confluence} below the required {min_confluence}"
                       + (f"; missing {', '.join(missing)}" if missing else ""))
        return base
    if require_bias_agreement:
        wanted = BULLISH if side == "BUY" else BEARISH
        if bias != wanted:
            base.reason = f"{side} against a {bias} bias"
            return base

    base.take = True
    base.reason = f"{side} with {confluence} condition(s): {', '.join(base.present())}"
    return base


def condition_effect(decisions: List[SignalDecision]) -> Dict[str, dict]:
    """How often each condition was present, and how often it coincided with a taken trade.

    Read this before adding another condition. One that is present in every decision — taken or refused —
    is not filtering anything, and one that is never present is dead code pretending to be a rule.
    """
    out: Dict[str, dict] = {}
    for d in decisions:
        for name, value in d.conditions.items():
            row = out.setdefault(name, {"present": 0, "absent": 0, "present_and_taken": 0})
            if value:
                row["present"] += 1
                if d.take:
                    row["present_and_taken"] += 1
            else:
                row["absent"] += 1
    return dict(sorted(out.items()))
