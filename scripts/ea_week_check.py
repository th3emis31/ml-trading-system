"""One week of SmartEntry V9 results, win and loss, per symbol. Read-only - never trades.

The owner froze the EA on 2026-09-25 with the new settings and asked for a full picture next Friday
before making the NEXT single change. One change at a time is their standing rule, so this exists to
make each week's change judgeable instead of guessed at.

It reads account 25446287's closed deals by the EA's own magic numbers (gold 60701111, bitcoin
60701122). Tracking another expert's trades is normally against the house rule in
.claude/memory/only-track-this-systems-own-strategies.md - this is the owner's explicit request for
their own EA, and it stays read-only.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.runtime_paths import installed_terminal          # noqa: E402

FREEZE = ROOT / "data/ea_watch/freeze.json"
OUT = ROOT / "data/ea_watch/week_report.json"
# The EA runs on the panel terminal, whose location is this machine's business and lives in
# config/machine.json rather than here (build map step 9).
TERMINAL = installed_terminal("mt5_panel")
MAGICS = {60701111: "XAUUSD", 60701122: "BTCUSD"}


def read_deals(since: datetime):
    """Closed deals since the freeze, via the EA's own terminal.

    This attaches to the terminal the EA runs in, NOT the one the trading app uses - connecting to the
    app's terminal has halted live strategies before (24 September, two strategies down for two hours).
    """
    import MetaTrader5 as mt5
    if not mt5.initialize(path=TERMINAL):
        return None, f"MT5 refused: {mt5.last_error()}"
    try:
        info = mt5.account_info()
        deals = mt5.history_deals_get(since, datetime.now(timezone.utc))
        return ([d._asdict() for d in (deals or [])],
                {"login": getattr(info, "login", None), "balance": getattr(info, "balance", None),
                 "equity": getattr(info, "equity", None), "server": getattr(info, "server", None)})
    finally:
        mt5.shutdown()


def summarise(deals, magic):
    rows = [d for d in deals if int(d.get("magic") or 0) == magic and d.get("entry") == 1]
    nets = [float(d.get("profit") or 0) + float(d.get("swap") or 0) + float(d.get("commission") or 0)
            for d in rows]
    wins = [n for n in nets if n > 0]
    losses = [n for n in nets if n <= 0]
    gross_win, gross_loss = sum(wins), -sum(losses)
    return {
        "magic": magic, "trades": len(nets),
        "wins": len(wins), "losses": len(losses),
        "win_rate": round(len(wins) / len(nets), 4) if nets else None,
        "won": round(gross_win, 2), "lost": round(gross_loss, 2),
        "net": round(sum(nets), 2),
        # None, not infinity: a week with no losing trade has an undefined profit factor, and printing
        # "inf" on a dashboard has broken a page here before.
        "profit_factor": round(gross_win / gross_loss, 3) if gross_loss > 0 else None,
        "best": round(max(nets), 2) if nets else None,
        "worst": round(min(nets), 2) if nets else None,
    }


if __name__ == "__main__":
    if not FREEZE.exists():
        raise SystemExit(f"no freeze record at {FREEZE} - run with 'freeze' first")
    freeze = json.loads(FREEZE.read_text(encoding="utf-8"))
    since = datetime.fromisoformat(freeze["frozen_at"]).replace(tzinfo=timezone.utc)
    deals, account = read_deals(since)
    if deals is None:
        raise SystemExit(f"could not read the account: {account}")
    report = {
        "at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "since": freeze["frozen_at"], "days": round((datetime.now(timezone.utc) - since).days, 1),
        "account": account, "frozen_settings": freeze["settings"],
        "by_symbol": {MAGICS[m]: summarise(deals, m) for m in MAGICS},
        "places_orders": False,
    }
    OUT.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    print(json.dumps(report["by_symbol"], indent=1))
    print(f"\nsaved -> {OUT}")
