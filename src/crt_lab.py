"""CRT_Dashboard_EA (MT4) rebuilt for the Strategy Lab: research only, never trades and never touches the expert.

The entry follows CRT_Dashboard_EA.mq4 (IC Markets MT4 data folder 50CA3DFB...): on each closed M15 bar, an anchor
candle 1..N bars back with a large body, middle candles that stay inside it (bodies smaller than the anchor's, closes
inside its range, no extreme spikes), a liquidity sweep beyond the anchor, and a breakout close beyond the other side
of the anchor. The setup score, near-threshold pass, 07-20 server-hour session, H4 EMA50 bias with a 0.12 ATR soft
buffer and the ADX/ATR regime gate are reproduced. Stop = anchor extreme -/+ max(fixed buffer, 0.8 ATR14), target =
risk x preset R. Distances in the expert are points x Point; both symbols quote in 0.01.

Not reproduced (stateful or broker-side): the adaptive score shift and session-hour memory built from the expert's
own trade history, the 3-loss streak pause, the daily loss and account equity guards, live spread (a typical spread
is assumed for the score adjustments) and the per-tick stop handling (the simulator moves stops from the next bar).

    python -m src.crt_lab run [--symbols XAUUSD BTCUSD]
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from . import strategy_lab as lab
from .features import compute_atr, compute_ema

POINT = 0.01
OUTPUT_DIR = Path("data") / "strategy_lab"

# The expert's inputs and its symbol presets (ApplySymbolPreset); spread_points is the typical spread assumed for the
# spread-dependent score adjustments.
INPUTS = {"min_setup_score": 62.0, "near_threshold_window": 7.0, "strong_entry_min_score": 54.0, "bypass_body_points": 90,
          "atr_period": 14, "atr_multiplier": 0.80, "session_start": 7, "session_end": 20, "mtf_ma_period": 50,
          "mtf_soft_buffer_atr": 0.12, "adx_period": 14, "adx_trend_min": 23.0, "regime_atr_lookback": 60,
          "vol_high_factor": 1.35, "vol_low_factor": 0.85}
PRESETS = {
    "XAUUSD": {"min_mid": 1, "max_mid": 5, "min_body": 180, "mid_factor": 0.90, "sweep": 35, "manip": 300,
               "buffer": 120, "rr": 2.0, "max_spread": 70, "spread_points": 15,
               "be_lock": 15, "trail_start": 300, "trail_dist": 180},
    "BTCUSD": {"min_mid": 1, "max_mid": 6, "min_body": 2200, "mid_factor": 0.92, "sweep": 700, "manip": 8000,
               "buffer": 1500, "rr": 2.2, "max_spread": 3000, "spread_points": 1500,
               "be_lock": 190, "trail_start": 3750, "trail_dist": 2250},
}


def server_hours(times: pd.Series) -> np.ndarray:
    """IC Markets server clock = New York time + 7 h (UTC+3 in summer, UTC+2 in winter)."""
    stamps = pd.to_datetime(times, utc=True).dt.tz_convert("America/New_York").dt.tz_localize(None) + pd.Timedelta(hours=7)
    return stamps.dt.hour.to_numpy()


def wilder_adx(h: np.ndarray, l: np.ndarray, c: np.ndarray, period: int) -> np.ndarray:
    up, down = np.diff(h, prepend=np.nan), -np.diff(l, prepend=np.nan)
    plus = np.where((up > down) & (up > 0), up, 0.0)
    minus = np.where((down > up) & (down > 0), down, 0.0)
    prev = np.concatenate([[np.nan], c[:-1]])
    tr = np.nanmax(np.vstack([h - l, np.abs(h - prev), np.abs(l - prev)]), axis=0)

    def smooth(x):
        return pd.Series(x).ewm(alpha=1.0 / period, adjust=False).mean().to_numpy()

    atr = smooth(tr)
    with np.errstate(invalid="ignore", divide="ignore"):
        pdi, mdi = 100 * smooth(plus) / atr, 100 * smooth(minus) / atr
        dx = 100 * np.abs(pdi - mdi) / (pdi + mdi)
    adx = smooth(np.nan_to_num(dx))
    adx[: 2 * period] = np.nan
    return adx


def htf_bias(times15: pd.Series, bars4h: pd.DataFrame, ma_period: int, atr_period: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """H4 close, EMA and ATR of the last H4 bar that had CLOSED when each M15 bar closed."""
    frame = bars4h.copy()
    frame["datetime"] = pd.to_datetime(frame["datetime"], utc=True)
    frame = frame.sort_values("datetime").reset_index(drop=True)
    frame["ema"] = compute_ema(frame["close"].astype(float), ma_period).to_numpy()
    frame["atr"] = compute_atr(frame.astype({"high": float, "low": float, "close": float}), atr_period).to_numpy()
    frame["closed_at"] = frame["datetime"] + pd.Timedelta(hours=4)
    left = pd.DataFrame({"closed_at": pd.to_datetime(times15, utc=True) + pd.Timedelta(minutes=15)})
    left["order"] = np.arange(len(left))
    merged = pd.merge_asof(left.sort_values("closed_at"), frame[["closed_at", "close", "ema", "atr"]].sort_values("closed_at"),
                           on="closed_at", direction="backward").sort_values("order")
    return merged["close"].to_numpy(float), merged["ema"].to_numpy(float), merged["atr"].to_numpy(float)


def crt_signals(ind: lab.Indicators, symbol: str) -> dict:
    """Per closed M15 bar: direction, score, stop reference and the gate results (cached per Indicators)."""
    def build():
        p, cfg = PRESETS[symbol], INPUTS
        o, h, l, c = ind.o, ind.h, ind.l, ind.c
        n = len(c)
        atr = ind.atr(cfg["atr_period"])
        adx = wilder_adx(h, l, c, cfg["adx_period"])
        hours = server_hours(ind.times + pd.Timedelta(minutes=15))    # server clock when the breakout bar closes
        bars4h = getattr(ind, "htf_bars", None)
        if bars4h is None:
            raise ValueError("crt_signals needs ind.htf_bars (the H4 frame)")
        h4_close, h4_ema, h4_atr = htf_bias(ind.times, bars4h, cfg["mtf_ma_period"], cfg["atr_period"])
        atr_avg = pd.Series(atr).shift(1).rolling(cfg["regime_atr_lookback"]).mean().to_numpy()

        body_min, sweep, manip = p["min_body"] * POINT, p["sweep"] * POINT, p["manip"] * POINT
        spread_ratio = p["spread_points"] / p["max_spread"]
        direction = np.zeros(n, dtype=int)
        score = np.full(n, np.nan)
        ref_low, ref_high = np.full(n, np.nan), np.full(n, np.nan)
        accepted = np.zeros(n, dtype=bool)
        mid_bars, regime = np.zeros(n, dtype=int), np.zeros(n, dtype=int)
        for i in range(p["max_mid"] + 2, n):
            if not (cfg["session_start"] <= hours[i] < cfg["session_end"]):
                continue
            best = -1.0
            for a_off in range(p["min_mid"] + 1, p["max_mid"] + 2):
                a = i - a_off
                body = abs(c[a] - o[a])
                if body < body_min:
                    continue
                ah, al = h[a], l[a]
                for sign in (1, -1):
                    if sign == 1 and not (c[a] < o[a] and c[i] > o[i] and c[i] > ah):
                        continue
                    if sign == -1 and not (c[a] > o[a] and c[i] < o[i] and c[i] < al):
                        continue
                    ok, swept = True, False
                    for k in range(a + 1, i):
                        if abs(o[k] - c[k]) > body * p["mid_factor"] or h[k] > ah + manip or l[k] < al - manip \
                                or c[k] > ah or c[k] < al:
                            ok = False
                            break
                        if (sign == 1 and l[k] < al - sweep) or (sign == -1 and h[k] > ah + sweep):
                            swept = True
                    if not ok:
                        continue
                    if not (swept or (sign == 1 and l[i] < al - sweep) or (sign == -1 and h[i] > ah + sweep)):
                        continue
                    breakout = (c[i] - ah) if sign == 1 else (al - c[i])
                    s = 45.0 + min(18.0, max(0.0, breakout / body) * 18.0) + min(12.0, body / body_min * 12.0)
                    s += 10.0 if swept else 0.0
                    s += 8.0 if 1 <= a_off - 1 <= 4 else 4.0
                    s = min(100.0, s)
                    if s > best:
                        best = s
                        direction[i], score[i], mid_bars[i] = sign, s, a_off - 1
                        ref_low[i] = min(al, l[i]) if sign == 1 else al
                        ref_high[i] = ah if sign == 1 else max(ah, h[i])
            if best < 0:
                continue

            # Regime (DetectMarketRegime): 3 = volatile, 1 = trend, 2 = range.
            atr_now, avg, adx_now = atr[i], atr_avg[i], adx[i]
            if not (np.isfinite(atr_now) and np.isfinite(avg) and np.isfinite(adx_now)):
                continue
            if avg > 0 and atr_now >= avg * cfg["vol_high_factor"]:
                mode = 3
            elif adx_now >= cfg["adx_trend_min"]:
                mode = 1
            elif avg > 0 and atr_now <= avg * cfg["vol_low_factor"] and adx_now < cfg["adx_trend_min"] * 0.80:
                mode = 2
            else:
                mode = 1 if adx_now >= cfg["adx_trend_min"] * 0.90 else 2
            regime[i] = mode

            # EffectiveMinSetupScore with the dynamic threshold (trade-memory parts omitted).
            dyn = {2: 1.2, 3: 2.0}.get(mode, 0.0)
            dyn += 1.5 if spread_ratio >= 0.85 else 0.8 if spread_ratio >= 0.70 else -0.8 if spread_ratio <= 0.45 else 0.0
            dyn = max(-5.0, min(5.0, dyn))
            eff = cfg["min_setup_score"] + dyn
            eff -= 3.0 if spread_ratio <= 0.45 else 2.0 if spread_ratio <= 0.65 else 1.0 if spread_ratio <= 0.85 else 0.0
            eff = max(45.0, eff - 1.0)                                   # liquidity sweep required
            s = score[i]
            passed = s >= eff or (s >= max(46.0, eff - cfg["near_threshold_window"]) and s >= cfg["strong_entry_min_score"]
                                  and abs(c[i] - o[i]) >= cfg["bypass_body_points"] * POINT and spread_ratio <= 0.80)
            if not passed:
                continue
            sign = direction[i]
            if not (np.isfinite(h4_close[i]) and np.isfinite(h4_ema[i]) and h4_close[i] > 0 and h4_ema[i] > 0):
                continue
            soft = cfg["mtf_soft_buffer_atr"] * (h4_atr[i] if np.isfinite(h4_atr[i]) else 0.0)
            mtf_ok = (h4_close[i] > h4_ema[i] or (soft > 0 and h4_ema[i] - h4_close[i] <= soft)) if sign == 1 else \
                     (h4_close[i] < h4_ema[i] or (soft > 0 and h4_close[i] - h4_ema[i] <= soft))
            if not mtf_ok:
                continue
            if mode == 2 and (s < cfg["min_setup_score"] + 2.0 or spread_ratio > 0.90):
                continue
            if mode == 3 and (s < cfg["strong_entry_min_score"] or spread_ratio > 0.75):
                continue
            accepted[i] = True
        return {"direction": np.where(accepted, direction, 0), "score": score, "ref_low": ref_low, "ref_high": ref_high,
                "atr": atr, "mid": mid_bars, "mode": regime, "hours": hours, "h4_close": h4_close, "h4_ema": h4_ema,
                "h4_ema200": htf_bias(ind.times, bars4h, 200, cfg["atr_period"])[1], "ema50": ind.ema(50)}

    return ind._cached(("crt_signals", symbol), build)


def filter_mask(ind: lab.Indicators, sig: dict, side: np.ndarray, filters: dict) -> np.ndarray:
    """Extra entry filters on top of the expert's own gates (round 2 research); an empty dict keeps every signal."""
    close = ind.c
    long_, short_ = side == 1, side == -1
    mask = side != 0
    with np.errstate(invalid="ignore"):
        if "session" in filters:
            start, end = filters["session"]
            mask &= (sig["hours"] >= start) & (sig["hours"] < end)
        if filters.get("m15_trend"):
            mask &= (long_ & (close > sig["ema50"])) | (short_ & (close < sig["ema50"]))
        if filters.get("h4_strict"):
            mask &= (long_ & (sig["h4_close"] > sig["h4_ema"])) | (short_ & (sig["h4_close"] < sig["h4_ema"]))
        if filters.get("h4_ema200"):
            mask &= (long_ & (sig["h4_close"] > sig["h4_ema200"])) | (short_ & (sig["h4_close"] < sig["h4_ema200"]))
        if filters.get("min_score"):
            mask &= sig["score"] >= filters["min_score"]
        if filters.get("max_mid"):
            mask &= sig["mid"] <= filters["max_mid"]
        if filters.get("body_atr"):
            mask &= np.abs(close - ind.o) >= filters["body_atr"] * sig["atr"]
        if filters.get("trend_regime"):
            mask &= sig["mode"] == 1
    return mask


