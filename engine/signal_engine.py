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


# ============================================================ STEP 2: the full research record
#
# `SignalDecision` above is the compact pair the bar loop uses, and it stays exactly as it is: every
# existing caller keeps working, and `tests/test_engine_package.py` keeps passing unchanged. What
# follows is ADDITIVE — the complete record the research engine needs, with a deterministic reject
# code beside the free-text reason rather than instead of it.
#
# THE THREE FIELDS THAT EARN THEIR PLACE
# --------------------------------------
# * `invalidation` is NOT the stop. The stop is where the position closes; the invalidation is the
#   market fact that would prove the idea wrong. They coincide only by choice, and storing both is
#   what lets a post-mortem separate "the idea was wrong" from "the stop was badly placed".
# * `source_conditions` keeps every condition CHECKED, true or false — not only the ones that passed.
#   A condition that is always true is doing nothing, and that is invisible if only passes are kept.
# * `reject_code` is a stable enum, so refusals can be COUNTED. Free text cannot be grouped.
#
# ONE TARGET IS RECORDED AS ONE TARGET
# ------------------------------------
# Most rules in this project produce a single take-profit. tp2 and tp3 are therefore None, not a
# guessed multiple of tp1. Inventing them would put numbers in the ledger that no rule ever computed,
# and a later study of "how often does tp2 fill" would be measuring this function's arithmetic.

from engine.reject_codes import RejectCode  # noqa: E402  (additive, kept beside the compact pair)


@dataclass
class SignalRecord:
    """The complete record of one signal - taken or rejected - as the research engine stores it.

    Named ``SignalRecord``, not ``Signal``: ``volatility_trend_breakout.Signal`` already exists and
    means a breakout entry from the owner's Pine script. Two different things called Signal in one
    codebase is how a reader ends up reasoning about the wrong one.

    `trial_id` and `experiment_id` tie a signal to the run that produced it, which is what makes the
    multiple-testing accounting auditable rather than asserted.
    """

    asset: str
    timestamp: str
    bar: int
    direction: Optional[str]                     # BUY / SELL, or None when there was no candidate
    strategy: str = ""
    variant: str = ""
    timeframe: str = ""
    entry: Optional[float] = None
    stop: Optional[float] = None
    tp1: Optional[float] = None
    tp2: Optional[float] = None                  # None means the rule produced no second target
    tp3: Optional[float] = None
    risk: Optional[float] = None                 # price distance from entry to stop
    regime: Dict[str, Optional[str]] = field(default_factory=dict)
    confidence: Optional[float] = None           # only when the strategy genuinely produces one
    reasons: List[str] = field(default_factory=list)      # the free text, preserved
    invalidation: Optional[float] = None
    source_conditions: Dict[str, bool] = field(default_factory=dict)
    accepted: bool = False
    reject_code: Optional[RejectCode] = None
    trial_id: Optional[str] = None
    experiment_id: Optional[str] = None

    @property
    def targets(self) -> List[float]:
        """The targets that actually exist, in order. Absent ones are absent, not zero."""
        return [t for t in (self.tp1, self.tp2, self.tp3) if t is not None]

    @property
    def reward_risk(self) -> Optional[float]:
        """R:R to TP1 — the one that decides whether the trade is worth its own spread."""
        if self.entry is None or self.stop is None or self.tp1 is None:
            return None
        risk = abs(self.entry - self.stop)
        return None if risk <= 0 else abs(self.tp1 - self.entry) / risk

    def as_row(self) -> dict:
        """A flat dict for the ledger. The reject code serialises as its stable string."""
        return {
            "asset": self.asset, "timestamp": self.timestamp, "bar": self.bar,
            "direction": self.direction, "strategy": self.strategy, "variant": self.variant,
            "timeframe": self.timeframe, "entry": self.entry, "stop": self.stop,
            "tp1": self.tp1, "tp2": self.tp2, "tp3": self.tp3, "risk": self.risk,
            "reward_risk": self.reward_risk, "regime": dict(self.regime),
            "confidence": self.confidence, "reasons": list(self.reasons),
            "invalidation": self.invalidation, "source_conditions": dict(self.source_conditions),
            "accepted": self.accepted,
            "reject_code": (str(self.reject_code) if self.reject_code else None),
            "trial_id": self.trial_id, "experiment_id": self.experiment_id,
        }


