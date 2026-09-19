# Aurum Flow (Trade Smart FX Tools) — what the code actually does

**Status: research only. Never trades. No live input, preset or demo strategy changes on the
back of it.** The expert itself is not edited, compiled, attached or run anywhere; this is a
reading of source the owner supplied, and a replica of its entry rules tested on broker candles
outside any terminal.

Source: `Aurum Flow.mq4`, `#property version "7.05"`, Sahitya / Trade Smart FX Tools. The owner
also sent the compiled `Aurum_Flow_Lite.ex4` (v2.03), which holds only three readable strings
and cannot be read; the `.mq4` below is what the analysis rests on.

## The headline: the marketing and the code disagree

The product page and the `#property description` lines say the EA "combines supply & demand
zones, volume logic, and pending order structure". In the code:

* **The supply/demand zones are decorative.** `DrawSupplyDemandVisual()` computes
  `g_supplyHigh/g_supplyLow/g_demandHigh/g_demandLow` and draws two rectangles.
  **No trading decision reads any of those four variables.** `TryEntries()` never mentions them.
  The `UseZoneStopLoss` and `ZoneSLBuffer` inputs exist but nothing in the file uses them; the
  stop is always `entry ± SL * P()`, a fixed distance.
* The zone finder is also inverted and mis-indexed: a bullish candle (`close > open`) with body
  ≥ 60 % of range is stored as the **supply** (sell) zone and a bearish one as **demand**, which
  is backwards, and its "previous candle" check reads `i-1`, which in MQL4 shift indexing is the
  *newer* bar, not the older one. Harmless, because nothing trades off it.
* **There is no volume logic anywhere in the file.** No `Volume[]`, no tick-volume reference.

What actually decides trades is a trendline breakout with a moving-average filter.

## The real entry rule

1. **Two trendlines, rebuilt every `StructureRefreshBars` = 40 bars.** `FindTwoHighPeaks` scans
   the last `StructureDepth` = 200 bars for 3-bar fractal swing highs (`high[i] > high[i±1]`) and
   takes the **two highest** of them, forced at least `StructureSpacing` = 100 bars apart.
   `TL_HIGH` is the ray through those two points; `TL_LOW` mirrors it on the two lowest swing lows.
   Because it selects the two *highest* highs rather than recent structure, a break of `TL_HIGH`
   is close to "price exceeded the highest high of the last 200 bars" — a Donchian breakout with
   a slope, not a classical trendline.
2. **Two consecutive closed bars beyond the line.** At the open of a new bar, with candle 1 and
   candle 2 being the last two closed bars: buy if `high[1] ≥ TL_HIGH(t1)` **and**
   `high[2] ≥ TL_HIGH(t2)`; sell if `low[1] ≤ TL_LOW(t1)` **and** `low[2] ≤ TL_LOW(t2)`. Both
   true at once is skipped.
3. **Moving-average filter** (`EnableMAFilter` = true): `iMA(MAPeriod = 600, shift 0, SMA, close,
   bar 1)`. A buy needs price at or above the SMA600, a sell at or below it. The inputs are
   relabelled in the GUI as "AFCX Time / Heigth / Code / Width", which hides what they are.
4. **Entry is a STOP order, not a limit.** `OP_BUYSTOP` at `high[1] + PendingOrderDistance`
   (130 points), `OP_SELLSTOP` at `low[1] − 130 points`, expiring after
   `PendingExpiryMinutes` = 60 minutes. So it is a breakout continuation entry — it buys *higher*
   than the signal bar, not on a retest as the marketing implies.
5. **Fixed stop and target**: `SL` = 2100 points, `TP` = 1800 points. `P()` returns
   `Point × 10` when `Digits` is 3 or 5, otherwise `Point`, so on gold quoted to 2 or 3 decimals
   both give $0.01 per point: **stop $21.00, target $18.00.**

### The arithmetic problem in point 5

The stop is larger than the target. Reward:risk is 1800 / 2100 = **0.857 R**. Ignoring costs,
break-even needs a **53.8 % win rate**; with spread and swap it needs more. A strategy can be
built this way, but it means the published 66.23 % win rate is not evidence of an edge on its
own — it is the minimum the structure demands.

## What produces the published curve: the recovery cascade

