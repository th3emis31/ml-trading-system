"""SwingTrendPullback MT5 expert monitor: live status, health checks and a permanent trade record.

The expert (v2.02+) writes two files into MetaTrader's shared ``Common\\Files\\SwingTrendPullback``
folder: ``<login>_<symbol>_<magic>_status.json`` every 15 seconds (account, terminal, signal
checklist, open position, running totals) and ``..._deals.json`` after every trade (all of its
deals). This module reads them, pairs deals into trades, merges the trades into
``data/ea/swing_trend_pullback/trades.json`` so nothing is lost when MetaTrader history is
trimmed, parses the terminal's Experts log for events, and checks the EA's health.

It never talks to MetaTrader and never places, modifies or closes orders.
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .system_doctor import _read_json  # shared tolerant JSON reader (UTF-8 or MetaTrader ANSI)

EA_NAME = "SwingTrendPullback"
MAGIC = 996611
SYMBOL = "XAUUSD"
EXPECTED_VERSION = "2.02"
APPDATA = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
COMMON_DIR = APPDATA / "MetaQuotes" / "Terminal" / "Common" / "Files" / EA_NAME
TERMINAL_DIR = APPDATA / "MetaQuotes" / "Terminal" / "D0E8209F77C8CF37AD8BF550E51FF075"
PRESET_NAME = "SwingTrendPullback_XAUUSD_H4_tradingview.set"
STORE_PATH = Path("data") / "ea" / "swing_trend_pullback" / "trades.json"
STATUS_STALE_SECONDS = 120

# MT5 deal reasons (ENUM_DEAL_REASON)
DEAL_REASONS = {0: "manual", 1: "mobile", 2: "web", 3: "expert", 4: "stop loss", 5: "take profit",
                6: "stop out", 7: "rollover", 8: "variation margin", 9: "split"}

# What the loaded preset achieved before it went live (MT5 Strategy Tester, broker data, swap included).
REFERENCE = {
    "preset": PRESET_NAME,
    "timeframe": "XAUUSD H4",
    "tests": [
        {"name": "TradingView (user)", "period": "2023-01-02 to 2026-09-11", "trades": 67, "win_rate": 41.8,
         "profit_factor": 1.589, "net_pct": 4.01, "max_dd_pct": 2.56, "note": "10k USD, small order size, no swap"},
        {"name": "MT5 tester, same inputs", "period": "2023-01-02 to 2026-09-12", "trades": 103, "win_rate": 42.7,
         "profit_factor": 1.46, "net_pct": 23.3, "max_dd_pct": 5.6, "note": "100k USD, 1% risk, swap -10.7k"},
        {"name": "MT5 tester, same inputs", "period": "2018-03 to 2026-09", "trades": 223, "win_rate": 34.5,
         "profit_factor": 0.97, "net_pct": -2.8, "max_dd_pct": 24.7, "note": "loses 2018-2022, wins 2023-2026"},
    ],
    "source": "ml_trading_system/.claude/memory/BASELINE.md",
}

# Active-inputs line (EA log / status) key -> preset (.set) key
INPUT_KEYS = {
    "ema_fast": "EMAFastLen", "ema_slow": "EMASlowLen", "slope": "EMASlopeLookback", "trend_ema": "TrendFilterEmaLen",
    "push_lookback": "PushLookback", "push_atr": "PushAtrMult", "pullback_tol": "PullbackTolMult",
    "bull_close": "RequireBullClose", "rsi_filter": "UseRsiFilter", "rsi_len": "RsiLen", "rsi_min": "RsiMin",
    "adx_filter": "UseAdxFilter", "atr_len": "AtrLen", "stop_mult": "AtrSLMult", "take_profit": "UseTakeProfit",
    "rr": "RrRatio", "trailing": "UseTrailingStop", "trail_atr": "TrailAtrMult", "max_bars_exit": "UseMaxBarsExit",
    "max_bars": "MaxBarsInTrade", "risk_pct": "RiskPercent", "magic": "MagicNumber",
}

_INPUTS_RE = re.compile(
    r"EMA (?P<ema_fast>\d+)/(?P<ema_slow>\d+) slope (?P<slope>\d+) trendEMA (?P<trend_ema>\d+) \| "
    r"push (?P<push_lookback>\d+) > (?P<push_atr>[\d.]+) ATR \| tol (?P<pullback_tol>[\d.]+) ATR bullClose (?P<bull_close>on|off) \| "
    r"RSI (?P<rsi_filter>on|off) (?P<rsi_len>\d+)>(?P<rsi_min>[\d.]+) ADX (?P<adx_filter>on|off) \| "
    r"ATR (?P<atr_len>\d+) stop (?P<stop_mode>swing|ATR) (?P<stop_mult>[\d.]+) \| "
    r"TP (?P<take_profit>on|off) RR (?P<tp_mode>fixed|ATR) (?P<rr>[\d.]+) \| trail (?P<trailing>on|off) (?P<trail_atr>[\d.]+) \| "
    r"maxBars (?P<max_bars_exit>on|off) (?P<max_bars>\d+) \| lots (?P<lot_mode>\w+) risk (?P<risk_pct>[\d.]+)% magic (?P<magic>\d+)")


# ----------------------------------------------------------------------------- files
def _write_json_atomic(path, payload) -> None:
    """Write JSON through a temporary file, so a reader never sees a half-written one.

    ``default=str`` is not cosmetic. Payloads here are built from pandas and numpy, whose scalar
    types are not JSON serializable: a ``numpy.bool_`` in a daily plan crashed the SmartEntry Daily
    Agent every run with "Object of type bool is not JSON serializable" (seen 21 September 2026, task
    exit code 1). Without a fallback the whole agent aborts and writes nothing, which is a large
    consequence for a value that only needed rendering. Every other writer in this project already
    passes it.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    os.replace(tmp, path)


