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

# ---------------------------------------------------------------------------------
# The ladder. Added 19 September 2026 after the owner pointed out that a single
# pass/fail bar was blocking learning, confidence and ranking rather than only
# governing money.
#
# The 0.95 deflated Sharpe bar has NOT moved and is not negotiable. It applies at
# the last rung only, which is the one rung where real money is at stake. Every
# other rung has its own much lower requirement, so a candidate is never stuck with
# nothing to do: it keeps trading in shadow, keeps accumulating evidence, and keeps
# a confidence number that moves.
#
# Promotion is on measured evidence, never on a hunch, and demotion works the same
# way, so a rung is a description of what is known rather than a reward.
TIERS = (
    {"tier": 0, "name": "research", "risk": "none",
     "needs": "a backtest of any result, and a pre-declared rule",
     "learns": "from every new bar"},
    {"tier": 1, "name": "shadow", "risk": "none - places nothing",
     "needs": "positive on all three backtest splits, or beats its own inverse",
     "learns": "from every new bar and every simulated trade"},
    {"tier": 2, "name": "demo, minimum size", "risk": "demo only, smallest allowed size",
     "needs": "5 closed forward trades and a forward expectancy above zero",
     "learns": "from real fills, real spread and real slippage"},
    {"tier": 3, "name": "demo, full size", "risk": "demo only",
     "needs": f"{MIN_TRADES} closed forward trades, profit factor >= {CONFIRM_PROFIT_FACTOR}, "
              f"expectancy positive with 95 % confidence",
     "learns": "as above, at the size it would really trade"},
    {"tier": 4, "name": "real money", "risk": "REAL",
     "needs": "everything above AND deflated Sharpe >= 0.95 on a holdout that selection never saw, "
              "AND the owner's explicit decision",
     "learns": "as above"},
)
TIER2_MIN_TRADES = 5

# A backtest on a candidate that was SELECTED out of many is optimistic, so it is
# used as a prior and shrunk hard toward break-even. This factor is the discount:
# a backtest showing +0.40 R enters as +0.10 R, and it is worth the same as this
# many forward trades, no more.
BACKTEST_DISCOUNT = 0.25
BACKTEST_PRIOR_WEIGHT = 8.0


def _trade_rows(container) -> list:
    """The closed trades out of a ``trades`` block, which is a dict keyed by trade id."""
    if isinstance(container, dict):
        rows = list(container.values())
    elif isinstance(container, list):
        rows = list(container)
    else:
        return []
    return [r for r in rows if isinstance(r, dict) and str(r.get("status")) == "closed"]


# The realised-R field is spelled differently by the two producers: the demo strategies write
# ``r_result`` on close, src/crt_forward.py writes ``net_r`` per closed trade. Both are R multiples.
R_FIELDS = ("r_result", "net_r", "r_multiple")


def _r_values(rows: list) -> list:
    """Realised R per trade, whichever of the known field names carries it."""
    out = []
    for row in rows:
        value = next((row[name] for name in R_FIELDS if row.get(name) is not None), None)
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


def confidence(stats: dict, backtest_expectancy_r: Optional[float] = None) -> dict:
    """A confidence number that moves from the FIRST forward trade, never a locked door.

    The backtest enters as a prior, discounted hard because a candidate selected out of many is
    flattered by selection, and weighted as though it were only ``BACKTEST_PRIOR_WEIGHT`` trades.
    Each closed forward trade then pulls the estimate toward what actually happened, so by 30 trades
    the backtest barely matters and the forward record decides.

    This is a shrinkage estimate, not a promise. ``interval`` is what honesty looks like here: it
    starts wide and narrows, and while it spans zero the right reading is "not yet known".
    """
    n = stats["trades"]
    forward = stats["expectancy_r"]
    prior = (backtest_expectancy_r or 0.0) * BACKTEST_DISCOUNT
    if n == 0:
        estimate, weight = prior, 0.0
    else:
        weight = n / (n + BACKTEST_PRIOR_WEIGHT)
        estimate = weight * forward + (1 - weight) * prior
    low, high = stats["expectancy_95_low"], stats["expectancy_95_high"]
    return {"expectancy_r_estimate": round(estimate, 4),
            "forward_weight": round(weight, 3),
            "prior_used_r": round(prior, 4),
            "interval": [low, high],
            "interval_spans_zero": None if low is None else bool(low <= 0 <= high),
            "evidence_trades": n,
            "reading": _confidence_reading(estimate, low, high, n)}


def _confidence_reading(estimate: float, low, high, n: int) -> str:
    if n == 0:
        return (f"no forward trades yet; the discounted backtest prior alone puts it at "
                f"{estimate:+.3f} R per trade, which is a starting guess and nothing more")
    if low is None:
        return f"{n} forward trade so far, estimate {estimate:+.3f} R; far too little to read"
    if low > 0:
        return f"{estimate:+.3f} R per trade and the 95 % interval is entirely above zero over {n} trades"
    if high < 0:
        return f"{estimate:+.3f} R per trade and the 95 % interval is entirely below zero over {n} trades"
    return (f"{estimate:+.3f} R per trade over {n} trades; the interval still spans zero, so this is "
            f"the current best estimate rather than a finding")


