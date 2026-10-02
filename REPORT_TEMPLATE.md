# Pre-Market Report Template

This is the blueprint. Every pre-market report follows these twelve sections, in this order, with
nothing added and nothing dropped. The analyst prompt and the merge prompt we write later both bind
to this file, so if a section moves here it moves everywhere.

**Voice:** casual, plain, risk first. Say what you would actually say out loud to a trading buddy at
6am. No hype, no guru talk, no em dashes. Short sentences beat clever ones. If a setup is mid, say it
is mid.

**Where the data comes from, so nothing gets invented:**

| Field in this template | Real source |
|---|---|
| `{{SYMBOL}}`, `{{GAP_PCT}}`, `{{PRICE}}`, `{{PM_VOLUME}}` | `premarket_gappers_<date>.json`: `symbol`, `gap_pct`, `price`, `premarket_volume` |
| `{{CATALYST}}`, `{{HEADLINE}}`, `{{CATALYST_SOURCE}}` | same file: `catalyst`, `headlines`, `catalyst_source` |
| `{{PMH}}`, `{{PREV_HIGH}}`, `{{PREV_CLOSE}}`, `{{SMA200}}`, `{{HOD}}`, `{{LAST}}` | `src/tjl_scanner.py`: `pmh`, `prev_daily_high`, `prev_daily_close`, `sma200`, `today_hod`, `curr_px` |

`sma200` is the 200 day average close built from closed days only, and it comes back `None` when
there are fewer than 200 of them. That is the field section 6 leans on for trend context, so when it
is `None` the row says "not enough history" instead of borrowing a shorter average and calling it the
same thing.
| Section 9 and 10 events | `src/event_defence.py` `events_near()` and `classify_calendar_event()`, which carry `kind`, `tier`, `forecast`, `previous`, `actual` |

Anything with no source gets written as "not measured" rather than guessed. A number nobody can trace
is worse than a blank.

---

## Conviction key

Used in every table and every verdict. One key, same meaning everywhere.

| Mark | Means | What you do with it |
|---|---|---|
| 🟢 **Green** | Both brains agree, catalyst is real and fresh, levels are clean | Tradeable. Still size for the stop, not for the excitement. |
| 🟡 **Yellow** | One brain likes it, the other has a flag. Or the catalyst is fine but the chart is messy | Watch only. Needs the open to confirm before you touch it. |
| 🔴 **Red** | Weak or recycled catalyst, heavy overhead, dilution risk, or the two brains disagree | Skip. Write down why, because the reason is worth more than the trade. |

---

# 1. Title and dated subtitle

```
# Pre-Market Report, {{WEEKDAY}} {{DATE}}
### Claude and Codex, two independent passes. Written {{GENERATED_AT_UTC}} UTC.
```

Both brains run the same data without seeing each other's work. The subtitle says so every time, so
nobody reads an agreement as two opinions when it was really one.

# 2. Disclaimer, one line

```
> The rules pick the watchlist. Both AIs judge quality, not direction. Research only, not financial advice.
```

One line, exactly that shape. The scanner picks who makes the list. The brains only grade what the
scanner handed them.

# 3. Summary

Three lines. No more.

```
**The tape:** {{ONE_LINE_ON_THE_TAPE}}
**What we are watching for:** {{THE_CATCH}}
**Two brain verdict:** {{AGREE_OR_SPLIT}}, {{N_GREEN}} green, {{N_YELLOW}} yellow, {{N_RED}} red.
```

The tape line is the market, not a stock. Risk on, risk off, chop, gap and fade, whatever it is.
The catch is the one thing that would change the plan if it goes the other way. The verdict counts
the marks so the rest of the page has to agree with it.

# 4. Pre-Market Gappers

Every name the scanner flagged, with its full catalyst headline. Not a trimmed version, the whole
thing, because half a headline is how a secondary offering reads like a partnership.

```
### {{RANK}}. {{SYMBOL}}  {{GAP_PCT}}%  ${{PRICE}}  PM vol {{PM_VOLUME}}
**Headline:** {{HEADLINE_FULL}}
**Catalyst:** {{CATALYST}}
**Source:** {{CATALYST_SOURCE}}
**Read:** {{ONE_OR_TWO_LINES}}
```

If the headline is missing, write "no headline found" and mark the name red in section 5. A gap with
no reason behind it is somebody else's trade.

# 5. Day Trading Watchlist

| Ticker | Catalyst | Levels | Plan | Codex check | Conviction |
|---|---|---|---|---|---|
| {{SYMBOL}} | {{CATALYST_SHORT}} | PMH {{PMH}}, prev high {{PREV_HIGH}}, HOD {{HOD}} | {{ENTRY_TRIGGER}}, stop {{STOP}}, first target {{TARGET_1}} | {{CODEX_AGREE_OR_FLAG}} | 🟢 / 🟡 / 🔴 |