def find_export(common_dir=COMMON_DIR, magic: int = MAGIC, symbol: str = SYMBOL) -> dict:
    """Newest status/deals file pair written by the EA for this symbol and magic."""
    common_dir = Path(common_dir)
    found = {"status_path": None, "deals_path": None}
    if not common_dir.is_dir():
        return found
    statuses = sorted(common_dir.glob(f"*_{symbol}_{magic}_status.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if statuses:
        status = statuses[0]
        found["status_path"] = status
        deals = status.with_name(status.name.replace("_status.json", "_deals.json"))
        found["deals_path"] = deals if deals.exists() else None
    return found


def load_status(path, now: float | None = None) -> dict | None:
    if not path:
        return None
    status = _read_json(path)
    if not isinstance(status, dict):
        return None
    now = time.time() if now is None else now
    try:
        status["file_age_seconds"] = round(now - Path(path).stat().st_mtime, 1)
    except OSError:
        status["file_age_seconds"] = None
    return status


def server_offset_seconds(doc: dict | None) -> int:
    """Broker server time minus UTC, rounded to 30 minutes (0 when unknown)."""
    if not doc or not doc.get("server_time") or not doc.get("gmt_time"):
        return 0
    return int(round((int(doc["server_time"]) - int(doc["gmt_time"])) / 1800.0) * 1800)


def server_time_to_utc(server_seconds, offset: int) -> str | None:
    if not server_seconds:
        return None
    return datetime.fromtimestamp(int(server_seconds) - offset, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# ----------------------------------------------------------------------------- trades
def _exit_reason(deal: dict) -> str:
    comment = str(deal.get("comment") or "").lower()
    reason = deal.get("reason")
    if reason == 4 or comment.startswith("sl"):
        return "stop loss"
    if reason == 5 or comment.startswith("tp"):
        return "take profit"
    if "max hold" in comment:
        return "time exit"
    if reason == 6 or comment.startswith("so"):
        return "stop out"
    return DEAL_REASONS.get(reason, "other")


def pair_trades(deals: list[dict], offset: int = 0) -> list[dict]:
    """Group deals by position into trades (open or closed), oldest first."""
    positions: dict[int, dict] = {}
    for deal in sorted(deals or [], key=lambda d: (d.get("time_msc") or 0, d.get("ticket") or 0)):
        pos_id = int(deal.get("position") or deal.get("ticket") or 0)
        if not pos_id:
            continue
        rec = positions.setdefault(pos_id, {"position": pos_id, "entry": None, "exits": []})
        if deal.get("entry") == "IN" and rec["entry"] is None:
            rec["entry"] = deal
        elif deal.get("entry") in ("OUT", "OUT_BY", "INOUT"):
            rec["exits"].append(deal)
    trades = []
    for pos_id, rec in positions.items():
        entry, exits = rec["entry"], rec["exits"]
        if entry is None and not exits:
            continue
        first = entry or exits[0]
        side = str(first.get("type") or "")
        if entry is None:  # only an exit survived in history: the entry side is the opposite deal type
            side = "BUY" if side == "SELL" else "SELL"
        volume_in = float((entry or {}).get("volume") or sum(float(e.get("volume") or 0) for e in exits))
        volume_out = sum(float(e.get("volume") or 0) for e in exits)
        closed = bool(exits) and volume_out + 1e-9 >= volume_in
        money = lambda key: round(sum(float(d.get(key) or 0) for d in ([entry] if entry else []) + exits), 2)
        profit, swap, commission, fee = money("profit"), money("swap"), money("commission"), money("fee")
        open_price = float((entry or {}).get("price") or 0) or None
        close_price = (sum(float(e.get("price") or 0) * float(e.get("volume") or 0) for e in exits) / volume_out
                       if volume_out > 0 else None)
        sl = float((entry or {}).get("sl") or 0) or None
        tp = float((entry or {}).get("tp") or 0) or None
        r_multiple = None
        if open_price and sl and close_price and abs(open_price - sl) > 0:
            direction = 1 if side == "BUY" else -1
            r_multiple = round(direction * (close_price - open_price) / abs(open_price - sl), 2)
        open_time = (entry or {}).get("time")
        close_time = exits[-1].get("time") if exits else None
        hours = round((int(close_time) - int(open_time)) / 3600.0, 1) if open_time and close_time else None
        trades.append({
            "position": pos_id, "symbol": first.get("symbol") or SYMBOL, "side": side,
            "status": "closed" if closed else "open", "volume": round(volume_in, 2),
            "open_time_utc": server_time_to_utc(open_time, offset), "close_time_utc": server_time_to_utc(close_time, offset) if closed else None,
            "open_time_server": open_time, "close_time_server": close_time if closed else None,
            "open_price": open_price, "close_price": round(close_price, 3) if close_price else None,
            "sl": sl, "tp": tp, "profit": profit, "swap": swap, "commission": commission, "fee": fee,
            "net": round(profit + swap + commission + fee, 2), "r_multiple": r_multiple, "hours_held": hours,
            "exit_reason": _exit_reason(exits[-1]) if closed else None,
            "entry_comment": (entry or {}).get("comment"), "exit_comment": exits[-1].get("comment") if exits else None,
            "deal_tickets": [d.get("ticket") for d in ([entry] if entry else []) + exits],
        })
    trades.sort(key=lambda t: t.get("open_time_server") or t.get("close_time_server") or 0)
    return trades


def load_store(path=STORE_PATH) -> dict:
    store = _read_json(path)
    if not isinstance(store, dict) or not isinstance(store.get("trades"), dict):
        store = {"ea": EA_NAME, "magic": MAGIC, "symbol": SYMBOL, "trades": {}, "created_at": _now_iso()}
    return store


def save_store(store: dict, path=STORE_PATH) -> None:
    _write_json_atomic(path, store)


def merge_trades(store: dict, trades: list[dict], login=None) -> dict:
    """Add new trades and update changed ones (open -> closed). Nothing is ever removed."""
    added = updated = 0
    book = store.setdefault("trades", {})
    stamp = _now_iso()
    for trade in trades:
        key = f"{login or trade.get('login') or 'na'}:{trade['position']}"
        current = book.get(key)
        row = dict(trade, login=login or trade.get("login"))
        if current is None:
            row["first_seen_utc"] = stamp
            book[key] = row
            added += 1
        else:
            keep = {k: current.get(k) for k in ("first_seen_utc",)}
            if {k: v for k, v in current.items() if k not in ("first_seen_utc", "updated_utc")} != \
                    {k: v for k, v in row.items() if k not in ("first_seen_utc", "updated_utc")}:
                row.update(keep, updated_utc=stamp)
                book[key] = row
                updated += 1
    store["last_sync_utc"] = stamp
    return {"added": added, "updated": updated, "total": len(book)}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def trade_stats(trades: list[dict]) -> dict:
    closed = sorted((t for t in trades if t.get("status") == "closed"), key=lambda t: t.get("close_time_server") or 0)
    nets = [float(t.get("net") or 0) for t in closed]
    wins = [n for n in nets if n > 0]
    losses = [n for n in nets if n < 0]
    gross_profit, gross_loss = sum(wins), -sum(losses)
    equity, peak, max_dd, curve = 0.0, 0.0, 0.0, []
    streak = best_win = best_loss = 0
    for trade, net in zip(closed, nets):
        equity += net
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
        curve.append({"time": trade.get("close_time_utc"), "net": round(net, 2), "equity": round(equity, 2)})
        if net > 0:
            streak = streak + 1 if streak > 0 else 1
            best_win = max(best_win, streak)
        elif net < 0:
            streak = streak - 1 if streak < 0 else -1
            best_loss = max(best_loss, -streak)
    months: dict[str, dict] = {}
    for trade, net in zip(closed, nets):
        month = (trade.get("close_time_utc") or "")[:7] or "unknown"
        m = months.setdefault(month, {"month": month, "trades": 0, "wins": 0, "net": 0.0})
        m["trades"] += 1
        m["wins"] += 1 if net > 0 else 0
        m["net"] = round(m["net"] + net, 2)
    reasons: dict[str, int] = {}
    for trade in closed:
        reasons[trade.get("exit_reason") or "other"] = reasons.get(trade.get("exit_reason") or "other", 0) + 1
    hours = [t["hours_held"] for t in closed if t.get("hours_held") is not None]
    rs = [t["r_multiple"] for t in closed if t.get("r_multiple") is not None]
    count = len(closed)
    return {
        "closed_trades": count, "open_trades": sum(1 for t in trades if t.get("status") == "open"),
        "wins": len(wins), "losses": len(losses), "breakeven": count - len(wins) - len(losses),
        "win_rate": round(100.0 * len(wins) / count, 1) if count else None,
        "gross_profit": round(gross_profit, 2), "gross_loss": round(gross_loss, 2),
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else None,
        "net": round(sum(nets), 2), "total_swap": round(sum(float(t.get("swap") or 0) for t in closed), 2),
        "avg_win": round(gross_profit / len(wins), 2) if wins else None,
        "avg_loss": round(-gross_loss / len(losses), 2) if losses else None,
        "expectancy": round(sum(nets) / count, 2) if count else None,
        "largest_win": round(max(wins), 2) if wins else None, "largest_loss": round(min(losses), 2) if losses else None,
        "max_drawdown": round(max_dd, 2), "max_consecutive_wins": best_win, "max_consecutive_losses": best_loss,
        "avg_hours_held": round(sum(hours) / len(hours), 1) if hours else None,
        "avg_r": round(sum(rs) / len(rs), 2) if rs else None,
        "exit_reasons": reasons, "by_month": sorted(months.values(), key=lambda m: m["month"]), "equity_curve": curve,
    }


# ----------------------------------------------------------------------------- inputs and logs
def parse_inputs(text: str | None) -> dict | None:
    match = _INPUTS_RE.search(text or "")
    if not match:
        return None
    out = {}
    for key, value in match.groupdict().items():
        if value in ("on", "off"):
            out[key] = value == "on"
        elif re.fullmatch(r"\d+", value):
            out[key] = int(value)
        elif re.fullmatch(r"[\d.]+", value):
            out[key] = float(value)
        else:
            out[key] = value
    return out


def preset_inputs(path) -> dict | None:
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if "\x00" in text:
        text = Path(path).read_bytes().decode("utf-16", errors="replace")
    values = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(";") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.split("||")[0].strip()
    return values


def compare_with_preset(active: dict | None, preset: dict | None) -> list[dict]:
    """Differences between the EA's active inputs and the preset file (empty list = identical)."""
    if not active or not preset:
        return []
    diffs = []
    for key, preset_key in INPUT_KEYS.items():
        if key not in active or preset_key not in preset:
            continue
        want_raw, have = preset[preset_key], active[key]
        if isinstance(have, bool):
            want = want_raw.lower() in ("true", "1")
            same = want == have
        else:
            try:
                want = float(want_raw)
                same = abs(float(have) - want) < 1e-6
            except ValueError:
                want, same = want_raw, str(have) == want_raw
        if not same:
            diffs.append({"input": preset_key, "active": have, "preset": want})
    return diffs


_LOG_LINE_RE = re.compile(r"^\w*\t\d\t(?P<time>\d\d:\d\d:\d\d)\.\d+\t(?P<source>[^\t]*)\t(?P<text>.*)$")


def classify_event(text: str) -> str:
    low = text.lower()
    if "initialized on" in low:
        return "start"
    if low.startswith("inputs:"):
        return "inputs"
    if " entry " in f" {low} ":
        return "entry"
    if "position closed" in low or low.startswith("closed ("):
        return "exit"
    if "will retry" in low:
        return "retry"
    if "failed" in low or "rejected" in low or "not allowed" in low:
        return "error"
    if "skipped" in low:
        return "skip"
    if "removed" in low:
        return "detached"
    if "loaded successfully" in low:
        return "attached"
    return "info"


def parse_log_lines(day: str, lines, ea_name: str = EA_NAME) -> list[dict]:
    """Events for this EA from one day's MT5 log lines (terminal local time)."""
    events = []
    for line in lines:
        if ea_name not in line:
            continue
        match = _LOG_LINE_RE.match(line.rstrip("\r\n"))
        if not match:
            continue
        text = match.group("text").strip()
        if text.startswith("SwingPullback: "):
            text = text[len("SwingPullback: "):]
        elif not text.startswith("expert " + ea_name):
            continue
        events.append({"time": f"{day} {match.group('time')}", "kind": classify_event(text), "text": text})
    return events


def _read_utf16(path: Path) -> list[str]:
    try:
        raw = path.read_bytes()
    except OSError:
        return []
    for enc in ("utf-16", "utf-8"):
        try:
            return raw.decode(enc).splitlines()
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace").splitlines()


def read_log_events(terminal_dir=TERMINAL_DIR, days: int = 3, today: datetime | None = None) -> list[dict]:
    terminal_dir = Path(terminal_dir)
    today = today or datetime.now()
    events = []
    for back in range(days - 1, -1, -1):
        day = today - timedelta(days=back)
        stamp = day.strftime("%Y%m%d")
        iso = day.strftime("%Y-%m-%d")
        for folder in (terminal_dir / "MQL5" / "Logs", terminal_dir / "logs"):
            path = folder / f"{stamp}.log"
            if path.exists():
                events.extend(parse_log_lines(iso, _read_utf16(path)))
    events.sort(key=lambda e: e["time"])
    return events


# ----------------------------------------------------------------------------- health
def _market_probably_closed(now_utc: datetime) -> bool:
    """Gold spot is closed from Friday ~21:00 UTC to Sunday ~22:00 UTC."""
    wd, hour = now_utc.weekday(), now_utc.hour
    return wd == 5 or (wd == 4 and hour >= 21) or (wd == 6 and hour < 22)


def health_checks(status: dict | None, events: list[dict], active_inputs: dict | None, preset: dict | None,
                  now_utc: datetime | None = None) -> dict:
    now_utc = now_utc or datetime.now(timezone.utc)
    checks = []

    def add(name, state, detail):
        checks.append({"name": name, "state": state, "detail": detail})

    starts = [e for e in events if e["kind"] == "start"]
    last_start = starts[-1] if starts else None
    closed_market = _market_probably_closed(now_utc)

    if status is None:
        add("Dashboard export", "warn",
            "No status file yet. Load EA v2.02 on the chart (remove and attach again) so it can publish its status and trades.")
    else:
        age = status.get("file_age_seconds")
        if not status.get("running", True):
            add("EA running", "bad", f"EA stopped on the chart (deinit reason {status.get('deinit_reason')}).")
        elif age is not None and age > STATUS_STALE_SECONDS:
            add("EA running", "bad", f"Last status {int(age)} s ago: the EA, chart or terminal is not running.")
        else:
            add("EA running", "ok", f"Status updated {int(age or 0)} s ago.")

    version = (status or {}).get("version")
    if version is None and last_start:
        match = re.search(r"v(\d+\.\d+)", last_start["text"])
        version = match.group(1) if match else None
    if version == EXPECTED_VERSION:
        add("EA version", "ok", f"v{version}")
    elif version:
        add("EA version", "warn", f"v{version} running; v{EXPECTED_VERSION} is compiled. Remove and attach the EA to load it.")
    else:
        add("EA version", "warn", "Unknown: no start found in the last days of logs.")

    if status is not None:
        add("Terminal connection", "ok" if status.get("terminal_connected") else "bad",
            f"{status.get('company') or ''} {status.get('server') or ''}".strip() or "connection state")
        algo = bool(status.get("algo_terminal")) and bool(status.get("algo_expert"))
        add("Algo Trading", "ok" if algo else "bad",
            "Enabled in the terminal and for this EA." if algo else
            f"Terminal {'on' if status.get('algo_terminal') else 'OFF'}, EA {'on' if status.get('algo_expert') else 'OFF'}: no orders can be placed.")
        mode = status.get("account_trade_mode")
        add("Account type", "ok" if mode == 0 else "warn",
            f"{'Demo' if mode == 0 else 'REAL MONEY' if mode == 2 else 'Contest'} account {status.get('login')}, "
            f"balance {status.get('balance')} {status.get('currency') or ''}")
        tick_age = (int(status.get("server_time") or 0) - int(status.get("tick_time") or 0)) if status.get("tick_time") else None
        if tick_age is None:
            add("Market data", "warn", "No quote received yet.")
        elif tick_age > 300 and not closed_market:
            add("Market data", "warn", f"Last quote {tick_age // 60} min old.")
        else:
            add("Market data", "ok", "Market closed (weekend); quotes resume at the open." if closed_market and tick_age > 300
                else f"Quote {tick_age} s old, spread {status.get('spread_points')} points.")
        position = status.get("position")
        if position:
            add("Open position protected", "ok" if float(position.get("sl") or 0) > 0 else "bad",
                f"{position.get('type')} {position.get('volume')} lots, stop {position.get('sl')}, target {position.get('tp') or 'none'}")

    if active_inputs and preset:
        diffs = compare_with_preset(active_inputs, preset)
        add("Inputs match preset", "ok" if not diffs else "warn",
            f"Identical to {PRESET_NAME}." if not diffs else
            "Differs from preset: " + ", ".join(f"{d['input']} {d['active']} (preset {d['preset']})" for d in diffs))
    elif active_inputs is None:
        add("Inputs match preset", "warn", "No inputs line found yet (EA v2.01+ logs it on start).")

    cutoff = (datetime.now() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    errors = [e for e in events if e["kind"] == "error" and e["time"] >= cutoff]
    add("Errors (24 h)", "ok" if not errors else "warn",
        "No trade errors." if not errors else f"{len(errors)} error(s); last: {errors[-1]['text']}")

    order = {"ok": 0, "warn": 1, "bad": 2}
    overall = max((c["state"] for c in checks), key=lambda s: order[s], default="warn")
    return {"overall": overall, "checks": checks, "market_closed": closed_market}


# ----------------------------------------------------------------------------- summary
def build_summary(common_dir=COMMON_DIR, terminal_dir=TERMINAL_DIR, store_path=STORE_PATH,
                  now: float | None = None) -> dict:
    """Everything the auto-trader EA tab shows. Syncs exported deals into the permanent store first."""
    export = find_export(common_dir)
    status = load_status(export["status_path"], now=now)
    deals_doc = _read_json(export["deals_path"]) if export["deals_path"] else None
    offset = server_offset_seconds(status or deals_doc)

    store = load_store(store_path)
    sync = {"added": 0, "updated": 0, "total": len(store.get("trades", {})), "source": None}
    if deals_doc and isinstance(deals_doc.get("deals"), list):
        login = deals_doc.get("login") or (status or {}).get("login")
        sync.update(merge_trades(store, pair_trades(deals_doc["deals"], offset), login=login))
        sync["source"] = str(export["deals_path"])
        if sync["added"] or sync["updated"] or not Path(store_path).exists():
            save_store(store, store_path)
    trades = sorted(store.get("trades", {}).values(),
                    key=lambda t: t.get("open_time_server") or t.get("close_time_server") or 0, reverse=True)

    events = read_log_events(terminal_dir)
    inputs_text = (status or {}).get("inputs")
    if not inputs_text:
        inputs_events = [e for e in events if e["kind"] == "inputs"]
        inputs_text = inputs_events[-1]["text"] if inputs_events else None
    active_inputs = parse_inputs(inputs_text)
    preset_path = Path(terminal_dir) / "MQL5" / "Presets" / PRESET_NAME
    preset = preset_inputs(preset_path)

    health = health_checks(status, events, active_inputs, preset)
    return {
        "available": True, "places_orders": False, "ea": EA_NAME, "magic": MAGIC, "symbol": SYMBOL,
        "expected_version": EXPECTED_VERSION, "generated_utc": _now_iso(),
        "export": {"status_file": str(export["status_path"]) if export["status_path"] else None,
                   "deals_file": str(export["deals_path"]) if export["deals_path"] else None,
                   "folder": str(common_dir), "server_offset_hours": offset / 3600.0},
        "status": status, "health": health,
        "inputs": {"text": inputs_text, "active": active_inputs, "preset_name": PRESET_NAME,
                   "preset": preset, "differences": compare_with_preset(active_inputs, preset)},
        "stats": trade_stats(trades), "trades": trades[:1000], "store": {"path": str(store_path), **sync,
                                                                          "last_sync_utc": store.get("last_sync_utc")},
        "events": events[-150:][::-1], "reference": REFERENCE,
    }


if __name__ == "__main__":
    summary = build_summary()
    print(json.dumps({"health": summary["health"], "stats": {k: v for k, v in summary["stats"].items()
                                                              if k not in ("equity_curve", "by_month")},
                      "store": summary["store"], "inputs": summary["inputs"]["differences"]}, indent=1))
