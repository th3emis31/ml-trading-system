"""Forward watch for the owner's Gold Reaper expert (owner decision 2026-09-18), read-only.

The vendor's Strategy Tester report (ReportTester-25446287.xlsx, XAUUSD M1, 2020-11-03 to 2026-08-13) shows 279
trades, 70.25 % won, profit factor 2.06 and +$1,239 on a $3,000 deposit. Those settings were chosen by the vendor
over that same history, so the report describes a fit, not a forecast: only trades the strategy takes *after* the
report was written can say whether the edge is real. This module records exactly those.

What it does: reads closed deals from the account the app is connected to, keeps the ones carrying the expert's magic
number or comment, journals each one once, and compares the running forward result with what the backtest would lead
you to expect. What it never does: place, modify or close an order, or touch the expert in any way. The expert is the
owner's; this is a notebook, not a hand on the wheel.

Evidence rule, the same one the rest of the system uses: under 30 closed trades nothing is called anything; at 30 an
interim reading is allowed; a verdict waits for 100.

Run: python -m src.gold_reaper_watch [--days 30] [--json]
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from .runtime_paths import smartentry_data_dir

STRATEGY = "gold_reaper_forward"
DEFAULT_CONFIG = {
    "enabled": True,
    "magic": 8000,                       # ST1_MagicNumber in the report's inputs
    "comment_contains": "Gold Reaper",   # ST1_Comment; either match is enough, the expert writes both
    "symbol": "XAUUSD",
    "note": "Read-only forward watch. This module never sends an order and never changes the expert.",
}

# Straight from the vendor report, for comparison only. Nothing here is a target or a promise.
BACKTEST = {
    "source": "ReportTester-25446287.xlsx (MT5 Strategy Tester, VantageMarkets-Demo build 6182)",
    "period": "2020-11-03 to 2026-08-13 (5.77 years)",
    "trades": 279,
    "trades_per_year": 48.4,
    "win_rate_pct": 70.25,
    "profit_factor": 2.061,
    "average_trade_usd": 4.442,
    "max_equity_drawdown_pct": 7.34,
    "annualised_sharpe": 1.40,           # recomputed from the report's monthly equity; the report's own 8.80 is per-trade
    "caveats": ["commission was not charged in the test (spread and swap only)",
                "no out-of-sample split: the settings were chosen over this same history",
                "history quality 98 % on M1"],
}
INTERIM_TRADES = 30
VERDICT_TRADES = 100


def watch_dir() -> Path:
    return smartentry_data_dir() / "gold_reaper_watch"


def load_watch_config(path: Optional[Path] = None) -> dict:
    from . import demo_executor

    return demo_executor.load_config(Path(path) if path else watch_dir() / "config.json", DEFAULT_CONFIG)


def journal_path() -> Path:
    return watch_dir() / "trades.jsonl"


def journal_rows(path: Optional[Path] = None) -> list[dict]:
    """Every trade recorded so far, through the same JSONL reader the demo strategies use."""
    from .demo_session_pullback import read_jsonl

    return read_jsonl(Path(path) if path else journal_path())


def matches(deal: dict, config: dict) -> bool:
    """Is this closed deal one of the expert's? Magic or comment is enough; the symbol must still match."""
    if config.get("symbol") and str(deal.get("symbol") or "").upper() != str(config["symbol"]).upper():
        return False
    if config.get("magic") not in (None, "") and str(deal.get("magic")) == str(config["magic"]):
        return True
    needle = str(config.get("comment_contains") or "").strip().lower()
    return bool(needle) and needle in str(deal.get("comment") or "").lower()


def net_of(deal: dict) -> float:
    """What the trade actually put in the account: profit after swap, commission and fee."""
    return round(sum(float(deal.get(key) or 0.0) for key in ("profit", "swap", "commission", "fee")), 2)


def collect(get: Optional[Callable] = None, days: int = 30, config: Optional[dict] = None,
            path: Optional[Path] = None) -> dict:
    """Fetch closed deals through the app and append the expert's new ones to the journal, once each."""
    config = config or load_watch_config()
    if get is None:
        from .system_doctor import get_json as get
    code, body = get(f"/api/mt5/deals?days={int(days)}", 60)
    if code != 200 or not isinstance(body, dict) or not body.get("ok"):
        return {"available": False, "reason": f"MT5 deal history unavailable ({code}): "
                                              f"{(body or {}).get('reason') or 'no answer from the app'}", "added": 0}
    known = {row.get("ticket") for row in journal_rows(path)}
    added = []
    for deal in body.get("deals") or []:
        if deal.get("ticket") in known or not matches(deal, config):
            continue
        stamp = deal.get("time")
        added.append({"ticket": deal.get("ticket"), "position_id": deal.get("position_id"),
                      "closed_utc": datetime.fromtimestamp(int(stamp), timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                      if stamp else None,
                      "symbol": deal.get("symbol"), "volume": deal.get("volume"), "price": deal.get("price"),
                      "profit": deal.get("profit"), "swap": deal.get("swap"), "commission": deal.get("commission"),
                      "fee": deal.get("fee"), "net": net_of(deal), "magic": deal.get("magic"),
                      "comment": deal.get("comment"), "recorded_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")})
    if added:
        target = Path(path) if path else journal_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            for row in sorted(added, key=lambda r: r["closed_utc"] or ""):
                handle.write(json.dumps(row) + "\n")
    return {"available": True, "added": len(added), "scanned": len(body.get("deals") or []), "days": days}


