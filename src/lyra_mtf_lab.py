"""The Lyra expert's entry rule, ported into this system's engine. Research only; it never trades.

Written 21 September 2026. The owner corrected a verdict I had given from a single MT4 account
statement: I read the symbols where Lyra loses and called the whole expert a loss. Its own
per-symbol brain files tell a different story, and bitcoin is the part worth testing:

    BTCUSD   +105.82 over 12 trades (terminal 50CA3DFB) and +7.46 over 14 (terminal 1016BE39)
    XAUUSD   -168.65 over 128 trades
    US500Cash roughly break-even

There is **no bitcoin-specific rule** in the expert. ``LyraAI_v65_MT4.mq4`` runs one rule over
every symbol and varies only the lot cap (``MaxLot_Crypto = 0.05``). So the hypothesis under test
is narrow and worth stating plainly: *the same rule that loses on gold wins on bitcoin.* Twenty-six
trades cannot settle that, which is why this exists - to put a forward record on it.

THE RULE, transcribed from the v6.5 source (classic mode: ``SniperMode`` and ``PipMode`` both off,
which is how it was configured on both terminals).

``TFDir`` scores one timeframe from EMA 21 / 55 / 200 and RSI 14::

    bp += 2 if fast > slow > trend and price > trend      (else bp += 1 if fast > slow and price > trend)
    ep += 2 if fast < slow < trend and price < trend      (else ep += 1 if fast < slow and price < trend)
    bp += 1 on a fresh fast-over-slow cross, ep += 1 on the opposite
    bp += 1 if RSI > 55, ep += 1 if RSI < 45
    direction = +1 if bp >= 2 and bp > ep, -1 if ep >= 2 and ep > bp, else 0

It is evaluated on D1, H4, H1 and M15. The main direction is D1's, unless H4, H1 and M15 all agree
against it, which is allowed as a counter-trend entry with the score multiplied by ``CTR_ScoreMult``.
At least three of the four must agree. The score is 3.0 for D1, 2.0 for H4, 2.0 for H1, 1.5 for M15
and a further 2.0 when all four line up, plus 0.5 when the last closed M15 candle closes in the
trade's direction with a body over 60 % of its range, plus a candle pattern score of 2.0 to 3.5
taken from the last three closed H4 candles first and the H1 candles only if H4 shows nothing.
A pattern is required. ADX(H1, 14) must be at least 18, and the total must reach 6.0.

Entry is a market order. The stop is ``2 x ATR(H1, 14)``, widened to clear the 50-bar H4 swing by
half an ATR when that swing sits within three ATR. Targets are 1.5 R and 3.0 R with 60 % booked at
the first and break-even after it.

WHAT IS DELIBERATELY LEFT OUT, because it cannot be replayed honestly:

* **The adaptive brain.** Hour tiers, day-of-week tiers and the per-pattern block/boost weights are
  accumulated from the expert's own closed trades in ``MQL4\\Files\\LyraV65MT4_<SYMBOL>.csv``. They
  are state built by live trading, so replaying them over history would let the rule consult
  outcomes it had not yet had. This port tests the **base rule only**, which is the conservative
  choice: the adaptive layer is claimed to improve on it.
* **Spread/ATR and Friday-gap gates**, which need tick-level spread this system does not store.
  The cost model charges the round trip instead.

TWO FAITHFUL APPROXIMATIONS, both lookahead-free and both worth knowing when reading the numbers:

* MetaTrader's ``iMA(..., 0)`` reads the *forming* higher-timeframe bar. That is reproduced exactly
  - the EMA over closed higher-timeframe bars is advanced one step with the current base close -
  so the rule sees what it saw live without seeing anything it could not.
* ``iRSI(..., 0)`` is approximated by the RSI at the last *closed* higher-timeframe bar. Advancing
  Wilder's smoothing through a partial bar is not worth the complexity for a term that contributes
  one point out of the two ``TFDir`` needs.

    python -m src.lyra_mtf_lab run [--symbols BTCUSD XAUUSD] [--timeframes 15m 1h]
    python -m src.lyra_mtf_lab register     # add the surviving spec to the shadow book
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from itertools import product
from typing import Optional

import numpy as np
import pandas as pd

from . import strategy_lab as lab
from .candle_patterns import _geometry
from .mtf_data import resample_bars

# The expert's own defaults, unchanged from the copy running on both terminals.
EMA_FAST, EMA_SLOW, EMA_TREND = 21, 55, 200
RSI_PERIOD, ATR_PERIOD, ADX_PERIOD = 14, 14, 14
ADX_MIN = 18.0
MIN_MTF_SCORE = 6.0
ATR_SL_MULTI = 2.0
TP1_RR, TP2_RR = 1.5, 3.0
PARTIAL_PCT = 60.0
SR_BARS = 50
START_HOUR, END_HOUR = 7, 20
BLOCKED_HOURS = (16, 18)
CTR_SCORE_MULT = 0.75      # v6.5 counter-trend penalty
MIN_AGREE = 3

HTF_RULES = {"M15": 15, "H1": 60, "H4": 240, "D1": 1440}


# --------------------------------------------------------------------------- indicators


def _wilder(values: np.ndarray, length: int) -> np.ndarray:
    """Wilder's smoothing, the average MetaTrader's ADX and RSI are built on."""
    out = np.full(len(values), np.nan)
    if len(values) <= length:
        return out
    seed = np.nanmean(values[1:length + 1])
    out[length] = seed
    for i in range(length + 1, len(values)):
        prev = out[i - 1]
        v = values[i]
        out[i] = prev if not np.isfinite(v) else (prev * (length - 1) + v) / length
    return out


def adx(high: np.ndarray, low: np.ndarray, close: np.ndarray, length: int = ADX_PERIOD) -> np.ndarray:
    """Average Directional Index, MetaTrader's iADX(..., MODE_MAIN)."""
    n = len(close)
    if n < length * 2 + 2:
        return np.full(n, np.nan)
    up, down = np.diff(high, prepend=np.nan), -np.diff(low, prepend=np.nan)
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    prev_close = np.concatenate([[np.nan], close[:-1]])
    tr = np.nanmax(np.vstack([high - low, np.abs(high - prev_close), np.abs(low - prev_close)]), axis=0)
    atr_s = _wilder(tr, length)
    with np.errstate(invalid="ignore", divide="ignore"):
        plus_di = 100.0 * _wilder(plus_dm, length) / atr_s
        minus_di = 100.0 * _wilder(minus_dm, length) / atr_s
        dx = 100.0 * np.abs(plus_di - minus_di) / (plus_di + minus_di)
    return _wilder(np.where(np.isfinite(dx), dx, np.nan), length)


