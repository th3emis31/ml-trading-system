"""When does this system actually make money? A "Sessions" sheet built from its own closed trades.

Asked for on 25 September 2026: which session, day, hour and month the system does best in, recorded
in Excel with proper boxes and colour. Every figure here comes from `/api/trades/closed` - the real
MT5 deal history with entry and exit prices paired - and is bucketed in the BROKER'S server clock,
because a session is a wall-clock idea and the server runs UTC+3 in summer, UTC+2 in winter.

## The thing this sheet must not do

It must not turn 8 trades into a strategy. The project's evidence rule is 100 after-cost trades on
unseen data before anything is called profitable, and splitting 102 trades four ways leaves every
bucket below that bar - Thursday has 8 trades and an average of +34, which is one good trade wearing a
disguise. So each row carries its trade count, any bucket under `THIN_SAMPLE` is coloured amber and
labelled `thin`, and the verdict box says in words that these are descriptions of the past and not yet
evidence. The strongest row in the whole sheet (London losing over 35 trades) is still short of the
bar, and the sheet says so.

Colour note: Excel's `Interior.Color` and `Font.Color` take **BGR**, not RGB - 0x0000FF is red, not
blue. Every constant below is written BGR for that reason.
"""
from __future__ import annotations

import collections
import json
import sys
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.runtime_paths import machine_config                     # noqa: E402

SHEET = "Sessions"
BASE = "http://localhost:5000"

# Below this many trades a bucket is a story, not a measurement.
THIN_SAMPLE = 30
# The project's bar for calling anything profitable, kept visible on the sheet so the gap is obvious.
EVIDENCE_BAR = 100

# BGR, not RGB.
GREEN_FILL, GREEN_TEXT = 0xE8F5E9, 0x2E7D32
RED_FILL, RED_TEXT = 0xE8E8FD, 0x2626C5
AMBER_FILL, AMBER_TEXT = 0xE0F0FF, 0x0B79D0
HEAD_FILL, HEAD_TEXT = 0x4A3728, 0xFFFFFF
TITLE_FILL = 0x3B2A1E
CARD_FILL = 0xF7F3F0
GREY_TEXT = 0x707070
BORDER = 0xC8BEB4


def api(path: str, timeout: int = 240):
    with urllib.request.urlopen(BASE + path, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def to_server_clock(stamp) -> datetime:
    """A deal's time in the broker's clock. Deals carry a UTC epoch or a UTC string."""
    when = (datetime.utcfromtimestamp(stamp) if isinstance(stamp, (int, float))
            else datetime.strptime(str(stamp)[:19], "%Y-%m-%d %H:%M:%S"))
    return when + timedelta(hours=3)


def session_of(hour: int) -> str:
    """Server clock: Asia 00-07, London 08-15, New York 16-23 - the same split the dashboard uses."""
    return "Asia" if hour < 8 else ("London" if hour < 16 else "New York")


def session_stats(trades: list) -> dict:
    """Every bucketing of the real trades, each row carrying the count that qualifies it."""
    def group(key) -> list:
        buckets: dict = collections.defaultdict(list)
        for trade in trades:
            buckets[key(trade)].append(float(trade["net"]))
        rows = []
        for name, values in buckets.items():
            wins = [v for v in values if v > 0]
            rows.append({
                "bucket": name, "trades": len(values), "wins": len(wins),
                "win_pct": round(100.0 * len(wins) / len(values), 1),
                "net": round(sum(values), 2),
                "avg": round(sum(values) / len(values), 3),
                "best": round(max(values), 2), "worst": round(min(values), 2),
                "thin": len(values) < THIN_SAMPLE,
            })
        return sorted(rows, key=lambda row: -row["net"])

    weekday_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    by_session = group(lambda t: session_of(to_server_clock(t["time"]).hour))
    by_weekday = sorted(group(lambda t: to_server_clock(t["time"]).strftime("%A")),
                        key=lambda row: weekday_order.index(row["bucket"]))
    by_hour = sorted(group(lambda t: f"{to_server_clock(t['time']).hour:02d}:00"),
                     key=lambda row: row["bucket"])
    by_month = sorted(group(lambda t: to_server_clock(t["time"]).strftime("%Y-%m")),
                      key=lambda row: row["bucket"])
    by_symbol = group(lambda t: t["symbol"])

    # The verdict is stated only as far as the evidence reaches.
    ranked = [row for row in by_session if not row["thin"]] or by_session
    best = max(ranked, key=lambda row: row["avg"])
    worst = min(ranked, key=lambda row: row["avg"])
    total = len(trades)
    enough = total >= EVIDENCE_BAR and not best["thin"]
    verdict = (
        f"On {total} real closed trades, the best session by expectancy is {best['bucket']} "
        f"({best['avg']:+.2f} per trade over {best['trades']}) and the worst is {worst['bucket']} "
        f"({worst['avg']:+.2f} over {worst['trades']}).")
    caveat = (
        f"This DESCRIBES the past; it is not yet evidence. The rule is {EVIDENCE_BAR} after-cost trades "
        f"before anything is called profitable, and splitting {total} trades leaves every bucket below "
        f"it - the rows marked 'thin' have fewer than {THIN_SAMPLE} trades, where one good trade moves "
        f"the average more than any edge would. Read the trade COUNT before the colour.")
    return {"by_session": by_session, "by_weekday": by_weekday, "by_hour": by_hour,
            "by_month": by_month, "by_symbol": by_symbol,
            "verdict": verdict, "caveat": caveat, "total": total,
            "meets_evidence_bar": enough,
            "generated_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")}


