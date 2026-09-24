# SmartEntry V9 (MT5, account 25446287) — measured notes and test candidates

The owner reports the live gold set is profitable (~1100 in the tester) and the bitcoin set is
profitable too, and asked for improvement. These are candidates to **run**, not changes to apply:
the EA ships as a compiled `SmartEntry_V9.ex5` with no source, so nothing here can be backtested
inside this repo and no claim below is a claim about profit.

## The two live sets

| | Gold `AurumFlow_v04_LOAD-THIS_pd30` | Bitcoin `AurumFlow_v09_NOT-RUN_BTCUSD-scaled` |
|---|---|---|
| SL | 2100 pt = 21.00 price = **1.14 x H1 ATR** | 50281 pt = 502.81 price = **1.23 x H1 ATR** |
| TP | 1800 pt = 18.00 price = 0.98 x ATR | 43098 pt = 430.98 price = 1.05 x ATR |
| Reward:risk | **0.857** | **0.857** |
| Break-even win rate after costs | **54.4 %** | **55.7 %** |
| Spread cost as a share of the stop | 1.0 % | 3.4 % |
| Spread filter | 5 - 30 pt | 500 - 2000 pt |
| Measured spread | 21 median, **28 max** | 1694 median, **1707 max** |

**The bitcoin scaling is sound.** Whoever produced v09 scaled SL, TP, the pending distance and the
spread window, and the result lands within 8 % of gold's stop when both are expressed in ATR (1.23 vs
1.14) and within 12 % on the pending distance (0.018 vs 0.016 ATR). That is a good port, not a guess.

**Both spread caps are live, not binding.** Across 50,000 M1 bars (gold 5 Aug - 24 Sep, bitcoin
20 Aug - 24 Sep) **zero** bars exceeded either cap. Gold's tightest moment is the 22:00 UTC rollover
hour, median 26 and max 28 against a cap of 30.

**The `Structure*` inputs are the one open question.** `StructureDepth=210`, `StructureSpacing=110`
and `StructureRefreshBars=60` are **identical** in both sets, while everything else price-related was
scaled about 24x for bitcoin. If those inputs are counted in BARS that is correct and nothing is
wrong. If they are counted in POINTS, bitcoin's structure detection is running at roughly 1/100th of
the intended scale. The `.ex5` is compiled and the MT4 `AurumFlow-v7.mq4` on this machine is a
different expert that does not contain these inputs, so it cannot be settled from the files. Settle
it by running the bitcoin set once with `StructureSpacing=110` and once with `11000`: if the trade
count barely moves they are bars, and if it changes sharply they are points.

## Candidates

Each changes **exactly one input** from its base - verified byte by byte - because two runs that
differ in more than one thing attribute to nothing. All are UTF-16, matching what the terminal writes.

| File | Change | What it is for |
|---|---|---|
| `XAUUSD_A_spreadcap45` | MaxSpreadPoints 30 -> 45 | Gold never traded above 28, so this admits no worse fill in normal conditions; it removes the 2-point margin at the 22:00 rollover where a widening silently blocks entries. **Robustness, not edge** - expect the same or slightly more trades, not a better curve. |
| `XAUUSD_B_rr_1to1` | TP 1800 -> 2100 | Reward:risk 0.857 -> 1.00, break-even win rate 54.4 % -> 50.5 %. Win rate will fall; the question is whether payoff rises more. |
| `XAUUSD_C_rr_1to15` | TP 1800 -> 3150 | Reward:risk 1.50, break-even 41.2 %. Run with B to see which way the curve moves instead of assuming the middle. |
| `XAUUSD_D_trade_all_year` | November + December on | The set currently sits out two months a year. Free to confirm either way. |
| `BTCUSD_A_spreadcap2500` | MaxSpreadPoints 2000 -> 2500 | 17 % headroom today; bitcoin spreads widen far harder than gold's on news. |
| `BTCUSD_B_rr_1to1` | TP -> 50281 | Payoff matters MORE on bitcoin than gold: its spread is 3.4 % of the stop against gold's 1.0 %. |
| `BTCUSD_C_rr_1to15` | TP -> 75422 | Break-even 41.9 %. Bitcoin trends harder, which is a reason to test a wider target, not to assume it. |

## How to judge a result

Use the same symbol, period, model and starting balance as the run that produced the current figure,
change only the preset, and compare **profit factor and expectancy per trade** rather than net profit
- net profit rewards whichever run happened to take more trades. A candidate needs roughly 100 trades
before its number means anything, and the house rules in `CLAUDE.md` apply before anything goes live.
