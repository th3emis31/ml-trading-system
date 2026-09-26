"""Obsidian notes (14 Sep 2026): the system's reports written into an Obsidian vault as Markdown notes.

Notes only; nothing here reads from Obsidian to trade and nothing places orders. The vault is a plain folder:
- Home.md                          links to everything
- Daily/<date>.md                  daily report: bias, levels, momentum, TradingView daily plan, system status
- Learning/<date> Learning.md      the honest self-learning verdict of that day
- Learning/Learning Curve.md       one row per training day per symbol
- Research/Research Log.md         Strategy Lab totals and every recorded result from BASELINE.md (failures included)
- Trades/Trade Journal.md          index of paper trades and TradingView alerts
- Trades/<one note per trade/alert> written once, never overwritten, so your own comments stay

Generated notes carry "generated_by: smartentry" in their front matter. Text below "## My notes" is kept on every
refresh; delete the generated_by line and the note is never touched again.

Scheduled hourly by the "SmartEntry Obsidian Notes" task. Run: python -m src.obsidian_notes [--vault PATH] [--register]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from .system_doctor import _read_json

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
CONFIG_PATH = DATA_DIR / "obsidian.json"
DEFAULT_VAULT = Path.home() / "Documents" / "SmartEntry Vault"
BASELINE_PATH = ROOT / "ml_trading_system" / ".claude" / "memory" / "BASELINE.md"
MARKER = "generated_by: smartentry"
MY_NOTES = "## My notes"
NAMES = {"XAUUSD": "Gold", "BTCUSD": "Bitcoin"}
SYMBOLS = ("XAUUSD", "BTCUSD")
DISCLAIMER = "> Written by SmartEntry from its own reports. Research only: not financial advice, and nothing here places orders."


def _num(value, digits: int = 2) -> str:
    if value is None or value == "":
        return "—"
    try:
        return f"{float(value):,.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


def _pct(value, digits: int = 1) -> str:
    return "—" if value is None else f"{float(value) * 100:.{digits}f}%"


def safe_name(text: str) -> str:
    """A file name Obsidian and Windows both accept."""
    cleaned = re.sub(r'[\\/:*?"<>|#^\[\]]+', " ", str(text))
    return re.sub(r"\s+", " ", cleaned).strip()[:120] or "note"


def write_generated(path: Path, meta: dict, body: str) -> str:
    """Write a generated note, keeping everything from '## My notes' down. Skips notes the owner took over."""
    path.parent.mkdir(parents=True, exist_ok=True)
    kept = f"{MY_NOTES}\n\n"
    old = None
    if path.exists():
        old = path.read_text(encoding="utf-8")
        if MARKER not in old:
            return "skipped (edited by you)"
        if MY_NOTES in old:
            kept = old[old.index(MY_NOTES):]
    front = "---\n" + "".join(f"{key}: {value}\n" for key, value in meta.items()) + MARKER + "\n---\n"
    content = front + body.rstrip() + "\n\n" + kept.rstrip() + "\n"
    if content == old:
        return "unchanged"
    path.write_text(content, encoding="utf-8")
    return "written"


def write_once(path: Path, content: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return "kept"
    path.write_text(content, encoding="utf-8")
    return "written"


def ensure_vault(vault: Path) -> None:
    for folder in ("Daily", "Learning", "Research", "Trades", ".obsidian"):
        (vault / folder).mkdir(parents=True, exist_ok=True)
    app_config = vault / ".obsidian" / "app.json"
    if not app_config.exists():
        app_config.write_text(json.dumps({"alwaysUpdateLinks": True}, indent=1), encoding="utf-8")


def register_vault(vault: Path, config_path: Optional[Path] = None) -> str:
    """Add the vault to Obsidian's vault list so it opens on first start. Run while Obsidian is closed."""
    if config_path is None:
        appdata = os.environ.get("APPDATA")
        if not appdata:
            return "APPDATA not set; open the folder in Obsidian with 'Open folder as vault'"
        config_path = Path(appdata) / "obsidian" / "obsidian.json"
    config_path = Path(config_path)
    config = _read_json(config_path) or {}
    vaults = config.setdefault("vaults", {})
    target = str(Path(vault).resolve())
    for entry in vaults.values():
        if str(entry.get("path", "")).lower() == target.lower():
            return "already registered"
    vault_id = hashlib.md5(target.lower().encode("utf-8")).hexdigest()[:16]
    vaults[vault_id] = {"path": target, "ts": int(time.time() * 1000),
                        "open": not any(entry.get("open") for entry in vaults.values())}
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(config), encoding="utf-8")
    return "registered"


