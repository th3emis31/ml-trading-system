"""Refresh the whole Trading Business Dashboard from the running system. One command, nothing typed.

    python scripts/excel_refresh.py

It rewrites four things, in the order that matters:

    1. price data   XAU/BTC daily and H4, in the BROKER'S server clock
    2. trade journal the system's own closed trades, with real entry and exit prices
    3. starting balance  the real account balance minus what this system earned
    4. System Live  account, per-strategy results, live signals, strategy state, independence

Two details this exists to get right, both learned the hard way on 25 September 2026.

**The clock.** The sheet works in broker server time (UTC+3 in summer, UTC+2 in winter) and the app
serves UTC. Writing UTC put the H4 bars on hours 1/5/9 instead of 0/4/8, so the Asia-range lookup -
`Key = Day*100 + Hour`, checking hours 0, 4, 20, 22 - missed every row, and the whole dashboard read
NO-GO with blank order levels. The offset is not hard-coded, because a fixed number is wrong for half
the year: MetaTrader H4 bars always open on server hours 0/4/8/12/16/20 and daily bars on 00:00, so
whichever offset lands a bar on its grid IS the offset that applied to that bar.

**The tables.** These sheets are Excel Tables with live formula columns - EMA50, ATR14, H4 Trend,
London DT. Overwriting cells would destroy them, so the table is RESIZED and only the raw columns are
written; Excel fills the calculated ones itself.
"""
from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = "http://localhost:5000"

PRICE_JOBS = (
    ("XAUUSD", "1d", "XAU D1 Data", "tblXAU_D1", False, 24),
    ("XAUUSD", "4h", "XAU H4 Data", "tblXAU_H4", True, 4),
    ("BTCUSD", "1d", "BTC D1 Data", "tblBTC_D1", False, 24),
    ("BTCUSD", "4h", "BTC H4 Data", "tblBTC_H4", True, 4),
)

STRATEGY_NAMES = {440401: "GOLD4H model", 440502: "Gold session pullback",
                  440603: "Volatility breakout", 440704: "Daily plan",
                  440805: "Sweep reversal", 903110: "SmartEntry auto"}


