"""14. Robustness: Monte Carlo, trade-order randomisation, slippage and spread stress, perturbation.

Four different questions, and they are not interchangeable. Reporting one and implying the others is how a
fragile result looks robust:

* **Trade-order randomisation** — the same trades in a different sequence. Answers "was the equity curve's
  shape luck?" It cannot answer whether the trades themselves were luck, because it reuses them.
* **Bootstrap resampling** — trades drawn with replacement. Answers "what would a different sample of this
  same edge have looked like?" The 5th percentile is the number worth quoting, not the mean.
* **Cost stress** — the same trades with a worse spread. Answers "how much of this is the cost assumption?"
  A result that dies at 1.5x spread was never a result.
* **Permutation** (in `execution_engine.permutation`) — the only one that re-simulates. Same trade count,
  same risk, random ENTRY BARS. It is the one that asks whether the timing carries information, and for a
  one-sided strategy in a trending market it is the only control that means anything.

The first three reuse the realised trades, so they inherit whatever the strategy actually did — including
its luck. Only the permutation goes back to the bars.
"""
from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np


def _nets(trades: List[dict]) -> np.ndarray:
    return np.array([t.get("net_pct", 0.0) / 100.0 for t in trades], dtype=float)


def _compound(returns: np.ndarray) -> float:
    equity = 1.0
    for r in returns:
        equity *= (1.0 + r)
    return (equity - 1.0) * 100.0


def _max_drawdown(returns: np.ndarray) -> float:
    equity, peak, worst = 1.0, 1.0, 0.0
    for r in returns:
        equity *= (1.0 + r)
        peak = max(peak, equity)
        if peak > 0:
            worst = max(worst, (peak - equity) / peak)
    return worst * 100.0


def shuffle_order(trades: List[dict], draws: int = 1000, seed: int = 20260927) -> Dict[str, float]:
    """The same trades in a different order. Tests the SHAPE of the curve, not the edge behind it.

    The total return is identical every time by construction — only the path changes — so the number that
    matters here is the drawdown distribution, not the return.
    """
    nets = _nets(trades)
    if len(nets) < 5:
        return {"available": False, "reason": f"only {len(nets)} trades; too few to shuffle"}
    rng = np.random.default_rng(seed)
    drawdowns = [_max_drawdown(rng.permutation(nets)) for _ in range(draws)]
    return {"available": True, "draws": draws,
            "real_drawdown_pct": round(_max_drawdown(nets), 3),
            "median_drawdown_pct": round(float(np.median(drawdowns)), 3),
            "p95_drawdown_pct": round(float(np.percentile(drawdowns, 95)), 3),
            "worst_drawdown_pct": round(float(np.max(drawdowns)), 3),
            "note": "same trades, different order; total return is unchanged by construction"}


def bootstrap(trades: List[dict], draws: int = 1000, seed: int = 20260927) -> Dict[str, float]:
    """Resample the trades WITH replacement. The 5th percentile is the honest headline, not the mean."""
    nets = _nets(trades)
    if len(nets) < 5:
        return {"available": False, "reason": f"only {len(nets)} trades; too few to resample"}
    rng = np.random.default_rng(seed)
    totals = [_compound(rng.choice(nets, size=len(nets), replace=True)) for _ in range(draws)]
    totals = np.array(totals, dtype=float)
    return {"available": True, "draws": draws,
            "real_net_pct": round(_compound(nets), 3),
            "p05_net_pct": round(float(np.percentile(totals, 5)), 3),
            "median_net_pct": round(float(np.median(totals)), 3),
            "p95_net_pct": round(float(np.percentile(totals, 95)), 3),
            "share_losing": round(float(np.mean(totals < 0)), 4)}


def cost_stress(trades: List[dict], extra_cost_pct_of_price: float = 0.0001) -> Dict[str, float]:
    """Charge every trade an extra slice of the entry price and see what survives.

    `extra_cost_pct_of_price` is a FRACTION, not a percentage: 0.0001 is 0.01 %. That unit is the single
    most expensive mistake recorded in this project's history — read as a percentage it charges 100x too
    much and turns every edge negative.
    """
    nets = _nets(trades)
    if not len(nets):
        return {"available": False, "reason": "no trades"}
    stressed = nets - float(extra_cost_pct_of_price)
    return {"available": True,
            "extra_cost_fraction": extra_cost_pct_of_price,
            "extra_cost_percent_of_price": round(extra_cost_pct_of_price * 100, 5),
            "real_net_pct": round(_compound(nets), 3),
            "stressed_net_pct": round(_compound(stressed), 3),
            "still_positive": bool(_compound(stressed) > 0)}


def survives(trades: List[dict], *, min_p05_pct: float = 0.0,
             max_share_losing: float = 0.25, stress_fraction: float = 0.0001) -> Dict[str, object]:
    """All three reused-trade tests at once, with an explicit verdict and the reason for it.

    Deliberately NOT a single number. A strategy can pass the bootstrap and die under cost stress, and
    collapsing that into one score hides exactly the thing worth knowing.
    """
    boot = bootstrap(trades)
    shuffled = shuffle_order(trades)
    stressed = cost_stress(trades, stress_fraction)
    if not boot.get("available"):
        return {"verdict": "insufficient", "why": boot.get("reason")}

    failures = []
    if boot["p05_net_pct"] <= min_p05_pct:
        failures.append(f"5th percentile {boot['p05_net_pct']}% is not above {min_p05_pct}%")
    if boot["share_losing"] > max_share_losing:
        failures.append(f"{boot['share_losing']:.0%} of resamples lose money")
    if not stressed.get("still_positive"):
        failures.append(f"negative at +{stressed['extra_cost_percent_of_price']}% extra cost")
    return {"verdict": "robust" if not failures else "fragile",
            "why": "; ".join(failures) or "cleared the bootstrap, the shuffle and the cost stress",
            "bootstrap": boot, "shuffle": shuffled, "cost_stress": stressed}