# ---------- daily notes ----------

def _plan_for(data_dir: Path, date: str, symbol: str) -> Optional[dict]:
    plan = _read_json(data_dir / "tradingview_plans" / f"{date}_{symbol}.json")
    if not plan:
        latest = _read_json(data_dir / "tradingview_plans" / f"latest_{symbol}.json")
        plan = latest if latest and latest.get("date") == date else None
    return plan


def _market_section(symbol: str, market: dict, plan: Optional[dict]) -> str:
    name = NAMES.get(symbol, symbol)
    lines = [f"## {name} ({symbol})"]
    if not market or not market.get("available", True):
        lines.append(f"Market data was not available: {(market or {}).get('reason', 'no data')}.")
    else:
        bias = market.get("bias") or {}
        levels = market.get("levels") or {}
        vol = market.get("volatility") or {}
        mom = market.get("momentum") or {}
        returns = mom.get("returns_pct") or {}
        lines.append(f"- **Bias:** {bias.get('label', '—')} (score {_num(bias.get('score'))}) · timeframes {(market.get('alignment') or {}).get('label', '—')}")
        lines += [f"  - {reason}" for reason in (bias.get("reasons") or [])[:8]]
        lines.append(f"- **Price:** daily close {_num(levels.get('close'))} (last daily bar {market.get('last_daily_bar', '—')}) · ATR {_num(levels.get('atr'))} ({_num(vol.get('atr_pct'))}%)")
        lines.append(f"- **Volatility:** {vol.get('regime', '—')} · ATR percentile 1y {_num(vol.get('atr_percentile_1y'), 1)} · realised 20d {_num(vol.get('realised_vol_20d_pct'), 1)}%")
        lines.append(f"- **Momentum:** RSI {_num(mom.get('rsi_14'), 1)} ({mom.get('rsi_state', '—')}) · returns 1d {_num(returns.get('1d'))}% · 5d {_num(returns.get('5d'))}% · 20d {_num(returns.get('20d'))}% · 60d {_num(returns.get('60d'))}%")
        level_rows = levels.get("levels") or []
        if level_rows:
            lines += ["", "| Level | Price | Distance |", "|---|---:|---:|"]
            lines += [f"| {row.get('name')} | {_num(row.get('price'))} | {_num(row.get('distance_pct'))}% |" for row in level_rows[:10]]
    lines += ["", "### Daily plan (SwingTrendPullback, H4)"]
    if not plan:
        lines.append("No plan was saved for this day. Open the TradingView page to create one.")
    elif not plan.get("available", True):
        lines.append(f"Plan unavailable: {plan.get('reason', '')}")
    else:
        lines.append(f"**{plan.get('headline', '')}** (last closed H4 candle {plan.get('last_closed_candle', '—')} UTC, generated {plan.get('generated_at', '—')})")
        lines += ["", "| Entry | Stop loss | Take profit | Reward:risk | Trailing stop |", "|---:|---:|---:|---:|---:|",
                  f"| {_num(plan.get('entry'))} | {_num(plan.get('stop_loss'))} | {_num(plan.get('take_profit'))} | {_num(plan.get('reward_risk'), 1)}R | {_num(plan.get('trailing_stop_atr'), 1)} ATR |", ""]
        lines += [f"- [{'x' if item.get('ok') else ' '}] {item.get('name')}" for item in plan.get("checklist") or []]
        res, sup = plan.get("nearest_resistance"), plan.get("nearest_support")
        if res:
            lines.append(f"- Nearest resistance: {res.get('name')} {_num(res.get('price'))}")
        if sup:
            lines.append(f"- Nearest support: {sup.get('name')} {_num(sup.get('price'))}")
        last_trade = plan.get("last_closed_trade")
        if last_trade:
            lines.append(f"- Last closed plan trade: {last_trade.get('opened')} → {last_trade.get('closed')}, {last_trade.get('reason')}, {_num(last_trade.get('result_pct'))}%")
    return "\n".join(lines)


