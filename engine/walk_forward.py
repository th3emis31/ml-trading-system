"""13. Walk-forward: train → validate → test, rolling windows, out-of-sample verification.

Delegates to the split boundaries a `Market` already owns (`search` / `validation` / `holdout`), because
those boundaries are STORED per market in the registry rather than recomputed per run. That matters more
than it sounds: a holdout whose start date moves between runs is not a holdout, and a candidate can be
re-tested until it passes one.

WHY THE SPLIT IS NOT THE WHOLE ANSWER
-------------------------------------
Three splits answer "did it work in periods selection never saw". They do NOT answer "did it survive the
number of things I tried", which is what the deflated Sharpe is for, and they do not answer "does the
timing carry information", which is what the permutation is for. This project has recorded results that
passed the splits and failed both of the others.

The standing bar, which is never to be lowered: deflated Sharpe ≥ 0.95, at least 100 closed holdout trades
(30 for a single rule strategy), drawdown within limits, and a control that actually traded and lost.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from src import strategy_lab as lab

SPLITS = ("search", "validation", "holdout")


def periods(market: lab.Market) -> Dict[str, str]:
    """The date range of each split, as stored for this market — print these above any result."""
    return dict((market.info.get("periods") or {}))


def evaluate_spec(market: lab.Market, spec: dict, with_holdout: bool = True) -> dict:
    """One spec across all three splits, with the holdout evaluated too.

    Named `evaluate_spec`, not `evaluate`: `tjl_scanner.evaluate` already exists.
    """
    record = lab.evaluate_candidate(market, spec, with_holdout=with_holdout)
    record["variant"] = spec.get("variant")
    return record


def evaluate_grid(market: lab.Market, specs: List[dict]) -> List[dict]:
    """A whole declared grid, with the deflated Sharpe charged for EVERY trial in it.

    The trial count is `len(specs)`, not the number that happened to produce trades. Charging only the
    survivors is how a sweep flatters itself.
    """
    records = []
    for spec in specs:
        try:
            records.append(evaluate_spec(market, spec))
        except Exception as exc:                        # noqa: BLE001 — one bad variant must not stop the grid
            records.append({"variant": spec.get("variant"), "error": f"{type(exc).__name__}: {exc}"[:120]})

    usable = [r for r in records if not r.get("error") and r.get("validation_sr") is not None]
    sr_variance = lab._variance([[r["validation_sr"], (r.get("validation") or {}).get("trades")]
                                 for r in usable]) if usable else 0.0
    for record in records:
        if not record.get("error"):
            record["holdout_verdict"] = lab.holdout_verdict(record, len(specs), sr_variance)
    return records


def positive_on_all_splits(records: List[dict]) -> List[str]:
    """Variants that made money in every period — consistency, before any bar is applied."""
    out = []
    for r in records:
        if r.get("error"):
            continue
        values = [(r.get(s) or {}).get("total_return_pct") for s in SPLITS]
        if all(v is not None and v > 0 for v in values):
            out.append(r.get("variant"))
    return out


def cleared_the_bar(records: List[dict]) -> List[str]:
    """Variants that passed the full holdout verdict, deflated Sharpe included."""
    return [r.get("variant") for r in records if (r.get("holdout_verdict") or {}).get("passed")]


def table(records: List[dict]) -> List[dict]:
    """One flat row per variant, in the order a reader needs: consistency, sample, then the verdict."""
    rows = []
    for r in records:
        if r.get("error"):
            rows.append({"variant": r.get("variant"), "error": r["error"]})
            continue
        holdout = r.get("holdout") or {}
        verdict = r.get("holdout_verdict") or {}
        rows.append({
            "variant": r.get("variant"),
            "search_pct": (r.get("search") or {}).get("total_return_pct"),
            "validation_pct": (r.get("validation") or {}).get("total_return_pct"),
            "holdout_pct": holdout.get("total_return_pct"),
            "holdout_trades": holdout.get("trades"),
            "ambiguous_exits": holdout.get("ambiguous_exits"),
            "expectancy_r": holdout.get("expectancy_r"),
            "deflated_sharpe": verdict.get("deflated_sharpe"),
            "passed": verdict.get("passed"),
            "insufficient_evidence": (holdout.get("trades") or 0) < 100,
        })
    return rows