def _ema(values: np.ndarray, span: int) -> np.ndarray:
    return pd.Series(values, dtype=float).ewm(span=span, adjust=False).mean().to_numpy()


def _rsi(values: np.ndarray, length: int) -> np.ndarray:
    delta = np.diff(values, prepend=np.nan)
    gain = _wilder(np.where(delta > 0, delta, 0.0), length)
    loss = _wilder(np.where(delta < 0, -delta, 0.0), length)
    with np.errstate(invalid="ignore", divide="ignore"):
        return 100.0 - 100.0 / (1.0 + gain / loss)


# --------------------------------------------------------------------------- timeframe alignment


def _base_minutes(times: pd.Series) -> int:
    deltas = pd.Series(times).diff().dt.total_seconds().dropna()
    return max(1, int(round(float(deltas.mode().iloc[0]) / 60.0)))


def _align_closed(htf: pd.DataFrame, htf_minutes: int, base_times: pd.Series,
                  base_minutes: int, columns: dict) -> dict:
    """Map each closed higher-timeframe value onto the base bars that could legally see it.

    A higher-timeframe bar labelled ``T`` closes at ``T + htf_minutes``; a base bar labelled ``t``
    is decided at ``t + base_minutes``. So a base bar may use the newest higher-timeframe bar whose
    close time is at or before its own. Anything else would be reading the future.
    """
    closes = pd.to_datetime(htf["datetime"], utc=True) + pd.Timedelta(minutes=htf_minutes)
    decided = pd.to_datetime(base_times, utc=True) + pd.Timedelta(minutes=base_minutes)
    left = pd.DataFrame({"decided": decided.to_numpy()}).sort_values("decided")
    right = pd.DataFrame({"decided": closes.to_numpy(), **columns}).sort_values("decided")
    merged = pd.merge_asof(left, right, on="decided", direction="backward")
    return {name: merged[name].to_numpy() for name in columns}


