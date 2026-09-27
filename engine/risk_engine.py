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

from dataclasses import dataclass
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
