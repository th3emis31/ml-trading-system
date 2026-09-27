"""10. Risk: position sizing, risk %, R:R, daily loss, drawdown, exposure.

Every number a trade is sized by comes from ONE place. That is not tidiness — it is the rule the owner's
CLAUDE.md states outright ("Position size always comes from one sizing function; never hard-code lots or
units"), and the reason is that a second sizing path is invisible until it has already traded differently
from the first.

THE UNIT TRAP THIS MODULE EXISTS TO PREVENT
-------------------------------------------
Sizing is where units go wrong, and this project has the scars: a distance measured in one instrument's
ATR applied to another timeframe, and break-even distances in raw points sized for gold applied to BTCUSD
where Point is 0.01, which trailed a dollar-eighty behind price and stopped four trades out within a
second. So `position_size` takes the stop DISTANCE IN PRICE and the value of one price unit per lot, and
refuses anything else. There is no "pips" argument, because a pip means different things per symbol and
the conversion is the bug.

LIMITS ARE CHECKED BEFORE A TRADE, NOT AFTER
--------------------------------------------
`check` returns a refusal with a reason rather than a boolean, so the ledger can record WHY a setup was not
taken. A backtest that silently drops trades at a risk limit and reports the survivors is describing a
strategy nobody ran.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Limits:
    """The owner's standing limits. Defaults match the live demo strategies rather than being invented."""

    risk_percent: float = 1.0          # of equity, per trade
    max_open_per_symbol: int = 1       # the owner's standing rule: never stack the same asset
    max_open_total: int = 2
    daily_loss_percent: float = 3.0    # stop for the day past this
    max_drawdown_percent: float = 15.0 # halt the strategy past this
    min_reward_risk: float = 1.0       # a setup below this is not worth its own spread


@dataclass
class Refusal:
    """Why a setup was not taken. Carries the reason so the ledger can record it verbatim."""

    reason: str
    detail: Optional[dict] = None

    def __bool__(self) -> bool:      # a Refusal is falsey, so `if not check(...)` reads naturally
        return False


def position_size(equity: float, risk_percent: float, stop_distance_price: float,
                  value_per_price_unit_per_lot: float) -> Optional[float]:
    """Lots to trade, from the stop DISTANCE IN PRICE. Returns None when it cannot be computed.

    `value_per_price_unit_per_lot` is what one full price unit of movement is worth on one lot — for gold
    at 100 oz per lot that is 100. Passing a pip value here instead is the classic way to size a position
    a hundred times too large.
    """
    if equity <= 0 or risk_percent <= 0:
        return None
    if stop_distance_price is None or stop_distance_price <= 0:
        return None
    if value_per_price_unit_per_lot is None or value_per_price_unit_per_lot <= 0:
        return None
    risk_money = equity * (risk_percent / 100.0)
    money_per_lot = stop_distance_price * value_per_price_unit_per_lot
    if money_per_lot <= 0:
        return None
    return risk_money / money_per_lot


def reward_risk(entry: float, stop: float, target: float) -> Optional[float]:
    """Target distance over stop distance. None when the geometry is impossible."""
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    return abs(target - entry) / risk


@dataclass
class Exposure:
    """What is open right now, so limits can be checked against reality rather than intent."""

    open_by_symbol: dict
    day_pnl_percent: float = 0.0
    drawdown_percent: float = 0.0

    def open_total(self) -> int:
        return int(sum(self.open_by_symbol.values()))