def tf_direction(base_df: pd.DataFrame, htf_minutes: int, base_minutes: int) -> np.ndarray:
    """``TFDir`` for one timeframe, evaluated per base bar. +1 bullish, -1 bearish, 0 undecided."""
    price = base_df["close"].to_numpy(dtype=float)
    if htf_minutes <= base_minutes:
        htf = base_df
    else:
        htf = resample_bars(base_df, f"{htf_minutes}min")
    if htf.empty or len(htf) < EMA_TREND + 5:
        return np.zeros(len(price), dtype=int)

    hc = htf["close"].to_numpy(dtype=float)
    cols = {"fast": _ema(hc, EMA_FAST), "slow": _ema(hc, EMA_SLOW),
            "trend": _ema(hc, EMA_TREND), "rsi": _rsi(hc, RSI_PERIOD)}
    # shift-1 values, the previous closed bar, for the cross test
    cols["fast_prev"] = np.concatenate([[np.nan], cols["fast"][:-1]])
    cols["slow_prev"] = np.concatenate([[np.nan], cols["slow"][:-1]])
    a = _align_closed(htf, htf_minutes, base_df["datetime"], base_minutes, cols)

    # iMA(..., 0) reads the forming bar: advance the closed EMA one step with the current price.
    def forming(name: str, span: int) -> np.ndarray:
        k = 2.0 / (span + 1.0)
        return a[name] * (1.0 - k) + price * k

    f0, s0, t0 = forming("fast", EMA_FAST), forming("slow", EMA_SLOW), forming("trend", EMA_TREND)
    f1, s1, r0 = a["fast"], a["slow"], a["rsi"]

    bp = np.zeros(len(price), dtype=float)
    ep = np.zeros(len(price), dtype=float)
    with np.errstate(invalid="ignore"):
        strong_up = (f0 > s0) & (s0 > t0) & (price > t0)
        weak_up = (f0 > s0) & (price > t0) & ~strong_up
        strong_dn = (f0 < s0) & (s0 < t0) & (price < t0)
        weak_dn = (f0 < s0) & (price < t0) & ~strong_dn
        bp += np.where(strong_up, 2.0, np.where(weak_up, 1.0, 0.0))
        ep += np.where(strong_dn, 2.0, np.where(weak_dn, 1.0, 0.0))
        bp += np.where((f0 > s0) & (f1 <= s1), 1.0, 0.0)
        ep += np.where((f0 < s0) & (f1 >= s1), 1.0, 0.0)
        bp += np.where(r0 > 55.0, 1.0, 0.0)
        ep += np.where(r0 < 45.0, 1.0, 0.0)
        known = np.isfinite(f0) & np.isfinite(s0) & np.isfinite(t0) & np.isfinite(r0)
        direction = np.where((bp >= 2) & (bp > ep), 1, np.where((ep >= 2) & (ep > bp), -1, 0))
    return np.where(known, direction, 0).astype(int)


# --------------------------------------------------------------------------- candles