# Round 2 (pre-stated 15 Sep 2026 before any round 2 result): one filter at a time on the expert's entries.
ROUND2_FILTERS = [
    ("none", {}),
    ("london_ny_hours", {"session": (10, 19)}),          # server 10-19 = 07-16 UTC in summer
    ("m15_ema50_agrees", {"m15_trend": True}),
    ("h4_close_beyond_ema50", {"h4_strict": True}),
    ("h4_close_beyond_ema200", {"h4_ema200": True}),
    ("score_70_plus", {"min_score": 70.0}),
    ("max_3_middle_bars", {"max_mid": 3}),
    ("breakout_body_half_atr", {"body_atr": 0.5}),
]
ROUND1_TRIALS = 8


def crt_orders(ind: lab.Indicators, spec: dict):
    symbol = spec["params"]["symbol"]
    sig = crt_signals(ind, symbol)
    preset = PRESETS[symbol]
    rr = spec["exits"].get("rr")
    side = sig["direction"].copy()
    side = np.where(filter_mask(ind, sig, side, spec["params"].get("filters") or {}), side, 0)
    buffer = np.maximum(preset["buffer"] * POINT, np.round(sig["atr"] * INPUTS["atr_multiplier"] / POINT) * POINT)
    close = ind.c
    with np.errstate(invalid="ignore"):
        stop = np.where(side == 1, sig["ref_low"] - buffer, np.where(side == -1, sig["ref_high"] + buffer, np.nan))
        risk = np.where(side == 1, close - stop, np.where(side == -1, stop - close, np.nan))
        valid = (side != 0) & np.isfinite(stop) & (risk > 0)
    side = np.where(valid, side, 0)
    stop = np.where(valid, stop, np.nan)
    target = np.full(len(close), np.nan)
    if rr:
        target = np.where(side == 1, close + rr * risk, np.where(side == -1, close - rr * risk, np.nan))
    return side.astype(int), stop, target


