"""CRT sweep -> market structure shift -> FVG 50 % entry: the model in the owner's pictures. Research only, never trades.

Rules (fixed before any result): strategies/crt_mss_fvg.md. Ranges come from the previous server-time H4 candle, the
previous server day or the Asian session; the sweep, MSS and FVG are read on 5m or 15m broker bars from MT5 and the
entry is a resting limit at the FVG's 50 % level, simulated by strategy_lab.simulate_orders with spread and swap.

    python -m src.crt_mss_lab run [--markets XAUUSD:15m ...]
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from itertools import product

import numpy as np
import pandas as pd

from . import crt_fvg_lab
from . import crt_lab
from . import strategy_lab as lab

MODELS = ("h4", "h4_asia", "d1", "asia")
TARGETS = ("opposite", "rr2")
MARKETS = ("XAUUSD:15m", "XAUUSD:5m", "BTCUSD:15m", "BTCUSD:5m")
SWING_LOOKBACK, STOP_BUFFER_ATR, MIN_RISK_ATR = 10, 0.1, 0.5
HOLD_HOURS = {"h4": 8, "h4_asia": 8, "asia": 8, "d1": 24}


def bar_minutes(times) -> int:
    diffs = pd.to_datetime(pd.Series(times), utc=True).diff().dt.total_seconds().dropna()
    return int(round(diffs.median() / 60)) if len(diffs) else 15


def asian_levels(times, h, l, minutes: int) -> tuple[np.ndarray, np.ndarray]:
    """Per bar: today's 00-06 UTC high and low, available only from 06:00 UTC (NaN before or when incomplete)."""
    stamps = pd.to_datetime(pd.Series(times), utc=True)
    frame = pd.DataFrame({"day": stamps.dt.floor("D"), "hour": stamps.dt.hour, "h": h, "l": l})
    asia = frame[frame["hour"] < 6].groupby("day").agg(hi=("h", "max"), lo=("l", "min"), n=("h", "size"))
    asia = asia[asia["n"] >= int(6 * 60 / minutes * 0.75)]
    hi = frame["day"].map(asia["hi"]).to_numpy(float)
    lo = frame["day"].map(asia["lo"]).to_numpy(float)
    early = frame["hour"].to_numpy() < 6
    hi[early], lo[early] = np.nan, np.nan
    return hi, lo


def sweep_windows(ind: lab.Indicators, model: str, minutes: int) -> list[dict]:
    """Range (H, L) and the bar indices in which the sweep and MSS must happen, plus the last bar a limit may fill."""
    o, h, l, c, times = ind.o, ind.h, ind.l, ind.c, ind.times
    windows = []
    if model in ("h4", "h4_asia", "d1"):
        hours = 24 if model == "d1" else 4
        candles = crt_fvg_lab.htf_candles(times, o, h, l, c, hours, minutes)
        rows = list(candles.itertuples(index=False))
        for a, b in zip(rows[:-1], rows[1:]):
            if pd.Timestamp(b.bucket) - pd.Timestamp(a.bucket) > pd.Timedelta(days=4):
                continue
            windows.append({"H": float(a.h), "L": float(a.l), "start": int(b.start), "end": int(b.end), "fill_end": int(b.end)})
        return windows
    stamps = pd.to_datetime(pd.Series(times), utc=True)
    hour, day = stamps.dt.hour.to_numpy(), stamps.dt.floor("D").to_numpy()
    _, starts = np.unique(day, return_index=True)
    bounds = list(starts) + [len(o)]
    for s, e in zip(bounds[:-1], bounds[1:]):
        idx = np.arange(s, e)
        asia = idx[hour[idx] < 6]
        window = idx[(hour[idx] >= 6) & (hour[idx] < 13)]
        fill = idx[hour[idx] < 16]
        if len(asia) < int(6 * 60 / minutes * 0.75) or not len(window) or not len(fill):
            continue
        windows.append({"H": float(h[asia].max()), "L": float(l[asia].min()), "start": int(window[0]), "end": int(window[-1]),
                        "fill_end": int(fill[-1])})
    return windows


