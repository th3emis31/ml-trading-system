"""Is the bar data actually sound? Gaps, duplicates and impossible candles — layer 1 of the diagram.

    python -m src.data_audit XAUUSD 4h
    python -m src.data_audit --all

Every backtest in this project rests on these bars and none of it has ever been checked. `mtf_data`
does call `drop_duplicates` and `dropna`, but it drops SILENTLY and it looks for neither missing bars
nor impossible candles — so a hole in the history or a high below its own low would quietly change every
result downstream and never announce itself.

WHAT IS CHECKED, AND WHY EACH ONE MATTERS HERE
----------------------------------------------
* **Duplicates** — two rows for one timestamp. A duplicated bar double-counts a move and can make a
  strategy appear to enter twice at the same price.
* **Ordering** — bars out of time order break every rolling indicator silently, because a window then
  contains the future.
* **Impossible candles** — `high < low`, or an open or close outside the bar's own range. These cannot
  occur in real data; where they appear they are a feed or conversion error, and a stop placed inside a
  broken bar resolves nonsensically.
* **Non-positive prices** — a zero or negative price makes every percentage return meaningless, and one
  zero close is enough to produce a -100 % return that no equity curve recovers from.
* **Gaps** — missing bars. This one needs care rather than a threshold, which is why it is reported in
  three buckets rather than as a single number (see `gap_report`).
* **Frozen bars** — `high == low == open == close`. Legal but suspicious in size: a run of them usually
  means a stalled feed rather than a genuinely motionless market.

THE GAP PROBLEM, STATED HONESTLY
--------------------------------
Gold closes at the weekend AND for about an hour every day; bitcoin does neither. So "the interval is
bigger than one bar" is not evidence of anything. The first version of this hard-coded "a Friday gap is
the weekend" and reported 20,017 missing bars on XAUUSD 4h - 55.6 % of the history - which would have been
an alarming and wrong headline. The session is now LEARNED from the data (`trading_slots`): a slot present
on most of its weekly occurrences is one the instrument really trades, and only an absence from one of
those counts. Intervals are then classified:

* **expected** — exactly one bar apart.
* **closed** — the gap spans only slots this instrument never trades, so the market was shut. Reported
  separately so it can be seen and ignored rather than folded into either the good or the bad pile.
* **missing** — a gap that skips at least one slot the instrument DOES normally trade. The real holes.
* **odd** — a gap that is not a whole multiple of the bar size at all, which usually means the timestamps
  are not on the grid the timeframe claims.

KNOWN LIMITATION OF THE LEARNED SESSION
---------------------------------------
Inferring the session needs each slot to recur. On a short history - a few weeks or less - a slot whose
only occurrence is the missing bar has no surviving occurrences at all, so it looks like a slot the
instrument never trades and the hole is reported as a closure instead. The method therefore UNDER-reports
holes on short series and is sound on the thousands of bars a real audit runs over. It is stated here
rather than hidden because under-reporting a fault is the more dangerous direction.

Nothing here repairs anything. It reports, because a silent repair to price history is worse than a
known hole: the hole can be worked around, the repair cannot be seen.
"""
from __future__ import annotations

import argparse
from typing import Optional

TIMEFRAME_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440}
FROZEN_RUN_WARN = 5          # a run of this many identical bars is worth a look
EXTREME_RANGE_MULTIPLE = 20   # a bar this many times the median range is flagged, never removed


def candle_faults(frame) -> dict:
    """Candles that cannot exist. Each is counted AND sampled, because a count alone is not actionable."""
    import pandas as pd

    out = {"impossible_high_low": [], "open_outside_range": [], "close_outside_range": [],
           "non_positive": [], "missing_values": []}
    needed = ["open", "high", "low", "close"]
    if any(col not in frame.columns for col in needed):
        return {"available": False, "reason": f"frame is missing one of {needed}"}

    for position, row in frame.iterrows():
        stamp = str(row.get("datetime"))
        values = [row[col] for col in needed]
        if any(pd.isna(v) for v in values):
            out["missing_values"].append(stamp)
            continue
        o, h, l, c = (float(v) for v in values)
        if h < l:
            out["impossible_high_low"].append({"at": stamp, "high": h, "low": l})
        if not (l <= o <= h):
            out["open_outside_range"].append({"at": stamp, "open": o, "low": l, "high": h})
        if not (l <= c <= h):
            out["close_outside_range"].append({"at": stamp, "close": c, "low": l, "high": h})
        if min(o, h, l, c) <= 0:
            out["non_positive"].append({"at": stamp, "low": l})
        del position

    return {"available": True,
            **{key: {"count": len(rows), "examples": rows[:5]} for key, rows in out.items()},
            "total": sum(len(rows) for rows in out.values())}


