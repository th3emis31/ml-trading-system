# Friday's failure against Thursday, and what Monday does

**Status: measurement first, then a costed backtest. Research only — nothing here trades.**

Declared 19 September 2026 **before the run**, from the owner's whiteboard sketch: three daily
candles labelled Th, Fr, Mo, with Thursday and Friday capped by the same horizontal line and Monday
drawn zig-zagging downward.

## The rule, stated precisely

The owner's words: *"If the high of Friday is not high[er] [than] the high of Thursday, on Monday
will visit low price of Friday, and same opposite daily candle."*

So two mirrored claims:

| | Condition on Friday | Claim about Monday |
|---|---|---|
| **Bearish** | Friday's high **≤** Thursday's high (Friday failed to take Thursday's high) | Monday trades **down to Friday's low**: `Monday.low ≤ Friday.low` |
| **Bullish** | Friday's low **≥** Thursday's low (Friday failed to take Thursday's low) | Monday trades **up to Friday's high**: `Monday.high ≥ Friday.high` |

"Visit" is read as touched or exceeded, at any point during Monday's session.

## Getting the days right, which is the easy thing to get wrong

This broker's daily candle **opens at 21:00 UTC**, so a bar is stamped the day *before* the session
it covers. Verified on 5,084 gold daily bars: every bar opens at 21:00, and `open + 1 day` gives a
clean Monday–Friday distribution (986–998 of each) while the raw stamp gives Sunday–Thursday. So

* Thursday's session = the bar opening **Wednesday** 21:00,
* Friday's session = the bar opening **Thursday** 21:00,
* Monday's session = the bar opening **Sunday** 21:00.

A week counts only when all three sessions are present and consecutive; holiday weeks with a
missing Monday or Friday are skipped rather than patched.

## What is measured, in order

**1. The claim on its own, against the base rate.** The hit rate is meaningless without knowing how
often Monday visits Friday's low *anyway*. Both are computed on the same weeks:

* hit rate given the condition,
* **base rate** over every Monday regardless of the condition,
* the difference, with a **Wilson 95 % interval**, so a thin sample cannot pass for a finding.

A rule like this can look strong purely because Monday very often touches Friday's low in any case —
the two candles are adjacent and the gap between them is small.

**2. Then, whether it is tradeable.** A statistical tendency is not a strategy. Entry at Monday's
open, target Friday's low (or high for the bullish case), and because the sketch names no stop, two
pre-declared ones are tested rather than one chosen after the fact:

* stop at the **opposite extreme of Friday's candle** (Friday's high for a short), the structural choice;
* stop at **1.0 × ATR(14)** from the entry, the volatility choice.

Costs are the system's own model, spread and swap, unchanged. Results are reported per market with
trade counts, win rate, profit factor, expectancy in R and drawdown.

## Markets and the caveat on bitcoin

**XAUUSD**, 5,084 daily bars from 2007, is the real test: gold has a true weekend, so Thursday,
Friday and Monday are distinct sessions with a gap between them, which is what the rule is about.

**BTCUSD** is run too and reported separately, with the caveat stated here in advance: bitcoin trades
through the weekend, so its "Friday" and "Monday" are adjacent in a way gold's are not, and Saturday
and Sunday can already have visited Friday's low before Monday opens. A result there means something
different and is not evidence for or against the rule as drawn.

## What would count

For the tendency: the edge over the base rate must hold on gold with a 95 % interval clearing zero,
in **both** the bearish and bullish directions — a rule that only works one way is a directional bet
on the period, not a structure.

For the strategy: the standing bar, unchanged. After costs, ≥ 30 trades, drawdown within limits, and
it must beat its own inverse. Nothing here lowers anything.