def mss_scan(window: dict, d: int, h, l, c, asia_hi=None, asia_lo=None) -> dict | None:
    """First sweep beyond the range side, MSS close through the swing, and the latest FVG of that move (or None)."""
    level, opposite = (window["L"], window["H"]) if d == 1 else (window["H"], window["L"])
    s = swing = None
    for j in range(window["start"], window["end"] + 1):
        beyond = l[j] < level if d == 1 else h[j] > level
        if beyond and asia_lo is not None and s is None:
            need = asia_lo[j] if d == 1 else asia_hi[j]
            beyond = bool(np.isfinite(need) and (l[j] < need if d == 1 else h[j] > need))
        deeper = s is not None and (l[j] < l[s] if d == 1 else h[j] > h[s])
        if (s is None and beyond) or deeper:
            s = j
            segment = h[max(0, j - SWING_LOOKBACK):j] if d == 1 else l[max(0, j - SWING_LOOKBACK):j]
            if not len(segment):
                s = None
                continue
            swing = float(segment.max() if d == 1 else segment.min())
            continue
        if s is not None and ((d == 1 and c[j] > swing) or (d == -1 and c[j] < swing)):
            for k in range(j, s + 1, -1):
                if k - 2 < s:
                    break
                if d == 1 and l[k] > h[k - 2]:
                    return {"dir": 1, "sweep": float(l[s]), "mss_bar": j, "ce": (float(h[k - 2]) + float(l[k])) / 2,
                            "opposite": opposite, "fill_end": window["fill_end"]}
                if d == -1 and h[k] < l[k - 2]:
                    return {"dir": -1, "sweep": float(h[s]), "mss_bar": j, "ce": (float(h[k]) + float(l[k - 2])) / 2,
                            "opposite": opposite, "fill_end": window["fill_end"]}
            return None
    return None


def mss_fill(setup: dict, o, h, l, c, atr, target_mode: str):
    """(fill bar, entry, stop, target) for the 50 % FVG limit, or None."""
    d, ce, sweep, opposite, m = setup["dir"], setup["ce"], setup["sweep"], setup["opposite"], setup["mss_bar"]
    a = atr[m]
    if not np.isfinite(a) or a <= 0:
        return None
    for j in range(m + 1, min(setup["fill_end"], len(o) - 1) + 1):
        p = j - 1
        if p > m:
            if (d == 1 and c[p] < sweep) or (d == -1 and c[p] > sweep):
                return None
            if (d == 1 and h[p] >= opposite) or (d == -1 and l[p] <= opposite):
                return None
        if (d == 1 and l[j] <= ce) or (d == -1 and h[j] >= ce):
            entry = min(ce, o[j]) if d == 1 else max(ce, o[j])
            stop = sweep - d * STOP_BUFFER_ATR * a
            risk = d * (entry - stop)
            if risk < MIN_RISK_ATR * a:
                return None
            if target_mode == "opposite":
                target = opposite
                if d * (target - entry) < risk:
                    return None
            else:
                target = entry + d * 2.0 * risk
            return j, float(entry), float(stop), float(target)
    return None


def mss_orders(ind: lab.Indicators, spec: dict):
    p = spec["params"]
    minutes = bar_minutes(ind.times)

    def build():
        asia_hi, asia_lo = asian_levels(ind.times, ind.h, ind.l, minutes) if p["model"] == "h4_asia" else (None, None)
        setups = []
        for window in sweep_windows(ind, p["model"], minutes):
            for d in (1, -1):
                setup = mss_scan(window, d, ind.h, ind.l, ind.c, asia_hi, asia_lo)
                if setup is not None:
                    setups.append(setup)
        return setups

    n = len(ind.c)
    side, stop, target, entry = np.zeros(n, dtype=int), np.full(n, np.nan), np.full(n, np.nan), np.full(n, np.nan)
    atr = ind.atr(14)
    for setup in ind._cached(("mss_setups", p["model"]), build):
        order = mss_fill(setup, ind.o, ind.h, ind.l, ind.c, atr, p["target"])
        if order is None:
            continue
        j, e, st, tg = order
        if side[j - 1] != 0:
            continue
        side[j - 1], entry[j - 1], stop[j - 1], target[j - 1] = setup["dir"], e, st, tg
    return side, stop, target, entry