def _wilson(wins: int, trades: int) -> Optional[tuple[float, float]]:
    """95 % confidence interval for a win rate (Wilson), so a small sample cannot be read as a precise number."""
    if trades <= 0:
        return None
    z, p, n = 1.96, wins / trades, trades
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    spread = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return round(100 * max(0.0, centre - spread), 1), round(100 * min(1.0, centre + spread), 1)


def forward_record(rows: list[dict]) -> dict:
    """The forward record: what these trades did, with no comparison and no opinion."""
    nets = [float(r.get("net") or 0.0) for r in rows]
    if not nets:
        return {"trades": 0, "net": 0.0, "note": "no trade by this expert has been recorded on this account yet"}
    wins = [n for n in nets if n > 0]
    losses = [n for n in nets if n <= 0]
    running, peak, drawdown = 0.0, 0.0, 0.0
    for value in nets:
        running += value
        peak = max(peak, running)
        drawdown = max(drawdown, peak - running)
    gross_loss = abs(sum(losses))
    return {"trades": len(nets), "wins": len(wins), "win_rate_pct": round(100 * len(wins) / len(nets), 1),
            "win_rate_95_ci": _wilson(len(wins), len(nets)),
            "net": round(sum(nets), 2), "gross_profit": round(sum(wins), 2), "gross_loss": round(-gross_loss, 2),
            "profit_factor": round(sum(wins) / gross_loss, 3) if gross_loss else None,
            "average_trade": round(sum(nets) / len(nets), 2),
            "largest_win": round(max(nets), 2), "largest_loss": round(min(nets), 2),
            "max_drawdown_money": round(drawdown, 2),
            "first_trade_utc": rows[0].get("closed_utc"), "last_trade_utc": rows[-1].get("closed_utc")}


def compare(forward: dict) -> dict:
    """Forward record against the backtest's expectation, with the evidence bar stated before the comparison."""
    trades = forward.get("trades", 0)
    if trades < INTERIM_TRADES:
        stage, verdict = "not enough yet", (f"{trades} of {INTERIM_TRADES} trades needed before even an interim "
                                            "reading; nothing is claimed from this")
    elif trades < VERDICT_TRADES:
        stage, verdict = "interim", f"{trades} trades: an early reading only, {VERDICT_TRADES} are needed for a verdict"
    else:
        stage, verdict = "verdict", f"{trades} trades: enough for a verdict on this account"
    lines = []
    ci = forward.get("win_rate_95_ci")
    if ci:
        inside = ci[0] <= BACKTEST["win_rate_pct"] <= ci[1]
        lines.append(f"win rate {forward['win_rate_pct']} % (95 % range {ci[0]}-{ci[1]} %) versus "
                     f"{BACKTEST['win_rate_pct']} % in the backtest: "
                     + ("consistent with it" if inside else "the backtest's rate is outside this range"))
    if forward.get("profit_factor") is not None:
        lines.append(f"profit factor {forward['profit_factor']} versus {BACKTEST['profit_factor']} in the backtest")
    if trades:
        lines.append(f"average trade {forward['average_trade']} versus {BACKTEST['average_trade_usd']} in the backtest "
                     "(account currency, not the report's USD)")
    return {"stage": stage, "verdict": verdict, "comparisons": lines,
            "reminder": "The backtest was fitted over its own history and charged no commission. Forward trades on "
                        "this account are the only evidence that counts."}


def build_status(get: Optional[Callable] = None, days: int = 30, config: Optional[dict] = None,
                 path: Optional[Path] = None) -> dict:
    config = config or load_watch_config()
    collected = collect(get, days, config, path) if config.get("enabled", True) else {
        "available": False, "reason": "the watch is disabled in its config", "added": 0}
    rows = sorted(journal_rows(path), key=lambda r: r.get("closed_utc") or "")
    forward = forward_record(rows)
    return {"strategy": STRATEGY, "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "config": config, "collection": collected, "backtest": BACKTEST, "forward": forward,
            "comparison": compare(forward), "recent_trades": list(reversed(rows))[:25],
            "places_orders": False,
            "note": "Read-only. This watch records what the owner's expert does; it never trades and never edits it."}


def save_status(status: dict, path: Optional[Path] = None) -> Path:
    target = Path(path) if path else watch_dir() / "latest.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(status, indent=1, default=str), encoding="utf-8")
    return target


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Gold Reaper forward watch (read-only).")
    parser.add_argument("--days", type=int, default=30, help="how far back to scan the account history")
    parser.add_argument("--json", action="store_true", help="print the whole status as JSON")
    cli_args = parser.parse_args()

    state = build_status(days=cli_args.days)
    written = save_status(state)
    if cli_args.json:
        print(json.dumps(state, indent=1, default=str))
    else:
        collection, record, verdict = state["collection"], state["forward"], state["comparison"]
        if not collection.get("available"):
            print(f"collection: {collection.get('reason')}")
        else:
            print(f"scanned {collection['scanned']} closed deals of the last {collection['days']} days, "
                  f"{collection['added']} new trade(s) by this expert")
        print(f"forward: {record.get('trades', 0)} trades, net {record.get('net', 0)}, "
              f"win rate {record.get('win_rate_pct', '-')} %, profit factor {record.get('profit_factor', '-')}")
        print(f"stage:   {verdict['stage']} - {verdict['verdict']}")
        for line in verdict["comparisons"]:
            print(f"         {line}")
        print(f"written  {written}")
