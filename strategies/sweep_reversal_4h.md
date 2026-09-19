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