def _lyra_patterns(g: dict, bullish: bool) -> list[tuple[str, np.ndarray, float, float]]:
    """(name, mask on the LAST CLOSED bar, H4 score, H1 score) in the expert's own priority order."""
    def sh(a, n):
        return np.concatenate([np.full(n, np.nan if a.dtype.kind == "f" else False), a[:-n]]) if n else a

    # src.candle_patterns._geometry names these "range", "upper", "lower" and "up"; the expert's own
    # source calls them rng, upWick, dnWick and bull, and the transcriptions below follow the expert.
    body, rng, up, dn, bull = g["body"], g["range"], g["upper"], g["lower"], g["up"]
    ok = rng > 0
    o, c = g["o"], g["c"]
    b1, r1, bull1, o1, c1 = sh(body, 1), sh(rng, 1), sh(bull, 1), sh(o, 1), sh(c, 1)
    b2, r2, bull2, o2, c2 = sh(body, 2), sh(rng, 2), sh(bull, 2), sh(o, 2), sh(c, 2)
    h1s, l1s = sh(g["h"], 1), sh(g["l"], 1)
    with np.errstate(invalid="ignore"):
        if bullish:
            maru = ok & bull & (body / rng > 0.85) & (up < body * 0.08) & (dn < body * 0.08)
            eng = ok & bull & ~bull1 & (o < c1) & (c > o1) & (body > b1) & (body / rng > 0.5)
            star = ok & ~bull2 & (b2 > r2 * 0.4) & (b1 < r1 * 0.35) & bull & (body > rng * 0.4) & (c > (o2 + c2) / 2.0)
            pin = ok & (body > 0) & (dn >= body * 2.5) & (up <= body * 0.5) & (body / rng < 0.35)
            ham = ok & (body > 0) & (dn >= body * 2.0) & (up <= body * 0.5) & (body / rng < 0.4)
            three = ok & bull & bull1 & bull2 & (c > c1) & (c1 > c2) & (body > rng * 0.5) & (b1 > r1 * 0.5)
            inside = ok & (g["h"] <= h1s) & (g["l"] >= l1s) & bull & (body > rng * 0.4)
            return [("maru", maru, 3.5, 3.0), ("engulf", eng, 3.0, 2.5), ("star", star, 3.5, 3.0),
                    ("pin", pin, 2.5, 2.5), ("hammer", ham, 2.5, 2.0),
                    ("inside", inside, 0.0, 2.0), ("three", three, 3.0, 2.5)]
        maru = ok & ~bull & (body / rng > 0.85) & (up < body * 0.08) & (dn < body * 0.08)
        eng = ok & ~bull & bull1 & (o > c1) & (c < o1) & (body > b1) & (body / rng > 0.5)
        star = ok & bull2 & (b2 > r2 * 0.4) & (b1 < r1 * 0.35) & ~bull & (body > rng * 0.4) & (c < (o2 + c2) / 2.0)
        pin = ok & (body > 0) & (up >= body * 2.5) & (dn <= body * 0.5) & (body / rng < 0.35)
        shoot = ok & (body > 0) & (up >= body * 2.0) & (dn <= body * 0.5) & (body / rng < 0.4)
        three = ok & ~bull & ~bull1 & ~bull2 & (c < c1) & (c1 < c2) & (body > rng * 0.5) & (b1 > r1 * 0.5)
        inside = ok & (g["h"] <= h1s) & (g["l"] >= l1s) & ~bull & (body > rng * 0.4)
        return [("maru", maru, 3.5, 3.0), ("engulf", eng, 3.0, 2.5), ("star", star, 3.5, 3.0),
                ("pin", pin, 2.5, 2.5), ("shoot", shoot, 2.5, 2.0),
                ("inside", inside, 0.0, 2.0), ("three", three, 3.0, 2.5)]


def _pattern_score(base_df: pd.DataFrame, htf_minutes: int, base_minutes: int,
                   bullish: bool, column: int) -> np.ndarray:
    """Best pattern score from the last three CLOSED bars of one timeframe, per base bar."""
    htf = base_df if htf_minutes <= base_minutes else resample_bars(base_df, f"{htf_minutes}min")
    if htf.empty or len(htf) < 5:
        return np.zeros(len(base_df), dtype=float)
    g = _geometry(htf)
    best = np.zeros(len(htf), dtype=float)
    for _name, mask, h4_score, h1_score in _lyra_patterns(g, bullish):
        score = h4_score if column == 0 else h1_score
        if score <= 0:
            continue
        clean = np.where(np.isfinite(mask.astype(float)), mask, False).astype(bool)
        best = np.where(clean & (best == 0.0), score, best)
    aligned = _align_closed(htf, htf_minutes, base_df["datetime"], base_minutes, {"pat": best})
    return np.nan_to_num(aligned["pat"], nan=0.0)


# --------------------------------------------------------------------------- the builder


