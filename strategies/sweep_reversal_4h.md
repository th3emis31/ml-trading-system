# The 4H manipulation candle: sweep a level, close back inside, trade the reversal

**Status: research only. Never trades. No live input, preset, demo strategy or expert changes.**

Declared 19 September 2026 **before the run**, from two photographs the owner sent with the words
*"4H wait for manipulation 4H candle"*.

## What the pictures show

**First picture, the short.** A run of 4H candles drifts down to a level drawn as a horizontal black
line across several prior highs. One candle — circled at its wick — pushes **above** that line, then
**closes back below** it. A red arrow marks the entry on that rejection, a red box sits above as the
risk, and a large green box runs down as the reward. A hook is drawn through the sweep to show price
going up through the level and turning straight back.

**Second picture, the long.** The same thing mirrored. Green lines mark prior lows, one candle's wick
is circled where it pokes **below** them, it closes back **above**, and two long green strokes run up.

So the pattern is a **liquidity sweep followed by rejection**: the candle takes out the stops beyond
a level and then fails to hold there. The manipulation is the wick; the signal is the close.

## What this is not

It is **not** `src/crt_mss_lab.py`, which also begins with a sweep. That model then requires a market
structure shift and a fair-value-gap retest, read on 5m or 15m bars, with a resting limit entry. This
one is simpler and lives entirely on the 4H candle: sweep, close back inside, trade. Fewer moving
parts, so fewer ways to fit it to the past.

## The rule, stated precisely

For the short (the long is the exact mirror):

1. **Reference level** = the highest high of the previous `lookback` 4H candles, not counting the
   current one.
2. **Sweep** — the current candle's **high** exceeds that level.
3. **Rejection** — the current candle's **close** is back **below** that level.
4. **Optional confirmation** — the candle closes below its own open, as the circled candle in the
   picture does. Tested both with and without, because the picture shows one case and one case is not
   evidence.
5. **Entry** at the next candle's open. **Stop** above the sweep candle's high. **Target** at
   `rr` times that risk.

## The grid, fixed here before anything is run

| Knob | Values |
|---|---|
| `lookback` | 10, 20, 40 candles |
| `require_body` | True (candle must close against the sweep), False |
| `rr` | 1.0, 2.0, 3.0 |

3 × 2 × 3 = **18 variants per market**, on **XAUUSD and BTCUSD 4H** = **36 trials**, every one
counted in the deflated Sharpe. Both directions trade under the same parameters, because a rule that
only works one way is a directional bet on the period rather than a structure — the same requirement
the Thursday/Friday/Monday rule was held to.

## How it is judged

Through the system's existing engine, with its own cost model, one position at a time, and the
market's stored search / validation / **locked holdout** split. Reported for every variant:

* trades, win rate, profit factor, expectancy in R and drawdown on each of the three splits;
* the **inverse baseline** — the same signals traded the other way — because gold rose across most
  of this history and a long-biased rule is flattered by that alone;
* the per-trade Sharpe achieved against the per-trade Sharpe the deflation requires.

The standing bar is unchanged: after costs, ≥ 30 holdout trades, drawdown within limits, deflated
Sharpe ≥ 0.95. Plus the two conditions that have caught every candidate so far — **positive on all
three splits** and **beating its own inverse**. Nothing here lowers anything.

If it fails, that is recorded as plainly as a pass would be.

---

## Correction, 19 September 2026: the owner's reading, and the one that earned a forward test

Everything above describes the **fade**. That was my reading of the photographs, and it was wrong.
The owner stated the rule in words: *"Wait for 4h candle manipulation — if the close above the
previous high buy, if the close below sell. 4h is very powerful."* That is the **continuation**: the
candle sweeps the level and **closes beyond** it, and you trade **with** the close. The first version
of `src/sweep_reversal.py` not only failed to test this, it carried a test asserting that exactly this
case was *not* a signal.

Both readings are now in the grid as `mode`, so the document's own record of my error stands. The grid
is 2 modes × 3 lookbacks × 2 body filters × 3 reward ratios = **36 variants per market**, all counted.

One addition, declared before it was run and for a stated reason: a close beyond a range is a
breakout, and a breakout needs a trend to run into. Without a filter the continuation made about
+40 % on gold's 2024-26 run and lost roughly half over the fifteen ranging years before it — a regime
result, not an edge. An **EMA400** filter on the 4H (about eleven weeks of trend) was added for that
reason and no other.

### What it measured

`continue | lb40 | nobody | rr2 | ema400` on **XAUUSD 4H**, under the cost model corrected the same day
from measured broker spreads:

| Window | Net | Trades | PF | After-cost expectancy |
|---|---|---|---|---|
| search | +6.33 % | 270 | 1.060 | +0.0518 R |
| validation | +11.42 % | 85 | 1.312 | +0.0546 R |
| **holdout** | **+26.69 %** | **118** | **1.378** | **+0.2193 R** |

Max drawdown 6.76 %. Its own inverse returns −35.60 % at PF 0.628, so it beats the mirror by 62.3
points. **Zero ambiguous exits** — no trade's outcome rests on the engine's stop-before-target
assumption, so the sign of every window is read off the data rather than assigned. It does not beat
buy-and-hold over the holdout (+74.0 %), at a small fraction of the drawdown.

**Deflated Sharpe 0.181 against the 0.95 bar, so it is not promoted and nothing about it is called
profitable.** The bar is not moved. What the number mostly reflects is the trial count charged against
it — 36 declared variants on top of a cumulative registry count in the tens of thousands.

### What was done about that

A **single pre-declared paper forward test**, which incurs no trial-count penalty because there is one
hypothesis and it is fixed in advance:

* the spec is frozen as `sweep_reversal.FORWARD_CANDIDATE` and pinned by tests;
* it runs hourly as `sweep_continue_xau_4h` in `src/crt_forward.py`, on broker bars, state in
  `data/strategy_lab/forward_sweep_continue_xau_4h.json`, from `start_at` **2026-09-19 16:11 UTC**;
* nothing before that instant counts, and the module has no order path at all — it **places nothing**;
* it sits at tier 1 (shadow) on the ladder in `src/forward_evidence.py`, and a verdict needs 30
  closed forward trades.

The owner's rule is the first candidate of any family to be positive in all three windows, beat its
own inverse, and have its sign independent of an engine assumption. That earned it a forward test. It
has not earned money, and will not be given any until the evidence is its own.
