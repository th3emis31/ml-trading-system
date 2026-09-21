"""One row per day for the WHOLE system: what it learned, researched, signalled, traded and earned.

Every other view in this system is a slice - the learning page, the strategy lab, the demo pages, the
doctor. None of them answers "what did the system actually do on Thursday". This assembles one record
per calendar day from every dated source, so a run of empty days or a day where everything happened is
visible at a glance.

WHAT IT DOES NOT DO. It reads; it never writes to any source and never trades. It reports zero as zero -
a day with no trades shows no trades, and is not smoothed, interpolated or left out to make the strip
look busier. Days before a source existed are shown as absent rather than as zero, because "we were not
recording yet" and "nothing happened" are different facts.

    python -m src.system_calendar [--days 60] [--json]
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from .runtime_paths import smartentry_data_dir

ROOT = Path(__file__).resolve().parents[1]
MEMORY_DIR = ROOT / ".claude" / "memory"
LOG_STAMP = re.compile(r"^(20\d\d-\d\d-\d\d) \d\d:\d\d:\d\d\s+(\S+)")
DATE = re.compile(r"(20\d\d-\d\d-\d\d)")

# Which cycle log belongs to which strategy, so a day can say which one acted.
CYCLE_LOGS = {
    "gold_session_pullback": "demo_session_pullback.log",
    "volatility_breakout": "demo_volatility_breakout.log",
    "daily_plan": "demo_plan_trader.log",
}
# A cycle decision that means an order was actually placed, as opposed to a decision not to.
ACTED = {"opened", "order_sent", "trade_closed", "partial"}


def _load_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _blank_day() -> dict:
    return {
        "learning_runs": 0, "promotions": 0, "learning_sources": set(),
        "research_results": 0, "candidates_evaluated": 0,
        "signals": 0, "signal_wins": 0, "signal_losses": 0, "signal_unresolved": 0,
        "cycles": 0, "orders_placed": 0, "trades_closed": 0,
        "net_r": 0.0, "net_money": 0.0,
        "strategies_active": set(), "health": None, "report": False,
    }


def learning_by_day(path: Optional[Path] = None) -> dict:
    rows = _load_json(Path(path or smartentry_data_dir() / "learning_decisions.json"), []) or []
    out: dict[str, dict] = defaultdict(lambda: {"runs": 0, "promotions": 0, "sources": set()})
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        day = str(row.get("trained_at") or "")[:10]
        if not day:
            continue
        out[day]["runs"] += 1
        if row.get("rf_promoted") or row.get("lstm_promoted"):
            out[day]["promotions"] += 1
        if row.get("data_source"):
            out[day]["sources"].add(str(row["data_source"]))
    return out


def research_by_day(memory_dir: Optional[Path] = None, registry: Optional[Path] = None) -> dict:
    """Recorded results (BASELINE rows) and candidates scored, per day."""
    out: dict[str, dict] = defaultdict(lambda: {"results": 0, "candidates": 0})
    baseline = Path(memory_dir or MEMORY_DIR) / "BASELINE.md"
    for line in (baseline.read_text(encoding="utf-8", errors="replace").splitlines()
                 if baseline.exists() else []):
        if not line.startswith("| 20"):
            continue
        found = DATE.search(line[:20])
        if found:
            out[found.group(1)]["results"] += 1
    data = _load_json(Path(registry or smartentry_data_dir() / "strategy_lab" / "registry.json"), {}) or {}
    for record in (data.get("candidates") or {}).values():
        day = str((record or {}).get("evaluated_at") or "")[:10]
        if day:
            out[day]["candidates"] += 1
    return out


def signals_by_day(path: Optional[Path] = None) -> dict:
    rows = _load_json(Path(path or smartentry_data_dir() / "signals.json"), []) or []
    rows = rows if isinstance(rows, list) else (rows.get("signals") or [])
    out: dict[str, dict] = defaultdict(lambda: {"count": 0, "wins": 0, "losses": 0, "unresolved": 0})
    for row in rows:
        if not isinstance(row, dict):
            continue
        day = str(row.get("generated_at") or "")[:10]
        if not day:
            continue
        out[day]["count"] += 1
        outcome = str(row.get("outcome") or "").upper()
        if outcome in ("WIN", "TP1", "TP2", "TP3"):
            out[day]["wins"] += 1
        elif outcome in ("LOSS", "SL"):
            out[day]["losses"] += 1
        else:
            out[day]["unresolved"] += 1
    return out


def cycles_by_day(paper_dir: Optional[Path] = None) -> dict:
    """What each demo strategy decided, per day, straight from its own cycle log."""
    directory = Path(paper_dir or smartentry_data_dir() / "paper_trading")
    out: dict[str, dict] = defaultdict(lambda: {"cycles": 0, "orders": 0, "active": set()})
    for strategy, name in CYCLE_LOGS.items():
        path = directory / name
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            found = LOG_STAMP.match(line)
            if not found:
                continue
            day, event = found.group(1), found.group(2).rstrip(":")
            out[day]["cycles"] += 1
            out[day]["active"].add(strategy)
            if event in ACTED:
                out[day]["orders"] += 1
    return out


def closed_trades_by_day(data_dir: Optional[Path] = None) -> dict:
    """Closed trades with their R and money, from the forward tests and the demo journals."""
    data = Path(data_dir or smartentry_data_dir())
    out: dict[str, dict] = defaultdict(lambda: {"closed": 0, "net_r": 0.0, "net_money": 0.0})

    for path in sorted((data / "strategy_lab").glob("*forward*.json")):
        state = _load_json(path, {}) or {}
        for trade in state.get("closed_trades") or []:
            day = str(trade.get("exit_time") or trade.get("closed_at") or "")[:10]
            if not day:
                continue
            out[day]["closed"] += 1
            for field in ("net_r", "r_result", "r_multiple"):
                if trade.get(field) is not None:
                    out[day]["net_r"] += float(trade[field])
                    break

    memory_dir = data / "trade_memory"
    for path in sorted(memory_dir.glob("*.jsonl")) if memory_dir.exists() else []:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            row = _load_json_line(line)
            if not row or row.get("event") == "opened":
                continue
            day = str(row.get("closed_at") or row.get("exit_time") or "")[:10]
            if not day:
                continue
            out[day]["closed"] += 1
            if row.get("r_result") is not None:
                out[day]["net_r"] += float(row["r_result"])
            if row.get("net_money") is not None:
                out[day]["net_money"] += float(row["net_money"])
    return out


def _load_json_line(line: str):
    try:
        return json.loads(line)
    except ValueError:
        return None


def health_by_day(data_dir: Optional[Path] = None) -> dict:
    """The doctor's own verdict per day, and whether a daily report was written."""
    data = Path(data_dir or smartentry_data_dir())
    out: dict[str, dict] = defaultdict(lambda: {"health": None, "report": False})
    history = _load_json(data / "system_health" / "doctor_history.json", []) or []
    for entry in history if isinstance(history, list) else []:
        day = str((entry or {}).get("generated_at") or "")[:10]
        if day:
            out[day]["health"] = entry.get("overall")
    for path in (data / "daily_reports").glob("20*.json"):
        out[path.stem[:10]]["report"] = True
    return out