`EnableRecoveryMode2` defaults to **true**. Once an open position is `Recovery2TriggerPips` = 200
pips against (on gold, `GetPipSize()` = `Point` = $0.01, so **$2.00**), then on every new candle
that price moves a further $2.00 against it, `AddRecovery2Trade` opens **another position in the
same losing direction** with a linearly growing size (`baseLot × count`: 0.01, 0.02, 0.03 …) up
to `Recovery2MaxTrades` = **60** positions, capped per order by `MaxLot` = 0.1.

Those added positions are sent as `OrderSend(..., 0, 0, ...)` — **stop loss 0 and take profit 0.
The recovery trades have no stop at all.** This directly contradicts the vendor's "fixed Stop
Loss and Take Profit on every trade".

`CheckMultiDealBreakeven` then closes **everything** the moment total floating profit reaches
`$0.01` while more than one position is open. That is the mechanism that manufactures a high win
rate and a smooth equity curve: most cascades come back to break even and are booked as wins.
The only backstop is `CheckFloatingLossLimit`, which closes all positions at a floating loss of
`FloatingLossCooldownTrigger` = **−$100 flat** — a fixed dollar figure, not a share of equity, so
it is 20 % of the vendor's own $500 test account and 1 % of a $10,000 one.

This is a martingale/grid recovery layer on top of a breakout entry. It is the standard shape of
an equity curve that rises steadily and then gives everything back in one sequence.

## Other findings worth recording

* **`TradeInNovember = false`, `TradeInDecember = false`.** Two months are switched off by
  default with no mechanism reason. That is a curve-fitting signature: the months that lost in
  the vendor's own backtest were removed rather than explained.
* **`MinSpreadPoints = 9`** — the EA refuses to trade when the spread is *too low*. In a tester
  with a tight fixed spread it may never trade at all, and live behaviour becomes
  broker-specific.
* **`LosingStreakTrigger = 1`** with `StreakPauseMinutes = 500`: a single loss pauses trading for
  over eight hours, on top of `CooldownMinutes` = 10 and a 24-hour pause after a floating-loss
  stop-out. It trades rarely by construction.
* **It cannot be backtested as shipped.** `OnInit` calls `WebRequest` to
  `tradesmartfxtools.in/LicenseKey/aurum-flow-lite.php` and returns `INIT_FAILED` unless the
  server answers `OK`, or a `GlobalVariable` shows a successful check within `GraceHours` = 24.
  `WebRequest` is not permitted in the MT4 Strategy Tester, so the licence path fails there and
  the EA blocks itself. This is consistent with the vendor describing the Lite build as "for
  observation and learning — not for profit generation".
* **It reports account data to the vendor** on every check, every 60 seconds: account number,
  server, broker and **account balance**, in the URL query string.
* `MagicNumber = 060701111` has a leading zero, so MQL4 reads it as octal: the actual magic
  number is **12812873**, not 60701111. Cosmetic, but it shows the level of care.

## What is replicated here, and what cannot be

`src/aurum_flow_lab.py` implements **rules 1–5 above: the entry edge**, through the system's
existing engine (`strategy_lab.simulate_orders`), with spread and overnight swap, one position at
a time, a locked holdout, and deflated Sharpe counting every trial.

It deliberately does **not** implement the recovery cascade. The engine holds one position at a
time, and a faithful 60-position martingale cannot be expressed in it. That is not a gap in the
test — it is the point of the test. **If the entry edge is negative on its own, then whatever
the vendor's curve shows is being produced by the cascade, and a cascade with no stops and a
flat −$100 backstop is a loss that has not happened yet, not an edge.**

Reported for each variant: trades, win rate, profit factor, expectancy in R, max drawdown, and
the **inverse baseline** (the same signals traded the other way), because a long-biased breakout
on gold's 2024–2026 rise can look profitable purely from market direction.

## The pre-declared variant grid

Declared before any run. Nothing is tuned on the holdout.

| Knob | Values | Default in the EA |
|---|---|---|
| `structure_depth` | 200 | 200 |
| `spacing` | 100 | 100 |
| `refresh_bars` | 40 | 40 |
| `ma_period` | 0 (off), 600 | 600 |
| `entry_points` | 130 | 130 |
| `sl_points` / `tp_points` | 2100 / 1800, and 2100 / 3150 (1.5 R) | 2100 / 1800 |
| `block_nov_dec` | True, False | True |
| `side` | both | both |

