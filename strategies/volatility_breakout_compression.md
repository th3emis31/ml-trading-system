# Does the Volatility Trend Breakout do better in compression?

**Status: research only. Never trades. The live demo strategy's inputs are unchanged — the filter
is a new opt-in config field that defaults to `all`, so `src/demo_volatility_breakout.py` behaves
exactly as before unless someone deliberately switches it on.**

Declared 19 September 2026 **before the run**.

## Where the idea came from

It is the one by-product worth keeping from a day spent failing to make the Aurum Flow mechanism
work. That mechanism has no edge — around 260 configurations across three timeframes, and its
inverse loses too — but while searching it, one thing was consistent enough to be worth a look
somewhere else: on gold 1h, **volatility compression beat expansion in every session**.

| Gold 1h, Aurum Flow filter grid, holdout | mean profit factor |
|---|---|
| `compression` (ATR14 below its 200-bar median) | **0.963 – 1.001** across sessions |
| `expansion` (ATR14 above it) | 0.706 – 0.840 |

Those numbers belong to a strategy with no edge, so they are **not** evidence of anything. They
are a hypothesis: that breakouts on gold fare better when they fire out of a quiet stretch than
when volatility is already elevated. That is also the more sensible story — a breakout that
happens after ATR has already expanded is chasing a move that has largely happened.

The right place to test a hypothesis is a strategy that **already has an edge**. The Volatility
Trend Breakout is the only one here that beats its own inverse: BTCUSD out of sample (settings
fitted on gold, never on bitcoin) gave 248 trades, profit factor 1.232, +26.68 %, drawdown
12.72 %, and gold's full history 286 trades at profit factor 1.291.

## The change under test

One new config field, one pre-declared binary, nothing else touched:

* `atr_regime = "all"` (default, current behaviour) | `"expansion"` | `"compression"`
* `atr_median_len = 200` — ATR(`atr_len`) compared with its own rolling median over 200 bars, read
  **at the signal bar**, so it sees nothing the strategy could not have seen.

Everything else stays at the owner's Pine defaults: EMA50 filter, Donchian 20, ATR 14 with the
1.4 buffer, RSI > 52, volume filter, stop 1.5 ATR, TP1 1.3 R, TP2 2.8 R, trail 2.2 ATR, 65-bar
time exit, 0.04 % per side commission, 2 ticks slippage, 5× leverage cap.

## Trials, and why the count is small

3 regimes × 2 markets = **6 trials**, and the inverse baseline for each is a baseline rather than a
candidate. This is deliberately not a sweep: `atr_median_len` is fixed at 200 rather than searched,
and no other parameter moves. A day of grid-widening on Aurum Flow is the reason — the more knobs
that turn, the higher the deflated Sharpe bar has to be, and a single pre-declared binary keeps the
burden honest.

## Data and splits

Vantage MT5 4H candles through the running app, XAUUSD (2007 →) and BTCUSD (2018 →). The module
runs a full-history backtest rather than search/validation/holdout, so the pre-declared reporting
is:

1. **Full history**, strategy and inverse.
2. **Both halves** — for gold 2007–2022 against 2023–2026, for bitcoin 2018–2022 against
   2023–2026 — because the owner's settings were chosen on the recent window and the earlier one is
   the closer thing to out of sample.
3. **Trade counts per regime**, since a filter that leaves 40 trades has told us nothing.

## What would count as success, fixed in advance

The filter is worth keeping only if **all** of these hold:

1. Compression beats `all` on **both** markets, not just gold.
2. It beats `all` in **both halves** of each market's history, not only the recent one.
3. At least 100 trades survive the filter per market, so the comparison is not noise.
4. Compression still beats its own inverse by a wider margin than `all` does.

If compression wins on gold's recent window only, the recorded conclusion is that the hypothesis
came from gold's recent window and failed to generalise — which is exactly what happened to the
Aurum Flow filters, and the reason conditions 1 and 2 are written down now rather than after the
numbers arrive.