lab.ORDER_BUILDERS["crt"] = crt_orders


def exit_variants(symbol: str) -> list[tuple[str, dict]]:
    """Pre-stated before any result: A = the expert's live exits, the rest fix the exit only (same entries)."""
    p = PRESETS[symbol]
    base = {"stop": "crt", "sl_atr": 0.0, "rr": p["rr"], "trail_atr": 0.0, "max_bars": 0, "swing_lookback": 0}
    return [
        ("A_live_exits", dict(base, be_trigger_r=1.0, be_lock_price=p["be_lock"] * POINT,
                              trail_start_price=p["trail_start"] * POINT, trail_dist_price=p["trail_dist"] * POINT)),
        ("B_fixed_target", dict(base)),
        ("C_breakeven_at_1R", dict(base, be_trigger_r=1.0, be_lock_price=p["be_lock"] * POINT)),
        ("D_trail_from_1.5R_1R_behind", dict(base, trail_start_r=1.5, trail_dist_r=1.0)),
        ("E_no_target_trail_from_1R_1.5R_behind_1day", dict(base, rr=0.0, trail_start_r=1.0, trail_dist_r=1.5, max_bars=96)),
        ("F_target_1.5R", dict(base, rr=1.5)),
        ("G_target_3R", dict(base, rr=3.0)),
        ("H_fixed_target_8h_time_stop", dict(base, max_bars=32)),
    ]


