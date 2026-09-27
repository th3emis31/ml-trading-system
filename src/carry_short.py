"""Does a gold short earn its keep by being held? Research only, never trades.

    python -m src.carry_short run [--symbol XAUUSD] [--timeframe 4h]

THE HYPOTHESIS, and the number that refutes it
----------------------------------------------
`strategies/carry_short_gold.md` states it in full. In short: the broker CREDITS a gold short 34.41 points
a night (measured 27 Sep 2026, `swap_mode` POINTS) and CHARGES a gold long 79.48, so the two sides differ by
about 9.7 % a year in financing alone. Until today this system charged shorts the long rate, so every short
strategy was designed under a cost model that penalised holding by 0.0270 % of price per night more than
reality. The claim is that a short setup with only a near-zero price expectancy turns profitable once it is
held across enough rollovers.

It is refuted by any of three outcomes, all printed by this module rather than left to interpretation:

1. after-cost expectancy_r does not rise as the hold lengthens;
2. the improvement is LARGER than the summed carry can explain, so price did the work, not financing;
3. the same sweep improves on the LONG side too, which would make it a trend effect rather than a carry one.

ONE VARIABLE ONLY
-----------------
The entry is deliberately borrowed, not invented: `sweep_reversal` in `reclaim` mode, whose behaviour is
already measured. Only `max_bars` moves. That keeps the configuration count at TEN (five hold lengths x two
sides), which is where `.claude/skills/measure` says to stop and get new evidence rather than keep sweeping -
a 180-variant grid would make the best row the luckiest row before the hypothesis had been tested at all.

THE DECOMPOSITION
-----------------
`simulate_orders` records `swap_pct` and `nights` per trade, and computes
``net_pct = (gross - cost_pct - swap_frac) * 100``. So for each trade:

    carry contribution to net  = -swap_pct        (negative swap_pct is a CREDIT, so it adds)
    price-and-spread component =  net_pct + swap_pct

Summing those separately is what tells price and financing apart, and it is the whole reason this module
exists rather than another row in the direction sweep.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

import numpy as np

from . import strategy_lab as lab

# Named for this experiment rather than generically, because two similar names already exist and mean other
# things: `forward_evidence.verdict` judges a FORWARD test against fixed profit-factor and expectancy
# criteria, and `cisd_lab.print_report` is a thin wrapper over `strategy_lab.print_variant_table`, whose
# table cannot render a per-side hold sweep with a carry decomposition. Neither is reusable here.

# The independent variable: 4h bars, so 6 bars is one night and 120 is roughly twenty.
HOLD_BARS = (6, 12, 30, 60, 120)
BASE_REF = 1          # the reclaim reference window that produced the most trades on gold 4h
BASE_RR = 2.0
BASE_TREND_EMA = 0    # no trend filter in the base test; it is a defence to add only if the idea survives


def spec_for(symbol: str, timeframe: str, max_bars: int, side: str) -> dict:
    """One reclaim spec, differing from its siblings only in how long the trade may be held."""
    return {
        "family": "sweep_reversal",
        "params": {"symbol": symbol, "timeframe": timeframe, "ref": BASE_REF, "rr": BASE_RR,
                   "mode": "reclaim", "trend_ema": BASE_TREND_EMA, "lookback": BASE_REF,
                   "require_body": False, "side": side},
        "exits": {"stop": "fixed", "sl_atr": 0.0, "rr": 0.0, "trail_atr": 0.0,
                  "max_bars": int(max_bars), "swing_lookback": 0},
        "variant": f"{side}|hold{max_bars}b",
    }


def decompose(trades: list[dict]) -> dict:
    """Split the after-cost result into what price paid and what financing paid."""
    if not trades:
        return {}
    net = float(sum(t["net_pct"] for t in trades))
    swap = float(sum(t.get("swap_pct") or 0.0 for t in trades))
    nights = float(sum(t.get("nights") or 0.0 for t in trades))
    return {
        "trades": len(trades),
        "sum_net_pct": round(net, 3),
        # A CREDIT shows here as a positive number, because swap_pct is negative when the broker pays.
        "carry_pct": round(-swap, 3),
        "price_and_spread_pct": round(net + swap, 3),
        "nights_total": round(nights, 1),
        "nights_per_trade": round(nights / len(trades), 2),
        "carry_per_night_pct": round((-swap / nights), 5) if nights else None,
    }


def run(symbol: str = "XAUUSD", timeframe: str = "4h") -> dict:
    from .mtf_data import load_bars

    # The reclaim builder registers itself on import, so importing the module IS the registration. Without
    # this every row came back KeyError: 'sweep_reversal' - the family exists, but only once its lab has been
    # loaded. `direction_sweep.load_families` does the same thing for the whole set.
    from . import sweep_reversal as _sweep_reversal
    if hasattr(_sweep_reversal, "_register"):
        _sweep_reversal._register()
    if "sweep_reversal" not in lab.ORDER_BUILDERS:
        return {"available": False, "reason": "the sweep_reversal builder did not register"}

    bars = load_bars(symbol, timeframe, source="app")
    if bars is None or bars.empty:
        return {"available": False, "reason": "no broker bars"}

    registry = lab.load_registry()
    key = f"{symbol}:{timeframe}"
    market = lab.Market(symbol, timeframe, bars,
                        boundaries=(registry["markets"].get(key) or {}).get("boundaries"), swap=True)

    holding = market.holding or {}
    out = {"available": True, "market": key,
           "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
           "configurations_tried": len(HOLD_BARS) * 2,
           "swap": {"mode": holding.get("mode"), "long": holding.get("long"), "short": holding.get("short")},
           "info": market.info, "rows": []}

    for side in ("short", "long"):
        for max_bars in HOLD_BARS:
            spec = spec_for(symbol, timeframe, max_bars, side)
            try:
                trades = market.simulate(spec, "holdout")
            except Exception as exc:                      # noqa: BLE001 - one bad row must not stop the test
                out["rows"].append({"side": side, "hold_bars": max_bars, "error": f"{type(exc).__name__}: {exc}"[:110]})
                continue
            summary = market.summary("holdout", trades)
            inverse_spec = {**spec, "params": {**spec["params"], "inverse": True}}
            try:
                inverse = market.summary("holdout", market.simulate(inverse_spec, "holdout"))
            except Exception:                             # noqa: BLE001
                inverse = {}
            out["rows"].append({
                "side": side, "hold_bars": max_bars, "variant": spec["variant"],
                "trades": summary.get("trades"),
                "win_rate_pct": summary.get("win_rate_pct"),
                "expectancy_r": summary.get("expectancy_r"),
                "expectancy_r_bound": summary.get("expectancy_r_bound"),
                "net_pct": summary.get("total_return_pct"),
                "max_drawdown_pct": summary.get("max_drawdown_pct"),
                "ambiguous_exits": summary.get("ambiguous_exits"),
                "buy_and_hold_pct": summary.get("buy_and_hold_pct"),
                "inverse_net_pct": inverse.get("total_return_pct"),
                "inverse_trades": inverse.get("trades"),
                "insufficient_evidence": (summary.get("trades") or 0) < 100,
                **decompose(trades),
            })

    out["verdict"] = carry_verdict(out["rows"])
    lab.LAB_DIR.mkdir(parents=True, exist_ok=True)
    path = lab.LAB_DIR / f"carry_short_{symbol}_{timeframe}_{datetime.now(timezone.utc):%Y%m%d}.json"
    path.write_text(json.dumps(out, indent=1, default=lambda v: v.item() if hasattr(v, "item") else str(v)),
                    encoding="utf-8")
    out["path"] = str(path)
    return out


def carry_verdict(rows: list[dict]) -> dict:
    """The three refutation tests from the strategy document, answered by the numbers rather than by me."""
    shorts = [r for r in rows if r.get("side") == "short" and r.get("trades")]
    longs = [r for r in rows if r.get("side") == "long" and r.get("trades")]
    if len(shorts) < 2:
        return {"supported": False, "why": "not enough short rows to compare hold lengths"}

    shortest, longest = shorts[0], shorts[-1]
    e_short = shortest.get("expectancy_r")
    e_long = longest.get("expectancy_r")

    # 1. does expectancy rise with the hold?
    rises = (e_short is not None and e_long is not None and e_long > e_short)

    # 2. is the improvement no larger than the carry can explain? Compare the CHANGE in total return with
    #    the CHANGE in accumulated carry. If the return grew by much more than the carry did, price did it.
    d_net = (longest.get("net_pct") or 0.0) - (shortest.get("net_pct") or 0.0)
    d_carry = (longest.get("carry_pct") or 0.0) - (shortest.get("carry_pct") or 0.0)
    carry_explains = bool(d_carry > 0 and d_net <= d_carry * 1.5)

    # 3. does the LONG side improve too? If it does, longer holds help regardless of financing.
    long_also = False
    if len(longs) >= 2:
        le_short, le_long = longs[0].get("expectancy_r"), longs[-1].get("expectancy_r")
        long_also = (le_short is not None and le_long is not None and le_long > le_short)

    enough = (longest.get("trades") or 0) >= 100
    return {
        "expectancy_rises_with_hold": rises,
        "carry_explains_the_gain": carry_explains,
        "long_side_also_improves": long_also,
        "hundred_trades_at_longest_hold": enough,
        "delta_net_pct": round(d_net, 3),
        "delta_carry_pct": round(d_carry, 3),
        "supported": bool(rises and carry_explains and not long_also and enough),
        "why": ("all three refutation tests survived and the sample clears 100 trades" if
                (rises and carry_explains and not long_also and enough) else
                "; ".join(filter(None, [
                    None if rises else "expectancy does not rise with the hold",
                    None if carry_explains else "the gain is bigger than the carry explains, so price did it",
                    "the long side improves too, so it is a trend effect" if long_also else None,
                    None if enough else "under 100 trades at the longest hold: insufficient evidence",
                ]))),
    }


def print_carry_report(report: dict) -> None:
    info = report.get("info") or {}
    swap = report.get("swap") or {}
    print("PRE-FLIGHT (.claude/skills/measure) - read before any number below")
    print(f"  units       {report['market'].split(':')[0]} quotes to 0.01; costs are a FRACTION of price, "
          f"round trip {info.get('cost_round_trip_pct')}")
    print(f"  swap        mode {swap.get('mode')}: long {swap.get('long')} / night, short {swap.get('short')} "
          f"/ night. A NEGATIVE short rate is a CREDIT - that is the whole hypothesis.")
    print(f"  scale       4h bars, holds of {HOLD_BARS[0]}-{HOLD_BARS[-1]} bars = about 1-20 nights. Stop is "
          f"the swept extreme, not an ATR fraction, so it cannot fall inside one candle.")
    print( "  causality   signals on closed bars, fill at the NEXT bar's open; a last-bar signal is not traded")
    print( "  control     every row is run inverted on the same bars, and the LONG side is run as a second "
           "control for 'longer holds just help'")
    print(f"  provenance  broker bars via the app, {info.get('bars')} bars, {info.get('data_start')} -> "
          f"{info.get('data_end')}; holdout {(info.get('periods') or {}).get('holdout')}")
    print(f"  trials      {report.get('configurations_tried')} configurations, ONE variable (hold length). "
          f"Any row under 100 closed trades is INSUFFICIENT EVIDENCE whatever its profit factor.")
    print()

    print(f"  {'side':6} {'hold':>6} {'trades':>7} {'win%':>6} {'exp_r':>7} {'net%':>8} "
          f"{'carry%':>8} {'price%':>8} {'nights/tr':>10} {'inv%':>8} {'amb':>4} evidence")
    for r in report.get("rows") or []:
        if r.get("error"):
            print(f"  {r['side']:6} {r['hold_bars']:>6} {'-':>7}  {r['error']}")
            continue
        ev = "INSUFFICIENT" if r.get("insufficient_evidence") else "ok"
        print(f"  {r['side']:6} {r['hold_bars']:>5}b {r.get('trades'):>7} {r.get('win_rate_pct'):>6} "
              f"{r.get('expectancy_r'):>7} {r.get('net_pct'):>8} {r.get('carry_pct'):>8} "
              f"{r.get('price_and_spread_pct'):>8} {r.get('nights_per_trade'):>10} "
              f"{str(r.get('inverse_net_pct')):>8} {str(r.get('ambiguous_exits')):>4} {ev}")

    v = report.get("verdict") or {}
    print()
    print("  THE THREE REFUTATION TESTS, declared before the run:")
    print(f"    1 expectancy_r rises with the hold          {v.get('expectancy_rises_with_hold')}")
    print(f"    2 the carry explains the gain               {v.get('carry_explains_the_gain')}"
          f"   (net moved {v.get('delta_net_pct')}pp, carry moved {v.get('delta_carry_pct')}pp)")
    print(f"    3 the LONG side does NOT also improve       {not v.get('long_side_also_improves')}")
    print(f"      100+ trades at the longest hold           {v.get('hundred_trades_at_longest_hold')}")
    print()
    print(f"  VERDICT: {'SUPPORTED' if v.get('supported') else 'NOT SUPPORTED'} - {v.get('why')}")
    if report.get("path"):
        print(f"  written to {report['path']}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Carry-positive short test. Research only, never trades.")
    sub = parser.add_subparsers(dest="command", required=True)
    runner = sub.add_parser("run", help="sweep the hold length on both sides and decompose price vs carry")
    runner.add_argument("--symbol", default="XAUUSD")
    runner.add_argument("--timeframe", default="4h")
    args = parser.parse_args(argv)

    report = run(args.symbol, args.timeframe)
    if not report.get("available"):
        print(report.get("reason"))
        return 1
    print_carry_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