def check_trade(symbol: str, entry: float, stop: float, target: float,
                exposure: Exposure, limits: Limits = Limits()):
    """True when the trade may be taken, or a Refusal carrying the reason it may not.

    Named `check_trade`, not `check`: `tradingview_chart_worker.check` already exists.

    The order is deliberate: the cheapest and most absolute checks first, so the reason recorded is the
    most fundamental one rather than whichever happened to be tested last.
    """
    risk = abs(entry - stop)
    if risk <= 0:
        return Refusal("stop is at the entry price, so the trade has no risk unit")

    if exposure.drawdown_percent >= limits.max_drawdown_percent:
        return Refusal(f"drawdown {exposure.drawdown_percent:.2f}% at or past the "
                       f"{limits.max_drawdown_percent:.2f}% halt",
                       {"drawdown_percent": exposure.drawdown_percent})

    if exposure.day_pnl_percent <= -abs(limits.daily_loss_percent):
        return Refusal(f"daily loss {exposure.day_pnl_percent:.2f}% past the "
                       f"-{limits.daily_loss_percent:.2f}% stop",
                       {"day_pnl_percent": exposure.day_pnl_percent})

    if exposure.open_by_symbol.get(symbol, 0) >= limits.max_open_per_symbol:
        return Refusal(f"already holding {symbol}; one open trade per asset",
                       {"open": exposure.open_by_symbol.get(symbol, 0)})

    if exposure.open_total() >= limits.max_open_total:
        return Refusal(f"{exposure.open_total()} positions open, limit {limits.max_open_total}")

    rr = reward_risk(entry, stop, target)
    if rr is None or rr < limits.min_reward_risk:
        return Refusal(f"reward:risk {rr if rr is None else round(rr, 2)} below the "
                       f"{limits.min_reward_risk} minimum", {"reward_risk": rr})

    return True


# ==================================================== STEP 3: spec-aware sizing, and no silent clamping
#
# `position_size` above is unchanged and stays the function existing callers use. What follows is a
# SECOND path that knows the instrument's contract specification, and it exists because the first one
# takes `value_per_price_unit_per_lot` as an argument — which means the caller works it out, and a
# caller working out a contract size is exactly how a wrong one gets used confidently.
#
# WHAT THIS LAYER IS AND IS NOT FOR
# --------------------------------
# It answers ONE question: *can this proposed trade be sized and accepted under the risk rules?* It has
# no opinion on whether the setup is any good — that is the signal engine's job — and none on how the
# order fills, which is the execution engine's. Mixing those is how a risk limit ends up silently
# deciding a strategy's win rate.
#
# THE ROUNDING POLICY, STATED EXPLICITLY BECAUSE IT MUST BE
# --------------------------------------------------------
# A broker trades in steps of `lot_step`, so a computed 0.0137 lots is not tradable. Turning it quietly
# into 0.01 is the thing this module must never do, because the trade then carries a risk nobody asked
# for and the ledger records the figure that was requested rather than the one taken.
#
# So the policy is NAMED, DEFAULTED and REPORTED:
#
#   rounding="down"   (default) floor to the step grid. Chosen because flooring can only ever risk
#                     LESS than the target, never more — the error is always in the safe direction —
#                     and `actual_risk` and `risk_error` are returned every time so the difference is
#                     a number the caller reads rather than an assumption they inherit.
#   rounding="exact"  refuse unless the raw size already sits on the step grid. For callers who would
#                     rather not trade than trade a size they did not ask for.
#
# "Exactly 1 %" is therefore a claim this module will not make on its own behalf. It reports
# `target_risk`, `actual_risk` and `risk_error`, and lets the reader see that 0.01 lots of gold risks
# 99.87 rather than 100.00 — which is not 1 %, and saying so is the whole point.

from engine import instrument_specs as _specs                    # noqa: E402  (additive)
from engine.instrument_specs import InstrumentSpec, SpecError     # noqa: E402
from engine.reject_codes import RejectCode, named as _named_code  # noqa: E402

#: The rounding policies this module will apply. Anything else is a refusal, not a silent default.
ROUNDING_POLICIES = ("down", "exact")


@dataclass
class RiskRejection:
    """A risk refusal that can be counted, read, AND reconstructed.

    Three fields because each answers a different question and none substitutes for the others:

    * `code` — countable. A funnel groups on this.
    * `message` — readable. A human finds out what happened from this.
    * `inputs` — reconstructable. Every number the calculation used, so the refusal can be re-derived
      months later without the code that produced it. A refusal you cannot reproduce is an anecdote.
    """

    code: RejectCode
    message: str
    inputs: dict = field(default_factory=dict)

    def __bool__(self) -> bool:      # falsey, so `if not size_position(...)` reads naturally
        return False

    def as_dict(self) -> dict:
        return {"code": str(self.code), "message": self.message, "inputs": dict(self.inputs)}