def r_stats(trades: list[dict], stop: np.ndarray) -> dict:
    """Gross and after-cost R per split; ``stop`` is the per-signal-bar stop array the trades were simulated with."""
    if not trades:
        return {"trades": 0, "gross_r": 0.0, "net_r": 0.0}
    gross_r, net_r, outcomes = [], [], {}
    for t in trades:
        risk = abs(t["entry_price"] - stop[t["entry_idx"]])
        gross_r.append(t["r_multiple"])
        net_r.append(t["net_pct"] / 100.0 * t["entry_price"] / risk if risk > 0 else 0.0)
        outcomes[t["outcome"]] = outcomes.get(t["outcome"], 0) + 1
    return {"trades": len(trades), "gross_r": round(float(np.sum(gross_r)), 2), "net_r": round(float(np.sum(net_r)), 2),
            "avg_net_r": round(float(np.mean(net_r)), 3), "outcomes": outcomes}


# Round 3, pre-stated 20 Sep 2026 BEFORE any round 3 result: the exits shipped in
# strategies/mt4/CRT_Dashboard_EA_v2.mq4, against v1's own exits as the reference. The entries are
# identical throughout - only the exit changes - because the measured defect is in the exit: v1's trail
# starts at 0.15 R and follows 0.09 R behind, capping winners at +0.158 R while losers pay -1.019 R.
# Deliberately NO sweep over TrailStartR / TrailDistanceR: the gold M15 holdout is already spent, so a
# parameter search here would be fitting, not measuring. Five variants, every one counted.
ROUND3_TRIALS = 5


