# Carry-positive short gold

**Status: REFUTED 27 September 2026 (see section 11). Never traded. No live input, preset, demo strategy or EA changes on the back of this
document.** Written 27 September 2026, before any code, because the measurement that motivates it was made
today and no strategy in this system was designed with it.

## 1. Hypothesis

**A short gold position is paid to exist, so a short setup whose price expectancy is only near zero becomes
profitable once it is held across enough broker rollovers — and the longer it is held, the more of its
return comes from financing rather than from price.**

The inefficiency is not a prediction edge. It is a **holding-cost asymmetry**, measured from the broker on
27 September 2026 through MT5 `symbol_info` (`swap_mode` 1 = POINTS, gold point 0.01):

| side | broker figure | per night at gold 4,286 | per year (forex calendar, ~260 rollovers incl. triple Wednesdays) |
|---|---|---|---|
| long | `swap_long` −79.48 pts | **0.0185 % cost** | ≈ 6.8 % cost |
| short | `swap_short` +34.41 pts | **0.0080 % credit** | ≈ 2.9 % credit |

So the two sides of the same instrument differ by roughly **9.7 % a year in financing alone**, before any
price movement. That spread is set by a funding desk from interest rates and gold's cost of carry, not by
traders competing it away, which is why it should persist. Bitcoin is the same shape and more extreme:
`swap_short` is **0.0** (shorts are not financed at all) while longs pay ≈ 20 %/yr.

**Why this is new rather than a repackaging.** Until today `src/strategy_lab.py` charged gold shorts the
*long* rate — 0.0190 % a night as a cost — because the table said "short side assumed equal to long". Every
short strategy in this system was therefore designed and judged under a cost model that penalised holding
by 0.0270 % of price per night more than reality. Correcting it moved six measured short variants on
XAUUSD:4h by 0.35–1.32 percentage points and one from −1.93 % to +2.46 %. No existing strategy was built to
exploit a hold that is now paid for; `sweep_reversal`, `cisd`, `crt_*` and `smart_entry_arch` all use short
`max_bars` horizons chosen when holding was expensive.

**It also sits on the owner's own measured edge.** Their real closed trades are 51 SELL gold at 70.6 % win
for +£494.95, over a window in which gold *rose* 8.98 % — so their selection skill on the short side is
demonstrated and is not direction luck. This strategy takes the same side.

### The number that would prove it wrong

Stated before any result, and it is the whole point of the design:

> Take one entry rule. Hold it for N rollovers, sweeping N. Decompose every trade into its **price**
> component and its **carry** component (the engine already records `nights` and `swap_pct` per trade).
>
> **The hypothesis is WRONG if, as N rises, after-cost expectancy_r does not rise, or if the improvement is
> not attributable to the carry component.**

Concretely, it is refuted by any of:

* after-cost `expectancy_r` at the longest hold is **not greater** than at the shortest hold, on ≥ 100
  holdout trades; or
* the increase in total return is **less than the summed carry** (`Σ swap_pct`), meaning price, not
  financing, did the work and the carry story is a coincidence; or
* the same sweep run on the **long** side improves too — that would mean longer holds help regardless of
  financing, which is a trend effect wearing this hypothesis as a costume.

A result where longer holds help *and* the carry explains the size of the help is the only outcome that
supports it.

## 2. Instruments and timeframe

* **XAUUSD**, and **BTCUSD** as the second test of the same mechanism (unfinanced shorts, crypto calendar
  financed every night rather than the forex Mon–Fri with triple Wednesday).
* **4h bars** for the signal. Chosen because the hold length is measured in nights, so the bar must be
  coarse enough that a multi-night hold is a handful of bars rather than hundreds, and `sweep_reversal` and
  `cisd` already have measured behaviour on gold 4h to compare against. **Not** 15m or 30m: at those sizes a
  multi-night hold is hundreds of bars and the ATR-scaled stop becomes smaller than a typical day's range,
  which is the unit error that broke two earlier attempts (`.claude/memory/LESSONS.md`, 27 Sep).
* Sessions: all hours for the entry. The owner's own late-session concentration (18:00–23:00 UTC went 12 for
  12 in their trades) is a **filter to test**, not an assumption — it is 12 trades and cannot carry a rule.

## 3. Entry rules