That is 2 (`ma_period`) × 2 (stop/target) × 2 (`block_nov_dec`) = **8 variants per market and
timeframe**, run on XAUUSD 15m and 1h. The 1.5 R pair and the `block_nov_dec = False` row are
included precisely to separate "the EA's settings" from "this mechanism", and the trial count
passed to the deflated Sharpe includes every one of them.

## The verdict rule, fixed in advance

The standing bar applies unchanged: after costs, ≥ 30 holdout trades for a single rule strategy,
drawdown within limits, and **deflated Sharpe ≥ 0.95** counting all trials. The bar is not
lowered for this candidate. A failure is recorded in `.claude/memory/BASELINE.md` exactly as a
pass would be, and the honest conclusion on failure is that this mechanism does not survive on
gold at these costs — not that it needs more tuning.

## The MT5 port

`strategies/mt5/AurumFlow_MT5.mq5` is a faithful translation of the owner's MQL4 source,
requested on 19 September 2026. It compiles clean (**0 errors, 0 warnings**, MetaEditor build in
`C:\Users\th_em\MT5_SwingTrend_Tester`, which is the separate Strategy Tester install — no
running terminal was touched).

Fidelity: all 37 inputs and all 8 section separators carry over with identical names and
defaults, and the variables the original deliberately left non-input (`Recovery2TriggerPips`,
`Recovery2CloseProfit`, `Recovery2MaxTrades`, `Recovery2SingleCloseProfit`, `MAMethod`,
`EnableSizeAdjustment`, `SizeAdjustmentStep`, `RecoveryConfirmations`, `ZoneLookbackBars`) are
non-input there too. `EnableRecoveryMode2` is left **on**, as shipped: changing a default
silently would be worse than porting the risk faithfully, because the owner would believe they
were running the same thing.

**The licence check is intact and unchanged.** It still calls
`tradesmartfxtools.in/LicenseKey/aurum-flow-lite.php` on init and every 60 seconds, still refuses
to initialise unless that server answers `OK` or a success is cached within `GraceHours`, and
still sends the same query string. Nothing was removed or weakened; the vendor keeps full control
of which accounts can run it. `tests/test_aurum_flow_mt5_port.py` fails if that ever changes.

The places MQL5 forced a different API are each marked `PORT:` in the file (19 of them). The
substantial ones:

| MQL4 | MQL5 | Why it mattered |
|---|---|---|
| `OrdersTotal()` covers trades **and** pendings | `PositionsTotal()` and `OrdersTotal()` are separate lists | every enumeration loop had to be split; the original's "skip pending orders" branches become unnecessary |
| `OrdersHistoryTotal()` + `MODE_HISTORY` | `HistorySelect()` + deals, filtered to `DEAL_ENTRY_OUT` | closed-trade P&L is now profit + swap + commission per closing deal |
| `OrderProfit()+OrderSwap()+OrderCommission()` on an open trade | `POSITION_PROFIT` + `POSITION_SWAP`, commission fetched via `HistorySelectByPosition` | positions carry no commission field, and the breakeven logic fires at **$0.01**, so omitting commission would close baskets early |
| `iMA(...)` returns a value | indicator handle + `CopyBuffer` | and MQL5's applied-price constants are all shifted by one, so `MAPrice = 0` is mapped explicitly to `PRICE_CLOSE` |
| `iHigh/iLow/iHighest/iLowest/iBarShift` | written out with `CopyHigh`/`CopyLow` | avoids depending on which MT5 build exposes which timeseries built-in |
| `TimeMonth/TimeYear/TimeDayOfWeek/...` | `MqlDateTime` via `TimeToStruct` | those functions no longer exist |
| `ArrayRemove` (their own helper) | renamed `RemovePendingExpiryAt` | `ArrayRemove` is a built-in name in MQL5 |
| `MagicNumber = 060701111` | `12812873` | the leading zero made it an **octal** literal, so MT4 was really using 12812873; the port uses that number so both platforms share one magic |
| `OrderSend(..., expiry, colour)` | `CTrade.BuyStop/SellStop` with `ORDER_TIME_GTC` | the original tracked expiry itself, which is kept, so brokers that reject `ORDER_TIME_SPECIFIED` still work |

**It cannot be backtested in MT5's Strategy Tester either**, for the same reason as the MT4
build: `WebRequest` is not permitted there, so the licence call fails and `OnInit` returns
`INIT_FAILED`. That is the vendor's design, not a porting defect. The entry rules were therefore
tested through the system instead, and those results are in the rows above.