def tier(stats: dict, splits_positive: Optional[bool] = None, beats_inverse: Optional[bool] = None,
         deflated_sharpe: Optional[float] = None, owner_approved: bool = False) -> dict:
    """Which rung the evidence supports. Nothing is blocked from the rung below where it sits.

    Deliberately generous at the bottom and strict only at the top: the point is that a candidate
    always has somewhere to keep learning, while real money still waits for the 0.95 bar.
    """
    n = stats["trades"]
    pf = stats["profit_factor"]
    expectancy = stats["expectancy_r"]
    low = stats["expectancy_95_low"]

    reached = 0
    if splits_positive or beats_inverse:
        reached = 1
    if n >= TIER2_MIN_TRADES and expectancy is not None and expectancy > 0:
        reached = 2
    if (n >= MIN_TRADES and pf is not None and pf != float("inf") and pf >= CONFIRM_PROFIT_FACTOR
            and low is not None and low > 0 and stats["max_drawdown_r"] <= MAX_DRAWDOWN_R):
        reached = 3
    if reached == 3 and deflated_sharpe is not None and deflated_sharpe >= 0.95 and owner_approved:
        reached = 4

    spec = TIERS[reached]
    nxt = TIERS[reached + 1] if reached + 1 < len(TIERS) else None
    return {"tier": reached, "name": spec["name"], "risk": spec["risk"],
            "learning": spec["learns"],
            "next_tier": (nxt["name"] if nxt else None),
            "to_advance": (nxt["needs"] if nxt else "already at the top rung"),
            "blocked_from_learning": False}


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
     "state": "paper_trading/demo_volatility_breakout_state.json",
     "config": "paper_trading/demo_volatility_breakout.json", "kind": "demo",
     "backtest_expectancy_r": 0.139,     # BASELINE 18 Sep: BTCUSD out of sample, 248 trades
     "splits_positive": None, "beats_inverse": True, "deflated_sharpe": None,
     "note": "the only strategy sending orders; XAUUSD and BTCUSD 4H"},
    {"key": "gold_session_pullback", "label": "Gold session pullback", "magic": 440502,
     "state": "paper_trading/demo_session_pullback_state.json",
     "config": "paper_trading/demo_session_pullback.json", "kind": "demo",
     "note": "dry_run: it decides and places nothing, so it cannot accumulate evidence"},
)

