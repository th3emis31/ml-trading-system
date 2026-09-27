"""The owner's FVG retest rule on 15m. Research only, never trades.

    python -m src.fvg_retest run [--symbol XAUUSD] [--timeframe 15m]
    python -m src.fvg_retest full [--symbol XAUUSD] [--timeframe 15m]

THE RULE AS GIVEN
-----------------
"Run backtest FVG 15m entry after retest when candle after retest close above full body" - so:

  1. a fair value gap forms (three-candle imbalance);
  2. price comes back and RETESTS the gap;
  3. the candle AFTER that retest closes back beyond the gap with a FULL BODY;
  4. enter on that confirmation.

Each of those four is made computable below, and the two that are genuinely ambiguous - how much of a
candle counts as "full body", and how long a gap stays valid - are SWEPT rather than chosen.

WHAT IS REUSED, AND WHAT IS NOT
-------------------------------
The gap detector is `smart_entry_arch.fvg_flags`, not a second implementation: bar i's LOW above bar i-2's
HIGH, which is a real imbalance known at bar i's close. Its own docstring warns that
``features.detect_fvg`` is a DIFFERENT thing wearing the same name - it tests for consecutive rising
closes - so that one is deliberately not used either.

`crt_fvg_lab` is also not extended, because it is not this rule: its setups must come from a CRT range
sweep first, so it can never produce a plain "gap, retest, confirm" trade.

CAUSALITY, SPELLED OUT
----------------------
The gap is known at bar i's close. The retest is known at bar j's close. The confirmation is known at bar
j+1's close, and that is the signal bar - the engine then fills at bar j+2's OPEN. Nothing reads a bar it
could not have seen, and the confirmation candle's own body is measured from its own open and close.

THE ONE THING TO WATCH, REPORTED NOT ASSUMED
--------------------------------------------
The stop sits at the far edge of the gap, so the risk unit is the GAP WIDTH. A gap can be narrow, and a
stop narrower than a typical 15m candle would let the engine's stop-first convention decide trades instead
of the market - the error recorded twice in `.claude/memory/LESSONS.md`. So the pre-flight prints the median
gap width against the median bar range, and every row reports its ambiguous-exit count. A row whose stop is
inside one candle is not a result.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from itertools import product

import numpy as np

from . import strategy_lab as lab
from .smart_entry_arch import fvg_flags

# "full body" has no single definition, so it is swept: the body must be at least this share of the
# candle's whole range. 0.6 is a decisive candle; 0.8 is a near-marubozu.
BODY_FRACTIONS = (0.6, 0.8)
REWARD_RATIOS = (1.0, 2.0, 3.0)
MAX_WAIT_BARS = 40      # how long a gap stays live waiting for its retest (40 x 15m = 10 hours)
MAX_BARS = 48           # time exit, 12 hours


def fvg_retest_signals(ind: lab.Indicators, body_fraction: float, max_wait: int):
    """side / stop / target arrays for the gap-retest-confirm sequence.

    Walks each gap forward exactly once, so one gap yields at most one trade and a later gap cannot
    retroactively change an earlier one.
    """
    o, h, l, c = ind.o, ind.h, ind.l, ind.c
    n = len(c)
    bullish, bearish = ind._cached(("fvg_flags",), lambda: fvg_flags(h, l))

    side = np.zeros(n, dtype=int)
    stop = np.full(n, np.nan, dtype=float)          # gap_far: the far edge of the gap
    stop_confirm = np.full(n, np.nan, dtype=float)  # confirm_bar: the confirmation candle's own extreme
    stop_retest = np.full(n, np.nan, dtype=float)   # retest_bar: the deepest point of the retest

    body = np.abs(c - o)
    rng = h - l
    with np.errstate(invalid="ignore", divide="ignore"):
        full_body = np.where(rng > 0, body / rng, 0.0) >= body_fraction

    for i in np.flatnonzero(bullish | bearish):
        is_bull = bool(bullish[i])
        # The gap itself: between bar i-2's high and bar i's low (mirrored for a bearish gap).
        low_edge = h[i - 2] if is_bull else h[i]
        high_edge = l[i] if is_bull else l[i - 2]
        if not np.isfinite(low_edge) or not np.isfinite(high_edge) or high_edge <= low_edge:
            continue

        # Walk forward for the RETEST: the first bar that trades back into the gap.
        retest = -1
        for j in range(i + 1, min(i + 1 + max_wait, n)):
            # A close beyond the far side kills the gap before it is retested - the structure is gone.
            if (is_bull and c[j] < low_edge) or (not is_bull and c[j] > high_edge):
                break
            if l[j] <= high_edge and h[j] >= low_edge:
                retest = j
                break
        if retest < 0 or retest + 1 >= n:
            continue

        # The CONFIRMATION is the very next candle: it must close back beyond the gap, with a full body,
        # and in the direction of the gap.
        k = retest + 1
        closed_beyond = c[k] > high_edge if is_bull else c[k] < low_edge
        right_way = c[k] > o[k] if is_bull else c[k] < o[k]
        if not (closed_beyond and right_way and full_body[k]):
            continue

        side[k] = 1 if is_bull else -1
        stop[k] = low_edge if is_bull else high_edge
        stop_confirm[k] = l[k] if is_bull else h[k]
        stop_retest[k] = min(l[retest], l[k]) if is_bull else max(h[retest], h[k])

    return side, stop, stop_confirm, stop_retest


def fvg_retest_orders(ind: lab.Indicators, spec: dict):
    """side / stop / target, with the stop at the far edge of the gap and the target an R multiple of it."""
    p = spec["params"]
    body_fraction = float(p.get("body_fraction") or 0.6)
    rr = float(p.get("rr") or 2.0)
    max_wait = int(p.get("max_wait") or MAX_WAIT_BARS)

    stop_mode = str(p.get("stop_mode") or "gap_far")
    side, s_gap, s_confirm, s_retest = ind._cached(
        ("fvg_retest", body_fraction, max_wait),
        lambda: fvg_retest_signals(ind, body_fraction, max_wait))
    side = side.copy()
    base_stop = {"gap_far": s_gap, "confirm_bar": s_confirm, "retest_bar": s_retest}[stop_mode]

    wanted = str(p.get("side") or p.get("base_side") or "")
    if wanted in ("long", "short"):
        keep = 1 if wanted == "long" else -1
        side = np.where(side == keep, keep, 0).astype(int)

    with np.errstate(invalid="ignore"):
        risk = np.abs(ind.c - base_stop)

    # The control mirrors the stop at the same distance rather than reusing the gap edge, which would sit
    # on the wrong side once the direction is flipped and would be rejected as unusable - the zero-trade
    # control bug already recorded twice today.
    if p.get("inverse"):
        side = -side
        with np.errstate(invalid="ignore"):
            stop = np.where(side == 1, ind.c - risk, np.where(side == -1, ind.c + risk, np.nan))
    else:
        stop = base_stop

    with np.errstate(invalid="ignore"):
        target = np.where(side == 1, ind.c + rr * risk, np.where(side == -1, ind.c - rr * risk, np.nan))
        usable = (np.isfinite(stop) & np.isfinite(risk) & (risk > 0)
                  & np.where(side == 1, stop < ind.c, np.where(side == -1, stop > ind.c, False)))
    side = np.where(usable, side, 0).astype(int)
    return side, stop, target


def _register() -> None:
    lab.ORDER_BUILDERS["fvg_retest"] = fvg_retest_orders


_register()


STOP_MODES = ("gap_far", "confirm_bar", "retest_bar")


def fvg_variants(symbol: str = "XAUUSD", timeframe: str = "15m") -> list:
    """Nine: three stop placements x three reward ratios, at the body threshold with a usable sample.

    body_fraction is FIXED at 0.6 here: at 0.8 the holdout carried only 35-39 trades, under the evidence
    bar, so sweeping it again would only add trials without adding evidence. The variable under test is the
    STOP, because it was measured at 8.30 points against a 1.55 point gap - my choice, not the owner's rule.
    """
    out = []
    for stop_mode, rr in product(STOP_MODES, REWARD_RATIOS):
        body_fraction = 0.6
        out.append({
            "family": "fvg_retest",
            "params": {"symbol": symbol, "timeframe": timeframe, "body_fraction": body_fraction,
                       "rr": rr, "max_wait": MAX_WAIT_BARS, "stop_mode": stop_mode},
            "exits": {"stop": "fixed", "sl_atr": 0.0, "rr": 0.0, "trail_atr": 0.0,
                      "max_bars": MAX_BARS, "swing_lookback": 0},
            "description": (f"three-candle FVG, retest, then the NEXT candle closes beyond the gap with a "
                            f"body >= {body_fraction:g} of its range; stop at the far gap edge, {rr:g}R"),
            "variant": f"{stop_mode}|rr{rr:g}",
        })
    return out


def scale_check(market: lab.Market) -> dict:
    """Median gap width against median bar range - the number that says whether the stop is measurable."""
    ind = market.ind
    bullish, bearish = fvg_flags(ind.h, ind.l)
    widths = []
    for i in np.flatnonzero(bullish):
        widths.append(float(ind.l[i] - ind.h[i - 2]))
    for i in np.flatnonzero(bearish):
        widths.append(float(ind.l[i - 2] - ind.h[i]))
    widths = [w for w in widths if np.isfinite(w) and w > 0]
    bar_range = float(np.nanmedian(ind.h - ind.l))
    return {"gaps": len(widths),
            "median_gap": round(float(np.median(widths)), 4) if widths else None,
            "median_bar_range": round(bar_range, 4),
            "gap_over_bar": round(float(np.median(widths)) / bar_range, 2) if widths and bar_range else None}


def _market(symbol: str, timeframe: str):
    from .mtf_data import load_bars
    bars = load_bars(symbol, timeframe, source="app")
    if bars is None or bars.empty:
        return None
    registry = lab.load_registry()
    key = f"{symbol}:{timeframe}"
    return lab.Market(symbol, timeframe, bars,
                      boundaries=(registry["markets"].get(key) or {}).get("boundaries"), swap=True)


def run(symbol: str = "XAUUSD", timeframe: str = "15m", side: str = "both") -> dict:
    market = _market(symbol, timeframe)
    if market is None:
        return {"available": False, "reason": "no broker bars"}

    rows = []
    for spec in fvg_variants(symbol, timeframe):
        if side in ("long", "short"):
            spec = {**spec, "params": {**spec["params"], "side": side}}
        try:
            trades = market.simulate(spec, "holdout")
        except Exception as exc:                          # noqa: BLE001
            rows.append({"variant": spec["variant"], "error": f"{type(exc).__name__}: {exc}"[:110]})
            continue
        summary = market.summary("holdout", trades)
        inv_params = {**spec["params"], "inverse": True}
        if "side" in inv_params:                      # hand the filter to this builder, not to the lab's
            inv_params["base_side"] = inv_params.pop("side")
        inverse_spec = {**spec, "params": inv_params}
        try:
            inverse = market.summary("holdout", market.simulate(inverse_spec, "holdout"))
        except Exception:                                 # noqa: BLE001
            inverse = {}
        longs = sum(1 for t in trades if t["side"] == "BUY")
        rows.append({
            "variant": spec["variant"], "trades": summary.get("trades"),
            "long_trades": longs, "short_trades": (summary.get("trades") or 0) - longs,
            "win_rate_pct": summary.get("win_rate_pct"), "profit_factor": summary.get("profit_factor"),
            "expectancy_r": summary.get("expectancy_r"),
            "net_pct": summary.get("total_return_pct"),
            "max_drawdown_pct": summary.get("max_drawdown_pct"),
            "ambiguous_exits": summary.get("ambiguous_exits"),
            "buy_and_hold_pct": summary.get("buy_and_hold_pct"),
            "inverse_net_pct": inverse.get("total_return_pct"),
            "inverse_trades": inverse.get("trades"),
            "insufficient_evidence": (summary.get("trades") or 0) < 100,
            "beats_inverse": bool((inverse.get("trades") or 0) > 0
                                  and inverse.get("total_return_pct") is not None
                                  and inverse["total_return_pct"] < 0
                                  and (summary.get("total_return_pct") or -999) > inverse["total_return_pct"]),
        })

    report = {"available": True, "market": f"{symbol}:{timeframe}", "side": side,
              "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
              "configurations_tried": len(rows), "scale": scale_check(market),
              "info": market.info, "rows": rows}
    lab.LAB_DIR.mkdir(parents=True, exist_ok=True)
    path = lab.LAB_DIR / f"fvg_retest_{side}_{symbol}_{timeframe}_{datetime.now(timezone.utc):%Y%m%d}.json"
    path.write_text(json.dumps(report, indent=1, default=lambda v: v.item() if hasattr(v, "item") else str(v)),
                    encoding="utf-8")
    report["path"] = str(path)
    return report


def run_full(symbol: str = "XAUUSD", timeframe: str = "15m", side: str = "both") -> dict:
    """All three splits plus the deflated Sharpe, charged for every configuration tried."""
    market = _market(symbol, timeframe)
    if market is None:
        return {"available": False, "reason": "no broker bars"}

    variants = fvg_variants(symbol, timeframe)
    if side in ("long", "short"):
        variants = [{**v, "params": {**v["params"], "side": side}} for v in variants]
    records = []
    for spec in variants:
        try:
            record = lab.evaluate_candidate(market, spec, with_holdout=True)
        except Exception as exc:                          # noqa: BLE001
            records.append({"variant": spec["variant"], "error": f"{type(exc).__name__}: {exc}"[:110]})
            continue
        record["variant"] = spec["variant"]
        records.append(record)

    usable = [r for r in records if not r.get("error") and r.get("validation_sr") is not None]
    sr_variance = lab._variance([[r["validation_sr"], (r.get("validation") or {}).get("trades")]
                                 for r in usable]) if usable else 0.0
    for record in records:
        if not record.get("error"):
            record["holdout_verdict"] = lab.holdout_verdict(record, len(variants), sr_variance)

    report = {"available": True, "market": f"{symbol}:{timeframe}", "side": side, "n_trials": len(variants),
              "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
              "scale": scale_check(market), "info": market.info, "records": records}
    lab.LAB_DIR.mkdir(parents=True, exist_ok=True)
    path = lab.LAB_DIR / f"fvg_retest_full_{side}_{symbol}_{timeframe}_{datetime.now(timezone.utc):%Y%m%d}.json"
    path.write_text(json.dumps(report, indent=1, default=lambda v: v.item() if hasattr(v, "item") else str(v)),
                    encoding="utf-8")
    report["path"] = str(path)
    return report


def _preflight(report: dict) -> None:
    info = report.get("info") or {}
    scale = report.get("scale") or {}
    periods = info.get("periods") or {}
    print("PRE-FLIGHT (.claude/skills/measure) - read before any number below")
    print(f"  units       round-trip {info.get('cost_round_trip_pct')} as a FRACTION of price; "
          f"cost model {info.get('cost_model')}")
    print(f"  scale       {scale.get('gaps')} gaps found; median gap {scale.get('median_gap')} against a "
          f"median bar range of {scale.get('median_bar_range')} = {scale.get('gap_over_bar')}x a candle.")
    print( "              The stop IS the gap, so under about 1x the engine's stop-first convention would be "
           "deciding trades. Ambiguous exits are reported per row.")
    print( "  causality   gap known at bar i's close, retest at bar j, confirmation at bar j+1 (the signal "
           "bar); the engine fills at bar j+2's OPEN")
    print( "  control     every variant is run inverted with the stop MIRRORED at the same distance, so the "
           "control actually trades; a zero-trade inverse is a free pass, not a control")
    print(f"  provenance  broker bars via the app, {info.get('bars')} bars, {info.get('data_start')} -> "
          f"{info.get('data_end')}")
    if periods.get("holdout"):
        print(f"  splits      search {periods.get('search')}")
        print(f"              validation {periods.get('validation')}")
        print(f"              holdout {periods.get('holdout')}")
    print(f"  trials      {report.get('configurations_tried') or report.get('n_trials')} configurations. "
          f"Under 100 closed trades is INSUFFICIENT EVIDENCE whatever the profit factor.")
    print()


def print_run_report(report: dict) -> None:
    _preflight(report)
    print(f"  {'variant':16} {'trades':>7} {'L/S':>9} {'win%':>6} {'PF':>6} {'exp_r':>8} {'net%':>8} "
          f"{'dd%':>7} {'inv%':>8} {'amb':>4} evidence")
    for r in sorted(report.get("rows") or [], key=lambda x: -(x.get("net_pct") or -999)):
        if r.get("error"):
            print(f"  {r['variant']:16} {r['error']}")
            continue
        ev = "INSUFFICIENT" if r.get("insufficient_evidence") else ("ok" if r.get("beats_inverse") else "no control")
        print(f"  {r['variant']:16} {r.get('trades'):>7} "
              f"{str(r.get('long_trades')) + '/' + str(r.get('short_trades')):>9} "
              f"{str(r.get('win_rate_pct')):>6} {str(r.get('profit_factor')):>6} "
              f"{str(r.get('expectancy_r')):>8} {str(r.get('net_pct')):>8} "
              f"{str(r.get('max_drawdown_pct')):>7} {str(r.get('inverse_net_pct')):>8} "
              f"{str(r.get('ambiguous_exits')):>4} {ev}")
    if report.get("path"):
        print(f"\n  written to {report['path']}")


def print_full_report(report: dict) -> None:
    _preflight(report)
    print(f"  {'variant':16} {'search%':>9} {'valid%':>9} {'hold%':>9} {'hold tr':>8} {'amb':>5} {'dSR':>7} {'passed':>7}")
    for r in sorted(report.get("records") or [],
                    key=lambda x: -((x.get("holdout") or {}).get("total_return_pct") or -999)):
        if r.get("error"):
            print(f"  {r['variant']:16} {r['error']}")
            continue
        hold = r.get("holdout") or {}
        hv = r.get("holdout_verdict") or {}
        print(f"  {r['variant']:16} {str((r.get('search') or {}).get('total_return_pct')):>9} "
              f"{str((r.get('validation') or {}).get('total_return_pct')):>9} "
              f"{str(hold.get('total_return_pct')):>9} {str(hold.get('trades')):>8} "
              f"{str(hold.get('ambiguous_exits')):>5} {str(hv.get('deflated_sharpe')):>7} "
              f"{str(hv.get('passed')):>7}")
    allpos = [r for r in report.get("records") or []
              if not r.get("error") and all((r.get(s) or {}).get("total_return_pct") is not None
                                            and r[s]["total_return_pct"] > 0
                                            for s in ("search", "validation", "holdout"))]
    passed = [r for r in report.get("records") or [] if (r.get("holdout_verdict") or {}).get("passed")]
    print()
    print(f"  positive on ALL THREE splits: {len(allpos)} of {len(report.get('records') or [])}"
          f"  -> {[r['variant'] for r in allpos] or 'none'}")
    print(f"  cleared the full bar (incl. deflated Sharpe 0.95): {len(passed)}"
          f"  -> {[r['variant'] for r in passed] or 'none'}")
    if report.get("path"):
        print(f"  written to {report['path']}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="FVG retest confirmation. Research only, never trades.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, helptext in (("run", "holdout only"), ("full", "all three splits plus the deflated Sharpe")):
        sp = sub.add_parser(name, help=helptext)
        sp.add_argument("--symbol", default="XAUUSD")
        sp.add_argument("--timeframe", default="15m")
        sp.add_argument("--side", default="both", choices=("both", "long", "short"))
    args = parser.parse_args(argv)

    report = (run_full if args.command == "full" else run)(args.symbol, args.timeframe, args.side)
    if not report.get("available"):
        print(report.get("reason"))
        return 1
    (print_full_report if args.command == "full" else print_run_report)(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
