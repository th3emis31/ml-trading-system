# Do candlestick patterns carry information on gold?

**Status: measurement only. No model is retrained, no feature list is changed, nothing is written
to `models/`, and no strategy or EA is touched.** This answers one question with numbers — which of
these patterns actually predicts anything on this broker's gold and bitcoin candles — before any of
them is allowed near the learners.

Declared 19 September 2026 **before the measurement**, after an audit the owner asked for found
that the system learns **no candlestick patterns at all**: the RandomForest's 33 features and the
LSTM's 21 contain none, and no feature anywhere uses open against close, body size or wick length.
The two nearest (`detect_pullback`, `detect_reversal` in `src/features.py`) are close-only, and
`detect_reversal` gives a bullish and a bearish reversal **the same value**, so it cannot say which
way it pointed.

## Why measure before adding

Two of this system's own results argue for it. The LSTM and the tree models have already been shown
to have no measurable edge on XAUUSD 1h at any data volume or network size (rows of 18 September),
so adding twelve features to a learner that scores like a coin flip would tell us nothing about the
features. And a day spent on the Aurum Flow mechanism showed how easily a filter that looks good on
one window evaporates on another. So each pattern is measured on its own first, as a fact about
gold, independent of any model.

## The patterns, and their thresholds — fixed here, never searched

Sizes are normalised by ATR(14) or by the bar's own range, so "long wick" means the same thing at
gold 1,200 and gold 4,300. `body = |close − open|`, `range = high − low`, `upper = high − max(open,
close)`, `lower = min(open, close) − low`. Every pattern is **direction-signed**: `+1` bullish,
`−1` bearish, `0` absent — the thing `detect_reversal` gets wrong.

### One-candle

| Pattern | Definition | Sign |
|---|---|---|
| **Marubozu** | `body / range ≥ 0.80` and `range ≥ 0.5 ATR` | `+1` if close > open, else `−1` |
| **Doji** | `body / range ≤ 0.10` and `range ≥ 0.3 ATR` | `0` — indecision has no direction; measured as its own flag |
| **Hammer** | `lower ≥ 2 × body`, `upper ≤ 0.25 × range`, `body / range ≤ 0.35`, `range ≥ 0.5 ATR` | `+1` |
| **Shooting star** | `upper ≥ 2 × body`, `lower ≤ 0.25 × range`, `body / range ≤ 0.35`, `range ≥ 0.5 ATR` | `−1` |
| **Pin bar** | one wick ≥ `0.66 × range` and `range ≥ 0.75 ATR` | `+1` if the long wick is below, `−1` if above |

### Two-candle

| Pattern | Definition | Sign |
|---|---|---|
| **Bullish engulfing** | previous bar down, this bar up, `open ≤ prev close`, `close ≥ prev open`, this `body ≥ prev body` | `+1` |
| **Bearish engulfing** | previous bar up, this bar down, `open ≥ prev close`, `close ≤ prev open`, this `body ≥ prev body` | `−1` |
| **Piercing line** | previous bar down, this bar up, opens below prev close, closes above the midpoint of the previous body but below its open | `+1` |
| **Dark cloud cover** | mirror of piercing | `−1` |

### Three-candle

| Pattern | Definition | Sign |
|---|---|---|
| **Morning star** | bar −2 down with `body ≥ 0.5 ATR`; bar −1 small (`body ≤ 0.35 × bar −2 body`); bar 0 up closing above the midpoint of bar −2's body | `+1` |
| **Evening star** | mirror | `−1` |
| **Three white soldiers** | three consecutive up bars, each closing above the last, each `body / range ≥ 0.6` | `+1` |
| **Three black crows** | mirror | `−1` |

Thirteen detectors. The thresholds above are the conventional textbook values, written down now so
that no threshold can be adjusted after seeing a result. If a pattern fails, it fails.

## How each is measured

For every pattern occurrence, the **triple-barrier outcome** of a trade taken in the pattern's own
direction, entered at the **next bar's open** (`src/edge_research.triple_barrier_outcomes`, which is
already the system's labeller and already refuses to guess bars without a full horizon ahead):

* target **+1.0 ATR**, stop **−1.0 ATR**, horizon **12 bars** — a symmetric 1:1 barrier, so a
  win rate above 50 % is the whole test and no reward:risk arithmetic is needed.
* Compared against the **base rate**: the same barrier taken on *every* bar in the same direction
  over the same period. A pattern is only interesting if it beats the base rate, not 50 %.
* Reported per pattern: occurrences, win rate, base rate, the difference, and a **binomial
  95 % interval** on the difference, so a pattern with 40 occurrences cannot masquerade as a
  finding.

Markets: **XAUUSD and BTCUSD**, on **15m, 1h and 4h** — 13 patterns × 2 markets × 3 timeframes =
**78 tests**. With that many, some will clear 95 % confidence by chance alone (about four), so the
pre-declared rule is:

> A pattern counts as carrying information only if its edge over the base rate holds with at least
> 200 occurrences, in the **same direction on both markets**, and on at least **two of the three
> timeframes**. Anything that appears on one market and one timeframe is treated as noise, whatever
> its p-value.

## What happens next, and what does not

If patterns survive, the next step is a separate, gated test of whether adding them to the feature
set improves the models — run through the existing 05:30 daily-learning gate, which keeps a new
model only when it beats the old one on unseen bars. **Nothing in this document changes any feature
list, model, strategy or EA.** If nothing survives, that is recorded and the feature work is
dropped, because adding features that carry no information to a learner with no edge would be
theatre.

## Second stage: backtest the patterns as strategies, with costs, on both assets

Declared 19 September 2026 **before the run**, after the owner pointed out that both assets need
backtesting and not only measuring.

**The gap this closes.** The measurement above is **gross**. It counts how often a symmetric
1 ATR barrier resolves in the pattern's favour and charges nothing for the spread or the overnight
swap. On a 1:1 barrier a +2 percentage-point win-rate edge is worth roughly **+0.04 R per trade**
before costs, and the system's cost model on these markets is a real fraction of that. So a pattern
can be genuinely predictive and still lose money, and the measurement alone cannot tell the
difference. This stage puts each pattern through the same engine every other strategy in this log
uses, with the same costs.

**The rule under test.** For each of the 13 patterns: enter in the pattern's own direction at the
**next bar's open** (market order, exactly as the measurement assumed), stop `sl_atr` × ATR(14) from
the signal bar's close, target `rr` × that risk, one position at a time, time exit after 48 bars.
Doji is the exception — it has no direction, so it is skipped here rather than given one.

| Knob | Values | Why |
|---|---|---|
| `pattern` | the 12 signed patterns (doji excluded) | one variant each, no combinations |
| `sl_atr` | 1.0 | fixed, to match the measurement's barrier |
| `rr` | 1.0, 2.0 | 1.0 reproduces the measured barrier under costs; 2.0 asks whether a wider target helps |

12 × 2 = **24 variants per market**, on **XAUUSD and BTCUSD** at **1h and 4h** — the two timeframes
where the measurement found its largest effects. **96 trials**, every one counted in the deflated
Sharpe.

**The bar does not move.** After costs, ≥ 30 holdout trades, drawdown within limits, deflated
Sharpe ≥ 0.95, plus the two conditions that caught the Aurum Flow filters: positive on **all three
splits**, and beating its **own inverse**. The prediction on record before running is that nothing
passes, because a +0.04 R gross edge is smaller than the cost of trading it — and if that is what
happens, it is the answer, not a reason for a wider grid.