def build_calendar(days: int = 60, today: Optional[date] = None) -> dict:
    """One row per calendar day, newest last. Absent sources are absent, not zero."""
    today = today or datetime.now(timezone.utc).date()
    learning, research = learning_by_day(), research_by_day()
    signals, cycles = signals_by_day(), cycles_by_day()
    trades, health = closed_trades_by_day(), health_by_day()

    known = set(learning) | set(research) | set(signals) | set(cycles) | set(trades) | set(health)
    first_seen = min(known) if known else str(today)

    rows = []
    for offset in range(days - 1, -1, -1):
        day = str(today - timedelta(days=offset))
        if day < first_seen:
            rows.append({"date": day, "recording": False})
            continue
        entry = _blank_day()
        entry.update({
            "learning_runs": learning.get(day, {}).get("runs", 0),
            "promotions": learning.get(day, {}).get("promotions", 0),
            "learning_sources": sorted(learning.get(day, {}).get("sources", set())),
            "research_results": research.get(day, {}).get("results", 0),
            "candidates_evaluated": research.get(day, {}).get("candidates", 0),
            "signals": signals.get(day, {}).get("count", 0),
            "signal_wins": signals.get(day, {}).get("wins", 0),
            "signal_losses": signals.get(day, {}).get("losses", 0),
            "signal_unresolved": signals.get(day, {}).get("unresolved", 0),
            "cycles": cycles.get(day, {}).get("cycles", 0),
            "orders_placed": cycles.get(day, {}).get("orders", 0),
            "strategies_active": sorted(cycles.get(day, {}).get("active", set())),
            "trades_closed": trades.get(day, {}).get("closed", 0),
            "net_r": round(trades.get(day, {}).get("net_r", 0.0), 3),
            "net_money": round(trades.get(day, {}).get("net_money", 0.0), 2),
            "health": health.get(day, {}).get("health"),
            "report": health.get(day, {}).get("report", False),
        })
        entry["date"] = day
        entry["recording"] = True
        entry["activity"] = day_activity(entry)
        rows.append(entry)

    return {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "days": days, "first_recorded": first_seen, "rows": rows,
            "totals": totals(rows), "legend": LEGEND, "places_orders": False,
            "note": "Read-only. Zero is reported as zero; days before a source existed are marked not recording."}


