"""The system's memory of its own results: every closed forward trade, scored honestly.

Why this exists. On 19 September 2026 an audit counted the forward evidence this system could learn
from and found **two closed trades**, with nothing in ``daily_learning.py``, ``train.py``,
``features.py`` or ``signal_engine.py`` reading any of them. The system retrains every day at 05:30
and tests about a thousand strategy candidates every hour, so it never stops learning — but it was
re-deriving everything from the same candles each day and discarding the one kind of evidence that
cannot be overfitted: what happened when it actually traded.

A backtest can be tuned until it looks good. A forward trade cannot, because it happened after the
decision was made. That is the whole point of this file.

**It only tracks what this system runs itself** — the gold session pullback (magic 440502), the
volatility trend breakout (440603), the paper 4H model, and research candidates awaiting forward
confirmation. Other experts on the terminal are deliberately not tracked, journalled or scored
here; that is the owner's standing rule and it also keeps the record meaningful, because a mixed
scoreboard cannot say what this system earned.

**It reads. It never trades**, never opens an MT5 connection of its own, and never changes a
strategy's configuration. Whether a candidate graduates is a decision for the owner, informed by
what this records.

    python -m src.forward_evidence report [--json]
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .runtime_paths import smartentry_data_dir
# The project's one tolerant JSON reader (UTF-8, BOM or the ANSI MetaTrader writes);
# src/ea_monitor.py imports the same one rather than adding a second.
from .system_doctor import _read_json as _read_json_or_none

# A verdict needs this much evidence. Fixed here so no candidate is judged on a lucky handful.
MIN_TRADES = 30
CONFIRM_PROFIT_FACTOR = 1.2
REFUTE_PROFIT_FACTOR = 0.8
MAX_DRAWDOWN_R = 12.0


def _trade_rows(container) -> list:
    """The closed trades out of a ``trades`` block, which is a dict keyed by trade id."""
    if isinstance(container, dict):
        rows = list(container.values())
    elif isinstance(container, list):
        rows = list(container)
    else:
        return []
    return [r for r in rows if isinstance(r, dict) and str(r.get("status")) == "closed"]


def _r_values(rows: list) -> list:
    """Realised R per trade. Both demo strategies write ``r_result`` when a trade closes."""
    out = []
    for row in rows:
        value = row.get("r_result")
        if value is None:
            value = row.get("r_multiple")
        try:
            if value is not None and math.isfinite(float(value)):
                out.append(float(value))
        except (TypeError, ValueError):
            continue
    return out


def _mean_interval(values: list) -> tuple:
    """95 % interval on the mean R, from the t approximation. None below two trades."""
    n = len(values)
    if n < 2:
        return (None, None)
    mean = sum(values) / n
    variance = sum((v - mean) ** 2 for v in values) / (n - 1)
    if variance <= 0:
        return (round(mean, 4), round(mean, 4))
    half = 1.96 * math.sqrt(variance / n)
    return (round(mean - half, 4), round(mean + half, 4))


def score(values: list) -> dict:
    """Running forward statistics for one source. Everything here is descriptive, nothing is fitted."""
    n = len(values)
    wins = [v for v in values if v > 0]
    losses = [v for v in values if v < 0]
    gross_win, gross_loss = sum(wins), -sum(losses)
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else (None if gross_win == 0 else float("inf"))
    equity, peak, worst = 0.0, 0.0, 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        worst = max(worst, peak - equity)
    low, high = _mean_interval(values)
    return {"trades": n,
            "wins": len(wins),
            "win_rate_pct": round(100.0 * len(wins) / n, 2) if n else None,
            "total_r": round(sum(values), 4) if n else 0.0,
            "expectancy_r": round(sum(values) / n, 4) if n else None,
            "expectancy_95_low": low, "expectancy_95_high": high,
            "profit_factor": (round(profit_factor, 3) if isinstance(profit_factor, float)
                              and math.isfinite(profit_factor) else profit_factor),
            "max_drawdown_r": round(worst, 4) if n else 0.0}


def verdict(stats: dict) -> dict:
    """collecting / confirmed / refuted, against the criteria fixed at the top of this file."""
    n = stats["trades"]
    if n < MIN_TRADES:
        return {"status": "collecting", "trades": n, "needed": MIN_TRADES - n,
                "why": f"{n} closed forward trades; a verdict needs {MIN_TRADES}"}
    pf = stats["profit_factor"]
    low = stats["expectancy_95_low"]
    drawdown = stats["max_drawdown_r"]
    if pf is not None and pf != float("inf") and pf < REFUTE_PROFIT_FACTOR:
        return {"status": "refuted", "why": f"profit factor {pf} below {REFUTE_PROFIT_FACTOR} over {n} trades"}
    if stats["expectancy_95_high"] is not None and stats["expectancy_95_high"] < 0:
        return {"status": "refuted", "why": f"expectancy is negative with 95 % confidence over {n} trades"}
    if drawdown > MAX_DRAWDOWN_R:
        return {"status": "refuted", "why": f"forward drawdown {drawdown} R exceeds {MAX_DRAWDOWN_R} R"}
    if (pf is not None and pf >= CONFIRM_PROFIT_FACTOR and low is not None and low > 0):
        return {"status": "confirmed",
                "why": f"profit factor {pf} and expectancy positive with 95 % confidence over {n} trades"}
    return {"status": "inconclusive",
            "why": f"{n} trades, profit factor {pf}, expectancy interval [{low}, {stats['expectancy_95_high']}] "
                   f"spans zero"}


# Every source this system runs itself. `label` is what the owner sees; `magic` is None for paper.
SOURCES = (
    {"key": "volatility_trend_breakout", "label": "Volatility Trend Breakout", "magic": 440603,
     "state": "paper_trading/demo_volatility_breakout_state.json", "kind": "demo",
     "note": "the only strategy sending orders; XAUUSD and BTCUSD 4H"},
    {"key": "gold_session_pullback", "label": "Gold session pullback", "magic": 440502,
     "state": "paper_trading/demo_session_pullback_state.json", "kind": "demo",
     "note": "dry_run: it decides and places nothing, so it cannot accumulate evidence"},
)

# Research candidates that have earned a forward test but NOT a deployment. Each names the row in
# .claude/memory/BASELINE.md that justifies watching it, so the claim is always traceable.
WATCHLIST = (
    {"key": "aurum_mechanism_btc_4h",
     "label": "Trendline break, BTCUSD 4H, ATR exits 4.45/8.90, SMA600 filter",
     "evidence": "2026-09-19 BASELINE: positive on all three splits (+506 % / +22.9 % / +26.1 %), "
                 "73 holdout trades, PF 1.474, deflated Sharpe 0.7504 against the 0.95 bar",
     "why_watch": "the closest candidate of about 400 tested for that mechanism; 0.75 is not 0.95, "
                  "and only forward trades can close the gap without lowering the bar"},
    {"key": "morning_star_xauusd_4h",
     "label": "Morning star, XAUUSD 4H, 1 R target",
     "evidence": "2026-09-19 BASELINE: positive on all three splits, 53 holdout trades, PF 1.386, "
                 "60.4 % win rate, beats its own inverse (inverse PF 0.555)",
     "why_watch": "the only candlestick pattern of 96 tested with the right shape on gold; its "
                  "deflated Sharpe is killed by the dispersion across the 24 variants tried, which "
                  "a single pre-declared forward test would not incur"},
)


def collect(data_dir: Optional[Path] = None) -> dict:
    """Read every source and candidate. Missing files mean 'no evidence yet', never an error."""
    root = Path(data_dir) if data_dir is not None else smartentry_data_dir()
    report = {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
              "criteria": {"min_trades": MIN_TRADES, "confirm_profit_factor": CONFIRM_PROFIT_FACTOR,
                           "refute_profit_factor": REFUTE_PROFIT_FACTOR,
                           "max_drawdown_r": MAX_DRAWDOWN_R},
              "tracks_only": "strategies this system runs itself; other experts are deliberately excluded",
              "sources": [], "watchlist": [], "totals": {}}

    total = 0
    for source in SOURCES:
        state = _read_json_or_none(root / source["state"]) or {}
        rows = _trade_rows(state.get("trades"))
        values = _r_values(rows)
        stats = score(values)
        total += stats["trades"]
        report["sources"].append({**{k: source[k] for k in ("key", "label", "magic", "kind", "note")},
                                  "state_file": source["state"],
                                  "state_present": bool(state),
                                  "open_trades": len([r for r in (_trade_rows_any(state.get("trades")))
                                                      if str(r.get("status")) == "open"]),
                                  "stats": stats, "verdict": verdict(stats)})

    for candidate in WATCHLIST:
        report["watchlist"].append({**candidate, "stats": score([]), "verdict": verdict(score([])),
                                    "forward_test_running": False,
                                    "note": "no forward test is running for this candidate yet; "
                                            "starting one is the owner's decision"})

    report["totals"] = {"closed_forward_trades": total,
                        "sources_with_any_evidence": sum(1 for s in report["sources"] if s["stats"]["trades"]),
                        "candidates_awaiting_forward_test": len(report["watchlist"])}
    return report


def _trade_rows_any(container) -> list:
    """Every trade row regardless of status, so open positions can be counted separately."""
    if isinstance(container, dict):
        return [r for r in container.values() if isinstance(r, dict)]
    if isinstance(container, list):
        return [r for r in container if isinstance(r, dict)]
    return []


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="What this system has actually learned from its own trades")
    parser.add_argument("command", choices=["report"])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    report = collect()
    if args.json:
        print(json.dumps(report, indent=1, default=str))
        return 0

    print(f"Forward evidence as at {report['generated_at']}")
    print(f"A verdict needs {MIN_TRADES} closed forward trades. "
          f"Confirm at PF >= {CONFIRM_PROFIT_FACTOR} with expectancy positive at 95 %; "
          f"refute below PF {REFUTE_PROFIT_FACTOR}.\n")
    for source in report["sources"]:
        stats, v = source["stats"], source["verdict"]
        magic = f"magic {source['magic']}" if source["magic"] else "paper"
        print(f"  {source['label']} ({magic})")
        print(f"     closed {stats['trades']:>3}   open {source['open_trades']}   "
              f"total {stats['total_r']:+.2f} R   expectancy "
              f"{stats['expectancy_r'] if stats['expectancy_r'] is not None else 'n/a'}   "
              f"PF {stats['profit_factor'] if stats['profit_factor'] is not None else 'n/a'}")
        print(f"     {v['status'].upper()}: {v['why']}")
        print(f"     {source['note']}")
    print("\n  Candidates that earned a forward test but not a deployment:")
    for candidate in report["watchlist"]:
        print(f"     {candidate['label']}")
        print(f"        evidence : {candidate['evidence']}")
        print(f"        watching : {candidate['why_watch']}")
        print(f"        forward  : {candidate['verdict']['why']}")
    totals = report["totals"]
    print(f"\n  TOTAL closed forward trades this system can learn from: {totals['closed_forward_trades']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