def _system_section(system: dict) -> str:
    doctor, deep = system.get("doctor") or {}, system.get("deep_check") or {}
    lab, paper, demo = system.get("strategy_lab") or {}, system.get("paper_trader") or {}, system.get("demo_execution") or {}
    lines = ["## System", f"- **Health:** {doctor.get('overall', '—')} (checked {doctor.get('checked_at', '—')})"]
    lines += [f"  - Problem: {problem}" for problem in doctor.get("problems") or []]
    lines += [f"- {line}" for line in deep.get("code") or []]
    markets = lab.get("markets") or {}
    if markets:
        totals = {key: sum(int(m.get(key) or 0) for m in markets.values()) for key in ("evaluated", "validated", "holdout_passed")}
        lines.append(f"- **Strategy Lab:** {totals['evaluated']:,} strategies tried · {totals['validated']:,} passed validation · **{totals['holdout_passed']} passed the locked holdout**")
    if paper:
        lines.append(f"- **Paper trader:** {paper.get('bars_decided', 0)} bars decided · {paper.get('closed_trades', 0)} closed trades · {paper.get('last_message', '')}")
    if demo:
        lines.append(f"- **Demo execution:** enabled {demo.get('enabled')} · dry run {demo.get('dry_run')} · open positions {len(demo.get('open_positions') or [])}")
    return "\n".join(lines)


def daily_note(report: dict, plans: dict, previous: Optional[str], following: Optional[str]) -> str:
    date = report.get("date")
    nav = " · ".join(part for part in ["[[Home]]", f"previous [[Daily/{previous}]]" if previous else "",
                                       f"next [[Daily/{following}]]" if following else "", f"[[Learning/{date} Learning]]"] if part)
    parts = [f"# Daily note {date}", nav, "", DISCLAIMER, "", f"Report generated {report.get('generated_at', '—')}."]
    for symbol in SYMBOLS:
        parts += ["", _market_section(symbol, (report.get("markets") or {}).get(symbol) or {}, plans.get(symbol))]
    if report.get("gold_btc_correlation_60d") is not None:
        parts += ["", f"Gold/bitcoin 60-day correlation: {_num(report.get('gold_btc_correlation_60d'))}"]
    parts += ["", _system_section(report.get("system") or {})]
    return "\n".join(parts)


# ---------- learning notes ----------