@dataclass
class PositionSizing:
    """A sized position and every number needed to audit how it was reached.

    Nothing here is internal state for its own sake: each field is one that, if wrong, would make the
    size wrong while the arithmetic still looked correct.
    """

    instrument: str
    currency: str                       # the instrument's profit currency
    account_currency: str
    equity: float
    risk_percent: float
    target_risk: float                  # equity * risk_percent / 100, in the ACCOUNT currency
    entry: float
    stop: float
    stop_distance: float                # in price
    tick_size: float
    tick_value: float                   # per lot, in the ACCOUNT currency
    contract_size: float
    raw_position_size: float            # before the step grid
    final_position_size: float          # what would actually be sent
    actual_risk: float                  # what the final size really loses at the stop
    risk_error: float                   # actual_risk - target_risk; negative means risking less
    rounding: str
    conversion_rate: Optional[float] = None
    ticks_to_stop: Optional[float] = None

    @property
    def risk_error_percent_of_target(self) -> Optional[float]:
        """The rounding error as a share of the target, which is the honest way to judge it."""
        if not self.target_risk:
            return None
        return round(self.risk_error / self.target_risk * 100.0, 4)

    @property
    def exact(self) -> bool:
        """True only when the step grid cost nothing at all. Rarely true, and never assumed."""
        return abs(self.risk_error) < 1e-9

    def __bool__(self) -> bool:
        return True

    def as_dict(self) -> dict:
        return {"instrument": self.instrument, "currency": self.currency,
                "account_currency": self.account_currency,
                "equity": self.equity, "risk_percent": self.risk_percent,
                "target_risk": round(self.target_risk, 6),
                "entry": self.entry, "stop": self.stop,
                "stop_distance": round(self.stop_distance, 10),
                "ticks_to_stop": self.ticks_to_stop,
                "tick_size": self.tick_size, "tick_value": self.tick_value,
                "contract_size": self.contract_size,
                "raw_position_size": round(self.raw_position_size, 10),
                "final_position_size": self.final_position_size,
                "actual_risk": round(self.actual_risk, 6),
                "risk_error": round(self.risk_error, 6),
                "risk_error_percent_of_target": self.risk_error_percent_of_target,
                "exact": self.exact, "rounding": self.rounding,
                "conversion_rate": self.conversion_rate}


def _floor_to_step(value: float, step: float) -> float:
    """Floor to the step grid, then round to the step's own precision.

    The second half matters: 0.13 / 0.01 in binary floating point is 12.999999999999998, so a bare
    floor divide gives 0.12 and every position is one step too small. Rounding to the step's decimal
    places first is what makes the grid arithmetic mean what it says.
    """
    if step <= 0:
        return value
    steps = round(value / step, 9)
    import math

    return round(math.floor(steps) * step, 10)


