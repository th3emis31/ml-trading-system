"""CRT + 15m FVG retest and CRT AMD (Asian range) research models: research only, never trades.

Rules and the pre-stated variant grid are in strategies/crt_fvg_amd.md. Setups come from four models (the MT4
CRT_Dashboard_EA signal, classic H1 / H4 CRT, Asian-range AMD); each is entered with a resting limit order on the retest
of the first 15m fair value gap in its direction, simulated by strategy_lab.simulate_orders with spread and swap.

    python -m src.crt_fvg_lab run [--symbols XAUUSD BTCUSD]
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from itertools import product

import numpy as np
import pandas as pd

from . import crt_lab
from . import strategy_lab as lab

MODELS = ("crt_ea", "crt_h1", "crt_h4", "amd_asia")
ENTRIES, STOPS, TARGETS = ("proximal", "ce"), ("structure", "fvg"), ("rr2", "range")
PRIOR_CRT_TRIALS = 24          # crt_lab rounds 1 and 2 on the same markets
FILL_WINDOW_BARS = 16
STOP_BUFFER_ATR, MIN_RISK_ATR, MAX_BARS = 0.1, 0.5, 96


def server_time(times) -> pd.Series:
    return pd.to_datetime(pd.Series(times), utc=True).dt.tz_convert("America/New_York").dt.tz_localize(None) + pd.Timedelta(hours=7)


def htf_candles(times, o, h, l, c, hours: int, bar_minutes: int = 15) -> pd.DataFrame:
    """Server-time H1/H4/D1 candles built from ``bar_minutes`` bars, with the first and last bar index of each
    (75 % complete or more)."""
    bucket = server_time(times).dt.floor(f"{hours}h").to_numpy()
    frame = pd.DataFrame({"bucket": bucket, "i": np.arange(len(o)), "o": o, "h": h, "l": l, "c": c})
    g = frame.groupby("bucket", sort=True)
    out = pd.DataFrame({"start": g["i"].first(), "end": g["i"].last(), "n": g["i"].count(), "o": g["o"].first(),
                        "h": g["h"].max(), "l": g["l"].min(), "c": g["c"].last()})
    out["bucket"] = out.index
    return out[out["n"] >= int(hours * 60 / bar_minutes * 0.75)].reset_index(drop=True)


def crt_htf_setups(candles: pd.DataFrame, hours: int) -> list[dict]:
    """Classic CRT: B sweeps one side of A and closes back inside A -> trade toward A's other side."""
    setups, period = [], pd.Timedelta(hours=hours)
    rows = list(candles.itertuples(index=False))
    for a, b in zip(rows[:-1], rows[1:]):
        if pd.Timestamp(b.bucket) - pd.Timestamp(a.bucket) != period:
            continue
        common = {"mid_from": int(b.end) + 1, "k_to": int(b.end) + max(8, 4 * hours), "created": int(b.end), "fill_to": None}
        if b.l < a.l and a.l < b.c < a.h:
            setups.append(dict(common, dir=1, invalid=float(b.l), target=float(a.h)))
        elif b.h > a.h and a.l < b.c < a.h:
            setups.append(dict(common, dir=-1, invalid=float(b.h), target=float(a.l)))
    return setups


def amd_setups(times, o, h, l, c) -> list[dict]:
    """Asian range (00-06 UTC) swept 06-10 UTC, close back inside before 13 UTC -> trade toward the other side."""
    stamps = pd.to_datetime(pd.Series(times), utc=True)
    hour = stamps.dt.hour.to_numpy()
    day = stamps.dt.floor("D").to_numpy()
    setups = []
    _, starts = np.unique(day, return_index=True)
    bounds = list(starts) + [len(o)]
    for s, e in zip(bounds[:-1], bounds[1:]):
        idx = np.arange(s, e)
        asia = idx[hour[idx] < 6]
        if len(asia) < 16:
            continue
        high, low = float(h[asia].max()), float(l[asia].min())
        sweep = None
        for m in idx[(hour[idx] >= 6) & (hour[idx] < 10)]:
            up, down = h[m] > high, l[m] < low
            if up and down:
                break
            if up or down:
                sweep = (int(m), -1 if up else 1)
                break
        if sweep is None:
            continue
        s_bar, d = sweep
        before_13 = idx[(hour[idx] < 13) & (idx >= s_bar)]
        before_16 = idx[hour[idx] < 16]
        if not len(before_13) or not len(before_16):
            continue
        confirm = next((int(m) for m in before_13 if (c[m] < high if d == -1 else c[m] > low)), None)
        if confirm is None:
            continue
        invalid = float(h[s_bar:confirm + 1].max()) if d == -1 else float(l[s_bar:confirm + 1].min())
        setups.append({"dir": d, "invalid": invalid, "target": low if d == -1 else high, "mid_from": s_bar + 1,
                       "k_to": int(before_13[-1]), "created": confirm, "fill_to": int(before_16[-1])})
    return setups


