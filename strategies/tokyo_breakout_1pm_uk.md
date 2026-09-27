# 1pm UK Tokyo-range breakout continuation

Owner's rule, 27 September 2026: at 13:00 UK time, wait for a break of the Tokyo session high or low and
trade the continuation. Implemented in `src/tokyo_breakout.py`; full result table in
`.claude/memory/BASELINE.md` under 2026-09-27.

**Status: backtested, NOT promoted.** Never traded. No live input, preset, demo strategy or EA changed.

## Hypothesis

The Asian session sets a range that the London afternoon resolves; a break after 1pm UK, in the direction of
the break, continues often enough to pay for the range width as a stop.

### What would prove it wrong

Stated before the run: fewer than 100 closed holdout trades; a non-negative after-cost `expectancy_r`
failing to appear; or an inverse control that traded and did NOT lose. Any of those refutes it.

## Instruments and timeframe

XAUUSD, 15m bars. 15m is fine enough to place a 13:00 trigger and a session range, and coarse enough that
the stop (the full Tokyo range) is many times one candle - the unit error recorded twice in LESSONS.md.

## Entry

* Tokyo range = high/low of 00:00-06:00 UTC (also tested 00:00-08:00). Complete by 08:00 UTC, so it is never
  read before it has formed.
* Trigger window 13:00-20:00 **Europe/London**, so BST and GMT both land on the owner's 1pm.
* First 15m close beyond the range in that window; long above the high, short below the low. One per day.

## Exits

Stop at the far end of the Tokyo range. Target 1R, 2R or 3R of that width. Time exit 32 bars (8 h).

## Risk

1 % of equity from the system's sizing function; one open trade per asset; AVOID days and the tier-1 news
window block entries.

## Success metric

After-cost `expectancy_r` > 0 on >= 100 holdout trades, beating an inverse control that traded and lost.
**Met.** Promotion additionally needs a deflated Sharpe >= 0.95 counting all six trials, and consistency
across the search and validation splits - **neither measured yet**, which is why this is not promoted.

## Known risks

* The holdout is 5.5 months and one regime; the 15m series is capped at 50,000 bars.
* Session-range behaviour changes with volatility regime; a quiet Asian session gives a narrow range and a
  very tight stop.
* Six configurations is modest but not one: the best row is still the best of six.

## Status

| date | status | note |
|---|---|---|
| 2026-09-27 | backtested | 6/6 profitable, 103-109 trades each, all beat a losing control, 0 ambiguous exits. Gold fell 10.5 % over the same window. Deflated Sharpe unmeasured; not promoted. |
