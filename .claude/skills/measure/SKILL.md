---
name: measure
description: The contract for producing a number anyone can trust — pre-flight checks, the inverse control, the sample-size rule, and how to report a result honestly. Use before ANY backtest, sweep, statistic or performance claim.
---

# Measuring

A wrong number is worse than no number, because work gets built on it. Every
incorrect result this project has produced came from units, scale or provenance,
never from the strategy logic. This skill exists so those three are checked before
a table is written, not after someone objects to it.

## Pre-flight. All five, every time, before running anything

1. **Units.** State the instrument's price increment and confirm it in the config.
   Gold and Bitcoin 0.01, most FX 0.0001, indices vary. Getting this wrong makes
   slippage larger than the stop and turns any edge negative. Symptom: everything
   loses, including things that should be coin flips.
2. **Scale.** The stop distance must be clearly wider than a typical bar range.
   When both the stop and the target fall inside one candle, the engine's
   stop-first convention decides that trade, not the market. Count those bars and
   report the count. Symptom: a fair 1:1 bet measures well under 50%.
3. **Causality.** Nothing in the signal may use a value from the bar it trades on,
   or from any bar after it. Say which bar the fill happens on.
4. **Control.** Run the inverse direction on the same data. An edge that does not
   clearly beat its own inverse is drift, not edge. An inverse that produces zero
   trades is not a control, it is a free pass.
5. **Provenance.** Where did the data come from, what dates does it span, and is
   any pasted transcript or screenshot actually current? Check the timestamp.

## The sample rule

Under 100 closed trades the row is labelled **insufficient evidence**, whatever
the profit factor says. Do not rank, promote or compare such rows against ones
that do clear it.

## Reporting

- Lead with what would make the number wrong, then the number.
- Give trades, win rate, profit factor, expectancy, max drawdown against peak
  equity, longest losing streak, and the ambiguous-exit count.
- Separate what was measured from what it implies. One sentence each.
- Record every run in `.claude/memory/BASELINE.md`, including the bad ones.
  Bad rows are what stop a dead strategy being re-tried under a new name later.

## When a result turns out to be wrong

Say so in the first line of the next message, name the cause, and write the
lesson into `.claude/memory/LESSONS.md` as a rule that would have caught it.
Then add a test or a guard so it cannot recur silently. A corrected result is
worth more than a clean-looking one; a defended result is worth nothing.

## Sweeps and multiple configurations

Every extra configuration tried on the same data raises the chance that the best
row is the luckiest row. Count how many have been tried and say the number when
reporting. Past roughly ten, stop, and get new evidence instead: a different
period, a different instrument, a walk-forward with parameters fixed per fold,
or live demo fills.