def ea_setups(ind: lab.Indicators, symbol: str) -> list[dict]:
    sig = crt_lab.crt_signals(ind, symbol)
    setups = []
    for i in np.flatnonzero(sig["direction"]):
        d = int(sig["direction"][i])
        invalid = sig["ref_low"][i] if d == 1 else sig["ref_high"][i]
        if np.isfinite(invalid):
            setups.append({"dir": d, "invalid": float(invalid), "target": None, "mid_from": int(i) - 1, "k_to": int(i) + 2,
                           "created": int(i), "fill_to": None})
    return setups


def fvg_order(setup: dict, o, h, l, c, atr, entry_mode: str, stop_mode: str, target_mode: str):
    """(fill bar, entry, stop, target) for the first FVG retest of a setup, or None."""
    n, d, invalid, level = len(o), setup["dir"], setup["invalid"], setup["target"]
    for k in range(max(setup["mid_from"] + 1, 2), min(setup["k_to"], n - 1) + 1):
        if (d == 1 and c[k] < invalid) or (d == -1 and c[k] > invalid):
            return None
        if d == 1:
            if not l[k] > h[k - 2]:
                continue
            bottom, top = h[k - 2], l[k]
            if bottom <= invalid:
                continue
            limit = top if entry_mode == "proximal" else (top + bottom) / 2
            far_edge = bottom
        else:
            if not h[k] < l[k - 2]:
                continue
            bottom, top = h[k], l[k - 2]
            if top >= invalid:
                continue
            limit = bottom if entry_mode == "proximal" else (top + bottom) / 2
            far_edge = top
        a = atr[k]
        if not np.isfinite(a) or a <= 0:
            return None
        arm = max(k + 1, setup["created"] + 1)
        end = min(n - 1, setup["fill_to"] if setup["fill_to"] is not None else k + FILL_WINDOW_BARS)
        for j in range(arm, end + 1):
            p = j - 1
            if p > k:
                if (d == 1 and c[p] < invalid) or (d == -1 and c[p] > invalid):
                    return None
                if level is not None and ((d == 1 and h[p] >= level) or (d == -1 and l[p] <= level)):
                    return None
            if (d == 1 and l[j] <= limit) or (d == -1 and h[j] >= limit):
                entry = min(limit, o[j]) if d == 1 else max(limit, o[j])
                ref = invalid if stop_mode == "structure" else far_edge
                stop = ref - d * STOP_BUFFER_ATR * a
                risk = d * (entry - stop)
                if not np.isfinite(risk) or risk < MIN_RISK_ATR * a:
                    return None
                if target_mode == "range" and level is not None:
                    target = level
                    if d * (target - entry) < risk:
                        return None
                else:
                    rr = 3.0 if target_mode == "range" else 2.0
                    target = entry + d * rr * risk
                return j, float(entry), float(stop), float(target)
        return None
    return None


def model_setups(ind: lab.Indicators, symbol: str, model: str) -> list[dict]:
    def build():
        if model == "crt_ea":
            return ea_setups(ind, symbol)
        if model in ("crt_h1", "crt_h4"):
            hours = 1 if model == "crt_h1" else 4
            return crt_htf_setups(htf_candles(ind.times, ind.o, ind.h, ind.l, ind.c, hours), hours)
        return amd_setups(ind.times, ind.o, ind.h, ind.l, ind.c)

    return ind._cached(("fvg_setups", symbol, model), build)