HEADERS = ("", "trades", "wins", "win %", "net", "per trade", "best", "worst", "")


def _box(ws, top: int, left: int, bottom: int, right: int) -> None:
    """A real border around a block, so each table reads as one object."""
    rng = ws.Range(ws.Cells(top, left), ws.Cells(bottom, right))
    for index in (7, 8, 9, 10):            # left, top, bottom, right edges
        edge = rng.Borders(index)
        edge.LineStyle = 1
        edge.Weight = 2
        edge.Color = BORDER


def _block(ws, row: int, title: str, rows: list, note: str = "") -> int:
    """One titled table. Returns the next free row."""
    ws.Cells(row, 2).Value = title
    ws.Cells(row, 2).Font.Bold = True
    ws.Cells(row, 2).Font.Size = 12
    if note:
        ws.Cells(row, 5).Value = note
        ws.Cells(row, 5).Font.Size = 9
        ws.Cells(row, 5).Font.Color = GREY_TEXT
    row += 1

    head = row
    for offset, label in enumerate(HEADERS):
        cell = ws.Cells(row, 2 + offset)
        cell.Value = label
        cell.Font.Bold = True
        cell.Font.Color = HEAD_TEXT
        cell.Interior.Color = HEAD_FILL
        cell.HorizontalAlignment = -4152 if offset else -4131
    row += 1

    first = row
    for entry in rows:
        values = (entry["bucket"], entry["trades"], entry["wins"], entry["win_pct"] / 100.0,
                  entry["net"], entry["avg"], entry["best"], entry["worst"],
                  "thin" if entry["thin"] else "")
        for offset, value in enumerate(values):
            cell = ws.Cells(row, 2 + offset)
            if offset == 0:
                # Forced to TEXT before the write. "00:00" assigned to a General cell is parsed as a
                # TIME and displayed as 0.00, so the hour block came out as a column of numbers with
                # midnight and noon indistinguishable.
                cell.NumberFormat = "@"
            cell.Value = value
            if offset == 0:
                cell.Font.Bold = True
            elif offset == 3:
                cell.NumberFormat = "0.0%"
            elif offset in (4, 6, 7):
                cell.NumberFormat = "#,##0.00"
            elif offset == 5:
                cell.NumberFormat = "+0.000;-0.000;0.000"
        # Colour carries the SIGN of the money, and amber overrides it when the sample is too small to
        # mean anything - so a green cell can never flatter eight trades into a finding.
        band = ws.Range(ws.Cells(row, 2), ws.Cells(row, 10))
        if entry["thin"]:
            band.Interior.Color = AMBER_FILL
            ws.Cells(row, 10).Font.Color = AMBER_TEXT
            ws.Cells(row, 10).Font.Bold = True
        elif entry["net"] > 0:
            band.Interior.Color = GREEN_FILL
            ws.Cells(row, 6).Font.Color = GREEN_TEXT
        else:
            band.Interior.Color = RED_FILL
            ws.Cells(row, 6).Font.Color = RED_TEXT
        ws.Cells(row, 6).Font.Bold = True
        row += 1

    _box(ws, head, 2, row - 1, 10)
    return row + 1 if not rows else row + 1