lab.ORDER_BUILDERS["crt_mss"] = mss_orders


def mss_variants(symbol: str, timeframe: str) -> list[dict]:
    minutes = lab.TIMEFRAME_MINUTES[timeframe]
    out = []
    for model, target in product(MODELS, TARGETS):
        exits = {"stop": "sweep", "sl_atr": 0.0, "rr": 0.0, "trail_atr": 0.0, "swing_lookback": 0,
                 "max_bars": int(HOLD_HOURS[model] * 60 / minutes)}
        name = f"{model}|{target}"
        out.append({"family": "crt_mss", "params": {"symbol": symbol, "model": model, "target": target}, "exits": exits,
                    "description": f"CRT sweep + MSS + FVG 50% {timeframe} {name}", "variant": name})
    return out


def run(markets=MARKETS) -> dict:
    from .mtf_data import load_bars

    n_trials = len(MODELS) * len(TARGETS) * len(markets)
    report = {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "n_trials": n_trials, "markets": {}}
    for key in markets:
        symbol, timeframe = key.split(":")
        bars = load_bars(symbol, timeframe, source="mt5")
        market = lab.Market(symbol, timeframe, bars, swap=True)
        records = []
        for spec in mss_variants(symbol, timeframe):
            record = lab.evaluate_candidate(market, spec, with_holdout=True)
            orders = lab.strategy_orders(market.ind, spec)
            for split in ("search", "validation", "holdout"):
                record[f"{split}_r"] = crt_lab.r_stats(market.simulate(spec, split, orders), orders[1])
            record["variant"] = spec["variant"]
            records.append(record)
        sr_variance = lab._variance([[r["validation_sr"], r["validation"]["trades"]] for r in records if r["validation_sr"] is not None])
        for record in records:
            record["holdout_verdict"] = lab.holdout_verdict(record, n_trials, sr_variance)
        validated = [r for r in records if r["validated"]]
        chosen = max(validated, key=lambda r: r["score"]) if validated else None
        report["markets"][key] = {"info": market.info, "data_source": bars.attrs.get("source"), "variants": records,
                                  "validated": [r["variant"] for r in validated],
                                  "chosen_on_search_validation": chosen["variant"] if chosen else None}
    path = lab.LAB_DIR / f"crt_mss_backtest_{datetime.now(timezone.utc).strftime('%Y%m%d')}.json"
    path.write_text(json.dumps(report, indent=1, default=lambda v: v.item() if hasattr(v, "item") else str(v)), encoding="utf-8")
    report["path"] = str(path)
    return report


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="CRT sweep + MSS + FVG 50% entry (the owner's pictures), research only.")
    sub = parser.add_subparsers(dest="command", required=True)
    go = sub.add_parser("run")
    go.add_argument("--markets", nargs="+", default=list(MARKETS))
    args = parser.parse_args(argv)
    report = run(args.markets)
    for key, market in report["markets"].items():
        print(f"\n=== {key} ({market['data_source']}) {market['info']['data_start']} -> {market['info']['data_end']} | {market['info']['periods']}")
        for r in market["variants"]:
            v, s, va, ho = r.get("holdout_verdict") or {}, r["search"], r["validation"], r["holdout"]
            print(f"{r['variant']:18s} S {s['trades']:>4} PF {s['profit_factor']} {s['total_return_pct']}% R {r['search_r']['net_r']:>7} "
                  f"grossR {r['search_r']['gross_r']:>7} | V {va['trades']:>3} PF {va['profit_factor']} R {r['validation_r']['net_r']:>6} | "
                  f"{'PASS' if r['validated'] else 'fail'} {r['score']:>7} || H {ho['trades']:>3} PF {ho['profit_factor']} "
                  f"{ho['total_return_pct']}% R {r['holdout_r']['net_r']:>6} DD {ho['max_drawdown_pct']} DSR {v.get('deflated_sharpe')} "
                  f"{'PASS' if v.get('passed') else 'fail'}")
        print("validated:", market["validated"], "| chosen:", market["chosen_on_search_validation"])
    print("\nsaved:", report["path"], "| trials counted:", report["n_trials"])


if __name__ == "__main__":
    main()
