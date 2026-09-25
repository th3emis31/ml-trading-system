"""Write a SYSTEM LIVE sheet into the owner's Trading Business Dashboard, from the running system.

Every figure here is read from the system that produced it - the MT5 account, the closed-trade
history, the live signal payload, the strategy cycles, the provider survey. Nothing is typed in and
nothing is estimated, because the dashboard existed for a week showing a balance of 10,768 and a
71.4% win rate computed correctly from seven invented trades. A number nobody can trace is worse than
a blank cell: the blank one gets questioned.

Where a figure cannot be read, the cell says so. It is never left to look like a zero.

Read-only against the system; writes only to Excel.
"""
from __future__ import annotations

import json
import sys
import urllib.request
from datetime import datetime, timezone

BASE = "http://localhost:5000"

# Instrument palette: a slate ground with one gauge blue, and semantic colours kept separate from it
# so "good" never reads as "accent".
INK = "1B2430"
MUTED = "6B7A88"
RULE = "D5DCE3"
BAND = "F2F5F8"
ACCENT = "1F5F8B"
GOOD = "2D7D5A"
WARN = "8A6D1F"
BAD = "9B3B2F"


def api(path: str, timeout: int = 180):
    try:
        with urllib.request.urlopen(BASE + path, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as exc:                      # a dead endpoint must show as unreadable, not as 0
        return {"_error": f"{type(exc).__name__}: {exc}"}


def read_system() -> list:
    """Every row the sheet shows: (section, label, value, note, kind).

    ``kind`` drives the formatting only - 'good'/'warn'/'bad'/'money'/'pct'/'plain'.
    """
    rows: list = []

    def add(section, label, value, note="", kind="plain"):
        rows.append((section, label, value, note, kind))

    # ---- account: the one thing everything else is measured against ----
    demo = api("/api/demo-trading/status")
    acct = (demo or {}).get("account") or {}
    if acct.get("available"):
        live = "DEMO" if acct.get("is_demo") else "LIVE"
        add("ACCOUNT", "Login", acct.get("login"), f"{acct.get('server')} · {acct.get('company')}")
        add("ACCOUNT", "Type", live, "no real money at risk" if acct.get("is_demo") else "REAL MONEY",
            "good" if acct.get("is_demo") else "bad")
        add("ACCOUNT", "Currency", acct.get("currency"), "every money figure below is in this currency")
        add("ACCOUNT", "Balance", acct.get("balance"), "", "money")
        add("ACCOUNT", "Equity", acct.get("equity"), "balance plus open profit", "money")
        add("ACCOUNT", "Open profit", acct.get("open_profit"), f"{acct.get('open_legs')} open leg(s)", "money")
    else:
        add("ACCOUNT", "Account", "UNREADABLE", str(acct.get("reason") or demo.get("_error") or "")[:70], "bad")

    # ---- what THIS system earned: its own magics only ----
    closed = api("/api/trades/closed")
    if closed.get("available"):
        t = closed["totals"]
        pf = t.get("profit_factor")
        add("THIS SYSTEM", "Closed trades", closed.get("count"), "its own strategies only, not the "
            "other experts on this account")
        add("THIS SYSTEM", "Net", t.get("net"), "", "money")
        add("THIS SYSTEM", "Won / Lost", f"{t.get('won')} / {t.get('lost')}", "gross", "plain")
        add("THIS SYSTEM", "Win rate", t.get("win_rate"), f"{t.get('wins')}W / {t.get('losses')}L", "pct")
        add("THIS SYSTEM", "Profit factor", pf if pf is not None else "n/a",
            "above 1.0 means it earns more than it loses",
            "good" if (pf or 0) > 1 else "bad")
    else:
        add("THIS SYSTEM", "Closed trades", "UNREADABLE", str(closed.get("reason") or "")[:70], "bad")

    # ---- per strategy, so a losing one cannot hide inside the total ----
    growth = api("/api/growth")
    names = {"440401": "GOLD4H model", "440502": "Gold session pullback", "440603": "Volatility breakout",
             "440704": "Daily plan", "440805": "Sweep reversal", "903110": "SmartEntry auto"}
    for magic, stats in sorted((growth.get("by_strategy") or {}).items()):
        net = stats.get("net")
        add("BY STRATEGY", names.get(magic, f"magic {magic}"), net,
            f"{stats.get('trades')} trades · win {round((stats.get('win_rate') or 0)*100)}%",
            "good" if (net or 0) > 0 else "bad")
    for symbol, stats in sorted((growth.get("by_symbol") or {}).items()):
        net = stats.get("net")
        add("BY MARKET", symbol, net,
            f"{stats.get('trades')} trades · win {round((stats.get('win_rate') or 0)*100)}%",
            "good" if (net or 0) > 0 else "bad")

    # ---- what the models say right now, and whether they have any edge at all ----
    live = api("/api/signals/live")
    for sig in (live.get("signals") or []):
        edge = sig.get("model_edge") or {}
        status = str(edge.get("status") or "not measured")
        age = sig.get("signal_bar_age_minutes")
        add("LIVE SIGNALS", sig.get("symbol"), sig.get("signal"),
            f"{sig.get('data_source') or 'no source'} · bar {round(age) if age else '?'} min old · {status}",
            "warn" if status.startswith("no edge") else "plain")

    # ---- the strategies that actually place orders ----
    for path, label in (("/api/demo-trading/status", "Gold session pullback"),
                        ("/api/demo-breakout/status", "Volatility breakout")):
        state = api(path)
        if state.get("_error"):
            add("STRATEGIES", label, "UNREADABLE", state["_error"][:60], "bad")
            continue
        halted = state.get("halted")
        cycle = state.get("last_cycle") or {}
        add("STRATEGIES", label, "HALTED" if halted else "running",
            f"last cycle {str(cycle.get('at'))[:16]} · {str(cycle.get('decision') or '')[:26]}",
            "bad" if halted else "good")

    # ---- can this run without the internet? the owner's standing constraint ----
    bm = api("/api/i40/build-map")
    prov = (bm or {}).get("providers") or {}
    if prov.get("rows"):
        offline = prov.get("offline_capable")
        add("INDEPENDENCE", "Works offline", "YES" if offline else "NOT YET",
            "a local model is installed" if offline else "no local model installed yet",
            "good" if offline else "warn")
        add("INDEPENDENCE", "Build map", f"{bm.get('steps_done')} of {bm.get('steps_total')} steps",
            (bm.get("next_step") or {}).get("title", ""))
    mem = (bm or {}).get("memory") or {}
    if mem.get("entries"):
        add("INDEPENDENCE", "Memory traceable", (mem.get("traceable_pct") or 0) / 100.0,
            f"{mem.get('entries')} entries · {mem.get('hypotheses')} unproven claims", "pct")

    return rows


def write(rows: list) -> str:
    import win32com.client as win32

    xl = win32.GetActiveObject("Excel.Application")
    wb = xl.Workbooks(1)
    name = "System Live"
    try:
        ws = wb.Sheets(name)
        ws.Cells.Clear()
    except Exception:
        ws = wb.Sheets.Add(After=wb.Sheets(wb.Sheets.Count))
        ws.Name = name

    xl.ScreenUpdating = False
    ws.Cells.Font.Name = "Aptos Narrow"
    ws.Range("A:A").ColumnWidth = 2
    ws.Range("B:B").ColumnWidth = 30
    ws.Range("C:C").ColumnWidth = 18
    ws.Range("D:D").ColumnWidth = 62

    ws.Range("B2").Value = "SYSTEM LIVE"
    ws.Range("B2").Font.Size = 20
    ws.Range("B2").Font.Bold = True
    ws.Range("B2").Font.Color = int(INK[4:6] + INK[2:4] + INK[0:2], 16)
    ws.Range("B3").Value = ("Read from the running system at "
                            + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                            + ".  Nothing on this sheet is typed in.")
    ws.Range("B3").Font.Size = 9
    ws.Range("B3").Font.Color = int(MUTED[4:6] + MUTED[2:4] + MUTED[0:2], 16)

    r = 5
    section = None
    for sec, label, value, note, kind in rows:
        if sec != section:
            section = sec
            r += 1
            cell = ws.Range(f"B{r}:D{r}")
            cell.Merge()
            ws.Range(f"B{r}").Value = sec
            ws.Range(f"B{r}").Font.Bold = True
            ws.Range(f"B{r}").Font.Size = 9
            ws.Range(f"B{r}").Font.Color = 0xFFFFFF
            cell.Interior.Color = int(ACCENT[4:6] + ACCENT[2:4] + ACCENT[0:2], 16)
            ws.Range(f"B{r}").HorizontalAlignment = -4131
            ws.Range(f"B{r}").IndentLevel = 1
            r += 1
        ws.Range(f"B{r}").Value = label
        ws.Range(f"C{r}").Value = value
        ws.Range(f"D{r}").Value = note
        ws.Range(f"C{r}").HorizontalAlignment = -4152
        ws.Range(f"D{r}").Font.Size = 9
        ws.Range(f"D{r}").Font.Color = int(MUTED[4:6] + MUTED[2:4] + MUTED[0:2], 16)
        if kind == "money":
            ws.Range(f"C{r}").NumberFormat = "#,##0.00;[Red]-#,##0.00"
        elif kind == "pct":
            ws.Range(f"C{r}").NumberFormat = "0.0%"
        colour = {"good": GOOD, "warn": WARN, "bad": BAD}.get(kind)
        if colour:
            ws.Range(f"C{r}").Font.Color = int(colour[4:6] + colour[2:4] + colour[0:2], 16)
            ws.Range(f"C{r}").Font.Bold = True
        if r % 2 == 0:
            ws.Range(f"B{r}:D{r}").Interior.Color = int(BAND[4:6] + BAND[2:4] + BAND[0:2], 16)
        ws.Range(f"B{r}:D{r}").Borders(9).Color = int(RULE[4:6] + RULE[2:4] + RULE[0:2], 16)
        r += 1

    ws.Range(f"B{r+1}").Value = ("Refresh:  python scripts/excel_system_live.py   "
                                 "(reads the system and rewrites this sheet)")
    ws.Range(f"B{r+1}").Font.Size = 9
    ws.Range(f"B{r+1}").Font.Italic = True
    ws.Range(f"B{r+1}").Font.Color = int(MUTED[4:6] + MUTED[2:4] + MUTED[0:2], 16)
    try:
        # Cosmetic only, and it needs the sheet to be the active one - never worth failing the write.
        ws.Activate()
        xl.ActiveWindow.FreezePanes = False
        ws.Range("B6").Select()
        xl.ActiveWindow.FreezePanes = True
        ws.Range("B2").Select()
    except Exception:
        pass
    xl.ScreenUpdating = True
    wb.Save()
    return f"{len(rows)} rows written to '{name}'"


if __name__ == "__main__":
    data = read_system()
    print(write(data))
    for sec, label, value, note, kind in data:
        print(f"  {sec:14} {label:24} {str(value)[:22]:22} {note[:52]}")