def build_signal(*, asset: str, timestamp: str, bar: int, direction: Optional[str],
                 strategy: str = "", variant: str = "", timeframe: str = "",
                 entry: Optional[float] = None, stop: Optional[float] = None,
                 tp1: Optional[float] = None, tp2: Optional[float] = None,
                 tp3: Optional[float] = None,
                 regime: Optional[Dict[str, Optional[str]]] = None,
                 conditions: Optional[Dict[str, bool]] = None,
                 invalidation: Optional[float] = None, confidence: Optional[float] = None,
                 bias: str = NEUTRAL, min_confluence: int = 1,
                 require_bias_agreement: bool = False,
                 trial_id: Optional[str] = None, experiment_id: Optional[str] = None) -> SignalRecord:
    """Assemble a Signal and decide it, attaching a deterministic reject code when it does not qualify.

    Checks run in order of how FUNDAMENTAL the objection is, not in the order they were written, so
    the code recorded is the deepest reason rather than whichever test happened to run last. A setup
    with no stop is REJECT_INVALID_STOP, never REJECT_INVALID_STRUCTURE — "it had no stop" and "it had
    too few conditions" are different research findings and must not collapse into one another.
    """
    conditions = dict(conditions or {})
    risk = None if (entry is None or stop is None) else abs(entry - stop)
    sig = SignalRecord(asset=asset, timestamp=timestamp, bar=bar, direction=direction,
                       strategy=strategy, variant=variant, timeframe=timeframe,
                       entry=entry, stop=stop, tp1=tp1, tp2=tp2, tp3=tp3, risk=risk,
                       regime=dict(regime or {}), confidence=confidence,
                       invalidation=invalidation, source_conditions=conditions,
                       trial_id=trial_id, experiment_id=experiment_id)

    def refuse(code: RejectCode, why: str) -> SignalRecord:
        sig.accepted, sig.reject_code, sig.reasons = False, code, [why]
        return sig

    if direction not in ("BUY", "SELL"):
        return refuse(RejectCode.NO_SETUP, "no directional candidate at this bar")
    if entry is None or not entry > 0:
        return refuse(RejectCode.INVALID_ENTRY, "entry is missing or not a positive price")
    if stop is None:
        return refuse(RejectCode.INVALID_STOP, "no stop, so the setup has no invalidation")
    if risk is None or risk <= 0:
        return refuse(RejectCode.INVALID_STOP, "stop is at the entry, so there is no risk unit")
    if (direction == "BUY" and stop >= entry) or (direction == "SELL" and stop <= entry):
        return refuse(RejectCode.INVALID_STOP,
                      f"stop is on the wrong side of the entry for a {direction}")
    if tp1 is None:
        return refuse(RejectCode.INVALID_TARGET, "no first target, so there is nothing to aim at")
    if (direction == "BUY" and tp1 <= entry) or (direction == "SELL" and tp1 >= entry):
        return refuse(RejectCode.INVALID_TARGET,
                      f"first target is on the wrong side of the entry for a {direction}")

    present = sorted(k for k, v in conditions.items() if v)
    if len(present) < min_confluence:
        missing = sorted(k for k, v in conditions.items() if not v)
        # A NAMED condition that failed reports its OWN code. "no FVG" and "not enough conditions"
        # are different findings, and collapsing them loses the one that can be acted on.
        for needle, code in (("fvg", RejectCode.NO_FVG),
                             ("sweep", RejectCode.NO_LIQUIDITY),
                             ("liquidity", RejectCode.NO_LIQUIDITY),
                             ("reclaim", RejectCode.INVALID_RECLAIM),
                             ("session", RejectCode.SESSION)):
            hit = next((name for name in missing if needle in name.lower()), None)
            if hit:
                return refuse(code, f"{hit} absent; {len(present)} of {min_confluence} present")
        return refuse(RejectCode.INVALID_STRUCTURE,
                      f"{len(present)} of a required {min_confluence} conditions present"
                      + (f"; missing {', '.join(missing)}" if missing else ""))

    if require_bias_agreement:
        wanted = BULLISH if direction == "BUY" else BEARISH
        if bias != wanted:
            return refuse(RejectCode.NO_TREND, f"{direction} against a {bias} structural bias")

    sig.accepted = True
    sig.reasons = [f"{direction} with {len(present)} condition(s): {', '.join(present)}"]
    return sig


def reject_profile(signals: List[SignalRecord]) -> Dict[str, object]:
    """Opportunities, signals, rejections, and why — BY CODE.

    These are the four questions the research engine must be able to answer about any run, and they
    are only answerable because the code is an enum rather than a sentence.
    """
    accepted = sum(1 for s in signals if s.accepted)
    by_code: Dict[str, int] = {}
    uncoded = 0
    for s in signals:
        if s.accepted:
            continue
        if s.reject_code is None:
            uncoded += 1                       # a rejection with no code is a defect, so it is COUNTED
            continue
        key = str(s.reject_code)
        by_code[key] = by_code.get(key, 0) + 1
    return {"opportunities": len(signals), "accepted": accepted,
            "rejected": len(signals) - accepted,
            "by_code": dict(sorted(by_code.items(), key=lambda kv: -kv[1])),
            "rejections_without_a_code": uncoded,
            "acceptance_rate_pct": (round(accepted / len(signals) * 100, 2) if signals else None)}


def signal_from_decision(decision: SignalDecision, *, asset: str, timeframe: str = "",
                         strategy: str = "", variant: str = "",
                         tp2: Optional[float] = None, tp3: Optional[float] = None,
                         regime: Optional[Dict[str, Optional[str]]] = None,
                         invalidation: Optional[float] = None,
                         trial_id: Optional[str] = None,
                         experiment_id: Optional[str] = None) -> SignalRecord:
    """Promote a compact `SignalDecision` into a full `Signal`, for callers migrating gradually.

    This is the backward-compatibility seam. The bar loop can keep calling `decide_signal` and gain
    the full record without its decision logic being rewritten in the same change — which is how the
    old and the new would otherwise drift apart and start disagreeing about the same bar.
    """
    return build_signal(
        asset=asset, timestamp=decision.ts, bar=decision.bar, direction=decision.side,
        strategy=strategy, variant=variant, timeframe=timeframe,
        entry=decision.entry, stop=decision.stop, tp1=decision.target, tp2=tp2, tp3=tp3,
        regime=regime, conditions=decision.conditions,
        invalidation=invalidation if invalidation is not None else decision.stop,
        bias=decision.bias, min_confluence=max(1, decision.confluence if decision.take else 1),
        trial_id=trial_id, experiment_id=experiment_id)