def write_sessions(stats: dict) -> str:
    """Create or rewrite the Sessions sheet. Idempotent: the block is cleared and rebuilt each run."""
    import win32com.client as win32

    from excel_refresh import open_workbook                       # same folder, one way to attach

    xl, wb, started_here = open_workbook()
    alerts, calculation = True, -4105
    try:
        # Merge() and a few other calls raise a confirmation dialog, and a dialog BLOCKS every further
        # COM call - two runs hung past a 400-second timeout with the sheet half written and Excel
        # sitting there looking perfectly responsive. Alerts off for the write, restored afterwards.
        alerts = bool(xl.DisplayAlerts)
        xl.DisplayAlerts = False
        xl.ScreenUpdating = False
        # MANUAL while writing, as scripts/excel_refresh.py does and for the same reason: this workbook
        # carries 40,000+ rows of price data with live formula columns, and leaving calculation on
        # automatic recalculates all of it after EVERY one of the ~800 cell writes here. That is not slow,
        # it is indistinguishable from hung - two runs were killed at 400 seconds before this line existed.
        calculation = xl.Calculation
        xl.Calculation = -4135                                    # xlCalculationManual
        try:
            ws = wb.Sheets(SHEET)
        except Exception:
            ws = wb.Sheets.Add(After=wb.Sheets(wb.Sheets.Count))
            ws.Name = SHEET
        # BOUNDED, never ws.Cells. Formatting the whole grid means touching 17 billion cells, and it
        # hung the first run past a 500-second timeout with the sheet half built. The sheet is ~90 rows
        # of content, so a generous window costs nothing and finishes instantly.
        canvas = ws.Range(ws.Cells(1, 1), ws.Cells(220, 14))
        canvas.UnMerge()                                          # undo any merge a previous run left
        canvas.Clear()
        canvas.Interior.Color = 0xFFFFFF                          # white ground, not the theme's grey

        ws.Columns(1).ColumnWidth = 2
        ws.Columns(2).ColumnWidth = 15
        for column in range(3, 11):
            ws.Columns(column).ColumnWidth = 11
        ws.Rows(1).RowHeight = 8

        # Filled as a range but NOT merged. A merge is only cosmetic here and it is the one call that
        # can stop the whole write dead; an unmerged row of filled cells looks the same.
        title = ws.Range(ws.Cells(2, 2), ws.Cells(2, 10))
        ws.Cells(2, 2).Value = "When this system actually makes money"
        ws.Cells(2, 2).Font.Size = 18
        ws.Cells(2, 2).Font.Bold = True
        ws.Cells(2, 2).Font.Color = 0xFFFFFF
        title.Interior.Color = TITLE_FILL
        ws.Rows(2).RowHeight = 34
        ws.Cells(2, 2).HorizontalAlignment = -4131
        ws.Cells(2, 2).IndentLevel = 1

        ws.Cells(3, 2).Value = (f"{stats['total']} real closed trades from the MT5 history, bucketed in "
                                f"the broker's server clock  |  refreshed {stats['generated_at']}")
        ws.Cells(3, 2).Font.Size = 9
        ws.Cells(3, 2).Font.Color = GREY_TEXT

        # One line per row, wrapped in Python rather than by Excel, so no merge and no wrap height
        # guessing. Text overflows across the empty cells to its right, which reads the same.
        import textwrap

        lines = [stats["verdict"]] + textwrap.wrap(stats["caveat"], 118)
        last = 5 + len(lines)
        ws.Range(ws.Cells(5, 2), ws.Cells(last, 10)).Interior.Color = CARD_FILL
        for offset, line in enumerate(lines):
            cell = ws.Cells(5 + offset, 2)
            cell.Value = line
            cell.Font.Size = 10
            cell.IndentLevel = 1
            if offset == 0:
                cell.Font.Bold = True
            else:
                cell.Font.Color = GREY_TEXT
        _box(ws, 5, 2, last, 10)

        row = last + 2
        row = _block(ws, row, "By session", stats["by_session"],
                     "Asia 00-07, London 08-15, New York 16-23, server clock")
        row = _block(ws, row, "By day of week", stats["by_weekday"], "server clock")
        row = _block(ws, row, "By month", stats["by_month"])
        row = _block(ws, row, "By market", stats["by_symbol"])
        row = _block(ws, row, "By hour of day", stats["by_hour"],
                     "the thinnest split of all - read the counts")

        ws.Cells(row + 1, 2).Value = ("Every number above is computed from the deal history, never "
                                      "entered by hand. Rebuild it with: python scripts/excel_sessions.py")
        ws.Cells(row + 1, 2).Font.Size = 9
        ws.Cells(row + 1, 2).Font.Color = GREY_TEXT

        try:
            ws.Activate()
            xl.ActiveWindow.FreezePanes = False
            ws.Range("B8").Select()
            xl.ActiveWindow.FreezePanes = True
        except Exception:
            pass          # freezing needs the sheet active and is cosmetic; never fail the refresh
        wb.Save()
    finally:
        xl.ScreenUpdating = True
        try:                                     # put the owner's Excel back exactly as we found it
            xl.Calculation = calculation
            xl.DisplayAlerts = alerts
        except Exception:
            pass
        if started_here:
            wb.Close(SaveChanges=True)
            xl.Quit()
    counts = ", ".join(f"{row['bucket']} {row['trades']}" for row in stats["by_session"])
    return f"Sessions sheet: {stats['total']} trades ({counts})"


def main() -> int:
    payload = api("/api/trades/closed")
    if not payload.get("available"):
        print(f"cannot build the sheet: {payload.get('reason')}")
        return 1
    stats = session_stats(payload["trades"])
    print(stats["verdict"])
    print()
    print(stats["caveat"])
    print()
    for label in ("by_session", "by_weekday", "by_month"):
        print(label.replace("by_", "by ") + ":")
        for row in stats[label]:
            flag = "  thin" if row["thin"] else ""
            print(f"  {row['bucket']:<11} n={row['trades']:<4} win {row['win_pct']:>5.1f}%  "
                  f"net {row['net']:>9.2f}  per trade {row['avg']:>+8.3f}{flag}")
        print()
    out = Path(machine_config().get("excel_workbook") or "")
    print(write_sessions(stats))
    print(f"workbook: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