Rules for this table:

- **Levels** are measured, never eyeballed. Pull them from the scanner fields. Round to the tick.
- **Plan** always names the trigger, the stop and the first target. A plan without a stop is a wish.
- **Codex check** is one short phrase, the thing the other brain saw that this one did not. If both
  brains said the same thing, write "agrees" and leave it.
- No entry wider than the stop you would actually take. If the stop is too far for your size, the row
  is yellow at best.

# 6. Swing Watchlist

| Ticker | Catalyst | Trend context | Idea | Codex check | Conviction |
|---|---|---|---|---|---|
| {{SYMBOL}} | {{CATALYST_SHORT}} | {{ABOVE_OR_BELOW_SMA200}}, {{MULTI_MONTH_CONTEXT}} | {{SWING_IDEA}} | {{CODEX_AGREE_OR_FLAG}} | 🟢 / 🟡 / 🔴 |

Different job from section 5. Here the catalyst has to survive more than a morning, so the question
is whether the story still matters in two weeks. Trend context uses the daily, not the five minute.

# 7. Market Trends of the Day

What is actually leading and lagging. Sectors, not stories. Three to five bullets, each one a fact
you could check later:

```
- {{SECTOR}} is {{LEADING_OR_LAGGING}}, {{EVIDENCE}}
- Small caps versus large caps: {{READ}}
- Risk appetite: {{READ}}
```

# 8. Technical Signals for Today

The index level stuff, stated as levels and not as feelings:

```
- {{INDEX}}: holding above {{LEVEL}} keeps {{CONSEQUENCE}}. Losing it opens {{CONSEQUENCE}}.
- Volatility: {{READ}}
- Breadth: {{READ}}
```

# 9. Economic Data, Rates and the Fed

Straight from the calendar. Every tier 1 release gets a row, with forecast and previous, because the
surprise is what moves price and you cannot see a surprise without the expectation.

| Time UTC | Event | Tier | Forecast | Previous | Actual | Why it matters today |
|---|---|---|---|---|---|---|
| {{TIME_UTC}} | {{EVENT_KIND}} | {{TIER}} | {{FORECAST}} | {{PREVIOUS}} | {{ACTUAL}} | {{ONE_LINE}} |

Two things that get written every time:

- **Releases that share a minute get listed separately.** Payrolls, average hourly earnings and the
  unemployment rate all print together, and the wage number is often the one that actually moves
  gold and rates. One line hides that, three lines do not.
- **Rates and Fed read:** one line on what the day's data does to the rate path, and one on any Fed
  speaker on the calendar.

# 10. Coming Up

Tomorrow, so nothing arrives as a surprise.

```
**Events:** {{TOMORROW_EVENTS_WITH_TIMES}}
**Earnings:** {{TOMORROW_EARNINGS}}
**Anything that changes today's plan:** {{READ}}
```

# 11. Skips and Traps

The section that saves more money than the watchlist makes. Every name that looked good and is not
good, with the reason:

```
- **{{SYMBOL}}**: {{WHY_SKIPPED}}. {{WHAT_WOULD_CHANGE_IT}}
```

Reasons worth naming by their real name: recycled catalyst, dilution or offering risk, overhead
supply from a prior multi month level, thin pre-market volume, no borrow, already up four days,
headline that is a reprint of last week.

# 12. Where the two brains landed

The honest part. Not a victory lap for whoever was right.

```
**Agreed on:** {{LIST}}
**Split on:** {{LIST}}, and why
**Rules versus discretion:** {{WHERE_THE_SCANNER_AND_THE_BRAINS_DISAGREED}}
**Claude's sharp catch:** {{ONE_THING}}
**Codex's sharp catch:** {{ONE_THING}}
```

Each brain gets one sharp catch, the thing the other one missed. If a brain did not catch anything,
write "nothing the other did not have". Making one up to fill the line defeats the whole point of
running two.

---

## Rules that apply to the whole report

1. **Levels come from the data, not from the chart in your head.** Every number traces to a field in
    the table at the top.
2. **No em dashes.** Commas, colons and full stops do the job.
3. **If a ticker has a weak catalyst or heavy overhead, say avoid it outright.** Do not bury it in a
    yellow. Yellow means unclear, red means no.
4. **Never fill a section with filler.** An empty section says "nothing today" and that is a real
    answer.
5. **The scanner picks the list, the brains grade it.** Neither brain adds a ticker the rules did not
    surface. If a brain wants one added, it goes in section 12 as a disagreement, not in section 5.