def round3_variants(symbol: str) -> list[tuple[str, dict, dict]]:
    p = PRESETS[symbol]
    base = {"stop": "crt", "sl_atr": 0.0, "rr": p["rr"], "trail_atr": 0.0, "max_bars": 0, "swing_lookback": 0}
    v1_ref = dict(exit_variants(symbol))["A_live_exits"]
    return [
        # the reference, unchanged, so the table is self-contained
        ("V1_live_exits_reference", {}, v1_ref),
        # v2 as shipped: break-even at 1R locking 0.10 R, then trail 1.0 R behind from 1.5 R, 2 R target
        ("V2_default", {}, dict(base, be_trigger_r=1.0, be_lock_r=0.10, trail_start_r=1.5, trail_dist_r=1.0)),
        # the two halves of that change, separated, so credit lands on the right one
        ("V2_break_even_only", {}, dict(base, be_trigger_r=1.0, be_lock_r=0.10)),
        ("V2_r_trail_only", {}, dict(base, trail_start_r=1.5, trail_dist_r=1.0)),
        # v2's management with the wider target that scored best in round 1
        ("V2_default_3R", {}, dict(base, rr=3.0, be_trigger_r=1.0, be_lock_r=0.10,
                                   trail_start_r=1.5, trail_dist_r=1.0)),
    ]


def round2_variants(symbol: str) -> list[tuple[str, dict, dict]]:
    exits = dict(exit_variants(symbol))
    return [(f"{fname}+{ename}", filters, exits[ename]) for fname, filters in ROUND2_FILTERS
            for ename in ("B_fixed_target", "G_target_3R")]


UNSEEN_CACHE = "data/research/cache/xauusd_{tf}_mt5hist_2018_2024.csv"


