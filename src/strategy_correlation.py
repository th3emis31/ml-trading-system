"""Are these candidates different trades, or the same trade wearing different names?

WHY THIS EXISTS
---------------
The strategy book holds 1,972 entries and nothing has ever measured whether any two of them are the
same trade. 84 % are `ema_pullback`. A book of near-identical rules is not a book of edges, it is one
edge counted 1,661 times, and every diversification argument built on it is false.

It also changes what the evidence bars mean. The lab judges each candidate ALONE against a 0.95
deflated Sharpe, which almost nothing clears. The standard alternative is a portfolio of weaker but
genuinely uncorrelated rules, where Sharpe scales with the square root of the number of INDEPENDENT
strategies. That scaling is real but strictly bounded: even a small positive average correlation caps
the achievable improvement, so the number that matters is not how many candidates there are but how
uncorrelated they actually are. Measuring it is the first honest step, and the lab has never taken it.

WHY DRAWDOWN CORRELATION, NOT JUST RETURN CORRELATION
-----------------------------------------------------
Two profitable strategies measured over a long backtest both drift up and to the right, so the
correlation of their equity curves reads high whether or not they trade alike. Correlating their
RUNNING DRAWDOWNS asks the question that matters to someone holding both: do they hurt at the same
time? Both are reported here, because they answer different questions and the gap between them is
itself informative.

    python -m src.strategy_correlation --symbol XAUUSD --timeframe 4h --family sweep_reclaim

Research only. Reads bars, scores declared specs, writes nothing, places no orders.
"""
from __future__ import annotations

import argparse
import json
from typing import Optional

import numpy as np

from . import strategy_lab as lab
from .snooping_test import benchmark_bar_returns, strategy_bar_returns


def running_drawdown(returns) -> np.ndarray:
    """Drawdown from the running peak of the cumulative return, at every bar."""
    equity = np.cumsum(np.asarray(returns, dtype=float))
    peak = np.maximum.accumulate(equity)
    return equity - peak                      # zero at a new high, negative below it


def _corr(matrix: np.ndarray) -> np.ndarray:
    """Pearson correlation across columns, with zero-variance columns reported as nan not 1.0."""
    out = np.full((matrix.shape[1], matrix.shape[1]), np.nan, dtype=float)
    std = matrix.std(axis=0)
    good = np.flatnonzero(std > 0)
    if len(good) > 1:
        sub = np.corrcoef(matrix[:, good], rowvar=False)
        for a, i in enumerate(good):
            for b, j in enumerate(good):
                out[i, j] = sub[a, b]
    for i in range(matrix.shape[1]):
        if std[i] > 0:
            out[i, i] = 1.0
    return out


def _upper_mean(matrix: np.ndarray) -> Optional[float]:
    n = matrix.shape[0]
    if n < 2:
        return None
    values = [matrix[i, j] for i in range(n) for j in range(i + 1, n) if np.isfinite(matrix[i, j])]
    return float(np.mean(values)) if values else None


def effective_independent_count(mean_correlation: Optional[float], n: int) -> Optional[float]:
    """How many genuinely independent strategies `n` correlated ones are worth.

    For n equally weighted strategies with average pairwise correlation r, the portfolio variance
    reduction is equivalent to n / (1 + (n - 1) * r) independent ones. At r = 0 that is n; at r = 1 it
    is 1. It is the honest headline number for a book: 1,972 entries that all trade alike are worth
    one, and no amount of counting changes that.
    """
    if mean_correlation is None or n < 1:
        return None
    denominator = 1.0 + (n - 1) * float(mean_correlation)
    if denominator <= 0:
        return None
    return float(n / denominator)