Short only. The entry is deliberately **not** new: reusing a rule whose behaviour is already measured is
what lets the hold-length sweep be the only thing that changes.

* Base rule: `sweep_reversal` in `reclaim` mode — the 4h candle takes out the low of the previous `ref`
  candles and closes back above their high (mirrored for a sell: takes out the high, closes below the low).
  Registered builder, already in the engine, 18 declared variants.
* Direction: **short only** (`params["side"] = "short"`), which the engine now honours in registered
  builders as well as the built-in signal path.
* Confidence/grade inputs: none. No score is invented; the sweep is over hold length only.

## 4. Exit rules

This is where the strategy differs from everything already in the book, and the only axis swept:

* **Stop**: beyond the swept extreme of the signal candle, as `sweep_reversal` already computes it. Fixed,
  not trailed — a trail would shorten holds and confound the variable under test.
* **Target**: `rr` ∈ {1, 2, 3} R, as already declared.
* **Time exit**: `max_bars` swept across **{6, 12, 30, 60, 120} 4h bars** ≈ **1, 2, 5, 10, 20 nights**. This
  is the independent variable.
* **Invalidation**: none beyond stop/target/time. Deliberately plain.

## 5. Risk

* Risk per trade: 1 % of equity, from the system's single sizing function. Never a hard-coded lot.
* Max open positions: **one per asset**, the owner's standing rule. No stacking.
* Max daily loss: as configured for the demo strategies; any breach halts.
* Correlation limit: gold and bitcoin are tested separately and must never both be open on the same signal.

## 6. Filters

* Market-condition gate: no entries when the day is rated AVOID.
* News: the existing tier-1 event window gate.
* Regime filter to **test, not assume**: `trend_ema` ∈ {0, 400}. A carry-positive short held for 20 nights
  into a strong uptrend is the obvious failure mode, so a trend filter is the first candidate defence.
* Triple-Wednesday rollover: the engine already weights Wednesday 3×, and `exit_before_triple_swap` exists.
  For a short that *earns* carry, leaving the triple night in is now favourable — the opposite of the long
  side. Tested both ways rather than assumed.

## 7. Model inputs

None. No ML, no features, no labels, so no leakage review is needed. The decomposition uses fields the
engine already records per trade (`nights`, `swap_pct`, `net_pct`, `net_r`, `r_multiple`).

## 8. Success metric

**The one metric: after-cost `expectancy_r` on the locked holdout, at the best hold length, must exceed the
same rule's `expectancy_r` at the shortest hold — and the difference must be no larger than the summed carry
(`Σ swap_pct`) can explain.**

Promotion bar, unchanged from the standing rules and not to be lowered:

* ≥ **100** closed holdout trades (30 minimum for a single rule strategy, but a carry claim needs nights, so
  100 is the bar used here);
* after-cost positive on a holdout selection never saw;
* max drawdown ≤ 20 %;
* deflated Sharpe ≥ **0.95**, counting every one of the 18 × 5 × 2 = 180 configurations this design will try;
* beats an inverse control that **traded and lost**;
* ambiguous-exit count reported, and the `expectancy_r_bound` must not sit on the other side of zero.

Baseline to beat: `sweep_reversal reclaim|ref1|rr3|notrend` short on XAUUSD:4h, and the long-side result of
the same sweep as the control for "longer holds just help".

## 9. Known risks and failure modes

* **Gold trends up.** 2.9 %/yr of carry does not pay for a 10 %/yr uptrend. This strategy should stop
  trading in a confirmed gold uptrend; that is what the `trend_ema` filter is for and it may be mandatory
  rather than optional.
* **The rate is not fixed.** `swap_short` is a broker setting and can change or invert with interest rates.
  A strategy whose edge is financing must re-read `swap_long`/`swap_short` and halt if the short credit
  disappears. This is a live dependency, not a constant.
* **It is one broker.** +34.41 points is Vantage's demo. Another broker may charge shorts.
* **Survivorship in the sweep.** 180 configurations is far past the ten at which the `measure` skill says to
  stop and get new evidence. The best row will be the luckiest row until it is re-tested on bars the sweep
  never saw.
* **Longer holds mean fewer trades.** At 20 nights on 4h bars the holdout may not reach 100 trades at all,
  in which case the honest answer is "insufficient evidence", not a smaller bar.