def run_unseen() -> dict:
    """Round 3's exits on gold M15 bars no CRT selection has ever chosen with.

    The window ends 2024-08-01, six days BEFORE the app's history begins, so nothing here informed
    any CRT parameter, filter or exit. Nothing is selected or tuned: the five declared exits run over
    the whole window at once, to answer one pre-stated question. The trail defect is mechanical
    arithmetic - v1's trail starts at 0.15 R and follows 0.09 R behind - so removing it should help
    here too. If it did not, round 3 would have been a fact about 2026 rather than about the expert.
    """
    from .walkforward_backtest import summarize_trades

    frames = {}
    for tf in ("15m", "4h"):
        path = Path(UNSEEN_CACHE.format(tf=tf))
        if not path.exists():
            return {"error": f"missing {path}; this window exists only as a cached export"}
        frame = pd.read_csv(path)
        frame["datetime"] = pd.to_datetime(frame["datetime"], utc=True)
        frames[tf] = frame.sort_values("datetime").reset_index(drop=True)

    bars = frames["15m"]
    market = lab.Market("XAUUSD", "15m", bars, swap=True)
    market.ind.htf_bars = frames["4h"]
    rows = np.arange(lab.WARMUP_BARS, len(market.ind.c) - 1)
    report = {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
              "window": f"{lab._iso(bars['datetime'].iloc[0])} to {lab._iso(bars['datetime'].iloc[-1])}",
              "bars": int(len(bars)), "cost_model": market.info["cost_model"],
              "note": "no selection and no tuning; the five round-3 exits over the whole window",
              "variants": []}
    for name, filters, exits in round3_variants("XAUUSD"):
        spec = {"family": "crt", "params": {"symbol": "XAUUSD", "filters": filters}, "exits": exits,
                "description": f"CRT replica, {name}, unseen 2022-2024"}
        orders = lab.strategy_orders(market.ind, spec)
        side, stop, target = orders[:3]
        entry_prices = orders[3] if len(orders) > 3 else None
        atr = market.ind.atr(int(spec["params"].get("atr_len", 14)))
        trades = lab.simulate_orders(market.ind.o, market.ind.h, market.ind.l, market.ind.c, atr,
                                     market.ind.times, side, stop, target, rows, exits,
                                     market.cost_pct, market.holding, entry_prices)
        if not trades:
            report["variants"].append({"variant": name, "trades": 0})
            continue
        summary = summarize_trades(trades, test_start=market.ind.times.iloc[int(rows[0])],
                                   test_end=market.ind.times.iloc[int(rows[-1])], test_bars=len(rows),
                                   bars_in_market=sum(t["bars_held"] for t in trades))
        outcomes = {}
        for t in trades:
            outcomes[t["outcome"]] = outcomes.get(t["outcome"], 0) + 1
        report["variants"].append({"variant": name, **{k: summary.get(k) for k in
                                   ("trades", "profit_factor", "total_return_pct", "expectancy_r",
                                    "max_drawdown_pct", "win_rate_pct", "ambiguous_exits")},
                                   "net_r": round(sum(t["net_r"] for t in trades), 2),
                                   "outcomes": outcomes})
    lab.LAB_DIR.mkdir(parents=True, exist_ok=True)
    path = lab.LAB_DIR / f"crt_unseen_2022_2024_{datetime.now(timezone.utc):%Y%m%d}.json"
    path.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    report["path"] = str(path)
    return report