def learning_note(curve: dict, date: str) -> str:
    overall, task = curve.get("overall") or {}, curve.get("task") or {}
    lines = [f"# Learning {date}", f"[[Home]] · [[Learning/Learning Curve]] · [[Daily/{date}]]", "", DISCLAIMER, "",
             f"## {overall.get('headline', 'No verdict')}", overall.get("detail", ""), "",
             f"- **Daily learning:** {'running' if task.get('healthy') else 'STOPPED'} · last run {task.get('last_run', '—')} · {task.get('message', '')}"]
    for symbol, data in (curve.get("symbols") or {}).items():
        trend, checked, latest = data.get("trend") or {}, data.get("checked") or {}, data.get("latest") or {}
        gate = data.get("latest_gate") or {}
        lines += ["", f"## {NAMES.get(symbol, symbol)} ({symbol})"]
        for model, key in (("Random forest", "rf"), ("LSTM", "lstm")):
            t = trend.get(key) or {}
            if t.get("label") == "insufficient" or not t:
                lines.append(f"- **{model}:** not enough days yet")
            else:
                lines.append(f"- **{model}:** {t['label'].replace('_', ' ')} · last {t['window']} days {_pct(t['recent_mean'])} vs {_pct(t['previous_mean'])} ({t['change_pts']:+.1f} pts, chance ±{t['noise_pts']:.1f})")
        lines.append(f"- **Checked runs (same unseen bars):** {checked.get('runs', 0)} of {checked.get('needed', 10)} · new better {checked.get('new_better', 0)} · tie {checked.get('tie', 0)} · worse {checked.get('new_worse', 0)}")
        if gate:
            lines.append(f"- **Money test (live model, unseen bars):** return {_num(gate.get('live_return_pct'))}% · expectancy {_num(gate.get('live_expectancy_pct'), 3)}% per trade · {gate.get('trades', '—')} trades · {gate.get('window', '')}")
        lines.append(f"- **Latest run {latest.get('trained_at', '—')}:** RF {_pct(latest.get('rf'))} (new {_pct(latest.get('rf_challenger'))}) · LSTM {_pct(latest.get('lstm'))} (new {_pct(latest.get('lstm_challenger'))})")
        for text in (latest.get("rf_decision"), latest.get("lstm_decision"), latest.get("error")):
            if text:
                lines.append(f"  - {text}")
    return "\n".join(lines)


def learning_curve_note(curve: dict) -> str:
    lines = ["# Learning Curve", "[[Home]]", "", DISCLAIMER, "",
             "One row per training day (the day's last run). Runs before 13 Sep 2026 had no unseen-bar check."]
    for symbol, data in (curve.get("symbols") or {}).items():
        lines += ["", f"## {NAMES.get(symbol, symbol)} ({symbol})", "",
                  "| Day | RF live | RF new | RF | LSTM live | LSTM new | LSTM |", "|---|---:|---:|---|---:|---:|---|"]
        kept = {True: "kept", False: "rejected", None: "no check"}
        for point in reversed([p for p in data.get("points") or [] if p.get("counted")]):
            lines.append(f"| [[Daily/{str(point.get('trained_at'))[:10]}\\|{str(point.get('trained_at'))[:10]}]] | {_pct(point.get('rf'))} | {_pct(point.get('rf_challenger'))} | "
                         f"{kept.get(point.get('rf_promoted'), '—')} | {_pct(point.get('lstm'))} | {_pct(point.get('lstm_challenger'))} | {kept.get(point.get('lstm_promoted'), '—')} |")
    return "\n".join(lines)


# ---------- research ----------

def research_note(curve: dict, baseline_text: Optional[str]) -> str:
    lab = curve.get("strategy_lab") or {}
    lines = ["# Research Log", "[[Home]]", "", DISCLAIMER, "", "## Strategy Lab"]
    if lab.get("available"):
        totals = lab.get("totals") or {}
        lines += [f"Last run finished {lab.get('last_finished_at', '—')}. Tried {totals.get('evaluated', 0):,} · passed validation {totals.get('validated', 0):,} · **passed the locked holdout {totals.get('holdout_passed', 0)}**.",
                  "", "| Market | Tried | Validated | Holdout passed |", "|---|---:|---:|---:|"]
        lines += [f"| {m['market']} | {m['evaluated']:,} | {m['validated']:,} | {m['holdout_passed']} |" for m in lab.get("markets") or []]
    else:
        lines.append(f"Not available: {lab.get('reason', '')}")
    lines += ["", "## Every recorded result (BASELINE.md, failures included)"]
    table = [line for line in (baseline_text or "").splitlines() if line.startswith("|")]
    lines += table if table else ["BASELINE.md was not found."]
    return "\n".join(lines)


# ---------- trades ----------

