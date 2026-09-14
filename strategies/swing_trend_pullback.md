# Swing Trend Pullback (21/50 EMA) — long only

Source: MT5 expert `SwingTrendPullback.mq5` (terminal `C:\Program Files\MetaTrader 5`,
data folder `D0E8209F…`, Vantage demo account 25446287, XAUUSD H4), a port of the
TradingView script "Swing Trend Pullback Strategy (21/50 EMA) - Long Only".

1. **Hypothesis** — in an established uptrend, a pullback into the rising EMA21 after a
   momentum push is bought by trend followers, so the trend resumes more often than it
   fails.
2. **Instruments and timeframe** — XAUUSD H4 (also attached briefly to XAUUSD H1 and
   BTCUSD H1 on 12 Sep). All sessions.
3. **Entry rules** (closed bar, market order at the next bar's open; long only)
   - Uptrend: EMA21 > EMA50 and EMA21 rising versus 1 bar ago.
   - Momentum push: in the last 15 bars, max(high − EMA50) > 0.6 × ATR14.
   - Pullback: low ≤ EMA21 + 0.6 ATR and close ≥ EMA21 − 0.6 ATR; close > open; close > EMA50.
   - Filters: RSI14 > 40 (on); ADX14 > 20 (off by default).
4. **Exit rules**
   - Stop: min(lowest low of the last 5 bars, EMA21) − 1.5 × ATR.
   - Target: entry + 2 × (entry − stop).
   - Trailing stop: highest high since entry − 3.5 × ATR, never below the initial stop.
   - Time exit: close after 150 bars.
5. **Risk** — as coded: lot = 10% of balance × leverage ÷ (contract × price). At 1:500 on
   a ~90k balance that is about 10 lots of gold, i.e. a single stop can cost a third of the
   account. **Must be replaced by risk-per-trade sizing (e.g. 0.5–1% of equity at the stop)
   before any real-money use.**
6. **Filters** — no session, news or volatility-regime filter; long only.
7. **Model inputs** — none (pure rules).
8. **Success metric** — same bar as the research engine: after costs, profit factor ≥ 1.2,
   ≥ 100 trades, max drawdown ≤ 20%, positive on the validation folds **and** on the locked
   holdout, and better than buy-and-hold or clearly lower drawdown. Evaluated by
   `src/strategy_lab.py` on broker candles.
9. **Known risks / failure modes**
   - Long only: in a falling or sideways gold market it keeps buying dips that fail.
     Our 13 Sep rule check found every trend/breakout rule on gold 4h lost on 2016–2024
     folds and only won in the 2024–26 bull run.
   - `CloseMyPosition` sends volume 0, which MT5 rejects, so the 150-bar exit would not
     close the position.
   - `deviation = 0` can cause requotes on fast markets.
   - Oversized lots (see Risk).
10. **Status** — running on demo since 12 Sep 2026 (no entries logged yet as of 13 Sep).
    **Backtest 13 Sep 2026** (`python -m src.strategy_lab ea`, MT5 broker XAUUSD H4 candles
    2007-06-21 to 2026-09-11, costs 0.04% round trip, trailing stop on closed-bar highs as
    in the Pine script; the MQL5 port trails from bar opens):
    | Period | Trades | Win % | PF | Return | Max DD | Buy & hold |
    |---|---|---|---|---|---|---|
    | Search 2008-08 → 2022-08 | 189 | 33.3 | 1.03 | −2.4% | 35.6% | +99.7% |
    | Validation 2022-08 → 2024-08 | 63 | 47.6 | 1.79 | +23.9% | 4.2% | +43.2% |
    | Holdout 2024-08 → 2026-09 | 71 | 42.3 | 1.17 | +9.7% | 23.2% | +73.7% |
    Verdict: **fails** — loses over 14 years with a 36% drawdown and only works in strong
    gold uptrends; never beats simply holding gold. Profitable in 8 of 12 counted years.

    **v2.00 (13 Sep 2026)** — compiled 0 errors / 0 warnings, running on the chart (log
    "initialized on XAUUSD H4, v2.00"). Fixed: risk-% lot sizing (original selectable), time-exit
    close with the real volume/price/filling mode, slippage, closed-bar trailing, restart recovery.
    Added: long/short/both, trend-filter EMA, ATR stop mode, TP on/off, spread filter, push alerts,
    professional two-column dashboard (status, long/short checklist, position, next-trade risk,
    this EA's closed-trade statistics). Original defaults kept except lot sizing (now 1% risk).

    **Strategy Lab search of this family** (1,500 variants, XAUUSD 4H and 1H, same costs and
    splits): 286 variants passed search+validation on 4H, 116 on 1H; **none passes the
    holdout's deflated Sharpe bar (0.95)** once the ~750 trials per market are counted.
    Most consistent 4H variants, saved as MT5 presets in `MQL5\Presets`:
    | Preset | Search | Validation | Holdout (2024-08 → 2026-09) | Deflated Sharpe |
    |---|---|---|---|---|
    | `SwingTrendPullback_XAUUSD_H4_steady.set` (EMA13/100, trend EMA200, 1 ATR stop, 3R, 12 bars) | 422 tr, PF 1.32, +77.8%, DD 18.5% | 157 tr, PF 1.32, +17.3% | 174 tr, PF 1.47, +40.8%, DD 11.3% | 0.41 |
    | `SwingTrendPullback_XAUUSD_H4_trend_rider.set` (EMA21/100, trend EMA600, RSI>40, 1 ATR stop, 3.5 ATR trail, 50 bars) | 236 tr, PF 1.37, +51.0%, DD 16.5% | 82 tr, PF 1.52, +15.0% | 95 tr, PF 1.91, +56.0%, DD 13.8% | 0.62 |
    Both are long only and profitable in every period, but not yet proven beyond selection
    luck: forward-test on demo before any real money.

    **v2.01 (13 Sep 2026)** — an entry, max-hold exit or trailing-stop update rejected as market
    closed / requote is retried later in the same bar (`RetrySeconds`, default 60); exits are
    handled before entries so a signal on the exit bar can be taken; optional `MinLotMaxRiskPct`
    (0 = skip as before). Found by the MT5 tester: on D1, v2.00 placed 5 trades in 8 years because
    bars open at 00:00 while gold trades from 01:00. Compiled 0 errors / 0 warnings.

    **Best timeframe — real MT5 Strategy Tester** (separate portable copy
    `C:\Users\th_em\MT5_SwingTrend_Tester`, never trades; Vantage demo symbol spec incl. swap,
    1-minute OHLC, 100k USD, 1% risk; usable broker M1 history starts 2018):
    | Preset | Trades | Win % | PF | Net (after swap) | Swap | Max DD | Holdout PF |
    |---|---|---|---|---|---|---|---|
    | **H4 steady** | 642 | 37.2 | 1.30 | **+128%** | −61k | 23.1% | 1.51 (178 tr) |
    | H1 best | 565 | 12.2 | 1.33 | +117% | −88k | 20.3% | 1.53 (DD 26%) |
    | H4 trend rider | 356 | 25.6 | 1.30 | +71% | −68k | 30.2% | 1.93, but loses 2018–23 |
    | D1 best | 102 | 52.0 | 1.13 | +3.7% | −16k | 7.9% | 1.48 |
    Verdict: **H4 with the steady preset** is the best timeframe (profitable in validation and
    holdout, most trades, shortest holds so the least swap per trade). Still not proven beyond
    selection luck (deflated Sharpe 0.41): demo forward test only. Swap on long gold is the largest
    cost for every timeframe, so shorter holding periods matter more than the entry timeframe.

    **User's TradingView inputs vs MT5 (13 Sep 2026)** — preset
    `SwingTrendPullback_XAUUSD_H4_tradingview.set` (EMA21/51, push 15 > 0.5 ATR, tol 0.55, RSI>40,
    ATR12, swing stop 1.5 ATR, 2R, trail 4.5 ATR, 150 bars):
    | Test | Trades | Win % | PF | Net | Max DD |
    |---|---|---|---|---|---|
    | TradingView 2023-01 → 2026-09 (10k, no swap) | 67 | 41.8 | 1.59 | +4.0% | 2.6% |
    | MT5 2023-01 → 2026-09 (1% risk, swap) | 103 | 42.7 | 1.46 | +23.3% | 5.6% |
    | MT5 2018 → 2026 | 223 | 34.5 | 0.97 | −2.8% | 24.7% |
    | MT5 2018 → 2026 + EMA200 trend filter | 202 | 34.7 | 1.06 | +5.0% | 17.6% |
    MT5 confirms the TradingView edge for 2023–2026; before 2023 the same rules lost every year.
