"""Separate GENERATING a strategy from PROVING one, so the evidence bar becomes reachable.

    python -m src.strategy_confirm register    # lock in the current survivors
    python -m src.strategy_confirm status      # progress against what each one needs
    python -m src.strategy_confirm confirm     # score them on bars that arrived AFTER registration

THE PROBLEM THIS SOLVES, WITH THE ARITHMETIC
--------------------------------------------
The Strategy Lab has tried 395,134 candidates and passed none. That is not a verdict on the strategies,
it is a consequence of how the gate is fed. Reaching DSR 0.95 requires

    sr > sr0 + 1.645 / sqrt(n - 1)      with      sr0 = sqrt(sr_variance) * E[max of n_trials]

and with the lab's own `sr_variance` of 0.01 that floor is:

| n_trials | 1 | 1,000 | 10,000 | 50,000 (the lab, per market) |
|---|---|---|---|---|
| sr0 | 0.052 | 0.326 | 0.386 | **0.424** |

Measured per-trade Sharpe of the real strategies here is 0.0777 (live EA, gold 4h) and 0.0288 (1h). Both
sit far below 0.424, and below the floor the margin is negative, so NO number of trades can rescue them.
The lab searches 50,000 candidates and then asks the winner to survive deflation for 50,000 trials, so
every extra candidate raises the bar for all of them. The harder it searches, the less anything can prove.

The bar is not the problem and is not touched here - `HOLDOUT_CRITERIA["min_deflated_sharpe"]` stays at
0.95, a standing owner rule since 14 September 2026. What changes is the trial count the bar is applied
against: a SMALL, pre-declared set of candidates, scored on data none of them has seen.

WHY THE WINDOW HAS TO BE FORWARD, AND CANNOT BE CARVED OUT OF HISTORY
--------------------------------------------------------------------
The obvious implementation - hold back a slice of existing history - does not work here, and saying so is
the whole honesty of this module. The lab's holdout already runs to the last closed bar, so there is no
unused later data to find; any slice carved out now has already influenced which candidates survived,
because they were selected using neighbours, rolling windows and Monte Carlo over that same history.

So confirmation is FORWARD ONLY. A candidate is registered with the data end at that moment, and it is
scored exclusively on bars that arrive afterwards. That cannot be gamed by re-running, re-picking or
re-tuning, because the window is defined by a timestamp written before the evidence existed - the same
discipline `src/self_improvement.py` applies to predictions, and the reason a refuted result there still
counts as progress.

The cost is patience: at 8 registered candidates the floor is 0.089, and a candidate with a per-trade
Sharpe of 0.15 then needs about 330 trades to clear 0.95. On XAUUSD 4h at roughly 28 trades a year that
is years away; on 15m it is months. `trades_needed` is reported for every slot so the wait is a number on
the page rather than a surprise.

NOTHING HERE TRADES, and nothing it writes changes any live behaviour. It appends to its own ledger and
reads the lab's own Market/simulate/holdout_verdict machinery rather than re-implementing any of it.
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .runtime_paths import smartentry_data_dir

# The pre-declared number of slots. This IS the trial count the deflation uses, so it is deliberately
# small and deliberately fixed: growing it later would retroactively raise the bar for candidates already
# registered, which is the exact self-defeating loop this module exists to escape.
# TWO, not eight. Measured while building this, and it overturned the first choice: the floor is
# sqrt(0.01) * E[max of n_trials], and E[max] climbs fast - 0.520 at one or two trials, 0.853 at three,
# 1.459 at eight. The live EA's per-trade Sharpe is 0.0777, so at THREE slots the floor (0.0853) already
# exceeds it and nothing real can pass. Two is therefore the largest honest number, and it is free because
# `deflated_sharpe` does max(n_trials, 2) so one and two give the identical floor of 0.052.
CONFIRM_SLOTS = 2
LEDGER_NAME = "confirmations.jsonl"
Z95 = 1.6449                 # DSR 0.95
SR_VARIANCE = 0.01           # the lab's own value; see the module docstring for how it was verified


def confirm_ledger(base: Optional[Path] = None) -> Path:
    return Path(base or smartentry_data_dir()) / "strategy_lab" / LEDGER_NAME


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def floor_for(n_trials: int) -> float:
    """sr0: the per-trade Sharpe a candidate must EXCEED before any amount of evidence helps."""
    from .strategy_lab import _expected_max_factor

    return math.sqrt(SR_VARIANCE) * _expected_max_factor(max(int(n_trials), 2))


def trades_needed(per_trade_sharpe: float, n_trials: int = CONFIRM_SLOTS) -> Optional[int]:
    """How many forward trades this edge needs to clear DSR 0.95, or None if it never can.

    None is the honest answer for an edge below the floor, and it is the most useful thing this module
    reports: it turns "failed" into "cannot pass at this trial count", which is a different sentence and
    points at a different fix.
    """
    margin = float(per_trade_sharpe) - floor_for(n_trials)
    if margin <= 0:
        return None
    return int(math.ceil(1 + (Z95 / margin) ** 2))


def implied_sharpe(dsr: float, trades: int, n_trials: int) -> Optional[float]:
    """Recover a candidate's per-trade Sharpe from the DSR the book already recorded for it.

    The book stores `deflated_sharpe`, `n_trials` and the holdout trade count, but not the raw Sharpe, so
    it is inverted rather than left unknown:

        DSR = Phi((sr - sr0) * sqrt(n-1) / sqrt(denom))   =>   sr = sr0 + Phi^-1(DSR) / sqrt(n-1)

    taking denom = 1. That is an approximation: `denominator` in `strategy_lab.deflated_sharpe` carries the
    returns' skew and kurtosis, and for a fat-tailed trade distribution it differs from 1, so the recovered
    Sharpe is an estimate. It is used only to size the forward evidence a candidate will need, never
    reported as the candidate's measured Sharpe.
    """
    if dsr is None or trades is None or trades < 2:
        return None
    dsr = min(max(float(dsr), 1e-6), 1 - 1e-6)
    # inverse normal CDF via erfinv, so no scipy dependency
    z = math.sqrt(2.0) * _erfinv(2.0 * dsr - 1.0)
    return round(floor_for(n_trials or 1) + z / math.sqrt(trades - 1), 5)


def _erfinv(y: float) -> float:
    """Inverse error function, Newton-refined from a rational start. Keeps scipy out of a hot path."""
    if y <= -1.0:
        return -float("inf")
    if y >= 1.0:
        return float("inf")
    a = 0.147
    ln = math.log(1 - y * y) if abs(y) < 1 else -745.0
    first = 2 / (math.pi * a) + ln / 2
    x = math.copysign(math.sqrt(max(math.sqrt(first * first - ln / a) - first, 0.0)), y)
    for _ in range(3):                      # Newton on erf(x) - y
        err = math.erf(x) - y
        x -= err / (2 / math.sqrt(math.pi) * math.exp(-x * x))
    return x


def years_to_prove(per_trade_sharpe: float, trades_per_year: float,
                   n_trials: int = CONFIRM_SLOTS) -> Optional[float]:
    """How long the forward evidence will take at this market's own trade rate.

    Reported because `trades_needed` alone hides the answer. A 4h strategy taking 28 trades a year and
    needing 4,090 of them is not "patience", it is 146 years - and knowing that is what turns this from a
    waiting game into a clear instruction to find a STRONGER edge rather than a more patient one.
    """
    need = trades_needed(per_trade_sharpe, n_trials)
    if need is None or trades_per_year <= 0:
        return None
    return round(need / trades_per_year, 1)


def read_confirmations(base: Optional[Path] = None) -> list:
    path = confirm_ledger(base)
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    # Later rows for the same slot supersede earlier ones, so the ledger stays append-only while
    # `status` still shows one line per candidate.
    latest = {}
    for row in rows:
        latest[row.get("slot_id")] = row
    return list(latest.values())


def _append_row(row: dict, base: Optional[Path] = None) -> Path:
    path = confirm_ledger(base)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")
    return path


def register(candidates: list, base: Optional[Path] = None) -> dict:
    """Lock in up to CONFIRM_SLOTS candidates. The registration time defines their evidence window.

    `candidates` is a list of dicts carrying at least `market`, `spec` and `validation_sr` - the shape
    `strategy_lab.evaluate_candidate` already returns. Registration records the DATA END at this moment,
    not the wall clock, because bars are what the evidence is counted in.
    """
    existing = read_confirmations(base)
    open_slots = CONFIRM_SLOTS - len([r for r in existing if r.get("state") == "watching"])
    if open_slots <= 0:
        return {"ok": False, "registered": 0,
                "reason": f"all {CONFIRM_SLOTS} slots are in use; a slot frees when its candidate is "
                          f"settled, and the count is fixed so the bar cannot move under those already "
                          f"registered"}

    taken = {r.get("slot_id") for r in existing}
    added = []
    for cand in candidates[:open_slots]:
        sr = cand.get("validation_sr")
        slot_id = f"{cand.get('market')}::{cand.get('id') or cand.get('spec_id')}"
        if slot_id in taken:
            continue
        row = {
            "slot_id": slot_id, "state": "watching", "registered_at": _stamp(),
            "data_end_at_registration": cand.get("data_end"),
            "market": cand.get("market"), "family": cand.get("family"),
            "description": cand.get("description"), "spec": cand.get("spec"),
            "n_trials": CONFIRM_SLOTS,
            "floor_sr0": round(floor_for(CONFIRM_SLOTS), 4),
            "expected_per_trade_sharpe": sr,
            "trades_needed": trades_needed(sr, CONFIRM_SLOTS) if sr is not None else None,
            "bar": "deflated Sharpe >= 0.95, unchanged; only the trial count differs",
            "places_orders": False,
        }
        _append_row(row, base)
        added.append(row)
        taken.add(slot_id)
    return {"ok": True, "registered": len(added), "slots": CONFIRM_SLOTS,
            "floor_sr0": round(floor_for(CONFIRM_SLOTS), 4), "candidates": added}


def confirm_one(row: dict, bars=None) -> dict:
    """Score one registered candidate on bars AFTER its registration only.

    The window is built by handing the lab's own Market the registration point as `holdout_start`, so the
    holdout split IS the forward window and every number comes from the lab's own simulate/summary code.
    """
    from . import strategy_lab as lab

    market_key = row.get("market") or ""
    if ":" not in market_key:
        return {"ok": False, "slot_id": row.get("slot_id"), "reason": f"bad market key {market_key!r}"}
    symbol, timeframe = market_key.split(":", 1)
    cut = row.get("data_end_at_registration")
    if not cut:
        return {"ok": False, "slot_id": row.get("slot_id"),
                "reason": "no data end recorded at registration, so the forward window is undefined"}

    try:
        frame = bars if bars is not None else lab._default_loader(symbol, timeframe)
        market = lab.Market(symbol, timeframe, frame,
                            boundaries={"validation_start": cut, "holdout_start": cut})
    except Exception as exc:
        return {"ok": False, "slot_id": row.get("slot_id"), "reason": f"{type(exc).__name__}: {exc}"}

    spec = row.get("spec") or {}
    orders = lab.strategy_orders(market.ind, spec)
    trades = market.simulate(spec, "holdout", orders)
    summary = market.summary("holdout", trades)
    record = {"holdout": summary, "holdout_returns": [t["net_pct"] for t in trades]}
    verdict = lab.holdout_verdict(record, row["n_trials"], SR_VARIANCE)

    got = summary.get("trades") or 0
    need = row.get("trades_needed")
    return {"ok": True, "slot_id": row.get("slot_id"), "market": market_key,
            "window_from": market.boundaries["holdout_start"], "window_to": market.info["data_end"],
            "forward_trades": got, "trades_needed": need,
            "progress_pct": round(got / need * 100, 1) if need else None,
            "summary": {k: summary.get(k) for k in
                        ("trades", "win_rate_pct", "profit_factor", "total_return_pct",
                         "max_drawdown_pct", "expectancy_pct")},
            "verdict": verdict, "places_orders": False}


def confirm(base: Optional[Path] = None) -> dict:
    """Score every watching candidate and append the result. Settles only when the bar is genuinely met."""
    out = []
    for row in read_confirmations(base):
        if row.get("state") != "watching":
            continue
        result = confirm_one(row)
        out.append(result)
        verdict = (result.get("verdict") or {}) if result.get("ok") else {}
        if verdict.get("passed"):
            settled = dict(row)
            settled.update({"state": "confirmed", "settled_at": _stamp(), "result": result})
            _append_row(settled, base)
    return {"checked": len(out), "results": out,
            "confirmed": [r["slot_id"] for r in out if (r.get("verdict") or {}).get("passed")],
            "generated_at": _stamp(), "places_orders": False}


def watchlist_survivors(limit: int = CONFIRM_SLOTS) -> list:
    """The book's current watchlist entries, best first, in the shape `register` expects.

    Read straight from `strategy_book.load_book` rather than through a helper that does not exist. Each
    entry's own `latest` record carries the validation Sharpe that decides how much forward evidence the
    candidate will need.
    """
    from . import strategy_book as book

    entries = book.load_book().get("entries") or {}
    rows = list(entries.values()) if isinstance(entries, dict) else list(entries)
    watch = [e for e in rows if isinstance(e, dict) and e.get("status") == "watchlist"]
    watch.sort(key=lambda e: float((e.get("latest") or {}).get("score") or e.get("score") or 0), reverse=True)
    out = []
    for entry in watch[:limit]:
        latest = entry.get("latest") or {}
        out.append({"market": entry.get("market"), "id": entry.get("id"), "family": entry.get("family"),
                    "description": entry.get("description"), "spec": entry.get("spec"),
                    # The book does not store the raw Sharpe, so recover it from the DSR it did store.
                    "validation_sr": (latest.get("validation_sr")
                                      or implied_sharpe((latest.get("deflated_sharpe")),
                                                        ((latest.get("holdout") or {}).get("trades")),
                                                        (latest.get("n_trials") or 1))),
                    "implied_from_dsr": latest.get("validation_sr") is None,
                    "book_dsr": latest.get("deflated_sharpe"),
                    "book_trials": latest.get("n_trials"),
                    "book_holdout": latest.get("holdout"),
                    "data_end": latest.get("data_end") or entry.get("data_end")})
    return out


def confirm_status(base: Optional[Path] = None) -> dict:
    rows = read_confirmations(base)
    watching = [r for r in rows if r.get("state") == "watching"]
    unreachable = [r["slot_id"] for r in watching if r.get("trades_needed") is None]
    return {
        "slots": CONFIRM_SLOTS, "used": len(watching), "free": CONFIRM_SLOTS - len(watching),
        "floor_sr0": round(floor_for(CONFIRM_SLOTS), 4),
        "floor_at_lab_scale": round(floor_for(50_000), 4),
        "watching": watching,
        "confirmed": [r["slot_id"] for r in rows if r.get("state") == "confirmed"],
        "cannot_pass_at_this_trial_count": unreachable,
        "note": ("The bar is unchanged at deflated Sharpe 0.95. Only the trial count differs: "
                 f"{CONFIRM_SLOTS} pre-declared candidates give a floor of {floor_for(CONFIRM_SLOTS):.4f} "
                 f"instead of {floor_for(50_000):.4f} at the lab's search scale. Evidence is counted "
                 "only on bars that arrived after registration."),
        "places_orders": False,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Confirm a small pre-declared set of strategies on forward bars.")
    parser.add_argument("command", choices=("status", "register", "confirm"))
    parser.add_argument("--top", type=int, default=CONFIRM_SLOTS,
                        help="how many watchlist survivors to register (register only)")
    args = parser.parse_args(argv)

    if args.command == "status":
        s = confirm_status()
        print(f"Confirmation slots: {s['used']} of {s['slots']} used, {s['free']} free")
        print(f"  floor at {s['slots']} trials       : {s['floor_sr0']}")
        print(f"  floor at the lab's 50,000 trials : {s['floor_at_lab_scale']}   <- why nothing passes there")
        for row in s["watching"]:
            need = row.get("trades_needed")
            print(f"  [{row['market']}] sr {row.get('expected_per_trade_sharpe')} "
                  f"needs {need if need else 'CANNOT PASS at this trial count'} forward trades "
                  f"since {row.get('data_end_at_registration')}")
            print(f"      {str(row.get('description'))[:100]}")
        if s["cannot_pass_at_this_trial_count"]:
            print(f"  below the floor, so no evidence can help: {len(s['cannot_pass_at_this_trial_count'])}")
        print(f"\n  {s['note']}")
        return 0

    if args.command == "register":
        from . import strategy_book as book

        entries = watchlist_survivors(args.top)
        if not entries:
            print("No watchlist survivors found in the strategy book, so nothing was registered.")
            print("This is a report, not a failure: registering nothing is correct when the book is empty.")
            return 0
        out = register(entries)
        print(json.dumps(out, indent=1)[:1500])
        return 0

    out = confirm()
    print(f"checked {out['checked']} candidate(s); confirmed {len(out['confirmed'])}")
    for r in out["results"]:
        if not r.get("ok"):
            print(f"  {r.get('slot_id')}: could not score - {r.get('reason')}")
            continue
        v = r.get("verdict") or {}
        print(f"  {r['slot_id']}")
        print(f"      forward window {r['window_from']} -> {r['window_to']}")
        print(f"      {r['forward_trades']} of {r['trades_needed']} trades needed"
              f" ({r['progress_pct']}%)" if r.get("trades_needed") else
              f"      {r['forward_trades']} trades; this edge cannot pass at this trial count")
        print(f"      DSR {v.get('deflated_sharpe')}  passed={v.get('passed')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
