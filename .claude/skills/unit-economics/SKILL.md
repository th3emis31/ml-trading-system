---
name: unit-economics
family: business
description: Work out what one unit actually earns after every cost, and say which side of the line it falls. Use when the user asks whether something is profitable, what it costs per trade or per customer, or why a positive-looking thing loses money.
---

# Unit economics

The question is never "did it make money overall". It is **what does one unit earn, and what does one
unit cost**. A thing with a real edge and a larger cost loses forever, and no amount of scaling fixes
it — scaling multiplies both sides.

This was settled on bitcoin here on 24 September 2026. The strategy had a genuine gross edge of
**0.0823 per trade** and the broker charged **0.169 per trade** in spread. It lost consistently across
3,545 trades, in 5 of 6 years, at a profit factor pinned between 0.94 and 0.97 — not because the rules
were wrong, but because the cost was twice the edge. No parameter change could have fixed that, and
two rounds were spent trying before anyone did the arithmetic.

## Procedure

1. **Define the unit.** One trade, one customer, one order, one run. Say it out loud — half of all
   confusion here is two people costing different units.
2. **Measure the cost on the same period as the result.** Not the current spread against a 2024
   backtest: that exact mistake made a spread-cap change look safe when the trades it admitted lost
   money. Pull the cost distribution from inside the window being judged.
3. **Compute gross, not net, first.** Net is what you observe; gross is net plus the costs you
   removed. Gross tells you whether there is anything there at all.
4. **Put them side by side, per unit.** `gross_per_unit` against `cost_per_unit`. The ratio is the
   whole answer.
5. **State the break-even cost.** "This needs a spread under 823 points" is actionable; "the spread
   is too high" is not. It tells the owner exactly what to go and check.
6. **Check the arithmetic reconciles.** `gross − costs = net`, to the penny. If it does not, one of
   the three is measured over a different set than the others.

## What good looks like

- Every figure is per unit, and the unit is named.
- Cost is measured, never assumed. A generic assumption decides the answer rather than the market —
  for XRPUSD the real round trip is 1.64 % against a generic 0.1 %, sixteen times out.
- The break-even threshold is stated as a number someone can go and look up.
- When the edge is real but smaller than the cost, that is said plainly: **the rules work and the
  venue is wrong.** That is a different problem with a different fix.

## Acceptance

```acceptance
number: reconciliation_error <= 0.01
number: units >= 100
appended: .claude/memory/BASELINE.md
ask: was the cost measured inside the same window as the result, not from the most recent data?
```

`reconciliation_error` is `abs(gross - costs - net)`. It is in the acceptance block because an
unreconciled set of three numbers is three numbers about three different things.
