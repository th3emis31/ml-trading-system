# Gold Session Pullback (H1 entries, H4 trend) — long and short

Owner's specification of 16 Sep 2026. Sibling of `swing_trend_pullback.md` (the live SwingTrendPullback EA:
H4, long only, pullback into EMA21, did not clear the deflated Sharpe bar). The Strategy Lab `ema_pullback` family
(`src/strategy_lab.py`) already computes fast-EMA pullbacks, a trend-EMA filter and swing ATR stops; this strategy
needs three additions to it: an H4 trend on H1 bars, a rejection-candle rule and split take-profits.

1. **Hypothesis** — while the H4 trend is intact, H1 pullbacks to EMA20 that are rejected during London/NY liquidity
   resume in the trend direction often enough to earn more than costs, because session liquidity absorbs the
   pullback and trend-following flows re-enter at the moving average.

2. **Instruments and timeframe** — XAUUSD only, broker (MT5) candles. Signal and entry bars H1; trend bars H4.
   Entries only on H1 bars that open inside London 07:00–16:00 UTC or New York 12:00–21:00 UTC (the session
   definitions `src/features.py` already uses; fixed in UTC, no DST shift). No entries 21:00–07:00 UTC.

3. **Entry rules** — evaluated once, on the close of an H1 bar (bar *t*); fills at the open of bar *t+1*.
   - **Trend (H4)**: the last H4 bar that closed at or before the close of bar *t*: EMA50 > EMA200 → longs only;
     EMA50 < EMA200 → shorts only.
   - **Pullback (H1)**: long: low(*t*) ≤ EMA20(*t*) and close(*t*) > EMA20(*t*). Short mirrored: high(*t*) ≥ EMA20(*t*)
     and close(*t*) < EMA20(*t*).
   - **Rejection candle (bar *t*, declared before any test, not tuned)**: long: close > open, close in the top third
     of the bar range, lower wick ≥ 50 % of the range. Short mirrored (close < open, bottom third, upper wick ≥ 50 %).
     Bars with range < 0.3 × ATR14(H1) are ignored.
   - No grade or confidence input; every qualifying bar is a signal, subject to the filters and risk caps below.

4. **Exit rules**
   - **Stop**: long: lowest low of the last 5 H1 bars (swing) − 1.5 × ATR14(H1). Short: highest high + 1.5 × ATR14.
     R = |entry − stop|. Skip the trade if R < 0.5 × ATR14 or R > 4 × ATR14.
   - **TP1** at 1.5R: close 50 %, move the stop on the rest to break-even (entry price).
   - **TP2** at 3R: close the remaining 50 %.
   - Stop and targets are set at the broker on entry; checked on the H1 bar path, stop first when both are touched
     in one bar (the conservative order the walk-forward engine uses).
   - **Time stop (owner decision 2026-09-16, before any test):** flat at 21:00 UTC (end of NY), so no position pays
     the overnight gold swap. In the backtest: exit at the open of the 21:00 bar, or the last close of the UTC day.
   - Invalidation: none beyond stop/targets/time stop (no discretionary exits).

5. **Risk**
   - Risk 0.5 % of equity per trade, sized from R to the stop.
   - Max 2 entries per UTC day.
   - **Max open positions (owner decision 2026-09-16):** 1 at a time.
   - Max daily loss 3 %: no new entries for the rest of the UTC day once closed + floating P&L ≤ −3 % of the day's
     starting equity. With 0.5 % risk and 2 trades a day this cap only binds on gaps or slippage; note it is
     effectively a gap guard, not a routine limit. With one position at a time a new entry only happens when flat,
     so closed P&L equals closed + floating at that moment; the backtest checks closed equity.
   - Halt at 15 % drawdown from the equity peak: stop trading, owner review required to restart.
   - Correlation: single instrument, but the live account also trades gold through other experts (for example
     magic 888888 and the Gold Reaper on demo). Their combined XAUUSD exposure is not controlled by this strategy.