def fvg_orders(ind: lab.Indicators, spec: dict):
    p = spec["params"]
    n = len(ind.c)
    side, stop, target, entry = np.zeros(n, dtype=int), np.full(n, np.nan), np.full(n, np.nan), np.full(n, np.nan)
    atr = ind.atr(14)
    for setup in model_setups(ind, p["symbol"], p["model"]):
        order = fvg_order(setup, ind.o, ind.h, ind.l, ind.c, atr, p["entry"], p["stop"], p["target"])
        if order is None:
            continue
        j, e, st, tg = order
        row = j - 1
        if side[row] != 0:
            continue
        side[row], entry[row], stop[row], target[row] = setup["dir"], e, st, tg
    return side, stop, target, entry


lab.ORDER_BUILDERS["crt_fvg"] = fvg_orders


def variants(symbol: str) -> list[dict]:
    exits = {"stop": "fvg", "sl_atr": 0.0, "rr": 0.0, "trail_atr": 0.0, "max_bars": MAX_BARS, "swing_lookback": 0}
    out = []
    for model, entry, stop, target in product(MODELS, ENTRIES, STOPS, TARGETS):
        name = f"{model}|{entry}|{stop}|{target if not (model == 'crt_ea' and target == 'range') else 'rr3'}"
        out.append({"family": "crt_fvg", "params": {"symbol": symbol, "model": model, "entry": entry, "stop": stop, "target": target},
                    "exits": dict(exits), "description": f"CRT+FVG retest {name}", "variant": name})
    return out


def run(symbols=("XAUUSD", "BTCUSD")) -> dict:
    from .mtf_data import load_bars

    registry = lab.load_registry()
    report = {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "markets": {}}
    for symbol in symbols:
        key = f"{symbol}:15m"
        market = lab.Market(symbol, "15m", load_bars(symbol, "15m", source="app"),
                            boundaries=(registry["markets"].get(key) or {}).get("boundaries"), swap=True)
        market.ind.htf_bars = load_bars(symbol, "4h", source="app")
        specs = variants(symbol)
        n_trials = PRIOR_CRT_TRIALS + len(specs)
        records = []
        for spec in specs:
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
        report["markets"][key] = {
            "info": market.info, "n_trials": n_trials, "setups": {m: len(model_setups(market.ind, symbol, m)) for m in MODELS},
            "variants": records, "validated": [r["variant"] for r in validated],
            "chosen_on_search_validation": chosen["variant"] if chosen else None}
    lab.LAB_DIR.mkdir(parents=True, exist_ok=True)
    path = lab.LAB_DIR / f"crt_fvg_backtest_{datetime.now(timezone.utc).strftime('%Y%m%d')}.json"
    path.write_text(json.dumps(report, indent=1, default=lambda v: v.item() if hasattr(v, "item") else str(v)), encoding="utf-8")
    report["path"] = str(path)
    return report


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="CRT + 15m FVG retest and CRT AMD research (never trades).")
    sub = parser.add_subparsers(dest="command", required=True)
    go = sub.add_parser("run")
    go.add_argument("--symbols", nargs="+", default=["XAUUSD", "BTCUSD"])
    args = parser.parse_args(argv)
    report = run(args.symbols)
    for key, market in report["markets"].items():
        print(f"\n=== {key} | {market['info']['periods']} | setups {market['setups']} | trials {market['n_trials']}")
        for r in market["variants"]:
            v, s, va, ho = r.get("holdout_verdict") or {}, r["search"], r["validation"], r["holdout"]
            print(f"{r['variant']:34s} S {s['trades']:>4} PF {s['profit_factor']} R {r['search_r']['net_r']:>7} | "
                  f"V {va['trades']:>3} PF {va['profit_factor']} R {r['validation_r']['net_r']:>6} | {'PASS' if r['validated'] else 'fail'} "
                  f"{r['score']:>7} || H {ho['trades']:>3} PF {ho['profit_factor']} {ho['total_return_pct']}% R {r['holdout_r']['net_r']:>6} "
                  f"DD {ho['max_drawdown_pct']} DSR {v.get('deflated_sharpe')} {'PASS' if v.get('passed') else 'fail'}")
        print("validated on search+validation:", market["validated"])
        print("chosen:", market["chosen_on_search_validation"])
    print("\nsaved:", report["path"])


if __name__ == "__main__":
    main()