# Research candidates that have earned a forward test but NOT a deployment. Each names the row in
# .claude/memory/BASELINE.md that justifies watching it, so the claim is always traceable.
WATCHLIST = (
    {"key": "aurum_mechanism_btc_4h",
     "state": "strategy_lab/forward_trendline_break_btc_4h.json",
     # Derived, not invented: at a 2 R payoff a profit factor of 1.474 implies a win rate of
     # 1.474/3.474 = 0.424, so expectancy = 0.424 x 2 - 0.576 = +0.273 R. It enters the confidence
     # estimate discounted to a quarter of that, because the candidate was selected out of many.
     "backtest_expectancy_r": 0.273,
     "splits_positive": True, "beats_inverse": True, "deflated_sharpe": 0.7504,
     "label": "Trendline break, BTCUSD 4H, ATR exits 4.45/8.90, SMA600 filter",
     "evidence": "2026-09-19 BASELINE: positive on all three splits (+506 % / +22.9 % / +26.1 %), "
                 "73 holdout trades, PF 1.474, deflated Sharpe 0.7504 against the 0.95 bar",
     "why_watch": "the closest candidate of about 400 tested for that mechanism; 0.75 is not 0.95, "
                  "and only forward trades can close the gap without lowering the bar"},
    {"key": "morning_star_xauusd_4h",
     "state": "strategy_lab/forward_morning_star_xau_4h.json",
     # At a 1 R payoff a profit factor of 1.386 implies a win rate of 1.386/2.386 = 0.581, so
     # expectancy = 2 x 0.581 - 1 = +0.162 R. Same quarter-weight discount applies.
     "backtest_expectancy_r": 0.162,
     "splits_positive": True, "beats_inverse": True, "deflated_sharpe": 0.0036,
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
        # dry_run / sending_orders live in the config file beside the state file.
        config = _read_json_or_none(root / source["config"]) or {}
        rows = _trade_rows(state.get("trades"))
        values = _r_values(rows)
        stats = score(values)
        total += stats["trades"]
        running = _running_state({**state, **config})
        report["sources"].append({**{k: source[k] for k in ("key", "label", "magic", "kind", "note")},
                                  "currently": running,
                                  "confidence": confidence(stats, source.get("backtest_expectancy_r")),
                                  "tier": tier(stats, splits_positive=source.get("splits_positive"),
                                               beats_inverse=source.get("beats_inverse"),
                                               deflated_sharpe=source.get("deflated_sharpe")),
                                  "state_file": source["state"],
                                  "state_present": bool(state),
                                  "open_trades": len([r for r in (_trade_rows_any(state.get("trades")))
                                                      if str(r.get("status")) == "open"]),
                                  "stats": stats, "verdict": verdict(stats)})

    for candidate in WATCHLIST:
        # src/crt_forward.py runs these hourly as PAPER forward tests and places nothing. Its state
        # file carries closed_trades with a net_r each, which is the same currency this scores in.
        state = _read_json_or_none(root / candidate["state"]) or {}
        closed = state.get("closed_trades") or []
        values = _r_values(closed)
        stats = score(values)
        total += stats["trades"]
        report["watchlist"].append({
            **candidate, "stats": stats, "verdict": verdict(stats),
            "confidence": confidence(stats, candidate.get("backtest_expectancy_r")),
            "tier": tier(stats, splits_positive=candidate.get("splits_positive"),
                         beats_inverse=candidate.get("beats_inverse"),
                         deflated_sharpe=candidate.get("deflated_sharpe")),
            "forward_test_running": bool(state),
            "places_orders": bool(state.get("places_orders")),
            "collecting_since": state.get("start_at"),
            "open_position": state.get("open_position"),
            "note": ("a paper forward test is running hourly and places nothing; only bars after "
                     f"{state.get('start_at')} count") if state else
                    "no forward test is running for this candidate yet"})

    report["totals"] = {"closed_forward_trades": total,
                        "sources_with_any_evidence": sum(1 for s in report["sources"] if s["stats"]["trades"]),
                        "candidates_awaiting_forward_test": len(report["watchlist"])}
    return report


def _running_state(state: dict) -> dict:
    """What a strategy is actually doing right now, which is not the same as what its evidence supports.

    The owner may run something ahead of its evidence - the volatility breakout was switched to
    sending orders on 17 September on their decision, before it had a single closed trade. That is
    their call to make, and this reports it plainly rather than describing the strategy by its tier
    and implying it places nothing.
    """
    if not state:
        return {"mode": "unknown", "why": "no state file"}
    if state.get("halted"):
        return {"mode": "halted", "why": "a kill switch has stopped it"}
    dry = state.get("dry_run")
    sending = state.get("sending_orders")
    if sending is True or dry is False:
        return {"mode": "demo", "why": "sending orders on the demo account"}
    if dry is True:
        return {"mode": "dry run", "why": "decides and places nothing"}
    return {"mode": "unknown", "why": "the state file does not say"}


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
    print("Nothing here is blocked from learning. Every candidate keeps its confidence estimate")
    print("moving with each closed trade, and the 0.95 deflated Sharpe bar applies at ONE rung")
    print(f"only - real money - where it does not move. A full-size verdict needs {MIN_TRADES} trades.\n")
    for source in report["sources"]:
        stats, v = source["stats"], source["verdict"]
        magic = f"magic {source['magic']}" if source["magic"] else "paper"
        print(f"  {source['label']} ({magic})")
        print(f"     closed {stats['trades']:>3}   open {source['open_trades']}   "
              f"total {stats['total_r']:+.2f} R   expectancy "
              f"{stats['expectancy_r'] if stats['expectancy_r'] is not None else 'n/a'}   "
              f"PF {stats['profit_factor'] if stats['profit_factor'] is not None else 'n/a'}")
        c, ti = source["confidence"], source["tier"]
        running = source["currently"]
        ahead = running["mode"] == "demo" and ti["tier"] < 2
        print(f"     RUNNING: {running['mode']} - {running['why']}")
        print(f"     evidence supports tier {ti['tier']} ({ti['name']})"
              + ("   <-- it is trading AHEAD of its evidence, by the owner's decision" if ahead else ""))
        print(f"     confidence: {c['reading']}")
        print(f"     to advance: {ti['to_advance']}")
        print(f"     learning now: {ti['learning']}")
        print(f"     {source['note']}")
    print("\n  Candidates that earned a forward test but not a deployment:")
    for candidate in report["watchlist"]:
        print(f"     {candidate['label']}")
        print(f"        evidence : {candidate['evidence']}")
        print(f"        watching : {candidate['why_watch']}")
        ti, c = candidate["tier"], candidate["confidence"]
        print(f"        tier     : {ti['tier']} - {ti['name']} ({ti['risk']})")
        print(f"        confidence: {c['reading']}")
        print(f"        to advance: {ti['to_advance']}")
    totals = report["totals"]
    print(f"\n  TOTAL closed forward trades this system can learn from: {totals['closed_forward_trades']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