def size_position(*, symbol: str, equity: float, entry: float, stop: float,
                  risk_percent: float = 1.0, spec: Optional[InstrumentSpec] = None,
                  specs_path=None, conversion_rate: Optional[float] = None,
                  rounding: str = "down", limits: Optional[Limits] = None):
    """Size a position from the instrument's own contract specification, or refuse and say why.

    Returns a `PositionSizing` carrying the full audit, or a `RiskRejection` carrying code, message and
    the inputs needed to reconstruct the failure. Never returns a bare number, and never silently
    changes an unusable size into a usable one.

    `risk_percent` defaults to 1.00 %, which is the owner's standing figure. `limits.risk_percent` is
    the CEILING: asking for more is `REJECT_RISK_TOO_LARGE` rather than being quietly reduced to it.

    The order of the checks is deliberate — most fundamental first — so the code recorded is the
    deepest reason rather than whichever test happened to run last. A missing instrument spec is not
    reported as a stop problem.
    """
    limits = limits or Limits()
    base = {"symbol": symbol, "equity": equity, "entry": entry, "stop": stop,
            "risk_percent": risk_percent, "rounding": rounding}

    if rounding not in ROUNDING_POLICIES:
        return RiskRejection(RejectCode.RISK,
                             f"rounding policy {rounding!r} is not one of {ROUNDING_POLICIES}; a "
                             "policy is never guessed",
                             {**base, "policies": list(ROUNDING_POLICIES)})

    # 1. the instrument. An unknown symbol or an incoherent spec stops everything, because every
    #    number below depends on it and a default contract size sizes every trade confidently wrong.
    try:
        spec = spec if spec is not None else _specs.instrument_for(symbol, specs_path)
        _specs.validate(spec)
    except SpecError as exc:
        return RiskRejection(_named_code(exc.code), exc.message, {**base, **exc.inputs})

    # 2. the risk inputs themselves.
    if equity is None or not equity > 0 or equity != equity:
        return RiskRejection(RejectCode.RISK, f"equity {equity!r} is not a positive number", base)
    if risk_percent is None or not risk_percent > 0 or risk_percent != risk_percent:
        return RiskRejection(RejectCode.RISK, f"risk_percent {risk_percent!r} is not positive", base)
    if risk_percent > limits.risk_percent:
        return RiskRejection(RejectCode.RISK_TOO_LARGE,
                             f"requested {risk_percent}% per trade, above the {limits.risk_percent}% "
                             "ceiling. The ceiling is not quietly applied in its place.",
                             {**base, "limit_risk_percent": limits.risk_percent})

    # 3. the geometry. No stop distance means no risk unit, so there is nothing to size against.
    if entry is None or stop is None or entry != entry or stop != stop:
        return RiskRejection(RejectCode.INVALID_STOP,
                             "entry and stop must both be numbers to size a position", base)
    stop_distance = abs(float(entry) - float(stop))
    if not stop_distance > 0:
        return RiskRejection(RejectCode.INVALID_STOP,
                             f"stop distance is {stop_distance}; the stop is at the entry, so the "
                             "trade has no risk unit and no size can be computed",
                             {**base, "stop_distance": stop_distance})

    # 4. money per tick, in the ACCOUNT currency. This is where a conversion would be needed, and
    #    where a missing one is refused rather than assumed to be 1.0.
    try:
        tick_value = spec.value_per_tick_per_lot(conversion_rate)
    except SpecError as exc:
        return RiskRejection(_named_code(exc.code), exc.message, {**base, **exc.inputs})

    target_risk = float(equity) * (float(risk_percent) / 100.0)
    ticks_to_stop = stop_distance / float(spec.tick_size)
    money_per_lot = ticks_to_stop * tick_value
    if not money_per_lot > 0:
        return RiskRejection(RejectCode.INVALID_LOT,
                             "one lot would lose nothing at this stop, so no size can be computed",
                             {**base, "ticks_to_stop": ticks_to_stop, "tick_value": tick_value})

    raw = target_risk / money_per_lot
    if raw != raw or raw in (float("inf"), float("-inf")) or not raw > 0:
        return RiskRejection(RejectCode.INVALID_LOT,
                             f"computed position size {raw!r} is not a finite positive number",
                             {**base, "target_risk": target_risk, "money_per_lot": money_per_lot})

    # 5. the step grid — explicit, never silent.
    if rounding == "exact":
        on_grid = abs(raw - _floor_to_step(raw, spec.lot_step)) < 1e-12
        if not on_grid:
            return RiskRejection(
                RejectCode.INVALID_LOT,
                f"{raw:.6f} lots is not on the {spec.lot_step} step grid and rounding='exact' was "
                "asked for, so the trade is refused rather than resized",
                {**base, "raw_position_size": raw, "lot_step": spec.lot_step,
                 "target_risk": target_risk, "money_per_lot": money_per_lot})
        final = raw
    else:
        final = _floor_to_step(raw, spec.lot_step)

    # 6. the broker's own volume limits. NEITHER is clamped: a size below the minimum means this
    #    account cannot take this trade at this risk, and saying so is the useful answer. Clamping up
    #    to min_lot would risk MORE than asked, which is the one direction that must never happen by
    #    accident.
    if final < spec.min_lot:
        would_risk = final * money_per_lot
        at_min = spec.min_lot * money_per_lot
        return RiskRejection(
            RejectCode.MIN_LOT,
            f"{raw:.6f} lots rounds to {final} on the {spec.lot_step} grid, below the broker's minimum "
            f"{spec.min_lot}. Trading the minimum instead would risk {at_min:.2f} "
            f"{spec.account_currency} against a target of {target_risk:.2f}, so it is refused rather "
            "than raised.",
            {**base, "raw_position_size": raw, "rounded_position_size": final,
             "min_lot": spec.min_lot, "lot_step": spec.lot_step, "target_risk": target_risk,
             "risk_at_rounded_size": would_risk, "risk_at_min_lot": at_min,
             "money_per_lot": money_per_lot, "stop_distance": stop_distance,
             "tick_value": tick_value, "tick_size": spec.tick_size,
             "contract_size": spec.contract_size})

    if final > spec.max_lot:
        return RiskRejection(
            RejectCode.MAX_LOT,
            f"{raw:.6f} lots exceeds the broker's maximum {spec.max_lot}. Reducing to the maximum "
            "would risk less than asked, which is a different trade, so it is refused rather than cut.",
            {**base, "raw_position_size": raw, "rounded_position_size": final,
             "max_lot": spec.max_lot, "target_risk": target_risk,
             "risk_at_max_lot": spec.max_lot * money_per_lot, "money_per_lot": money_per_lot})

    actual_risk = final * money_per_lot
    return PositionSizing(
        instrument=spec.symbol, currency=spec.currency, account_currency=spec.account_currency,
        equity=float(equity), risk_percent=float(risk_percent), target_risk=target_risk,
        entry=float(entry), stop=float(stop), stop_distance=stop_distance,
        tick_size=float(spec.tick_size), tick_value=float(tick_value),
        contract_size=float(spec.contract_size), raw_position_size=raw, final_position_size=final,
        actual_risk=actual_risk, risk_error=actual_risk - target_risk, rounding=rounding,
        conversion_rate=conversion_rate, ticks_to_stop=ticks_to_stop)