def lyra_orders(ind: lab.Indicators, spec: dict):
    """side / stop / target per bar for the Lyra v6.5 classic entry, base rule only."""
    p = spec.get("params") or {}
    df = ind.df
    n = len(ind.c)
    base_min = _base_minutes(ind.times)
    min_score = float(p.get("min_score", MIN_MTF_SCORE))
    min_agree = int(p.get("min_agree", MIN_AGREE))
    adx_min = float(p.get("adx_min", ADX_MIN))
    require_pattern = bool(p.get("require_pattern", True))
    allow_counter = bool(p.get("allow_counter_trend", True))

    dirs = {name: tf_direction(df, minutes, base_min) for name, minutes in HTF_RULES.items()}
    d_d1, d_h4, d_h1, d_m15 = dirs["D1"], dirs["H4"], dirs["H1"], dirs["M15"]

    # main direction: D1, unless H4+H1+M15 agree against it
    counter = (d_d1 != 0) & (d_h4 != 0) & (d_h1 != 0) & (d_h4 == d_h1) & (d_h1 == d_m15) & (d_h4 != d_d1)
    if not allow_counter:
        counter = np.zeros(n, dtype=bool)
    main = np.where(counter, d_h4, d_d1)

    ag_d1 = (d_d1 == main) & (main != 0)
    ag_h4 = (d_h4 == main) & (main != 0)
    ag_h1 = (d_h1 == main) & (main != 0)
    ag_m15 = (d_m15 == main) & (main != 0)
    agree = ag_d1.astype(int) + ag_h4.astype(int) + ag_h1.astype(int) + ag_m15.astype(int)

    score = (ag_d1 * 3.0 + ag_h4 * 2.0 + ag_h1 * 2.0 + ag_m15 * 1.5
             + (agree == 4) * 2.0)
    score = np.where(counter, score * CTR_SCORE_MULT, score)

    # last closed base-timeframe candle confirming the direction
    g = _geometry(df)
    prev_bull = np.concatenate([[False], g["up"][:-1]])
    prev_body = np.concatenate([[np.nan], g["body"][:-1]])
    prev_rng = np.concatenate([[np.nan], g["range"][:-1]])
    with np.errstate(invalid="ignore"):
        strong_prev = prev_body > prev_rng * 0.6
    score = score + np.where((main == 1) & prev_bull & strong_prev, 0.5, 0.0)
    score = score + np.where((main == -1) & ~prev_bull & strong_prev, 0.5, 0.0)

    pat_long = np.maximum(_pattern_score(df, 240, base_min, True, 0),
                          np.where(_pattern_score(df, 240, base_min, True, 0) > 0, 0.0,
                                   _pattern_score(df, 60, base_min, True, 1)))
    pat_short = np.maximum(_pattern_score(df, 240, base_min, False, 0),
                           np.where(_pattern_score(df, 240, base_min, False, 0) > 0, 0.0,
                                    _pattern_score(df, 60, base_min, False, 1)))
    pattern = np.where(main == 1, pat_long, np.where(main == -1, pat_short, 0.0))
    score = score + pattern

    # ADX on H1
    h1 = df if base_min >= 60 else resample_bars(df, "60min")
    adx_vals = adx(h1["high"].to_numpy(dtype=float), h1["low"].to_numpy(dtype=float),
                   h1["close"].to_numpy(dtype=float), ADX_PERIOD)
    adx_aligned = _align_closed(h1, 60, df["datetime"], base_min, {"adx": adx_vals})["adx"]

    hours = pd.to_datetime(ind.times, utc=True).dt.hour.to_numpy()
    session = (hours >= START_HOUR) & (hours < END_HOUR)
    for blocked in BLOCKED_HOURS:
        session &= hours != blocked

    with np.errstate(invalid="ignore"):
        ok = ((main != 0) & (agree >= min_agree) & (score >= min_score) & session
              & np.isfinite(adx_aligned) & (adx_aligned >= adx_min))
    if require_pattern:
        ok &= pattern > 0
    side = np.where(ok, main, 0).astype(int)

    # stop: 2 x ATR(H1), widened to clear the 50-bar H4 swing by half an ATR when it is within 3 ATR
    atr_h1 = _align_closed(h1, 60, df["datetime"], base_min,
                           {"atr": lab.compute_atr(h1.astype({"high": float, "low": float, "close": float}),
                                                   ATR_PERIOD).to_numpy()})["atr"]
    h4 = df if base_min >= 240 else resample_bars(df, "240min")
    sw_low = pd.Series(h4["low"].to_numpy(dtype=float)).rolling(SR_BARS).min().shift(1).to_numpy()
    sw_high = pd.Series(h4["high"].to_numpy(dtype=float)).rolling(SR_BARS).max().shift(1).to_numpy()
    swings = _align_closed(h4, 240, df["datetime"], base_min, {"lo": sw_low, "hi": sw_high})
    price = ind.c
    with np.errstate(invalid="ignore"):
        base_sl = atr_h1 * float(p.get("sl_atr", ATR_SL_MULTI))
        near_sup = np.isfinite(swings["lo"]) & (swings["lo"] > 0) & ((price - swings["lo"]) < atr_h1 * 3)
        near_res = np.isfinite(swings["hi"]) & (swings["hi"] > 0) & ((swings["hi"] - price) < atr_h1 * 3)
        long_d = np.where(near_sup, np.maximum(base_sl, price - swings["lo"] + atr_h1 * 0.5), base_sl)
        short_d = np.where(near_res, np.maximum(base_sl, swings["hi"] - price + atr_h1 * 0.5), base_sl)
        dist = np.where(side == 1, long_d, np.where(side == -1, short_d, np.nan))
        stop = np.where(side == 1, price - dist, np.where(side == -1, price + dist, np.nan))
        rr = float(p.get("rr", TP2_RR))
        target = np.where(side == 1, price + rr * dist, np.where(side == -1, price - rr * dist, np.nan))
        usable = np.isfinite(stop) & np.isfinite(dist) & (dist > 0) & np.isfinite(atr_h1) & (atr_h1 > 0)
    side = np.where(usable, side, 0).astype(int)
    return side, np.where(usable, stop, np.nan), np.where(usable, target, np.nan)