def day_activity(row: dict) -> int:
    """0-4, the heat of a day. Deliberately counts DISTINCT kinds of work, not volume.

    A day that evaluated 4,000 candidates and did nothing else is not a busier day for this system than
    one that learned, signalled, traded and closed a trade - and a volume-weighted score would say it
    was, because the search produces thousands of rows an hour while a closed trade is rare.
    """
    kinds = 0
    if row.get("learning_runs"):
        kinds += 1
    if row.get("research_results") or row.get("candidates_evaluated"):
        kinds += 1
    if row.get("signals") or row.get("cycles"):
        kinds += 1
    if row.get("orders_placed") or row.get("trades_closed"):
        kinds += 1
    return kinds


LEGEND = {
    "activity": "0-4: distinct kinds of work that day (learning, research, signalling/cycling, ordering)",
    "promotions": "the learning gate replacing a live model - the loop closing",
    "net_r": "closed forward and demo trades only; backtests are never counted here",
    "recording": "false means the system was not yet recording that day, which is not the same as a quiet day",
}


def totals(rows: list[dict]) -> dict:
    live = [r for r in rows if r.get("recording")]
    return {
        "days_recording": len(live),
        "days_with_learning": sum(1 for r in live if r["learning_runs"]),
        "promotions": sum(r["promotions"] for r in live),
        "research_results": sum(r["research_results"] for r in live),
        "candidates_evaluated": sum(r["candidates_evaluated"] for r in live),
        "signals": sum(r["signals"] for r in live),
        "signal_wins": sum(r["signal_wins"] for r in live),
        "signal_losses": sum(r["signal_losses"] for r in live),
        "cycles": sum(r["cycles"] for r in live),
        "orders_placed": sum(r["orders_placed"] for r in live),
        "trades_closed": sum(r["trades_closed"] for r in live),
        "net_r": round(sum(r["net_r"] for r in live), 3),
        "net_money": round(sum(r["net_money"] for r in live), 2),
        "days_with_a_closed_trade": sum(1 for r in live if r["trades_closed"]),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="One row per day for the whole system (read-only).")
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    calendar = build_calendar(args.days)
    if args.json:
        print(json.dumps(calendar, indent=1, default=str))
        return 0

    blocks = " .:+#"
    print(f"system calendar, {calendar['days']} days to {calendar['rows'][-1]['date']}   "
          f"(recording since {calendar['first_recorded']})\n")
    print(f"  {'date':11s} {'heat':4s} {'learn':>5s} {'promo':>5s} {'results':>7s} {'cands':>6s} "
          f"{'sigs':>4s} {'W/L':>7s} {'cycles':>6s} {'orders':>6s} {'closed':>6s} {'netR':>7s} {'health':>8s}")
    for row in calendar["rows"]:
        if not row.get("recording"):
            print(f"  {row['date']:11s}  -   (not recording yet)")
            continue
        heat = blocks[min(4, row["activity"])]
        wl = f"{row['signal_wins']}/{row['signal_losses']}" if row["signals"] else "-"
        print(f"  {row['date']:11s}  {heat}   {row['learning_runs']:5d} {row['promotions']:5d} "
              f"{row['research_results']:7d} {row['candidates_evaluated']:6d} {row['signals']:4d} {wl:>7s} "
              f"{row['cycles']:6d} {row['orders_placed']:6d} {row['trades_closed']:6d} "
              f"{row['net_r']:+7.2f} {str(row['health'] or '-'):>8s}")
    t = calendar["totals"]
    print(f"\n  over {t['days_recording']} recording days: {t['promotions']} promotions, "
          f"{t['research_results']} recorded results, {t['candidates_evaluated']} candidates scored,")
    print(f"  {t['signals']} signals ({t['signal_wins']}W/{t['signal_losses']}L), {t['cycles']} cycles, "
          f"{t['orders_placed']} orders placed, {t['trades_closed']} trades closed,")
    print(f"  {t['net_r']:+.2f} R and {t['net_money']:+.2f} money on closed trades, "
          f"on {t['days_with_a_closed_trade']} day(s) with a closed trade.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
