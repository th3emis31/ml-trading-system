# The reclaim candle with a distant level and a runner exit

**Status: research only. Never trades. No live input, preset, demo strategy or expert changes.**

Declared 8 October 2026 **before the run**, after the owner sent three more 4H photographs with the
words *"I can show 1000 pictures with all profitable is the best strategy for 4H but you still
working wrong"*.

## Why a second grid at all

The first reclaim run (18 variants, same day) produced the best result this project has measured on
any rule: 8 of 18 variants positive on search, validation **and** holdout, and 12 of 18 beating their
own inverse. The best with a real sample, `reclaim|ref1|rr3|ema400`, made +23.70% on the holdout from
103 trades at profit factor 1.402 and a 9.65% maximum drawdown.

It failed on two checks: deflated Sharpe 0.5913 against the 0.95 bar, and it lost to buy-and-hold,
which returned 65.11% over the same holdout.

The photographs say the grid, not the rule, is the suspect. Two specific mismatches:

1. **The reference level.** `RECLAIM_REFS = (1, 2, 3)` means the swept level is the extreme of the
   previous one to three candles. The pictures show horizontal lines drawn at levels much further
   back, and the circled candle sweeping one of those. A three-candle extreme is not the same object.

2. **The exit.** `REWARD_RATIOS = (1, 2, 3)` with `MAX_BARS = 30` caps every winner at three times
   risk and closes anything unresolved after five days. In every photograph the move after the
   reclaim candle **runs**, well past three times the candle's own range. A fixed cap takes 3R off a
   10R move while still paying the full stop on every loss, which lowers the profit factor of a real
   trend-following edge and can invert the result entirely.

This is therefore a different hypothesis about the SAME rule, not a re-run of the same hypothesis
hoping for a better number. The entry condition is untouched.

## The rule, unchanged

A single 4H candle takes out the **low** of the previous `ref` candles **and closes above their
high** (mirrored for sells). Stop just beyond the candle's own swept extreme. The entry is the next
candle's open. The EMA400 trend filter is kept because it appeared in 7 of the 8 variants that were
positive on all three splits in the first run, so removing it now would be discarding a result.

## The grid, fixed here before anything is run

| Knob | Values |
|---|---|
| `ref` (reference window, candles) | 1, 5, 10, 20 |
| exit | `fixed_rr3`, `trail_1.5atr`, `partial_2r_then_trail` |
| trend filter | EMA400 only |

12 variants. **Trials charged against this rule: 30** (the 18 already spent on the first grid plus
these 12). The deflated Sharpe is deflated by 30, not by 12, because both grids were tried on the
same idea and counting only the second would launder the search.

## What would make this a result

Unchanged, and not negotiable: positive on all three splits, at least 30 holdout trades, drawdown
within limits, beating its own inverse, deflated Sharpe at or above **0.95**, and beating
buy-and-hold on the holdout. Failing any one of those means it is not promoted, however good the
other numbers look and however many winning screenshots exist.

## What a negative result would mean

If the distant level and the runner exit do not lift it, then the fixed-R cap was not what was
holding the rule back, and the honest reading is that the rule is real but not large enough to clear
a bar set for committing money. It would still be the strongest candidate in the book and would still
earn a paper forward test.