lab.ORDER_BUILDERS["lyra_mtf"] = lyra_orders


def spec(symbol: str, timeframe: str, **overrides) -> dict:
    """The expert's own settings, as a Strategy Lab spec."""
    params = {"symbol": symbol, "timeframe": timeframe, "min_score": MIN_MTF_SCORE,
              "min_agree": MIN_AGREE, "adx_min": ADX_MIN, "require_pattern": True,
              "allow_counter_trend": True, "sl_atr": ATR_SL_MULTI, "rr": TP2_RR}
    params.update(overrides)
    return {"family": "lyra_mtf", "params": params,
            "exits": {"stop": "builder", "sl_atr": ATR_SL_MULTI, "rr": TP2_RR,
                      "partial_at_r": TP1_RR, "partial_pct": PARTIAL_PCT,
                      "be_lock_r": TP1_RR, "max_bars": 0, "trail_atr": 0.0,
                      "swing_lookback": SR_BARS}}


def run(symbols=("BTCUSD", "XAUUSD"), timeframes=("15m", "1h")) -> dict:
    from .mtf_data import load_bars

    registry = lab.load_registry()
    report = {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
              "rule": "Lyra v6.5 classic entry, base rule only (no adaptive brain)",
              "places_orders": False, "markets": {}}
    for symbol, timeframe in product(symbols, timeframes):
        key = f"{symbol}:{timeframe}"
        bars = load_bars(symbol, timeframe, source="app")
        if bars is None or bars.empty:
            report["markets"][key] = {"error": "no broker bars"}
            continue
        market = lab.Market(symbol, timeframe, bars,
                            boundaries=(registry["markets"].get(key) or {}).get("boundaries"), swap=True)
        s = spec(symbol, timeframe)
        entry = {"symbol": symbol, "timeframe": timeframe, "bars": int(len(market.ind.c)),
                 "cost_round_trip_pct": market.cost_pct, "period": market.info["periods"], "splits": {}}
        side, _stop, _target = lyra_orders(market.ind, s)
        entry["signals"] = int(np.count_nonzero(side))
        for split in market.rows:
            trades = market.simulate(s, split)
            entry["splits"][split] = market.summary(split, trades) if trades else {"trades": 0}
        report["markets"][key] = entry
    lab.LAB_DIR.mkdir(parents=True, exist_ok=True)
    path = lab.LAB_DIR / f"lyra_mtf_{datetime.now(timezone.utc):%Y%m%d}.json"
    path.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    report["path"] = str(path)
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Backtest the Lyra v6.5 entry rule (research only)")
    parser.add_argument("command", choices=["run"])
    parser.add_argument("--symbols", nargs="+", default=["BTCUSD", "XAUUSD"])
    parser.add_argument("--timeframes", nargs="+", default=["15m", "1h"])
    args = parser.parse_args(argv)

    report = run(tuple(args.symbols), tuple(args.timeframes))
    print(f"{report['rule']}   places_orders={report['places_orders']}\n")
    for key, market in report["markets"].items():
        if market.get("error"):
            print(f"{key}: {market['error']}")
            continue
        print(f"=== {key}  {market['bars']} bars, {market['signals']} signals, "
              f"costs {market['cost_round_trip_pct'] * 100:.4f}% round trip")
        for split, summary in market["splits"].items():
            if not summary.get("trades"):
                print(f"   {split:11s} no trades")
                continue
            print(f"   {split:11s} n {summary['trades']:4d}  PF {summary.get('profit_factor') or 0:5.3f}  "
                  f"net {summary.get('total_return_pct'):+9.2f}%  win {summary.get('win_rate_pct') or 0:5.1f}%  "
                  f"maxDD {summary.get('max_drawdown_pct'):6.2f}%")
        print()
    print(f"saved {report['path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
