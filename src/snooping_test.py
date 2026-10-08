"""Which candidates beat buy-and-hold AFTER accounting for the search that found them.

WHY THIS EXISTS
---------------
The lab has searched 815,989 candidates and not one of its 1,972 book entries clears the 0.95 deflated
Sharpe bar. That is not a coincidence and it is not the bar being wrong: the deflated Sharpe charges a
candidate for every trial ever run on its market - 97,133 on XAUUSD:4h alone - so the more the lab
searches, the harder it becomes for anything to pass. A search that defeats its own evidence test
cannot find anything, however long it runs.

Hansen's Superior Predictive Ability test answers the same question a different way, and it was built
for exactly this situation: thousands of technical trading rules tested on ONE price series, compared
against a benchmark, with the data-snooping bias removed. Two properties matter here.

  * Its benchmark is buy-and-hold, which this project already uses as its promotion control.
  * It is explicitly LESS sensitive to the inclusion of poor and irrelevant alternatives than White's
    Reality Check. The lab's problem is that it is charged in full for hundreds of thousands of
    obviously bad candidates. SPA does not punish a good rule for the company it kept.

`StepM` goes further and names WHICH candidates beat the benchmark, controlling the family-wise error
rate across the whole set, rather than returning a single yes/no for the best one.

WHAT THIS DOES NOT DO
---------------------
It does not replace the 0.95 deflated Sharpe bar and it does not promote anything. The owner's standing
rule is that the bar never moves, and it has not. This is a second, independent piece of evidence
answering a question the bar cannot: not "did everything fail" but "which of these survive the search".

HOW A CANDIDATE AND THE BENCHMARK ARE MADE COMPARABLE
-----------------------------------------------------
SPA compares two loss series of equal length, bar for bar. A strategy trades irregularly, so its
returns are placed on the bars where they were REALISED - each trade's after-cost net return on its own
exit bar, zero on every bar it was flat - over exactly the holdout bars. The benchmark is the per-bar
return of simply holding the instrument over those same bars. Both series then cover the same period at
the same frequency, and their means are each side's total return over the holdout.

arch takes LOSSES, where lower is better, so every return series is negated on the way in. That
convention is pinned by a test rather than trusted: fed raw returns instead of losses, the verdict
flips, which is the kind of error that produces confident numbers pointing the wrong way.

    python -m src.snooping_test --symbol XAUUSD --timeframe 4h

Research only. Reads bars, scores declared specs, writes nothing, places no orders.
"""
from __future__ import annotations

import argparse
import json
from typing import Optional

import numpy as np

from . import strategy_lab as lab

# arch is an optional dependency, like MT5 and voice: a missing one reports a status, never a 500.
try:                                             # pragma: no cover - import guard
    from arch.bootstrap import SPA, StepM
    ARCH_AVAILABLE = True
except Exception:                                # pragma: no cover
    SPA = StepM = None                           # type: ignore[assignment]
    ARCH_AVAILABLE = False

DEFAULT_REPS = 1000          # arch's own default
DEFAULT_SIZE = 0.05          # family-wise error rate for StepM


def as_losses(returns) -> np.ndarray:
    """arch minimises loss, so a return series enters negated. Pinned by test, not by assumption."""
    return -np.asarray(returns, dtype=float)


def strategy_bar_returns(market: "lab.Market", spec: dict, split: str = "holdout") -> np.ndarray:
    """One fractional return per holdout bar: a trade's net result on its exit bar, zero elsewhere.

    Placed on the EXIT bar, found from the trade's exit_time rather than by adding bars_held to the
    entry index, because the entry fill is the bar after the signal and an off-by-one there would shift
    every trade against the benchmark.
    """
    rows = np.asarray(market.rows[split], dtype=int)
    series = np.zeros(len(rows), dtype=float)
    if len(rows) == 0:
        return series
    times = market.ind.times
    position = {str(t): i for i, t in enumerate(times.dt.strftime("%Y-%m-%d %H:%M"))}
    first, last = int(rows[0]), int(rows[-1])
    for trade in market.simulate(spec, split):
        index = position.get(str(trade.get("exit_time")))
        if index is None:
            index = min(last, int(trade["entry_idx"]) + int(trade.get("bars_held") or 0) + 1)
        index = min(max(index, first), last)
        series[index - first] += float(trade["net_pct"]) / 100.0
    return series


