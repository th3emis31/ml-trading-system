"""Backtest the CISD card as a strategy, after costs, on both markets: research only, never trades.

    python -m src.cisd_lab run [--symbols XAUUSD BTCUSD] [--timeframes 1h 4h]

WHAT THE CARD DOES AND DOES NOT SAY
-----------------------------------
The card defines a SIGNAL. It says nothing about where the stop goes, what the target is, or when to give
up - and those decide most of the result. So the exits are pre-declared here, before any number is seen,
and they are mine rather than the card's:

* **Stop** beyond the price the run swept, plus a quarter-ATR buffer. That is the structural stop the
  pattern implies: the whole premise is that the sweep was the low, so price trading back through it says
  the premise was wrong. It is also what makes the risk vary with the setup instead of being a fixed ATR.
* **Target** at ``rr`` times that risk, measured from the signal bar's close.
* **Time exit** after ``MAX_BARS``, so a setup that goes nowhere stops tying up the test.

Entry is a market order at the NEXT bar's open - the signal bar's own close is never a fill price.

THE GRID, DECLARED BEFORE THE RESULT
------------------------------------
16 variants per market and timeframe: 2 run lengths x 2 sweep lookbacks x 2 waiting windows x 2 reward
ratios. Every one of them counts as a trial in the deflated Sharpe, which is the point of declaring them
here rather than trying ideas until one passes.

Two controls run beside each variant, because a pattern that "works" needs to beat more than zero:

* **The inverse.** The same signals traded the other way on the same bars. If the inverse also makes
  money, the edge is in the exits or the market's drift, not in the pattern.
* **All three splits.** search, validation and holdout must all be positive for the result to mean
  anything; one good split out of three is what a coin looks like.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from itertools import product

import numpy as np

from . import strategy_lab as lab
from .cisd import cisd_arrays

MIN_RUNS = (1, 2)
SWEEP_LOOKBACKS = (10, 20)
MAX_WAITS = (3, 5)
REWARD_RATIOS = (1.0, 2.0)
STOP_BUFFER_ATR = 0.25       # beyond the swept extreme, so the exact sweep price is not the stop
MAX_BARS = 48


def cisd_orders(ind: lab.Indicators, spec: dict):
    """side / stop / target for one CISD variant, anchored on the swept extreme."""
    p = spec["params"]
    min_run = int(p.get("min_run") or 1)
    sweep_lookback = int(p.get("sweep_lookback") or 10)
    max_wait = int(p.get("max_wait") or 5)
    key = ("cisd", min_run, sweep_lookback, max_wait)
    side, _level, extreme = ind._cached(
        key, lambda: cisd_arrays(ind.o, ind.h, ind.l, ind.c, min_run=min_run,
                                 sweep_lookback=sweep_lookback, max_wait=max_wait))
    side = side.copy()
    if p.get("inverse"):
        side = -side

    atr = ind.atr(14)
    buffer = STOP_BUFFER_ATR * atr
    with np.errstate(invalid="ignore"):
        # The stop sits beyond the price the run swept. `extreme` is the run's low for a bullish CISD and
        # its high for a bearish one, so the sign follows the ORIGINAL direction of the pattern - not the
        # inverted side, or the inverse control would be given a different (and easier) stop.
        original = np.where(np.isfinite(extreme), np.where(extreme < ind.c, 1, -1), 0)
        stop = np.where(original == 1, extreme - buffer,
                        np.where(original == -1, extreme + buffer, np.nan))
        risk = np.abs(ind.c - stop)
        target = np.where(side == 1, ind.c + p["rr"] * risk,
                          np.where(side == -1, ind.c - p["rr"] * risk, np.nan))
        # For the inverse control the stop must sit on the other side of the entry, or every inverse trade
        # is rejected by the engine and the control silently measures nothing.
        if p.get("inverse"):
            stop = np.where(side == 1, ind.c - risk, np.where(side == -1, ind.c + risk, np.nan))

    usable = np.isfinite(atr) & (atr > 0) & np.isfinite(stop) & np.isfinite(risk) & (risk > 0)
    side = np.where(usable, side, 0)
    return side.astype(int), stop, target


lab.ORDER_BUILDERS["cisd"] = cisd_orders


def cisd_variants(symbol: str, timeframe: str) -> list[dict]:
    """16 per market and timeframe, fixed before any result is looked at."""
    out = []
    for min_run, sweep_lookback, max_wait, rr in product(MIN_RUNS, SWEEP_LOOKBACKS, MAX_WAITS,
                                                         REWARD_RATIOS):
        out.append({
            "family": "cisd",
            "params": {"symbol": symbol, "timeframe": timeframe, "min_run": min_run,
                       "sweep_lookback": sweep_lookback, "max_wait": max_wait, "rr": rr},
            "exits": {"stop": "fixed", "sl_atr": 0.0, "rr": 0.0, "trail_atr": 0.0,
                      "max_bars": MAX_BARS, "swing_lookback": 0},
            "description": f"CISD run>={min_run}, sweep {sweep_lookback}, wait {max_wait}, {rr:.0f}R",
            "variant": f"run{min_run}|sweep{sweep_lookback}|wait{max_wait}|rr{rr:.0f}",
        })
    return out


def run(symbols=("XAUUSD", "BTCUSD"), timeframes=("1h", "4h")) -> dict:
    from .mtf_data import load_bars

    registry = lab.load_registry()
    n_trials = (len(MIN_RUNS) * len(SWEEP_LOOKBACKS) * len(MAX_WAITS) * len(REWARD_RATIOS)
                * len(symbols) * len(timeframes))
    report = {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
              "n_trials_total": n_trials,
              "definition": "bullish: close above the open of a run of down-close candles that swept a "
                            "prior low; bearish: the mirror. Stop beyond the swept extreme.",
              "markets": {}}
    for symbol, timeframe in product(symbols, timeframes):
        key = f"{symbol}:{timeframe}"
        bars = load_bars(symbol, timeframe, source="app")
        if bars is None or bars.empty:
            report["markets"][key] = {"error": "no broker bars"}
            continue
        market = lab.Market(symbol, timeframe, bars,
                            boundaries=(registry["markets"].get(key) or {}).get("boundaries"), swap=True)
        records = []
        for spec in cisd_variants(symbol, timeframe):
            record = lab.evaluate_candidate(market, spec, with_holdout=True)
            orders = lab.strategy_orders(market.ind, spec)
            record["variant"] = spec["variant"]
            record["signals"] = int(np.count_nonzero(orders[0]))
            inverse_spec = {**spec, "params": {**spec["params"], "inverse": True}}
            inverse = market.summary("holdout", market.simulate(inverse_spec, "holdout"))
            record["inverse_holdout"] = {"trades": inverse.get("trades"),
                                         "profit_factor": inverse.get("profit_factor"),
                                         "net_pct": inverse.get("total_return_pct")}
            record["splits_all_positive"] = all(
                (record.get(split) or {}).get("total_return_pct") is not None
                and record[split]["total_return_pct"] > 0
                for split in ("search", "validation", "holdout"))
            records.append(record)
        sr_variance = lab._variance([[r["validation_sr"], r["validation"]["trades"]]
                                     for r in records if r["validation_sr"] is not None])
        for record in records:
            record["holdout_verdict"] = lab.holdout_verdict(record, n_trials, sr_variance)
        report["markets"][key] = {
            "info": market.info, "variants": records,
            "passed_holdout": [r["variant"] for r in records
                               if (r.get("holdout_verdict") or {}).get("passed")],
            "positive_on_all_splits": [r["variant"] for r in records if r["splits_all_positive"]]}
    lab.LAB_DIR.mkdir(parents=True, exist_ok=True)
    scope = "-".join(sorted(symbols)) + "_" + "-".join(timeframes)
    path = lab.LAB_DIR / f"cisd_backtest_{scope}_{datetime.now(timezone.utc):%Y%m%d}.json"
    path.write_text(json.dumps(report, indent=1, default=lambda v: v.item() if hasattr(v, "item") else str(v)),
                    encoding="utf-8")
    report["path"] = str(path)
    return report


def permutation_check(market: lab.Market, spec: dict, *, split: str = "holdout", draws: int = 400,
                      seed: int = 20260926) -> dict:
    """Is this variant's profit skill, or would any trades of this shape and size have done as well?

    Each draw fires the SAME NUMBER of trades, with the SAME long/short mix and the SAME risk distances,
    at randomly chosen bars in the same period, through the same stops, targets, time exit, spread and
    swap. Only WHEN the trade happens changes. So the p-value answers one question exactly: does the CISD
    pattern pick moments, or is it just a way of being in this market with this stop?

    A deflated Sharpe asks whether a result survives the number of things tried. This asks something the
    deflation cannot: whether the timing carries any information at all. Simulates only; never trades.
    """
    side, stop, target = lab.strategy_orders(market.ind, spec)
    rows = market.rows[split]
    signal_rows = rows[side[rows] != 0]
    if len(signal_rows) < 20:
        return {"available": False, "reason": f"only {len(signal_rows)} signals in {split}; too few to permute"}

    real = market.summary(split, market.simulate(spec, split))
    real_net = float(real.get("total_return_pct") or 0.0)

    close = market.ind.c
    sides = side[signal_rows].astype(int)
    # Risk in PRICE units for each real trade, reused so the draws are sized like the real ones.
    risks = np.abs(close[signal_rows] - stop[signal_rows])
    reward = float(spec["params"]["rr"])

    atr = market.ind.atr(14)
    eligible = rows[np.isfinite(atr[rows]) & (atr[rows] > 0)]
    eligible = eligible[eligible < len(close) - 2]
    if len(eligible) < len(signal_rows) * 3:
        return {"available": False,
                "reason": f"{len(eligible)} eligible bars for {len(signal_rows)} signals; too few to permute"}

    rng = np.random.default_rng(seed)
    beats = 0
    nets = []
    for _ in range(draws):
        picked = rng.choice(eligible, size=len(signal_rows), replace=False)
        order = rng.permutation(len(signal_rows))
        fake_side = np.zeros_like(side)
        fake_stop = np.full(len(close), np.nan)
        fake_target = np.full(len(close), np.nan)
        for slot, bar in enumerate(picked):
            s = int(sides[order[slot]])
            risk = float(risks[order[slot]])
            if not np.isfinite(risk) or risk <= 0:
                continue
            fake_side[bar] = s
            fake_stop[bar] = close[bar] - risk if s == 1 else close[bar] + risk
            fake_target[bar] = close[bar] + reward * risk if s == 1 else close[bar] - reward * risk
        trades = market.simulate(spec, split, orders=(fake_side, fake_stop, fake_target))
        net = float(market.summary(split, trades).get("total_return_pct") or 0.0)
        nets.append(net)
        if net >= real_net:
            beats += 1

    nets_array = np.asarray(nets, dtype=float)
    return {"available": True, "split": split, "draws": draws,
            "real_net_pct": round(real_net, 3), "real_trades": int(real.get("trades") or 0),
            "random_mean_pct": round(float(nets_array.mean()), 3),
            "random_p95_pct": round(float(np.percentile(nets_array, 95)), 3),
            "beaten_by": int(beats),
            "p_value": round((beats + 1) / (draws + 1), 4),
            "note": "same trade count, long/short mix, risk distances, exits and costs; only the bars differ"}


def print_report(report: dict) -> None:
    print(f"CISD backtest - {report['n_trials_total']} trials, after spread and swap")
    for key, market in report["markets"].items():
        if market.get("error"):
            print(f"\n{key}: {market['error']}")
            continue
        print(f"\n{key}   holdout passed: {market['passed_holdout'] or 'none'}")
        rows = sorted(market["variants"], key=lambda r: -(r["holdout"].get("total_return_pct") or -999))
        # buy-and-hold sits in this table on purpose: a strategy that makes money while the market made
        # more is not an edge, it is a worse way of being long, and leaving the column out is how that
        # gets missed.
        hold_pct = (rows[0]["holdout"].get("buy_and_hold_pct") if rows else None)
        print(f"  buy and hold over the same holdout: {hold_pct:.1f}%" if hold_pct is not None else "")
        print(f"  {'variant':32} {'trades':>7} {'win%':>6} {'PF':>6} {'avg R':>7} "
              f"{'net%':>8} {'maxDD%':>7} {'inv net%':>9}")
        for record in rows[:8]:
            hold = record["holdout"]
            inverse = record["inverse_holdout"]
            print(f"  {record['variant']:32} {hold.get('trades', 0):>7} "
                  f"{(hold.get('win_rate_pct') or 0):>6.1f} {(hold.get('profit_factor') or 0):>6.2f} "
                  f"{(hold.get('avg_r') or 0):>7.3f} "
                  f"{(hold.get('total_return_pct') or 0):>8.2f} "
                  f"{(hold.get('max_drawdown_pct') or 0):>7.2f} "
                  f"{(inverse.get('net_pct') or 0):>9.2f}")
        if market["positive_on_all_splits"]:
            print(f"  positive on search, validation AND holdout: {market['positive_on_all_splits']}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Backtest the CISD pattern. Research only, never trades.")
    sub = parser.add_subparsers(dest="command", required=True)
    runner = sub.add_parser("run", help="run the pre-declared grid on both markets")
    runner.add_argument("--symbols", nargs="+", default=["XAUUSD", "BTCUSD"])
    runner.add_argument("--timeframes", nargs="+", default=["1h", "4h"])
    args = parser.parse_args(argv)

    report = run(tuple(args.symbols), tuple(args.timeframes))
    print_report(report)
    print(f"\nwritten to {report['path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
