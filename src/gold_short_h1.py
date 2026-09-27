"""A SHORT-ONLY gold H1 rule, built from the owner's own winning entries. Research only, never trades.

    python -m src.gold_short_h1 run

WHERE THE RULES CAME FROM
-------------------------
Not from a card or a video. From `src/manual_edge.py` reading the owner's 54 real gold trades:

* they are **94 % SELL**, win **70.6 %** of those and made **+GBP 494.95**, while the system's own gold
  trades are 88 % long and losing
* gold **ROSE 8.98 %** across the window those trades span, so the short side is not trend-following
* their winners, against their own losers, more often had the **SAR just flipped** (33.3 % vs 22.2 %),
  more often **swept the prior 20-bar high** (19.4 % vs 11.1 %), and were entered **later in the day**
  (mean 14:00 UTC vs 10:50; the 18:00-23:00 window was 12 trades and 12 winners)

So the rule tested here is: **sell gold on H1, after the highs have been taken, when the SAR has turned
down, in the sessions the owner actually wins in.** Each of those three is a switch, so the measurement
says which of them earns its place rather than assuming the stack helps.

THE CONTAMINATION, STATED BEFORE THE RESULT
-------------------------------------------
These features were chosen by looking at 54 trades that fall between 28 June and 12 August 2026 - inside
the Strategy Lab's holdout. That is fitting, however light: a rule derived from data cannot be honestly
tested on the same data. Two things follow and both are reported:

1. The **search and validation** splits (2018 -> 2024) contain none of the owner's trades, so they are the
   honest windows here, and the holdout is the contaminated one - the reverse of the usual reading.
2. A permutation test against random timing, which asks whether the entries are picked or merely placed.

Anything that only works in the holdout is the fitting showing through, and must be read as such.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from itertools import product

import numpy as np

from . import strategy_lab as lab
from .turn_anatomy import parabolic_sar

SWEEP_LOOKBACK = 20
# MEASURED from the owner's 54 gold trades, not chosen: their winners move a median 0.35 ATR and their
# losers 0.31 ATR, and the median hold is THIRTY MINUTES. The first version of this module used a stop at
# the swept high plus a quarter ATR and targets of 1-2 R over twelve hours - a half-day swing, which is a
# different strategy from the one being studied. On H1 bars a thirty-minute trade often opens and closes
# inside one candle, so the timeframe was coarser than the trade as well.
# IN POINTS, not in ATR. Their 0.35 ATR was measured against the HOURLY ATR (about 18 points on gold),
# which is 6.4 points. Applying "0.35 ATR" to a 15m frame instead made the levels 2.1 points - smaller
# than one 15m candle's 7.17-point average range - so a quarter of all trades touched both levels in the
# same bar and the engine booked every one of them a loss. A fair 1:1 then measured 37.3% instead of 50%.
# Fixed points remove the unit error entirely, and are what the owner's trades actually are.
STOP_POINTS = (4.0, 6.4, 9.0)     # 6.4 is their median winning move; the others bracket it
TARGET_POINTS = (4.0, 6.4, 9.0)
MAX_BARS = 24                # 24 x 5m = two hours, against their median hold of thirty minutes
# The owner's own session split: everything, the afternoon on, and the late window that went 12 for 12.
SESSIONS = {"all_hours": None, "from_13utc": (13, 23), "late_18_23utc": (18, 23)}
SWEEP_FILTERS = (True, False)
SAR_FILTERS = (True, False)


def short_orders(ind: lab.Indicators, spec: dict):
    """side / stop / target. SHORT ONLY - the side the owner actually makes money on."""
    p = spec["params"]
    stop_points = float(p.get("stop_points") or 6.4)
    target_points = float(p.get("target_points") or 6.4)
    window = SESSIONS.get(str(p.get("session") or "all_hours"))
    need_sweep = bool(p.get("need_sweep", True))
    need_sar = bool(p.get("need_sar", True))
    n = len(ind.c)

    _sar, rising = ind._cached(("sar",), lambda: parabolic_sar(ind.h, ind.l))
    flipped_down = np.zeros(n, dtype=bool)
    flipped_down[1:] = (~rising[1:]) & rising[:-1]

    prior_high = ind.highest(SWEEP_LOOKBACK, shift=1)
    with np.errstate(invalid="ignore"):
        swept = (ind.h > prior_high) & np.isfinite(prior_high)

    allowed = np.ones(n, dtype=bool)
    if window is not None:
        hours = ind.times.dt.hour.to_numpy()
        allowed = (hours >= window[0]) & (hours <= window[1])

    short = allowed.copy()
    if need_sar:
        short = short & flipped_down
    if need_sweep:
        short = short & swept

    side = np.where(short, -1, 0).astype(int)
    if p.get("inverse"):
        side = -side

    atr = ind.atr(14)
    with np.errstate(invalid="ignore"):
        stop = np.where(side == -1, ind.c + stop_points,
                        np.where(side == 1, ind.c - stop_points, np.nan))
        risk = np.abs(ind.c - stop)
        target = np.where(side == -1, ind.c - target_points,
                          np.where(side == 1, ind.c + target_points, np.nan))
        usable = (np.isfinite(atr) & (atr > 0) & np.isfinite(stop) & np.isfinite(risk) & (risk > 0)
                  & np.where(side == -1, stop > ind.c, np.where(side == 1, stop < ind.c, False)))
    side = np.where(usable, side, 0)
    return side.astype(int), stop, target


lab.ORDER_BUILDERS["gold_short_h1"] = short_orders


def short_variants(symbol: str = "XAUUSD", timeframe: str = "5m") -> list:
    """108: 3 sessions x sweep on/off x SAR on/off x 3 stops x 3 targets. Declared before any result."""
    out = []
    for session, need_sweep, need_sar, stop_points, target_points in product(
            SESSIONS, SWEEP_FILTERS, SAR_FILTERS, STOP_POINTS, TARGET_POINTS):
        name = (f"{session}|{'sweep' if need_sweep else 'nosweep'}|"
                f"{'sar' if need_sar else 'nosar'}|s{stop_points:g}|t{target_points:g}")
        out.append({
            "family": "gold_short_h1",
            "params": {"symbol": symbol, "timeframe": timeframe, "session": session,
                       "need_sweep": need_sweep, "need_sar": need_sar,
                       "stop_points": stop_points, "target_points": target_points},
            "exits": {"stop": "fixed", "sl_atr": 0.0, "rr": 0.0, "trail_atr": 0.0,
                      "max_bars": MAX_BARS, "swing_lookback": 0},
            "description": (f"short gold H1 in {session}"
                            + (", after the highs are swept" if need_sweep else "")
                            + (", once the SAR turns down" if need_sar else "")
                            + f", stop {stop_points:g} pts, target {target_points:g} pts"),
            "variant": name,
        })
    return out


def run(symbol: str = "XAUUSD", timeframe: str = "5m") -> dict:
    from .mtf_data import load_bars

    registry = lab.load_registry()
    variants = short_variants(symbol, timeframe)
    key = f"{symbol}:{timeframe}"
    bars = load_bars(symbol, timeframe, source="app")
    if bars is None or bars.empty:
        return {"available": False, "reason": "no broker bars"}
    market = lab.Market(symbol, timeframe, bars,
                        boundaries=(registry["markets"].get(key) or {}).get("boundaries"), swap=True)
    records = []
    for spec in variants:
        record = lab.evaluate_candidate(market, spec, with_holdout=True)
        record["variant"] = spec["variant"]
        record["signals"] = int(np.count_nonzero(lab.strategy_orders(market.ind, spec)[0]))
        inverse = {**spec, "params": {**spec["params"], "inverse": True}}
        inv = market.summary("holdout", market.simulate(inverse, "holdout"))
        record["inverse_holdout"] = {"trades": inv.get("trades"), "net_pct": inv.get("total_return_pct")}
        record["splits_all_positive"] = all(
            (record.get(s) or {}).get("total_return_pct") is not None and record[s]["total_return_pct"] > 0
            for s in ("search", "validation", "holdout"))
        # The UNCONTAMINATED windows: the owner's trades are all inside the holdout, so these two are
        # where a real edge has to show up first.
        record["clean_windows_positive"] = all(
            (record.get(s) or {}).get("total_return_pct") is not None and record[s]["total_return_pct"] > 0
            for s in ("search", "validation"))
        records.append(record)
    sr_variance = lab._variance([[r["validation_sr"], r["validation"]["trades"]]
                                 for r in records if r["validation_sr"] is not None])
    for record in records:
        record["holdout_verdict"] = lab.holdout_verdict(record, len(variants), sr_variance)
    report = {"available": True, "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
              "n_trials_total": len(variants),
              "note": ("short-only gold H1, rules taken from the owner's own 54 trades. Those trades fall "
                       "inside the holdout, so search and validation are the honest windows here."),
              "markets": {key: {"info": market.info, "variants": records,
                                "passed_holdout": [r["variant"] for r in records
                                                   if (r.get("holdout_verdict") or {}).get("passed")],
                                "positive_on_all_splits": [r["variant"] for r in records
                                                           if r["splits_all_positive"]],
                                "positive_on_clean_windows": [r["variant"] for r in records
                                                              if r["clean_windows_positive"]]}}}
    lab.LAB_DIR.mkdir(parents=True, exist_ok=True)
    path = lab.LAB_DIR / f"gold_short_h1_{datetime.now(timezone.utc):%Y%m%d}.json"
    path.write_text(json.dumps(report, indent=1, default=lambda v: v.item() if hasattr(v, "item") else str(v)),
                    encoding="utf-8")
    report["path"] = str(path)
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Short-only gold H1 from the owner's own trades. Never trades.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="run the declared grid")
    parser.parse_args(argv)

    report = run()
    if not report.get("available"):
        print(report.get("reason"))
        return 1
    lab.print_variant_table(report, title="Short-only gold H1, from the owner's own entries",
                            sort="net", show_signals=True)
    market = next(iter(report["markets"].values()))
    print(f"\n  positive on the UNCONTAMINATED windows (search + validation): "
          f"{market['positive_on_clean_windows'] or 'none'}")
    print(f"  written to {report['path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
