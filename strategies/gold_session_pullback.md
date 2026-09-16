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
   - **Time stop — OPEN, not specified by the owner.** Proposal to confirm before the backtest: flat at 21:00 UTC
     (end of NY) so no position pays the overnight gold swap. Must be fixed before testing, not chosen on results.
   - Invalidation: none beyond stop/targets/time stop (no discretionary exits).

5. **Risk**
   - Risk 0.5 % of equity per trade, sized from R to the stop.
   - Max 2 entries per UTC day.
   - **Max open positions — OPEN, not specified.** Proposal: 1 at a time.
   - Max daily loss 3 %: no new entries for the rest of the UTC day once closed + floating P&L ≤ −3 % of the day's
     starting equity. With 0.5 % risk and 2 trades a day this cap only binds on gaps or slippage; note it is
     effectively a gap guard, not a routine limit.
   - Halt at 15 % drawdown from the equity peak: stop trading, owner review required to restart.
   - Correlation: single instrument, but the live account also trades gold through other experts (for example
     magic 888888 and the Gold Reaper on demo). Their combined XAUUSD exposure is not controlled by this strategy.

6. **Filters**
   - Session: London/NY entry window above.
   - News: no entries from 30 min before to 30 min after a High-impact USD event (`src/economic_calendar.py`,
     `news_window`). **Testability gap:** that module caches only the current week's ForexFactory export, so the
     filter cannot be applied to history as it stands; a historical calendar source is required, or the backtest
     must report results without the news filter and label them so.
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
   - News filter not testable on history (section 6).
   - **Stop trading when**: the 15 % drawdown halt fires; live expectancy < 0R after 30 trades; rolling 50-trade
     expectancy < −0.10R; or three consecutive losing calendar months.

10. **Status** — idea (2026-09-16, owner specification). Not backtested, no code, no paper or live use.
    Before any backtest: close the two OPEN items (time stop, max open positions), confirm the rejection-candle
    definition, decide how the news filter is handled on history, then run `/backtest` with a locked holdout.