## 10. Status

| date | status | note |
|---|---|---|
| 2026-09-27 | **idea** | Document written before code, on the day the short financing rate was first measured (`COST_MODEL` v3 → v4). |
| 2026-09-27 | **REFUTED** | `python -m src.carry_short run` on XAUUSD:4h, 10 configurations, one variable. Failed two of its own three declared tests. Kept as a record, not deleted: the mechanism is real and the module that measures it is reusable. |

## 11. Result — refuted by its own test, same day

The carry exists, behaves exactly as predicted, and is far too small to matter.

| side | hold | trades | win % | expectancy_r | net % | **carry %** | price % | nights/trade | inverse % | buy & hold |
|---|---|---|---|---|---|---|---|---|---|---|
| short | 6b | 108 | 37.96 | **−0.1275** | +1.38 | +0.64 | +1.61 | 0.74 | −10.96 | +71.22 |
| short | 12b | 104 | 31.73 | −0.1603 | −0.30 | +0.90 | −0.01 | 1.08 | −2.72 | +71.22 |
| short | 30b | 102 | 27.45 | −0.1995 | −4.41 | +1.13 | −4.13 | 1.38 | −7.82 | +71.22 |
| short | 60b | 101 | 26.73 | −0.2162 | −4.53 | +1.18 | −4.30 | 1.47 | −8.40 | +71.22 |
| short | 120b | 98 | 26.53 | **−0.2172** | −0.89 | +1.29 | −0.65 | 1.64 | −10.34 | +71.22 |
| long | 6b | 111 | 53.15 | +0.3504 | +28.67 | −1.94 | +27.71 | 0.92 | −4.59 | +71.22 |
| long | 60b | 98 | 48.98 | **+0.4203** | +32.04 | −2.79 | +31.53 | 1.50 | −17.61 | +71.22 |

**Test 1 — does expectancy rise with the hold? NO.** Short `expectancy_r` falls monotonically from −0.1275
to −0.2172 as the hold stretches from about one night to about 1.6. Longer is strictly worse.

**Test 2 — does the carry explain the gain? There was no gain to explain.** The carry itself is real and
measures correctly: it accumulates 0.64 % → 1.29 % as nights per trade rise 0.74 → 1.64, which is the
0.008 %/night credit doing exactly what the broker's figure says. It is simply an order of magnitude too
small. Against a price component that goes from +1.61 % to −4.30 %, 1.2 % of financing is noise.

**Test 3 — does the long side also improve? YES, which is fatal.** Long `expectancy_r` rises 0.3504 →
0.4203 over the same sweep. Longer holds help in the direction gold was actually going, so the effect under
test is drift, not financing — the failure mode named in section 9 before the run.

Note also that **every short row has a negative `expectancy_r` even where `net_pct` is positive.** The 6-bar
row shows +1.38 % net and −0.1275 R. `net_pct` compounds the raw price move on full capital; `expectancy_r`
scales each trade by its own risk distance, which is what a 1 %-risk-per-trade account actually experiences.
For a risk-sized strategy `expectancy_r` is the honest figure, and it says the short side loses at every
hold length tested.

### What this does NOT license

The long rows look strong — +32.04 % at a 5.5 % maximum drawdown, expectancy_r +0.42, zero ambiguous exits,
against an inverse control that lost 17.6 %. **They still returned less than half of simply holding gold
(+71.22 % over the same holdout).** So the long side of this rule is not a discovery to promote; whether it
is attractive at all depends on buy-and-hold's own drawdown over that window, which has not been measured
here. Two of the five long rows are also under 100 trades and are insufficient evidence on their own terms.

### What survives

* The **measurement**: gold shorts are credited ≈ 0.008 %/night and the engine now accounts for it. That
  correction stands on its own and improved six previously-measured short variants.
* The **module**: `src/carry_short.py` decomposes any strategy's result into price and carry, which nothing
  in this system could do before, with `tests/test_carry_short.py` pinning the sign convention.
* The **conclusion**, stated plainly: financing is not an edge on gold at these hold lengths. A carry claim
  would need an instrument where the credit is large relative to drift, or hold lengths of many weeks, which
  4h bars and a 2-year holdout cannot evidence.