6. **Filters**
   - Session: London/NY entry window above.
   - News: no entries from 30 min before to 30 min after a High-impact USD event (`src/economic_calendar.py`,
     `news_window`). That module caches only the current week's ForexFactory export, so this 30-min filter cannot be
     applied to history.
   - **Tier-1 event window (added 2026-09-16 after the FOMC candle, owner request):** no new entries from 60 min
     before to 90 min after an FOMC decision, FOMC press conference, FOMC minutes, CPI or NFP release
     (`src/event_defence.py`). History comes from `data/historical_events.csv` (official Fed and BLS schedules,
     2018 to 16 Sep 2026), so the backtest runs this window both off and on and reports both. The broader 30-min
     High-impact filter is still not testable on history.
   - **Volatility breaker (same request, reported as a separate labelled variant):** an H1 range above 3 × ATR14
     (ATR up to the previous bar) blocks new entries on the next 3 bars.
   - Regime: the H4 EMA50/EMA200 trend only. No market-condition gate beyond it; entries are refused on AVOID if the
     live plan layer adds one.

7. **Model inputs** — none. Rule-based; the only constants are the ones declared in sections 3–6. No fitted
   parameters, no ML labels, so no label leakage; look-ahead risk is limited to H4/H1 alignment (section 3 uses the
   last H4 bar closed at or before the H1 close) and to filling on bar *t+1*.

8. **Success metric** — promotion needs all of:
   - Net expectancy > +0.20R per trade after spread, commission, slippage and swap, over ≥ 100 out-of-sample trades
     that no rule or constant choice ever saw;
   - Max drawdown < 10 % at 0.5 % risk on that out-of-sample period (the 15 % halt in section 5 is a live safety
     stop, not an acceptance limit);
   - The standing owner bar: deflated Sharpe ≥ 0.95 counting every variant tried (owner rule of 14 Sep 2026).
   - **Baselines to beat:** zero expectancy; the same entry bars with the opposite side (tests whether the trend
     filter adds anything); and the best H4 sibling in `swing_trend_pullback.md` (steady preset: holdout PF 1.47,
     +40.8 %, DD 11.3 %, but deflated Sharpe 0.41).

9. **Known risks / failure modes**
   - Weak prior: the H4 pullback sibling on the same metal did not clear the bar.
   - Regime dependence: 2024–2026 gold trended strongly up, so an H4 filter will be mostly long; shorts may be too
     few to judge, and the long result may be the trend, not the entry.
   - Break-even at TP1 turns many winners into scratches; expectancy can sit near zero even with a high TP1 hit rate.
   - Session-open spread widening (07:00 London, 12:30 US data) inflates costs on exactly the bars that trigger.
   - Trade count: strong trends often do not retrace to EMA20, so 100 out-of-sample trades may need several years of
     H1 broker history.
   - Only the tier-1 events are testable on history; other High-impact USD releases are not filtered in the backtest.
   - **Stop trading when**: the 15 % drawdown halt fires; live expectancy < 0R after 30 trades; rolling 50-trade
     expectancy < −0.10R; or three consecutive losing calendar months.

10. **Status** — backtest code written (2026-09-16): `python -m src.gold_session_pullback_lab run`
    (`src/gold_session_pullback_lab.py`, tests `tests/test_gold_session_pullback_lab.py`). Owner decisions closed
    before testing: flat 21:00 UTC, one position, the rejection candle above, news filter run off and on (tier-1
    window from the historical file). Split: the Strategy Lab's locked XAUUSD 1h boundaries (holdout from
    2025-01-08 01:00). Three variants (no filter, tier-1, tier-1 + breaker) count as three trials for the deflated
    Sharpe. Results go to `.claude/memory/BASELINE.md`. No paper or live use.
    **Backtest 2026-09-16 (commit 7d6d3c6): FAIL** on every variant - holdout +0.028R / +0.032R (tier-1) /
    +0.039R (tier-1 + breaker) over 79 / 75 / 73 trades, deflated Sharpe 0.60-0.65 (bar 0.95), development 2018-2024
    negative (-0.03R); 80 % of trades end at the 21:00 time stop. Not promotable.
    **Demo (owner request 2026-09-16):** `src/demo_session_pullback.py` trades it on the Vantage demo account 11581419
    only (hard-coded refusal of any other account), magic 440502, two 0.01-lot legs (A: TP 1.5R, B: TP 3R, B to
    break-even after A), one trade at a time, 2 a day, flat 21:00 UTC; kill switches on this strategy's own P&L
    (-3 % day stop, 15 % drawdown halt) and any MT5 error halts. Built in dry run; sending orders is the owner's switch
    (`dry_run` false) after seeing the backtest. Demo trades are forward evidence only; the evidence bar is unchanged.