def correlation_report(market: "lab.Market", specs: list, split: str = "holdout") -> dict:
    """Return and drawdown correlation across a set of candidates, plus the equal-weight portfolio."""
    if not specs:
        return {"available": False, "reason": "no candidates given"}

    labels, columns = [], []
    for spec in specs:
        series = strategy_bar_returns(market, spec, split)
        if not np.any(series):
            continue                           # a candidate that never traded says nothing about anyone
        labels.append(str(spec.get("variant") or spec.get("description") or len(labels)))
        columns.append(series)
    if len(columns) < 2:
        return {"available": False,
                "reason": f"need at least two candidates that traded; got {len(columns)}"}

    returns = np.column_stack(columns)
    drawdowns = np.column_stack([running_drawdown(c) for c in columns])
    r_corr, d_corr = _corr(returns), _corr(drawdowns)
    mean_r, mean_d = _upper_mean(r_corr), _upper_mean(d_corr)

    # The equal-weight portfolio of everything here, on the same bars.
    portfolio = returns.mean(axis=1)
    benchmark = benchmark_bar_returns(market, split)
    best = int(np.argmax(returns.sum(axis=0)))

    def sharpe(series):
        series = np.asarray(series, dtype=float)
        sd = series.std(ddof=1)
        return float(series.mean() / sd * np.sqrt(len(series))) if sd > 0 else None

    pairs = []
    n = len(labels)
    for i in range(n):
        for j in range(i + 1, n):
            if np.isfinite(r_corr[i, j]):
                pairs.append((float(r_corr[i, j]), labels[i], labels[j]))
    pairs.sort(key=lambda row: -row[0])

    return {
        "available": True,
        # getattr, not market.key: the report is a pure function of the return streams, so it must
        # be callable with a stub market in a test without the whole thing falling over on a label.
        "market": getattr(market, "key", None),
        "split": split,
        "candidates": n,
        "bars": int(returns.shape[0]),
        "mean_return_correlation": None if mean_r is None else round(mean_r, 4),
        "mean_drawdown_correlation": None if mean_d is None else round(mean_d, 4),
        "effective_independent": None if mean_r is None else round(
            effective_independent_count(mean_r, n) or 0.0, 2),
        "most_correlated_pairs": [{"correlation": round(c, 4), "a": a, "b": b} for c, a, b in pairs[:5]],
        "least_correlated_pairs": [{"correlation": round(c, 4), "a": a, "b": b} for c, a, b in pairs[-5:]],
        "portfolio_sum_pct": round(100.0 * float(portfolio.sum()), 3),
        "best_single_sum_pct": round(100.0 * float(returns[:, best].sum()), 3),
        "best_single": labels[best],
        "benchmark_sum_pct": round(100.0 * float(benchmark.sum()), 3),
        "portfolio_sharpe": sharpe(portfolio),
        "best_single_sharpe": sharpe(returns[:, best]),
        "note": ("Sums are arithmetic over per-bar returns, not compounded. Equal weight, no sizing, "
                 "no rebalancing rule. This measures overlap; it promotes nothing."),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="How much do these candidates overlap?")
    parser.add_argument("--symbol", default="XAUUSD")
    parser.add_argument("--timeframe", default="4h")
    parser.add_argument("--family", default="sweep_reclaim")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    mismatches = lab.market_meta_mismatches()
    if mismatches:
        print("SPLIT BOUNDARIES DISAGREE WITH THE REGISTRY. Refusing to report.")
        for row in mismatches[:5]:
            print(f"  {row}")
        return 1

    from . import direction_sweep
    from .mtf_data import load_bars
    families = direction_sweep.load_families()
    builder = families.get(args.family)
    if not callable(builder):
        print(f"unknown family {args.family!r}; known: {sorted(families)}")
        return 1
    lab.ensure_family("sweep_reversal" if args.family == "sweep_reclaim" else args.family)

    key = f"{args.symbol}:{args.timeframe}"
    bars = load_bars(args.symbol, args.timeframe, source="app")
    market = lab.Market(args.symbol, args.timeframe, bars,
                        boundaries=(lab.market_meta().get(key) or {}).get("boundaries"), swap=True)
    report = correlation_report(market, list(builder(args.symbol, args.timeframe)))

    if args.json:
        print(json.dumps(report, indent=1, default=str))
        return 0
    if not report.get("available"):
        print(report.get("reason"))
        return 1
    print(f"{report['market']}  {args.family}  {report['candidates']} candidates that traded, "
          f"{report['bars']} bars")
    print(f"  mean return correlation    : {report['mean_return_correlation']}")
    print(f"  mean DRAWDOWN correlation  : {report['mean_drawdown_correlation']}   "
          f"(do they hurt at the same time?)")
    print(f"  effective independent count: {report['effective_independent']} of {report['candidates']}")
    print()
    print(f"  best single   {report['best_single']:<30} {report['best_single_sum_pct']:+8.2f}%  "
          f"sharpe {report['best_single_sharpe']}")
    print(f"  equal weight portfolio{'':<24} {report['portfolio_sum_pct']:+8.2f}%  "
          f"sharpe {report['portfolio_sharpe']}")
    print(f"  buy and hold{'':<34} {report['benchmark_sum_pct']:+8.2f}%")
    print()
    print("  most correlated pairs:")
    for row in report["most_correlated_pairs"]:
        print(f"    {row['correlation']:+.3f}  {row['a']}  vs  {row['b']}")
    print("  least correlated pairs:")
    for row in report["least_correlated_pairs"]:
        print(f"    {row['correlation']:+.3f}  {row['a']}  vs  {row['b']}")
    print()
    print("  " + report["note"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