def trading_slots(times, minutes: int, min_share: float = 0.5) -> set:
    """Which minute-of-week slots this instrument actually trades, LEARNED from the data.

    The first version of this hard-coded "a gap starting on a Friday is the weekend", and on XAUUSD 4h it
    reported 20,017 missing bars - 55.6 % of the history - which would have been an alarming and wrong
    headline. Gold also closes for about an hour every day, so a naive check flags a hole every single
    session.

    Rather than encode each instrument's hours (which differ by broker and change over the years), the
    trading calendar is inferred: for each slot in the week, how often does a bar appear there compared
    with how many times that slot came round? Slots present on at least `min_share` of their occurrences
    are the instrument's real session. A bar absent from one of THOSE is a genuine hole; a bar absent from
    a slot the instrument never trades is the market being shut.
    """
    import pandas as pd

    slot = (times.dt.dayofweek * 1440 + times.dt.hour * 60 + times.dt.minute) // minutes
    present = slot.value_counts()
    weeks = max(1, int((times.max() - times.min()) / pd.Timedelta(weeks=1)))
    return {int(k) for k, count in present.items() if count >= min_share * weeks}


def gap_report(frame, timeframe: str) -> dict:
    """Missing bars, counted only against slots the instrument actually trades.

    Buckets: `expected` (one bar apart), `closed` (the gap spans only slots this instrument never trades,
    so the market was shut), `missing` (a whole number of bars and at least one of them in a live slot),
    and `odd` (not a whole multiple of the bar size, meaning the timestamps are off the claimed grid).
    """
    import pandas as pd

    minutes = TIMEFRAME_MINUTES.get(timeframe)
    if minutes is None:
        return {"available": False, "reason": f"unknown timeframe {timeframe!r}"}
    times = pd.to_datetime(frame["datetime"], utc=True).sort_values().reset_index(drop=True)
    if len(times) < 3:
        return {"available": False, "reason": "fewer than three bars"}

    live = trading_slots(times, minutes)
    step = pd.Timedelta(minutes=minutes)
    deltas = times.diff().dropna()
    buckets = {"expected": 0, "closed": 0, "missing": 0, "odd": 0}
    missing_bars, examples, worst = 0, [], None

    for position, delta in deltas.items():
        if delta == step:
            buckets["expected"] += 1
            continue
        if delta % step != pd.Timedelta(0):
            buckets["odd"] += 1
            if len(examples) < 6:
                examples.append({"after": str(times.iloc[position - 1]),
                                 "off_grid_by": str(delta % step), "gap": str(delta)})
            continue
        # Walk the slots the gap skipped and count only the ones this instrument normally trades.
        started = times.iloc[position - 1]
        skipped = int(delta / step) - 1
        live_missing = 0
        for k in range(1, skipped + 1):
            moment = started + k * step
            slot = (moment.dayofweek * 1440 + moment.hour * 60 + moment.minute) // minutes
            if int(slot) in live:
                live_missing += 1
        if live_missing == 0:
            buckets["closed"] += 1
            continue
        buckets["missing"] += 1
        missing_bars += live_missing
        if len(examples) < 6:
            examples.append({"after": str(started), "missing_bars": live_missing,
                             "skipped_total": skipped, "gap": str(delta)})
        if worst is None or live_missing > worst["missing_bars"]:
            worst = {"after": str(started), "missing_bars": live_missing, "gap": str(delta)}

    total = sum(buckets.values())
    return {"available": True, "timeframe": timeframe, "bar_minutes": minutes,
            "live_slots_per_week": len(live), "intervals": total, "buckets": buckets,
            "missing_bars_total": missing_bars,
            "missing_pct_of_expected": round(100.0 * missing_bars / (len(times) + missing_bars), 4),
            "worst_gap": worst, "examples": examples}