def check_trade_coded(symbol: str, entry: float, stop: float, target: float,
                      exposure: "Exposure", limits: Optional[Limits] = None):
    """`check_trade`, but the refusal carries a deterministic code as well as its reason.

    A separate function rather than a changed return type, because `check_trade` is called by the bar
    loop and its `Refusal` is already recorded verbatim in ledgers. Same order of checks, same reasons,
    so the two can never disagree about whether a trade is allowed — only about how much detail they
    give about the refusal.
    """
    limits = limits or Limits()
    verdict = check_trade(symbol, entry, stop, target, exposure, limits)
    if verdict is True:
        return True

    reason = verdict.reason
    inputs = {"symbol": symbol, "entry": entry, "stop": stop, "target": target,
              "open_by_symbol": dict(exposure.open_by_symbol),
              "day_pnl_percent": exposure.day_pnl_percent,
              "drawdown_percent": exposure.drawdown_percent,
              **(verdict.detail or {})}
    for needle, code in (("drawdown", RejectCode.MAX_DRAWDOWN),
                         ("daily loss", RejectCode.DAILY_LOSS),
                         ("one open trade per asset", RejectCode.MAX_EXPOSURE),
                         ("positions open", RejectCode.MAX_EXPOSURE),
                         ("reward:risk", RejectCode.REWARD_RISK),
                         ("no risk unit", RejectCode.INVALID_STOP)):
        if needle in reason:
            return RiskRejection(code, reason, inputs)
    return RiskRejection(RejectCode.RISK, reason, inputs)