def run(symbols=("XAUUSD", "BTCUSD"), round_no: int = 1) -> dict:
    from .mtf_data import load_bars

    registry = lab.load_registry()
    report = {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "round": round_no, "markets": {}}
    for symbol in symbols:
        key = f"{symbol}:15m"
        bars, bars4h = load_bars(symbol, "15m", source="app"), load_bars(symbol, "4h", source="app")
        boundaries = (registry["markets"].get(key) or {}).get("boundaries")
        market = lab.Market(symbol, "15m", bars, boundaries=boundaries, swap=True)
        market.ind.htf_bars = bars4h
        if round_no == 3:
            variants = round3_variants(symbol)
            # every CRT trial ever charged against this holdout, not just this round's
            n_trials = ROUND1_TRIALS + len(ROUND2_FILTERS) * 2 + len(variants)
        elif round_no == 2:
            variants = round2_variants(symbol)
            n_trials = ROUND1_TRIALS + len(variants)
        else:
            variants = [(name, {}, exits) for name, exits in exit_variants(symbol)]
            n_trials = len(variants)
        records = []
        for name, filters, exits in variants:
            spec = {"family": "crt", "params": {"symbol": symbol, "filters": filters}, "exits": exits,
                    "description": f"CRT_Dashboard_EA replica, {name}"}
            record = lab.evaluate_candidate(market, spec, with_holdout=True)
            for split in ("search", "validation", "holdout"):
                record[f"{split}_r"] = r_stats(market.simulate(spec, split), lab.strategy_orders(market.ind, spec)[1])
            record["variant"] = name
            records.append(record)
        # Selection uses search + validation only; the holdout is read afterwards.
        sr_variance = lab._variance([[r["validation_sr"], r["validation"]["trades"]] for r in records if r["validation_sr"] is not None])
        for record in records:
            record["holdout_verdict"] = lab.holdout_verdict(record, n_trials, sr_variance)
        validated = [r for r in records if r["validated"]]
        chosen = max(validated, key=lambda r: r["score"]) if validated else None
        live_exits = exit_variants(symbol)[0][1]
        live_trades = [t for t in market.simulate({"family": "crt", "params": {"symbol": symbol}, "exits": live_exits}, "holdout")
                       if t["entry_time"] >= "2026-09-08"]
        report["markets"][key] = {"info": market.info, "variants": records,
                                  "chosen_on_search_validation": chosen["variant"] if chosen else None,
                                  "replica_trades_since_2026_09_08_live_exits": live_trades}
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    suffix = "" if round_no == 1 else f"_round{round_no}"
    path = OUTPUT_DIR / f"crt_backtest{suffix}_{datetime.now(timezone.utc).strftime('%Y%m%d')}.json"
    path.write_text(json.dumps(report, indent=1, default=lambda v: v.item() if hasattr(v, "item") else str(v)), encoding="utf-8")
    report["path"] = str(path)
    return report


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Backtest the MT4 CRT_Dashboard_EA rules (research only).")
    sub = parser.add_subparsers(dest="command", required=True)
    go = sub.add_parser("run")
    go.add_argument("--symbols", nargs="+", default=["XAUUSD", "BTCUSD"])
    go.add_argument("--round", type=int, choices=(1, 2, 3), default=1,
                    help="1 = exit variants, 2 = entry filters x 2 exits, 3 = the v2 expert's exits")
    sub.add_parser("unseen", help="round 3's exits on gold M15 bars no CRT selection ever used (2022-06 to 2024-08)")
    args = parser.parse_args(argv)

    if args.command == "unseen":
        report = run_unseen()
        if report.get("error"):
            print(report["error"])
            return
        print(f"=== unseen gold M15: {report['bars']} bars, {report['window']}")
        print(f"    costs {report['cost_model']}; {report['note']}\n")
        for r in report["variants"]:
            if not r.get("trades"):
                print(f"{r['variant']:26s} no trades")
                continue
            print(f"{r['variant']:26s} n {r['trades']:4d} PF {r['profit_factor'] or 0:5.3f} "
                  f"net {r['total_return_pct']:+7.2f}% netR {r['net_r']:+7.2f} "
                  f"expR {r['expectancy_r']:+.4f} DD {r['max_drawdown_pct']:5.2f}% win {r['win_rate_pct']:4.1f}% "
                  f"{r['outcomes']}")
        print("\nsaved:", report["path"])
        return

    report = run(args.symbols, args.round)
    for key, market in report["markets"].items():
        print(f"\n=== {key} {market['info']['data_start']} to {market['info']['data_end']} | {market['info']['periods']}")
        for r in market["variants"]:
            v = r.get("holdout_verdict") or {}
            print(f"{r['variant']:52s} search {r['search']['trades']:>4} PF {r['search']['profit_factor']} {r['search']['total_return_pct']}% "
                  f"netR {r['search_r'].get('net_r')} | val {r['validation']['trades']:>3} PF {r['validation']['profit_factor']} "
                  f"netR {r['validation_r'].get('net_r')} | gates {'pass' if r['validated'] else 'fail'} score {r['score']} || "
                  f"holdout {r['holdout']['trades']} PF {r['holdout']['profit_factor']} {r['holdout']['total_return_pct']}% "
                  f"grossR {r['holdout_r'].get('gross_r')} netR {r['holdout_r'].get('net_r')} DD {r['holdout']['max_drawdown_pct']} "
                  f"DSR {v.get('deflated_sharpe')} pass {v.get('passed')} outcomes {r['holdout_r'].get('outcomes')}")
        print("chosen on search+validation:", market["chosen_on_search_validation"])
        print("replica trades since 2026-09-08 (live exits):",
              [(t["entry_time"], t["side"], t["outcome"], t["r_multiple"]) for t in market["replica_trades_since_2026_09_08_live_exits"]])
    print("\nsaved:", report["path"])


if __name__ == "__main__":
    main()