def benchmark_bar_returns(market: "lab.Market", split: str = "holdout") -> np.ndarray:
    """Per-bar return of holding the instrument across the same bars the strategy was judged on."""
    rows = np.asarray(market.rows[split], dtype=int)
    close = np.asarray(market.ind.c, dtype=float)
    if len(rows) == 0:
        return np.zeros(0, dtype=float)
    first, last = int(rows[0]), int(rows[-1])
    window = close[first:last + 1]
    out = np.zeros(len(window), dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        out[1:] = np.diff(window) / window[:-1]
    return np.nan_to_num(out, nan=0.0, posinf=0.0, neginf=0.0)


def superior_candidates(market: "lab.Market", specs: list, split: str = "holdout",
                        reps: int = DEFAULT_REPS, size: float = DEFAULT_SIZE,
                        seed: int = 20261008) -> dict:
    """SPA and StepM for a set of candidates against buy-and-hold on one market.

    Returns a plain dict so it can be stored and compared later. `spa_pvalue` is the consistent
    p-value: the probability that the BEST of these candidates beats buy-and-hold by luck alone, given
    how many were tried. `superior` names the candidates StepM keeps.
    """
    if not ARCH_AVAILABLE:
        return {"available": False,
                "reason": "the arch package is not installed (pip install arch); no test was run"}
    if not specs:
        return {"available": False, "reason": "no candidates given"}

    benchmark = benchmark_bar_returns(market, split)
    columns, labels, means = [], [], []
    for spec in specs:
        series = strategy_bar_returns(market, spec, split)
        if series.shape != benchmark.shape:       # pragma: no cover - guarded by construction
            continue
        columns.append(series)
        labels.append(str(spec.get("variant") or spec.get("description") or len(labels)))
        means.append(float(series.sum()))
    if not columns:
        return {"available": False, "reason": "no candidate produced a return series"}

    models = np.column_stack(columns)
    spa = SPA(as_losses(benchmark), as_losses(models), reps=reps, seed=seed)
    spa.compute()
    pvalues = {name: float(value) for name, value in spa.pvalues.items()}

    step = StepM(as_losses(benchmark), as_losses(models), size=size, reps=reps, seed=seed)
    step.compute()
    superior = []
    for chosen in step.superior_models:
        text = str(chosen)
        digits = "".join(ch for ch in text if ch.isdigit())
        if digits and int(digits) < len(labels):
            superior.append(labels[int(digits)])
        else:
            superior.append(text)

    return {
        "available": True,
        "market": market.key,
        "split": split,
        "bars": int(len(benchmark)),
        "candidates": len(labels),
        # ARITHMETIC SUM of per-bar simple returns, on both sides, which is what SPA compares
        # (it tests mean loss). It is NOT a compounded total return and will not match the
        # buy_and_hold_pct a backtest summary reports: on BTCUSD 4h this reads +11.71 % where the
        # compounded figure is -0.70 %. Both sides are summed the same way, so the comparison is
        # sound; only the label would mislead if it claimed to be a compounded return.
        "benchmark_sum_pct": round(100.0 * float(benchmark.sum()), 3),
        "spa_pvalue": pvalues.get("consistent"),
        "spa_pvalues": pvalues,
        "superior": superior,
        "size": size,
        "reps": reps,
        "sums_pct": {label: round(100.0 * total, 3) for label, total in zip(labels, means)},
        "note": ("SPA/StepM against buy-and-hold after data snooping. This does NOT promote anything "
                 "and does not replace the 0.95 deflated Sharpe bar."),
    }


def _market(symbol: str, timeframe: str) -> "lab.Market":
    from .mtf_data import load_bars

    bars = load_bars(symbol, timeframe, source="app")
    key = f"{symbol}:{timeframe}"
    return lab.Market(symbol, timeframe, bars,
                      boundaries=(lab.market_meta().get(key) or {}).get("boundaries"), swap=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Which candidates beat buy-and-hold after the search.")
    parser.add_argument("--symbol", default="XAUUSD")
    parser.add_argument("--timeframe", default="4h")
    parser.add_argument("--family", default="sweep_reclaim",
                        help="a key from direction_sweep.FAMILY_VARIANTS")
    parser.add_argument("--reps", type=int, default=DEFAULT_REPS)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if not ARCH_AVAILABLE:
        print("arch is not installed. pip install arch")
        return 1

    # The boundaries decide the holdout, and a cached one that disagrees with the registry would make
    # every number below a measurement against the wrong window. Refuse rather than report.
    mismatches = lab.market_meta_mismatches()
    if mismatches:
        print("SPLIT BOUNDARIES DISAGREE WITH THE REGISTRY. Refusing to report.")
        for row in mismatches[:5]:
            print(f"  {row}")
        return 1

    from . import direction_sweep
    families = direction_sweep.load_families()
    builder = families.get(args.family)
    if not callable(builder):
        print(f"unknown or unloadable family {args.family!r}; known: {sorted(families)}")
        return 1
    lab.ensure_family("sweep_reversal" if args.family == "sweep_reclaim" else args.family)

    market = _market(args.symbol, args.timeframe)
    specs = list(builder(args.symbol, args.timeframe))
    result = superior_candidates(market, specs, reps=args.reps)

    if args.json:
        print(json.dumps(result, indent=1, default=str))
        return 0
    if not result.get("available"):
        print(result.get("reason"))
        return 1
    print(f"{market.key}  {args.family}  {result['candidates']} candidates, "
          f"{result['bars']} holdout bars")
    print(f"  buy-and-hold, sum of bar returns: {result['benchmark_sum_pct']:+.2f}%  (arithmetic, not compounded)")
    print(f"  SPA consistent p-value          : {result['spa_pvalue']:.4f}  "
          f"({'at least one candidate beats it' if (result['spa_pvalue'] or 1) < 0.05 else 'no candidate beats it after the search'})")
    print(f"  StepM superior at size {result['size']}      : "
          f"{result['superior'] if result['superior'] else 'none'}")
    print()
    for label, total in sorted(result["sums_pct"].items(), key=lambda kv: -kv[1])[:8]:
        print(f"    {label:<34} {total:+8.2f}%")
    print()
    print("  " + result["note"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