def _paper_trade_note(trade: dict, symbol: str) -> tuple[str, str]:
    title = safe_name(f"Paper {str(trade.get('entry_time'))[:16]} {symbol} {trade.get('side')}")
    day = str(trade.get("entry_time"))[:10]
    body = "\n".join([
        "---", "type: paper-trade", f"symbol: {symbol}", f"side: {trade.get('side')}", f"outcome: {trade.get('outcome')}", "---",
        f"# {title}", f"[[Trades/Trade Journal]] · [[Daily/{day}]]", "", DISCLAIMER, "",
        "| Field | Value |", "|---|---|",
        f"| Signal bar | {trade.get('signal_bar')} |", f"| Entry | {trade.get('entry_time')} at {_num(trade.get('entry_price'))} |",
        f"| Stop / target | {_num(trade.get('stop'))} / {_num(trade.get('target'))} |",
        f"| Model | p(win) {_num(trade.get('p_win'), 3)} · EV {_num(trade.get('ev_r'), 3)}R · threshold {_num(trade.get('threshold_r'), 3)}R |",
        f"| Exit | {trade.get('exit_time')} · {trade.get('outcome')} after {trade.get('bars_held')} bars |",
        f"| Result | net {_num(trade.get('net_pct'), 3)}% · {_num(trade.get('r_multiple'), 2)}R |", "", MY_NOTES, ""])
    return title, body


def _alert_note(alert: dict) -> tuple[str, str]:
    received = str(alert.get("received_at"))
    test = alert.get("source") == "dashboard-test"
    title = safe_name(f"TradingView {received[:19]} {alert.get('symbol')} {alert.get('side')}{' TEST' if test else ''}")
    body = "\n".join([
        "---", "type: tradingview-alert", f"symbol: {alert.get('symbol')}", f"side: {alert.get('side')}", f"test: {str(test).lower()}", "---",
        f"# {title}", f"[[Trades/Trade Journal]] · [[Daily/{received[:10]}]]", "", DISCLAIMER, "",
        "| Field | Value |", "|---|---|",
        f"| Received | {received} UTC · source {alert.get('source')} |",
        f"| Entry / stop / target | {_num(alert.get('entry'))} / {_num(alert.get('stop_loss'))} / {_num(alert.get('take_profit_1') or alert.get('take_profit'))} |",
        f"| Risk:reward · grade | {_num(alert.get('risk_reward'))} · {alert.get('grade', '—')} |",
        f"| Model check | {alert.get('alignment', '—')} · {alert.get('notes', '')} |",
        f"| Text | {alert.get('text', '')} |", "", MY_NOTES, ""])
    return title, body


# ---------- run ----------