def api(path: str, timeout: int = 300):
    with urllib.request.urlopen(BASE + path, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def to_server_clock(iso: str, step_hours: int) -> datetime:
    """UTC timestamp -> the broker's server clock, derived from the bar grid rather than assumed."""
    dt = datetime.strptime(str(iso)[:19], "%Y-%m-%dT%H:%M:%S") if "T" in str(iso) else \
        datetime.strptime(str(iso)[:19], "%Y-%m-%d %H:%M:%S")
    for offset in (3, 2):
        candidate = dt + timedelta(hours=offset)
        if candidate.hour % step_hours == 0:
            return candidate
    return dt + timedelta(hours=3)


def session_for(hour: int) -> str:
    """Server clock: Asia 00-07, London 08-15, New York 16-23."""
    return "Asia" if hour < 8 else ("London" if hour < 16 else "New York")


def refresh_prices(xl, wb) -> list:
    done = []
    for symbol, timeframe, sheet, table, has_time, step in PRICE_JOBS:
        bars = api(f"/api/data/bars?symbol={symbol}&timeframe={timeframe}&count=20000")["bars"]
        rows = []
        for bar in bars:
            stamp = to_server_clock(bar["datetime"], step)
            values = (float(bar["open"]), float(bar["high"]), float(bar["low"]), float(bar["close"]),
                      int(bar.get("volume") or 0), 0, int(bar.get("spread") or 0))
            rows.append((stamp.strftime("%Y.%m.%d"), stamp.strftime("%H:%M:%S")) + values
                        if has_time else (stamp.strftime("%Y.%m.%d"),) + values)
        ws = wb.Sheets(sheet)
        lo = ws.ListObjects(table)
        n, raw = len(rows), (9 if has_time else 8)
        lo.Resize(ws.Range(ws.Cells(4, 1), ws.Cells(4 + n, lo.Range.Columns.Count)))
        ws.Range(ws.Cells(5, 1), ws.Cells(4 + n, raw)).Value = tuple(rows)
        done.append(f"{sheet}: {n:,} bars to {rows[-1][0]}")
    return done


def refresh_journal(xl, wb) -> str:
    payload = api("/api/trades/closed")
    if not payload.get("available"):
        return f"trade journal: NOT refreshed - {payload.get('reason')}"
    trades = payload["trades"]
    rows = []
    for trade in trades:
        stamp = trade["time"]
        when = (datetime.utcfromtimestamp(stamp) if isinstance(stamp, (int, float))
                else datetime.strptime(str(stamp)[:19], "%Y-%m-%d %H:%M:%S"))
        server = when + timedelta(hours=3)
        rows.append((
            server.strftime("%Y-%m-%d"), server.strftime("%H:%M"), trade["symbol"],
            session_for(server.hour), STRATEGY_NAMES.get(trade["magic"], f"magic {trade['magic']}"),
            trade["direction"], trade.get("entry_price") or "",
            # Stop and target are not in the deal record, so they stay blank. Deriving them from the
            # exit price would put an invented number where a real one belongs, and the R-multiple
            # column would then compute a risk that was never taken.
            "", "", trade["price"], trade["volume"], "", trade["net"],
        ))
    ws = wb.Sheets("Trade Journal")
    lo = ws.ListObjects("tblTrades")
    n = len(rows)
    lo.Resize(ws.Range(ws.Cells(3, 1), ws.Cells(3 + n, lo.Range.Columns.Count)))
    ws.Range(ws.Cells(4, 1), ws.Cells(3 + n, 13)).Value = tuple(rows)
    ws.Range(ws.Cells(4, 20), ws.Cells(3 + n, 20)).Value = tuple(
        ("from the MT5 closed-trade history",) for _ in rows)
    return f"trade journal: {n} real trades, net {payload['totals']['net']}"


def refresh_balance(xl, wb) -> str:
    account = api("/api/demo-trading/status").get("account") or {}
    totals = api("/api/trades/closed").get("totals") or {}
    if not account.get("available") or totals.get("net") is None:
        return "starting balance: NOT refreshed - the account or the trade totals could not be read"
    opening = round(float(account["balance"]) - float(totals["net"]), 2)
    settings = wb.Sheets("Settings")
    settings.Range("C4").Value = opening
    settings.Range("D4").Value = (
        f"MT5 {account['login']} {account['server']} - balance {account['balance']} "
        f"{account['currency']} minus this system's net {totals['net']}. Read from the account.")
    return (f"starting balance: {opening:,.2f} {account['currency']} "
            f"(so the balance tile shows the real {account['balance']:,.2f})")


WORKBOOK = Path(r"C:\Users\th_em\Desktop\Trading Dashboard\Trading-Business-Dashboard.xlsx")


def open_workbook():
    """Attach to the owner's Excel if it is running, otherwise start a private one for this run.

    Returns (excel, workbook, started_here). A scheduled refresh cannot assume Excel is open, and it
    must never close a window the owner is working in - so it only quits what it started itself.
    """
    import win32com.client as win32

    try:
        xl = win32.GetActiveObject("Excel.Application")
        for index in range(1, xl.Workbooks.Count + 1):
            book = xl.Workbooks(index)
            if book.Name.lower() == WORKBOOK.name.lower():
                return xl, book, False
        return xl, xl.Workbooks.Open(str(WORKBOOK)), False       # Excel open, this book is not
    except Exception:
        pass
    xl = win32.DispatchEx("Excel.Application")                    # a private instance, not theirs
    xl.Visible = False
    xl.DisplayAlerts = False
    return xl, xl.Workbooks.Open(str(WORKBOOK)), True


def main() -> int:
    xl, wb, started_here = open_workbook()
    print(f"refreshing {wb.Name}" + (" (opened for this run)" if started_here else " (already open)"))
    xl.ScreenUpdating = False
    xl.Calculation = -4135                      # manual while writing; 40,000 rows recalculating per
    try:                                        # write would take minutes instead of seconds
        for line in refresh_prices(xl, wb):
            print("  " + line, flush=True)
        print("  " + refresh_journal(xl, wb), flush=True)
        print("  " + refresh_balance(xl, wb), flush=True)
    finally:
        xl.Calculation = -4105
        xl.CalculateFullRebuild()
        xl.ScreenUpdating = True

    from excel_system_live import read_system, write as write_live      # same folder

    print("  " + write_live(read_system()))
    wb.Save()
    dash = wb.Sheets("Dashboard")
    print()
    print(f"  balance {dash.Range('B5').Value:,.2f}   net {dash.Range('D5').Value:,.2f}   "
          f"win {dash.Range('F5').Value:.1%}   PF {dash.Range('H5').Value:.2f}   "
          f"trades {int(dash.Range('J5').Value)}")
    print(f"  refreshed {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC")
    if started_here:
        # Only ever close what this run opened. Quitting the owner's own Excel would throw away
        # whatever they had on screen.
        wb.Close(SaveChanges=True)
        xl.Quit()
    return 0


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
    raise SystemExit(main())
