"""Port of the "AI-Powered Breakout" Pine indicator, so it can be measured instead of admired.

Source: "AI-Powered Breakout with Advanced Features" (shortTitle "Breakout AI") by danylosam,
Pine v5, Mozilla Public License 2.0 - https://mozilla.org/MPL/2.0/. Supplied by the owner on
26 September 2026. This is a derivative work of an MPL-2.0 file and carries that attribution.

WHY THIS IS WORTH TESTING
-------------------------
Every strategy this system runs on gold is **long-only** - the live SwingTrendPullback EA took 151 long
and 0 short trades on 4h, the RF model 543 long to 20 short - which is the mechanism behind buying into
the 23 September selloff. This indicator emits breakdown signals as well as breakouts, so it is the
first candidate with a way to express "down".

TWO HONEST FINDINGS ABOUT THE SCRIPT ITSELF, BEFORE ANY RESULT
--------------------------------------------------------------
**1. The "AI" part is inert.** The script computes

    fastMA     = ta.ema(src, length)
    slowMA     = ta.ema(fastMA, length)
    adaptiveMA = fastMA + multiplier * (fastMA - slowMA)
    breakoutSignal   = ta.crossover(close, ta.highest(high, length))  and close > adaptiveMA
    breakdownSignal  = ta.crossunder(close, ta.lowest(low, length))   and close < adaptiveMA

`ta.highest(high, length)` INCLUDES the current bar, so it is always >= the current high, which is
always >= the current close. `close > highest(high, length)` therefore requires `close > high`, which
cannot happen. `breakoutSignal` can never be true, `breakdownSignal` likewise, and neither variable is
referenced anywhere else in the script - they are computed and discarded. So `adaptiveMA`, the only
thing in the script that the name "AI-Powered" could refer to, has no effect on any signal or plot.

That is not a criticism of the visible behaviour; it is a statement about what is actually being tested
here. What draws the triangles and fires the alerts is the pivot-cluster logic below, which is a
well-known "breakout finder" pattern, and that is what this module ports.

**2. It is an indicator, not a strategy.** There are no entries, stops, targets or position sizing in
it, so "does it work" is not a question the script can answer on its own. To measure it at all, exits
have to come from somewhere - see `CONFIG_NOTE`.

WHAT THE WORKING LOGIC ACTUALLY DOES
------------------------------------
A breakout is reported when price closes above a CLUSTER of previous pivot highs:

* pivot highs are found with `ta.pivothigh(prd, prd)`, confirmed `prd` bars late - so there is no
  lookahead, and a signal can only use pivots that were already confirmed;
* pivots older than `bo_len` bars are discarded;
* the bar must close up (`close > open`) and above the highest high of the last `prd` bars;
* walking from the newest pivot, it takes pivots BELOW the current close and tracks the highest of
  them as the level `bomax`;
* at least `mintest` of those pivots must sit inside a band of `chwidth` below `bomax`, where
  `chwidth` is `cwidthu` (3 %) of the recent 300-bar range - so the tolerance scales with volatility;
* the bar's open must be at or below `bomax`, i.e. the close broke the level on this bar.

The breakdown case is the exact mirror. The cluster requirement is the substance of the idea: it is not
"price made a new high", it is "price broke a level that had been tested repeatedly".
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from .volatility_trend_breakout import Candle, Config, Signal, atr

# Defaults exactly as the Pine inputs ship. NOT tuned here, and deliberately not swept - a swept
# parameter set would need its trial count deflating, and the point of the first run is to measure the
# thing the owner was given.
PRD = 5              # pivot period (input.int(defval=5, title="Period"))
BO_LEN = 200         # max breakout length
CWIDTHU = 0.03       # threshold rate 3 %
MINTEST = 2          # minimum number of tests
RANGE_LOOKBACK = 300 # the script's math.min(bar_index, 300) window for chwidth

CONFIG_NOTE = (
    "The indicator defines no exits, so this measurement borrows the exits, costs, sizing and slippage "
    "of the owner's own Volatility Trend Breakout Config - 1.5 ATR stop, TP1 1.3R on half, TP2 2.8R, "
    "2.2 ATR trail after TP1, 65-bar time exit, 0.04 %/side, 2 ticks slippage. That is a stated "
    "assumption, not a neutral choice: a different exit set would give a different number. It is used "
    "because it makes the result directly comparable with a strategy already running on this account, "
    "and because inventing new exits here would tune two things at once."
)


@dataclass
class Pivot:
    index: int
    value: float


def detect_pivots(candles: Sequence[Candle], prd: int = PRD) -> Tuple[List[Pivot], List[Pivot]]:
    """Confirmed pivot highs and lows, matching ta.pivothigh/pivotlow(prd, prd).

    A pivot at bar i is only CONFIRMED at bar i+prd, because it needs prd bars either side. The
    returned index is the pivot's own bar; the caller must not use a pivot before bar index + prd, and
    `breakout_signals` enforces that. Getting this wrong is the classic way a backtest of this pattern
    invents an edge that does not exist live.
    """
    highs, lows = [], []
    n = len(candles)
    for i in range(prd, n - prd):
        window = candles[i - prd:i + prd + 1]
        centre = candles[i]
        if all(centre.high >= c.high for c in window) and \
           any(centre.high > c.high for c in window if c is not centre):
            highs.append(Pivot(i, centre.high))
        if all(centre.low <= c.low for c in window) and \
           any(centre.low < c.low for c in window if c is not centre):
            lows.append(Pivot(i, centre.low))
    return highs, lows


def breakout_signals(candles: Sequence[Candle], cfg: Config = Config(), *,
                     prd: int = PRD, bo_len: int = BO_LEN, cwidthu: float = CWIDTHU,
                     mintest: int = MINTEST, allow_short: bool = True) -> List[Signal]:
    """The script's visible logic, as Signals the project's own simulator can run.

    Levels for the stop and targets come from `cfg` (ATR-based), because the indicator supplies none.
    """
    n = len(candles)
    atr_series = atr(candles, cfg.atr_len)
    highs, lows = detect_pivots(candles, prd)

    signals: List[Signal] = []
    for i in range(max(prd * 2, cfg.atr_len + 1), n):
        a = atr_series[i]
        if a is None or a <= 0:
            continue
        bar = candles[i]

        # The script's chwidth: a share of the recent range, so tolerance scales with volatility.
        start = max(0, i - RANGE_LOOKBACK + 1)
        window = candles[start:i + 1]
        chwidth = (max(c.high for c in window) - min(c.low for c in window)) * cwidthu
        if chwidth <= 0:
            continue

        # Only pivots CONFIRMED by now (index + prd <= i) and inside bo_len, newest first.
        ph = [p for p in highs if p.index + prd <= i and i - p.index <= bo_len][::-1]
        pl = [p for p in lows if p.index + prd <= i and i - p.index <= bo_len][::-1]

        # --- bullish: close above a cluster of pivot highs -----------------------------------------
        hgst = max(c.high for c in candles[max(0, i - prd):i]) if i > 0 else bar.high
        if len(ph) >= mintest and bar.close > bar.open and bar.close > hgst:
            bomax, xx = ph[0].value, -1
            for x, p in enumerate(ph):
                if p.value >= bar.close:
                    break
                xx = x
                bomax = max(bomax, p.value)
            if xx >= mintest and bar.open <= bomax:
                num = sum(1 for p in ph[:xx + 1] if bomax - chwidth <= p.value <= bomax)
                if num >= mintest and hgst < bomax:
                    stop = bar.close - cfg.sl_atr_mult * a
                    risk = bar.close - stop
                    signals.append(Signal(index=i, ts=bar.ts, direction="long", entry=bar.close,
                                          stop=stop, tp1=bar.close + cfg.tp1_r * risk,
                                          tp2=bar.close + cfg.tp2_r * risk, atr=a,
                                          reason=f"broke cluster of {num} pivot highs at {bomax:.2f}"))
                    continue

        # --- bearish: close below a cluster of pivot lows ------------------------------------------
        if not allow_short:
            continue
        lwst = min(c.low for c in candles[max(0, i - prd):i]) if i > 0 else bar.low
        if len(pl) >= mintest and bar.close < bar.open and bar.close < lwst:
            bomin, xx = pl[0].value, -1
            for x, p in enumerate(pl):
                if p.value <= bar.close:
                    break
                xx = x
                bomin = min(bomin, p.value)
            if xx >= mintest and bar.open >= bomin:
                num = sum(1 for p in pl[:xx + 1] if bomin <= p.value <= bomin + chwidth)
                if num >= mintest and lwst > bomin:
                    stop = bar.close + cfg.sl_atr_mult * a
                    risk = stop - bar.close
                    signals.append(Signal(index=i, ts=bar.ts, direction="short", entry=bar.close,
                                          stop=stop, tp1=bar.close - cfg.tp1_r * risk,
                                          tp2=bar.close - cfg.tp2_r * risk, atr=a,
                                          reason=f"broke cluster of {num} pivot lows at {bomin:.2f}"))
    return signals


def signal_mix(signals: Sequence[Signal]) -> dict:
    """How many each way. Recorded because long-only is the defect this candidate is meant to fix."""
    longs = sum(1 for s in signals if s.direction == "long")
    return {"signals": len(signals), "long": longs, "short": len(signals) - longs,
            "short_pct": round((len(signals) - longs) / len(signals) * 100, 1) if signals else 0.0}