def audit_bars(frame, timeframe: str, symbol: str = "") -> dict:
    """The whole check for one symbol and timeframe. Reports; never repairs."""
    import pandas as pd

    if frame is None or len(frame) == 0:
        return {"available": False, "symbol": symbol, "reason": "no bars"}

    times = pd.to_datetime(frame["datetime"], utc=True)
    duplicated = times.duplicated(keep=False)
    in_order = bool(times.is_monotonic_increasing)

    ranges = (frame["high"].astype(float) - frame["low"].astype(float))
    median_range = float(ranges.median()) if len(ranges) else 0.0
    frozen = int(((frame["high"].astype(float) == frame["low"].astype(float))).sum())
    extreme = ranges[ranges > EXTREME_RANGE_MULTIPLE * median_range] if median_range > 0 else ranges.iloc[0:0]

    # The longest run of completely identical bars, which is what a stalled feed looks like.
    longest_frozen_run = run = 0
    previous = None
    for _, row in frame.iterrows():
        key = (float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"]))
        if key == previous and key[1] == key[2]:
            run += 1
        else:
            run = 1
        previous = key
        longest_frozen_run = max(longest_frozen_run, run)

    faults = candle_faults(frame)
    gaps = gap_report(frame, timeframe)

    problems = []
    if int(duplicated.sum()):
        problems.append(f"{int(duplicated.sum())} duplicated timestamp row(s)")
    if not in_order:
        problems.append("bars are not in time order")
    if faults.get("total"):
        problems.append(f"{faults['total']} impossible candle(s)")
    if gaps.get("missing_bars_total"):
        problems.append(f"{gaps['missing_bars_total']} missing bar(s) in slots this instrument does trade")
    if gaps.get("buckets", {}).get("odd"):
        problems.append(f"{gaps['buckets']['odd']} interval(s) off the {timeframe} grid")
    if longest_frozen_run >= FROZEN_RUN_WARN:
        problems.append(f"a run of {longest_frozen_run} identical motionless bars")

    return {
        "available": True, "symbol": symbol, "timeframe": timeframe,
        "bars": len(frame), "first": str(times.min()), "last": str(times.max()),
        "duplicated_rows": int(duplicated.sum()), "in_time_order": in_order,
        "median_range": round(median_range, 6),
        "frozen_bars": frozen, "longest_frozen_run": longest_frozen_run,
        "extreme_range_bars": int(len(extreme)),
        "candle_faults": faults, "gaps": gaps,
        "problems": problems, "clean": not problems,
        "places_orders": False,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Audit bar data for gaps, duplicates and bad candles.")
    parser.add_argument("symbol", nargs="?", default="XAUUSD")
    parser.add_argument("timeframe", nargs="?", default="4h")
    parser.add_argument("--bars", type=int, default=16000)
    parser.add_argument("--all", action="store_true", help="both symbols across 15m, 1h and 4h")
    args = parser.parse_args(argv)

    from .mtf_data import fetch_app_bars

    jobs = ([(s, t) for s in ("XAUUSD", "BTCUSD") for t in ("15m", "1h", "4h")]
            if args.all else [(args.symbol, args.timeframe)])

    worst = 0
    for symbol, timeframe in jobs:
        try:
            frame = fetch_app_bars(symbol, timeframe, args.bars).sort_values("datetime").reset_index(drop=True)
        except Exception as exc:
            print(f"{symbol} {timeframe}: could not load ({type(exc).__name__}: {exc})")
            continue
        out = audit_bars(frame, timeframe, symbol)
        mark = "clean" if out["clean"] else "PROBLEMS"
        print(f"{symbol} {timeframe}: {out['bars']:,} bars  {out['first'][:16]} -> {out['last'][:16]}  [{mark}]")
        g = out["gaps"]
        if g.get("available"):
            b = g["buckets"]
            print(f"    intervals: {b['expected']:,} expected, {b['closed']:,} market shut, "
                  f"{b['missing']:,} with holes, {b['odd']:,} off-grid"
                  f"   missing bars {g['missing_bars_total']:,} ({g['missing_pct_of_expected']}%)")
            if g.get("worst_gap"):
                print(f"    worst hole: {g['worst_gap']['missing_bars']} bars after {g['worst_gap']['after'][:16]}")
        print(f"    duplicates {out['duplicated_rows']}   in order {out['in_time_order']}   "
              f"impossible candles {out['candle_faults'].get('total')}   "
              f"frozen {out['frozen_bars']} (longest run {out['longest_frozen_run']})   "
              f"extreme-range bars {out['extreme_range_bars']}")
        for problem in out["problems"]:
            print(f"      - {problem}")
        worst = max(worst, len(out["problems"]))
    return 0 if worst == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