def write_all(vault: Path, data_dir: Path = DATA_DIR, baseline_path: Path = BASELINE_PATH, now: Optional[datetime] = None) -> dict:
    from . import learning_curve

    vault, data_dir = Path(vault), Path(data_dir)
    now = now or datetime.now()
    ensure_vault(vault)
    results: dict[str, str] = {}

    report_dates = sorted(p.stem for p in (data_dir / "daily_reports").glob("????-??-??.json"))
    for index, date in enumerate(report_dates):
        report = _read_json(data_dir / "daily_reports" / f"{date}.json")
        if not isinstance(report, dict):
            continue
        plans = {symbol: _plan_for(data_dir, date, symbol) for symbol in SYMBOLS}
        previous = report_dates[index - 1] if index > 0 else None
        following = report_dates[index + 1] if index + 1 < len(report_dates) else None
        results[f"Daily/{date}.md"] = write_generated(vault / "Daily" / f"{date}.md", {"date": date, "tags": "[smartentry, daily]"},
                                                      daily_note(report, plans, previous, following))

    curve = learning_curve.build_learning_curve(data_dir, now=now)
    today = now.strftime("%Y-%m-%d")
    results[f"Learning/{today} Learning.md"] = write_generated(vault / "Learning" / f"{today} Learning.md",
                                                               {"date": today, "tags": "[smartentry, learning]"}, learning_note(curve, today))
    results["Learning/Learning Curve.md"] = write_generated(vault / "Learning" / "Learning Curve.md", {"tags": "[smartentry, learning]"},
                                                            learning_curve_note(curve))
    baseline_text = Path(baseline_path).read_text(encoding="utf-8") if Path(baseline_path).exists() else None
    results["Research/Research Log.md"] = write_generated(vault / "Research" / "Research Log.md", {"tags": "[smartentry, research]"},
                                                          research_note(curve, baseline_text))

    journal = ["# Trade Journal", "[[Home]]", "", DISCLAIMER, "", "| When | Type | Symbol | Side | Result | Note |", "|---|---|---|---|---|---|"]
    entries = []
    paper = _read_json(learning_curve.paper_state_path(data_dir)) or {}
    symbol = ((paper.get("setup") or {}).get("symbol")) or "XAUUSD"
    for trade in paper.get("closed_trades") or []:
        title, body = _paper_trade_note(trade, symbol)
        results[f"Trades/{title}.md"] = write_once(vault / "Trades" / f"{title}.md", body)
        entries.append((str(trade.get("entry_time")), "paper", symbol, trade.get("side"), f"{trade.get('outcome')} {_num(trade.get('net_pct'), 3)}%", title))
    alerts = _read_json(data_dir / "tradingview_alerts.json") or []
    for alert in alerts if isinstance(alerts, list) else []:
        title, body = _alert_note(alert)
        results[f"Trades/{title}.md"] = write_once(vault / "Trades" / f"{title}.md", body)
        entries.append((str(alert.get("received_at")), "TradingView test" if alert.get("source") == "dashboard-test" else "TradingView",
                        alert.get("symbol"), alert.get("side"), alert.get("alignment", "—"), title))
    journal += [f"| {when[:16]} | {kind} | {sym} | {side} | {result} | [[Trades/{title}\\|open]] |"
                for when, kind, sym, side, result, title in sorted(entries, reverse=True)]
    if not entries:
        journal.append("| — | — | — | — | No trades or alerts yet | — |")
    results["Trades/Trade Journal.md"] = write_generated(vault / "Trades" / "Trade Journal.md", {"tags": "[smartentry, trades]"}, "\n".join(journal))

    latest_daily = report_dates[-1] if report_dates else None
    home = ["# SmartEntry", "", DISCLAIMER, "", f"Updated {now.strftime('%Y-%m-%d %H:%M')} (every hour by the SmartEntry Obsidian Notes task).", "",
            "## Today", f"- Daily note: [[Daily/{latest_daily}]]" if latest_daily else "- No daily report yet",
            f"- Learning: [[Learning/{today} Learning]] · {(curve.get('overall') or {}).get('headline', '')}", "",
            "## Always up to date", "- [[Learning/Learning Curve]]", "- [[Research/Research Log]]", "- [[Trades/Trade Journal]]", "",
            "## Daily notes"] + [f"- [[Daily/{date}]]" for date in reversed(report_dates)]
    results["Home.md"] = write_generated(vault / "Home.md", {"tags": "[smartentry]"}, "\n".join(home))
    counts: dict[str, int] = {}
    for outcome in results.values():
        counts[outcome] = counts.get(outcome, 0) + 1
    return {"vault": str(vault), "notes": len(results), "counts": counts, "results": results}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Write SmartEntry reports into an Obsidian vault.")
    parser.add_argument("--vault", help="vault folder (saved in data/obsidian.json)")
    parser.add_argument("--register", action="store_true", help="add the vault to Obsidian's vault list (Obsidian closed)")
    args = parser.parse_args(argv)
    config = _read_json(CONFIG_PATH) or {}
    vault = Path(args.vault or config.get("vault_path") or DEFAULT_VAULT)
    if args.vault or not config.get("vault_path"):
        CONFIG_PATH.write_text(json.dumps({**config, "vault_path": str(vault)}, indent=1), encoding="utf-8")
    summary = write_all(vault)
    if args.register:
        summary["obsidian_registration"] = register_vault(vault)
    print(json.dumps({key: value for key, value in summary.items() if key != "results"}, indent=1))
    return 0


if __name__ == "__main__":
    # Wrapped so this loop cannot finish without a record - see loop_ledger.run_main.
    from .loop_ledger import run_main

    raise SystemExit(run_main("obsidian_notes", main))
