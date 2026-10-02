# Watchlist Criteria

Source of truth for the pre-market scanner. Two setups, both already backtested. If the scanner and
this file ever disagree, this file wins and the scanner gets fixed.

Nothing in here is a suggestion. Every line is a filter the scanner encodes literally, so a rule that
is fuzzy here becomes a bug there.

## How to read the rules

- **All required means all required.** These are AND filters, not a scorecard. Four out of five is a
  fail, not a yellow.
- **The comparison signs are deliberate.** `>` is strictly greater, `>=` includes the number itself.
  Day trading gap is `> 3%` so exactly 3.00% fails. Swing gap is `>= 8%` so exactly 8.00% passes.
  Same for market cap: day is `> $1B`, swing is `>= $800M`. Do not round these into each other.
- **Times are New York, always.** The system runs on UTC, so the scanner converts. During daylight
  time New York is UTC minus 4, in winter it is UTC minus 5. Hard coding either one breaks the
  watchlist twice a year, which is the kind of bug nobody notices until the fills look weird.
- **A level you cannot measure is a fail, not a guess.** If market cap or RVOL is missing, the name
  does not make the list. Write "no data" and move on.

---

# 1. Day Trading Watchlist: Trend Join Long

**Evidence:** 54.6% win rate, profit factor 1.59, over 280 trades.

280 trades clears the 100 trade bar, so this one is real and not a small sample story. A 54.6% win
rate with PF 1.59 means the winners are bigger than the losers, which is the whole point. It also
means almost half of these lose, so the stop is not optional.

## Pre-market selection, all five required

| # | Rule | Exact test |
|---|---|---|
| 1 | Gap versus previous close | `gap_pct > 3` |
| 2 | Price | `price > 3.00` USD |
| 3 | Market cap | `market_cap > 1_000_000_000` |
| 4 | Pre-market relative volume | `rvol > 1.5` |
| 5 | Breaking above yesterday's high | `price > prev_daily_high` |

Rule 5 is checked in pre-market. We want it already pushing through yesterday's high before the bell,
not hoping it gets there later.

## Intraday plan

**Window:** 10:00am to 3:30pm ET. No new triggers outside it. The first thirty minutes are skipped on
purpose, that opening spike is where the account goes to die.

**Trigger:** price above the pre-market high **AND** above the prior high of day. Both, not either.
One of them alone is just a stock being green.

**Stop and 1R:** 1% below the pre-market high, or 1% below the low of day, whichever is lower. That
distance is your 1R. Everything else is measured off it, so get it right before you size.

**Scaling:**

| Leg | Action |
|---|---|
| First third | Out at +1R |
| Second third | Out at +2R |
| Last third | Trail on the 21 EMA |

**Hard close:** flat by 3:51pm ET. Not 3:55, not the bell. The last few minutes are somebody else's
problem.

Note the two clocks. 3:30pm is the last time a new trigger counts. 3:51pm is when everything is
closed whether it worked or not.

---

# 2. Swing Watchlist

**Evidence:** split by catalyst, because the two behave nothing alike.

| Catalyst | Win rate | Profit factor |
|---|---|---|
| News, no earnings | 57.6% | 5.34 |
| Earnings on the gap day | 44.7% | 2.57 |

Read that properly. News gaps win more often and pay far better. Earnings gaps lose more than half
the time and still come out ahead because the winners run. Those are two different trades wearing the
same shirt, so the catalyst type gets recorded on every name.

Trade counts for these two were not given, so they are not written here. When they turn up they go in
this table, and until then neither row claims to clear the 100 trade bar.

## Pre-market selection, all six required

| # | Rule | Exact test |
|---|---|---|
| 1 | Gap | `gap_pct >= 8` |
| 2 | Price | `price > 3.00` USD |
| 3 | Open above yesterday's high | `open > prev_daily_high` |
| 4 | Open above the 200 day average | `open > sma200` |
| 5 | Market cap | `market_cap >= 800_000_000` |
| 6 | A real catalyst | earnings on the gap day, **or** news with no earnings |

Rules 3 and 4 use the **open**, not the pre-market price. That is the difference from the day setup,
which checks the level in pre-market. Here we wait for the actual open to print.

`sma200` is the 200 day average close from closed days only. It comes back empty until 200 closed
days exist, and when it is empty the name fails rule 4. Do not swap in a shorter average to fill the
gap, that is a different test wearing the same name.

Rule 6 is binary and it is the whole setup. A gap with no catalyst behind it is not on this list.

## Entry and exit management, honestly

**Not built yet.** Swing entry and exit rules are still being worked out.

So swing names are **starter ideas only**. No stop, no target, no R multiple goes on a swing row,
because we have not validated one. Writing a number there to make the table look finished would be
inventing a risk level, and a made up stop is worse than no stop because people trust it.

A swing row says what it is and why it qualified. That is all it is allowed to say.

---

# What the scanner encodes today, and what it does not

`src/tjl_scanner.py` already exists and gets part of this right. Worth knowing before anyone assumes
the rules are live.

**Already matching:**

- The 10:00 to 15:30 New York window, with a weekday gate.
- Pre-market high built from the 04:00 to 09:30 window, session high from 09:30 onward, both
  excluding the bar still forming.
- The trigger: price above the pre-market high and above the session high so far.
- `sma200` as the mean close of the last 200 closed days.

**Not there yet:**

- **None of the pre-market selection filters.** No gap percent, no price floor, no market cap, no
  RVOL. Those four are the whole first half of both setups and the scanner has no feed for them.
- **No US equity universe.** It currently runs on gold and bitcoin only, which are the markets this
  system trades. The watchlist setups above are written for stocks.
- **No swing path at all.** Rules 3 and 4 use the open, which the scanner does not evaluate.

**Two things called Trend Join Long, and they are not the same thing.** Worth writing down before
somebody "fixes" one to match the other.

`src/tjl_scanner.py` requires yesterday's close to be above the 200 day average before it calls a
daily breakout. That condition is not in the equity day rules above. It is not a bug: that scanner
was built to the earlier TradingView specification for **gold and bitcoin**, where the 200 day
average was part of the setup, and it has been running on those two markets since. Its own docstring
says so.

So:

- **`src/tjl_scanner.py` stays as it is**, on gold and bitcoin, with its 200 day condition. It is not
  the equity scanner and must not be bent into one.
- **The equity rules above get their own implementation.** They need gap percent, price, market cap
  and RVOL, which that scanner has no feed for anyway.
- **Do not copy the 200 day condition into the equity day path.** It would make the day filter
  stricter than the one backtested at 54.6%, and the live hit rate would drift from the backtest for
  a reason nobody would find. On the equity side the 200 day average belongs to the swing setup,
  rule 4, and nowhere else.

---

# Changing this file

1. A rule changes here first, then in the scanner. Never the other way round.
2. Any new rule needs a backtest with a trade count, not an opinion. Under 100 trades is insufficient
   evidence and gets labelled that way.
3. Never loosen a filter to let a name through. If it did not qualify, it did not qualify, and the
   interesting question is why you wanted it anyway.
4. Keep the comparison signs exact. Most of the arguing about watchlists is really arguing about
   whether a rule was `>` or `>=`.
