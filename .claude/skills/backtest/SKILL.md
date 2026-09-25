---
name: backtest
description: Run or build a leak-free, cost-aware backtest and record comparable metrics against the baseline. Use when the user says backtest, test the strategy, historical performance, walk-forward, or out-of-sample.
---

# Backtest protocol

Before running anything, locate the existing backtest code (`grep -rn "backtest\|walk_forward\|out_of_sample"`).
Extend it; do not write a second engine.

## Checklist (every backtest must satisfy all)
1. **Data**: source, symbol(s), timeframe, date range, and bar count stated. Gaps and
   duplicate timestamps checked.
2. **No lookahead**: signals at bar *t* use only data up to *t*. Fills happen at *t+1* open
   (or worse), never at the signal bar's close. Scalers/indicators fitted on training data only.
3. **Costs**: spread, commission, slippage from the single cost config. Report gross and net.
4. **Risk**: position size from the sizing function; max concurrent positions and max
   daily loss enforced as in live.
5. **Split**: walk-forward (preferred) or fixed train/test with the test period after the
   train period. Report only out-of-sample results as "the result".
6. **Metrics**: trades, win rate, profit factor, expectancy, max drawdown, Sharpe/Sortino,
   CAGR, average R, exposure, longest losing streak.
7. **Evidence threshold**: < 100 test trades → label "insufficient evidence".
8. **Reproducibility**: seed, commit hash, config, feature list recorded.

## Steps
1. Read the strategy doc in `strategies/<name>.md` (create it with `/strategy` if missing).
2. Record the current baseline from `.claude/memory/BASELINE.md`.
3. Run the backtest through the project's existing entrypoint (add flags, do not fork it).
4. Write results to `.claude/memory/BASELINE.md` as a new dated row with the commit hash.
5. Compare with the previous row and state clearly: better / worse / not significant.
6. Never tune parameters on the test period. If you sweep, sweep on train and report test.

## Engines in this repository (live app in C:\Users\th_em)

- Current live strategy: `src/walkforward_backtest.py` (`run_walkforward_backtest`),
  UI at `/pipeline/training`, API `/api/pipeline/backtest/run` (symbol/range or `ALL`),
  every run appended to `data/backtest_runs.json`. Costs: `BACKTEST_COSTS`.
- Research strategies: `src/edge_research.py` (triple-barrier, validation/holdout split,
  baselines, pass criteria). Use `/edge-research`.
- `src/backtest.py` is the original synthetic-data demo: do not use it for results.

## Acceptance

How to tell the output is actually correct. Adjudicated by something other than the model.

```acceptance
appended: .claude/memory/BASELINE.md
number: trades >= 100
run: python -m pytest -q tests/test_signal_parity.py
ask: does the reported window sit OUTSIDE the period the parameters were chosen on?
```

The `ask` is not a formality. On 24 September 2026 a 12x improvement and a +417 gold result both
came from windows the parameters were fitted on, and both had to be withdrawn.
