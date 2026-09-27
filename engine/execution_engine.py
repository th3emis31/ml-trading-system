"""09. Execution: entry price, spread, slippage, SL, TP1→TP3, partial close, break-even, trailing.

Delegates to `src.strategy_lab.simulate_orders`, and deliberately does not reimplement it, because that
function is the one thing in this whole system with known-answer tests behind it
(`tests/test_engine_truth.py`, 13 of them):

* a planted edge is found — 100 % win on a market built so every long wins
* a planted loss is reported as a loss, not flattered
* cost is charged ONCE per trade and equals the spread, checked per trade rather than on the compound
* entry is the NEXT bar's open, never the signal bar's close
* a signal on the last bar is not traded
* one position at a time

Writing a second execution model would mean re-earning all of that, and the two most expensive errors this
project has recorded came from exactly such re-implementations.

THREE CONVENTIONS THAT ARE CHOICES, NOT ARITHMETIC — stated here so a reader of any result knows them:

1. `cost_pct` is a FRACTION of price, not a percentage. 0.000115 is 0.0115 %. Reading it as a percentage
   charges 100x too much and turns every edge negative.
2. When one bar touches BOTH the stop and the target, the engine books the STOP. That is the safe choice
   but it is a choice, so `ambiguous_exits` is reported per run and a high count means the convention
   decided the trade rather than the market.
3. `total_return_pct` compounds each trade's raw price move on the whole account — a full-capital,
   unleveraged figure directly comparable to buy-and-hold. `expectancy_r` is the risk-scaled view, and it
   is the honest one for a strategy sized at a fixed risk per trade.
"""
from __future__ import annotations

from typing import List, Optional

from src import strategy_lab as lab


def market_for(symbol: str, timeframe: str, frame, boundaries: Optional[dict] = None,
               swap: bool = True) -> lab.Market:
    """A Market: the bars, the split boundaries, the cost model and the holding cost, in one object."""
    if boundaries is None:
        registry = lab.load_registry()
        boundaries = (registry["markets"].get(f"{symbol}:{timeframe}") or {}).get("boundaries")
    return lab.Market(symbol, timeframe, frame, boundaries=boundaries, swap=swap)


def run_trades(market: lab.Market, spec: dict, split: str = "holdout", orders=None) -> List[dict]:
    """Every trade a spec would have taken in one split, after spread and overnight swap.

    Named `run_trades`, not `simulate`: `edge_research.simulate` already exists and means its own thing.
    """
    return market.simulate(spec, split, orders)


def metrics(market: lab.Market, split: str, trades: List[dict]) -> dict:
    """The agreed metric set for a trade list, from NET (after-cost) returns.

    Named `metrics`, not `summarise`: `plan_journal.summarise` and `ea_week_check.summarise` both exist.
    """
    return market.summary(split, trades)


def control(market: lab.Market, spec: dict, split: str = "holdout") -> dict:
    """The inverse of a spec on the same bars — the control every result must beat.

    Two failure modes this is written to avoid, both of which have happened here:

    * a control that takes ZERO trades is not a control, it is a free pass. A bullish-only pattern
      inverted on a long-only pass produces nothing, and "beats its inverse" then passes for free.
    * a control that also MAKES money is worse than none: it says both directions paid, which is drift.

    The caller must check `trades` and `total_return_pct` before believing a comparison, which is what
    `beats_control` below does.
    """
    inverse = {**spec, "params": {**spec["params"], "inverse": True}}
    try:
        return market.summary(split, market.simulate(inverse, split))
    except Exception:                                  # noqa: BLE001
        return {}


def beats_control(result: dict, control_result: dict) -> bool:
    """True only when the control actually TRADED, LOST, and the strategy did better than it."""
    traded = (control_result.get("trades") or 0) > 0
    control_net = control_result.get("total_return_pct")
    if not traded or control_net is None or control_net >= 0:
        return False
    return (result.get("total_return_pct") or -999) > control_net


def permutation(market: lab.Market, spec: dict, split: str = "holdout", draws: int = 400) -> dict:
    """Does the TIMING carry information, or is this just a way of being in the market with this stop?

    Each draw fires the same number of trades, with the same long/short mix and the same risk distances,
    at randomly chosen bars in the same period, through the same stops, targets, costs and time exit. Only
    WHEN changes. A deflated Sharpe asks whether a result survives the number of things tried; this asks
    something deflation cannot — whether the entry moment matters at all.

    For a one-sided strategy in a trending market this is the control that means something, because
    flipping direction is guaranteed to lose and therefore proves nothing.
    """
    return lab.permutation_check(market, spec, split=split, draws=draws)
